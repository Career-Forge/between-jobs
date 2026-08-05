"""Telegram webhook receiver.

Verifies the update is really from Telegram, resolves (or creates) the
sender's identity, classifies the message, and dispatches to whichever of
the two real intents (Sprint 2.4) it matches -- or a fallback for anything
else. "find/apply/track" parity with the n8n bot needs the full agent
runtime, which doesn't exist yet; this is the first real slice of it.

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
from .intents import Intent, classify
from .resumes import get_resume, save_resume
from .telegram_client import TelegramClient
from .telegram_identity import resolve_or_create_user_id

router = APIRouter()

_FALLBACK_TEXT = (
    'I can do two things right now: send "my resume: <paste your resume>" to set '
    'one up, or "check my resume" to see what\'s on file.'
)
_RESUME_SAVED_TEXT = (
    'Got it -- saved your resume. Send "check my resume" any time to confirm what\'s on file.'
)
_NO_RESUME_TEXT = (
    'I don\'t have a resume on file yet. Send "my resume: <paste your resume text>" to set one up.'
)


def _verify_webhook_secret(request: Request, expected: str = Depends(get_webhook_secret)) -> None:
    got = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not hmac.compare_digest(got, expected):
        raise HTTPException(status_code=401, detail="invalid webhook secret")


async def _handle_message(supabase: AsyncClient, user_id: str, text: str) -> str:
    classification = classify(text)

    if classification.intent == Intent.SET_UP_RESUME:
        assert classification.resume_text is not None
        await save_resume(supabase, user_id, classification.resume_text)
        return _RESUME_SAVED_TEXT

    if classification.intent == Intent.CHECK_RESUME:
        resume = await get_resume(supabase, user_id)
        if resume is None:
            return _NO_RESUME_TEXT
        preview = resume["raw_text"][:200]
        return f"Yes, I have a resume on file (updated {resume['updated_at']}). Preview:\n{preview}"

    return _FALLBACK_TEXT


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

    user_id = await resolve_or_create_user_id(supabase, telegram_user_id)
    reply = await _handle_message(supabase, user_id, message["text"])
    await telegram.send_message(chat_id, reply)
    return {"status": "ok"}
