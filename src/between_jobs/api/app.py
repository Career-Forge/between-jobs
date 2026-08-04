"""FastAPI spine skeleton (master plan Phase 2).

Sprint 2.1 proved the loop (request -> real Postgres row -> response).
Sprint 2.2 replaces the trusted `user_id` request field with one derived
from a verified Supabase Auth access token -- `POST /sessions` now
requires a real `Authorization: Bearer <jwt>` header. The agent runtime,
event bus, and Telegram bridge are still separate, larger pieces this
skeleton exists to be attached to.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import Depends, FastAPI, HTTPException, Request
from postgrest.exceptions import APIError
from supabase import AsyncClient

from .auth import create_jwks_client, require_user_id
from .models import CreateSessionRequest
from .supabase_client import create_supabase_client

# Postgres error code for a foreign-key violation -- raised here when the
# verified user_id doesn't match a real auth.users row (shouldn't happen
# in practice once auth is real, but a deleted-user race is possible).
_FOREIGN_KEY_VIOLATION = "23503"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.supabase, app.state.supabase_url = await create_supabase_client()
    app.state.jwks_client = create_jwks_client(app.state.supabase_url)
    yield


app = FastAPI(title="between-jobs", version="0.0.1", lifespan=lifespan)


def get_supabase(request: Request) -> AsyncClient:
    return cast(AsyncClient, request.app.state.supabase)


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
