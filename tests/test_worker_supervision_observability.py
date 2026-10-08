"""What the supervisor does for the people watching it: reports a worker's
failed ticks to the error tracker as one thinned issue per worker and exception type, and
pings the worker's healthcheck after each successful tick.

The tracker and the check are fakes here (their own tests are test_error_reporting.py and
test_worker_pings.py). What is pinned: what is reported and what is not, how often, that a
worker that stands by or is cancelled reports and pings nothing, and that neither a reporter
nor a heartbeat that raises can stop the loop."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import pytest

from between_jobs.api import worker_supervision
from between_jobs.api.worker_pings import WorkerPinger
from between_jobs.api.worker_supervision import (
    FAIL_PING_AFTER_FAILURES,
    REPORT_MIN_SPACING_SECONDS,
    WorkerState,
    run_supervised,
)


class StopAfter:
    """A fake `sleep` that records delays, advances the fake clock by them, and cancels the
    loop (as shutdown does) on the n-th call."""

    def __init__(self, stop_after: int, clock: Clock | None = None) -> None:
        self.delays: list[float] = []
        self._stop_after = stop_after
        self._clock = clock

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        if self._clock is not None:
            self._clock.advance(delay)
        if len(self.delays) >= self._stop_after:
            raise asyncio.CancelledError


class Clock:
    def __init__(self) -> None:
        self.now = 10_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Heartbeat:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.error: Exception | None = None

    def succeeded(self) -> None:
        self.calls.append("ok")
        if self.error is not None:
            raise self.error

    def failed(self) -> None:
        self.calls.append("fail")
        if self.error is not None:
            raise self.error


class StubLease:
    def __init__(self, state: str = "held", may_tick: bool = True) -> None:
        self.state = state
        self.may = may_tick
        self.crashed = False

    def status(self) -> str:
        return self.state

    def may_tick(self) -> bool:
        return self.may

    def report(self) -> dict[str, Any]:
        return {"state": self.state}

    async def stop(self) -> None: ...


class Reports:
    def __init__(self) -> None:
        self.calls: list[tuple[BaseException, dict[str, Any]]] = []
        self.error: Exception | None = None

    def __call__(self, error: BaseException, **kwargs: Any) -> None:
        self.calls.append((error, kwargs))
        if self.error is not None:
            raise self.error

    @property
    def counts(self) -> list[str]:
        return [call[1]["tags"]["consecutive_failures"] for call in self.calls]


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(worker_supervision, "_monotonic", fake)
    return fake


@pytest.fixture
def reports(monkeypatch: pytest.MonkeyPatch) -> Reports:
    recorder = Reports()
    monkeypatch.setattr(worker_supervision, "report_exception", recorder)
    return recorder


def _failing(*errors: Exception | None) -> Any:
    """A tick that raises each of `errors` in turn (None = succeed), then keeps succeeding."""
    queue = list(errors)

    async def tick() -> None:
        error = queue.pop(0) if queue else None
        if error is not None:
            raise error

    return tick


async def _run(tick: Any, state: WorkerState, sleep: StopAfter) -> None:
    with pytest.raises(asyncio.CancelledError):
        await run_supervised(tick, state=state, sleep=sleep)


# --- healthcheck pings ----------------------------------------------------------------------


async def test_every_successful_tick_pings_and_a_failed_one_does_not_ping_success() -> None:
    heartbeat = Heartbeat()
    state = WorkerState(name="w", interval_seconds=60, heartbeat=heartbeat)

    await _run(_failing(None, RuntimeError("x"), None, None), state, StopAfter(4))

    assert heartbeat.calls == ["ok", "ok", "ok"]  # the failed second tick sent nothing


async def test_a_worker_tells_its_check_failing_only_from_the_third_failure_in_a_row() -> None:
    heartbeat = Heartbeat()
    state = WorkerState(name="w", interval_seconds=60, heartbeat=heartbeat)
    errors = [RuntimeError("x")] * 5

    await _run(_failing(*errors, None), state, StopAfter(6))

    assert FAIL_PING_AFTER_FAILURES == 3
    assert heartbeat.calls == ["fail", "fail", "fail", "ok"]  # failures 3, 4 and 5, then recovery


async def test_one_failure_the_retry_cures_pings_nothing_bad() -> None:
    heartbeat = Heartbeat()
    state = WorkerState(name="w", interval_seconds=60, heartbeat=heartbeat)

    await _run(_failing(RuntimeError("blip"), None), state, StopAfter(2))

    assert heartbeat.calls == ["ok"]


async def test_a_tick_that_never_finishes_pings_nothing() -> None:
    heartbeat = Heartbeat()
    state = WorkerState(name="w", interval_seconds=60, heartbeat=heartbeat)

    async def hangs() -> None:
        await asyncio.sleep(3600)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(run_supervised(hangs, state=state), timeout=0.05)

    assert heartbeat.calls == []


async def test_a_worker_without_a_heartbeat_just_runs() -> None:
    state = WorkerState(name="w", interval_seconds=60)
    assert state.heartbeat is None
    await _run(_failing(None, RuntimeError("x"), None), state, StopAfter(3))


async def test_a_heartbeat_that_raises_cannot_stop_the_loop(
    caplog: pytest.LogCaptureFixture,
) -> None:
    heartbeat = Heartbeat()
    heartbeat.error = RuntimeError("the pinger is broken")
    ticks: list[int] = []

    async def tick() -> None:
        ticks.append(1)

    state = WorkerState(name="w", interval_seconds=60, heartbeat=heartbeat)
    with caplog.at_level(logging.WARNING):
        await _run(tick, state, StopAfter(4))

    assert len(ticks) == 4
    assert state.consecutive_failures == 0 and state.last_success_at is not None
    assert "worker heartbeat failed" in caplog.text


# -- ... and the lease -----------------------------------------------------------------------------


async def test_a_worker_that_does_not_hold_its_lease_ticks_nothing_pings_nothing_reports_nothing(
    reports: Reports,
) -> None:
    heartbeat = Heartbeat()
    ticks: list[int] = []

    async def tick() -> None:
        ticks.append(1)
        raise RuntimeError("would be reported")

    state = WorkerState(
        name="w",
        interval_seconds=60,
        heartbeat=heartbeat,
        lease=StubLease("standby", may_tick=False),
    )
    await _run(tick, state, StopAfter(5))

    assert ticks == [] and heartbeat.calls == [] and reports.calls == []


async def test_a_worker_that_holds_its_lease_pings() -> None:
    heartbeat = Heartbeat()
    state = WorkerState(name="w", interval_seconds=60, heartbeat=heartbeat, lease=StubLease("held"))
    await _run(_failing(None, None), state, StopAfter(2))
    assert heartbeat.calls == ["ok", "ok"]


async def test_a_tick_that_finishes_after_the_lease_lapsed_does_not_ping() -> None:
    heartbeat = Heartbeat()
    lease = StubLease("held")
    state = WorkerState(name="w", interval_seconds=60, heartbeat=heartbeat, lease=lease)

    async def tick() -> None:
        lease.state = "standby"  # the lease went to another process while this tick ran

    await _run(tick, state, StopAfter(1))

    assert heartbeat.calls == []


async def test_a_failure_after_the_lease_lapsed_does_not_ping_failing_either() -> None:
    heartbeat = Heartbeat()
    lease = StubLease("held")
    state = WorkerState(name="w", interval_seconds=60, heartbeat=heartbeat, lease=lease)

    async def tick() -> None:
        lease.state = "unknown"
        raise RuntimeError("x")

    await _run(tick, state, StopAfter(4))  # failures 1..: every one past the third would ping

    assert heartbeat.calls == []


# -- ... through the real pinger ------------------------------------------------------------------


async def test_through_the_real_pinger_one_ping_goes_out_however_fast_the_loop_runs() -> None:
    seen: list[str] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text="OK")

    url = "https://hc-ping.example.test/3f9c2a7e-1b4d-4e8a-9c6f-0d5e7a1b2c3d"
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    pinger = WorkerPinger("outbox", url, client, clock=lambda: 5.0)
    state = WorkerState(name="outbox", interval_seconds=5, heartbeat=pinger)

    async def tick() -> None:
        await asyncio.sleep(0)

    await _run(tick, state, StopAfter(20))
    for _ in range(20):
        await asyncio.sleep(0)

    assert seen == [url]  # twenty ticks, one ping
    await pinger.aclose()
    await client.aclose()


async def test_through_the_real_pinger_a_persistently_failing_worker_says_so() -> None:
    seen: list[str] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text="OK")

    url = "https://hc-ping.example.test/3f9c2a7e-1b4d-4e8a-9c6f-0d5e7a1b2c3d"
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    pinger = WorkerPinger("outbox", url, client, clock=lambda: 5.0)
    state = WorkerState(name="outbox", interval_seconds=5, heartbeat=pinger)

    await _run(_failing(*[RuntimeError("down")] * 6), state, StopAfter(5))
    for _ in range(20):
        await asyncio.sleep(0)

    assert seen == [url + "/fail"]
    await pinger.aclose()
    await client.aclose()


# --- error reports -------------------------------------------------------------------------------


async def test_the_first_failure_is_reported_with_the_worker_the_count_and_a_fingerprint(
    reports: Reports, clock: Clock
) -> None:
    error = ConnectionError("db down")
    state = WorkerState(name="outbox", interval_seconds=60)

    await _run(_failing(error), state, StopAfter(2, clock))

    [(reported, kwargs)] = reports.calls
    assert reported is error
    assert kwargs == {
        "tags": {"worker": "outbox", "consecutive_failures": "1"},
        "fingerprint": ["worker-tick-failure", "outbox", "builtins.ConnectionError"],
    }


async def test_failures_in_a_row_are_reported_on_the_1st_2nd_4th_8th_not_each(
    reports: Reports, clock: Clock
) -> None:
    state = WorkerState(name="w", interval_seconds=60)
    clock.advance(0)

    class AdvancingSleep(StopAfter):
        async def __call__(self, delay: float) -> None:
            clock.advance(601)  # past the ten-minute spacing: spacing is not what limits this test
            await super().__call__(delay)

    await _run(_failing(*[RuntimeError("x")] * 10), state, AdvancingSleep(10))

    assert reports.counts == ["1", "2", "4", "8"]


async def test_the_same_kind_of_failure_is_not_reported_again_within_ten_minutes(
    reports: Reports, clock: Clock
) -> None:
    state = WorkerState(name="w", interval_seconds=60)

    await _run(_failing(*[RuntimeError("x")] * 4), state, StopAfter(4))  # the clock never moves

    assert reports.counts == ["1"]


async def test_a_worker_that_fails_every_other_tick_is_reported_once_not_every_time(
    reports: Reports, clock: Clock
) -> None:
    state = WorkerState(name="outbox", interval_seconds=5)
    ticks: list[Exception | None] = [
        RuntimeError("blip") if i % 2 == 0 else None for i in range(40)
    ]

    await _run(_failing(*ticks), state, StopAfter(40, clock))

    assert len(reports.calls) == 1  # failure number 1, over and over, within ten minutes


@pytest.mark.parametrize(("gap", "expected"), [(599, ["1"]), (600, ["1", "2"]), (601, ["1", "2"])])
async def test_the_same_failure_is_reported_again_once_ten_minutes_have_passed(
    reports: Reports, clock: Clock, gap: int, expected: list[str]
) -> None:
    """Failure 2 is a candidate for a report (a power of two); whether it is sent depends only
    on the time since failure 1. The numbers are written out, not read from the module."""
    sleeps = 0

    async def sleep(delay: float) -> None:
        nonlocal sleeps
        sleeps += 1
        clock.advance(gap)
        if sleeps >= 2:
            raise asyncio.CancelledError

    state = WorkerState(name="w", interval_seconds=60)
    with pytest.raises(asyncio.CancelledError):
        await run_supervised(
            _failing(RuntimeError("x"), RuntimeError("x")), state=state, sleep=sleep
        )

    assert reports.counts == expected
    assert REPORT_MIN_SPACING_SECONDS == 600


async def test_a_different_exception_type_is_reported_on_its_own_and_with_its_own_fingerprint(
    reports: Reports, clock: Clock
) -> None:
    state = WorkerState(name="w", interval_seconds=60)

    await _run(_failing(RuntimeError("a"), ValueError("b")), state, StopAfter(2))

    fingerprints = [call[1]["fingerprint"] for call in reports.calls]
    assert fingerprints == [
        ["worker-tick-failure", "w", "builtins.RuntimeError"],
        ["worker-tick-failure", "w", "builtins.ValueError"],
    ]


async def test_a_long_crash_loop_is_a_handful_of_events_all_in_one_issue(
    reports: Reports, clock: Clock
) -> None:
    state = WorkerState(name="job_registry_poller", interval_seconds=900, backoff_base_seconds=60)

    await _run(_failing(*[OSError("net")] * 300), state, StopAfter(300, clock))

    assert state.consecutive_failures == 300
    assert len(reports.calls) <= 8
    assert len({tuple(call[1]["fingerprint"]) for call in reports.calls}) == 1


async def test_a_worker_failure_is_reported_whatever_the_worker_is_called(reports: Reports) -> None:
    for name in ("outbox", "saved_search_matcher", "hiring_signal_cache_purge"):
        state = WorkerState(name=name, interval_seconds=60)
        await _run(_failing(RuntimeError("x")), state, StopAfter(1))
    assert [call[1]["tags"]["worker"] for call in reports.calls] == [
        "outbox",
        "saved_search_matcher",
        "hiring_signal_cache_purge",
    ]


async def test_cancelling_the_worker_at_shutdown_reports_nothing(reports: Reports) -> None:
    started = asyncio.Event()

    async def tick() -> None:
        started.set()
        await asyncio.sleep(3600)

    heartbeat = Heartbeat()
    task = asyncio.create_task(
        run_supervised(tick, state=WorkerState("w", 60, heartbeat=heartbeat))
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert reports.calls == [] and heartbeat.calls == []


async def test_a_cancelled_future_inside_a_tick_that_is_not_shutdown_is_a_reported_failure(
    reports: Reports,
) -> None:
    async def tick() -> None:
        raise asyncio.CancelledError  # an inner future somebody else cancelled

    state = WorkerState(name="w", interval_seconds=60)
    await _run(tick, state, StopAfter(1))

    [(reported, kwargs)] = reports.calls
    assert isinstance(reported, asyncio.CancelledError)
    assert kwargs["fingerprint"][2] == "asyncio.exceptions.CancelledError"


async def test_a_tick_that_fails_after_the_lease_lapsed_is_reported_but_pings_nothing(
    reports: Reports,
) -> None:
    heartbeat = Heartbeat()
    lease = StubLease("held")

    async def tick() -> None:
        lease.state = "standby"  # the lease went to another process while this tick ran
        raise RuntimeError("x")

    state = WorkerState(name="w", interval_seconds=60, heartbeat=heartbeat, lease=lease)
    await _run(tick, state, StopAfter(1))

    assert reports.counts == ["1"]  # a failure of a tick this process ran is still worth knowing
    assert heartbeat.calls == []  # the check belongs to whichever process holds the lease


async def test_a_cancelled_future_inside_a_tick_never_pings_success_and_fails_from_the_third() -> (
    None
):
    async def tick() -> None:
        raise asyncio.CancelledError  # an inner future somebody else cancelled

    heartbeat = Heartbeat()
    state = WorkerState(name="w", interval_seconds=60, heartbeat=heartbeat)
    await _run(tick, state, StopAfter(4))

    assert state.consecutive_failures == 4
    assert heartbeat.calls == ["fail", "fail"]  # failures 3 and 4 only, never "ok"


async def test_a_reporter_that_raises_cannot_stop_the_loop(
    reports: Reports, caplog: pytest.LogCaptureFixture
) -> None:
    reports.error = RuntimeError("the tracker client is broken")
    ticks: list[int] = []

    async def tick() -> None:
        ticks.append(1)
        raise ValueError("a real failure")

    state = WorkerState(name="w", interval_seconds=60)
    with caplog.at_level(logging.WARNING):
        await _run(tick, state, StopAfter(3))

    assert len(ticks) == 3
    assert state.consecutive_failures == 3
    assert "worker failure report failed" in caplog.text


async def test_nothing_is_reported_for_a_worker_that_succeeds(reports: Reports) -> None:
    await _run(_failing(None, None, None), WorkerState(name="w", interval_seconds=60), StopAfter(3))
    assert reports.calls == []
