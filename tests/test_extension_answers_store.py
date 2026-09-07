"""Tests for `approved_answers` persistence -- browser-extension.md E1's
"known-question memory" (exact-question and canonical-intent-alias match
tiers only; semantic matching is explicitly deferred past v1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from between_jobs.api.extension_answers_store import (
    match_approved_answer,
    record_answer_used,
    save_approved_answer,
)

_USER_ID = "00000000-0000-0000-0000-000000000001"
_OTHER_USER_ID = "00000000-0000-0000-0000-000000000002"
_ANSWER_ID = "20000000-0000-0000-0000-000000000001"


def _apply_or(rows: list[dict[str, Any]], expr: str) -> list[dict[str, Any]]:
    """Minimal parser for the two `.or_()` clause shapes this module
    actually sends -- `col.is.null` and `col.gt.<iso>` -- not a general
    PostgREST filter-string parser."""
    clauses = expr.split(",")

    def matches(row: dict[str, Any]) -> bool:
        for clause in clauses:
            column, op, value = clause.split(".", 2)
            if op == "is" and value == "null" and row.get(column) is None:
                return True
            if op == "gt":
                current = row.get(column)
                if current is not None and current > value:
                    return True
        return False

    return [r for r in rows if matches(r)]


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, column: str, value: Any) -> _ChainBuilder:
        return _ChainBuilder([r for r in self._rows if r.get(column) == value])

    def or_(self, expr: str) -> _ChainBuilder:
        return _ChainBuilder(_apply_or(self._rows, expr))

    def order(self, *_: Any, **__: Any) -> _ChainBuilder:
        rows = sorted(self._rows, key=lambda r: r.get("updated_at", ""), reverse=True)
        return _ChainBuilder(rows)

    def limit(self, _n: int) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


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


class _FakeTable:
    def __init__(self, *, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows if rows is not None else []
        self._next_id = 1

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.rows)

    def update(self, data: dict[str, Any]) -> _UpdateBuilder:
        return _UpdateBuilder(self, data)

    def upsert(self, data: dict[str, Any], *, on_conflict: str) -> _ChainBuilder:
        conflict_cols = on_conflict.split(",")
        existing = next(
            (r for r in self.rows if all(r.get(c) == data.get(c) for c in conflict_cols)),
            None,
        )
        if existing is not None:
            existing.update(data)
            return _ChainBuilder([existing])
        row = {"id": f"row-{self._next_id}", "times_used": 0, **data}
        self._next_id += 1
        self.rows.append(row)
        return _ChainBuilder([row])


class _FakeSupabase:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._table = _FakeTable(rows=rows)

    def table(self, name: str) -> _FakeTable:
        assert name == "approved_answers"
        return self._table


async def test_match_approved_answer_exact_question_hit() -> None:
    supabase = _FakeSupabase(
        rows=[
            {
                "id": _ANSWER_ID,
                "user_id": _USER_ID,
                "normalized_question": "are you willing to relocate",
                "canonical_intent": "willing_to_relocate",
                "answer_text": "Yes",
                "expires_at": None,
                "updated_at": "2026-09-01T00:00:00Z",
            }
        ]
    )

    result = await match_approved_answer(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        normalized_question="are you willing to relocate",
    )

    assert result is not None
    assert result["answer_text"] == "Yes"


async def test_match_approved_answer_falls_back_to_canonical_intent() -> None:
    supabase = _FakeSupabase(
        rows=[
            {
                "id": _ANSWER_ID,
                "user_id": _USER_ID,
                "normalized_question": "would you be open to relocating",
                "canonical_intent": "willing_to_relocate",
                "answer_text": "Yes",
                "expires_at": None,
                "updated_at": "2026-09-01T00:00:00Z",
            }
        ]
    )

    result = await match_approved_answer(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        normalized_question="are you willing to relocate to nyc",
        canonical_intent="willing_to_relocate",
    )

    assert result is not None
    assert result["answer_text"] == "Yes"


async def test_match_approved_answer_no_intent_alias_without_canonical_intent() -> None:
    """A differently-worded question with no `canonical_intent` supplied
    must never fall through to a fuzzy/semantic match -- that tier is
    explicitly out of scope for v1."""
    supabase = _FakeSupabase(
        rows=[
            {
                "id": _ANSWER_ID,
                "user_id": _USER_ID,
                "normalized_question": "would you be open to relocating",
                "canonical_intent": "willing_to_relocate",
                "answer_text": "Yes",
                "expires_at": None,
                "updated_at": "2026-09-01T00:00:00Z",
            }
        ]
    )

    result = await match_approved_answer(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        normalized_question="are you willing to relocate to nyc",
    )

    assert result is None


async def test_match_approved_answer_ignores_expired_rows() -> None:
    yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    supabase = _FakeSupabase(
        rows=[
            {
                "id": _ANSWER_ID,
                "user_id": _USER_ID,
                "normalized_question": "when are you available to start",
                "canonical_intent": "availability",
                "answer_text": "Immediately",
                "expires_at": yesterday,
                "updated_at": "2026-09-01T00:00:00Z",
            }
        ]
    )

    result = await match_approved_answer(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        normalized_question="when are you available to start",
    )

    assert result is None


async def test_match_approved_answer_never_sees_another_user_s_row() -> None:
    supabase = _FakeSupabase(
        rows=[
            {
                "id": _ANSWER_ID,
                "user_id": _OTHER_USER_ID,
                "normalized_question": "are you willing to relocate",
                "canonical_intent": "willing_to_relocate",
                "answer_text": "Yes",
                "expires_at": None,
                "updated_at": "2026-09-01T00:00:00Z",
            }
        ]
    )

    result = await match_approved_answer(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        normalized_question="are you willing to relocate",
        canonical_intent="willing_to_relocate",
    )

    assert result is None


async def test_match_approved_answer_blocks_a_jurisdiction_mismatch_on_intent_tier() -> None:
    """Regression guard for a real, adversarially-confirmed bug: the
    intent-alias tier used to ignore jurisdiction entirely, so a US-tagged
    work-authorization answer could get served for a UK posting whose
    screening question happened to share the same canonical_intent."""
    supabase = _FakeSupabase(
        rows=[
            {
                "id": _ANSWER_ID,
                "user_id": _USER_ID,
                "normalized_question": "are you authorized to work in the us",
                "canonical_intent": "work_authorization",
                "jurisdiction": "US",
                "answer_text": "Yes",
                "expires_at": None,
                "updated_at": "2026-09-01T00:00:00Z",
            }
        ]
    )

    result = await match_approved_answer(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        normalized_question="are you eligible to work in the uk",
        canonical_intent="work_authorization",
        jurisdiction="UK",
    )

    assert result is None


async def test_match_approved_answer_blocks_a_jurisdiction_mismatch_on_exact_tier_too() -> None:
    """The same identically-worded question can legitimately appear on
    postings in two different jurisdictions -- the exact-question tier
    needs the same guard as the intent tier, not just the fallback one."""
    supabase = _FakeSupabase(
        rows=[
            {
                "id": _ANSWER_ID,
                "user_id": _USER_ID,
                "normalized_question": "are you authorized to work in this country",
                "canonical_intent": "work_authorization",
                "jurisdiction": "US",
                "answer_text": "Yes",
                "expires_at": None,
                "updated_at": "2026-09-01T00:00:00Z",
            }
        ]
    )

    result = await match_approved_answer(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        normalized_question="are you authorized to work in this country",
        jurisdiction="UK",
    )

    assert result is None


async def test_match_approved_answer_allows_a_jurisdiction_match() -> None:
    supabase = _FakeSupabase(
        rows=[
            {
                "id": _ANSWER_ID,
                "user_id": _USER_ID,
                "normalized_question": "are you authorized to work in the us",
                "canonical_intent": "work_authorization",
                "jurisdiction": "US",
                "answer_text": "Yes",
                "expires_at": None,
                "updated_at": "2026-09-01T00:00:00Z",
            }
        ]
    )

    result = await match_approved_answer(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        normalized_question="are you authorized to work in the us",
        jurisdiction="US",
    )

    assert result is not None
    assert result["answer_text"] == "Yes"


async def test_match_approved_answer_allows_a_jurisdiction_untagged_answer_regardless() -> None:
    """An answer with no jurisdiction tag was never jurisdiction-specific
    (e.g. "are you willing to relocate") -- it stays eligible no matter
    what jurisdiction the caller supplies, or doesn't supply."""
    supabase = _FakeSupabase(
        rows=[
            {
                "id": _ANSWER_ID,
                "user_id": _USER_ID,
                "normalized_question": "are you willing to relocate",
                "canonical_intent": "willing_to_relocate",
                "jurisdiction": None,
                "answer_text": "Yes",
                "expires_at": None,
                "updated_at": "2026-09-01T00:00:00Z",
            }
        ]
    )

    result = await match_approved_answer(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        normalized_question="are you willing to relocate",
        jurisdiction="UK",
    )

    assert result is not None


async def test_save_approved_answer_inserts_a_new_row() -> None:
    supabase = _FakeSupabase()

    row = await save_approved_answer(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        normalized_question="are you willing to relocate",
        answer_text="Yes",
        canonical_intent="willing_to_relocate",
    )

    assert row["answer_text"] == "Yes"
    assert row["user_id"] == _USER_ID
    assert row["evidence_fact_ids"] == []


async def test_save_approved_answer_upserts_on_same_question() -> None:
    supabase = _FakeSupabase()
    await save_approved_answer(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        normalized_question="are you willing to relocate",
        answer_text="No",
    )

    updated = await save_approved_answer(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        normalized_question="are you willing to relocate",
        answer_text="Yes, for the right role",
    )

    assert updated["answer_text"] == "Yes, for the right role"
    assert len(supabase._table.rows) == 1


async def test_save_approved_answer_carries_sensitive_category_for_d6_gate() -> None:
    supabase = _FakeSupabase()

    row = await save_approved_answer(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        normalized_question="are you legally authorized to work in the us",
        answer_text="Yes",
        sensitive_category="work_authorization",
        jurisdiction="US",
    )

    assert row["sensitive_category"] == "work_authorization"
    assert row["jurisdiction"] == "US"


async def test_save_approved_answer_preserves_metadata_on_a_partial_resave() -> None:
    """Regression guard for a real, adversarially-confirmed bug: re-saving
    an edited answer_text without re-sending its metadata used to wipe
    sensitive_category/jurisdiction/canonical_intent/evidence_fact_ids back
    to unset, silently defeating D6's own opt-in gate for that answer."""
    supabase = _FakeSupabase()
    await save_approved_answer(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        normalized_question="are you legally authorized to work in the us",
        answer_text="Yes",
        canonical_intent="work_authorization",
        evidence_fact_ids=["fact-1"],
        jurisdiction="US",
        sensitive_category="work_authorization",
    )

    updated = await save_approved_answer(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        normalized_question="are you legally authorized to work in the us",
        answer_text="Yes, I have a valid work permit",
    )

    assert updated["answer_text"] == "Yes, I have a valid work permit"
    assert updated["sensitive_category"] == "work_authorization"
    assert updated["jurisdiction"] == "US"
    assert updated["canonical_intent"] == "work_authorization"
    assert updated["evidence_fact_ids"] == ["fact-1"]


async def test_record_answer_used_increments_the_counter() -> None:
    supabase = _FakeSupabase(rows=[{"id": _ANSWER_ID, "user_id": _USER_ID, "times_used": 2}])

    await record_answer_used(supabase, _USER_ID, _ANSWER_ID)  # type: ignore[arg-type]

    assert supabase._table.rows[0]["times_used"] == 3


async def test_record_answer_used_is_a_noop_for_a_missing_row() -> None:
    supabase = _FakeSupabase(rows=[])

    await record_answer_used(supabase, _USER_ID, _ANSWER_ID)  # type: ignore[arg-type]

    assert supabase._table.rows == []


async def test_record_answer_used_cannot_touch_another_user_s_row() -> None:
    supabase = _FakeSupabase(rows=[{"id": _ANSWER_ID, "user_id": _OTHER_USER_ID, "times_used": 2}])

    await record_answer_used(supabase, _USER_ID, _ANSWER_ID)  # type: ignore[arg-type]

    assert supabase._table.rows[0]["times_used"] == 2
