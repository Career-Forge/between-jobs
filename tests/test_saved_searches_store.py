"""Tests for saved-search persistence (Job Finder P9a, today-feed-job-
matching.md)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from postgrest.exceptions import APIError

from between_jobs.api.saved_searches_store import (
    MAX_SAVED_SEARCHES,
    SavedSearchLimitReached,
    SavedSearchNotFound,
    create_saved_search,
    delete_saved_search,
    get_saved_search,
    list_saved_searches,
    set_saved_search_active,
)

_USER_ID = "00000000-0000-0000-0000-000000000001"
_OTHER_USER_ID = "00000000-0000-0000-0000-000000000002"
_SEARCH_ID = "10000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, column: str, value: Any) -> _ChainBuilder:
        return _ChainBuilder([r for r in self._rows if r.get(column) == value])

    def order(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FailingInsert:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def execute(self) -> SimpleNamespace:
        raise self._error


class _FakeTable:
    def __init__(
        self, *, rows: list[dict[str, Any]] | None = None, insert_error: Exception | None = None
    ) -> None:
        self.rows = rows if rows is not None else []
        self._next_id = 1
        self.insert_error = insert_error

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.rows)

    def insert(self, data: dict[str, Any]) -> Any:
        if self.insert_error is not None:
            return _FailingInsert(self.insert_error)
        row = {"id": f"row-{self._next_id}", **data}
        self._next_id += 1
        self.rows.append(row)
        return _ChainBuilder([row])

    def update(self, data: dict[str, Any]) -> _UpdateBuilder:
        return _UpdateBuilder(self, data)

    def delete(self) -> _DeleteBuilder:
        return _DeleteBuilder(self)


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


class _FakeSupabase:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._table = _FakeTable(rows=rows)

    def table(self, name: str) -> _FakeTable:
        assert name == "saved_searches"
        return self._table


async def test_create_saved_search_returns_the_inserted_row() -> None:
    supabase = _FakeSupabase()

    row = await create_saved_search(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        query="backend engineer",
        location="New York, NY",
        companies=["Anthropic"],
        remote_only=False,
    )

    assert row["query"] == "backend engineer"
    assert row["companies"] == ["Anthropic"]
    assert row["user_id"] == _USER_ID


async def test_list_saved_searches_only_returns_the_caller_s_own() -> None:
    supabase = _FakeSupabase(
        rows=[
            {"id": "s1", "user_id": _USER_ID, "query": "a"},
            {"id": "s2", "user_id": _OTHER_USER_ID, "query": "b"},
        ]
    )

    results = await list_saved_searches(supabase, _USER_ID)  # type: ignore[arg-type]

    assert [r["id"] for r in results] == ["s1"]


async def test_set_saved_search_active_updates_the_flag() -> None:
    supabase = _FakeSupabase(rows=[{"id": _SEARCH_ID, "user_id": _USER_ID, "is_active": True}])

    row = await set_saved_search_active(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        _SEARCH_ID,
        is_active=False,
    )

    assert row["is_active"] is False


async def test_set_saved_search_active_raises_when_not_found() -> None:
    supabase = _FakeSupabase(rows=[])

    try:
        await set_saved_search_active(
            supabase,  # type: ignore[arg-type]
            _USER_ID,
            _SEARCH_ID,
            is_active=False,
        )
        raise AssertionError("expected SavedSearchNotFound")
    except SavedSearchNotFound:
        pass


async def test_set_saved_search_active_cannot_touch_another_user_s_search() -> None:
    supabase = _FakeSupabase(
        rows=[{"id": _SEARCH_ID, "user_id": _OTHER_USER_ID, "is_active": True}]
    )

    try:
        await set_saved_search_active(
            supabase,  # type: ignore[arg-type]
            _USER_ID,
            _SEARCH_ID,
            is_active=False,
        )
        raise AssertionError("expected SavedSearchNotFound")
    except SavedSearchNotFound:
        pass


async def test_delete_saved_search_removes_the_row() -> None:
    supabase = _FakeSupabase(rows=[{"id": _SEARCH_ID, "user_id": _USER_ID}])

    await delete_saved_search(supabase, _USER_ID, _SEARCH_ID)  # type: ignore[arg-type]

    assert supabase._table.rows == []


async def test_delete_saved_search_raises_when_not_found() -> None:
    supabase = _FakeSupabase(rows=[])

    try:
        await delete_saved_search(supabase, _USER_ID, _SEARCH_ID)  # type: ignore[arg-type]
        raise AssertionError("expected SavedSearchNotFound")
    except SavedSearchNotFound:
        pass


async def test_get_saved_search_returns_the_row() -> None:
    supabase = _FakeSupabase(rows=[{"id": _SEARCH_ID, "user_id": _USER_ID, "query": "a"}])

    row = await get_saved_search(supabase, _USER_ID, _SEARCH_ID)  # type: ignore[arg-type]

    assert row["id"] == _SEARCH_ID


async def test_get_saved_search_raises_when_not_found() -> None:
    supabase = _FakeSupabase(rows=[])

    try:
        await get_saved_search(supabase, _USER_ID, _SEARCH_ID)  # type: ignore[arg-type]
        raise AssertionError("expected SavedSearchNotFound")
    except SavedSearchNotFound:
        pass


async def test_get_saved_search_cannot_see_another_user_s_search() -> None:
    supabase = _FakeSupabase(rows=[{"id": _SEARCH_ID, "user_id": _OTHER_USER_ID}])

    try:
        await get_saved_search(supabase, _USER_ID, _SEARCH_ID)  # type: ignore[arg-type]
        raise AssertionError("expected SavedSearchNotFound")
    except SavedSearchNotFound:
        pass


def _api_error(code: str) -> APIError:
    return APIError({"message": "m", "code": code, "details": None, "hint": None})


async def test_create_past_the_cap_raises_the_limit_error() -> None:
    supabase = _FakeSupabase()
    supabase._table.insert_error = _api_error("BJ009")

    with pytest.raises(SavedSearchLimitReached):
        await create_saved_search(
            supabase,  # type: ignore[arg-type]
            _USER_ID,
            query="a",
            location=None,
            companies=[],
            remote_only=False,
        )


async def test_create_lets_any_other_database_error_through_unchanged() -> None:
    supabase = _FakeSupabase()
    supabase._table.insert_error = _api_error("23503")

    with pytest.raises(APIError) as raised:
        await create_saved_search(
            supabase,  # type: ignore[arg-type]
            _USER_ID,
            query="a",
            location=None,
            companies=[],
            remote_only=False,
        )

    assert raised.value.code == "23503"


def test_the_cap_is_twenty() -> None:
    assert MAX_SAVED_SEARCHES == 20
