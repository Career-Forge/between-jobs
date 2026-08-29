"""Tests for working-set persistence and reference resolution (Sprint 2.6e)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from between_jobs.api.working_sets_store import (
    ReferenceOutOfRange,
    WorkingSetExpired,
    WorkingSetNotFound,
    create_working_set,
    get_active_working_set,
    get_working_set,
    resolve_reference,
)

_USER_ID = "00000000-0000-0000-0000-000000000001"
_WORKING_SET_ID = "50000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def order(self, *_: Any, **__: Any) -> _ChainBuilder:
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
        rows = [self.insert_row] if self.insert_row is not None else []
        return _ChainBuilder(rows)


class _FakeSupabaseClient:
    def __init__(self, working_sets: _FakeTable) -> None:
        self.working_sets = working_sets

    def table(self, name: str) -> Any:
        if name == "working_sets":
            return self.working_sets
        raise AssertionError(f"unexpected table: {name}")


def _iso(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).isoformat()


async def test_create_working_set_inserts_items_and_expiry() -> None:
    new_row = {"id": _WORKING_SET_ID, "items": [{"job_id": "a"}, {"job_id": "b"}]}
    table = _FakeTable(select_rows=[], insert_row=new_row)
    client = _FakeSupabaseClient(table)

    result = await create_working_set(
        client,  # type: ignore[arg-type]
        _USER_ID,
        kind="job_search_results",
        source_channel="telegram",
        items=[{"job_id": "a"}, {"job_id": "b"}],
    )

    assert result == new_row
    assert len(table.insert_calls) == 1
    assert table.insert_calls[0]["kind"] == "job_search_results"
    assert table.insert_calls[0]["items"] == [{"job_id": "a"}, {"job_id": "b"}]
    assert "expires_at" in table.insert_calls[0]


async def test_get_working_set_found() -> None:
    row = {"id": _WORKING_SET_ID}
    client = _FakeSupabaseClient(_FakeTable(select_rows=[row]))
    result = await get_working_set(client, _USER_ID, _WORKING_SET_ID)  # type: ignore[arg-type]
    assert result == row


async def test_get_working_set_not_found_raises() -> None:
    client = _FakeSupabaseClient(_FakeTable(select_rows=[]))
    with pytest.raises(WorkingSetNotFound):
        await get_working_set(client, _USER_ID, _WORKING_SET_ID)  # type: ignore[arg-type]


async def test_get_active_working_set_returns_none_when_absent() -> None:
    client = _FakeSupabaseClient(_FakeTable(select_rows=[]))
    result = await get_active_working_set(client, _USER_ID, "job_search_results")  # type: ignore[arg-type]
    assert result is None


async def test_get_active_working_set_returns_the_row() -> None:
    row = {"id": _WORKING_SET_ID, "kind": "job_search_results"}
    client = _FakeSupabaseClient(_FakeTable(select_rows=[row]))
    result = await get_active_working_set(client, _USER_ID, "job_search_results")  # type: ignore[arg-type]
    assert result == row


async def test_resolve_reference_returns_the_indexed_item() -> None:
    row = {
        "id": _WORKING_SET_ID,
        "items": ["first", "second", "third"],
        "expires_at": _iso(timedelta(minutes=10)),
    }
    client = _FakeSupabaseClient(_FakeTable(select_rows=[row]))
    result = await resolve_reference(client, _USER_ID, _WORKING_SET_ID, 2)  # type: ignore[arg-type]
    assert result == "second"


async def test_resolve_reference_raises_when_expired() -> None:
    row = {
        "id": _WORKING_SET_ID,
        "items": ["first"],
        "expires_at": _iso(-timedelta(minutes=1)),
    }
    client = _FakeSupabaseClient(_FakeTable(select_rows=[row]))
    with pytest.raises(WorkingSetExpired):
        await resolve_reference(client, _USER_ID, _WORKING_SET_ID, 1)  # type: ignore[arg-type]


async def test_resolve_reference_raises_when_index_out_of_range() -> None:
    row = {
        "id": _WORKING_SET_ID,
        "items": ["first", "second"],
        "expires_at": _iso(timedelta(minutes=10)),
    }
    client = _FakeSupabaseClient(_FakeTable(select_rows=[row]))
    with pytest.raises(ReferenceOutOfRange):
        await resolve_reference(client, _USER_ID, _WORKING_SET_ID, 3)  # type: ignore[arg-type]


async def test_resolve_reference_raises_when_index_is_zero_or_negative() -> None:
    row = {
        "id": _WORKING_SET_ID,
        "items": ["first"],
        "expires_at": _iso(timedelta(minutes=10)),
    }
    client = _FakeSupabaseClient(_FakeTable(select_rows=[row]))
    with pytest.raises(ReferenceOutOfRange):
        await resolve_reference(client, _USER_ID, _WORKING_SET_ID, 0)  # type: ignore[arg-type]
