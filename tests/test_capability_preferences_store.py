"""Tests for capability-preference persistence (Sprint 2.7d)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from between_jobs.api.capability_preferences_store import (
    delete_preference,
    get_preference,
    list_preferences,
    set_preference,
)

_USER_ID = "00000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTable:
    def __init__(
        self, *, select_rows: list[dict[str, Any]], upsert_row: dict[str, Any] | None = None
    ) -> None:
        self.select_rows = select_rows
        self.upsert_row = upsert_row
        self.upsert_calls: list[tuple[dict[str, Any], str]] = []
        self.delete_calls = 0

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def upsert(self, data: dict[str, Any], on_conflict: str = "") -> _ChainBuilder:
        self.upsert_calls.append((data, on_conflict))
        rows = [self.upsert_row] if self.upsert_row is not None else []
        return _ChainBuilder(rows)

    def delete(self) -> _ChainBuilder:
        self.delete_calls += 1
        return _ChainBuilder([])


class _FakeSupabaseClient:
    def __init__(self, table: _FakeTable) -> None:
        self.capability_preferences = table

    def table(self, name: str) -> Any:
        assert name == "capability_preferences"
        return self.capability_preferences


async def test_get_preference_found() -> None:
    row = {"capability": "resume_forge", "execution_mode": "byok_first_party"}
    client = _FakeSupabaseClient(_FakeTable(select_rows=[row]))
    result = await get_preference(client, _USER_ID, "resume_forge")  # type: ignore[arg-type]
    assert result == row


async def test_get_preference_returns_none_when_absent() -> None:
    client = _FakeSupabaseClient(_FakeTable(select_rows=[]))
    result = await get_preference(client, _USER_ID, "resume_forge")  # type: ignore[arg-type]
    assert result is None


async def test_list_preferences_returns_all_rows() -> None:
    rows = [{"capability": "default"}, {"capability": "resume_forge"}]
    client = _FakeSupabaseClient(_FakeTable(select_rows=rows))
    result = await list_preferences(client, _USER_ID)  # type: ignore[arg-type]
    assert result == rows


async def test_set_preference_upserts_on_user_and_capability() -> None:
    saved_row = {"capability": "resume_forge", "execution_mode": "byok_first_party"}
    table = _FakeTable(select_rows=[], upsert_row=saved_row)
    client = _FakeSupabaseClient(table)

    result = await set_preference(
        client,  # type: ignore[arg-type]
        _USER_ID,
        capability="resume_forge",
        execution_mode="byok_first_party",
        provider="openrouter",
        model="anthropic/claude-sonnet-4-6",
    )

    assert result == saved_row
    assert len(table.upsert_calls) == 1
    data, on_conflict = table.upsert_calls[0]
    assert data["capability"] == "resume_forge"
    assert data["provider"] == "openrouter"
    assert on_conflict == "user_id,capability"


async def test_delete_preference_calls_delete() -> None:
    table = _FakeTable(select_rows=[])
    client = _FakeSupabaseClient(table)

    await delete_preference(client, _USER_ID, "resume_forge")  # type: ignore[arg-type]

    assert table.delete_calls == 1
