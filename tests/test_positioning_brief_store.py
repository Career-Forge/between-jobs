"""Tests for positioning-brief persistence (outreach-v2-search-first.md
Phase I) -- directly user-owned, immutable, "current = most recent" per
application.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from between_jobs.api.positioning_brief import PositioningBrief
from between_jobs.api.positioning_brief_store import create_brief, get_latest_brief

_USER_ID = "00000000-0000-0000-0000-000000000001"
_APPLICATION_ID = "30000000-0000-0000-0000-000000000001"
_BRIEF_ID = "90000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, column: str, value: Any) -> _ChainBuilder:
        # A REAL filter, not a no-op -- get_latest_brief's own user_id
        # scoping is a real cross-user access check (this backend queries
        # with the service-role key, RLS never gates it), so a fake that
        # ignores .eq() would give false confidence that scoping works.
        return _ChainBuilder([row for row in self._rows if row.get(column) == value])

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
        rows = [self.insert_row] if self.insert_row is not None else [{**data, "id": "row-1"}]
        self.select_rows.extend(rows)
        return _ChainBuilder(rows)


class _FakeSupabaseClient:
    def __init__(self, *, briefs: _FakeTable | None = None) -> None:
        self.positioning_briefs = briefs or _FakeTable(
            select_rows=[], insert_row={"id": _BRIEF_ID, "application_id": _APPLICATION_ID}
        )

    def table(self, name: str) -> Any:
        return {"positioning_briefs": self.positioning_briefs}[name]


def _brief() -> PositioningBrief:
    return PositioningBrief(
        lead_with="Lead with LLM Orchestration.",
        lead_with_citation="LLM Orchestration",
        gap_that_matters="Kafka is missing.",
        gap_citation="Kafka",
        recommended_project="Build a Kafka-based service.",
    )


async def test_create_brief_inserts_the_row() -> None:
    supabase = _FakeSupabaseClient()

    result = await create_brief(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        application_id=_APPLICATION_ID,
        brief=_brief(),
        rubric_warnings=[],
    )

    assert result["id"] == _BRIEF_ID
    inserted = supabase.positioning_briefs.insert_calls[0]
    assert inserted["lead_with_citation"] == "LLM Orchestration"
    assert inserted["gap_citation"] == "Kafka"
    assert inserted["application_id"] == _APPLICATION_ID
    assert inserted["rubric_warnings"] == []


async def test_get_latest_brief_returns_none_when_nothing_exists() -> None:
    supabase = _FakeSupabaseClient(briefs=_FakeTable(select_rows=[]))
    result = await get_latest_brief(supabase, _USER_ID, _APPLICATION_ID)  # type: ignore[arg-type]
    assert result is None


async def test_get_latest_brief_returns_the_top_row() -> None:
    row = {
        "id": _BRIEF_ID,
        "user_id": _USER_ID,
        "application_id": _APPLICATION_ID,
        "lead_with_citation": "Kafka",
    }
    supabase = _FakeSupabaseClient(briefs=_FakeTable(select_rows=[row]))
    result = await get_latest_brief(supabase, _USER_ID, _APPLICATION_ID)  # type: ignore[arg-type]
    assert result == row


async def test_get_latest_brief_never_returns_another_users_brief() -> None:
    """Regression test for a real IDOR an adversarial review caught: this
    backend queries with the service-role key, so RLS never gates this
    read -- user_id must be a real filter in the query itself."""
    other_user_row = {
        "id": _BRIEF_ID,
        "user_id": "99999999-0000-0000-0000-000000000099",
        "application_id": _APPLICATION_ID,
        "lead_with_citation": "Kafka",
    }
    supabase = _FakeSupabaseClient(briefs=_FakeTable(select_rows=[other_user_row]))
    result = await get_latest_brief(supabase, _USER_ID, _APPLICATION_ID)  # type: ignore[arg-type]
    assert result is None
