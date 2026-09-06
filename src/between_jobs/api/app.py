"""FastAPI spine skeleton (master plan Phase 2).

Sprint 2.1 proved the loop (request -> real Postgres row -> response).
Sprint 2.2 replaced the trusted `user_id` request field with one derived
from a verified Supabase Auth access token. Sprint 2.3 adds the Telegram
bridge (telegram_webhook.py) -- the first real channel, though still with
no feature routing behind it. The agent runtime and event bus are still
separate, larger pieces this skeleton exists to be attached to.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial
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
from .company_intel_routes import router as company_intel_router
from .contact_research_routes import router as contact_research_router
from .credentials_routes import router as credentials_router
from .digest_listener import handle_batch as handle_digest_batch
from .discovery_routes import router as discovery_router
from .env import require_env
from .errors import ApiError
from .gmail_oauth_routes import router as gmail_oauth_router
from .gmail_reply_checker import run_reply_check_forever
from .interview_practice_routes import router as interview_practice_router
from .job_registry_poller import run_poller_forever
from .link_routes import router as link_router
from .models import CreateSessionRequest
from .outbox_store import run_worker_forever
from .positioning_brief_routes import router as positioning_brief_router
from .profile_routes import router as profile_router
from .resume_documents_routes import router as resume_documents_router
from .saved_search_matcher import run_matcher_forever
from .saved_searches_routes import router as saved_searches_router
from .supabase_client import create_supabase_client
from .telegram_client import TelegramClient
from .telegram_webhook import router as telegram_router
from .today_routes import router as today_router
from .warm_path_events_routes import router as warm_path_events_router

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

    # Horizon Sprint 4.0 -- the outbox's first running worker, per
    # outbox_store.py's own note that wiring this in is "a decision worth
    # making deliberately once [a listener] exists," not a side effect of
    # an earlier sprint. A second Supabase client (not app.state.supabase)
    # so the worker's own long-lived polling never shares connection
    # state with request-handling code.
    #
    # DISABLE_OUTBOX_WORKER skips this entirely -- every test in this repo
    # boots the real app (and thus this lifespan) via TestClient against a
    # stubbed, unreachable SUPABASE_URL; without the gate, the worker's
    # first poll would throw an unhandled connection error inside its own
    # task on every single test run. tests/conftest.py sets this
    # automatically so no individual test file has to know about it.
    #
    # Job Finder P10 (job-finder-p10-digest.md) binds `telegram=app.state.
    # telegram_client` (already built above) into the listener via
    # `partial` -- `outbox_store.py`'s own `Listener` type stays a plain
    # 2-arg callable, no change to that abstraction for one listener's
    # own extra dependency.
    app.state.outbox_worker_task = None
    if not os.environ.get("DISABLE_OUTBOX_WORKER"):
        worker_supabase, _worker_url = await create_supabase_client()
        app.state.outbox_worker_task = asyncio.create_task(
            run_worker_forever(
                worker_supabase,
                listeners=[partial(handle_digest_batch, telegram=app.state.telegram_client)],
            )
        )

    # Job Finder P2 -- the registry poller's own worker, same shape and
    # same reasoning as the outbox worker above: its own dedicated
    # Supabase client (never app.state.supabase), gated by its own
    # DISABLE_* env var so tests never trigger a real network/DB call,
    # cancelled the same way on shutdown. Reuses app.state.http for its
    # outbound ATS calls rather than minting a second httpx.AsyncClient.
    app.state.job_registry_poller_task = None
    if not os.environ.get("DISABLE_JOB_REGISTRY_POLLER"):
        poller_supabase, _poller_url = await create_supabase_client()
        app.state.job_registry_poller_task = asyncio.create_task(
            run_poller_forever(app.state.http, poller_supabase)
        )

    # Job Finder P9b -- the saved-search matcher's own worker, same shape
    # as the two workers above. Its own dedicated Supabase client, gated
    # by its own DISABLE_* env var, no `app.state.http` (the matcher never
    # makes a raw HTTP call -- see saved_search_matcher.py's own
    # docstring for why).
    app.state.saved_search_matcher_task = None
    if not os.environ.get("DISABLE_SAVED_SEARCH_MATCHER"):
        matcher_supabase, _matcher_url = await create_supabase_client()
        app.state.saved_search_matcher_task = asyncio.create_task(
            run_matcher_forever(matcher_supabase)
        )

    # Gmail reply/status parsing R3 -- same shape as the three workers
    # above. Its own dedicated Supabase client, gated by its own
    # DISABLE_* env var, reuses `app.state.http` for its outbound Gmail
    # calls (refresh_access_token/get_thread) rather than minting a
    # second httpx.AsyncClient.
    app.state.gmail_reply_checker_task = None
    if not os.environ.get("DISABLE_GMAIL_REPLY_CHECKER"):
        reply_checker_supabase, _reply_checker_url = await create_supabase_client()
        app.state.gmail_reply_checker_task = asyncio.create_task(
            run_reply_check_forever(app.state.http, reply_checker_supabase)
        )

    yield

    if app.state.outbox_worker_task is not None:
        app.state.outbox_worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await app.state.outbox_worker_task

    if app.state.job_registry_poller_task is not None:
        app.state.job_registry_poller_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await app.state.job_registry_poller_task

    if app.state.saved_search_matcher_task is not None:
        app.state.saved_search_matcher_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await app.state.saved_search_matcher_task

    if app.state.gmail_reply_checker_task is not None:
        app.state.gmail_reply_checker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await app.state.gmail_reply_checker_task

    await app.state.http.aclose()


app = FastAPI(title="between-jobs", version="0.0.1", lifespan=lifespan)
app.include_router(telegram_router)
app.include_router(profile_router)
app.include_router(applications_router)
app.include_router(credentials_router)
app.include_router(link_router)
app.include_router(resume_documents_router)
app.include_router(today_router)
app.include_router(discovery_router)
app.include_router(company_intel_router)
app.include_router(contact_research_router)
app.include_router(positioning_brief_router)
app.include_router(warm_path_events_router)
app.include_router(gmail_oauth_router)
app.include_router(interview_practice_router)
app.include_router(saved_searches_router)


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
