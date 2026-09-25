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
import logging
import os
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import partial
from typing import Any, cast

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
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
from .extension_routes import router as extension_router
from .forge_engines_client import _base_url as forge_engines_base_url
from .gmail_oauth_routes import router as gmail_oauth_router
from .gmail_reply_checker import _DEFAULT_CHECK_INTERVAL_SECONDS as REPLY_CHECK_INTERVAL_SECONDS
from .gmail_reply_checker import run_reply_check_forever
from .hiring_signal_cache import PURGE_INTERVAL_SECONDS
from .hiring_signal_cache import run_purge_forever as run_hiring_cache_purge_forever
from .hiring_signal_routes import router as hiring_signal_router
from .hiring_signal_routes import status_router as hiring_signal_status_router
from .hiring_signal_search import refuse_non_provider_hosts
from .interview_practice_routes import router as interview_practice_router
from .job_registry_poller import _DEFAULT_POLL_INTERVAL_SECONDS as POLLER_INTERVAL_SECONDS
from .job_registry_poller import run_poller_forever
from .latex_service_client import _base_url as latex_service_base_url
from .link_routes import router as link_router
from .logging_setup import configure_logging, request_id_var
from .models import CreateSessionRequest
from .outbox_store import _DEFAULT_POLL_INTERVAL_SECONDS as OUTBOX_POLL_INTERVAL_SECONDS
from .outbox_store import run_worker_forever
from .positioning_brief_routes import router as positioning_brief_router
from .profile_routes import router as profile_router
from .resume_documents_routes import router as resume_documents_router
from .saved_search_matcher import _DEFAULT_MATCH_INTERVAL_SECONDS as MATCHER_INTERVAL_SECONDS
from .saved_search_matcher import run_matcher_forever
from .saved_searches_routes import router as saved_searches_router
from .supabase_client import create_supabase_client
from .telegram_client import TelegramClient
from .telegram_webhook import router as telegram_router
from .today_routes import router as today_router
from .warm_path_events_routes import router as warm_path_events_router
from .worker_supervision import WorkerRegistry, WorkerState

load_dotenv()
configure_logging()

logger = logging.getLogger(__name__)

# Postgres error code for a foreign-key violation -- raised here when the
# verified user_id doesn't match a real auth.users row (shouldn't happen
# in practice once auth is real, but a deleted-user race is possible).
_FOREIGN_KEY_VIOLATION = "23503"

# Per-phase timeout for /health's dependency probes (see `_probe_dependency`).
_DEPENDENCY_TIMEOUT_SECONDS = 2.0


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.supabase, app.state.supabase_url = await create_supabase_client()
    app.state.jwks_client = create_jwks_client(app.state.supabase_url)

    app.state.http = httpx.AsyncClient()
    # Hiring Signals' own client: every request it makes is checked against the
    # four search-provider hosts first (see `refuse_non_provider_hosts`), so the
    # "never request linkedin.com" hard line holds at runtime as well as in the
    # source. A separate client because `app.state.http` legitimately talks to
    # many other hosts.
    app.state.hiring_http = httpx.AsyncClient(event_hooks={"request": [refuse_non_provider_hosts]})
    app.state.telegram_client = TelegramClient(app.state.http, require_env("TELEGRAM_BOT_TOKEN"))
    app.state.telegram_webhook_secret = require_env("TELEGRAM_WEBHOOK_SECRET")

    # Background workers. Each gets its own Supabase client (never
    # app.state.supabase), so its long-lived polling never shares connection
    # state with request handling, and its own DISABLE_* flag -- any non-empty
    # value turns it off, and tests/conftest.py sets all five so no test run
    # starts one against the stubbed, unreachable SUPABASE_URL. Every loop runs
    # under worker_supervision: a tick that raises is logged and retried rather
    # than ending the worker, and `app.state.workers` is what /health reports.
    #
    # - outbox (Horizon Sprint 4.0) feeds the Today digest. Job Finder P10 binds
    #   `telegram=app.state.telegram_client` into its listener via `partial`,
    #   keeping outbox_store's `Listener` a plain two-argument callable.
    # - job_registry_poller (Job Finder P2) reuses app.state.http for its ATS calls.
    # - saved_search_matcher (Job Finder P9b) makes no raw HTTP call at all.
    # - gmail_reply_checker (Gmail reply/status parsing R3) reuses app.state.http.
    # - hiring_signal_cache_purge keeps the shared query cache's third-party text
    #   inside its lifetime even when nobody searches, and runs whether or not
    #   the feature is switched on (see `hiring_signal_cache`, "Lifetime").
    workers = WorkerRegistry()
    app.state.workers = workers

    async def start_worker(
        name: str,
        *,
        interval_seconds: float,
        disable_env: str,
        run: Callable[[AsyncClient, WorkerState], Coroutine[Any, Any, None]],
        backoff_base_seconds: float | None = None,
    ) -> None:
        state = workers.register(
            name,
            interval_seconds=interval_seconds,
            enabled=not os.environ.get(disable_env),
            backoff_base_seconds=backoff_base_seconds,
        )
        if state.enabled:
            worker_supabase, _worker_url = await create_supabase_client()
            # Stamped here as well as by the loop, so a worker that never
            # starts its loop still goes stale instead of "starting" forever.
            state.started_at = datetime.now(UTC)
            state.task = asyncio.create_task(run(worker_supabase, state))

    await start_worker(
        "outbox",
        interval_seconds=OUTBOX_POLL_INTERVAL_SECONDS,
        disable_env="DISABLE_OUTBOX_WORKER",
        run=lambda sb, state: run_worker_forever(
            sb,
            listeners=[partial(handle_digest_batch, telegram=app.state.telegram_client)],
            state=state,
        ),
    )
    await start_worker(
        "job_registry_poller",
        interval_seconds=POLLER_INTERVAL_SECONDS,
        disable_env="DISABLE_JOB_REGISTRY_POLLER",
        run=lambda sb, state: run_poller_forever(app.state.http, sb, state=state),
        # A retry re-fetches every due board, so it starts at a minute, not 5s.
        backoff_base_seconds=60.0,
    )
    await start_worker(
        "saved_search_matcher",
        interval_seconds=MATCHER_INTERVAL_SECONDS,
        disable_env="DISABLE_SAVED_SEARCH_MATCHER",
        run=lambda sb, state: run_matcher_forever(sb, state=state),
    )
    await start_worker(
        "gmail_reply_checker",
        interval_seconds=REPLY_CHECK_INTERVAL_SECONDS,
        disable_env="DISABLE_GMAIL_REPLY_CHECKER",
        run=lambda sb, state: run_reply_check_forever(app.state.http, sb, state=state),
    )
    await start_worker(
        "hiring_signal_cache_purge",
        interval_seconds=PURGE_INTERVAL_SECONDS,
        disable_env="DISABLE_HIRING_SIGNAL_CACHE_PURGE",
        run=lambda sb, state: run_hiring_cache_purge_forever(sb, state=state),
    )

    # /health's dependency probes get their own small pool, so a flood of
    # health checks can't take connections from the workers or requests.
    app.state.health_http = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=3), timeout=_DEPENDENCY_TIMEOUT_SECONDS
    )
    app.state.health_dependencies = None
    app.state.health_lock = asyncio.Lock()

    yield

    await workers.stop_all()
    await app.state.health_http.aclose()
    await app.state.http.aclose()
    await app.state.hiring_http.aclose()


app = FastAPI(title="between-jobs", version="0.0.1", lifespan=lifespan)
# The browser extension (browser-extension.md) calls this API directly
# from a chrome-extension:// origin -- unlike the web frontend, which
# never triggers a real CORS check at all (Vite's dev proxy makes its
# requests same-origin from the browser's own point of view). Permissive
# by design, not an oversight: every route here is already gated by a
# verified Supabase JWT (auth.require_user_id) -- CORS only controls
# which browser-page origins may READ a response via fetch/XHR, it is not
# this API's authorization boundary, and no route here ever relies on a
# cookie (allow_credentials stays False, so this can't be combined with
# credentialed requests to leak a session).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    # Lets the extension read the request id it can quote in a bug report.
    expose_headers=["X-Request-ID"],
)

# A caller-supplied id is kept only when it looks like one; anything else is
# replaced, so a request can't inject arbitrary text into every log line.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{8,64}$")


@app.middleware("http")
async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    incoming = request.headers.get("x-request-id", "")
    request_id = incoming if _REQUEST_ID_RE.fullmatch(incoming) else uuid.uuid4().hex
    token = request_id_var.set(request_id)
    try:
        response = await call_next(request)
    except Exception:
        # An exception no handler caught. Logged here, inside the request's
        # context, so the line carries the request id; answered here too, so
        # the 500 carries the header and the same envelope as every other error.
        logger.exception(
            "unhandled error",
            extra={"ctx": {"method": request.method, "route": _route_path(request)}},
        )
        fallback = ApiError("INTERNAL_ERROR", "Something went wrong. Try again in a moment.")
        response = JSONResponse(status_code=fallback.status_code, content=fallback.to_body())
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = request_id
    return response


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
app.include_router(extension_router)
app.include_router(hiring_signal_router)
app.include_router(hiring_signal_status_router)


def _route_path(request: Request) -> str:
    """The route template (`/applications/{application_id}`) when the request
    matched one, else the bare path -- never the query string."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else request.url.path


@app.exception_handler(ApiError)
async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    # The cause's type (and a Postgres SQLSTATE when it carries one) is what
    # makes a 500 diagnosable; its message is left out because database and
    # provider messages can quote the values involved.
    cause = exc.__cause__
    ctx: dict[str, object] = {
        "code": exc.code,
        "status": exc.status_code,
        "method": request.method,
        "route": _route_path(request),
    }
    if cause is not None:
        ctx["cause_type"] = f"{type(cause).__module__}.{type(cause).__qualname__}"
        cause_code = getattr(cause, "code", None)
        if isinstance(cause_code, str) and len(cause_code) <= 16:
            ctx["cause_code"] = cause_code
    # A provider refusing or failing (a user's own key, a rate limit) is not
    # this service breaking, so those 5xx codes log a level lower.
    if exc.status_code < 500:
        level = logging.INFO
    elif exc.code.startswith("PROVIDER_"):
        level = logging.WARNING
    else:
        level = logging.ERROR
    logger.log(level, "api error %s", exc.code, extra={"ctx": ctx})
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
    #
    # Only WHERE and WHY go into the body (`loc`, `type`, `msg`), never the offending
    # `input` or its `ctx`. The input is what somebody typed: echoing it back made
    # a 3 MB request a 3 MB response, and a lone surrogate or a NaN in it could not be
    # encoded at all, so a request the client got wrong answered a bare 500 instead of
    # this 422.
    errors = [
        {"type": e.get("type"), "loc": list(e.get("loc", ())), "msg": e.get("msg")}
        for e in exc.errors()
    ]
    logger.info(
        "request validation failed",
        extra={
            "ctx": {
                "method": request.method,
                "route": _route_path(request),
                "errors": len(errors),
                "fields": ",".join(".".join(str(p) for p in e["loc"]) for e in errors)[:300],
            }
        },
    )
    fallback = ApiError(
        "INVALID_INPUT", "Invalid request.", details={"errors": jsonable_encoder(errors)}
    )
    return JSONResponse(status_code=fallback.status_code, content=fallback.to_body())


_DEPENDENCY_CACHE_SECONDS = 30.0


async def _probe_dependency(http: httpx.AsyncClient, url: str) -> str:
    """ok (it answered below 500), error (it answered 5xx) or unreachable --
    which also covers a malformed URL and a probe that overran its budget."""
    try:
        async with asyncio.timeout(_DEPENDENCY_TIMEOUT_SECONDS + 0.5):
            response = await http.get(url)
    except Exception as e:
        # One short line (the /health cache bounds how often): which dependency
        # and why, never the URL's path or any message text.
        logger.info(
            "dependency probe failed",
            extra={
                "ctx": {
                    "host": url.split("://", 1)[-1].split("/", 1)[0],
                    "error_type": f"{type(e).__module__}.{type(e).__qualname__}",
                }
            },
        )
        return "unreachable"
    return "ok" if response.status_code < 500 else "error"


async def _check_dependencies(request: Request) -> dict[str, str]:
    """Reachability of Supabase, forge-engines and the LaTeX service, cached
    for 30s and computed by one caller at a time, so hitting /health in a loop
    can't multiply outbound traffic."""
    names = ("supabase", "forge_engines", "latex_service")
    # Off under tests (tests/conftest.py), like the workers: they would otherwise
    # call real hosts. Reported as not_checked rather than guessed.
    if os.environ.get("DISABLE_HEALTH_DEPENDENCY_CHECKS"):
        return dict.fromkeys(names, "not_checked")
    state = request.app.state
    async with state.health_lock:
        cached = getattr(state, "health_dependencies", None)
        loop_now = asyncio.get_running_loop().time()
        if cached is not None and loop_now - cached[0] < _DEPENDENCY_CACHE_SECONDS:
            return dict(cached[1])
        urls = (
            # Any answer means the project is up; without a key this is a 401.
            f"{state.supabase_url.rstrip('/')}/rest/v1/",
            f"{forge_engines_base_url()}/health",
            f"{latex_service_base_url()}/health",
        )
        http: httpx.AsyncClient = state.health_http
        results = await asyncio.gather(*(_probe_dependency(http, url) for url in urls))
        report = dict(zip(names, results, strict=True))
        state.health_dependencies = (loop_now, report)
        return report


@app.api_route("/health", methods=["GET", "HEAD"])
async def health(request: Request) -> JSONResponse:
    """200 when every enabled worker is running (or starting, or retrying
    within its window), 503 when one has died, not finished a tick for 3x its
    interval, or failed every tick for too long -- the check UptimeRobot
    watches (use the status code, or the keyword `"status":"ok"`). A worker
    switched off with DISABLE_* reports `disabled`, which is not a failure.
    Dependencies are reported for diagnosis and never change the status."""
    workers: WorkerRegistry = getattr(request.app.state, "workers", WorkerRegistry())
    now = datetime.now(UTC)
    healthy = workers.healthy(now)
    body = {
        "status": "ok" if healthy else "failing",
        "workers": workers.report(now),
        "dependencies": await _check_dependencies(request),
    }
    return JSONResponse(status_code=200 if healthy else 503, content=body)


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
        # the cause's type and SQLSTATE go to the server log (the ApiError
        # handler logs `__cause__`), never into the response body.
        raise ApiError("INTERNAL_ERROR", "Something went wrong creating that session.") from e
    return cast(dict[str, Any], result.data[0])
