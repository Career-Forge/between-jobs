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
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from supabase import AsyncClient

from .account_routes import router as account_router
from .app_state import build_push_notifier, build_webhook_probe
from .applications_routes import router as applications_router
from .auth import create_jwks_client
from .body_limit import BodyLimitMiddleware, max_request_body_bytes
from .capabilities_routes import router as capabilities_router
from .company_intel_routes import router as company_intel_router
from .contact_research_routes import router as contact_research_router
from .credentials_routes import router as credentials_router
from .deferred_reply import shutdown_deferred_replies
from .digest_listener import handle_batch as handle_digest_batch
from .discovery_routes import router as discovery_router
from .env import optional_env, refuse, web_app_url
from .error_reporting import flush_error_reporting, init_error_reporting, report_exception
from .errors import ApiError, log_api_error
from .extension_routes import router as extension_router
from .forge_engines_client import _base_url as forge_engines_base_url
from .gmail_oauth_routes import router as gmail_oauth_router
from .gmail_reply_checker import _DEFAULT_CHECK_INTERVAL_SECONDS as REPLY_CHECK_INTERVAL_SECONDS
from .gmail_reply_checker import run_reply_check_forever
from .hiring_signal_cache import PURGE_INTERVAL_SECONDS
from .hiring_signal_cache import run_purge_forever as run_hiring_cache_purge_forever
from .hiring_signal_routes import hiring_signals_allowlist
from .hiring_signal_routes import router as hiring_signal_router
from .hiring_signal_routes import status_router as hiring_signal_status_router
from .hiring_signal_search import refuse_non_provider_hosts
from .interview_practice_routes import router as interview_practice_router
from .job_registry_poller import _DEFAULT_POLL_INTERVAL_SECONDS as POLLER_INTERVAL_SECONDS
from .job_registry_poller import run_poller_forever
from .latex_service_client import _base_url as latex_service_base_url
from .latex_service_client import latex_max_concurrency
from .link_routes import router as link_router
from .logging_setup import configure_logging, request_id_var
from .outbox_store import _DEFAULT_POLL_INTERVAL_SECONDS as OUTBOX_POLL_INTERVAL_SECONDS
from .outbox_store import run_worker_forever
from .positioning_brief_routes import router as positioning_brief_router
from .product_events import emit_setup_required
from .product_events import flush as flush_product_events
from .profile_routes import router as profile_router
from .resume_documents_routes import router as resume_documents_router
from .saved_search_matcher import _DEFAULT_MATCH_INTERVAL_SECONDS as MATCHER_INTERVAL_SECONDS
from .saved_search_matcher import run_matcher_forever
from .saved_searches_routes import router as saved_searches_router
from .supabase_client import create_supabase_client
from .telegram_client import TelegramClient, parse_bot_username
from .telegram_webhook import router as telegram_router
from .tester_enrollment import tester_program_required
from .tester_enrollment_routes import router as tester_enrollment_router
from .today_routes import router as today_router
from .warm_path_events_routes import router as warm_path_events_router
from .webhook_probe import webhook_report
from .worker_lease import TTL_SECONDS as WORKER_LEASE_TTL_SECONDS
from .worker_lease import WorkerLease
from .worker_leases_store import claim_worker_lease
from .worker_pings import WorkerPings
from .worker_supervision import WorkerRegistry, WorkerState

load_dotenv()
configure_logging()

logger = logging.getLogger(__name__)

# Per-phase timeout for /health's dependency probes (see `_probe_dependency`).
_DEPENDENCY_TIMEOUT_SECONDS = 2.0


LEASED_WORKERS = frozenset({"job_registry_poller", "saved_search_matcher", "gmail_reply_checker"})
"""The workers that hold a lease (P2.19, v1): the three whose ticks select their work
with no claim, so two copies of the API would each do all of it -- and, for the
matcher and the Gmail checker, spend the user's own LLM credit twice. The outbox
claims its rows with FOR UPDATE SKIP LOCKED behind idempotent consumers, and the
cache purge converges, so neither needs one (tests/integration/test_local_outbox_claim.py
runs two real claimers to show it). Adding a worker here is the whole change."""


def _worker_leases_enabled() -> bool:
    """WORKER_LEASES=on|off, default on. Parsed strictly -- anything else stops the
    boot -- because this is a safety switch: the DISABLE_* flags read "any non-empty
    value" as true, so `WORKER_LEASES=false` handled that way would silently turn
    the guard OFF. A blank value counts as unset."""
    raw = (os.environ.get("WORKER_LEASES") or "on").strip().lower()
    if raw == "on":
        return True
    if raw == "off":
        return False
    refuse("WORKER_LEASES must be 'on' or 'off'")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Error reporting, only when SENTRY_DSN is set (error_reporting.py). First, so it is
    # running before anything below can fail or be served, and so a malformed setting stops
    # the boot like every other configuration error.
    init_error_reporting()
    # Both are read lazily (the body cap on every request, the compile limit when the first
    # PDF is compiled); reading them once here makes a bad value stop the boot with a message
    # instead of failing requests later, one by one.
    max_request_body_bytes()
    latex_max_concurrency()
    # The tester-programme gate reads its switch on every request; reading it here too makes a
    # bad value stop the boot (it is a consent check, so no spelling is guessed at).
    tester_program_required()
    # Likewise the list of people allowed to use Hiring signals, read on every request: a typo
    # in it must stop the boot, not decide who the feature is open to.
    hiring_signals_allowlist()
    # The web app's public address is optional (the bot's /privacy and /learn link to it when it
    # is set); a malformed one stops the boot here instead of reaching a person's chat.
    web_app_url()
    app.state.supabase, app.state.supabase_url = await create_supabase_client()
    app.state.jwks_client = create_jwks_client(app.state.supabase_url)

    app.state.http = httpx.AsyncClient()
    # Hiring Signals' own client: every request it makes is checked against the
    # four search-provider hosts first (see `refuse_non_provider_hosts`), so the
    # "never request linkedin.com" hard line holds at runtime as well as in the
    # source. A separate client because `app.state.http` legitimately talks to
    # many other hosts.
    app.state.hiring_http = httpx.AsyncClient(event_hooks={"request": [refuse_non_provider_hosts]})
    # Telegram is optional: a server with neither value runs web-only (the
    # webhook and the link-code route answer 404 FEATURE_DISABLED, the digest
    # push is skipped, and GET /capabilities says so). One value without the
    # other is a mistake, not "off" -- a bot that can't verify its webhook, or
    # a secret with no bot behind it -- so it stops the boot instead of quietly
    # running half-configured.
    telegram_token = optional_env("TELEGRAM_BOT_TOKEN")
    telegram_secret = optional_env("TELEGRAM_WEBHOOK_SECRET")
    if (telegram_token is None) != (telegram_secret is None):
        refuse(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET must be set together, "
            "or both left unset to run without Telegram"
        )
    app.state.telegram_client = (
        TelegramClient(app.state.http, telegram_token) if telegram_token is not None else None
    )
    app.state.telegram_webhook_secret = telegram_secret
    # Discord has no adapter yet, so a Discord link code cannot be redeemed here (the link route
    # answers FEATURE_DISABLED for it). Its own task turns this on.
    app.state.discord_enabled = False
    # Optional and cosmetic: the name the Integrations page tells people to look
    # for. A malformed value is dropped, not fatal -- a typo in a display name
    # must not keep the API from starting -- and the page then says "this
    # server's bot" instead. Only meaningful when there is a bot.
    app.state.telegram_bot_username = None
    if telegram_token is not None:
        try:
            app.state.telegram_bot_username = parse_bot_username(
                os.environ.get("TELEGRAM_BOT_USERNAME")
            )
        except ValueError:
            logger.warning("TELEGRAM_BOT_USERNAME is not a valid Telegram username; ignoring it")

    # Background workers. Each gets its own Supabase client (never
    # app.state.supabase), so its long-lived polling never shares connection
    # state with request handling, and its own DISABLE_* flag -- any non-empty
    # value turns it off, and tests/conftest.py sets all five so no test run
    # starts one against the stubbed, unreachable SUPABASE_URL. Every loop runs
    # under worker_supervision: a tick that raises is logged and retried rather
    # than ending the worker, and `app.state.workers` is what /health reports.
    #
    # - outbox (Horizon Sprint 4.0) feeds the Today digest. Job Finder P10 binds a
    #   `Notifier` into its listener via `partial`, keeping outbox_store's `Listener` a plain
    #   two-argument callable: one that fans out to every channel a user linked that has an
    #   adapter on this server (`build_push_notifier`), or None on a server with none.
    # - job_registry_poller (Job Finder P2) reuses app.state.http for its ATS calls.
    # - saved_search_matcher (Job Finder P9b) makes no raw HTTP call at all.
    # - gmail_reply_checker (Gmail reply/status parsing R3) reuses app.state.http.
    # - hiring_signal_cache_purge keeps the shared query cache's third-party text
    #   inside its lifetime even when nobody searches, and runs whether or not
    #   the feature is switched on (see `hiring_signal_cache`, "Lifetime"). It also hosts the
    #   daily Telegram webhook probe (webhook_probe.py) on a server that has a bot.
    workers = WorkerRegistry()
    app.state.workers = workers
    # Healthchecks pings (worker_pings.py): a worker pings after each successful tick when
    # its HEALTHCHECKS_URL_* setting is set, and otherwise not at all.
    pings = WorkerPings()
    app.state.worker_pings = pings
    leases_on = _worker_leases_enabled()
    # One id per process, made here (so it is post-fork and unique per process) and
    # never the bare Railway deployment id, which every replica of a deployment
    # shares: two replicas would both pass the lease's holder check. The deployment
    # prefix is only a label for the logs.
    holder = f"{(os.environ.get('RAILWAY_DEPLOYMENT_ID') or 'local')[:8]}:{uuid.uuid4().hex}"
    leases: list[WorkerLease] = []

    # The Telegram webhook probe asks Telegram once a day whether it is delivering (None on a
    # server without a bot). It runs on the cache-purge worker's tick, so it only runs where that
    # worker does; its check has its own setting, HEALTHCHECKS_URL_TELEGRAM_WEBHOOK, read (and
    # validated) here whether or not the probe will run. The flag is read as start_worker reads it.
    # DISABLE_HIRING_SIGNALS is deliberately not consulted: that switch turns a feature off, not
    # the worker, which keeps running (and keeps hosting the probe) with the feature off.
    webhook_probe = build_webhook_probe(
        app.state.telegram_client,
        pings,
        host_enabled=not os.environ.get("DISABLE_HIRING_SIGNAL_CACHE_PURGE"),
    )
    app.state.webhook_probe = webhook_probe

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
        # Read for a disabled worker too, so a malformed URL stops the boot either way.
        state.heartbeat = pings.for_worker(name, disable_env=disable_env, enabled=state.enabled)
        if state.enabled:
            worker_supabase, _worker_url = await create_supabase_client()
            # Stamped here as well as by the loop, so a worker that never
            # starts its loop still goes stale instead of "starting" forever.
            state.started_at = datetime.now(UTC)
            if name in LEASED_WORKERS and leases_on:
                lease = WorkerLease(
                    name,
                    claim=partial(
                        claim_worker_lease, worker_supabase, name, holder, WORKER_LEASE_TTL_SECONDS
                    ),
                    holder=holder,
                    wants_lease=lambda: not state.tick_running_too_long(),
                )
                state.lease = lease
                lease.start()
                leases.append(lease)
            state.task = asyncio.create_task(run(worker_supabase, state))

    async def start_workers() -> None:
        await start_worker(
            "outbox",
            interval_seconds=OUTBOX_POLL_INTERVAL_SECONDS,
            disable_env="DISABLE_OUTBOX_WORKER",
            run=lambda sb, state: run_worker_forever(
                sb,
                listeners=[
                    partial(
                        handle_digest_batch,
                        notifier=build_push_notifier(sb, app.state.telegram_client),
                    )
                ],
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
            run=lambda sb, state: run_hiring_cache_purge_forever(
                sb,
                state=state,
                after_purge=webhook_probe.run_if_due if webhook_probe is not None else None,
            ),
        )
        # Each leased worker's keeper has made its first claim by now, or failed
        # and said so (every attempt is bounded at 10 s, and they run in parallel):
        # /health therefore never answers on a state nobody has asked about yet. A
        # claim that cannot be made reads `lease_unknown` (503), which is what
        # makes a platform's first-2xx healthcheck refuse a deploy whose lease path
        # is broken -- a missing migration, a missing grant -- instead of cutting
        # over to a container whose three workers can never tick.
        if leases:
            await asyncio.gather(*(lease.first_answer() for lease in leases))

    try:
        await start_workers()
        # A healthcheck setting named after no worker is ignored; say so while the operator
        # is looking at the boot log, since a check that never gets a ping never alerts.
        pings.warn_unrecognised_settings()
    except BaseException:
        # A half-started set of workers and keepers must not outlive a failed boot.
        await workers.stop_all()
        await pings.aclose()
        raise

    # /health's dependency probes get their own small pool, so a flood of
    # health checks can't take connections from the workers or requests.
    app.state.health_http = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=3), timeout=_DEPENDENCY_TIMEOUT_SECONDS
    )
    app.state.health_dependencies = None
    app.state.health_lock = asyncio.Lock()

    yield

    # Before anything the deferred work uses is closed: a resume generation still running
    # gets a few seconds to finish, then is cancelled (see deferred_reply).
    await shutdown_deferred_replies()
    await workers.stop_all()
    # A ping still on its way is abandoned; the check's grace time covers the restart.
    await pings.aclose()
    # Product-event inserts still in flight finish (bounded) before the client they use goes.
    await flush_product_events(timeout=5.0)
    # What the workers reported while stopping goes out too, in a thread and for at most a
    # couple of seconds: an unreachable Sentry must not hold the shutdown.
    await flush_error_reporting()
    await app.state.health_http.aclose()
    await app.state.http.aclose()
    await app.state.hiring_http.aclose()


app = FastAPI(title="between-jobs", version="0.0.1", lifespan=lifespan)

# Refuses a request body over MAX_REQUEST_BODY_BYTES (body_limit.py). Registered BEFORE
# the request-id middleware and CORS below, because each `add_middleware` call wraps
# everything added so far: this ends up innermost, so the 413 it answers with passes back
# out through the request-id middleware (X-Request-ID) and CORS (the browser can read it).
app.add_middleware(BodyLimitMiddleware)

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
    except Exception as unhandled:
        # An exception no handler caught. Logged here, inside the request's
        # context, so the line carries the request id; answered here too, so
        # the 500 carries the header and the same envelope as every other error.
        logger.exception(
            "unhandled error",
            extra={"ctx": {"method": request.method, "route": _route_path(request)}},
        )
        # Also to the error tracker, when one is configured: this handler swallows the
        # exception, so it never reaches the tracker's own request middleware. The tags are
        # the route's template and the request id the log line above carries, never a URL.
        report_exception(
            unhandled,
            tags={
                "route": _route_path(request),
                "method": request.method,
                "request_id": request_id,
            },
        )
        fallback = ApiError("INTERNAL_ERROR", "Something went wrong. Try again in a moment.")
        response = JSONResponse(status_code=fallback.status_code, content=fallback.to_body())
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = request_id
    return response


# The browser extension (browser-extension.md) calls this API directly from a
# chrome-extension:// origin, and so does the deployed web frontend: its bundle
# is served from a different host than the API (launch plan P2.3). Only
# `npm run dev`, through Vite's proxy, is same-origin. Permissive by design, not
# an oversight: every route here is already gated by a verified Supabase JWT
# (auth.require_user_id) -- CORS only controls which browser-page origins may
# READ a response via fetch/XHR, it is not this API's authorization boundary, and
# no route here ever relies on a cookie (allow_credentials stays False, so this
# can't be combined with credentialed requests to leak a session).
#
# Registered AFTER request_id_middleware so it is the OUTERMOST middleware.
# Anything that middleware answers itself -- its fallback 500 for an unhandled
# error -- then still passes through here and carries the CORS headers; without
# them a cross-origin browser discards the JSON error envelope and the caller
# sees an opaque network failure ("Failed to fetch") instead of the message and
# the request id it could quote in a bug report.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    # Lets the extension and the web app read the request id they can quote in a
    # bug report, and how long a 429 asks them to wait.
    expose_headers=["X-Request-ID", "Retry-After"],
)

app.include_router(capabilities_router)
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
app.include_router(account_router)
app.include_router(extension_router)
app.include_router(hiring_signal_router)
app.include_router(hiring_signal_status_router)
app.include_router(tester_enrollment_router)


def _route_path(request: Request) -> str:
    """The route template (`/applications/{application_id}`) when the request
    matched one, else the bare path -- never the query string."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else request.url.path


@app.exception_handler(ApiError)
async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    log_api_error(logger, exc, ctx={"method": request.method, "route": _route_path(request)})
    if exc.status_code >= 500:
        # A failure of ours, not the caller's: also to the error tracker. A 4xx is the API
        # saying what was wrong with the request, and is never reported.
        report_exception(
            exc,
            tags={
                "route": _route_path(request),
                "method": request.method,
                "request_id": request_id_var.get() or "",
                "error_code": exc.code,
            },
        )
    # The one place every route's "set this up first" answer passes through, so the one place
    # that records it as a product event. The user id is what the auth dependency stored once
    # the token verified; an unauthenticated or overridden request has none and records nothing.
    emit_setup_required(
        getattr(request.app.state, "supabase", None), getattr(request.state, "user_id", None), exc
    )
    headers: dict[str, str] = {}
    if exc.code == "RATE_LIMITED":
        # Whole seconds, as the HTTP header wants; the same number is in `details`.
        retry_after = exc.details.get("retry_after_seconds")
        if isinstance(retry_after, int) and retry_after >= 1:
            headers["Retry-After"] = str(retry_after)
    return JSONResponse(status_code=exc.status_code, content=exc.to_body(), headers=headers)


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

    The three leased workers (LEASED_WORKERS) add two states. `standby`: another
    process holds the lease, so this one is alive and idle on purpose -- healthy,
    which is what lets a new container pass a platform's deploy healthcheck while
    the old one still works. `lease_unknown`: the lease could not be asked, so no
    tick can start -- a failure, immediately, so a deploy whose lease path is broken
    is refused instead of cutting over to workers that can never run. Their body
    carries `lease: {state, claim_failures, last_claim_error}`, never the holder's id.

    Dependencies are reported for diagnosis and never change the status.

    `telegram_webhook` is the daily Telegram webhook probe's last verdict (webhook_probe.py):
    `status` is `ok`, `problem`, `unknown` or `not_configured` (no bot on this server),
    `last_checked_at` when it was reached, and `reasons` the fixed reason codes. It is reported
    and nothing more: a broken webhook is not this server being unhealthy, and `/health` is
    what a deploy waits on, so it never changes the status or the status code."""
    workers: WorkerRegistry = getattr(request.app.state, "workers", WorkerRegistry())
    now = datetime.now(UTC)
    healthy = workers.healthy(now)
    body = {
        "status": "ok" if healthy else "failing",
        "workers": workers.report(now),
        "dependencies": await _check_dependencies(request),
        "telegram_webhook": webhook_report(getattr(request.app.state, "webhook_probe", None)),
    }
    return JSONResponse(status_code=200 if healthy else 503, content=body)
