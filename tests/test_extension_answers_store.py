"""Tests for `approved_answers` persistence -- browser-extension.md E1's
"known-question memory" (exact-question and canonical-intent-alias match
tiers only; semantic matching is explicitly deferred past v1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

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


class _RpcCall:
    """What `supabase.rpc(name, params).execute()` returns: the function's own result. The only
    function the store calls is `record_approved_answer_use`, emulated here with the same
    semantics as its SQL (one atomic update scoped to the id AND the user)."""

    def __init__(self, supabase: _FakeSupabase, name: str, params: dict[str, Any]) -> None:
        self._supabase = supabase
        self._name = name
        self._params = params

    async def execute(self) -> SimpleNamespace:
        self._supabase.rpc_calls.append((self._name, self._params))
        override = self._supabase.rpc_result
        if override is not _UNSET:
            return SimpleNamespace(data=override)
        assert self._name == "record_approved_answer_use"
        updated = False
        for row in self._supabase._table.rows:
            if (
                row.get("id") == self._params["p_answer_id"]
                and row.get("user_id") == self._params["p_user_id"]
            ):
                row["times_used"] = row["times_used"] + 1
                row["last_used_at"] = datetime.now(UTC).isoformat()
                updated = True
        return SimpleNamespace(data=updated)


_UNSET: Any = object()


class _FakeSupabase:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._table = _FakeTable(rows=rows)
        self.rpc_calls: list[tuple[str, dict[str, Any]]] = []
        self.rpc_result: Any = _UNSET

    def table(self, name: str) -> _FakeTable:
        assert name == "approved_answers"
        return self._table

    def rpc(self, name: str, params: dict[str, Any]) -> _RpcCall:
        return _RpcCall(self, name, params)


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


async def test_an_intent_answer_is_found_for_another_wording_at_another_company() -> None:
    """The reuse the extension's classifier exists for: the answer was written for "Why do you
    want to work at Acme?" and is offered again for "Why do you want to work here?" on another
    company's form. Nothing about the company is in the lookup; only the intent links them."""
    supabase = _FakeSupabase()
    await save_approved_answer(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        normalized_question="why do you want to work at acme?",
        answer_text="I like building developer tools.",
        canonical_intent="why_this_company",
    )

    exact_wording_elsewhere = await match_approved_answer(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        normalized_question="why do you want to work here?",
    )
    by_intent = await match_approved_answer(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        normalized_question="why do you want to work here?",
        canonical_intent="why_this_company",
    )
    other_intent = await match_approved_answer(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        normalized_question="why do you want to work here?",
        canonical_intent="why_this_role",
    )

    assert exact_wording_elsewhere is None  # no intent sent: still no fuzzy match
    assert by_intent is not None and by_intent["answer_text"] == "I like building developer tools."
    assert by_intent["normalized_question"] == "why do you want to work at acme?"
    assert other_intent is None


async def test_a_work_eligibility_answer_is_reused_only_under_the_country_it_was_tagged_with() -> (
    None
):
    supabase = _FakeSupabase()
    await save_approved_answer(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        normalized_question="are you legally authorized to work in the united states?",
        answer_text="Yes",
        canonical_intent="work_authorization",
        jurisdiction="US",
    )
    reworded = "do you have the right to work in the us?"

    same_country = await match_approved_answer(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        normalized_question=reworded,
        canonical_intent="work_authorization",
        jurisdiction="US",
    )
    other_country = await match_approved_answer(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        normalized_question=reworded,
        canonical_intent="work_authorization",
        jurisdiction="GB",
    )
    unknown_country = await match_approved_answer(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        normalized_question=reworded,
        canonical_intent="work_authorization",
    )

    assert same_country is not None and same_country["answer_text"] == "Yes"
    assert other_country is None
    assert unknown_country is None


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


async def test_record_answer_used_increments_the_counter_and_stamps_when() -> None:
    supabase = _FakeSupabase(
        rows=[{"id": _ANSWER_ID, "user_id": _USER_ID, "times_used": 2, "last_used_at": None}]
    )

    found = await record_answer_used(supabase, _USER_ID, _ANSWER_ID)  # type: ignore[arg-type]

    row = supabase._table.rows[0]
    assert found is True
    assert row["times_used"] == 3
    assert datetime.fromisoformat(row["last_used_at"]).tzinfo is not None
    assert abs(datetime.now(UTC) - datetime.fromisoformat(row["last_used_at"])) < timedelta(
        seconds=30
    )


async def test_record_answer_used_is_one_atomic_call_not_a_read_then_a_write() -> None:
    """The increment is a single database function: nothing is read first, so two reports for one
    answer arriving together cannot both start from the same stored count."""
    supabase = _FakeSupabase(
        rows=[{"id": _ANSWER_ID, "user_id": _USER_ID, "times_used": 0, "last_used_at": None}]
    )

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("record_answer_used must not read or write the table itself")

    supabase._table.select = forbidden  # type: ignore[method-assign]
    supabase._table.update = forbidden  # type: ignore[method-assign]

    for _ in range(5):
        assert await record_answer_used(supabase, _USER_ID, _ANSWER_ID) is True  # type: ignore[arg-type]

    assert supabase._table.rows[0]["times_used"] == 5
    assert (
        supabase.rpc_calls
        == [("record_approved_answer_use", {"p_user_id": _USER_ID, "p_answer_id": _ANSWER_ID})] * 5
    )


async def test_record_answer_used_raises_on_a_result_that_is_not_true_or_false() -> None:
    supabase = _FakeSupabase()
    supabase.rpc_result = None

    with pytest.raises(RuntimeError, match="expected true or false"):
        await record_answer_used(supabase, _USER_ID, _ANSWER_ID)  # type: ignore[arg-type]


async def test_record_answer_used_says_not_found_for_a_missing_row() -> None:
    supabase = _FakeSupabase(rows=[])

    found = await record_answer_used(supabase, _USER_ID, _ANSWER_ID)  # type: ignore[arg-type]

    assert found is False
    assert supabase._table.rows == []


async def test_record_answer_used_cannot_touch_another_user_s_row() -> None:
    supabase = _FakeSupabase(
        rows=[{"id": _ANSWER_ID, "user_id": _OTHER_USER_ID, "times_used": 2, "last_used_at": None}]
    )

    found = await record_answer_used(supabase, _USER_ID, _ANSWER_ID)  # type: ignore[arg-type]

    assert found is False  # indistinguishable from a missing id
    assert supabase._table.rows[0]["times_used"] == 2
    assert supabase._table.rows[0]["last_used_at"] is None
