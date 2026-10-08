"""Thin wrapper over the Discord REST API: the transport, and nothing about what is sent.

`discord_adapter` decides what a message looks like and hands this module finished request
bodies. What lives here is what is true of every call:

- WHERE a request goes and with what credential. Two kinds of credential exist and they are
  kept apart on purpose. An interaction's TOKEN (valid for 15 minutes) goes in the URL path and
  needs no `Authorization` header; the BOT TOKEN goes in an `Authorization: Bot ...` header and
  is never put in a URL. Calls that use the bot token (opening a direct message, posting to a
  channel) exist only on a client that was given one.
- That a token can never reach a log or an exception message. httpx logs request URLs and quotes
  them in some of its errors, and an interaction token is a credential, so this module never lets
  a raw httpx error out (a transport failure is reported by its class alone) and never calls
  `raise_for_status` (its message is the URL). Every error it raises names the operation
  ("edit_original"), the HTTP status and Discord's own numeric error code -- nothing else.
- That a retry is bounded. Only a 429 is retried, after the wait Discord asks for, at most
  `max_attempts` times in all and never waiting more than `max_total_wait_seconds` in total;
  past either bound the 429 is raised as it is. Every other failure is raised at once: Discord
  counts 401, 403 and 429 answers (but not a 429 whose `X-RateLimit-Scope` is `shared`) against
  an invalid-request limit, currently 10,000 per 10 minutes, and an address past it is banned for
  a while, so a client that keeps hammering a refusal is the worst kind.

The API version is pinned in the URL (v10): an unversioned request is answered by a deprecated
version. Discord asks every client to send a `User-Agent` of the form `DiscordBot (url,
version)` and may block one that does not.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/Career-Forge/between-jobs, 0.0.1)"

ATTACHMENT_HOSTS = frozenset({"cdn.discordapp.com", "media.discordapp.net"})
"""The only hosts a file a person attached is downloaded from: Discord's own CDN."""

# Discord's JSON error codes for a token that no longer works: "Unknown webhook" (10015),
# "Unknown interaction" (10062) and "Invalid webhook token" (50027).
TOKEN_EXPIRED_CODES = frozenset({10015, 10062, 50027})

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_TOTAL_WAIT_SECONDS = 10.0
REQUEST_TIMEOUT_SECONDS = 15.0
DOWNLOAD_TIMEOUT_SECONDS = 20.0

_SNOWFLAKE = re.compile(r"[0-9]{1,25}")
_ORIGINAL = "@original"
_MESSAGE_ID = re.compile(r"[0-9]{1,25}")
# A token is path material: base64url characters. Anything else (a slash, a space, a dot, so
# no ".." either) would let a value change which URL is called, so it is refused outright.
_PATH_TOKEN = re.compile(r"[A-Za-z0-9_\-]{1,512}")


class DiscordError(Exception):
    """Base of what this module raises. Messages hold the operation's name and Discord's own
    numbers, never a URL, a token or a body."""


class DiscordApiError(DiscordError):
    """Discord answered with a refusal. `code` is Discord's JSON error code (None when the
    answer carried none) and `status_code` the HTTP status; the names are the ones the app's log
    formatter writes for an exception, so a failed call leaves both in the log."""

    def __init__(self, operation: str, status_code: int, code: int | None) -> None:
        super().__init__(f"Discord refused {operation}: HTTP {status_code}, error code {code}")
        self.operation = operation
        self.status_code = status_code
        self.code = code


class DiscordTokenExpired(DiscordApiError):
    """The interaction token no longer works (it is valid for 15 minutes, or it was never
    accepted)."""


class DiscordRateLimited(DiscordApiError):
    """Discord kept answering 429 past the bound on retries, or asked for a wait longer than
    the bound allows."""

    def __init__(self, operation: str, retry_after: float) -> None:
        super().__init__(operation, 429, None)
        self.retry_after = retry_after


class DiscordTransportError(DiscordError):
    """The request never produced an answer (a network error or a timeout). Only the class of
    the underlying error is kept: an httpx error can quote the URL."""

    def __init__(self, operation: str, error_type: str) -> None:
        super().__init__(f"the request for {operation} failed ({error_type})")
        self.operation = operation
        self.error_type = error_type


class DownloadTooLarge(DiscordError):
    """A file Discord's CDN would send is larger than the caller said it would accept."""

    def __init__(self, max_bytes: int) -> None:
        super().__init__(f"the file is larger than {max_bytes} bytes")
        self.max_bytes = max_bytes


class AttachmentRefused(DiscordError):
    """An attachment was not downloaded: its address is not Discord's CDN, or the CDN
    redirected, or answered with something other than the file."""


def attachment_url_problem(url: str) -> str | None:
    """Why `url` may not be fetched as an attachment, or None when it may. Only an `https`
    address on Discord's own CDN hosts, with no credentials and no explicit port other than 443
    (a redirect is never followed, so this is the only check the address gets)."""
    if (
        not isinstance(url, str)
        or not url
        or any(ch.isspace() or not ch.isprintable() for ch in url)
    ):
        return "the address is empty or holds whitespace or control characters"
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "the address is not valid"
    if parts.scheme != "https":
        return "the address is not https"
    if parts.username is not None or parts.password is not None:
        return "the address carries credentials"
    if (parts.hostname or "").lower() not in ATTACHMENT_HOSTS:
        return "the address is not on Discord's CDN"
    if port not in (None, 443):
        return "the address names a port"
    return None


def _retry_after_seconds(response: httpx.Response) -> float:
    """How long Discord asks us to wait: the body's `retry_after` (seconds, a float), else the
    `Retry-After` header, else one second. Never negative and never absurd (an answer asking for
    a day is treated as such and refused by the bound, not slept on)."""
    candidates: list[Any] = []
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        candidates.append(body.get("retry_after"))
    candidates.append(response.headers.get("retry-after"))
    for candidate in candidates:
        if isinstance(candidate, bool):
            continue
        try:
            seconds = float(candidate)
        except (TypeError, ValueError):
            continue
        if seconds == seconds and 0 <= seconds < 10**6:  # not NaN, not negative
            return seconds
    return 1.0


def _error_code(response: httpx.Response) -> int | None:
    try:
        body = response.json()
    except ValueError:
        return None
    code = body.get("code") if isinstance(body, dict) else None
    return code if isinstance(code, int) and not isinstance(code, bool) else None


def _message_id_of(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    message_id = body.get("id") if isinstance(body, dict) else None
    return message_id if isinstance(message_id, str) and _MESSAGE_ID.fullmatch(message_id) else None


class RedactTokensFilter(logging.Filter):
    """Safety net under the rule that a token never reaches a log: rewrites an interaction
    token in a webhook path in what httpx logs. httpx writes the request URL at INFO, and the app
    keeps it at WARNING, so this should never have anything to do; it is here so one changed log
    level cannot turn a credential into a log line. It sits on the `httpx` logger only: a filter
    on a parent logger does not see the records of its children (httpcore logs through
    `httpcore.http11` and the like, and not the request URL), so the net for everything else is
    the redaction `JsonFormatter` applies to every record on the way out (`logging_setup`)."""

    _WEBHOOK_PATH = re.compile(r"(/(?:webhooks|interactions)/[0-9]{1,25}/)[A-Za-z0-9_.\-]{20,}")

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._scrub(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(self._scrub(arg) for arg in record.args)
        return True

    def _scrub(self, value: Any) -> Any:
        # httpx passes the request URL as an `httpx.URL`, not text, so anything that is not a
        # plain number is looked at as text.
        if value is None or isinstance(value, bool | int | float):
            return value
        text = value if isinstance(value, str) else str(value)
        cleaned = self._WEBHOOK_PATH.sub(r"\1<token>", text)
        return value if cleaned == text else cleaned


_httpx_logger = logging.getLogger("httpx")
if not any(isinstance(f, RedactTokensFilter) for f in _httpx_logger.filters):
    _httpx_logger.addFilter(RedactTokensFilter())


class DiscordClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        application_id: str,
        bot_token: str | None = None,
        base_url: str = API_BASE,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        max_total_wait_seconds: float = DEFAULT_MAX_TOTAL_WAIT_SECONDS,
    ) -> None:
        if _SNOWFLAKE.fullmatch(application_id) is None:
            raise ValueError("the application id is not a Discord id")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._http = http
        self._application_id = application_id
        self._bot_token = bot_token
        self._base_url = base_url.rstrip("/")
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._max_total_wait = max_total_wait_seconds

    def __repr__(self) -> str:
        # The default repr would show the bot token.
        return f"DiscordClient(application_id={self._application_id!r}, bot={self.has_bot_token})"

    @property
    def application_id(self) -> str:
        return self._application_id

    @property
    def has_bot_token(self) -> bool:
        return self._bot_token is not None

    # -- the transport -----------------------------------------------------------------

    def _bot_headers(self) -> dict[str, str]:
        if self._bot_token is None:
            raise DiscordError("this call needs a bot token and none is configured")
        return {"Authorization": f"Bot {self._bot_token}"}

    async def _request(
        self,
        operation: str,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        files: list[tuple[str, bytes]] | None = None,
        token_path: bool = False,
    ) -> httpx.Response:
        """One call with the bounded 429 retry. `files` makes it a multipart request: the JSON
        body goes in `payload_json` and each file in `files[n]`, which is how Discord takes an
        upload. `token_path` marks a path that carries an interaction token, so a 404 or 401
        answer can be read as "the token expired"."""
        url = f"{self._base_url}{path}"
        request_headers = {"User-Agent": USER_AGENT, **(headers or {})}
        waited = 0.0
        for attempt in range(1, self._max_attempts + 1):
            try:
                if files is not None:
                    response = await self._http.request(
                        method,
                        url,
                        headers=request_headers,
                        data={"payload_json": json.dumps(json_body or {}, ensure_ascii=False)},
                        files={
                            f"files[{index}]": (name, content, "application/octet-stream")
                            for index, (name, content) in enumerate(files)
                        },
                        timeout=REQUEST_TIMEOUT_SECONDS,
                    )
                else:
                    response = await self._http.request(
                        method,
                        url,
                        headers=request_headers,
                        json=json_body,
                        timeout=REQUEST_TIMEOUT_SECONDS,
                    )
            except httpx.HTTPError as exc:
                raise DiscordTransportError(operation, type(exc).__name__) from None

            if response.status_code == 429:
                delay = _retry_after_seconds(response)
                if attempt == self._max_attempts or waited + delay > self._max_total_wait:
                    logger.warning(
                        "discord rate limit: giving up",
                        extra={"ctx": {"operation": operation, "attempt": attempt}},
                    )
                    raise DiscordRateLimited(operation, delay)
                logger.info(
                    "discord rate limit: waiting",
                    extra={"ctx": {"operation": operation, "attempt": attempt, "wait": delay}},
                )
                waited += delay
                await self._sleep(delay)
                continue
            if 200 <= response.status_code < 300:
                return response

            code = _error_code(response)
            if token_path and (
                code in TOKEN_EXPIRED_CODES or (response.status_code in (401, 404) and code is None)
            ):
                raise DiscordTokenExpired(operation, response.status_code, code)
            raise DiscordApiError(operation, response.status_code, code)
        raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover

    # -- interaction calls (the token is the credential) -------------------------------

    @staticmethod
    def _checked_token(token: str) -> str:
        if _PATH_TOKEN.fullmatch(token) is None:
            raise DiscordError("the interaction token is not a valid path segment")
        return token

    def _webhook_path(self, token: str) -> str:
        return f"/webhooks/{self._application_id}/{self._checked_token(token)}"

    async def edit_interaction_message(
        self,
        token: str,
        message_id: str,
        body: dict[str, Any],
        *,
        files: list[tuple[str, bytes]] | None = None,
    ) -> str | None:
        """PATCH of a message the interaction made: `"@original"` is the first response (a
        deferred one is replaced by the edit), anything else a follow-up's id. Returns the
        message's id when Discord says it."""
        if message_id != _ORIGINAL and _MESSAGE_ID.fullmatch(message_id) is None:
            raise DiscordError("the message id is not a Discord id")
        response = await self._request(
            "edit_original" if message_id == _ORIGINAL else "edit_followup",
            "PATCH",
            f"{self._webhook_path(token)}/messages/{message_id}",
            json_body=body,
            files=files,
            token_path=True,
        )
        return _message_id_of(response)

    async def create_followup(
        self,
        token: str,
        body: dict[str, Any],
        *,
        files: list[tuple[str, bytes]] | None = None,
    ) -> str | None:
        """POST of a new message under the interaction. Discord always answers with the message
        for this call, so its id comes back."""
        response = await self._request(
            "create_followup",
            "POST",
            self._webhook_path(token),
            json_body=body,
            files=files,
            token_path=True,
        )
        return _message_id_of(response)

    # -- bot calls (the bot token is the credential) -----------------------------------

    async def open_dm(self, user_id: str) -> str:
        """The id of the direct-message channel between the app's bot and `user_id` (Discord
        returns the existing one when there is one). Opening a conversation is allowed or
        refused by Discord according to the person's own privacy settings; see
        `discord_adapter.DiscordNotifier`."""
        if _SNOWFLAKE.fullmatch(user_id) is None:
            raise DiscordError("the recipient is not a Discord id")
        response = await self._request(
            "open_dm",
            "POST",
            "/users/@me/channels",
            headers=self._bot_headers(),
            json_body={"recipient_id": user_id},
        )
        try:
            channel_id = response.json().get("id")
        except (ValueError, AttributeError):
            channel_id = None
        if not isinstance(channel_id, str) or _SNOWFLAKE.fullmatch(channel_id) is None:
            raise DiscordError("Discord's answer to open_dm held no channel id")
        return channel_id

    async def create_channel_message(
        self,
        channel_id: str,
        body: dict[str, Any],
        *,
        files: list[tuple[str, bytes]] | None = None,
    ) -> str | None:
        if _SNOWFLAKE.fullmatch(channel_id) is None:
            raise DiscordError("the channel is not a Discord id")
        response = await self._request(
            "create_channel_message",
            "POST",
            f"/channels/{channel_id}/messages",
            headers=self._bot_headers(),
            json_body=body,
            files=files,
        )
        return _message_id_of(response)

    async def edit_channel_message(
        self, channel_id: str, message_id: str, body: dict[str, Any]
    ) -> str | None:
        if _SNOWFLAKE.fullmatch(channel_id) is None or _MESSAGE_ID.fullmatch(message_id) is None:
            raise DiscordError("the channel or message is not a Discord id")
        response = await self._request(
            "edit_channel_message",
            "PATCH",
            f"/channels/{channel_id}/messages/{message_id}",
            headers=self._bot_headers(),
            json_body=body,
        )
        return _message_id_of(response)

    # -- downloads -----------------------------------------------------------------------

    async def download_attachment(self, url: str, *, max_bytes: int) -> bytes:
        """The bytes of a file a person attached. The address must be `https` on Discord's own
        CDN (`attachment_url_problem`), it is requested with no credential of any kind, a
        redirect is refused rather than followed (so there is no second address to check), and
        the body is counted as it arrives: `DownloadTooLarge` as soon as it is known to exceed
        `max_bytes`, by its Content-Length or by what has been received, without holding the
        rest."""
        problem = attachment_url_problem(url)
        if problem is not None:
            raise AttachmentRefused(problem)
        try:
            async with asyncio.timeout(DOWNLOAD_TIMEOUT_SECONDS):
                async with self._http.stream(
                    "GET",
                    url,
                    headers={"User-Agent": USER_AGENT},
                    follow_redirects=False,
                ) as response:
                    if response.is_redirect:
                        raise AttachmentRefused("the CDN redirected, which is never followed")
                    if response.status_code != 200:
                        raise AttachmentRefused(f"the CDN answered HTTP {response.status_code}")
                    declared = response.headers.get("content-length", "")
                    if declared.isascii() and declared.isdigit() and int(declared) > max_bytes:
                        raise DownloadTooLarge(max_bytes)
                    received = bytearray()
                    async for chunk in response.aiter_bytes():
                        received += chunk
                        if len(received) > max_bytes:
                            raise DownloadTooLarge(max_bytes)
                    return bytes(received)
        except httpx.HTTPError as exc:
            raise DiscordTransportError("download_attachment", type(exc).__name__) from None
        except TimeoutError:
            raise DiscordTransportError("download_attachment", "TimeoutError") from None
