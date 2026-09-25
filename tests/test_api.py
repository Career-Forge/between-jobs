"""Tests for the FastAPI spine skeleton (Sprint 2.1/2.2).

The endpoint's own logic is tested here via fake Supabase/auth dependencies
swapped in through FastAPI's dependency_overrides -- no live credentials or
network access needed. Real, non-mocked JWT verification (the part that
actually matters to get right) is tested separately in test_auth.py.

The schema itself (RLS behavior, the foreign-key constraint this test's
404 case exercises) was verified separately, live, against the real
Supabase project before any application code was written -- see the
migration commit message.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from between_jobs.api.app import app
from between_jobs.api.app_state import get_supabase
from between_jobs.api.auth import require_user_id

_TEST_USER_ID = "00000000-0000-0000-0000-000000000001"


class _FakeInsertBuilder:
    def __init__(self, response_data: list[dict[str, Any]] | None, error: APIError | None) -> None:
        self._response_data = response_data
        self._error = error

    async def execute(self) -> SimpleNamespace:
        if self._error:
            raise self._error
        return SimpleNamespace(data=self._response_data)


class _FakeTable:
    def __init__(self, response_data: list[dict[str, Any]] | None, error: APIError | None) -> None:
        self._response_data = response_data
        self._error = error

    def insert(self, data: dict[str, Any]) -> _FakeInsertBuilder:
        return _FakeInsertBuilder(self._response_data, self._error)


class _FakeSupabaseClient:
    def __init__(
        self, response_data: list[dict[str, Any]] | None = None, error: APIError | None = None
    ) -> None:
        self._response_data = response_data
        self._error = error

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(self._response_data, self._error)


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Lifespan runs under TestClient regardless of dependency_overrides --
    # these just need to be non-empty so lifespan doesn't raise; none of
    # the clients/secrets built from them are actually used in these
    # tests (the ones that matter get overridden below).
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    # tests/conftest.py switches every worker and the dependency probes off.
    assert set(body["workers"]) == {
        "outbox",
        "job_registry_poller",
        "saved_search_matcher",
        "gmail_reply_checker",
        "hiring_signal_cache_purge",
    }
    assert {w["status"] for w in body["workers"].values()} == {"disabled"}
    assert set(body["dependencies"].values()) == {"not_checked"}


def test_create_session_without_auth_header_returns_401() -> None:
    # No dependency override for require_user_id here -- this exercises
    # the real header-parsing path, proving the endpoint is actually
    # protected rather than just trusting an override to exist.
    with TestClient(app) as client:
        response = client.post("/sessions", json={"context": {}})
    assert response.status_code == 401


def test_create_session_success() -> None:
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "user_id": _TEST_USER_ID,
        "context": {"hello": "world"},
        "created_at": "2026-07-29T00:00:00Z",
        "updated_at": "2026-07-29T00:00:00Z",
    }
    fake_client = _FakeSupabaseClient(response_data=[row])
    app.dependency_overrides[get_supabase] = lambda: fake_client
    app.dependency_overrides[require_user_id] = lambda: _TEST_USER_ID
    try:
        with TestClient(app) as client:
            response = client.post("/sessions", json={"context": {"hello": "world"}})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json() == row


def test_create_session_unknown_user_returns_404() -> None:
    error = APIError(
        {
            "message": 'insert or update on table "sessions" violates foreign key constraint',
            "code": "23503",
            "hint": None,
            "details": None,
        }
    )
    fake_client = _FakeSupabaseClient(error=error)
    app.dependency_overrides[get_supabase] = lambda: fake_client
    app.dependency_overrides[require_user_id] = lambda: _TEST_USER_ID
    try:
        with TestClient(app) as client:
            response = client.post("/sessions", json={"context": {}})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    body = response.json()["error"]
    assert body["code"] == "NOT_FOUND"
    assert _TEST_USER_ID in body["message"]


def test_create_session_other_db_error_returns_500() -> None:
    error = APIError(
        {"message": "something else broke", "code": "42P01", "hint": None, "details": None}
    )
    fake_client = _FakeSupabaseClient(error=error)
    app.dependency_overrides[get_supabase] = lambda: fake_client
    app.dependency_overrides[require_user_id] = lambda: _TEST_USER_ID
    try:
        with TestClient(app) as client:
            response = client.post("/sessions", json={"context": {}})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    body = response.json()["error"]
    assert body["code"] == "INTERNAL_ERROR"
    # Appendix B: never leak the raw database error string to the client.
    assert "something else broke" not in body["message"]
    assert "42P01" not in body["message"]
