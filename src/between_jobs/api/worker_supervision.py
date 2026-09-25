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

A worker's own tick must contain failures of individual items (one search,
one draft, one company) so that a supervised retry only ever repeats work
that hadn't happened yet -- the matcher's and the reply checker's ticks spend
users' LLM credit.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

BACKOFF_BASE_SECONDS = 5.0
BACKOFF_MAX_SECONDS = 300.0
# A fast worker (the outbox polls every 5s) would otherwise read as stale
# after one slow tick.
MIN_STALE_AFTER_SECONDS = 60.0
FAILING_AFTER_MAX_SECONDS = 1800.0

Sleep = Callable[[float], Awaitable[Any]]


def _now() -> datetime:
    return datetime.now(UTC)


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
        yet) or running. Dead, stale and failing fail /health."""
        now = now or _now()
        if not self.enabled:
            return "disabled"
        if self.task is not None and self.task.done():
            return "dead"
        reference = self.last_success_at or self.started_at
        if reference is None:
            return "starting"
        if now - reference > self.stale_after:
            return "stale"
        if self.failing_since is not None and now - self.failing_since > self.failing_after:
            return "failing"
        if self.consecutive_failures:
            return "retrying"
        return "running" if self.last_success_at is not None else "starting"

    def healthy(self, now: datetime | None = None) -> bool:
        return self.status(now) not in ("dead", "stale", "failing")

    def to_report(self, now: datetime | None = None) -> dict[str, Any]:
        def iso(value: datetime | None) -> str | None:
            return value.isoformat(timespec="seconds") if value else None

        return {
            "status": self.status(now),
            "last_success_at": iso(self.last_success_at),
            "last_error_at": iso(self.last_error_at),
            "last_error_type": self.last_error_type,
            "consecutive_failures": self.consecutive_failures,
        }


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
        try:
            await tick()
        except asyncio.CancelledError as e:
            current = asyncio.current_task()
            if current is None or current.cancelling():
                raise
            _log_failure(state, e)
        except Exception as e:
            _log_failure(state, e)
        else:
            state.last_success_at = _now()
            state.consecutive_failures = 0
            state.failing_since = None
            await sleep(state.interval_seconds)
            continue
        await sleep(state.next_retry_delay())


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
