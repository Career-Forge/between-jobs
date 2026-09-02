"""Tests for the jobs/applications HTTP endpoints (Sprint 2.6f).

Exercises the real FastAPI routes via TestClient, same shape as
test_profile_routes.py -- this is what proves request parsing, dependency
wiring, and error-code mapping actually work end to end, not just the
underlying store functions in isolation (covered in test_jobs_store.py /
test_applications_store.py).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from between_jobs.api.app import app
from between_jobs.api.app_state import get_supabase
from between_jobs.api.artifact_versions_store import artifact_id_for
from between_jobs.api.auth import require_user_id

_USER_ID = "00000000-0000-0000-0000-000000000001"
_JOB_ID = "20000000-0000-0000-0000-000000000001"
_SNAPSHOT_ID = "20000000-0000-0000-0000-000000000002"
_APPLICATION_ID = "30000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def order(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def limit(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def in_(self, *_: Any, **__: Any) -> _ChainBuilder:
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
        rows = [self.insert_row] if self.insert_row is not None else []
        return _ChainBuilder(rows)


class _FakeRpcBuilder:
    def __init__(self, data: Any, error: APIError | None) -> None:
        self._data = data
        self._error = error

    async def execute(self) -> SimpleNamespace:
        if self._error:
            raise self._error
        return SimpleNamespace(data=self._data)


class _FakeSupabaseClient:
    def __init__(
        self,
        *,
        jobs: _FakeTable | None = None,
        job_snapshots: _FakeTable | None = None,
        applications: _FakeTable | None = None,
        application_events: _FakeTable | None = None,
        artifact_versions: _FakeTable | None = None,
        event_outbox: _FakeTable | None = None,
        rpc_data: Any = None,
        rpc_error: APIError | None = None,
    ) -> None:
        self.jobs = jobs or _FakeTable(select_rows=[])
        self.job_snapshots = job_snapshots or _FakeTable(select_rows=[])
        self.applications = applications or _FakeTable(select_rows=[])
        self.application_events = application_events or _FakeTable(select_rows=[])
        self.artifact_versions = artifact_versions or _FakeTable(select_rows=[])
        self.event_outbox = event_outbox or _FakeTable(select_rows=[])
        self.rpc_data = rpc_data
        self.rpc_error = rpc_error

    def table(self, name: str) -> Any:
        return {
            "jobs": self.jobs,
            "job_snapshots": self.job_snapshots,
            "applications": self.applications,
            "application_events": self.application_events,
            "artifact_versions": self.artifact_versions,
            "event_outbox": self.event_outbox,
        }[name]

    def rpc(self, fn: str, params: dict[str, Any]) -> _FakeRpcBuilder:
        return _FakeRpcBuilder(self.rpc_data, self.rpc_error)


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")


def _client(supabase: _FakeSupabaseClient) -> TestClient:
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Any:
    yield
    app.dependency_overrides.clear()


def _paste_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "title": "Staff Engineer",
        "company_name": "Acme",
        "description_text": "Build things.",
        "canonical_url": "https://acme.example/jobs/1",
        "location_text": "Remote",
    }
    body.update(overrides)
    return body


def test_create_application_from_paste_success() -> None:
    new_job = {"id": _JOB_ID, "canonical_url": "https://acme.example/jobs/1"}
    new_snapshot = {"id": _SNAPSHOT_ID, "job_id": _JOB_ID, "title": "Staff Engineer"}
    new_app = {"id": _APPLICATION_ID, "user_id": _USER_ID, "status": "saved"}
    supabase = _FakeSupabaseClient(
        jobs=_FakeTable(select_rows=[], insert_row=new_job),
        job_snapshots=_FakeTable(select_rows=[], insert_row=new_snapshot),
        applications=_FakeTable(select_rows=[], insert_row=new_app),
        application_events=_FakeTable(
            select_rows=[], insert_row={"id": "event-1", "event_type": "application.created"}
        ),
    )
    with _client(supabase) as client:
        response = client.post("/applications", json=_paste_body())

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == _APPLICATION_ID
    assert body["snapshot"]["id"] == _SNAPSHOT_ID


def test_create_application_from_paste_missing_field_returns_structured_422() -> None:
    supabase = _FakeSupabaseClient()
    with _client(supabase) as client:
        response = client.post("/applications", json=_paste_body(company_name=""))

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "INVALID_INPUT"
    assert body["details"]["errors"]


def test_list_my_applications_embeds_snapshot() -> None:
    app_row = {"id": _APPLICATION_ID, "active_job_snapshot_id": _SNAPSHOT_ID}
    snapshot_row = {"id": _SNAPSHOT_ID, "title": "Staff Engineer"}
    supabase = _FakeSupabaseClient(
        applications=_FakeTable(select_rows=[app_row]),
        job_snapshots=_FakeTable(select_rows=[snapshot_row]),
    )
    with _client(supabase) as client:
        response = client.get("/applications")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["snapshot"]["title"] == "Staff Engineer"


def test_list_my_applications_includes_resume_exists_true_when_generated() -> None:
    """K1 (applications-kanban.md D5) -- the real "Resume ✓" badge, batch-
    computed for the whole list."""
    app_row = {"id": _APPLICATION_ID, "active_job_snapshot_id": _SNAPSHOT_ID}
    snapshot_row = {"id": _SNAPSHOT_ID, "title": "Staff Engineer"}
    resume_artifact_id = artifact_id_for(_APPLICATION_ID, "resume")
    supabase = _FakeSupabaseClient(
        applications=_FakeTable(select_rows=[app_row]),
        job_snapshots=_FakeTable(select_rows=[snapshot_row]),
        artifact_versions=_FakeTable(select_rows=[{"artifact_id": resume_artifact_id}]),
    )
    with _client(supabase) as client:
        response = client.get("/applications")

    assert response.json()[0]["resume_exists"] is True


def test_list_my_applications_includes_resume_exists_false_when_none_generated() -> None:
    app_row = {"id": _APPLICATION_ID, "active_job_snapshot_id": _SNAPSHOT_ID}
    snapshot_row = {"id": _SNAPSHOT_ID, "title": "Staff Engineer"}
    supabase = _FakeSupabaseClient(
        applications=_FakeTable(select_rows=[app_row]),
        job_snapshots=_FakeTable(select_rows=[snapshot_row]),
        artifact_versions=_FakeTable(select_rows=[]),
    )
    with _client(supabase) as client:
        response = client.get("/applications")

    assert response.json()[0]["resume_exists"] is False


def test_list_my_applications_empty() -> None:
    supabase = _FakeSupabaseClient(applications=_FakeTable(select_rows=[]))
    with _client(supabase) as client:
        response = client.get("/applications")

    assert response.status_code == 200
    assert response.json() == []


def test_get_my_application_embeds_snapshot() -> None:
    app_row = {"id": _APPLICATION_ID, "active_job_snapshot_id": _SNAPSHOT_ID}
    snapshot_row = {"id": _SNAPSHOT_ID, "title": "Staff Engineer"}
    supabase = _FakeSupabaseClient(
        applications=_FakeTable(select_rows=[app_row]),
        job_snapshots=_FakeTable(select_rows=[snapshot_row]),
    )
    with _client(supabase) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == _APPLICATION_ID
    assert body["snapshot"]["title"] == "Staff Engineer"
    assert body["resume_exists"] is False


def test_get_my_application_reports_resume_exists_when_an_artifact_was_generated() -> None:
    app_row = {"id": _APPLICATION_ID, "active_job_snapshot_id": _SNAPSHOT_ID}
    snapshot_row = {"id": _SNAPSHOT_ID, "title": "Staff Engineer"}
    supabase = _FakeSupabaseClient(
        applications=_FakeTable(select_rows=[app_row]),
        job_snapshots=_FakeTable(select_rows=[snapshot_row]),
        artifact_versions=_FakeTable(select_rows=[{"id": "version-row-1", "version": 1}]),
    )
    with _client(supabase) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}")

    assert response.json()["resume_exists"] is True


def test_get_my_application_not_found_returns_404() -> None:
    supabase = _FakeSupabaseClient(applications=_FakeTable(select_rows=[]))
    with _client(supabase) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_change_application_stage_success() -> None:
    updated = {"id": _APPLICATION_ID, "status": "applied"}
    supabase = _FakeSupabaseClient(rpc_data=updated)
    with _client(supabase) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/stage",
            json={"new_status": "applied", "idempotency_key": "change-1"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "applied"


def test_change_application_stage_invalid_status_returns_422() -> None:
    """K1 (applications-kanban.md D1/D2) -- `new_status` is now a Literal
    of the 7 enforced values; an unrecognized string 422s via FastAPI's
    own Pydantic validation before the route body ever runs."""
    supabase = _FakeSupabaseClient()
    with _client(supabase) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/stage",
            json={"new_status": "bogus", "idempotency_key": "change-1"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_change_application_stage_not_found_returns_404() -> None:
    error = APIError({"message": "application x not found for user y", "code": "P0001"})
    supabase = _FakeSupabaseClient(rpc_error=error)
    with _client(supabase) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/stage",
            json={"new_status": "applied", "idempotency_key": "change-1"},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
