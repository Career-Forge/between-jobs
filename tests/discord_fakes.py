"""Fakes and fixtures for the Discord tests.

THERE IS NO REFERENCE IMPLEMENTATION OF THE DISCORD ADAPTER, so no captured run to be diff-clean
against (see tests/golden/README.md, which governs ported behaviour; this is new behaviour). What
these tests pin is the adapter's own contract, from Discord's published documentation, with
SYNTHETIC fixtures: interactions written by hand in the shape Discord documents, signed with a
THROWAWAY Ed25519 key made here and used nowhere else. Nothing in this file is, or was derived
from, real Discord data, a real application, a real token or a real person. What no fake can
tell is how the real Discord behaves; that is checked by hand against a real application, and
the list of what no fake can answer is "Not verified against the real Discord" in
tests/golden/README.md.

`FakeDiscord` is an `httpx.MockTransport` handler that records every request the Discord client
makes (the method, the path split into what it addresses, the JSON body, the uploaded files and
whether a bot token was sent), answers like Discord (a message id for a message created, the
documented error bodies for the failures a test asks for), and serves the files an
`/import` attachment points at. `World` adds the forge-engines and LaTeX answers a resume
generation needs, so one network serves a whole conversation.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import default as email_policy
from types import SimpleNamespace
from typing import Any

import httpx
import test_telegram_prepare_callback as prepare_fakes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from between_jobs.api.discord_client import DiscordClient

# -- keys and signatures --------------------------------------------------------------------------

PRIVATE_KEY = Ed25519PrivateKey.generate()
PUBLIC_KEY_HEX = PRIVATE_KEY.public_key().public_bytes_raw().hex()
OTHER_KEY = Ed25519PrivateKey.generate()  # a key the app does not hold

APPLICATION_ID = "1234567890123456789"
# Shaped like a bot token (three dot-separated base64url parts) so the redaction is exercised, but
# starting with a letter no real token starts with (they begin with the base64 of a numeric id: M, N
# or O), so no secret scanner takes it for one.
BOT_TOKEN = "ZmFrZS1ib3QtdG9rZW4tZm9yLXRlc3Rz.GtEsT1.not-a-real-bot-token-just-shaped-like-one-123"
SUBJECT = "777000111222333444"
DM_CHANNEL = "888000111222333444"
GUILD_ID = "999000111222333444"
GUILD_CHANNEL = "555000111222333444"
SECOND_SUBJECT = "777000999888777666"


def sign(body: bytes, timestamp: str, key: Ed25519PrivateKey = PRIVATE_KEY) -> str:
    """The hex signature Discord puts in `X-Signature-Ed25519`."""
    return key.sign(timestamp.encode() + body).hex()


def signed_headers(
    body: bytes, *, timestamp: str | int, key: Ed25519PrivateKey = PRIVATE_KEY
) -> dict[str, str]:
    stamp = str(timestamp)
    return {"X-Signature-Ed25519": sign(body, stamp, key), "X-Signature-Timestamp": stamp}


# -- interactions (written by hand, in the shape Discord documents) -----------------------------


_counter = iter(range(1_100_000_000_000_000_001, 1_100_000_000_000_900_000))


def next_interaction_id() -> str:
    return str(next(_counter))


def fresh_token() -> str:
    """An interaction token shaped like Discord's (long base64url text), unique per call."""
    return "aW50ZXJhY3Rpb246" + uuid.uuid4().hex + uuid.uuid4().hex + "_-X"


def _base(
    kind: int,
    *,
    user_id: str,
    dm: bool,
    interaction_id: str | None,
    token: str | None,
    context: int | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": interaction_id or next_interaction_id(),
        "application_id": APPLICATION_ID,
        "type": kind,
        "token": token or fresh_token(),
        "version": 1,
        "locale": "en-US",
        "app_permissions": "0",
    }
    if dm:
        payload.update(
            {
                "channel_id": DM_CHANNEL,
                "channel": {"id": DM_CHANNEL, "type": 1},
                "user": {"id": user_id, "username": "someone", "global_name": "Someone"},
                "context": 1 if context is None else context,
                "authorizing_integration_owners": {"1": user_id},
            }
        )
    else:
        payload.update(
            {
                "guild_id": GUILD_ID,
                "channel_id": GUILD_CHANNEL,
                "channel": {"id": GUILD_CHANNEL, "type": 0},
                "member": {"user": {"id": user_id, "username": "someone"}, "roles": []},
                "context": 0 if context is None else context,
                "authorizing_integration_owners": {"0": GUILD_ID},
            }
        )
    return payload


def command(
    name: str,
    options: dict[str, Any] | None = None,
    *,
    user_id: str = SUBJECT,
    dm: bool = True,
    interaction_id: str | None = None,
    token: str | None = None,
    context: int | None = None,
    resolved: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _base(
        2, user_id=user_id, dm=dm, interaction_id=interaction_id, token=token, context=context
    )
    option_list = []
    for key, value in (options or {}).items():
        kind = 11 if key == "file" else 4 if isinstance(value, int) else 3
        option_list.append({"name": key, "type": kind, "value": value})
    data: dict[str, Any] = {"id": "1000000000000000001", "name": name, "type": 1}
    if option_list:
        data["options"] = option_list
    if resolved is not None:
        data["resolved"] = resolved
    payload["data"] = data
    return payload


def button(
    custom_id: str,
    *,
    user_id: str = SUBJECT,
    dm: bool = True,
    interaction_id: str | None = None,
    token: str | None = None,
    message_id: str = "1200000000000000007",
) -> dict[str, Any]:
    payload = _base(
        3, user_id=user_id, dm=dm, interaction_id=interaction_id, token=token, context=None
    )
    payload["data"] = {"custom_id": custom_id, "component_type": 2}
    payload["message"] = {"id": message_id, "channel_id": payload["channel_id"]}
    return payload


def ping() -> dict[str, Any]:
    return {"id": next_interaction_id(), "application_id": APPLICATION_ID, "type": 1, "version": 1}


def attachment_resolved(
    *,
    attachment_id: str = "1300000000000000001",
    filename: str = "resume.json",
    content_type: str | None = "application/json",
    size: int = 100,
    url: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """(the option value, the `resolved` block) for an `/import` command."""
    entry: dict[str, Any] = {
        "id": attachment_id,
        "filename": filename,
        "size": size,
        "url": url or f"https://cdn.discordapp.com/attachments/1/2/{filename}?ex=1&is=2&hm=3",
        "proxy_url": f"https://media.discordapp.net/attachments/1/2/{filename}",
    }
    if content_type is not None:
        entry["content_type"] = content_type
    return attachment_id, {"attachments": {attachment_id: entry}}


def body_of(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


# -- a recorded request ------------------------------------------------------------------------


@dataclass
class Recorded:
    method: str
    path: str  # after /api/v10
    host: str
    headers: dict[str, str]
    json_body: dict[str, Any] | None
    files: dict[str, bytes] = field(default_factory=dict)
    token: str | None = None
    """The interaction token in the path, if this was a webhook call."""

    @property
    def authorized_as_bot(self) -> bool:
        return self.headers.get("authorization", "").startswith("Bot ")

    @property
    def kind(self) -> str:
        """A short name for what was asked: edit_original, followup, edit_followup, open_dm,
        channel_message, edit_channel_message, download."""
        if self.host != "discord.com":
            return "download"
        if self.path.startswith("/webhooks/"):
            if self.method == "PATCH":
                return "edit_original" if self.path.endswith("/@original") else "edit_followup"
            return "followup"
        if self.path == "/users/@me/channels":
            return "open_dm"
        if self.path.startswith("/channels/"):
            return "edit_channel_message" if self.method == "PATCH" else "channel_message"
        return "other"


def _parse_multipart(request: httpx.Request) -> tuple[dict[str, Any] | None, dict[str, bytes]]:
    raw = (
        b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.content
    )
    payload: dict[str, Any] | None = None
    files: dict[str, bytes] = {}
    for part in BytesParser(policy=email_policy).parsebytes(raw).iter_parts():
        name = str(part.get_param("name", header="content-disposition"))
        decoded = part.get_payload(decode=True)
        content = decoded if isinstance(decoded, bytes) else b""
        filename = part.get_filename()
        if filename is not None:
            files[filename] = content
        elif name == "payload_json":
            payload = json.loads(content.decode())
    return payload, files


class FakeDiscord:
    """The Discord REST API and its CDN, as far as the adapter can tell."""

    def __init__(self, *, cdn_files: dict[str, bytes] | None = None) -> None:
        self.requests: list[Recorded] = []
        self.cdn_files = cdn_files or {}
        self.cdn_headers: dict[str, str] = {}
        self.cdn_status = 200
        self.cdn_redirect_to: str | None = None
        self._message_id = 1_400_000_000_000_000_000
        # Failures to inject: (kind of request, how many times, status, Discord's error code).
        self._failures: list[list[Any]] = []
        self.token_expired = False
        self.unreachable = False
        self.dm_channel = "1500000000000000001"

    # -- what a test asks for ------------------------------------------------------------

    def fail(
        self,
        kind: str,
        *,
        status: int,
        code: int | None = None,
        times: int = 1,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._failures.append([kind, times, status, code, body, headers or {}])

    def expire_token(self) -> None:
        """Every call that uses the interaction token now answers as an expired one does."""
        self.token_expired = True

    # -- reading what happened -----------------------------------------------------------

    def of(self, *kinds: str) -> list[Recorded]:
        return [r for r in self.requests if r.kind in kinds]

    @property
    def sent(self) -> list[Recorded]:
        """Every call that put a message in front of the person, in order."""
        return self.of("edit_original", "followup", "channel_message")

    @property
    def contents(self) -> list[str]:
        return [str((r.json_body or {}).get("content", "")) for r in self.sent]

    def all_text(self) -> str:
        return "\n".join(str(r.json_body) for r in self.requests if r.json_body is not None)

    # -- the transport ----------------------------------------------------------------------

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.unreachable:
            raise httpx.ConnectError(f"cannot reach {request.url}")
        host = request.url.host
        path = request.url.path
        if host == "discord.com":
            assert path.startswith("/api/v10"), path
            path = path[len("/api/v10") :]
        content_type = request.headers.get("content-type", "")
        body: dict[str, Any] | None = None
        files: dict[str, bytes] = {}
        if request.content and content_type.startswith("multipart/"):
            body, files = _parse_multipart(request)
        elif request.content and content_type.startswith("application/json"):
            body = json.loads(request.content)
        token_match = re.match(r"/webhooks/\d+/([^/]+)", path)
        recorded = Recorded(
            method=request.method,
            path=path,
            host=host,
            headers={k.lower(): v for k, v in request.headers.items()},
            json_body=body,
            files=files,
            token=token_match.group(1) if token_match else None,
        )
        self.requests.append(recorded)

        if host != "discord.com":
            return self._cdn(request)
        for failure in self._failures:
            kind, times, status, code, error_body, headers = failure
            if recorded.kind == kind and times > 0:
                failure[1] -= 1
                payload = error_body if error_body is not None else {"message": "x", "code": code}
                return httpx.Response(status, json=payload, headers=headers, request=request)
        if self.token_expired and recorded.token is not None:
            return httpx.Response(
                404, json={"message": "Unknown Webhook", "code": 10015}, request=request
            )
        if recorded.kind == "open_dm":
            return httpx.Response(200, json={"id": self.dm_channel, "type": 1}, request=request)
        self._message_id += 1
        message_id = self._message_id
        if recorded.kind in ("edit_original", "edit_followup", "edit_channel_message"):
            existing = path.rsplit("/", 1)[-1]
            message_id_text = str(message_id) if existing == "@original" else existing
            return httpx.Response(200, json={"id": message_id_text}, request=request)
        return httpx.Response(200, json={"id": str(message_id)}, request=request)

    def _cdn(self, request: httpx.Request) -> httpx.Response:
        if self.cdn_redirect_to is not None:
            return httpx.Response(302, headers={"location": self.cdn_redirect_to}, request=request)
        if self.cdn_status != 200:
            return httpx.Response(self.cdn_status, request=request)
        content = self.cdn_files.get(request.url.path.rsplit("/", 1)[-1])
        assert content is not None, f"no CDN file for {request.url.path}"
        return httpx.Response(200, content=content, headers=self.cdn_headers, request=request)


def http_for(fake: FakeDiscord) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))


def discord_client(
    fake: FakeDiscord,
    *,
    bot_token: str | None = BOT_TOKEN,
    sleep: Callable[[float], Any] | None = None,
    **kwargs: Any,
) -> DiscordClient:
    if sleep is not None:
        kwargs["sleep"] = sleep
    return DiscordClient(
        http_for(fake), application_id=APPLICATION_ID, bot_token=bot_token, **kwargs
    )


class SleepRecorder:
    """Stands in for `asyncio.sleep`: records the waits and does not wait."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


# -- the whole network for an end-to-end conversation ------------------------------------------


class World(FakeDiscord):
    """Discord, plus the forge-engines and LaTeX answers a resume generation makes."""

    def __init__(
        self,
        *,
        cdn_files: dict[str, bytes] | None = None,
        apply_body: Any = None,
        apply_status: int = 200,
    ) -> None:
        super().__init__(cdn_files=cdn_files)
        self.apply_body = (
            apply_body if apply_body is not None else prepare_fakes._FORGE_APPLY_RESPONSE_BODY
        )
        self.apply_status = apply_status
        self.engine_calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host in ("discord.com", "cdn.discordapp.com", "media.discordapp.net"):
            return super().handler(request)
        if request.url.path.endswith("/apply"):
            self.engine_calls += 1
            return httpx.Response(self.apply_status, json=self.apply_body, request=request)
        if request.url.path.endswith("/compile"):
            return httpx.Response(200, content=prepare_fakes._PDF_BYTES, request=request)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")


# -- a fake Supabase that also keeps the interaction claims ----------------------------------------

CLAIM_RPCS = frozenset(
    {"claim_discord_interaction", "complete_discord_interaction", "release_discord_interaction"}
)

_DAY = 86400.0


class FakeInteractionLedger:
    """An in-memory stand-in for `claim_discord_interaction` and its two companions, with a
    clock the test moves (the real functions use the database's). Ids are the text Discord
    gives them; the methods take an int too, so the shared ledger scenario
    (`update_ledger_fakes.run_scenario`) can drive it."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.now = 0.0
        self.claims: list[str] = []
        self.leases: list[int] = []

    async def claim(self, interaction_id: int | str, lease_seconds: int = 900) -> str:
        key = str(interaction_id)
        if not 1 <= lease_seconds <= 86400:
            raise ValueError("p_lease_seconds must be between 1 and 86400")
        self.claims.append(key)
        self.leases.append(lease_seconds)
        for stale in [k for k, r in self.rows.items() if r["claimed_at"] < self.now - 7 * _DAY][
            :200
        ]:
            del self.rows[stale]
        row = self.rows.get(key)
        if row is None:
            self.rows[key] = {"claimed_at": self.now, "completed": False}
            return "claimed"
        if not row["completed"] and row["claimed_at"] <= self.now - lease_seconds:
            row["claimed_at"] = self.now
            return "claimed"
        return "done" if row["completed"] else "in_progress"

    async def complete(self, interaction_id: int | str) -> None:
        row = self.rows.get(str(interaction_id))
        if row is not None:
            row["completed"] = True

    async def release(self, interaction_id: int | str) -> None:
        row = self.rows.get(str(interaction_id))
        if row is not None and not row["completed"]:
            del self.rows[str(interaction_id)]

    async def age(self, interaction_id: int | str, seconds: float) -> None:
        self.rows[str(interaction_id)]["claimed_at"] -= seconds

    async def known(self, interaction_id: int | str) -> bool:
        return str(interaction_id) in self.rows

    def rpc(self, name: str, params: dict[str, Any]) -> Any:
        ledger = self

        class _Call:
            async def execute(self) -> SimpleNamespace:
                interaction_id = params["p_interaction_id"]
                if name == "claim_discord_interaction":
                    return SimpleNamespace(
                        data=await ledger.claim(interaction_id, params.get("p_lease_seconds", 900))
                    )
                if name == "complete_discord_interaction":
                    await ledger.complete(interaction_id)
                else:
                    await ledger.release(interaction_id)
                return SimpleNamespace(data=None)

        return _Call()
