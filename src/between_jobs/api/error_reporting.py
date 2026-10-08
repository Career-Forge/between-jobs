"""Optional error reporting to Sentry. Off unless the operator sets SENTRY_DSN.

Nothing here runs, and `sentry_sdk` is never imported, until a DSN is configured: a
self-hoster who wants no error tracker gets no extra code path and no network call.

WHAT IS REPORTED, on purpose and nothing else:

- an exception that no handler caught, which the request middleware in app.py turns into
  the generic 500 (it is reported there, with the route's template, the method and the
  request id -- the same id the JSON log line carries -- as tags);
- an `ApiError` the API answers with a 5xx, on the web or in a chat message (the chat
  boundary in channel_core.py reports for itself, since it never reaches the web handler).
  A 4xx is the API telling a caller what was wrong with the request, so it is never
  reported (also enforced in `before_send`);
- a worker's failed tick (worker_supervision.py), grouped as one issue per worker and
  exception type;
- an exception from deferred chat work (a resume generation started from a message), which
  runs as a task of its own outside any request: `deferred_reply.DeferredReplies._run`
  reports whatever the work raised that was not an `ApiError` the chat boundary already
  answered (a cancellation at shutdown is not a failure and is not reported).

WHAT IS NOT SENT. The SDK is configured to collect as little as it can (no request
bodies, no local variables, no default PII, no events or breadcrumbs made from log lines,
no trace headers on outbound requests, tracing off unless SENTRY_TRACES_SAMPLE_RATE is
raised), and every event passes through sentry_scrub.py, which removes the rest by shape
and by regex. See that module for what regex scrubbing can and cannot do.

REPORTING IS BEST-EFFORT AND NEVER FAILS A REQUEST OR A WORKER. `report_exception`
swallows anything the SDK raises; the SDK sends from its own thread, so a slow or
unreachable Sentry costs the event loop nothing; and shutdown flushes in a thread, with a
timeout.

WHEN IT STARTS. `init_error_reporting` runs at the top of the app's lifespan, before the
app serves, so a bad setting stops the boot with the other configuration errors. That is
after the routers were built, so the SDK's FastAPI/Starlette integrations cannot patch
what they patch at import time (the per-route request handler, the exception handlers).
They still wrap every request in their ASGI middleware, and everything this module needs
is reported explicitly, by `report_exception`, at the places that handle the failure.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .env import optional_env, refuse
from .sentry_scrub import before_send, scrub_breadcrumb

logger = logging.getLogger(__name__)

DEFAULT_ENVIRONMENT = "production"
"""The label when SENTRY_ENVIRONMENT is unset: the SDK's own default, and the safe one --
a deployment that forgot to name itself is reported where an alert rule for production
will see it, never hidden under a label nobody watches."""

FLUSH_TIMEOUT_SECONDS = 2.0

_ENVIRONMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-]{0,63}")
_DSN_PATH = re.compile(r"/(?:[A-Za-z0-9_\-]{1,64}/)*[A-Za-z0-9_\-]{1,64}")
_RELEASE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-]{0,63}")

_sdk: Any | None = None
"""The imported `sentry_sdk` module once reporting is on; None means off."""
_failure_warned = False


@dataclass(frozen=True)
class SentryConfig:
    # Not in repr: a DSN is a (write-only) credential and must not reach a log line.
    dsn: str = field(repr=False)
    environment: str
    release: str | None
    traces_sample_rate: float


def _valid_dsn(dsn: str) -> bool:
    """https://<public key>@<host>[:port]/<project id>, nothing after it."""
    if len(dsn) > 512 or any(ch.isspace() or ord(ch) < 32 for ch in dsn):
        return False
    try:
        parts = urlsplit(dsn)
        host = parts.hostname
        _ = parts.port  # raises ValueError for a malformed port
    except ValueError:
        return False
    if parts.scheme != "https" or not parts.username or not host:
        return False
    if parts.query or parts.fragment:
        return False
    return _DSN_PATH.fullmatch(parts.path) is not None


def _traces_sample_rate() -> float:
    raw = optional_env("SENTRY_TRACES_SAMPLE_RATE")
    if raw is None or raw.strip() == "":
        return 0.0
    try:
        rate = float(raw.strip())
    except ValueError:
        rate = math.nan
    if not math.isfinite(rate) or not 0.0 <= rate <= 1.0:
        refuse("SENTRY_TRACES_SAMPLE_RATE must be a number from 0 to 1")
    return rate


def _release() -> str | None:
    """The deployed commit, when the platform says which it is (Railway sets it).
    Optional and never fatal: a value that is not a plain identifier is ignored."""
    raw = (optional_env("RAILWAY_GIT_COMMIT_SHA") or "").strip()
    return raw if _RELEASE.fullmatch(raw) else None


def load_sentry_config() -> SentryConfig | None:
    """None when SENTRY_DSN is unset or blank (reporting is off, and the other SENTRY_*
    settings are not read). A DSN or setting that is malformed stops the API from
    starting (`refuse`), naming the variable and never its value."""
    raw = optional_env("SENTRY_DSN")
    dsn = (raw or "").strip()
    if not dsn:
        return None
    if not _valid_dsn(dsn):
        refuse("SENTRY_DSN must be an https DSN of the form https://<key>@<host>/<project-id>")
    environment = (optional_env("SENTRY_ENVIRONMENT") or "").strip() or DEFAULT_ENVIRONMENT
    if not _ENVIRONMENT.fullmatch(environment):
        refuse("SENTRY_ENVIRONMENT must be 1-64 letters, digits, dots, dashes or underscores")
    return SentryConfig(
        dsn=dsn,
        environment=environment,
        release=_release(),
        traces_sample_rate=_traces_sample_rate(),
    )


def sentry_options(config: SentryConfig, integrations: list[Any]) -> dict[str, Any]:
    """The exact arguments `sentry_sdk.init` gets. Pure, so a test pins every one."""
    options: dict[str, Any] = {
        "dsn": config.dsn,
        "environment": config.environment,
        # 0 means off, so None, not 0: with a number set, the SDK still honours a
        # `sentry-trace` header a stranger sends and records that request in full.
        "traces_sample_rate": config.traces_sample_rate or None,
        "send_default_pii": False,
        "max_request_body_size": "never",
        "include_local_variables": False,
        # Release-health session counts are not used, and each request would feed them.
        "auto_session_tracking": False,
        "before_send": before_send,
        "before_send_transaction": before_send,
        "before_breadcrumb": scrub_breadcrumb,
        "integrations": integrations,
        # Only the integrations listed above: the auto-enabled ones include AI-SDK and
        # HTTP-client instrumentation this service has no use for.
        "auto_enabling_integrations": False,
        # No `sentry-trace` / `baggage` header (it names the project's public key and the
        # release) on any request this service makes to Supabase, an ATS or a provider.
        "trace_propagation_targets": [],
    }
    if config.release is not None:
        options["release"] = config.release
    return options


def _integrations() -> list[Any]:
    starlette = importlib.import_module("sentry_sdk.integrations.starlette")
    fastapi = importlib.import_module("sentry_sdk.integrations.fastapi")
    sdk_logging = importlib.import_module("sentry_sdk.integrations.logging")
    return [
        starlette.StarletteIntegration(),
        fastapi.FastApiIntegration(),
        # Neither events nor breadcrumbs from log lines: what is logged is already
        # in the JSON logs, and a log record reaching Sentry would skip the log
        # redactor and duplicate the exception the code reports itself.
        sdk_logging.LoggingIntegration(level=None, event_level=None),
    ]


def init_error_reporting() -> bool:
    """Starts the SDK when SENTRY_DSN is set; True when reporting is on.

    A configuration that cannot be used stops the API from starting (`refuse`): a DSN of
    the wrong shape, a setting out of range, or a DSN with no SDK installed to use it."""
    global _sdk
    config = load_sentry_config()
    if config is None:
        _sdk = None
        return False
    started = False
    try:
        sdk = importlib.import_module("sentry_sdk")
        sdk.init(**sentry_options(config, _integrations()))
        started = True
    except Exception as failure:
        # Only the type is logged, and the failure is not chained into the refusal below:
        # the SDK's own messages can quote the DSN.
        _sdk = None
        logger.error(
            "the error reporting SDK could not be started",
            extra={
                "ctx": {"error_type": f"{type(failure).__module__}.{type(failure).__qualname__}"}
            },
        )
    if not started:
        refuse(
            "SENTRY_DSN is set but error reporting could not start "
            "(is the sentry-sdk package installed, and is the DSN valid?)"
        )
    _sdk = sdk
    logger.info(
        "error reporting is on",
        extra={
            "ctx": {
                "environment": config.environment,
                "traces_sample_rate": config.traces_sample_rate,
            }
        },
    )
    return True


def reporting_enabled() -> bool:
    return _sdk is not None


def report_exception(
    error: BaseException,
    *,
    tags: Mapping[str, str] | None = None,
    fingerprint: Sequence[str] | None = None,
) -> None:
    """Reports `error` when reporting is on; does nothing otherwise, and never raises.

    `tags` are short, non-sensitive labels (a worker's name, a route template, a request
    id). `fingerprint` groups events: events with the same fingerprint are one issue."""
    sdk = _sdk
    if sdk is None:
        return
    try:
        scope_arguments: dict[str, Any] = {}
        if tags:
            scope_arguments["tags"] = {str(k): str(v) for k, v in tags.items()}
        if fingerprint:
            scope_arguments["fingerprint"] = [str(part) for part in fingerprint]
        sdk.capture_exception(error, **scope_arguments)
    except Exception as failure:
        _log_failure_once(failure)


def _log_failure_once(failure: BaseException) -> None:
    global _failure_warned
    if _failure_warned:
        return
    _failure_warned = True
    logger.warning(
        "an error report could not be handed to the SDK; further failures are not logged",
        extra={"ctx": {"error_type": f"{type(failure).__module__}.{type(failure).__qualname__}"}},
    )


async def flush_error_reporting(timeout: float = FLUSH_TIMEOUT_SECONDS) -> None:
    """Gives the SDK up to `timeout` seconds to send what it holds. For shutdown. The SDK's
    flush blocks its caller, so it runs in a thread and the event loop stays free."""
    sdk = _sdk
    if sdk is None:
        return
    try:
        await asyncio.wait_for(asyncio.to_thread(sdk.flush, timeout), timeout=timeout + 1.0)
    except Exception as failure:
        _log_failure_once(failure)
