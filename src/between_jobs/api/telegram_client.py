"""Thin wrapper over the Telegram Bot API.

Just enough to send a reply. Takes a shared `httpx.AsyncClient` rather than
constructing its own per call -- connection pooling, same reasoning as the
Supabase client being built once in lifespan rather than per-request.
"""

from __future__ import annotations

import httpx


class TelegramClient:
    def __init__(self, http: httpx.AsyncClient, bot_token: str) -> None:
        self._http = http
        self._base_url = f"https://api.telegram.org/bot{bot_token}"

    async def send_message(self, chat_id: int, text: str) -> None:
        response = await self._http.post(
            f"{self._base_url}/sendMessage", json={"chat_id": chat_id, "text": text}
        )
        response.raise_for_status()
