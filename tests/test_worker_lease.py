"""The lease keeper (launch plan P2.19): worker_lease.WorkerLease.

All on a virtual clock (tests/virtual_time.py), so "58 seconds later" is a method
call. What is pinned:

- the arithmetic the design stands on: a holder believes it holds for 58 s after it
  SENT its last good claim, starts no tick in the last 15 s of that, renews every
  15 s on a fixed schedule, and survives two lost heartbeats;
- unknown is never standby: only an explicit refusal is standby, and only for a
  bounded time; every other failure is classified and ages out to `unknown`;
- the keeper's own discipline: it always settles its first answer, stops cleanly,
  logs transitions rather than every claim, and a heartbeat that cannot run
  (wedged worker) lets the lease lapse instead of renewing forever.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from lease_fakes import FakeLeaseStore
from postgrest.exceptions import APIError
from virtual_time import VirtualTime

from between_jobs.api import worker_lease
from between_jobs.api.worker_lease import (
    LOCAL_SLACK_SECONDS,
    RENEW_SECONDS,
    STANDBY_FRESH_SECONDS,
    STANDBY_POLL_SECONDS,
    TICK_START_MARGIN_SECONDS,
    TTL_SECONDS,
    WorkerLease,
)
from between_jobs.api.worker_leases_store import LeaseAnswerError

WORKER = "job_registry_poller"
DEADLINE = TTL_SECONDS - LOCAL_SLACK_SECONDS  # 58


class Scripted:
    """A claim whose next answers the test chooses; after the script runs out it
    repeats its last entry. Records the virtual time of every call."""

    def __init__(self, vt: VirtualTime, *script: bool | BaseException) -> None:
        self._vt = vt
        self.script = list(script)
        self.calls: list[float] = []

    async def __call__(self) -> bool:
        self.calls.append(self._vt.now)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture
def vt() -> VirtualTime:
    return VirtualTime()


def _lease(vt: VirtualTime, claim: Callable[[], object], **kwargs: object) -> WorkerLease:
    return WorkerLease(
        WORKER,
        claim=claim,  # type: ignore[arg-type]
        holder="deploy:abc",
        monotonic=vt.monotonic,
        sleep=vt.sleep,
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.fixture
async def started(vt: VirtualTime) -> AsyncIterator[list[WorkerLease]]:
    leases: list[WorkerLease] = []
    yield leases
    for lease in leases:
        await lease.stop()


async def _start(
    started: list[WorkerLease], vt: VirtualTime, claim: Callable[[], object], **kwargs: object
) -> WorkerLease:
    lease = _lease(vt, claim, **kwargs)
    started.append(lease)
    lease.start()
    await vt.advance(0)
    return lease


# -- the arithmetic ----------------------------------------------------------


async def test_a_good_claim_makes_it_held_and_a_tick_may_start(
    vt: VirtualTime, started: list[WorkerLease]
) -> None:
    lease = await _start(started, vt, Scripted(vt, True))

    assert lease.status() == "held"
    assert lease.may_tick()


async def test_it_renews_every_15_seconds_on_a_fixed_schedule(
    vt: VirtualTime, started: list[WorkerLease]
) -> None:
    claim = Scripted(vt, True)
    await _start(started, vt, claim)

    await vt.advance(60)

    assert claim.calls == [0, 15, 30, 45, 60]


async def test_a_holder_believes_it_holds_58_seconds_from_when_it_sent_the_claim(
    vt: VirtualTime, started: list[WorkerLease]
) -> None:
    """After a good claim at t=0 every later one fails, so only time moves the
    belief: held until 58, and no new tick in the last 15 of it."""
    lease = await _start(started, vt, Scripted(vt, True, RuntimeError("down")))
    assert DEADLINE == 58 and TICK_START_MARGIN_SECONDS == 15

    await vt.advance(42.9)
    assert lease.may_tick()  # 42.9 + 15 < 58
    await vt.advance(0.2)
    assert lease.status() == "held" and not lease.may_tick()  # 43.1 + 15 >= 58
    await vt.advance(15.0)  # t = 58.1: the belief has run out and nothing renewed it
    assert lease.status() == "unknown"


async def test_two_lost_heartbeats_cost_a_tick_start_only_for_two_seconds(
    vt: VirtualTime, started: list[WorkerLease]
) -> None:
    """Good at 0, lost at 15 and 30, good at 45. The lease never leaves `held`;
    the only moment a tick could not start is 43..45, when belief is short but
    the next renewal has not landed yet."""
    claim = Scripted(vt, True, RuntimeError("blip"), RuntimeError("blip"), True)
    lease = await _start(started, vt, claim)

    await vt.advance(42)
    assert lease.status() == "held" and lease.may_tick()
    await vt.advance(2)  # t = 44
    assert lease.status() == "held" and not lease.may_tick()
    await vt.advance(2)  # t = 46: the heartbeat at 45 landed
    assert lease.status() == "held" and lease.may_tick()


async def test_the_belief_runs_from_when_the_claim_was_sent_not_when_it_returned(
    vt: VirtualTime, started: list[WorkerLease]
) -> None:
    """A slow answer must not extend the belief: the database set the expiry when
    it received the request, so counting from the reply would overshoot. The first
    claim is sent at t=0 and answered at t=8; every later one fails, so only that
    one answer sets the deadline."""
    calls: list[float] = []

    async def claim() -> bool:
        calls.append(vt.now)
        if len(calls) > 1:
            raise RuntimeError("down")
        await vt.sleep(8)
        return True

    lease = await _start(started, vt, claim)

    await vt.advance(57.9)
    assert lease.status() == "held"  # 58 from the send, at t=0
    await vt.advance(0.2)  # t = 58.1: it would still be held, until 66, if counted from the reply
    assert lease.status() == "unknown"


# -- unknown is never standby -------------------------------------------------


async def test_an_explicit_refusal_is_standby_and_clears_the_belief_at_once(
    vt: VirtualTime, started: list[WorkerLease]
) -> None:
    lease = await _start(started, vt, Scripted(vt, True, False))
    assert lease.status() == "held"

    await vt.advance(15)  # the renewal at 15 is refused

    assert lease.status() == "standby"
    assert not lease.may_tick()


async def test_a_failure_after_a_refusal_never_refreshes_standby(
    vt: VirtualTime, started: list[WorkerLease]
) -> None:
    """Standby is trusted for 30 s after the last REFUSAL. A run of failures is
    not another refusal, so it can only age the answer out into unknown."""
    lease = await _start(started, vt, Scripted(vt, False, RuntimeError("down")))
    assert lease.status() == "standby"

    await vt.advance(STANDBY_FRESH_SECONDS - 1)
    assert lease.status() == "standby"
    await vt.advance(2)
    assert lease.status() == "unknown"


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (APIError({"message": "no such function", "code": "PGRST202"}), "function_missing"),
        (APIError({"message": "permission denied", "code": "42501"}), "database"),
        (APIError({"message": "timeout", "code": "57014"}), "database"),
        (LeaseAnswerError("neither true nor false"), "malformed"),
        (httpx.ConnectError("refused"), "transport"),
        (TimeoutError(), "timeout"),
        (RuntimeError("anything else"), "unexpected"),
    ],
)
async def test_every_way_a_claim_can_fail_is_unknown_and_classified(
    vt: VirtualTime, started: list[WorkerLease], error: Exception, kind: str
) -> None:
    lease = await _start(started, vt, Scripted(vt, error))

    assert lease.status() == "unknown"  # never standby, never held
    assert not lease.may_tick()
    assert lease.last_error == kind
    assert lease.claim_failures == 1
    assert lease.report() == {"state": "unknown", "claim_failures": 1, "last_claim_error": kind}


async def test_a_claim_that_hangs_is_cut_off_and_counted_as_a_timeout(
    vt: VirtualTime, started: list[WorkerLease]
) -> None:
    async def hangs() -> bool:
        await asyncio.Event().wait()
        return True

    lease = _lease(vt, hangs, claim_timeout=0.05)
    started.append(lease)
    lease.start()

    await asyncio.wait_for(lease.first_answer(), timeout=2)  # real time: the timeout is real

    assert lease.last_error == "timeout"
    assert lease.status() == "unknown"


async def test_recovery_clears_the_failure_record(
    vt: VirtualTime, started: list[WorkerLease]
) -> None:
    lease = await _start(started, vt, Scripted(vt, RuntimeError("down"), True))
    assert lease.claim_failures == 1

    await vt.advance(STANDBY_POLL_SECONDS)

    assert lease.status() == "held"
    assert lease.claim_failures == 0 and lease.last_error is None


# -- the keeper's own discipline --------------------------------------------------


async def test_a_standby_asks_every_10_seconds_and_a_holder_every_15(
    vt: VirtualTime, started: list[WorkerLease]
) -> None:
    standby_calls = Scripted(vt, False)
    await _start(started, vt, standby_calls)
    await vt.advance(30)
    assert standby_calls.calls == [0, 10, 20, 30]
    assert STANDBY_POLL_SECONDS == 10 and RENEW_SECONDS == 15


async def test_the_first_answer_settles_even_when_the_first_claim_fails(
    vt: VirtualTime, started: list[WorkerLease]
) -> None:
    lease = _lease(vt, Scripted(vt, RuntimeError("down")))
    started.append(lease)
    lease.start()
    await vt.advance(0)

    await asyncio.wait_for(lease.first_answer(), timeout=1)  # would hang if it never settled


async def test_a_worker_that_does_not_want_the_lease_stops_renewing_and_it_lapses(
    vt: VirtualTime, started: list[WorkerLease]
) -> None:
    """The wedge gate: a tick that has run past its stale window must not keep the
    lease alive by heartbeat, or no standby could ever take over."""
    wedged = {"now": False}
    claim = Scripted(vt, True)
    lease = await _start(started, vt, claim, wants_lease=lambda: not wedged["now"])
    await vt.advance(20)
    assert claim.calls == [0, 15]

    wedged["now"] = True
    await vt.advance(60)

    assert claim.calls == [0, 15]  # no claim after the gate closed
    assert lease.status() == "unknown"  # the belief ran out by itself


async def test_a_wants_lease_callable_that_raises_does_not_stop_the_heartbeat(
    vt: VirtualTime, started: list[WorkerLease]
) -> None:
    def broken() -> bool:
        raise RuntimeError("bug in the gate")

    claim = Scripted(vt, True)
    await _start(started, vt, claim, wants_lease=broken)
    await vt.advance(30)

    assert claim.calls == [0, 15, 30]


async def test_a_broken_wedge_gate_is_reported_once_not_every_cycle(
    vt: VirtualTime, started: list[WorkerLease], caplog: pytest.LogCaptureFixture
) -> None:
    def broken() -> bool:
        raise RuntimeError("bug in the gate")

    caplog.set_level(logging.WARNING, logger=worker_lease.__name__)
    await _start(started, vt, Scripted(vt, True), wants_lease=broken)

    await vt.advance(300)  # twenty cycles

    gate = [r for r in caplog.records if "wedge gate failed" in r.getMessage()]
    assert len(gate) == 1


async def test_stopping_cancels_the_keeper_and_it_claims_no_more(
    vt: VirtualTime, started: list[WorkerLease]
) -> None:
    claim = Scripted(vt, True)
    lease = await _start(started, vt, claim)

    await lease.stop()
    await vt.advance(120)

    assert claim.calls == [0]
    assert not lease.crashed  # a stop is not a crash


async def test_a_keeper_that_dies_on_its_own_reports_crashed(
    vt: VirtualTime, started: list[WorkerLease]
) -> None:
    class Fatal(BaseException):
        pass

    lease = await _start(started, vt, Scripted(vt, Fatal()))

    assert lease.crashed
    assert lease.status() == "unknown"  # nothing is renewing it any more


async def test_stopping_a_keeper_that_had_crashed_says_why(
    vt: VirtualTime, started: list[WorkerLease], caplog: pytest.LogCaptureFixture
) -> None:
    class Fatal(BaseException):
        pass

    lease = await _start(started, vt, Scripted(vt, Fatal()))
    caplog.set_level(logging.ERROR, logger=worker_lease.__name__)

    await lease.stop()  # must not raise, and must not stay silent

    assert [r.getMessage() for r in caplog.records] == [
        "worker lease keeper had ended with an error"
    ]


async def test_it_logs_transitions_not_every_claim(
    vt: VirtualTime, started: list[WorkerLease], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=worker_lease.__name__)
    await _start(started, vt, Scripted(vt, True))

    await vt.advance(600)  # forty renewals

    changes = [r for r in caplog.records if r.getMessage() == "worker lease state changed"]
    assert len(changes) == 1
    assert changes[0].ctx["state"] == "held"  # type: ignore[attr-defined]
    assert changes[0].ctx["worker"] == WORKER  # type: ignore[attr-defined]


async def test_a_streak_of_failures_logs_on_a_doubling_schedule(
    vt: VirtualTime, started: list[WorkerLease], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=worker_lease.__name__)
    await _start(started, vt, Scripted(vt, httpx.ConnectError("refused")))

    await vt.advance(STANDBY_POLL_SECONDS * 15)  # 16 consecutive failures

    warnings = [r for r in caplog.records if r.getMessage() == "could not claim the worker lease"]
    assert [r.ctx["consecutive_failures"] for r in warnings] == [1, 2, 4, 8, 16]  # type: ignore[attr-defined]


async def test_a_missing_function_is_one_clear_error_naming_the_fix(
    vt: VirtualTime, started: list[WorkerLease], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR, logger=worker_lease.__name__)
    await _start(started, vt, Scripted(vt, APIError({"message": "x", "code": "PGRST202"})))

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "apply the database migrations" in errors[0].getMessage()


async def test_the_log_never_carries_anything_but_ids(
    vt: VirtualTime, started: list[WorkerLease], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=worker_lease.__name__)
    await _start(started, vt, Scripted(vt, RuntimeError("postgres://user:secret@host/db"), True))
    await vt.advance(30)

    for record in caplog.records:
        assert "secret" not in record.getMessage()
        ctx = getattr(record, "ctx", {})
        assert set(ctx) <= {"worker", "holder", "state", "kind", "consecutive_failures"}


# -- the same keeper against the shared fake store ---------------------------------


async def test_two_keepers_on_one_store_never_both_hold_it(
    vt: VirtualTime, started: list[WorkerLease]
) -> None:
    store = FakeLeaseStore(clock=vt.monotonic)

    def keeper(holder: str) -> WorkerLease:
        async def claim() -> bool:
            return await store.claim(WORKER, holder, TTL_SECONDS)

        lease = _lease(vt, claim)
        started.append(lease)
        return lease

    a, b = keeper("A"), keeper("B")
    a.start()
    b.start()

    for _ in range(300):
        await vt.advance(1)
        assert not (a.status() == "held" and b.status() == "held")
    assert {a.status(), b.status()} == {"held", "standby"}
