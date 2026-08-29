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
from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from postgrest.exceptions import APIError
from supabase import AsyncClient

from .app_state import get_supabase
from .applications_routes import router as applications_router
from .auth import create_jwks_client, require_user_id
from .credentials_routes import router as credentials_router
from .env import require_env
from .errors import ApiError
from .link_routes import router as link_router
from .models import CreateSessionRequest
from .profile_routes import router as profile_router
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
app.include_router(profile_router)
app.include_router(applications_router)
app.include_router(credentials_router)
app.include_router(link_router)


@app.exception_handler(ApiError)
async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_body())


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Closes a gap 2.6a left open: FastAPI's own automatic Pydantic body
    # validation (a missing/malformed required field) never reached
    # ApiError's handler at all -- it short-circuits before route code
    # runs, so every route (including profile_routes.py's, shipped in an
    # earlier sprint) was still returning FastAPI's own
    # {"detail": [...]} shape for this one failure mode. Noticed while
    # adding this sprint's own request models, since they'd have had the
    # exact same gap; fixed globally instead of letting it recur.
    fallback = ApiError(
        "INVALID_INPUT", "Invalid request.", details={"errors": jsonable_encoder(exc.errors())}
    )
    return JSONResponse(status_code=fallback.status_code, content=fallback.to_body())


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
            raise ApiError("NOT_FOUND", f"no user found for user_id {user_id!r}") from e
        # Appendix B: "Do not leak database error strings to the user" --
        # the real e.code/e.message go to the server log via `from e`,
        # never into the response body.
        raise ApiError("INTERNAL_ERROR", "Something went wrong creating that session.") from e
    return cast(dict[str, Any], result.data[0])
