"""Tests for ContactFinder persistence (outreach-contactfinder.md Phase
A/B) -- per-user, immutable-run shape mirroring company_intel_store.py's
own test suite.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from between_jobs.api.contact_research import ContactCandidate, ContactEvidence
from between_jobs.api.contact_research_store import (
    CandidateNotFound,
    create_run,
    get_candidates_with_evidence,
    get_evidence_for_candidate,
    get_latest_run,
    get_owned_candidate,
    save_enrichment,
)

_USER_ID = "00000000-0000-0000-0000-000000000001"
_APPLICATION_ID = "30000000-0000-0000-0000-000000000001"
_RUN_ID = "70000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def in_(self, *_: Any, **__: Any) -> _ChainBuilder:
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
        self._next_id = 1

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def insert(self, data: Any) -> _ChainBuilder:
        self.insert_calls.append(data)
        if isinstance(data, list):
            rows = []
            for row in data:
                rows.append({"id": f"row-{self._next_id}", **row})
                self._next_id += 1
        else:
            rows = [self.insert_row] if self.insert_row is not None else [{**data, "id": "row-1"}]
        self.select_rows.extend(rows)
        return _ChainBuilder(rows)

    def update(self, data: Any) -> _ChainBuilder:
        self.update_calls.append(data)
        # Mirrors a real Postgrest update-with-representation against
        # whatever row `.eq("id", ...)` targets -- this fake doesn't
        # actually filter on the id, since every test here updates the
        # one row already seeded in `select_rows`.
        for row in self.select_rows:
            row.update(data)
        return _ChainBuilder(self.select_rows)


class _FakeSupabaseClient:
    def __init__(
        self,
        *,
        runs: _FakeTable | None = None,
        candidates: _FakeTable | None = None,
        evidence: _FakeTable | None = None,
    ) -> None:
        self.contact_research_runs = runs or _FakeTable(
            select_rows=[], insert_row={"id": _RUN_ID, "application_id": _APPLICATION_ID}
        )
        self.contact_candidates = candidates or _FakeTable(select_rows=[])
        self.contact_candidate_evidence = evidence or _FakeTable(select_rows=[])

    def table(self, name: str) -> Any:
        return {
            "contact_research_runs": self.contact_research_runs,
            "contact_candidates": self.contact_candidates,
            "contact_candidate_evidence": self.contact_candidate_evidence,
        }[name]


def _evidence() -> ContactEvidence:
    return ContactEvidence(
        source_url="https://blog.acme.example/team/jane-doe",
        source_title="Jane Doe -- Technical Recruiter at Acme",
        source_snippet="Jane Doe is hiring for the Acme platform team.",
        observed_at="2026-08-01T00:00:00Z",
        evidence_kind="search_snippet",
        confidence="verified",
    )


def _candidate() -> ContactCandidate:
    return ContactCandidate(
        person_name="Jane Doe",
        claimed_title="Technical Recruiter",
        claimed_team="Platform",
        company="Acme",
        persona="recruiter",
        relevance_reason="Recruiter persona match.",
        priority_score=55.0,
        evidence=[_evidence()],
    )


async def test_create_run_inserts_the_run_candidates_and_evidence() -> None:
    supabase = _FakeSupabaseClient()

    run = await create_run(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        application_id=_APPLICATION_ID,
        company_name="Acme",
        candidates=[_candidate()],
        providers_used=["you_com"],
        warnings=[],
    )

    assert run["id"] == _RUN_ID
    assert len(supabase.contact_candidates.insert_calls) == 1
    inserted_candidates = supabase.contact_candidates.insert_calls[0]
    assert inserted_candidates[0]["run_id"] == _RUN_ID
    assert inserted_candidates[0]["person_name"] == "Jane Doe"

    assert len(supabase.contact_candidate_evidence.insert_calls) == 1
    inserted_evidence = supabase.contact_candidate_evidence.insert_calls[0]
    assert inserted_evidence[0]["candidate_id"] == "row-1"
    assert inserted_evidence[0]["source_url"] == _evidence()["source_url"]


async def test_create_run_with_no_candidates_skips_both_child_inserts() -> None:
    supabase = _FakeSupabaseClient()

    await create_run(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        application_id=_APPLICATION_ID,
        company_name="Acme",
        candidates=[],
        providers_used=[],
        warnings=["You.com key missing"],
    )

    assert supabase.contact_candidates.insert_calls == []
    assert supabase.contact_candidate_evidence.insert_calls == []


async def test_get_latest_run_returns_none_when_nothing_exists() -> None:
    supabase = _FakeSupabaseClient(runs=_FakeTable(select_rows=[]))
    result = await get_latest_run(supabase, _USER_ID, _APPLICATION_ID)  # type: ignore[arg-type]
    assert result is None


async def test_get_candidates_with_evidence_batches_evidence_by_candidate() -> None:
    candidate_rows = [
        {"id": "cand-1", "run_id": _RUN_ID, "person_name": "Jane Doe", "priority_score": 55},
        {"id": "cand-2", "run_id": _RUN_ID, "person_name": "John Smith", "priority_score": 30},
    ]
    evidence_rows = [
        {"id": "ev-1", "candidate_id": "cand-1", "source_url": "https://a.example"},
        {"id": "ev-2", "candidate_id": "cand-1", "source_url": "https://b.example"},
        {"id": "ev-3", "candidate_id": "cand-2", "source_url": "https://c.example"},
    ]
    supabase = _FakeSupabaseClient(
        candidates=_FakeTable(select_rows=candidate_rows),
        evidence=_FakeTable(select_rows=evidence_rows),
    )

    result = await get_candidates_with_evidence(supabase, _RUN_ID)  # type: ignore[arg-type]

    assert len(result) == 2
    by_id = {c["id"]: c for c in result}
    assert len(by_id["cand-1"]["evidence"]) == 2
    assert len(by_id["cand-2"]["evidence"]) == 1


async def test_get_candidates_with_evidence_returns_empty_list_with_no_candidates() -> None:
    supabase = _FakeSupabaseClient(candidates=_FakeTable(select_rows=[]))
    result = await get_candidates_with_evidence(supabase, _RUN_ID)  # type: ignore[arg-type]
    assert result == []


_CANDIDATE_ID = "80000000-0000-0000-0000-000000000001"


async def test_get_owned_candidate_returns_the_candidate_when_the_run_belongs_to_the_user() -> None:
    candidate_row = {"id": _CANDIDATE_ID, "run_id": _RUN_ID, "person_name": "Jane Doe"}
    run_row = {"id": _RUN_ID, "user_id": _USER_ID}
    supabase = _FakeSupabaseClient(
        runs=_FakeTable(select_rows=[run_row]),
        candidates=_FakeTable(select_rows=[candidate_row]),
    )

    result = await get_owned_candidate(supabase, _USER_ID, _CANDIDATE_ID)  # type: ignore[arg-type]

    assert result["id"] == _CANDIDATE_ID


async def test_get_owned_candidate_raises_when_no_candidate_exists() -> None:
    supabase = _FakeSupabaseClient(candidates=_FakeTable(select_rows=[]))

    with pytest.raises(CandidateNotFound):
        await get_owned_candidate(supabase, _USER_ID, _CANDIDATE_ID)  # type: ignore[arg-type]


async def test_get_owned_candidate_raises_when_the_run_belongs_to_someone_else() -> None:
    """The `contact_candidates` migration has no direct select policy --
    reachable only by joining through a run the user owns. This is the
    Python-side enforcement of that same boundary."""
    candidate_row = {"id": _CANDIDATE_ID, "run_id": _RUN_ID, "person_name": "Jane Doe"}
    supabase = _FakeSupabaseClient(
        runs=_FakeTable(select_rows=[]),  # the .eq("user_id", ...) filter finds nothing
        candidates=_FakeTable(select_rows=[candidate_row]),
    )

    with pytest.raises(CandidateNotFound):
        await get_owned_candidate(supabase, _USER_ID, _CANDIDATE_ID)  # type: ignore[arg-type]


async def test_save_enrichment_updates_the_candidate_row() -> None:
    candidate_row = {"id": _CANDIDATE_ID, "run_id": _RUN_ID, "person_name": "Jane Doe"}
    candidates_table = _FakeTable(select_rows=[candidate_row])
    supabase = _FakeSupabaseClient(candidates=candidates_table)

    result = await save_enrichment(
        supabase,  # type: ignore[arg-type]
        _CANDIDATE_ID,
        email="jane.doe@acme.example",
        email_status="verified",
        provider="apollo",
        enriched_at="2026-09-02T00:00:00Z",
    )

    assert result["enriched_email"] == "jane.doe@acme.example"
    assert candidates_table.update_calls[0]["enrichment_provider"] == "apollo"


async def test_get_evidence_for_candidate_returns_only_that_candidates_rows() -> None:
    rows = [{"id": "ev-1", "candidate_id": _CANDIDATE_ID, "source_url": "https://a.example"}]
    supabase = _FakeSupabaseClient(evidence=_FakeTable(select_rows=rows))

    result = await get_evidence_for_candidate(supabase, _CANDIDATE_ID)  # type: ignore[arg-type]

    assert result == rows
