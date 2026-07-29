"""Tests for the FastAPI spine skeleton (Sprint 2.1).

The endpoint's own logic is tested here via a fake Supabase client swapped
in through FastAPI's dependency_overrides -- no live credentials or network
access needed. The schema itself (RLS behavior, the foreign-key constraint
this test's 404 case exercises) was verified separately, live, against the
real Supabase project before this code was written -- see the migration
commit message.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from between_jobs.api.app import app, get_supabase


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
    # these just need to be non-empty so create_supabase_client() doesn't
    # raise; the client it builds is never actually queried in these tests.
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_session_success() -> None:
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "user_id": "00000000-0000-0000-0000-000000000001",
        "context": {"hello": "world"},
        "created_at": "2026-07-29T00:00:00Z",
        "updated_at": "2026-07-29T00:00:00Z",
    }
    fake_client = _FakeSupabaseClient(response_data=[row])
    app.dependency_overrides[get_supabase] = lambda: fake_client
    try:
        with TestClient(app) as client:
            response = client.post(
                "/sessions",
                json={
                    "user_id": "00000000-0000-0000-0000-000000000001",
                    "context": {"hello": "world"},
                },
            )
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
    try:
        with TestClient(app) as client:
            response = client.post(
                "/sessions",
                json={"user_id": "00000000-0000-0000-0000-000000000099", "context": {}},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert "00000000-0000-0000-0000-000000000099" in response.json()["detail"]


def test_create_session_other_db_error_returns_500() -> None:
    error = APIError(
        {"message": "something else broke", "code": "42P01", "hint": None, "details": None}
    )
    fake_client = _FakeSupabaseClient(error=error)
    app.dependency_overrides[get_supabase] = lambda: fake_client
    try:
        with TestClient(app) as client:
            response = client.post(
                "/sessions",
                json={"user_id": "00000000-0000-0000-0000-000000000001", "context": {}},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
