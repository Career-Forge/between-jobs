"""`/healthz`: the path a hosting platform probes while the instance runs.

Strict until the instance has once been fully healthy (so a deploy whose lease path is
broken is refused, exactly as `/health` would), then 503 only for a worker that has died
(the one failure a restart cures). `/health` stays the strict view throughout. The route
reads `app.state` only, so these tests set that by hand and do not boot the lifespan.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from between_jobs.api.app import app
from between_jobs.api.worker_supervision import WorkerRegistry, WorkerState


def _registry() -> tuple[WorkerRegistry, WorkerState, WorkerState]:
    registry = WorkerRegistry()
    outbox = registry.register("outbox", interval_seconds=60, enabled=True)
    matcher = registry.register("saved_search_matcher", interval_seconds=60, enabled=True)
    now = datetime.now(UTC)
    for state in (outbox, matcher):
        state.started_at = now
        state.last_success_at = now
    return registry, outbox, matcher


def _lease(state: str, *, crashed: bool = False) -> Any:
    return SimpleNamespace(crashed=crashed, status=lambda: state, report=lambda: {"state": state})


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)  # no `with`: the lifespan does not run


def _install(registry: WorkerRegistry, *, ready: bool = False) -> None:
    app.state.workers = registry
    app.state.platform_ready = ready


def test_healthy_workers_pass_and_the_instance_is_marked_ready(client: TestClient) -> None:
    registry, _, _ = _registry()
    _install(registry)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "phase": "serving"}
    assert app.state.platform_ready is True


def test_head_is_answered(client: TestClient) -> None:
    registry, _, _ = _registry()
    _install(registry)

    assert client.head("/healthz").status_code == 200


def test_a_lease_that_cannot_be_asked_refuses_a_starting_instance(client: TestClient) -> None:
    """The deploy gate: same answer as /health, and the instance is not marked ready."""
    registry, _, matcher = _registry()
    matcher.lease = _lease("unknown")
    _install(registry)

    response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json() == {"status": "failing", "phase": "starting"}
    assert app.state.platform_ready is False
    assert client.get("/health").status_code == 503


def test_a_failing_worker_refuses_a_starting_instance_too(client: TestClient) -> None:
    registry, _, matcher = _registry()
    matcher.failing_since = datetime.now(UTC) - timedelta(hours=2)
    matcher.consecutive_failures = 3
    _install(registry)

    assert client.get("/healthz").status_code == 503


def test_once_ready_a_worker_that_is_failing_does_not_fail_the_probe(client: TestClient) -> None:
    """A worker failing for 30 minutes, or a database blip over a minute, is not cured by
    a restart and must not take the API offline; /health still says so."""
    registry, _, matcher = _registry()
    _install(registry)
    assert client.get("/healthz").status_code == 200  # ready

    matcher.failing_since = datetime.now(UTC) - timedelta(hours=2)
    matcher.consecutive_failures = 3

    assert matcher.status() == "failing"
    assert client.get("/health").status_code == 503
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "phase": "serving"}


def test_once_ready_a_stale_worker_or_an_unaskable_lease_does_not_fail_the_probe(
    client: TestClient,
) -> None:
    registry, outbox, matcher = _registry()
    _install(registry)
    assert client.get("/healthz").status_code == 200

    outbox.started_at = outbox.last_success_at = datetime.now(UTC) - timedelta(hours=2)
    matcher.lease = _lease("unknown")

    assert outbox.status() == "stale"
    assert matcher.status() == "lease_unknown"
    assert client.get("/health").status_code == 503
    assert client.get("/healthz").status_code == 200


def test_once_ready_a_dead_worker_fails_the_probe(client: TestClient) -> None:
    registry, outbox, _ = _registry()
    _install(registry)
    assert client.get("/healthz").status_code == 200

    outbox.task = cast(Any, SimpleNamespace(done=lambda: True))

    assert outbox.status() == "dead"
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "failing", "phase": "serving"}


def test_a_crashed_lease_keeper_counts_as_a_dead_worker(client: TestClient) -> None:
    registry, _, matcher = _registry()
    _install(registry)
    assert client.get("/healthz").status_code == 200

    matcher.lease = _lease("held", crashed=True)

    assert client.get("/healthz").status_code == 503


def test_once_ready_a_standby_or_disabled_worker_is_not_dead(client: TestClient) -> None:
    """A new instance stands by for about 90 s of every deploy overlap; reading that as dead
    would make Render restart it."""
    registry, outbox, matcher = _registry()
    _install(registry)
    assert client.get("/healthz").status_code == 200

    matcher.lease = _lease("standby")
    outbox.enabled = False

    assert matcher.status() == "standby"
    assert outbox.status() == "disabled"
    assert client.get("/healthz").status_code == 200


def test_a_disabled_worker_is_not_a_failure(client: TestClient) -> None:
    registry, outbox, _ = _registry()
    outbox.enabled = False
    _install(registry)

    assert client.get("/healthz").status_code == 200


@pytest.mark.parametrize("ready", [False, True], ids=["starting", "serving"])
@pytest.mark.parametrize("failing", [False, True], ids=["200", "503"])
def test_the_body_carries_no_worker_detail(client: TestClient, ready: bool, failing: bool) -> None:
    registry, outbox, _ = _registry()
    if failing:
        outbox.task = cast(Any, SimpleNamespace(done=lambda: True))  # dead in both phases
    _install(registry, ready=ready)

    response = client.get("/healthz")

    assert response.status_code == (503 if failing else 200)
    assert set(response.json()) == {"status", "phase"}
