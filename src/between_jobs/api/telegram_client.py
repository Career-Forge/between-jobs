"""Thin wrapper over the Telegram Bot API.

Takes a shared `httpx.AsyncClient` rather than constructing its own per
call -- connection pooling, same reasoning as the Supabase client being
built once in lifespan rather than per-request.

Sprint 2.5d adds file download (for .json resume uploads) and callback-
query answering (for the confirm/cancel preview buttons) -- the file
download endpoint lives under a DIFFERENT host path (`/file/bot<token>/`,
not `/bot<token>/`), per Telegram's own API split between the bot API and
file API.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BOT_USERNAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,31}")
"""Telegram's rule for a username: 5-32 characters, letters, digits and
underscores, starting with a letter."""


class DownloadTooLarge(Exception):
    """A file Telegram would send is larger than the caller said it would accept."""

    def __init__(self, max_bytes: int) -> None:
        super().__init__(f"the file is larger than {max_bytes} bytes")
        self.max_bytes = max_bytes


class TelegramApiError(Exception):
    """A Bot API call failed, said in a few fixed words. `code` is one of:

    - `timeout`: no answer in time;
    - `transport`: the request could not be made or completed (DNS, TLS, a reset connection);
    - `refused`: Telegram answered with an HTTP status outside 2xx (a wrong bot token is a 401);
    - `malformed`: Telegram answered 2xx, but not with `{"ok": true, "result": {...}}`.

    `status_code` is Telegram's HTTP status, when there was one. The message is made of those
    two and nothing else: not the request URL (the bot token is part of it), not the exception
    that was caught, and not Telegram's own `description` text. So it is safe in a log line and
    in an error report, and nothing needs to scrub it."""

    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        detail = code if status_code is None else f"{code}, HTTP {status_code}"
        super().__init__(f"the Telegram Bot API call failed ({detail})")
        self.code = code
        self.status_code = status_code


def parse_bot_username(raw: str | None) -> str | None:
    """The bot's public username, without a leading "@", or None when none was
    given. Raises ValueError (naming no value) for something that can't be one.
    Shown to people so they can find the bot; it is not a credential."""
    if raw is None or raw.strip() == "":
        return None
    name = raw.strip().removeprefix("@")
    if _BOT_USERNAME.fullmatch(name) is None:
        raise ValueError("not a valid Telegram username")
    return name


# -- message text ---------------------------------------------------------------------
#
# Every message is sent with parse_mode "HTML", never left to Telegram's default. HTML only
# needs three characters escaped (& < >), where MarkdownV2 needs eighteen, and a message
# whose text Telegram cannot parse is rejected outright. So:
#   - a plain `str` is TEXT: the client escapes it. Names, titles, error messages and
#     anything else that came from outside can be passed as they are and can never be read
#     as markup.
#   - `Html` is MARKUP written by us (a template with <b>, <i>, <code>, <pre>), checked when
#     it is built, so a template with a stray "<" or an unclosed tag fails at import and in
#     the test run, not in a user's chat. Values go into a template through `render`, which
#     escapes them.
# <pre> and <code> render as tap-to-copy blocks in Telegram, which is what the resume
# template message needs.

_TAG = re.compile(r"<(/?)([a-z]+)>")
_ALLOWED_TAGS = frozenset({"b", "i", "code", "pre"})
_BARE_AMPERSAND = re.compile(r"&(?!(?:amp|lt|gt|quot|#\d+);)")


def _check_markup(value: str) -> None:
    open_tags: list[str] = []
    for match in _TAG.finditer(value):
        closing, name = match.group(1) == "/", match.group(2)
        if name not in _ALLOWED_TAGS:
            raise ValueError(f"<{name}> is not a tag Telegram messages here may use")
        if not closing:
            open_tags.append(name)
        elif not open_tags or open_tags.pop() != name:
            raise ValueError(f"</{name}> closes nothing that is open")
    if open_tags:
        raise ValueError(f"<{open_tags[-1]}> is never closed")
    remainder = _TAG.sub("", value)
    if "<" in remainder or ">" in remainder:
        raise ValueError("a literal < or > must be written &lt; or &gt;")
    if _BARE_AMPERSAND.search(remainder):
        raise ValueError("a literal & must be written &amp;")


class Html(str):
    """Message text that is already Telegram HTML. See the note above."""

    __slots__ = ()

    def __new__(cls, value: str) -> Html:
        _check_markup(value)
        return super().__new__(cls, value)


def escape(value: object) -> str:
    """`value` as text that is safe to put inside an `Html` message."""
    return html.escape(str(value), quote=False)


def render(template: Html, **values: object) -> Html:
    """Fills `template`'s {placeholders} with escaped `values`; the result is `Html`."""
    return Html(template.format(**{key: escape(value) for key, value in values.items()}))


def _plain_text_of(markup: str) -> str:
    return html.unescape(_TAG.sub("", markup))


def _is_entity_error(response: httpx.Response) -> bool:
    if response.status_code != 400:
        return False
    try:
        description = str(response.json().get("description", ""))
    except ValueError:
        return False
    return "can't parse entities" in description


def _html_body(text: str) -> str:
    """What goes in the request's `text`: an `Html` as written, anything else escaped."""
    return str(text) if isinstance(text, Html) else html.escape(text, quote=False)


_WEBHOOK_INFO_TIMEOUT_SECONDS = 10.0


def _webhook_info_of(response: httpx.Response) -> tuple[str | None, dict[str, Any] | None]:
    """(failure code, None) or (None, the `result` object) for a getWebhookInfo answer."""
    if not response.is_success:
        return "refused", None
    try:
        body = response.json()
    except (ValueError, RecursionError):  # not JSON at all, or nested past what json will read
        return "malformed", None
    if not isinstance(body, dict) or body.get("ok") is not True:
        return "malformed", None
    result = body.get("result")
    if not isinstance(result, dict):
        return "malformed", None
    return None, result


def _message_id_of(response: httpx.Response) -> int | None:
    try:
        result = response.json().get("result")
    except (ValueError, AttributeError):
        return None
    message_id = result.get("message_id") if isinstance(result, dict) else None
    return message_id if isinstance(message_id, int) and not isinstance(message_id, bool) else None


class TelegramClient:
    def __init__(self, http: httpx.AsyncClient, bot_token: str) -> None:
        self._http = http
        self._base_url = f"https://api.telegram.org/bot{bot_token}"
        self._file_base_url = f"https://api.telegram.org/file/bot{bot_token}"

    async def send_message(
        self, chat_id: int, text: str, *, reply_markup: dict[str, Any] | None = None
    ) -> int | None:
        """Sends `text` as an HTML-mode message: escaped if it is a plain `str`, as written if
        it is `Html`. If Telegram still cannot parse it (a bug in a template or a value that
        slipped past `render`) the message is sent once more as plain text instead of being
        lost -- a user who is told nothing is worse off than one who sees a stray tag.

        Returns Telegram's id for the message, or None if the answer did not carry one."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": _html_body(text),
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return _message_id_of(await self._post_html("sendMessage", payload))

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> int | None:
        """`editMessageText`: replaces a sent message's text, with the same HTML handling and
        plain-text fallback as `send_message`. Without `reply_markup` the message loses any
        inline keyboard it had -- that is how Telegram treats an edit. Returns the edited
        message's id, or None if the answer did not carry one."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": _html_body(text),
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return _message_id_of(await self._post_html("editMessageText", payload))

    async def _post_html(self, method: str, payload: dict[str, Any]) -> httpx.Response:
        response = await self._http.post(f"{self._base_url}/{method}", json=payload)
        if _is_entity_error(response):
            logger.warning("telegram could not parse a message; resending it as plain text")
            payload["text"] = _plain_text_of(payload["text"])
            del payload["parse_mode"]
            response = await self._http.post(f"{self._base_url}/{method}", json=payload)
        response.raise_for_status()
        return response

    async def send_document(
        self, chat_id: int, filename: str, content: bytes, *, caption: str | None = None
    ) -> int | None:
        """`sendDocument` -- Telegram's outbound-file API. Unlike every
        other method here, this is multipart/form-data (`files=`), not
        JSON: Telegram's Bot API only accepts a file upload as a real
        multipart part, not a base64-encoded JSON field. Returns Telegram's id for the
        message, or None if the answer did not carry one."""
        data: dict[str, Any] = {"chat_id": str(chat_id)}
        if caption is not None:
            data["caption"] = caption
        response = await self._http.post(
            f"{self._base_url}/sendDocument",
            data=data,
            files={"document": (filename, content, "application/octet-stream")},
        )
        response.raise_for_status()
        return _message_id_of(response)

    async def answer_callback_query(
        self, callback_query_id: str, *, text: str | None = None
    ) -> None:
        # Always call this for a callback_query, even with nothing to say --
        # it's what stops the tapped button's loading spinner client-side. `text`, when
        # given, is shown to the person who tapped as a brief notice.
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text is not None:
            payload["text"] = text
        response = await self._http.post(f"{self._base_url}/answerCallbackQuery", json=payload)
        response.raise_for_status()

    async def get_webhook_info(self) -> dict[str, Any]:
        """`getWebhookInfo`: what Telegram itself reports about delivering updates to this
        bot's webhook -- the `WebhookInfo` object, as the parsed JSON dict, unchecked (the
        webhook probe decides what it means). Raises `TelegramApiError` when there is no usable
        answer.

        THE BOT TOKEN IS IN THE URL PATH, so this method never lets the request, its URL or
        anything that quotes them out: an httpx error message and `raise_for_status` both
        carry the URL, and an exception raised inside an `except` block keeps the original as
        its `__context__`. So the failure is only noted inside the `except` blocks, and the
        error that leaves is raised after them, a new exception that holds a code and a status.
        (httpx's own INFO log line for the request also quotes the URL: `configure_logging`
        keeps httpx at WARNING, and the log redactor knows the shape of a bot token.)"""
        failure: str | None = None
        status_code: int | None = None
        result: dict[str, Any] | None = None
        try:
            response = await self._http.get(
                f"{self._base_url}/getWebhookInfo", timeout=_WEBHOOK_INFO_TIMEOUT_SECONDS
            )
        except httpx.TimeoutException:
            failure = "timeout"
        except (httpx.HTTPError, httpx.InvalidURL):
            failure = "transport"
        else:
            status_code = response.status_code
            failure, result = _webhook_info_of(response)
        if failure is not None or result is None:
            raise TelegramApiError(failure or "malformed", status_code=status_code)
        return result

    async def get_file_path(self, file_id: str) -> str:
        response = await self._http.get(f"{self._base_url}/getFile", params={"file_id": file_id})
        response.raise_for_status()
        return str(response.json()["result"]["file_path"])

    async def download_file(self, file_path: str, *, max_bytes: int | None = None) -> bytes:
        """The file's bytes. With `max_bytes`, raises `DownloadTooLarge` as soon as the file is
        known to be (by its Content-Length) or turns out to be (by counting the bytes as they
        arrive) larger than that, without holding the rest of it in memory; the size Telegram
        reports for a file is only advisory, so it is never the only check."""
        async with self._http.stream("GET", f"{self._file_base_url}/{file_path}") as response:
            response.raise_for_status()
            if max_bytes is None:
                return await response.aread()
            declared = response.headers.get("content-length", "")
            if declared.isascii() and declared.isdigit() and int(declared) > max_bytes:
                raise DownloadTooLarge(max_bytes)
            received = bytearray()
            async for chunk in response.aiter_bytes():
                received += chunk
                if len(received) > max_bytes:
                    raise DownloadTooLarge(max_bytes)
            return bytes(received)

    async def download_document(self, file_id: str, *, max_bytes: int | None = None) -> bytes:
        file_path = await self.get_file_path(file_id)
        return await self.download_file(file_path, max_bytes=max_bytes)
