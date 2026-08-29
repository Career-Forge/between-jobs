"""Tests for Today-feed persistence (Horizon Sprint 4.0)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from between_jobs.api.today_store import TodayItemNotFound, dismiss_today_item, list_today_items

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
        self.update_calls: list[dict[str, Any]] = []

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def update(self, data: dict[str, Any]) -> _ChainBuilder:
        self.update_calls.append(data)
        rows = [self.update_row] if self.update_row is not None else []
        return _ChainBuilder(rows)


class _FakeSupabaseClient:
    def __init__(self, today_items: _FakeTable) -> None:
        self.today_items = today_items

    def table(self, name: str) -> Any:
        if name == "today_items":
            return self.today_items
        raise AssertionError(f"unexpected table: {name}")


async def test_list_today_items_returns_the_rows() -> None:
    rows = [{"id": _ITEM_ID, "headline": "🆕 Tracking Staff Engineer @ Acme"}]
    client = _FakeSupabaseClient(_FakeTable(select_rows=rows))

    result = await list_today_items(client, _USER_ID)  # type: ignore[arg-type]

    assert result == rows


async def test_dismiss_today_item_sets_dismissed_at() -> None:
    updated = {"id": _ITEM_ID, "dismissed_at": "2026-08-21T00:00:00Z"}
    table = _FakeTable(select_rows=[], update_row=updated)
    client = _FakeSupabaseClient(table)

    result = await dismiss_today_item(client, _USER_ID, _ITEM_ID)  # type: ignore[arg-type]

    assert result == updated
    assert "dismissed_at" in table.update_calls[0]


async def test_dismiss_today_item_not_found_raises() -> None:
    table = _FakeTable(select_rows=[], update_row=None)
    client = _FakeSupabaseClient(table)

    with pytest.raises(TodayItemNotFound):
        await dismiss_today_item(client, _USER_ID, _ITEM_ID)  # type: ignore[arg-type]
