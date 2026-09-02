"""Tests for Job Finder P6b (live-search-track.md's own P6 scoping) --
the company-tier normalizer, the in-memory lookup index, and its
fail-open PostgREST-paginated loader. Ported from n8n's real Parse
Scorer Output / seed_company_tier_weights.js `normalizeCompanyName`."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from between_jobs.api.company_tiers import (
    CompanyTierIndex,
    get_company_tier_index,
    normalize_company_name,
)

# ── normalize_company_name ───────────────────────────────────────────────


def test_normalize_company_name_lowercases() -> None:
    assert normalize_company_name("Anthropic") == "anthropic"


def test_normalize_company_name_strips_legal_suffix() -> None:
    assert normalize_company_name("Acme Corp.") == "acme"


def test_normalize_company_name_strips_inc_suffix() -> None:
    assert normalize_company_name("Acme Inc") == "acme"


def test_normalize_company_name_replaces_ampersand_with_and() -> None:
    assert normalize_company_name("Johnson & Johnson") == "johnson and johnson"


def test_normalize_company_name_strips_punctuation() -> None:
    # "& Company" -> "and company" -> "company" is stripped as a legal
    # suffix, but the "and" itself is NOT -- "mckinsey and" is the real,
    # documented output (company_tier_overrides.json's own comment names
    # this exact string as why the short-form "McKinsey" alias had to be
    # added separately: the long form normalizes to "mckinsey and", not
    # "mckinsey").
    assert normalize_company_name("McKinsey & Company") == "mckinsey and"


def test_normalize_company_name_collapses_whitespace() -> None:
    assert normalize_company_name("  Acme   Holdings  ") == "acme"


def test_normalize_company_name_handles_none() -> None:
    assert normalize_company_name(None) == ""


def test_normalize_company_name_matches_the_real_source_keys() -> None:
    # Ported directly from company_tiers.json's own real keys -- confirms
    # the port matches n8n's actual output, not just this module's own logic.
    assert normalize_company_name("Ernst & Young") == "ernst and young"
    assert normalize_company_name("D. E. Shaw") == "d e shaw"
    assert normalize_company_name("Berkshire Hathaway") == "berkshire hathaway"


# ── CompanyTierIndex.lookup ──────────────────────────────────────────────


def _index() -> CompanyTierIndex:
    return CompanyTierIndex(weights_by_normalized_name={"anthropic": 1.0, "walmart": 0.8})


def test_lookup_hits_a_known_company() -> None:
    assert _index().lookup("Anthropic") == 1.0


def test_lookup_normalizes_before_matching() -> None:
    assert _index().lookup("Anthropic, Inc.") == 1.0


def test_lookup_misses_an_unknown_company() -> None:
    assert _index().lookup("Some Random Startup") is None


def test_lookup_returns_none_for_no_company() -> None:
    assert _index().lookup(None) is None
    assert _index().lookup("") is None


# ── get_company_tier_index (fail-open caching) ───────────────────────────


class _FakeQueryBuilder:
    """Simulates real PostgREST `.range()` pagination, mirroring
    `test_geo_gazetteer.py`'s own fake -- same PostgREST-1000-row-default
    bug class this project has hit three times already."""

    def __init__(self, rows: list[dict[str, Any]], *, raise_error: bool = False) -> None:
        self._rows = rows
        self._raise_error = raise_error
        self._start = 0
        self._end = len(rows) - 1

    def select(self, *_: Any, **__: Any) -> _FakeQueryBuilder:
        return self

    def range(self, start: int, end: int) -> _FakeQueryBuilder:
        self._start = start
        self._end = end
        return self

    async def execute(self) -> SimpleNamespace:
        if self._raise_error:
            raise RuntimeError("connection refused")
        return SimpleNamespace(data=self._rows[self._start : self._end + 1])


class _FakeSupabase:
    def __init__(self, rows: list[dict[str, Any]], *, raise_error: bool = False) -> None:
        self._rows = rows
        self._raise_error = raise_error

    def table(self, name: str) -> _FakeQueryBuilder:
        return _FakeQueryBuilder(self._rows, raise_error=self._raise_error)


async def test_get_company_tier_index_builds_from_real_rows() -> None:
    import between_jobs.api.company_tiers as tiers_module

    tiers_module._cached_index = None
    rows = [{"normalized_name": "anthropic", "weight": 1.0}]
    supabase = _FakeSupabase(rows)

    index = await get_company_tier_index(supabase)  # type: ignore[arg-type]

    assert index.lookup("Anthropic") == 1.0
    tiers_module._cached_index = None


async def test_get_company_tier_index_fails_open_on_error() -> None:
    import between_jobs.api.company_tiers as tiers_module

    tiers_module._cached_index = None
    supabase = _FakeSupabase([], raise_error=True)

    index = await get_company_tier_index(supabase)  # type: ignore[arg-type]

    assert index.lookup("Anthropic") is None
    tiers_module._cached_index = None


async def test_get_company_tier_index_caches_across_calls() -> None:
    import between_jobs.api.company_tiers as tiers_module

    tiers_module._cached_index = None
    supabase = _FakeSupabase([{"normalized_name": "anthropic", "weight": 1.0}])

    first = await get_company_tier_index(supabase)  # type: ignore[arg-type]
    supabase_second_call = _FakeSupabase([], raise_error=True)
    second = await get_company_tier_index(supabase_second_call)  # type: ignore[arg-type]

    assert first is second
    tiers_module._cached_index = None


async def test_get_company_tier_index_pages_past_the_first_1000_rows() -> None:
    """Regression test for the same PostgREST default-cap bug class
    already found and fixed for `import_job_registry_seed.py` and
    `geo_gazetteer.get_gazetteer` -- 576 real companies today is well
    under the cap, but a fixture bigger than one page is what actually
    proves the pagination loop works, not just that it compiles."""
    import between_jobs.api.company_tiers as tiers_module

    tiers_module._cached_index = None
    big_rows = [{"normalized_name": f"company{i}", "weight": 0.5} for i in range(1500)]
    supabase = _FakeSupabase(big_rows)

    index = await get_company_tier_index(supabase)  # type: ignore[arg-type]

    assert index.weights_by_normalized_name["company0"] == 0.5
    assert index.weights_by_normalized_name["company999"] == 0.5
    assert index.weights_by_normalized_name["company1499"] == 0.5
    tiers_module._cached_index = None
