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

import re
from typing import Any

import httpx

_BOT_USERNAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,31}")
"""Telegram's rule for a username: 5-32 characters, letters, digits and
underscores, starting with a letter."""


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


class TelegramClient:
    def __init__(self, http: httpx.AsyncClient, bot_token: str) -> None:
        self._http = http
        self._base_url = f"https://api.telegram.org/bot{bot_token}"
        self._file_base_url = f"https://api.telegram.org/file/bot{bot_token}"

    async def send_message(
        self, chat_id: int, text: str, *, reply_markup: dict[str, Any] | None = None
    ) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        response = await self._http.post(f"{self._base_url}/sendMessage", json=payload)
        response.raise_for_status()

    async def send_document(
        self, chat_id: int, filename: str, content: bytes, *, caption: str | None = None
    ) -> None:
        """`sendDocument` -- Telegram's outbound-file API. Unlike every
        other method here, this is multipart/form-data (`files=`), not
        JSON: Telegram's Bot API only accepts a file upload as a real
        multipart part, not a base64-encoded JSON field."""
        data: dict[str, Any] = {"chat_id": str(chat_id)}
        if caption is not None:
            data["caption"] = caption
        response = await self._http.post(
            f"{self._base_url}/sendDocument",
            data=data,
            files={"document": (filename, content, "application/octet-stream")},
        )
        response.raise_for_status()

    async def answer_callback_query(self, callback_query_id: str) -> None:
        # Always call this for a callback_query, even with nothing to say --
        # it's what stops the tapped button's loading spinner client-side.
        response = await self._http.post(
            f"{self._base_url}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id},
        )
        response.raise_for_status()

    async def get_file_path(self, file_id: str) -> str:
        response = await self._http.get(f"{self._base_url}/getFile", params={"file_id": file_id})
        response.raise_for_status()
        return str(response.json()["result"]["file_path"])

    async def download_file(self, file_path: str) -> bytes:
        response = await self._http.get(f"{self._file_base_url}/{file_path}")
        response.raise_for_status()
        return response.content

    async def download_document(self, file_id: str) -> bytes:
        file_path = await self.get_file_path(file_id)
        return await self.download_file(file_path)
