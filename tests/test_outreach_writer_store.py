"""Tests for outreach-draft persistence (outreach-contactfinder.md Phase
E) -- directly user-owned, immutable, "current = most recent" per
candidate.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from between_jobs.api.outreach_writer import OutreachDraft
from between_jobs.api.outreach_writer_store import (
    create_draft,
    get_latest_draft,
    mark_pushed_to_gmail,
)

_USER_ID = "00000000-0000-0000-0000-000000000001"
_CANDIDATE_ID = "80000000-0000-0000-0000-000000000001"
_DRAFT_ID = "90000000-0000-0000-0000-000000000001"


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
        self.update_calls: list[Any] = []

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def insert(self, data: Any) -> _ChainBuilder:
        self.insert_calls.append(data)
        rows = [self.insert_row] if self.insert_row is not None else [{**data, "id": "row-1"}]
        self.select_rows.extend(rows)
        return _ChainBuilder(rows)

    def update(self, data: Any) -> _ChainBuilder:
        self.update_calls.append(data)
        for row in self.select_rows:
            row.update(data)
        return _ChainBuilder(self.select_rows)


class _FakeSupabaseClient:
    def __init__(self, *, drafts: _FakeTable | None = None) -> None:
        self.outreach_drafts = drafts or _FakeTable(
            select_rows=[], insert_row={"id": _DRAFT_ID, "candidate_id": _CANDIDATE_ID}
        )

    def table(self, name: str) -> Any:
        return {"outreach_drafts": self.outreach_drafts}[name]


def _draft() -> OutreachDraft:
    return OutreachDraft(
        subject="Loved your PyData talk",
        email_body="Saw your PyData talk -- would love to chat about the role.",
        linkedin_message="Saw your PyData talk -- would love to connect.",
        follow_up_message="Following up in case my last note got buried!",
        hook_evidence_id="ev-2",
    )


async def test_create_draft_inserts_the_row() -> None:
    supabase = _FakeSupabaseClient()

    result = await create_draft(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        candidate_id=_CANDIDATE_ID,
        draft=_draft(),
        rubric_warnings=[],
    )

    assert result["id"] == _DRAFT_ID
    inserted = supabase.outreach_drafts.insert_calls[0]
    assert inserted["subject"] == "Loved your PyData talk"
    assert inserted["candidate_id"] == _CANDIDATE_ID
    assert inserted["rubric_warnings"] == []


async def test_get_latest_draft_returns_none_when_nothing_exists() -> None:
    supabase = _FakeSupabaseClient(drafts=_FakeTable(select_rows=[]))
    result = await get_latest_draft(supabase, _CANDIDATE_ID)  # type: ignore[arg-type]
    assert result is None


async def test_get_latest_draft_returns_the_top_row() -> None:
    row = {"id": _DRAFT_ID, "candidate_id": _CANDIDATE_ID, "subject": "Loved your PyData talk"}
    supabase = _FakeSupabaseClient(drafts=_FakeTable(select_rows=[row]))
    result = await get_latest_draft(supabase, _CANDIDATE_ID)  # type: ignore[arg-type]
    assert result == row


async def test_mark_pushed_to_gmail_updates_the_draft_row() -> None:
    row = {"id": _DRAFT_ID, "candidate_id": _CANDIDATE_ID}
    drafts_table = _FakeTable(select_rows=[row])
    supabase = _FakeSupabaseClient(drafts=drafts_table)

    result = await mark_pushed_to_gmail(
        supabase,  # type: ignore[arg-type]
        _DRAFT_ID,
        gmail_draft_id="gmail-draft-abc",
        pushed_to_gmail_at="2026-09-03T00:00:00Z",
    )

    assert result["gmail_draft_id"] == "gmail-draft-abc"
    assert drafts_table.update_calls[0]["gmail_draft_id"] == "gmail-draft-abc"
