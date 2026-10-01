"""A worker that holds a lease, under the supervisor and /health (launch plan P2.19).

Two layers. The supervisor-level tests use a stub lease whose answers the test
sets, to pin what `run_supervised`, `WorkerState.status()` and `/health` do with
each answer. The "done when" tests run two real supervised loops with real keepers
over one shared lease on a virtual clock and check what the plan asks for: only
one ticks at a time, and the other takes over within one TTL after the first stops.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from lease_fakes import FakeLeaseStore
from virtual_time import VirtualTime

from between_jobs.api.app import app
from between_jobs.api.worker_lease import (
    STANDBY_POLL_SECONDS,
    TTL_SECONDS,
    WorkerLease,
)
from between_jobs.api.worker_supervision import (
    LEASE_POLL_SECONDS,
    WorkerRegistry,
    WorkerState,
    run_supervised,
)

WORKER = "job_registry_poller"


def _now() -> datetime:
    return datetime.now(UTC)


class StubLease:
    """What a test needs of a lease: answers it sets, calls it records."""

    def __init__(self, state: str = "held", may_tick: bool = True) -> None:
        self.state = state
        self.may = may_tick
        self.crashed = False
        self.order: list[str] | None = None

    def status(self) -> str:
        return self.state

    def may_tick(self) -> bool:
        return self.may

    def report(self) -> dict[str, Any]:
        return {"state": self.state, "claim_failures": 0, "last_claim_error": None}

    async def stop(self) -> None:
        if self.order is not None:
            self.order.append("lease stopped")


class StopAfter:
    """A fake `sleep` that records delays and cancels the loop on the n-th call."""

    def __init__(self, stop_after: int) -> None:
        self.delays: list[float] = []
        self._stop_after = stop_after

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        if len(self.delays) >= self._stop_after:
            raise asyncio.CancelledError


# -- the gate ---------------------------------------------------------------------


async def test_a_worker_that_may_not_tick_waits_a_second_and_asks_again() -> None:
    ticks: list[int] = []

    async def tick() -> None:
        ticks.append(1)

    state = WorkerState(name=WORKER, interval_seconds=900, lease=StubLease("standby", False))
    sleep = StopAfter(stop_after=3)

    with pytest.raises(asyncio.CancelledError):
        await run_supervised(tick, state=state, sleep=sleep)

    assert ticks == []
    assert sleep.delays == [LEASE_POLL_SECONDS] * 3
    assert state.waiting_for_lease


async def test_it_ticks_the_moment_the_lease_allows_it_and_not_before() -> None:
    lease = StubLease("standby", False)
    ticks: list[int] = []

    async def tick() -> None:
        ticks.append(1)

    class Sleep:
        n = 0

        async def __call__(self, delay: float) -> None:
            self.n += 1
            if self.n == 2:  # after two waits, the lease arrives
                lease.state, lease.may = "held", True
            if self.n >= 3:  # wait, wait (lease arrives), tick, sleep out the interval
                raise asyncio.CancelledError

    state = WorkerState(name=WORKER, interval_seconds=900, lease=lease)
    with pytest.raises(asyncio.CancelledError):
        await run_supervised(tick, state=state, sleep=Sleep())

    assert len(ticks) == 1


async def test_a_worker_without_a_lease_is_exactly_as_before() -> None:
    ticks: list[int] = []

    async def tick() -> None:
        ticks.append(1)

    state = WorkerState(name="outbox", interval_seconds=5)
    sleep = StopAfter(stop_after=2)
    with pytest.raises(asyncio.CancelledError):
        await run_supervised(tick, state=state, sleep=sleep)

    assert ticks == [1, 1] or ticks == [1]
    assert sleep.delays[0] == 5  # the interval, never the 1-second lease poll
    assert state.active_since is None and state.lease is None


async def test_acquiring_the_lease_forgives_a_failure_streak_from_the_last_hold() -> None:
    lease = StubLease("held", True)
    state = WorkerState(name=WORKER, interval_seconds=900, lease=lease)
    state.waiting_for_lease = True  # it stood by after an earlier hold
    state.consecutive_failures = 7
    state.failing_since = _now() - timedelta(hours=2)

    async def tick() -> None:
        return None

    with pytest.raises(asyncio.CancelledError):
        await run_supervised(tick, state=state, sleep=StopAfter(1))

    assert state.consecutive_failures == 0 and state.failing_since is None
    assert state.active_since is not None
    assert not state.waiting_for_lease


async def test_a_tick_is_timed_and_the_clock_is_cleared_even_when_it_raises() -> None:
    state = WorkerState(name=WORKER, interval_seconds=900)
    seen: list[float | None] = []

    async def tick() -> None:
        seen.append(state.current_tick_started)
        raise RuntimeError("blip")

    with pytest.raises(asyncio.CancelledError):
        await run_supervised(tick, state=state, sleep=StopAfter(1))

    assert seen[0] is not None
    assert state.current_tick_started is None


def test_a_tick_that_has_outrun_the_stale_window_is_reported_as_running_too_long() -> None:
    import time

    state = WorkerState(name=WORKER, interval_seconds=900)  # stale after 2700 s
    assert not state.tick_running_too_long()
    state.current_tick_started = time.monotonic() - 2000
    assert not state.tick_running_too_long()
    state.current_tick_started = time.monotonic() - 2701
    assert state.tick_running_too_long()


async def test_it_says_so_when_the_lease_lapsed_under_a_running_tick(
    caplog: pytest.LogCaptureFixture,
) -> None:
    lease = StubLease("held", True)

    async def tick() -> None:
        lease.state = "unknown"  # the keeper lost it while this tick ran

    state = WorkerState(name=WORKER, interval_seconds=900, lease=lease)
    caplog.set_level(logging.WARNING, logger="between_jobs.api.worker_supervision")
    with pytest.raises(asyncio.CancelledError):
        await run_supervised(tick, state=state, sleep=StopAfter(1))

    assert [r.getMessage() for r in caplog.records] == [
        "worker lease lapsed while a tick was running"
    ]


# -- status -----------------------------------------------------------------------


def _leased(state_name: str, **fields: Any) -> WorkerState:
    state = WorkerState(name=WORKER, interval_seconds=900, lease=StubLease(state_name))
    for key, value in fields.items():
        setattr(state, key, value)
    return state


def test_standby_is_healthy_even_though_it_never_ticks() -> None:
    state = _leased("standby", started_at=_now() - timedelta(hours=5))

    assert state.status() == "standby"  # not "stale": it is idle on purpose
    assert state.healthy()


def test_lease_unknown_is_unhealthy_at_once() -> None:
    state = _leased("unknown", started_at=_now())

    assert state.status() == "lease_unknown"
    assert not state.healthy()  # not after failing_after: Railway's check is 300 s


def test_a_dead_worker_is_dead_whatever_its_lease_says() -> None:
    async def finished() -> asyncio.Task[None]:
        async def done() -> None:
            return None

        task = asyncio.create_task(done())
        await task
        return task

    state = _leased("standby")
    state.task = asyncio.run(finished())

    assert state.status() == "dead"


def test_a_dead_keeper_makes_the_worker_dead() -> None:
    lease = StubLease("held")
    lease.crashed = True
    state = WorkerState(name=WORKER, interval_seconds=900, lease=lease)
    state.started_at = state.last_success_at = _now()

    assert state.status() == "dead"
    assert not state.healthy()


def test_a_disabled_worker_is_disabled_whatever_its_lease_says() -> None:
    state = _leased("unknown")
    state.enabled = False

    assert state.status() == "disabled"


def test_a_worker_that_has_just_acquired_the_lease_is_starting_not_stale() -> None:
    """It stood by for hours, so last_success_at and started_at are old; staleness
    must run from the acquisition."""
    state = _leased(
        "held",
        started_at=_now() - timedelta(hours=5),
        last_success_at=_now() - timedelta(hours=4),
        active_since=_now() - timedelta(seconds=5),
    )

    assert state.status() == "starting"
    assert state.healthy()


def test_it_goes_stale_from_the_acquisition_if_it_then_never_ticks() -> None:
    state = _leased(
        "held",
        started_at=_now() - timedelta(hours=5),
        last_success_at=_now() - timedelta(hours=4),
        active_since=_now() - timedelta(hours=1),  # stale after 45 min for a 900 s worker
    )

    assert state.status() == "stale"


def test_after_its_first_tick_a_new_holder_is_running() -> None:
    state = _leased(
        "held",
        started_at=_now() - timedelta(hours=5),
        active_since=_now() - timedelta(minutes=10),
        last_success_at=_now() - timedelta(minutes=1),
    )

    assert state.status() == "running"


# -- /health ----------------------------------------------------------------------


def _health_with(registry: WorkerRegistry) -> Any:
    with TestClient(app) as client:
        client.app.state.workers = registry  # type: ignore[attr-defined]
        return client.get("/health")


def test_health_is_200_when_a_leased_worker_is_on_standby() -> None:
    registry = WorkerRegistry()
    state = registry.register(WORKER, interval_seconds=900, enabled=True)
    state.started_at = _now() - timedelta(hours=3)
    state.lease = StubLease("standby", False)

    response = _health_with(registry)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    worker = response.json()["workers"][WORKER]
    assert worker["status"] == "standby"
    assert worker["lease"] == {"state": "standby", "claim_failures": 0, "last_claim_error": None}


def test_health_is_503_when_the_lease_cannot_be_determined_and_never_leaks_the_holder() -> None:
    async def never() -> bool:
        raise RuntimeError("not reached")

    registry = WorkerRegistry()
    state = registry.register(WORKER, interval_seconds=900, enabled=True)
    state.started_at = _now()
    state.lease = WorkerLease(WORKER, claim=never, holder="deploy1:SECRETHOLDERID")

    response = _health_with(registry)

    assert response.status_code == 503
    assert response.json()["workers"][WORKER]["status"] == "lease_unknown"
    assert "SECRETHOLDERID" not in response.text


def test_health_turns_200_again_when_the_lease_answer_arrives() -> None:
    lease = StubLease("unknown", False)
    registry = WorkerRegistry()
    state = registry.register(WORKER, interval_seconds=900, enabled=True)
    state.started_at = _now()
    state.lease = lease
    assert _health_with(registry).status_code == 503

    lease.state = "standby"

    assert _health_with(registry).status_code == 200


def test_an_unleased_worker_reports_no_lease_field() -> None:
    registry = WorkerRegistry()
    registry.register("outbox", interval_seconds=5, enabled=True).last_success_at = _now()

    assert "lease" not in _health_with(registry).json()["workers"]["outbox"]


# -- shutdown ---------------------------------------------------------------------


async def test_the_keeper_is_stopped_only_after_the_worker_has_fully_unwound() -> None:
    """A cancelled tick may still be cleaning up; until it has, nobody else may be
    allowed to start the same work, so the lease has to outlive it."""
    order: list[str] = []
    lease = StubLease("held")
    lease.order = order
    registry = WorkerRegistry()
    state = registry.register(WORKER, interval_seconds=900, enabled=True)
    state.lease = lease

    async def worker() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(0)  # cleanup that needs a turn of the event loop
            order.append("worker unwound")
            raise

    state.task = asyncio.create_task(worker())
    await asyncio.sleep(0)

    await registry.stop_all()

    assert order == ["worker unwound", "lease stopped"]


# -- the plan's "done when": two processes, one lease --------------------------------


class _Process:
    """One API process's copy of a leased worker: its own state, keeper and
    supervised loop, over a lease store shared with the other."""

    def __init__(self, name: str, vt: VirtualTime, store: FakeLeaseStore, tick_seconds: float):
        self.name = name
        self.vt = vt
        self.tick_seconds = tick_seconds
        self.ticks: list[float] = []
        self.active = 0
        self.state = WorkerState(name=WORKER, interval_seconds=100)

        async def claim() -> bool:
            return await store.claim(WORKER, name, TTL_SECONDS)

        self.lease = WorkerLease(
            WORKER, claim=claim, holder=name, monotonic=vt.monotonic, sleep=vt.sleep
        )
        self.state.lease = self.lease
        self.task: asyncio.Task[None] | None = None

    async def tick(self) -> None:
        self.active += 1
        self.ticks.append(self.vt.now)
        try:
            await self.vt.sleep(self.tick_seconds)
        finally:
            self.active -= 1

    def start(self) -> None:
        self.lease.start()
        self.task = asyncio.create_task(
            run_supervised(self.tick, state=self.state, sleep=self.vt.sleep)
        )

    async def crash(self) -> None:
        """What SIGKILL does: everything stops at once and nothing is released."""
        assert self.task is not None
        self.task.cancel()
        await self.lease.stop()
        await asyncio.gather(self.task, return_exceptions=True)


@pytest.mark.parametrize("tick_seconds", [30, 200])
async def test_only_one_process_ticks_at_a_time_even_when_a_tick_outlasts_the_ttl(
    tick_seconds: float,
) -> None:
    """A 200 s tick is more than three TTLs. The heartbeat keeps the lease through
    it, so the other process never gets in."""
    vt = VirtualTime()
    store = FakeLeaseStore(clock=vt.monotonic)
    a, b = _Process("A", vt, store, tick_seconds), _Process("B", vt, store, tick_seconds)
    a.start()
    b.start()
    try:
        peak = 0
        for _ in range(1500):
            await vt.advance(1)
            peak = max(peak, a.active + b.active)

        assert peak == 1
        assert a.ticks and not b.ticks  # A claimed first and kept it throughout
        assert b.state.status() == "standby"
        assert a.state.status() in ("running", "starting")
    finally:
        await a.crash()
        await b.crash()


async def test_the_other_process_takes_over_within_one_ttl_after_the_first_dies() -> None:
    vt = VirtualTime()
    store = FakeLeaseStore(clock=vt.monotonic)
    a, b = _Process("A", vt, store, 30), _Process("B", vt, store, 30)
    a.start()
    b.start()
    try:
        await vt.advance(500)
        assert a.ticks and not b.ticks
        died_at = vt.now
        expiry = store.rows[WORKER][1]  # A's last renewal + 60
        assert died_at < expiry <= died_at + TTL_SECONDS

        await a.crash()
        taken_over = None
        for _ in range(300):
            await vt.advance(1)
            if b.ticks:
                taken_over = b.ticks[0]
                break

        assert taken_over is not None
        assert taken_over >= expiry  # it cannot have jumped a live lease
        # within one TTL of the death, plus the standby's polling and the loop's own
        assert taken_over - died_at <= TTL_SECONDS + STANDBY_POLL_SECONDS + LEASE_POLL_SECONDS + 1
        assert a.active == 0 and b.active <= 1
    finally:
        await b.crash()


async def test_a_graceful_stop_hands_over_no_slower_than_a_crash() -> None:
    """There is no release function in v1: stopping the keeper just stops
    renewing, so handover is by expiry either way."""
    vt = VirtualTime()
    store = FakeLeaseStore(clock=vt.monotonic)
    a, b = _Process("A", vt, store, 30), _Process("B", vt, store, 30)
    a.start()
    b.start()
    try:
        await vt.advance(300)
        stopped_at = vt.now
        registry = WorkerRegistry()
        registry.workers[WORKER] = a.state
        a.state.task = a.task
        await registry.stop_all()

        for _ in range(300):
            await vt.advance(1)
            if b.ticks:
                break

        assert b.ticks
        assert (
            b.ticks[0] - stopped_at <= TTL_SECONDS + STANDBY_POLL_SECONDS + LEASE_POLL_SECONDS + 1
        )
    finally:
        await b.crash()


async def test_a_worker_wedged_in_a_tick_stops_renewing_so_the_other_can_take_over() -> None:
    """A's tick never finishes. Once it has outrun A's stale window the keeper stops
    renewing, the lease lapses, and B (whose tick does finish) takes over. A's stuck
    tick is still running when B starts -- the bounded overlap the design accepts, in
    exchange for a standby not being blocked forever by a heartbeating zombie."""
    import time

    vt = VirtualTime()
    store = FakeLeaseStore(clock=vt.monotonic)
    a, b = _Process("A", vt, store, 1_000_000), _Process("B", vt, store, 30)
    a.lease = WorkerLease(
        WORKER,
        claim=lambda: store.claim(WORKER, "A", TTL_SECONDS),
        holder="A",
        monotonic=vt.monotonic,
        sleep=vt.sleep,
        wants_lease=lambda: not a.state.tick_running_too_long(),
    )
    a.state.lease = a.lease
    a.start()
    b.start()
    try:
        await vt.advance(40)
        assert a.active == 1 and not b.ticks  # A holds it, mid-tick, and is renewing
        # The clock tick_running_too_long reads is the real one: pretend A's tick began
        # long enough ago to be past its stale window (2700 s for a 900 s worker; here
        # 300 s for a 100 s one).
        a.state.current_tick_started = time.monotonic() - a.state.stale_after.total_seconds() - 1

        for _ in range(300):
            await vt.advance(1)
            if b.ticks:
                break

        assert b.ticks, "a heartbeating-but-wedged holder must not block every standby forever"
        assert a.active == 1  # the zombie tick is still there: bounded overlap, not cancelled
    finally:
        await a.crash()
        await b.crash()
