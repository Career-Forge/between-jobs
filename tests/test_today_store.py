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

    def in_(self, column: str, values: list[Any]) -> _ChainBuilder:
        return _ChainBuilder([row for row in self._rows if row[column] in values])

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTable:
    def __init__(
        self, *, select_rows: list[dict[str, Any]], update_row: dict[str, Any] | None = None
    ) -> None:
        self.select_rows = select_rows
        self.update_row = update_row
        self.update_calls: list[dict[str, Any]] = []
        self.select_calls = 0

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        self.select_calls += 1
        return _ChainBuilder(self.select_rows)

    def update(self, data: dict[str, Any]) -> _ChainBuilder:
        self.update_calls.append(data)
        rows = [self.update_row] if self.update_row is not None else []
        return _ChainBuilder(rows)


class _FakeSupabaseClient:
    def __init__(self, today_items: _FakeTable, *, job_matches: _FakeTable | None = None) -> None:
        self.today_items = today_items
        self.job_matches = job_matches or _FakeTable(select_rows=[])

    def table(self, name: str) -> Any:
        if name == "today_items":
            return self.today_items
        if name == "today_item_job_matches":
            return self.job_matches
        raise AssertionError(f"unexpected table: {name}")


async def test_list_today_items_returns_the_rows() -> None:
    rows = [
        {"id": _ITEM_ID, "kind": "job_tracked", "headline": "🆕 Tracking Staff Engineer @ Acme"}
    ]
    client = _FakeSupabaseClient(_FakeTable(select_rows=rows))

    result = await list_today_items(client, _USER_ID)  # type: ignore[arg-type]

    assert result == [{**rows[0], "job_match": None}]


async def test_list_today_items_embeds_the_job_match_for_high_fit_job_items() -> None:
    rows = [{"id": _ITEM_ID, "kind": "high_fit_job", "headline": "🎯 High-fit match"}]
    match = {"today_item_id": _ITEM_ID, "apply_url": "https://x.example/1", "score100": 78}
    client = _FakeSupabaseClient(
        _FakeTable(select_rows=rows), job_matches=_FakeTable(select_rows=[match])
    )

    result = await list_today_items(client, _USER_ID)  # type: ignore[arg-type]

    assert result == [{**rows[0], "job_match": match}]


async def test_list_today_items_skips_the_batch_query_when_no_job_match_items() -> None:
    rows = [
        {"id": _ITEM_ID, "kind": "job_tracked", "headline": "🆕 Tracking Staff Engineer @ Acme"}
    ]
    job_matches = _FakeTable(select_rows=[])
    client = _FakeSupabaseClient(_FakeTable(select_rows=rows), job_matches=job_matches)

    await list_today_items(client, _USER_ID)  # type: ignore[arg-type]

    assert job_matches.select_calls == 0


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
