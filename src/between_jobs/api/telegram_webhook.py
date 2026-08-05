"""Telegram webhook receiver.

Deliberately minimal: verify the update is really from Telegram, resolve
(or create) the sender's identity, send a plain acknowledgment. No feature
routing -- "find/apply/track/prefs" parity with the n8n bot needs the
agent runtime, which doesn't exist yet. This proves the channel wiring
(Telegram message in -> identity resolved -> Postgres -> a real reply
sent back to Telegram) without pretending to have built the features on
top of it.

Always returns 200 for a well-authenticated request, even for update
shapes it doesn't handle (e.g. non-message updates) -- Telegram retries
on non-2xx, and there's nothing to retry here since not every update
needs an action.
"""

from __future__ import annotations

import hmac
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from supabase import AsyncClient

from .app_state import get_supabase, get_telegram_client, get_webhook_secret
from .telegram_client import TelegramClient
from .telegram_identity import resolve_or_create_user_id

router = APIRouter()

_ACK_TEXT = "Got it -- you're linked. (Feature routing isn't built yet.)"


def _verify_webhook_secret(request: Request, expected: str = Depends(get_webhook_secret)) -> None:
    got = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not hmac.compare_digest(got, expected):
        raise HTTPException(status_code=401, detail="invalid webhook secret")


@router.post("/telegram/webhook", dependencies=[Depends(_verify_webhook_secret)])
async def telegram_webhook(
    request: Request,
    supabase: AsyncClient = Depends(get_supabase),
    telegram: TelegramClient = Depends(get_telegram_client),
) -> dict[str, str]:
    update = cast(dict[str, Any], await request.json())
    message = update.get("message")
    if not isinstance(message, dict) or "text" not in message:
        return {"status": "ignored"}

    telegram_user_id = message["from"]["id"]
    chat_id = message["chat"]["id"]

    await resolve_or_create_user_id(supabase, telegram_user_id)
    await telegram.send_message(chat_id, _ACK_TEXT)
    return {"status": "ok"}
