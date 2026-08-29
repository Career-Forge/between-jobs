"""Tests for the discovery HTTP endpoints (Horizon Sprint 4.1).

Exercises the real FastAPI routes via TestClient. The n8n pool dependency
is overridden with a fake -- same convention as every other outbound
integration in this codebase (forge-engines/latex-service's httpx
clients) -- no real Postgres connection in a unit test.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from between_jobs.api.app import app
from between_jobs.api.app_state import get_n8n_pool, get_supabase
from between_jobs.api.auth import require_user_id

_USER_ID = "00000000-0000-0000-0000-000000000001"

_JOB_ROW = {
    "id": 1,
    "title": "Staff AI Engineer",
    "company_name": "Acme",
    "location": "Remote",
    "remote": True,
    "apply_url": "https://acme.example/jobs/1",
    "posted_at": "2026-08-20T00:00:00Z",
}
_JOB_DETAIL_ROW = {**_JOB_ROW, "jd_text": "We need a Python engineer with RAG experience."}


class _FakeTransaction:
    async def __aenter__(self) -> _FakeTransaction:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _FakeConnection:
    def __init__(
        self, *, fetch_rows: list[dict[str, Any]], fetchrow_row: dict[str, Any] | None
    ) -> None:
        self._fetch_rows = fetch_rows
        self._fetchrow_row = fetchrow_row

    def transaction(self, *, readonly: bool = False) -> _FakeTransaction:
        return _FakeTransaction()

    async def fetch(self, _query: str, *_args: Any) -> list[dict[str, Any]]:
        return self._fetch_rows

    async def fetchrow(self, _query: str, *_args: Any) -> dict[str, Any] | None:
        return self._fetchrow_row


class _FakeAcquire:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConnection:
        return self._conn

    async def __aexit__(self, *_exc: object) -> None:
        return None


_UNSET = object()


class _FakePool:
    def __init__(
        self,
        *,
        fetch_rows: list[dict[str, Any]] | None = None,
        fetchrow_row: Any = _UNSET,
    ) -> None:
        self._conn = _FakeConnection(
            fetch_rows=fetch_rows if fetch_rows is not None else [_JOB_ROW],
            fetchrow_row=_JOB_DETAIL_ROW if fetchrow_row is _UNSET else fetchrow_row,
        )

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def limit(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTable:
    def __init__(
        self, *, select_rows: list[dict[str, Any]], insert_row: dict[str, Any] | None = None
    ) -> None:
        self.select_rows = select_rows
        self.insert_row = insert_row
        self.insert_calls: list[dict[str, Any]] = []

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def insert(self, data: dict[str, Any]) -> _ChainBuilder:
        self.insert_calls.append(data)
        rows = [self.insert_row] if self.insert_row is not None else [{**data, "id": "row-1"}]
        return _ChainBuilder(rows)


class _FakeSupabaseClient:
    def __init__(
        self,
        *,
        jobs: _FakeTable | None = None,
        job_snapshots: _FakeTable | None = None,
        applications: _FakeTable | None = None,
        application_events: _FakeTable | None = None,
        event_outbox: _FakeTable | None = None,
    ) -> None:
        self.jobs = jobs or _FakeTable(select_rows=[])
        self.job_snapshots = job_snapshots or _FakeTable(select_rows=[])
        self.applications = applications or _FakeTable(select_rows=[])
        self.application_events = application_events or _FakeTable(
            select_rows=[], insert_row={"id": "event-1"}
        )
        self.event_outbox = event_outbox or _FakeTable(select_rows=[])

    def table(self, name: str) -> Any:
        return {
            "jobs": self.jobs,
            "job_snapshots": self.job_snapshots,
            "applications": self.applications,
            "application_events": self.application_events,
            "event_outbox": self.event_outbox,
        }[name]


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")


@pytest.fixture(autouse=True)
def _clear_overrides() -> Any:
    yield
    app.dependency_overrides.clear()


def test_search_discover_returns_results() -> None:
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    app.dependency_overrides[get_n8n_pool] = lambda: _FakePool()

    with TestClient(app) as client:
        response = client.get("/discover", params={"q": "python"})

    assert response.status_code == 200
    assert response.json() == [_JOB_ROW]


def test_search_discover_without_a_pool_configured_returns_setup_required() -> None:
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    # No get_n8n_pool override -- exercises the real dependency, which
    # reads request.app.state.n8n_pool. The real app never set
    # N8N_JOBS_DATABASE_URL in this test process, so lifespan leaves it
    # None -- proving the honest SETUP_REQUIRED path, not a fake standing
    # in for "not configured."
    with TestClient(app) as client:
        response = client.get("/discover")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SETUP_REQUIRED"


def test_track_discovered_job_creates_application() -> None:
    supabase = _FakeSupabaseClient()
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    app.dependency_overrides[get_n8n_pool] = lambda: _FakePool()
    app.dependency_overrides[get_supabase] = lambda: supabase

    with TestClient(app) as client:
        response = client.post("/discover/1/track")

    assert response.status_code == 201
    body = response.json()
    assert body["snapshot"]["title"] == "Staff AI Engineer"
    assert len(supabase.job_snapshots.insert_calls) == 1
    assert supabase.job_snapshots.insert_calls[0]["description_text"] == _JOB_DETAIL_ROW["jd_text"]
    assert len(supabase.applications.insert_calls) == 1
    assert supabase.applications.insert_calls[0]["source_channel"] == "discover"


def test_track_discovered_job_not_found_returns_404() -> None:
    supabase = _FakeSupabaseClient()
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    app.dependency_overrides[get_n8n_pool] = lambda: _FakePool(fetchrow_row=None)
    app.dependency_overrides[get_supabase] = lambda: supabase

    with TestClient(app) as client:
        response = client.post("/discover/999/track")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert supabase.jobs.insert_calls == []
