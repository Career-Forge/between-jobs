"""Discord interactions endpoint: `POST /discord/interactions`.

Verifies that the request really is from Discord, claims the interaction's id so a repeat is not
processed twice, parses it with the Discord adapter (`discord_adapter`), answers Discord within
its three seconds, and hands the work to the channel-neutral logic (`channel_core`) with a Discord
renderer. What the app says and does lives in `channel_core`; everything that reads a raw
interaction or writes a Discord request body lives in `discord_adapter`.

THE ORDER, because the endpoint is open to the whole internet and must stay cheap until the caller
has proved who it is:

1. The body is read (it is capped well below the API's default by `body_limit`, before this code
   runs) and the two signature headers are checked for shape. No parsing, no database.
2. The Ed25519 signature over `X-Signature-Timestamp` + the RAW body bytes is verified with the
   application's public key. A missing, malformed or wrong signature is a 401 and nothing else
   happens: Discord's own routine security checks send invalid signatures on purpose and expect
   exactly that.
3. The timestamp must be within five minutes of now, either way. The signature covers it, so it
   cannot be edited; the window keeps a captured request from being replayed later. (Discord's
   documentation names no window. Five minutes tolerates a server clock that has drifted while
   leaving a captured request useless soon, and replay inside the window is stopped by step 6;
   a request is only useful to Discord for three seconds in any case.)
4. Only now is the body parsed. A PING (Discord checking the endpoint when its URL is saved) is
   answered `{"type": 1}`. Anything that is not a command or a button tap gets a short ephemeral
   refusal, and so does anything that did not come from the direct message with the app (the
   commands are registered for that place only; this is the second lock).
5. A place in the registry of running interactions is reserved (else a short "busy" answer: nothing
   queues), then
6. the interaction's id is CLAIMED in the database (`claim_discord_interaction`, a lease like
   Telegram's update claim): a repeat of an interaction that is running or done is answered with
   the same deferral and not processed. This is the first database access, and it is bounded: if it
   cannot be made within a second and a half the interaction is processed without dedup (see
   `discord_interactions_store`).
7. The answer is the DEFERRAL (a "thinking" placeholder for a command, a silent deferral for a
   button), and the real work -- `channel_core.handle_inbound`, with the renderer for this
   interaction -- runs as a supervised background task that starts only AFTER that answer has been
   sent. It must wait: the interaction's webhook accepts nothing until Discord has the first
   response, and a quick command (a database read) could otherwise reach Discord before its own
   acknowledgement did. The task waits for the response to leave (Starlette runs a response's
   background task after sending it), then a quarter of a second more because "left" is not
   "recorded by Discord", and for at most a few seconds if it never leaves.

Why a registry of our own and not only `deferred_reply.registry`: that one holds at most three
resume generations at a time, which is right for a generation and wrong for every command. This
one is the same class, sized for quick handlers (`MAX_IN_FLIGHT`), with the same guarantees:
strong references, a bound, failure containment (a failure is logged and the person is told), and
cancellation at shutdown. The slow part of a resume generation still moves to the generation
registry from inside `channel_core`, exactly as it does for Telegram, and keeps using this
interaction's renderer until its token expires 15 minutes after the command.

The interaction's id is claimed as done when the handler returns, whether it succeeded or not:
unlike Telegram, Discord has nothing that retries, so a repeat can only be a replay, and a replay
must never run a second time. The body is never logged, and neither is the interaction's token.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

import anyio
import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.background import BackgroundTask

from supabase import AsyncClient

from . import deferred_reply
from .app_state import get_discord_client, get_discord_config, get_http_client, get_supabase
from .channel_core import handle_inbound
from .channel_envelope import InboundMessage, Say, rich
from .deferred_reply import DeferredReplies
from .discord_adapter import (
    DiscordInteractionRenderer,
    InteractionKind,
    MalformedInteraction,
    build_renderer,
    deferred_response,
    immediate_response,
    interaction_id_of,
    interaction_kind,
    parse_interaction,
    pong_response,
)
from .discord_client import DiscordClient
from .discord_config import DiscordConfig
from .discord_interactions_store import (
    claim_interaction,
    complete_interaction,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_SIGNATURE = re.compile(r"[0-9a-fA-F]{128}")
_TIMESTAMP = re.compile(r"[0-9]{1,13}")

MAX_TIMESTAMP_SKEW_SECONDS = 300
"""How far the signed timestamp may be from now, in either direction (see the module docstring)."""

MAX_IN_FLIGHT = 64
"""Interactions being handled at once in this process; the next one is told the app is busy."""

# How long an unfinished claim holds. A claim is only ever taken over by a repeat, and a repeat of
# a real interaction is useless once its token (valid 15 minutes) has expired, so the lease is that
# long: nothing can take over a handler that is still working.
_LEASE_SECONDS = 900
_CLAIM_TIMEOUT_SECONDS = 1.5
_SETTLE_TIMEOUT_SECONDS = 10.0
_RESPONSE_WAIT_SECONDS = 5.0
_AFTER_RESPONSE_PAUSE_SECONDS = 0.25
"""A short pause between "the answer was handed to the server" and the first call to Discord. That
event says our bytes left, not that Discord has recorded them, and Discord documents no ordering
between the two; a command that touches the database waits longer than this anyway, so the pause
only matters to one that does not. It is a guess, bounded and cheap."""

REFUSE_GUILD_TEXT = (
    "I only work in a direct message with me. Open a chat with me and run the command there."
)
UNSUPPORTED_TEXT = "I can't do anything with that."
BUSY_TEXT = "I'm handling a lot right now. Try again in a moment."
FAILED_TEXT = "❌ Something went wrong. Try again in a moment."

interaction_registry = DeferredReplies(max_concurrent=MAX_IN_FLIGHT)
"""The process's registry of interactions being handled."""


async def shutdown_interaction_tasks() -> None:
    """Called once by the app's lifespan, before the clients the work uses are closed and before
    the generation registry (a handler may be starting a generation)."""
    await interaction_registry.shutdown()


# -- verification ---------------------------------------------------------------------------


class SignatureRejected(Exception):
    """The request is not provably from Discord. `reason` is a fixed word, safe to log."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def verify_request(
    public_key: Ed25519PublicKey,
    signature: str | None,
    timestamp: str | None,
    body: bytes,
    *,
    now: float,
) -> None:
    """Raises `SignatureRejected` unless `signature` (hex) is a valid Ed25519 signature, by
    `public_key`, of the timestamp's text followed by the body's exact bytes, and the timestamp
    is within `MAX_TIMESTAMP_SKEW_SECONDS` of `now`. The shapes are checked first with strict
    patterns (`bytes.fromhex` would accept spaces, and `str.isdigit` other scripts' digits)."""
    if not signature or not timestamp:
        raise SignatureRejected("missing")
    if _SIGNATURE.fullmatch(signature) is None or _TIMESTAMP.fullmatch(timestamp) is None:
        raise SignatureRejected("malformed")
    try:
        public_key.verify(bytes.fromhex(signature), timestamp.encode("ascii") + body)
    except InvalidSignature:
        raise SignatureRejected("bad_signature") from None
    signed_at = int(timestamp)
    if signed_at >= 10**11:
        # Whole seconds are what Discord sends. A value this large cannot be a date in seconds, so
        # it is read as milliseconds rather than refused as "from the future" if that ever changes.
        signed_at //= 1000
    skew = now - signed_at
    if skew > MAX_TIMESTAMP_SKEW_SECONDS:
        raise SignatureRejected("stale")
    if skew < -MAX_TIMESTAMP_SKEW_SECONDS:
        raise SignatureRejected("future")


def _unauthorized(reason: str) -> HTTPException:
    logger.info("discord interaction refused", extra={"ctx": {"reason": reason}})
    return HTTPException(status_code=401, detail="invalid request signature")


# -- the work ---------------------------------------------------------------------------------


def _log_ctx(message: InboundMessage) -> dict[str, str | None]:
    return {"update_id": message.update_id, "channel": message.channel}


async def _settle_claim(supabase: AsyncClient, interaction_id: str) -> None:
    """Completes the interaction's claim. Bounded, because a completion that never answers would
    hold this handler's task, and its place in `interaction_registry`, for as long as it hung. It
    runs in a `finally`; shutdown cancels the task once, which does not interrupt the awaits made
    while it unwinds, and nothing around this task is an anyio scope that could cancel it again
    (the handler is a task of its own, not part of the request). Never raises (the store swallows
    and logs)."""
    with anyio.move_on_after(_SETTLE_TIMEOUT_SECONDS):
        await complete_interaction(supabase, interaction_id)


async def _handle(
    supabase: AsyncClient,
    http: httpx.AsyncClient,
    renderer: DiscordInteractionRenderer,
    message: InboundMessage,
    response_sent: asyncio.Event,
    claimed_id: str | None,
) -> None:
    try:
        # Not before Discord has our first answer (see the module docstring); not forever either.
        try:
            await asyncio.wait_for(response_sent.wait(), timeout=_RESPONSE_WAIT_SECONDS)
        except TimeoutError:
            logger.warning(
                "the deferral was not confirmed sent; handling the interaction anyway",
                extra={"ctx": _log_ctx(message)},
            )
        else:
            await asyncio.sleep(_AFTER_RESPONSE_PAUSE_SECONDS)
        await handle_inbound(supabase, http, renderer, message, deferred=deferred_reply.registry)
        await renderer.settle()
    finally:
        if claimed_id is not None:
            await _settle_claim(supabase, claimed_id)


async def _tell_it_failed(renderer: DiscordInteractionRenderer, message: InboundMessage) -> None:
    await renderer.send(message.chat_ref, Say(rich(FAILED_TEXT)))


async def _mark_sent(event: asyncio.Event) -> None:
    event.set()


# -- the route --------------------------------------------------------------------------------


@router.post("/discord/interactions")
async def discord_interactions(
    request: Request,
    config: DiscordConfig = Depends(get_discord_config),
    supabase: AsyncClient = Depends(get_supabase),
    client: DiscordClient = Depends(get_discord_client),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> Response:
    body = await request.body()
    try:
        verify_request(
            config.public_key,
            request.headers.get("x-signature-ed25519"),
            request.headers.get("x-signature-timestamp"),
            body,
            now=time.time(),
        )
    except SignatureRejected as rejected:
        raise _unauthorized(rejected.reason) from None

    try:
        payload: Any = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="the body is not JSON") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="the body is not an interaction")

    kind = interaction_kind(payload)
    if kind is InteractionKind.PING:
        return JSONResponse(pong_response())

    try:
        message = parse_interaction(payload)
        renderer = build_renderer(client, payload, message) if message is not None else None
    except MalformedInteraction:
        logger.warning("a signed interaction was malformed", extra={"ctx": {"kind": kind.value}})
        raise HTTPException(status_code=400, detail="the interaction is malformed") from None
    if message is None or renderer is None:
        return JSONResponse(immediate_response(UNSUPPORTED_TEXT))
    if not message.is_private:
        # Refused with no database access and no work: a person's applications are not shown in
        # a place anyone else could be reading.
        return JSONResponse(immediate_response(REFUSE_GUILD_TEXT))

    slot = await interaction_registry.try_reserve()
    if slot is None:
        logger.info("discord interaction refused: busy", extra={"ctx": _log_ctx(message)})
        return JSONResponse(immediate_response(BUSY_TEXT))

    handed_over = False
    try:
        acknowledgement = JSONResponse(deferred_response(kind, ephemeral=not message.is_private))
        interaction_id = interaction_id_of(payload)
        claimed_id: str | None = None
        if interaction_id is not None:
            state = None
            try:
                async with asyncio.timeout(_CLAIM_TIMEOUT_SECONDS):
                    state = await claim_interaction(
                        supabase, interaction_id, lease_seconds=_LEASE_SECONDS
                    )
            except TimeoutError:
                logger.warning(
                    "the interaction claim timed out; processing without dedup",
                    extra={"ctx": _log_ctx(message)},
                )
            if state in ("done", "in_progress"):
                logger.info(
                    "a repeated discord interaction was dropped",
                    extra={"ctx": {**_log_ctx(message), "claim": state}},
                )
                return acknowledgement
            if state == "claimed":
                claimed_id = interaction_id

        response_sent = asyncio.Event()
        interaction_registry.start(
            slot,
            lambda: _handle(supabase, http, renderer, message, response_sent, claimed_id),
            name="discord_interaction",
            context=_log_ctx(message),
            on_failure=lambda: _tell_it_failed(renderer, message),
        )
        handed_over = True
        acknowledgement.background = BackgroundTask(_mark_sent, response_sent)
        return acknowledgement
    finally:
        if not handed_over:
            slot.release()
