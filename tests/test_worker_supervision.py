"""Worker supervision and /health (launch plan P0.4, P0.5).

Every background worker must survive a tick that raises (log it, back off,
tick again), still stop on cancellation, and report its state to /health,
which answers 503 when an enabled worker has died or gone stale."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from between_jobs.api import (
    gmail_reply_checker,
    hiring_signal_cache,
    job_registry_poller,
    outbox_store,
    saved_search_matcher,
)
from between_jobs.api.app import app
from between_jobs.api.job_registry_adapters import ADAPTERS, DueCompany
from between_jobs.api.worker_supervision import (
    BACKOFF_MAX_SECONDS,
    WorkerRegistry,
    WorkerState,
    backoff_seconds,
    run_supervised,
)


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")


class _StopAfter:
    """A fake `sleep` that records each delay and cancels the loop the
    `stop_after`-th time it is called -- the same way app.py's shutdown does,
    by raising CancelledError out of the await."""

    def __init__(self, stop_after: int) -> None:
        self.delays: list[float] = []
        self._stop_after = stop_after

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        if len(self.delays) >= self._stop_after:
            raise asyncio.CancelledError


# --- the supervisor ----------------------------------------------------------


async def test_a_failed_tick_is_logged_and_the_next_tick_still_runs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    ticks: list[int] = []

    async def tick() -> None:
        ticks.append(len(ticks) + 1)
        if len(ticks) == 1:
            raise RuntimeError("transient database blip")

    state = WorkerState(name="demo", interval_seconds=60)
    sleep = _StopAfter(stop_after=2)
    with pytest.raises(asyncio.CancelledError):
        await run_supervised(tick, state=state, sleep=sleep)

    assert ticks == [1, 2]
    assert sleep.delays == [5.0, 60]  # backoff after the failure, interval after the success
    assert state.consecutive_failures == 0
    assert state.last_error_type == "builtins.RuntimeError"
    assert state.last_success_at is not None
    failures = [r for r in caplog.records if r.name == "between_jobs.api.worker_supervision"]
    assert len(failures) == 1
    assert failures[0].exc_info is not None
    assert failures[0].ctx["worker"] == "demo"  # type: ignore[attr-defined]


async def test_repeated_failures_back_off_exponentially() -> None:
    async def tick() -> None:
        raise RuntimeError("down")

    sleep = _StopAfter(stop_after=4)
    with pytest.raises(asyncio.CancelledError):
        await run_supervised(tick, state=WorkerState("demo", 900), sleep=sleep)
    assert sleep.delays == [5.0, 10.0, 20.0, 40.0]


def test_backoff_is_capped_at_five_minutes_and_starts_no_slower_than_a_fast_interval() -> None:
    assert backoff_seconds(1) == 5.0
    assert backoff_seconds(30) == BACKOFF_MAX_SECONDS
    assert backoff_seconds(1, base=0.01) == 0.01


async def test_cancellation_mid_tick_stops_the_loop() -> None:
    started = asyncio.Event()

    async def tick() -> None:
        started.set()
        await asyncio.sleep(3600)

    task = asyncio.create_task(run_supervised(tick, state=WorkerState("demo", 60)))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_stray_cancellation_from_inside_a_tick_is_a_failed_tick_not_the_end() -> None:
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        ticks += 1
        if ticks == 1:
            raise asyncio.CancelledError  # an inner future someone else cancelled

    state = WorkerState(name="demo", interval_seconds=60)
    sleep = _StopAfter(stop_after=2)
    with pytest.raises(asyncio.CancelledError):
        await run_supervised(tick, state=state, sleep=sleep)

    assert ticks == 2
    assert state.last_error_type == "asyncio.exceptions.CancelledError"
    assert state.last_success_at is not None


async def test_a_fast_workers_retry_never_exceeds_half_its_stale_window() -> None:
    async def tick() -> None:
        raise RuntimeError("down")

    sleep = _StopAfter(stop_after=6)
    with pytest.raises(asyncio.CancelledError):
        await run_supervised(tick, state=WorkerState("outbox", 5), sleep=sleep)
    assert sleep.delays == [5.0, 10.0, 20.0, 30.0, 30.0, 30.0]


async def test_repeated_failures_log_a_traceback_only_on_powers_of_two(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)

    async def tick() -> None:
        raise RuntimeError("down")

    sleep = _StopAfter(stop_after=5)
    with pytest.raises(asyncio.CancelledError):
        await run_supervised(tick, state=WorkerState("demo", 900), sleep=sleep)

    records = [r for r in caplog.records if r.name == "between_jobs.api.worker_supervision"]
    assert [r.levelno for r in records] == [
        logging.ERROR,
        logging.ERROR,
        logging.WARNING,
        logging.ERROR,
        logging.WARNING,
    ]
    assert [r.exc_info is not None for r in records] == [True, True, False, True, False]


# --- every real worker runs under it ------------------------------------------------


def _workers() -> Iterator[tuple[str, str, Callable[..., Awaitable[None]]]]:
    """(module attribute patched, name, a call of the worker's forever loop)."""
    yield (
        "run_worker_once",
        "outbox",
        lambda sleep: outbox_store.run_worker_forever(object(), sleep=sleep),  # type: ignore[arg-type]
    )
    yield (
        "run_poll_tick",
        "job_registry_poller",
        lambda sleep: job_registry_poller.run_poller_forever(object(), object(), sleep=sleep),  # type: ignore[arg-type]
    )
    yield (
        "run_match_tick",
        "saved_search_matcher",
        lambda sleep: saved_search_matcher.run_matcher_forever(object(), sleep=sleep),  # type: ignore[arg-type]
    )
    yield (
        "run_reply_check_once",
        "gmail_reply_checker",
        lambda sleep: gmail_reply_checker.run_reply_check_forever(object(), object(), sleep=sleep),  # type: ignore[arg-type]
    )
    yield (
        "purge_all_expired",
        "hiring_signal_cache_purge",
        lambda sleep: hiring_signal_cache.run_purge_forever(object(), sleep=sleep),  # type: ignore[arg-type]
    )


_INTERVALS = {
    "outbox": 5.0,
    "job_registry_poller": 900.0,
    "saved_search_matcher": 21600.0,
    "gmail_reply_checker": 900.0,
    "hiring_signal_cache_purge": 3600.0,
}

_MODULES = {
    "outbox": outbox_store,
    "job_registry_poller": job_registry_poller,
    "saved_search_matcher": saved_search_matcher,
    "gmail_reply_checker": gmail_reply_checker,
    "hiring_signal_cache_purge": hiring_signal_cache,
}


@pytest.mark.parametrize(("tick_name", "worker", "run"), list(_workers()))
async def test_every_worker_survives_a_failed_tick_and_stops_on_cancel(
    tick_name: str,
    worker: str,
    run: Callable[..., Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    calls: list[int] = []

    async def flaky_tick(*_args: Any, **_kwargs: Any) -> int:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("tick 1 fails")
        return 0

    module = _MODULES[worker]
    monkeypatch.setattr(module, tick_name, flaky_tick)
    if worker == "job_registry_poller":

        async def no_backfill(*_args: Any, **_kwargs: Any) -> int:
            return 0

        monkeypatch.setattr(module, "run_eightfold_jd_backfill", no_backfill)

    sleep = _StopAfter(stop_after=2)
    with pytest.raises(asyncio.CancelledError):
        await run(sleep)

    assert len(calls) == 2  # tick 2 ran after tick 1 raised
    assert sleep.delays == [5.0, _INTERVALS[worker]]  # backoff, then the worker's own interval
    logged = [r for r in caplog.records if r.name == "between_jobs.api.worker_supervision"]
    assert len(logged) == 1
    assert logged[0].ctx["worker"] == worker  # type: ignore[attr-defined]


# --- worker state ----------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def test_worker_status_covers_every_case() -> None:
    now = _now()
    assert WorkerState("w", 60, enabled=False).status(now) == "disabled"
    assert WorkerState("w", 60, started_at=now).status(now) == "starting"
    assert WorkerState("w", 60, started_at=now, last_success_at=now).status(now) == "running"
    old = now - timedelta(seconds=181)
    assert WorkerState("w", 60, started_at=old, last_success_at=old).status(now) == "stale"
    retrying = WorkerState("w", 60, started_at=now, last_success_at=now, consecutive_failures=2)
    assert retrying.status(now) == "retrying"
    assert retrying.healthy(now)
    # every tick failing for longer than min(stale window, 30 min) is failing:
    # the 6-hour matcher is caught in 30 minutes, not 18 hours
    matcher = WorkerState(
        "saved_search_matcher",
        21600,
        started_at=now - timedelta(hours=2),
        last_success_at=now - timedelta(hours=2),
        consecutive_failures=9,
        failing_since=now - timedelta(minutes=31),
    )
    assert matcher.status(now) == "failing"
    assert not matcher.healthy(now)
    # the 60-second floor: a 5-second worker isn't stale after one slow tick
    assert WorkerState("w", 5, last_success_at=now - timedelta(seconds=30)).status(now) == "running"


async def test_a_worker_whose_task_ended_is_dead() -> None:
    async def done() -> None:
        return None

    task = asyncio.create_task(done())
    await task
    assert WorkerState("w", 60, task=task).status() == "dead"


# --- /health ---------------------------------------------------------------------------


async def _finished_task() -> asyncio.Task[None]:
    async def done() -> None:
        return None

    task = asyncio.create_task(done())
    await task
    return task


def _health_with(registry: WorkerRegistry) -> Any:
    with TestClient(app) as client:
        client.app.state.workers = registry  # type: ignore[attr-defined]
        return client.get("/health")


def test_health_is_200_when_every_enabled_worker_runs() -> None:
    registry = WorkerRegistry()
    registry.register("outbox", interval_seconds=5, enabled=True).last_success_at = _now()
    registry.register("gmail_reply_checker", interval_seconds=900, enabled=False)

    response = _health_with(registry)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["workers"]["outbox"]["status"] == "running"
    assert response.json()["workers"]["gmail_reply_checker"]["status"] == "disabled"


def test_health_is_503_when_a_worker_has_died() -> None:
    registry = WorkerRegistry()
    state = registry.register("job_registry_poller", interval_seconds=900, enabled=True)
    state.last_success_at = _now()
    state.task = asyncio.run(_finished_task())

    response = _health_with(registry)

    assert response.status_code == 503
    assert response.json()["status"] == "failing"
    assert response.json()["workers"]["job_registry_poller"]["status"] == "dead"


def test_health_is_503_when_a_worker_is_stale() -> None:
    registry = WorkerRegistry()
    state = registry.register("job_registry_poller", interval_seconds=900, enabled=True)
    state.last_success_at = _now() - timedelta(hours=1)
    state.last_error_type = "httpx.ConnectError"

    response = _health_with(registry)

    assert response.status_code == 503
    worker = response.json()["workers"]["job_registry_poller"]
    assert worker["status"] == "stale"
    assert worker["last_error_type"] == "httpx.ConnectError"


def test_health_is_200_when_a_worker_is_disabled() -> None:
    registry = WorkerRegistry()
    registry.register("saved_search_matcher", interval_seconds=21600, enabled=False)

    response = _health_with(registry)

    assert response.status_code == 200
    assert response.json()["workers"]["saved_search_matcher"]["status"] == "disabled"


def _probe_health(monkeypatch: pytest.MonkeyPatch, answer: Any, **env: str) -> Any:
    monkeypatch.delenv("DISABLE_HEALTH_DEPENDENCY_CHECKS", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with TestClient(app) as client:
        state = client.app.state  # type: ignore[attr-defined]
        original = state.health_http
        state.health_http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
        try:
            return client.get("/health")
        finally:
            # the lifespan closes whatever sits here at shutdown
            mock, state.health_http = state.health_http, original
            asyncio.run(mock.aclose())


def test_health_probes_dependencies_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.host == "forge.test":
            return httpx.Response(200)
        if request.url.host == "latex.test":
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(401)  # Supabase without a key: up

    response = _probe_health(
        monkeypatch,
        answer,
        FORGE_ENGINES_BASE_URL="http://forge.test",
        LATEX_SERVICE_BASE_URL="http://latex.test",
    )

    assert response.status_code == 200  # dependencies are reported, not gating
    assert response.json()["dependencies"] == {
        "supabase": "ok",
        "forge_engines": "ok",
        "latex_service": "unreachable",
    }


def test_health_reports_a_5xx_dependency_and_survives_a_malformed_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _probe_health(
        monkeypatch,
        lambda request: httpx.Response(503),
        FORGE_ENGINES_BASE_URL="http://forge-engines:abc",  # a typo'd port
        LATEX_SERVICE_BASE_URL="http://latex.test",
    )

    assert response.status_code == 200
    deps = response.json()["dependencies"]
    assert deps["forge_engines"] == "unreachable"
    assert deps["latex_service"] == "error"
    assert deps["supabase"] == "error"


def test_health_answers_head_requests() -> None:
    with TestClient(app) as client:
        response = client.head("/health")
    assert response.status_code == 200


# --- per-item containment: a retry never repeats spent work ------------------------------


async def test_one_failing_saved_search_does_not_fail_the_matcher_tick(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)
    matched: list[str] = []

    async def searches(_supabase: Any) -> list[dict[str, Any]]:
        return [{"id": "bad"}, {"id": "good"}]

    async def tier_index(_supabase: Any) -> object:
        return object()

    async def match_one(_supabase: Any, search: dict[str, Any], _tier: Any) -> None:
        if search["id"] == "bad":
            raise RuntimeError("that user's key is out of credit")
        matched.append(search["id"])

    monkeypatch.setattr(saved_search_matcher, "_select_active_saved_searches", searches)
    monkeypatch.setattr(saved_search_matcher, "get_company_tier_index", tier_index)
    monkeypatch.setattr(saved_search_matcher, "_match_one_search", match_one)

    processed = await saved_search_matcher.run_match_tick(object())  # type: ignore[arg-type]

    assert processed == 2
    assert matched == ["good"]
    failures = [r for r in caplog.records if r.name == "between_jobs.api.saved_search_matcher"]
    assert [r.ctx["saved_search_id"] for r in failures] == ["bad"]  # type: ignore[attr-defined]


async def test_one_failing_draft_does_not_fail_the_reply_check_tick(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)
    checked: list[str] = []

    async def drafts(_supabase: Any) -> list[dict[str, Any]]:
        return [{"draft_id": "bad"}, {"draft_id": "good"}]

    async def check_one(
        _http: Any, _supabase: Any, draft: dict[str, Any], *_args: Any, **_kwargs: Any
    ) -> None:
        if draft["draft_id"] == "bad":
            raise RuntimeError("classifier failed")
        checked.append(draft["draft_id"])

    monkeypatch.setattr(gmail_reply_checker, "require_gmail_oauth_config", lambda: object())
    monkeypatch.setattr(gmail_reply_checker, "_select_due_drafts", drafts)
    monkeypatch.setattr(gmail_reply_checker, "_check_one_draft", check_one)

    processed = await gmail_reply_checker.run_reply_check_once(object(), object())  # type: ignore[arg-type]

    assert processed == 2
    assert checked == ["good"]
    failures = [r for r in caplog.records if r.name == "between_jobs.api.gmail_reply_checker"]
    assert [r.ctx["draft_id"] for r in failures] == ["bad"]  # type: ignore[attr-defined]


async def test_an_adapter_that_raises_fails_only_its_own_board(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(_http: Any, _company: Any) -> Any:
        raise KeyError("unexpected payload shape")

    monkeypatch.setitem(ADAPTERS, "brokenats", broken)
    company = DueCompany(
        company_id="c1",
        name="Sample Co",
        ats_type="brokenats",
        slug="sample",
        api_base="",
        board="brokenats:sample:",
        etag="",
    )

    result = await job_registry_poller._fetch_one(object(), company)  # type: ignore[arg-type]

    assert result.status == "failed"


# --- the lifespan wires real workers into the registry ------------------------------------


def test_the_lifespan_starts_an_enabled_worker_and_reports_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    from between_jobs.api import app as app_module

    monkeypatch.delenv("DISABLE_OUTBOX_WORKER")
    ticks: list[int] = []

    async def fake_client() -> tuple[object, str]:
        return object(), "https://example.supabase.co"

    async def fake_once(*_args: Any, **_kwargs: Any) -> int:
        ticks.append(1)
        return 0

    monkeypatch.setattr(app_module, "create_supabase_client", fake_client)
    monkeypatch.setattr(outbox_store, "run_worker_once", fake_once)

    with TestClient(app) as client:
        deadline = time.monotonic() + 5
        while not ticks and time.monotonic() < deadline:
            time.sleep(0.01)
        body = client.get("/health").json()
        task = client.app.state.workers.workers["outbox"].task  # type: ignore[attr-defined]

    assert ticks
    assert body["workers"]["outbox"]["status"] == "running"
    assert body["workers"]["job_registry_poller"]["status"] == "disabled"
    assert task is not None and task.done()  # stopped at shutdown
