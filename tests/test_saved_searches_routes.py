"""Tests for the saved-searches HTTP endpoints (Job Finder P9a, today-
feed-job-matching.md). Exercises the real FastAPI routes via TestClient,
same convention as test_credentials_routes.py."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from between_jobs.api.app import app
from between_jobs.api.app_state import get_supabase
from between_jobs.api.auth import require_user_id

_USER_ID = "00000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, column: str, value: Any) -> _ChainBuilder:
        return _ChainBuilder([r for r in self._rows if r.get(column) == value])

    def order(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _UpdateBuilder:
    def __init__(self, table: _FakeTable, data: dict[str, Any]) -> None:
        self._table = table
        self._data = data
        self._filters: dict[str, Any] = {}

    def eq(self, column: str, value: Any) -> _UpdateBuilder:
        self._filters[column] = value
        return self

    async def execute(self) -> SimpleNamespace:
        matched = [
            r for r in self._table.rows if all(r.get(k) == v for k, v in self._filters.items())
        ]
        for row in matched:
            row.update(self._data)
        return SimpleNamespace(data=matched)


class _DeleteBuilder:
    def __init__(self, table: _FakeTable) -> None:
        self._table = table
        self._filters: dict[str, Any] = {}

    def eq(self, column: str, value: Any) -> _DeleteBuilder:
        self._filters[column] = value
        return self

    async def execute(self) -> SimpleNamespace:
        matched = [
            r for r in self._table.rows if all(r.get(k) == v for k, v in self._filters.items())
        ]
        for row in matched:
            self._table.rows.remove(row)
        return SimpleNamespace(data=matched)


class _FakeTable:
    def __init__(self, *, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows if rows is not None else []
        self._next_id = 1

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.rows)

    def insert(self, data: dict[str, Any]) -> _ChainBuilder:
        row = {"id": f"row-{self._next_id}", **data}
        self._next_id += 1
        self.rows.append(row)
        return _ChainBuilder([row])

    def update(self, data: dict[str, Any]) -> _UpdateBuilder:
        return _UpdateBuilder(self, data)

    def delete(self) -> _DeleteBuilder:
        return _DeleteBuilder(self)


class _FakeSupabase:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.saved_searches = _FakeTable(rows=rows)

    def table(self, name: str) -> _FakeTable:
        assert name == "saved_searches"
        return self.saved_searches


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


def _client(supabase: _FakeSupabase) -> TestClient:
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    return TestClient(app)


def test_create_saved_search_returns_201_with_the_new_row() -> None:
    supabase = _FakeSupabase()
    client = _client(supabase)

    response = client.post(
        "/saved-searches",
        json={
            "query": "backend engineer",
            "location": "New York, NY",
            "companies": ["Anthropic"],
            "remote_only": False,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["query"] == "backend engineer"
    assert body["companies"] == ["Anthropic"]


def test_create_saved_search_defaults_are_applied() -> None:
    supabase = _FakeSupabase()
    client = _client(supabase)

    response = client.post("/saved-searches", json={})

    assert response.status_code == 201
    body = response.json()
    assert body["query"] == ""
    assert body["companies"] == []
    assert body["remote_only"] is False


def test_list_saved_searches_returns_only_mine() -> None:
    supabase = _FakeSupabase(
        rows=[
            {"id": "s1", "user_id": _USER_ID, "query": "a"},
            {"id": "s2", "user_id": "someone-else", "query": "b"},
        ]
    )
    client = _client(supabase)

    response = client.get("/saved-searches")

    assert response.status_code == 200
    assert [r["id"] for r in response.json()] == ["s1"]


def test_toggle_saved_search_active_state() -> None:
    supabase = _FakeSupabase(rows=[{"id": "s1", "user_id": _USER_ID, "is_active": True}])
    client = _client(supabase)

    response = client.patch("/saved-searches/s1", json={"is_active": False})

    assert response.status_code == 200
    assert response.json()["is_active"] is False


def test_toggle_saved_search_active_state_404s_when_missing() -> None:
    supabase = _FakeSupabase(rows=[])
    client = _client(supabase)

    response = client.patch("/saved-searches/nope", json={"is_active": False})

    assert response.status_code == 404


def test_delete_saved_search_returns_204() -> None:
    supabase = _FakeSupabase(rows=[{"id": "s1", "user_id": _USER_ID}])
    client = _client(supabase)

    response = client.delete("/saved-searches/s1")

    assert response.status_code == 204
    assert supabase.saved_searches.rows == []


def test_delete_saved_search_404s_when_missing() -> None:
    supabase = _FakeSupabase(rows=[])
    client = _client(supabase)

    response = client.delete("/saved-searches/nope")

    assert response.status_code == 404
