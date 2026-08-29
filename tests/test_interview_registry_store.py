"""Tests for interview-registry persistence (InterviewForge R1,
interviewforge-v1.md)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from between_jobs.api.interview_registry import InterviewProcessModel
from between_jobs.api.interview_registry_store import (
    create_registry_entry,
    get_latest_registry_entry,
)

_RUN_ID = "70000000-0000-0000-0000-000000000001"


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
        self.insert_calls: list[Any] = []

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def insert(self, data: Any) -> _ChainBuilder:
        self.insert_calls.append(data)
        row = self.insert_row if self.insert_row is not None else {**data, "id": "row-1"}
        return _ChainBuilder([row])


class _FakeSupabaseClient:
    def __init__(self, *, registry: _FakeTable | None = None) -> None:
        self.interview_process_registry = registry or _FakeTable(select_rows=[])

    def table(self, name: str) -> Any:
        return {"interview_process_registry": self.interview_process_registry}[name]


def _model(**overrides: Any) -> InterviewProcessModel:
    base: InterviewProcessModel = {
        "company_name": "Acme",
        "rounds": [{"name": "Recruiter screen"}],
        "typical_topics": ["Python"],
        "difficulty_signal": "medium",
        "values_signals": ["ownership"],
        "confidence": "high",
    }
    return {**base, **overrides}  # type: ignore[typeddict-item]


async def test_create_registry_entry_inserts_the_model_fields() -> None:
    supabase = _FakeSupabaseClient()

    row = await create_registry_entry(
        supabase,  # type: ignore[arg-type]
        company_name="Acme",
        model=_model(),
        source_run_id=_RUN_ID,
    )

    assert row["id"] == "row-1"
    assert len(supabase.interview_process_registry.insert_calls) == 1
    inserted = supabase.interview_process_registry.insert_calls[0]
    assert inserted["company_name"] == "Acme"
    assert inserted["rounds"] == [{"name": "Recruiter screen"}]
    assert inserted["typical_topics"] == ["Python"]
    assert inserted["difficulty_signal"] == "medium"
    assert inserted["values_signals"] == ["ownership"]
    assert inserted["confidence"] == "high"
    assert inserted["source_run_id"] == _RUN_ID


async def test_get_latest_registry_entry_returns_none_when_nothing_exists() -> None:
    supabase = _FakeSupabaseClient(registry=_FakeTable(select_rows=[]))

    result = await get_latest_registry_entry(supabase, company_name="Acme")  # type: ignore[arg-type]

    assert result is None


async def test_get_latest_registry_entry_returns_the_top_row() -> None:
    row = {"id": "entry-1", "company_name": "Acme"}
    supabase = _FakeSupabaseClient(registry=_FakeTable(select_rows=[row]))

    result = await get_latest_registry_entry(supabase, company_name="Acme")  # type: ignore[arg-type]

    assert result == row
