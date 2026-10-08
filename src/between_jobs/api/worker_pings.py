"""Dead-man's-switch pings for the background workers (Healthchecks.io or compatible).

Each supervised worker can have a check. After every successful tick the supervisor calls
`WorkerPinger.succeeded()`, which GETs the check's ping URL; the check alerts when the pings
stop for longer than its grace time. That is the signal `/health` cannot give: Railway only
looks at `/health` while a deploy is going out, so a worker that dies or hangs a day later
needs something outside the process to notice the silence.

OFF UNLESS CONFIGURED. A worker pings only when HEALTHCHECKS_URL_<NAME> is set, where
<NAME> is the worker's DISABLE_<NAME> flag without the prefix (DISABLE_OUTBOX_WORKER ->
HEALTHCHECKS_URL_OUTBOX_WORKER). The value is the check's ping URL, which is a credential
(anyone who has it can mark the check up), so it is never logged and never put in an error
report.

A NAME THAT MATCHES NO WORKER IS SAID OUT LOUD. The setting is named after the DISABLE_* flag,
not after the name /health shows (the outbox's is HEALTHCHECKS_URL_OUTBOX_WORKER, though /health
calls the worker "outbox"), so a plausible guess is easy to get wrong, and a check that never
receives a ping stays in its "new" state, which does not alert: the worker would look monitored
and never be. `WorkerPings.warn_unrecognised_settings` therefore logs a warning, at boot, for
every environment variable that looks like a healthcheck URL but is not one of the workers'
settings (its name only -- the value is a credential and is never read).

A WORKER THAT IS OFF MUST NOT LOOK ALIVE. A worker switched off with its DISABLE_* flag
has no pinger, and one that is standing by without its lease never reaches a successful
tick, so neither pings: its check would go down, which is the truth about a worker that is
not running -- create no check for a worker you disable. (A URL left set for a disabled
worker is logged as a warning at boot and otherwise ignored.)

PINGING NEVER AFFECTS THE WORKER. `succeeded()` and `failed()` only schedule a task and
return, so a slow Healthchecks costs the tick loop nothing; at most one ping per worker is in
flight; a ping is sent at most once per 30 seconds per worker, so the outbox's five-second
loop cannot flood the service; there are no retries; a transport error is logged at WARNING
(the exception's type, never the URL) at most once a minute and swallowed.

Pure of the app: the HTTP client and the clock are injected, so the tests run it on a fake
transport and a fake clock."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable, Mapping
from urllib.parse import urlsplit

import httpx

from .env import optional_env, refuse

logger = logging.getLogger(__name__)

ENV_PREFIX = "HEALTHCHECKS_URL_"
LOOKALIKE_PREFIX = "HEALTHCHECK"
"""Any variable whose name starts with this (in any case) and is not a worker's setting is
reported as unrecognised: wide enough to catch a missing "S" or a missing underscore."""
DISABLE_PREFIX = "DISABLE_"
PING_TIMEOUT_SECONDS = 5.0
MIN_PING_INTERVAL_SECONDS = 30.0
WARN_INTERVAL_SECONDS = 60.0
MAX_URL_CHARS = 2048


def healthcheck_env_name(disable_env: str) -> str:
    """The setting that holds a worker's ping URL, from the name of its disable flag."""
    return ENV_PREFIX + disable_env.removeprefix(DISABLE_PREFIX)


def parse_ping_url(env_name: str, raw: str | None) -> str | None:
    """The ping URL, or None when the setting is unset or blank. A value that is not an
    https URL with a host and a path -- or that carries credentials, a query string or a
    fragment, none of which a ping URL has -- stops the API from starting (`refuse`),
    naming the setting and never its value."""
    value = (raw or "").strip()
    if not value:
        return None
    valid = len(value) <= MAX_URL_CHARS and not any(ch.isspace() or ord(ch) < 32 for ch in value)
    if valid:
        try:
            parts = urlsplit(value)
            _ = parts.port  # raises ValueError for a malformed port
        except ValueError:
            valid = False
        else:
            valid = (
                parts.scheme == "https"
                and bool(parts.hostname)
                and parts.username is None
                and parts.password is None
                and "?" not in value  # urlsplit reports an empty query as none at all
                and "#" not in value
                and parts.path not in ("", "/")
            )
    if not valid:
        refuse(
            f"{env_name} must be an https URL with a path and no credentials, query or fragment "
            "(a Healthchecks ping URL)"
        )
    return value


def load_ping_url(env_name: str) -> str | None:
    return parse_ping_url(env_name, optional_env(env_name))


class WorkerPinger:
    """Pings one worker's check. Not thread-safe: it lives on the event loop."""

    def __init__(
        self,
        worker: str,
        url: str,
        client: httpx.AsyncClient,
        *,
        clock: Callable[[], float] = time.monotonic,
        min_interval_seconds: float = MIN_PING_INTERVAL_SECONDS,
        timeout_seconds: float = PING_TIMEOUT_SECONDS,
        warn_interval_seconds: float = WARN_INTERVAL_SECONDS,
    ) -> None:
        self.worker = worker
        self._success_url = url
        # Healthchecks' "this run failed" signal is the same URL with /fail appended.
        self._fail_url = url.rstrip("/") + "/fail"
        self._client = client
        self._clock = clock
        self._min_interval = min_interval_seconds
        self._timeout = timeout_seconds
        self._warn_interval = warn_interval_seconds
        self._last_sent: float | None = None
        self._last_warned: float | None = None
        self._in_flight: asyncio.Task[None] | None = None
        self._closed = False

    def succeeded(self) -> None:
        """A tick finished: tell the check the worker is alive."""
        self._send(self._success_url)

    def failed(self) -> None:
        """The worker is failing: tell the check so now, rather than let its grace time run."""
        self._send(self._fail_url)

    def _send(self, url: str) -> None:
        if self._closed or self._in_flight is not None:
            return
        now = self._clock()
        if self._last_sent is not None and now - self._last_sent < self._min_interval:
            return
        try:
            task = asyncio.get_running_loop().create_task(self._ping(url))
        except RuntimeError:
            return  # not on an event loop: nothing to schedule on
        self._last_sent = now
        self._in_flight = task
        task.add_done_callback(self._finished)

    def _finished(self, _task: asyncio.Task[None]) -> None:
        self._in_flight = None

    async def _ping(self, url: str) -> None:
        try:
            async with asyncio.timeout(self._timeout):
                response = await self._client.get(url)
        except Exception as e:
            if self._may_warn():
                # The worker and what went wrong, never the URL: it is the check's credential.
                logger.warning(
                    "worker healthcheck ping failed",
                    extra={
                        "ctx": {
                            "worker": self.worker,
                            "problem": f"{type(e).__module__}.{type(e).__qualname__}",
                        }
                    },
                )
            return
        if not response.is_success and self._may_warn():
            logger.warning(
                "worker healthcheck ping failed",
                extra={"ctx": {"worker": self.worker, "problem": f"status {response.status_code}"}},
            )

    def _may_warn(self) -> bool:
        """True at most once per warn interval: a check that is down must not fill the log."""
        now = self._clock()
        if self._last_warned is not None and now - self._last_warned < self._warn_interval:
            return False
        self._last_warned = now
        return True

    async def aclose(self) -> None:
        """Stops pinging and abandons a ping still in flight."""
        self._closed = True
        task = self._in_flight
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class WorkerPings:
    """The pingers of one API process and the HTTP client they share (made only when at least
    one worker has a URL, so a deployment that sets none behaves exactly as before)."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self.pingers: dict[str, WorkerPinger] = {}
        self._known_env_names: set[str] = set()

    def for_worker(self, name: str, *, disable_env: str, enabled: bool) -> WorkerPinger | None:
        """The worker's pinger, or None when it has no URL or is switched off. The URL is
        validated either way, so a typo stops the boot whether or not the worker runs."""
        env_name = healthcheck_env_name(disable_env)
        # Known before the URL is read, and whether or not the worker runs: a disabled
        # worker's setting is a real one (it gets its own warning), not a stray.
        self._known_env_names.add(env_name)
        url = load_ping_url(env_name)
        if url is None:
            return None
        if not enabled:
            logger.warning(
                "a healthcheck URL is set for a worker that is switched off, so it will not be "
                "pinged and its check will report the worker down; delete that check",
                extra={"ctx": {"worker": name, "variable": env_name}},
            )
            return None
        if self._client is None:
            self._client = self._make_client()
        pinger = WorkerPinger(name, url, self._client)
        self.pingers[name] = pinger
        return pinger

    def warn_unrecognised_settings(self, environ: Mapping[str, str] | None = None) -> None:
        """Logs one warning for each variable that looks like a healthcheck URL setting but is
        none of the workers' (call it once every worker has been set up). Names only: the
        value of a variable is never read. Ignored, not refused: another tool may share the
        prefix, and a warning must never stop the boot or fail the caller."""
        try:
            names = list(os.environ if environ is None else environ)
            expected = sorted(self._known_env_names)
            for name in names:
                if name.upper().startswith(LOOKALIKE_PREFIX) and name not in self._known_env_names:
                    logger.warning(
                        "a setting looks like a worker's healthcheck URL but is none of them, so "
                        "it is ignored and nothing is monitored by it; `expected` lists the "
                        "names that work",
                        extra={"ctx": {"variable": name, "expected": expected}},
                    )
        except Exception:
            logger.warning("the healthcheck settings could not be checked for typos")

    def _make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=PING_TIMEOUT_SECONDS, follow_redirects=False)

    async def aclose(self) -> None:
        for pinger in self.pingers.values():
            await pinger.aclose()
        if self._client is not None:
            await self._client.aclose()
            self._client = None
