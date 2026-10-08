"""Keeps the background workers alive and says how they're doing.

Every worker is a loop of ticks. Before this module each loop was a bare
`while True`, so the first unexpected exception ended the task for good while
the process kept serving requests (and `/health` kept saying "ok") -- the job
registry poller died that way. `run_supervised` runs one tick at a time: a
tick that raises is logged and retried after a capped exponential backoff,
and `asyncio.CancelledError` still propagates so shutdown works as before.

Each worker's progress is kept in a `WorkerState`, collected in a
`WorkerRegistry` on `app.state`, which `/health` reads. A worker fails the
check when its task has ended (dead), when no tick has finished for 3x its
interval (stale -- this also catches a tick that hangs), or when every tick
has failed for longer than `FAILING_AFTER_MAX_SECONDS` (failing), so a worker
with a 6-hour interval that keeps failing is caught in 30 minutes, not 18
hours.

A worker may also hold a LEASE (worker_lease.py, launch plan P2.19): then a tick
starts only while this process holds it. A process that does not is `standby` --
alive and healthy, simply not the one doing the work -- and one that cannot tell is
`lease_unknown`, which fails the check, so a deploy whose lease path is broken is
caught by the platform's healthcheck instead of idling behind a 200.

Every failed tick is also reported to the error tracker when one is configured
(error_reporting.py), thinned the way the full tracebacks in the log are, and a worker
with a healthcheck (worker_pings.py) pings it after each successful tick. Both are
best-effort: neither can raise into the loop or slow it down.

A worker's own tick must contain failures of individual items (one search,
one draft, one company) so that a supervised retry only ever repeats work
that hadn't happened yet -- the matcher's and the reply checker's ticks spend
users' LLM credit.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .error_reporting import report_exception

logger = logging.getLogger(__name__)

BACKOFF_BASE_SECONDS = 5.0
BACKOFF_MAX_SECONDS = 300.0
# A fast worker (the outbox polls every 5s) would otherwise read as stale
# after one slow tick.
MIN_STALE_AFTER_SECONDS = 60.0
FAILING_AFTER_MAX_SECONDS = 1800.0

REPORT_MIN_SPACING_SECONDS = 600.0
"""The soonest the same worker's same kind of failure is reported again. The thinning by
failure count below resets whenever a tick succeeds, so a worker that fails every other tick
would otherwise be reported every few seconds."""

FAIL_PING_AFTER_FAILURES = 3
"""A worker tells its healthcheck "failing" only from its third failed tick in a row. One
failure the retry cures in seconds is not worth an alert; the check's grace time still
catches a worker that stops succeeding."""

LEASE_POLL_SECONDS = 1.0
"""How often a leased worker that may not tick re-reads its lease. The read is
local (the keeper does the I/O), so this is as cheap as a loop gets."""

Sleep = Callable[[float], Awaitable[Any]]


class Lease(Protocol):
    """What the supervisor needs from a lease: local reads only, so this module
    stays free of any database client. Implemented by worker_lease.WorkerLease."""

    @property
    def crashed(self) -> bool: ...

    def status(self) -> str: ...

    def may_tick(self) -> bool: ...

    def report(self) -> dict[str, Any]: ...

    async def stop(self) -> None: ...


class Heartbeat(Protocol):
    """What the supervisor needs from a worker's healthcheck (worker_pings.WorkerPinger):
    two calls that return at once and never raise."""

    def succeeded(self) -> None: ...

    def failed(self) -> None: ...


def _now() -> datetime:
    return datetime.now(UTC)


def _monotonic() -> float:
    return time.monotonic()


@dataclass
class WorkerState:
    name: str
    interval_seconds: float
    enabled: bool = True
    stale_after_seconds: float | None = None
    # The first retry's delay; defaults to min(5s, interval). A worker whose
    # retry repeats expensive outside calls (the registry poller) starts higher.
    backoff_base_seconds: float | None = None
    task: asyncio.Task[None] | None = None
    started_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_type: str | None = None
    failing_since: datetime | None = None
    consecutive_failures: int = 0
    # Set only for a worker that holds a lease. Last, with defaults, so positional
    # construction elsewhere is unchanged and a lease-less worker is untouched.
    lease: Lease | None = None
    active_since: datetime | None = None
    waiting_for_lease: bool = False
    # time.monotonic() when the tick now running began; None between ticks.
    current_tick_started: float | None = None
    # The worker's healthcheck, set only when its ping URL is configured and it is enabled.
    heartbeat: Heartbeat | None = None
    # When each kind of failure was last reported to the error tracker (monotonic seconds).
    reported_at: dict[str, float] = field(default_factory=dict)

    @property
    def stale_after(self) -> timedelta:
        seconds = self.stale_after_seconds or max(
            3 * self.interval_seconds, MIN_STALE_AFTER_SECONDS
        )
        return timedelta(seconds=seconds)

    @property
    def failing_after(self) -> timedelta:
        seconds = max(
            MIN_STALE_AFTER_SECONDS,
            min(self.stale_after.total_seconds(), FAILING_AFTER_MAX_SECONDS),
        )
        return timedelta(seconds=seconds)

    def tick_running_too_long(self) -> bool:
        """Whether the tick now running has outlived this worker's own stale
        window. The keeper stops renewing the lease then, so a holder that is
        stuck but still heartbeating cannot block every standby forever."""
        started = self.current_tick_started
        return started is not None and time.monotonic() - started > self.stale_after.total_seconds()

    def next_retry_delay(self) -> float:
        """Capped exponential backoff, never more than half the stale window,
        so a recovered dependency is noticed before /health would 503."""
        base = self.backoff_base_seconds or min(BACKOFF_BASE_SECONDS, self.interval_seconds)
        cap = min(BACKOFF_MAX_SECONDS, self.stale_after.total_seconds() / 2)
        return min(cap, backoff_seconds(self.consecutive_failures, base=base))

    def status(self, now: datetime | None = None) -> str:
        """disabled, dead (its task ended), stale (no finished tick for too
        long), failing (every tick has failed for too long), retrying (the
        last tick failed, still within the window), starting (no tick finished
        yet) or running. Dead, stale and failing fail /health.

        A leased worker adds two: `standby` (another process holds the lease; this
        one is alive and idle on purpose, so it is healthy and is checked BEFORE
        staleness, which would otherwise condemn it for not ticking) and
        `lease_unknown` (no definite answer from the lease, so no tick can start;
        unhealthy, and immediately, because a delayed status could not stop a
        deploy whose workers can never tick)."""
        now = now or _now()
        if not self.enabled:
            return "disabled"
        if self.task is not None and self.task.done():
            return "dead"
        if self.lease is not None:
            if self.lease.crashed:
                return "dead"
            lease_state = self.lease.status()
            if lease_state == "standby":
                return "standby"
            if lease_state == "unknown":
                return "lease_unknown"
            if lease_state == "held" and (self.waiting_for_lease or self.active_since is None):
                # The keeper has just got (or got back) the lease, but the loop has
                # not yet noticed -- it looks every LEASE_POLL_SECONDS -- so the
                # moment staleness is measured from (active_since) is not stamped
                # and the failure streak from the last hold is not yet cleared.
                # Reading the old clocks here would report a healthy takeover as
                # stale or failing for that second.
                return "starting"
        # A worker that has just taken over a lease has not ticked for as long as it
        # stood by: staleness runs from whenever it last started, succeeded or
        # acquired, whichever is latest.
        moments = [m for m in (self.last_success_at, self.active_since, self.started_at) if m]
        reference = max(moments) if moments else None
        if reference is None:
            return "starting"
        if now - reference > self.stale_after:
            return "stale"
        if self.failing_since is not None and now - self.failing_since > self.failing_after:
            return "failing"
        if self.consecutive_failures:
            return "retrying"
        if self.last_success_at is None or (
            self.active_since is not None and self.last_success_at < self.active_since
        ):
            return "starting"
        return "running"

    def healthy(self, now: datetime | None = None) -> bool:
        return self.status(now) not in ("dead", "stale", "failing", "lease_unknown")

    def to_report(self, now: datetime | None = None) -> dict[str, Any]:
        def iso(value: datetime | None) -> str | None:
            return value.isoformat(timespec="seconds") if value else None

        report: dict[str, Any] = {
            "status": self.status(now),
            "last_success_at": iso(self.last_success_at),
            "last_error_at": iso(self.last_error_at),
            "last_error_type": self.last_error_type,
            "consecutive_failures": self.consecutive_failures,
        }
        if self.lease is not None:
            # The state and the failure kind only -- never the holder's id, which
            # this unauthenticated body must not carry.
            report["lease"] = self.lease.report()
        return report


@dataclass
class WorkerRegistry:
    workers: dict[str, WorkerState] = field(default_factory=dict)

    def register(
        self,
        name: str,
        *,
        interval_seconds: float,
        enabled: bool,
        stale_after_seconds: float | None = None,
        backoff_base_seconds: float | None = None,
    ) -> WorkerState:
        state = WorkerState(
            name=name,
            interval_seconds=interval_seconds,
            enabled=enabled,
            stale_after_seconds=stale_after_seconds,
            backoff_base_seconds=backoff_base_seconds,
        )
        self.workers[name] = state
        return state

    def healthy(self, now: datetime | None = None) -> bool:
        return all(state.healthy(now) for state in self.workers.values())

    def report(self, now: datetime | None = None) -> dict[str, dict[str, Any]]:
        return {name: state.to_report(now) for name, state in self.workers.items()}

    async def stop_all(self) -> None:
        for state in self.workers.values():
            if state.task is not None and not state.task.done():
                state.task.cancel()
        for state in self.workers.values():
            if state.task is not None:
                try:
                    await state.task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception(
                        "worker ended with an error at shutdown",
                        extra={"ctx": {"worker": state.name}},
                    )
        # Only now, with every worker task fully unwound, stop the keepers: a tick
        # that was mid-way when it was cancelled is still finishing its cleanup, and
        # the lease must keep excluding other processes until it has.
        for state in self.workers.values():
            if state.lease is not None:
                await state.lease.stop()


def backoff_seconds(consecutive_failures: int, *, base: float = BACKOFF_BASE_SECONDS) -> float:
    """base, 2x base, 4x base ... capped at 5 minutes."""
    exponent = min(max(consecutive_failures - 1, 0), 16)
    return float(min(BACKOFF_MAX_SECONDS, base * 2**exponent))


async def run_supervised(
    tick: Callable[[], Awaitable[Any]],
    *,
    state: WorkerState,
    sleep: Sleep = asyncio.sleep,
) -> None:
    """Run `tick` forever: after a success sleep the worker's interval; after
    an exception log it and sleep the backoff instead, so a worker with a long
    interval retries a transient failure within minutes. Never returns.

    Cancelling this task (shutdown) propagates. A CancelledError that comes out
    of a tick without this task being cancelled -- an inner future somebody
    else cancelled -- counts as a failed tick, not the end of the worker."""
    state.started_at = state.started_at or _now()
    while True:
        lease = state.lease
        if lease is not None:
            # No `await` between this check and starting the tick below: the
            # answer cannot go stale in between.
            if not lease.may_tick():
                state.waiting_for_lease = True
                await sleep(LEASE_POLL_SECONDS)
                continue
            if state.waiting_for_lease or state.active_since is None:
                # (Re)acquired: any failure streak belongs to the previous hold,
                # and staleness now counts from this moment.
                state.waiting_for_lease = False
                state.active_since = _now()
                state.consecutive_failures = 0
                state.failing_since = None
        try:
            state.current_tick_started = time.monotonic()
            try:
                await tick()
            finally:
                state.current_tick_started = None
        except asyncio.CancelledError as e:
            current = asyncio.current_task()
            if current is None or current.cancelling():
                raise
            _log_failure(state, e)
        except Exception as e:
            _log_failure(state, e)
        else:
            _warn_if_lease_lapsed(state)
            state.last_success_at = _now()
            state.consecutive_failures = 0
            state.failing_since = None
            _ping(state, ok=True)
            await sleep(state.interval_seconds)
            continue
        await sleep(state.next_retry_delay())


def _warn_if_lease_lapsed(state: WorkerState) -> None:
    if state.lease is not None and state.lease.status() != "held":
        logger.warning(
            "worker lease lapsed while a tick was running",
            extra={"ctx": {"worker": state.name, "lease": state.lease.status()}},
        )


def _ping(state: WorkerState, *, ok: bool) -> None:
    """Tells the worker's healthcheck about a tick, when it has one. A worker that does not
    hold its lease pings nothing: it is standing by, and the check belongs to whichever
    process is doing the work."""
    heartbeat = state.heartbeat
    if heartbeat is None:
        return
    try:
        if state.lease is not None and state.lease.status() != "held":
            return
        if ok:
            heartbeat.succeeded()
        else:
            heartbeat.failed()
    except Exception:
        logger.warning("worker heartbeat failed", extra={"ctx": {"worker": state.name}})


def _report_failure(state: WorkerState, error: BaseException) -> None:
    """Sends a failed tick to the error tracker: on the 1st, 2nd, 4th, 8th ... failure in a
    row (the cadence of the full tracebacks in the log), and never the same kind of failure
    of the same worker twice within REPORT_MIN_SPACING_SECONDS. One fingerprint per worker
    and exception type, so a crash loop is a single issue however long it runs."""
    n = state.consecutive_failures
    if n & (n - 1) != 0:
        return
    kind = state.last_error_type or type(error).__qualname__
    now = _monotonic()
    last = state.reported_at.get(kind)
    if last is not None and now - last < REPORT_MIN_SPACING_SECONDS:
        return
    state.reported_at[kind] = now
    try:
        report_exception(
            error,
            tags={"worker": state.name, "consecutive_failures": str(n)},
            fingerprint=["worker-tick-failure", state.name, kind],
        )
    except Exception:
        # report_exception does not raise; if it ever does, the worker still retries.
        logger.warning("worker failure report failed", extra={"ctx": {"worker": state.name}})


def _log_failure(state: WorkerState, error: BaseException) -> None:
    now = _now()
    state.consecutive_failures += 1
    state.last_error_at = now
    state.failing_since = state.failing_since or now
    state.last_error_type = f"{type(error).__module__}.{type(error).__qualname__}"
    ctx = {
        "worker": state.name,
        "consecutive_failures": state.consecutive_failures,
        "retry_in_seconds": state.next_retry_delay(),
    }
    # The full traceback on the 1st, 2nd, 4th, 8th ... failure in a row; a
    # one-line warning in between, so a worker stuck failing for hours doesn't
    # bury the log (or a future error tracker's quota).
    n = state.consecutive_failures
    if n & (n - 1) == 0:
        logger.error(
            "worker tick failed; retrying after backoff",
            exc_info=(type(error), error, error.__traceback__),
            extra={"ctx": ctx},
        )
    else:
        logger.warning(
            "worker tick failed again; retrying after backoff",
            extra={"ctx": {**ctx, "error_type": state.last_error_type}},
        )
    _report_failure(state, error)
    if n >= FAIL_PING_AFTER_FAILURES:
        _ping(state, ok=False)
