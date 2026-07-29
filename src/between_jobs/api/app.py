"""FastAPI spine skeleton (Sprint 2.1, master plan Phase 2).

Deliberately minimal: a health check and one real endpoint (session
creation) that proves the loop -- request in, a real Postgres row out,
response back -- with nothing else attached yet. The agent runtime, event
bus, and Telegram bridge are separate, larger pieces that this skeleton
exists to be attached to, not things this sprint tries to also build.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import Depends, FastAPI, HTTPException, Request
from postgrest.exceptions import APIError
from supabase import AsyncClient

from .models import CreateSessionRequest
from .supabase_client import create_supabase_client

# Postgres error code for a foreign-key violation -- raised here when
# `user_id` doesn't match a real auth.users row.
_FOREIGN_KEY_VIOLATION = "23503"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.supabase = await create_supabase_client()
    yield


app = FastAPI(title="between-jobs", version="0.0.1", lifespan=lifespan)


def get_supabase(request: Request) -> AsyncClient:
    return cast(AsyncClient, request.app.state.supabase)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/sessions", status_code=201)
async def create_session(
    body: CreateSessionRequest, supabase: AsyncClient = Depends(get_supabase)
) -> dict[str, Any]:
    try:
        result = (
            await supabase.table("sessions")
            .insert({"user_id": body.user_id, "context": body.context})
            .execute()
        )
    except APIError as e:
        if e.code == _FOREIGN_KEY_VIOLATION:
            raise HTTPException(
                status_code=404, detail=f"no user found for user_id {body.user_id!r}"
            ) from e
        raise HTTPException(status_code=500, detail=f"{e.code}: {e.message}") from e
    return cast(dict[str, Any], result.data[0])
