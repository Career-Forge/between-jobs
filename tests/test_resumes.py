"""Tests for resume storage."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from between_jobs.api.resumes import get_resume, save_resume

_USER_ID = "00000000-0000-0000-0000-000000000001"


class _FakeQuery:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.upsert_calls: list[dict[str, Any]] = []

    def select(self, columns: str) -> _FakeQuery:
        return self

    def eq(self, column: str, value: Any) -> _FakeQuery:
        return self

    def upsert(self, data: dict[str, Any]) -> _FakeQuery:
        self.upsert_calls.append(data)
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeSupabaseClient:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._query = _FakeQuery(rows)

    def table(self, name: str) -> _FakeQuery:
        assert name == "resumes"
        return self._query


async def test_save_resume_upserts_with_user_id_and_text() -> None:
    client = _FakeSupabaseClient(rows=[])
    await save_resume(client, _USER_ID, "Jane Doe, Software Engineer")  # type: ignore[arg-type]
    assert client._query.upsert_calls == [
        {"user_id": _USER_ID, "raw_text": "Jane Doe, Software Engineer"}
    ]


async def test_get_resume_returns_none_when_absent() -> None:
    client = _FakeSupabaseClient(rows=[])
    result = await get_resume(client, _USER_ID)  # type: ignore[arg-type]
    assert result is None


async def test_get_resume_returns_row_when_present() -> None:
    row = {"raw_text": "Jane Doe", "updated_at": "2026-08-05T00:00:00Z"}
    client = _FakeSupabaseClient(rows=[row])
    result = await get_resume(client, _USER_ID)  # type: ignore[arg-type]
    assert result == row
