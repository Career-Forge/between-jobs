"""Tests for company-intel persistence (Horizon Sprint 5.0)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from between_jobs.api.company_intel_pipeline import Claim
from between_jobs.api.company_intel_store import create_run, get_claims_for_run, get_latest_run

_USER_ID = "00000000-0000-0000-0000-000000000001"
_APPLICATION_ID = "30000000-0000-0000-0000-000000000001"
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
        self._next_id = 1

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def insert(self, data: Any) -> _ChainBuilder:
        # Mirrors real Postgrest insert-with-representation: every row
        # gets a DB-assigned id and becomes visible to a later select(),
        # same as `id uuid primary key default gen_random_uuid()` would.
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


class _FakeSupabaseClient:
    def __init__(
        self,
        *,
        runs: _FakeTable | None = None,
        claims: _FakeTable | None = None,
    ) -> None:
        self.company_intel_runs = runs or _FakeTable(
            select_rows=[], insert_row={"id": _RUN_ID, "application_id": _APPLICATION_ID}
        )
        self.company_intel_claims = claims or _FakeTable(select_rows=[])

    def table(self, name: str) -> Any:
        return {
            "company_intel_runs": self.company_intel_runs,
            "company_intel_claims": self.company_intel_claims,
        }[name]


def _claim() -> Claim:
    return Claim(
        category="product_and_mission",
        claim_text="Acme builds banking software.",
        source_url="https://acme.example",
        source_title="About Acme",
        confidence="high",
    )


async def test_create_run_inserts_the_run_and_its_claims() -> None:
    supabase = _FakeSupabaseClient()

    run = await create_run(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        application_id=_APPLICATION_ID,
        company_name="Acme",
        claims=[_claim()],
        providers_used=["you_com"],
        warnings=[],
    )

    assert run["id"] == _RUN_ID
    assert len(supabase.company_intel_runs.insert_calls) == 1
    assert supabase.company_intel_runs.insert_calls[0]["company_name"] == "Acme"
    assert len(supabase.company_intel_claims.insert_calls) == 1
    inserted_claims = supabase.company_intel_claims.insert_calls[0]
    assert inserted_claims[0]["run_id"] == _RUN_ID
    assert inserted_claims[0]["category"] == "product_and_mission"


async def test_create_run_persists_claims_with_distinct_ids_even_when_identical() -> None:
    """Regression test: `Claim` (the pipeline's TypedDict) carries no
    `id` -- two claims that are otherwise identical (same category and
    text, which a real LLM synthesis pass can plausibly produce) must
    still come back as distinct, individually addressable rows once
    persisted, not collapse onto a shared id."""
    supabase = _FakeSupabaseClient()

    run = await create_run(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        application_id=_APPLICATION_ID,
        company_name="Acme",
        claims=[_claim(), _claim()],
        providers_used=["you_com"],
        warnings=[],
    )

    persisted = await get_claims_for_run(supabase, run["id"])  # type: ignore[arg-type]
    ids = [row["id"] for row in persisted]
    assert len(ids) == 2
    assert all(ids)
    assert len(set(ids)) == len(ids)


async def test_create_run_with_no_claims_skips_the_claims_insert() -> None:
    supabase = _FakeSupabaseClient()

    await create_run(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        application_id=_APPLICATION_ID,
        company_name="Acme",
        claims=[],
        providers_used=[],
        warnings=["You.com key missing"],
    )

    assert supabase.company_intel_claims.insert_calls == []


async def test_get_latest_run_returns_none_when_nothing_exists() -> None:
    supabase = _FakeSupabaseClient(runs=_FakeTable(select_rows=[]))

    result = await get_latest_run(supabase, _USER_ID, _APPLICATION_ID)  # type: ignore[arg-type]

    assert result is None


async def test_get_latest_run_returns_the_top_row() -> None:
    row = {"id": _RUN_ID, "company_name": "Acme"}
    supabase = _FakeSupabaseClient(runs=_FakeTable(select_rows=[row]))

    result = await get_latest_run(supabase, _USER_ID, _APPLICATION_ID)  # type: ignore[arg-type]

    assert result == row


async def test_get_claims_for_run_returns_the_rows() -> None:
    rows = [{"id": "claim-1", "category": "hiring_activity"}]
    supabase = _FakeSupabaseClient(claims=_FakeTable(select_rows=rows))

    result = await get_claims_for_run(supabase, _RUN_ID)  # type: ignore[arg-type]

    assert result == rows
