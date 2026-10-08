"""Supervised background work for a reply that outlives its request (`deferred_reply`):
strong references, a hard cap that never queues, failures that are logged and reported,
cancellation that is silent, and a shutdown that gives running work a short grace period."""

from __future__ import annotations

import asyncio
import gc
import logging
import weakref
from typing import Any

import pytest
from fake_sentry import FakeSentry

from between_jobs.api import error_reporting
from between_jobs.api.deferred_reply import DeferredReplies, Slot
from between_jobs.api.logging_setup import request_id_var


async def _reserve(registry: DeferredReplies) -> Slot:
    slot = await registry.try_reserve()
    assert slot is not None
    return slot


async def _reserve_keyed(registry: DeferredReplies, key: str) -> Slot:
    slot = await registry.try_reserve(key)
    assert slot is not None
    return slot


async def _settle() -> None:
    """Lets every ready task and callback run."""
    for _ in range(3):
        await asyncio.sleep(0)


def test_a_registry_needs_room_for_at_least_one_task() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        DeferredReplies(0)


# -- the bound -----------------------------------------------------------------------------


async def test_places_run_out_at_the_cap_and_the_next_request_is_refused_not_queued() -> None:
    registry = DeferredReplies(max_concurrent=2)

    first = await registry.try_reserve()
    second = await registry.try_reserve()
    refused = await asyncio.wait_for(registry.try_reserve(), timeout=1)  # answers, never waits

    assert first is not None and second is not None
    assert refused is None


async def test_the_default_cap_is_three() -> None:
    registry = DeferredReplies()

    slots = [await registry.try_reserve() for _ in range(4)]

    assert [slot is not None for slot in slots] == [True, True, True, False]


async def test_a_released_place_can_be_taken_again() -> None:
    registry = DeferredReplies(max_concurrent=1)
    slot = await _reserve(registry)
    assert await registry.try_reserve() is None

    slot.release()

    assert await registry.try_reserve() is not None


async def test_releasing_a_place_twice_frees_it_only_once() -> None:
    registry = DeferredReplies(max_concurrent=1)
    slot = await _reserve(registry)

    slot.release()
    slot.release()

    assert await registry.try_reserve() is not None  # the one place, taken again
    assert await registry.try_reserve() is None  # the double release did not make a second


async def test_a_task_gives_its_place_back_when_it_finishes() -> None:
    registry = DeferredReplies(max_concurrent=1)
    gate = asyncio.Event()

    async def work() -> None:
        await gate.wait()

    registry.start(await _reserve(registry), work, name="t")
    await _settle()
    assert await registry.try_reserve() is None  # still running, still holding it

    gate.set()
    await _settle()

    assert registry.running == 0
    assert await registry.try_reserve() is not None


async def test_a_task_that_fails_still_gives_its_place_back() -> None:
    registry = DeferredReplies(max_concurrent=1)

    async def work() -> None:
        raise RuntimeError("boom")

    registry.start(await _reserve(registry), work, name="t")
    await _settle()

    assert await registry.try_reserve() is not None


async def test_a_task_cancelled_before_it_ever_ran_still_gives_its_place_back() -> None:
    registry = DeferredReplies(max_concurrent=1)
    ran = False

    async def work() -> None:
        nonlocal ran
        ran = True

    task = registry.start(await _reserve(registry), work, name="t")
    task.cancel()  # before the loop has given it its first step
    await _settle()

    assert not ran
    assert registry.running == 0
    assert await registry.try_reserve() is not None


# -- one at a time per key -----------------------------------------------------------------


async def test_a_key_holds_one_place_even_while_others_are_free() -> None:
    registry = DeferredReplies(max_concurrent=3)

    mine = await registry.try_reserve("user-a")
    again = await registry.try_reserve("user-a")  # two places are free, but this key has one
    theirs = await registry.try_reserve("user-b")

    assert mine is not None and theirs is not None
    assert again is None
    assert registry.holds("user-a") and registry.holds("user-b")
    assert not registry.holds("user-c")


async def test_a_refusal_for_the_cap_is_told_apart_from_a_refusal_for_the_key() -> None:
    registry = DeferredReplies(max_concurrent=1)
    await _reserve_keyed(registry, "user-a")

    assert await registry.try_reserve("user-b") is None  # the cap: user-b holds nothing
    assert not registry.holds("user-b")
    assert await registry.try_reserve("user-a") is None  # the key: user-a holds the place
    assert registry.holds("user-a")


async def test_work_with_no_key_is_bounded_by_the_cap_alone() -> None:
    registry = DeferredReplies(max_concurrent=2)

    assert await registry.try_reserve() is not None
    assert await registry.try_reserve() is not None  # no key, so nothing says "already"
    assert await registry.try_reserve() is None


async def test_releasing_a_place_frees_its_key_with_it() -> None:
    registry = DeferredReplies()
    slot = await _reserve_keyed(registry, "user-a")

    slot.release()

    assert not registry.holds("user-a")
    assert await registry.try_reserve("user-a") is not None


async def test_releasing_twice_does_not_free_a_key_a_newer_place_holds() -> None:
    registry = DeferredReplies()
    first = await _reserve_keyed(registry, "user-a")
    first.release()
    second = await _reserve_keyed(registry, "user-a")

    first.release()  # a late, repeated release of the old place

    assert registry.holds("user-a")
    assert await registry.try_reserve("user-a") is None
    second.release()
    assert not registry.holds("user-a")


async def test_a_key_is_free_again_however_its_task_ends() -> None:
    registry = DeferredReplies(max_concurrent=4)
    gate = asyncio.Event()

    async def finishes() -> None:
        await gate.wait()

    async def fails() -> None:
        raise RuntimeError("boom")

    async def never_ends() -> None:
        await asyncio.Event().wait()

    async def never_runs() -> None:
        raise AssertionError("cancelled before it ran")

    running = registry.start(await _reserve_keyed(registry, "finishes"), finishes, name="t")
    registry.start(await _reserve_keyed(registry, "fails"), fails, name="t")
    cancelled = registry.start(await _reserve_keyed(registry, "cancelled"), never_ends, name="t")
    unstarted = registry.start(await _reserve_keyed(registry, "unstarted"), never_runs, name="t")
    unstarted.cancel()  # before the loop has given it its first step
    await _settle()
    cancelled.cancel()
    gate.set()
    await _settle()
    await asyncio.gather(running, cancelled, unstarted, return_exceptions=True)
    await _settle()

    for key in ("finishes", "fails", "cancelled", "unstarted"):
        assert not registry.holds(key), key
        assert await registry.try_reserve(key) is not None, key


async def test_a_key_stays_held_while_its_task_runs() -> None:
    registry = DeferredReplies()
    gate = asyncio.Event()

    async def work() -> None:
        await gate.wait()

    registry.start(await _reserve_keyed(registry, "user-a"), work, name="t")
    await _settle()

    assert registry.holds("user-a")
    assert await registry.try_reserve("user-a") is None

    gate.set()
    await _settle()


# -- keeping the task alive ----------------------------------------------------------------


async def test_a_running_task_is_not_lost_when_nobody_else_holds_it() -> None:
    """The event loop keeps only a weak reference to a task: one that waits on something
    nobody else holds is garbage-collected mid-run. The registry is the holder."""
    asyncio.get_running_loop().set_exception_handler(lambda _loop, _ctx: None)  # the control's
    registry = DeferredReplies()

    async def waits_on_a_future_nobody_holds() -> None:
        await asyncio.get_running_loop().create_future()

    unheld = weakref.ref(asyncio.create_task(waits_on_a_future_nobody_holds()))
    held = weakref.ref(
        registry.start(await _reserve(registry), waits_on_a_future_nobody_holds, name="t")
    )
    await _settle()
    gc.collect()

    assert unheld() is None  # the hazard, shown: with no holder the task is simply gone
    assert held() is not None
    assert registry.running == 1

    await registry.shutdown(grace_seconds=0.01)


async def test_the_work_runs_with_the_context_of_the_request_that_started_it() -> None:
    """The request id is a context variable: a task made inside a request carries it, so the
    lines logged from the background work can be traced to the delivery that began it."""
    registry = DeferredReplies()
    seen: list[str | None] = []

    async def work() -> None:
        seen.append(request_id_var.get())

    token = request_id_var.set("req-abc12345")
    try:
        registry.start(await _reserve(registry), work, name="t")
    finally:
        request_id_var.reset(token)
    await _settle()

    assert seen == ["req-abc12345"]


# -- failure -------------------------------------------------------------------------------


async def test_a_failure_is_logged_with_its_context_and_reported_to_the_user(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = DeferredReplies()
    told: list[str] = []

    async def work() -> None:
        raise RuntimeError("the engine fell over")

    async def on_failure() -> None:
        told.append("sorry")

    with caplog.at_level(logging.INFO, logger="between_jobs.api.deferred_reply"):
        registry.start(
            await _reserve(registry),
            work,
            name="prepare",
            context={"update_id": "77", "application_id": "app-1"},
            on_failure=on_failure,
        )
        await _settle()

    failures = [r for r in caplog.records if r.getMessage() == "deferred work failed"]
    assert len(failures) == 1
    assert failures[0].levelno == logging.ERROR
    assert failures[0].exc_info is not None
    assert failures[0].ctx == {  # type: ignore[attr-defined]
        "task": "prepare",
        "update_id": "77",
        "application_id": "app-1",
    }
    assert told == ["sorry"]


async def test_failing_to_tell_the_user_is_logged_and_never_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = DeferredReplies()
    problems: list[dict[str, Any]] = []
    asyncio.get_running_loop().set_exception_handler(lambda _loop, ctx: problems.append(ctx))

    async def work() -> None:
        raise RuntimeError("first")

    async def on_failure() -> None:
        raise RuntimeError("second")

    with caplog.at_level(logging.INFO, logger="between_jobs.api.deferred_reply"):
        task = registry.start(await _reserve(registry), work, name="t", on_failure=on_failure)
        await _settle()
        await task  # does not raise
        gc.collect()

    messages = [r.getMessage() for r in caplog.records]
    assert "deferred work failed" in messages
    assert "could not tell the user the deferred work failed" in messages
    assert problems == []  # nothing reached the loop's "exception was never retrieved" handler


async def test_a_failure_with_nobody_to_tell_is_still_logged_and_contained(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = DeferredReplies()

    async def work() -> None:
        raise ValueError("no one to tell")

    with caplog.at_level(logging.INFO, logger="between_jobs.api.deferred_reply"):
        task = registry.start(await _reserve(registry), work, name="t")
        await _settle()
        await task

    assert [r.getMessage() for r in caplog.records] == ["deferred work failed"]


# -- failure reaches the error tracker -------------------------------------------------------


@pytest.fixture
def sdk(monkeypatch: pytest.MonkeyPatch) -> FakeSentry:
    """Error reporting switched on, with a fake SDK that records what it is handed."""
    fake = FakeSentry()
    monkeypatch.setattr(error_reporting, "_sdk", fake)
    monkeypatch.setattr(error_reporting, "_failure_warned", False)
    return fake


async def test_a_failure_in_the_work_is_reported_with_the_task_name_and_no_ids(
    sdk: FakeSentry,
) -> None:
    """The task runs outside any request, so nothing else would see the exception: the registry
    catches it, and is the one place that can report it. The ids belong in the log lines."""
    registry = DeferredReplies()
    error = RuntimeError("the engine fell over")

    async def work() -> None:
        raise error

    registry.start(
        await _reserve(registry),
        work,
        name="prepare",
        context={"update_id": "77", "application_id": "app-1"},
    )
    await _settle()

    assert sdk.captured == [(error, {"tags": {"task": "prepare"}})]


async def test_the_failure_is_reported_before_the_user_is_told_and_survives_a_failed_telling(
    sdk: FakeSentry,
) -> None:
    registry = DeferredReplies()
    error = RuntimeError("first")
    reported_when_told: list[int] = []

    async def work() -> None:
        raise error

    async def on_failure() -> None:
        reported_when_told.append(len(sdk.captured))
        raise RuntimeError("second")

    task = registry.start(await _reserve(registry), work, name="t", on_failure=on_failure)
    await _settle()
    await task  # does not raise

    assert reported_when_told == [1]
    assert sdk.captured == [(error, {"tags": {"task": "t"}})]


async def test_a_cancelled_task_is_not_reported(sdk: FakeSentry) -> None:
    registry = DeferredReplies()

    async def work() -> None:
        await asyncio.Event().wait()  # never finishes

    task = registry.start(await _reserve(registry), work, name="t")
    await _settle()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert sdk.captured == []


async def test_work_that_succeeds_reports_nothing(sdk: FakeSentry) -> None:
    registry = DeferredReplies()

    async def work() -> None:
        return None

    registry.start(await _reserve(registry), work, name="t")
    await _settle()

    assert sdk.captured == []


async def test_with_reporting_off_a_failure_is_contained_and_nothing_extra_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(error_reporting, "_sdk", None)
    monkeypatch.setattr(error_reporting, "_failure_warned", False)
    registry = DeferredReplies()
    told: list[str] = []

    async def work() -> None:
        raise RuntimeError("boom")

    async def on_failure() -> None:
        told.append("sorry")

    with caplog.at_level(logging.DEBUG):
        task = registry.start(await _reserve(registry), work, name="t", on_failure=on_failure)
        await _settle()
        await task

    assert told == ["sorry"]
    assert not error_reporting.reporting_enabled()
    assert [r.getMessage() for r in caplog.records] == ["deferred work failed"]


async def test_an_sdk_that_raises_cannot_stop_the_user_being_told(sdk: FakeSentry) -> None:
    sdk.capture_error = RuntimeError("transport down")
    registry = DeferredReplies()
    told: list[str] = []

    async def work() -> None:
        raise ValueError("boom")

    async def on_failure() -> None:
        told.append("sorry")

    task = registry.start(await _reserve(registry), work, name="t", on_failure=on_failure)
    await _settle()
    await task

    assert told == ["sorry"]
    assert registry.running == 0


# -- cancellation and shutdown -------------------------------------------------------------


async def test_a_cancelled_task_says_nothing_to_the_user(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = DeferredReplies()
    told: list[str] = []

    async def work() -> None:
        await asyncio.Event().wait()  # never finishes

    async def on_failure() -> None:
        told.append("sorry")

    with caplog.at_level(logging.INFO, logger="between_jobs.api.deferred_reply"):
        task = registry.start(await _reserve(registry), work, name="t", on_failure=on_failure)
        await _settle()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert told == []
    assert [r.getMessage() for r in caplog.records] == [
        "deferred work cancelled; the user is not messaged"
    ]
    assert caplog.records[0].levelno == logging.INFO  # not an error: shutdown is not a failure


async def test_shutdown_lets_quick_work_finish_within_the_grace_period() -> None:
    registry = DeferredReplies()
    finished: list[str] = []

    async def work() -> None:
        await asyncio.sleep(0.05)
        finished.append("done")

    registry.start(await _reserve(registry), work, name="t")
    await registry.shutdown(grace_seconds=2.0)

    assert finished == ["done"]
    assert registry.running == 0


async def test_shutdown_cancels_what_outlasts_the_grace_period_and_waits_for_it() -> None:
    registry = DeferredReplies()
    told: list[str] = []
    cleaned_up: list[str] = []

    async def work() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cleaned_up.append("finally ran")

    async def on_failure() -> None:
        told.append("sorry")

    registry.start(await _reserve(registry), work, name="t", on_failure=on_failure)
    await _settle()

    await asyncio.wait_for(registry.shutdown(grace_seconds=0.05), timeout=2)

    assert cleaned_up == ["finally ran"]  # cancelled, and awaited until it had unwound
    assert told == []  # and the user is not messaged
    assert registry.running == 0
    assert await registry.try_reserve() is not None  # its place came back


async def test_shutdown_with_nothing_running_returns_at_once_and_can_repeat() -> None:
    registry = DeferredReplies()

    await asyncio.wait_for(registry.shutdown(grace_seconds=30), timeout=1)
    await asyncio.wait_for(registry.shutdown(grace_seconds=30), timeout=1)


def test_the_documented_grace_period_is_five_seconds() -> None:
    from between_jobs.api import deferred_reply

    assert deferred_reply.SHUTDOWN_GRACE_SECONDS == 5.0  # the README promises this number


def test_shutdown_ignores_a_task_left_behind_by_a_loop_that_has_closed() -> None:
    """The process-wide registry outlives any one event loop in a test session: a task its
    loop closed on can neither finish nor be awaited from another loop."""
    registry = DeferredReplies()
    first_loop = asyncio.new_event_loop()
    # The task is abandoned on purpose: its loop's "task destroyed but pending" report, made
    # whenever it is garbage-collected, is the noise this test creates and not a finding.
    first_loop.set_exception_handler(lambda _loop, _context: None)

    async def start_one() -> None:
        async def work() -> None:
            await asyncio.Event().wait()

        registry.start(await _reserve(registry), work, name="t")
        await _settle()

    first_loop.run_until_complete(start_one())
    first_loop.close()
    assert registry.running == 1

    asyncio.run(asyncio.wait_for(registry.shutdown(grace_seconds=0.05), timeout=2))

    assert registry.running == 0


async def test_the_default_grace_period_is_read_when_shutdown_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from between_jobs.api import deferred_reply

    monkeypatch.setattr(deferred_reply, "SHUTDOWN_GRACE_SECONDS", 0.05)
    registry = DeferredReplies()

    async def work() -> None:
        await asyncio.Event().wait()

    registry.start(await _reserve(registry), work, name="t")
    await asyncio.wait_for(registry.shutdown(), timeout=2)

    assert registry.running == 0
