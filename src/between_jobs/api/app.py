"""FastAPI spine skeleton (master plan Phase 2).

Sprint 2.1 proved the loop (request -> real Postgres row -> response).
Sprint 2.2 replaced the trusted `user_id` request field with one derived
from a verified Supabase Auth access token. Sprint 2.3 adds the Telegram
bridge (telegram_webhook.py) -- the first real channel, though still with
no feature routing behind it. The agent runtime and event bus are still
separate, larger pieces this skeleton exists to be attached to.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from postgrest.exceptions import APIError
from supabase import AsyncClient

from .app_state import get_supabase
from .auth import create_jwks_client, require_user_id
from .env import require_env
from .models import CreateSessionRequest
from .supabase_client import create_supabase_client
from .telegram_client import TelegramClient
from .telegram_webhook import router as telegram_router

load_dotenv()

# Postgres error code for a foreign-key violation -- raised here when the
# verified user_id doesn't match a real auth.users row (shouldn't happen
# in practice once auth is real, but a deleted-user race is possible).
_FOREIGN_KEY_VIOLATION = "23503"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.supabase, app.state.supabase_url = await create_supabase_client()
    app.state.jwks_client = create_jwks_client(app.state.supabase_url)

    app.state.http = httpx.AsyncClient()
    app.state.telegram_client = TelegramClient(app.state.http, require_env("TELEGRAM_BOT_TOKEN"))
    app.state.telegram_webhook_secret = require_env("TELEGRAM_WEBHOOK_SECRET")

    yield

    await app.state.http.aclose()


app = FastAPI(title="between-jobs", version="0.0.1", lifespan=lifespan)
app.include_router(telegram_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/sessions", status_code=201)
async def create_session(
    body: CreateSessionRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    try:
        result = (
            await supabase.table("sessions")
            .insert({"user_id": user_id, "context": body.context})
            .execute()
        )
    except APIError as e:
        if e.code == _FOREIGN_KEY_VIOLATION:
            raise HTTPException(
                status_code=404, detail=f"no user found for user_id {user_id!r}"
            ) from e
        raise HTTPException(status_code=500, detail=f"{e.code}: {e.message}") from e
    return cast(dict[str, Any], result.data[0])
