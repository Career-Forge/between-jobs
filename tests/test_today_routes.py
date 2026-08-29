"""Tests for the Today-feed HTTP endpoints (Horizon Sprint 4.0)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from between_jobs.api.app import app
from between_jobs.api.app_state import get_supabase
from between_jobs.api.auth import require_user_id

_USER_ID = "00000000-0000-0000-0000-000000000001"
_ITEM_ID = "60000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def is_(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def order(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTable:
    def __init__(
        self, *, select_rows: list[dict[str, Any]], update_row: dict[str, Any] | None = None
    ) -> None:
        self.select_rows = select_rows
        self.update_row = update_row

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def update(self, _data: dict[str, Any]) -> _ChainBuilder:
        rows = [self.update_row] if self.update_row is not None else []
        return _ChainBuilder(rows)


class _FakeSupabaseClient:
    def __init__(self, today_items: _FakeTable) -> None:
        self.today_items = today_items

    def table(self, name: str) -> Any:
        if name == "today_items":
            return self.today_items
        raise AssertionError(f"unexpected table: {name}")


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


def test_list_my_today_items_returns_undismissed_rows() -> None:
    rows = [{"id": _ITEM_ID, "headline": "🆕 Tracking Staff Engineer @ Acme"}]
    supabase = _FakeSupabaseClient(_FakeTable(select_rows=rows))

    with _client(supabase) as client:
        response = client.get("/today")

    assert response.status_code == 200
    assert response.json() == rows


def test_dismiss_my_today_item_success() -> None:
    updated = {"id": _ITEM_ID, "dismissed_at": "2026-08-21T00:00:00Z"}
    supabase = _FakeSupabaseClient(_FakeTable(select_rows=[], update_row=updated))

    with _client(supabase) as client:
        response = client.post(f"/today/{_ITEM_ID}/dismiss")

    assert response.status_code == 200
    assert response.json() == updated


def test_dismiss_my_today_item_not_found_returns_404() -> None:
    supabase = _FakeSupabaseClient(_FakeTable(select_rows=[], update_row=None))

    with _client(supabase) as client:
        response = client.post(f"/today/{_ITEM_ID}/dismiss")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
