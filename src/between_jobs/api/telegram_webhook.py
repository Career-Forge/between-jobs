"""Telegram webhook receiver.

Verifies the update is really from Telegram, claims its `update_id` so a redelivery is not
processed twice, parses it with the Telegram adapter (`telegram_adapter`), and hands the
result to the channel-neutral logic (`channel_core`) with a Telegram renderer. That is all it
does: what the bot says and does lives in `channel_core`, and everything that reads a raw
update or writes a Telegram request body lives in `telegram_adapter`.

Always returns 200 for a well-authenticated request, even for update shapes it doesn't
handle -- Telegram retries on non-2xx, and there's nothing to retry here since not every
update needs an action. (An unexpected exception still surfaces as a 500, which is how
Telegram is asked to retry; the claim of each update_id (P0.9) stops a redelivery of an
update that is still running or already done from being processed a second time.)

A resume generation does not hold the request open: `channel_core` answers the person at
once and finishes the work in a background task, so the 200 goes out within a moment and
Telegram has nothing to time out on. The claim is completed when this handler answers, which
means a redelivery of that update is dropped as a duplicate even while the generation runs --
and means that if the process dies mid-generation nothing resumes it (see `deferred_reply`).
"""

from __future__ import annotations

import hmac
from typing import Any, cast

import anyio
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from supabase import AsyncClient

from . import deferred_reply
from .app_state import get_http_client, get_supabase, get_telegram_renderer, get_webhook_secret
from .channel_core import handle_inbound, is_slow_request
from .channel_envelope import InboundMessage, Renderer
from .telegram_adapter import parse_update, update_id_of
from .telegram_updates_store import claim_update, complete_update, release_update

router = APIRouter()


def _verify_webhook_secret(request: Request, expected: str = Depends(get_webhook_secret)) -> None:
    got = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not hmac.compare_digest(got, expected):
        raise HTTPException(status_code=401, detail="invalid webhook secret")


# How long an unfinished claim holds before a redelivery may take the update over (see
# `claim_telegram_update`). Long enough that a live handler is never taken over, short enough
# that a handler whose process died is picked up while Telegram is still retrying. Most updates
# finish in a second or two. The resume generation no longer runs inside the handler (it is a
# deferred task), so the longer lease it used to need is more than it needs now; it is kept for
# the requests that start one, to keep the claim's behaviour unchanged.
_LEASE_SECONDS = 120
_SLOW_LEASE_SECONDS = 600
_SETTLE_TIMEOUT_SECONDS = 10.0


def _lease_seconds_for(message: InboundMessage | None) -> int:
    if message is not None and is_slow_request(message):
        return _SLOW_LEASE_SECONDS
    return _LEASE_SECONDS


async def _settle_claim(supabase: AsyncClient, update_id: int, *, finished: bool) -> None:
    """Completes the update's claim, or releases it if the handler did not finish. Shielded
    and bounded: this runs in a `finally` that a client disconnect can reach through a
    cancelled anyio scope, where an unshielded await is cancelled again at once and the claim
    would be left to wait out its lease. Neither call raises (the store swallows and logs)."""
    with anyio.CancelScope(shield=True), anyio.move_on_after(_SETTLE_TIMEOUT_SECONDS):
        if finished:
            await complete_update(supabase, update_id)
        else:
            await release_update(supabase, update_id)


def _parse(update: dict[str, Any]) -> tuple[InboundMessage | None, Exception | None]:
    """`parse_update`, with a malformed update (a message with no sender or chat: Telegram
    never sends one) held instead of raised. It is raised where the update is processed, so a
    malformed one is claimed and then released like any other update that fails, and Telegram
    is answered with the same 500 as always."""
    try:
        return parse_update(update), None
    except (KeyError, TypeError) as e:
        return None, e


async def _process_update(
    supabase: AsyncClient,
    renderer: Renderer,
    http: httpx.AsyncClient,
    message: InboundMessage | None,
    malformed: Exception | None,
) -> dict[str, str]:
    if malformed is not None:
        raise malformed
    if message is None:
        return {"status": "ignored"}
    await handle_inbound(supabase, http, renderer, message, deferred=deferred_reply.registry)
    return {"status": "ok"}


@router.post("/telegram/webhook", dependencies=[Depends(_verify_webhook_secret)])
async def telegram_webhook(
    request: Request,
    supabase: AsyncClient = Depends(get_supabase),
    renderer: Renderer = Depends(get_telegram_renderer),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> dict[str, str]:
    update = cast(dict[str, Any], await request.json())
    message, malformed = _parse(update)

    # P0.9: Telegram redelivers an update it got no 2xx for, and gives up waiting on a
    # slow handler too. The claim makes the second delivery a quick answer instead of a
    # second job, application and paid generation. It is released again if this delivery
    # fails or is cancelled, so the retry a 500 asks for is processed, not dropped.
    update_id = update_id_of(update)
    claimed = False
    if update_id is not None:
        state = await claim_update(supabase, update_id, lease_seconds=_lease_seconds_for(message))
        if state == "done":
            return {"status": "duplicate"}
        if state == "in_progress":
            # NOT a 200: if the delivery that holds the claim died with its process, nothing
            # will ever finish this update, and a 200 would tell Telegram to stop
            # redelivering it. A non-2xx keeps it retrying until that delivery completes
            # (then 'done') or its lease runs out (then this update is claimed and processed).
            raise HTTPException(
                status_code=503,
                detail="this update is still being processed",
                headers={"Retry-After": "30"},
            )
        claimed = state == "claimed"

    finished = False
    try:
        response = await _process_update(supabase, renderer, http, message, malformed)
        finished = True
        return response
    finally:
        if claimed and update_id is not None:
            await _settle_claim(supabase, update_id, finished=finished)
