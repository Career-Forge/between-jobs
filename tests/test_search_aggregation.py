"""Tests for Job Finder P5a-d (live-search-track.md's own P5 scoping) --
canonical dedup, aggregator demotion, tier filtering, sort, cohort/role
filtering, the registry lane, and (see the dedicated P5d section) the
gazetteer-backed location filter. Ported verbatim from n8n's real
Aggregate Jobs node (aggregate_jobs.js)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from between_jobs.api import geo_gazetteer as geo_gazetteer_module
from between_jobs.api.geo_gazetteer import Gazetteer, build_gazetteer
from between_jobs.api.search_aggregation import (
    _canonicalize_url,
    _dedup_keys,
    aggregate_jobs,
    apply_search_filters,
    expand_cohort,
    fetch_registry_lane,
    filter_by_companies,
    filter_by_location,
    filter_by_role,
    matches_all_role_terms,
    role_term_patterns,
)
from between_jobs.api.search_providers import SearchResult


def _result(**overrides: Any) -> SearchResult:
    base: dict[str, Any] = {
        "provider": "serper",
        "title": "Backend Engineer",
        "company": "Acme",
        "location": None,
        "remote": None,
        "apply_url": "https://boards.greenhouse.io/acme/jobs/1",
        "snippet": "",
        "posted_at": None,
        "source_tier": 1.0,
    }
    base.update(overrides)
    return SearchResult(**base)


# ── URL canonicalization ─────────────────────────────────────────────────


def test_canonicalize_url_strips_query_fragment_and_trailing_slash() -> None:
    assert (
        _canonicalize_url("https://Boards.Greenhouse.io/Acme/jobs/1/?ref=serper#top")
        == "https://boards.greenhouse.io/acme/jobs/1"
    )


# ── dedup keys ────────────────────────────────────────────────────────────


def test_dedup_keys_includes_url_key() -> None:
    keys = _dedup_keys(_result(apply_url="https://example.com/jobs/1"))
    assert "u:https://example.com/jobs/1" in keys


def test_dedup_keys_skips_short_company_title_pair() -> None:
    # "A|B" has length 2 after stripping the pipe -- below the >4 threshold.
    keys = _dedup_keys(_result(company="A", title="B", apply_url=""))
    assert not any(k.startswith("ct:") for k in keys)


def test_dedup_keys_includes_company_title_key_when_long_enough() -> None:
    keys = _dedup_keys(_result(company="Acme", title="Backend Engineer", apply_url=""))
    assert "ct:acme|backend engineer" in keys


# ── dedup + backfill via aggregate_jobs ──────────────────────────────────


def test_aggregate_jobs_dedupes_by_canonical_url_keeps_lower_tier() -> None:
    tier1 = _result(
        provider="you_com",
        apply_url="https://boards.greenhouse.io/acme/jobs/1?ref=a",
        source_tier=1.0,
        title="Backend Engineer @ tier1",
    )
    tier2 = _result(
        provider="serper",
        apply_url="https://boards.greenhouse.io/acme/jobs/1?ref=b",
        source_tier=2.0,
        title="Backend Engineer @ tier2",
    )

    result = aggregate_jobs([tier2, tier1])

    assert len(result) == 1
    assert result[0].title == "Backend Engineer @ tier1"  # lower tier survived


def test_aggregate_jobs_dedupes_by_company_title_across_different_urls() -> None:
    a = _result(apply_url="https://boards.greenhouse.io/acme/jobs/1", source_tier=1.0)
    b = _result(apply_url="https://jobs.lever.co/acme/2", source_tier=1.5)

    result = aggregate_jobs([a, b])

    assert len(result) == 1


def test_aggregate_jobs_backfills_salary_from_dropped_duplicate() -> None:
    kept = _result(
        apply_url="https://boards.greenhouse.io/acme/jobs/1",
        source_tier=1.0,
        salary_min=None,
        salary_max=None,
    )
    dropped = _result(
        apply_url="https://boards.greenhouse.io/acme/jobs/1",
        source_tier=2.0,
        salary_min=100000,
        salary_max=140000,
        salary_currency="USD",
    )

    result = aggregate_jobs([kept, dropped])

    assert len(result) == 1
    assert result[0].salary_min == 100000
    assert result[0].salary_currency == "USD"


def test_aggregate_jobs_backfills_sponsorship_and_location() -> None:
    kept = _result(
        apply_url="https://boards.greenhouse.io/acme/jobs/1",
        source_tier=1.0,
        sponsorship_signal="unknown",
        location=None,
    )
    dropped = _result(
        apply_url="https://boards.greenhouse.io/acme/jobs/1",
        source_tier=2.0,
        sponsorship_signal="explicit_yes",
        location="New York, NY",
    )

    result = aggregate_jobs([kept, dropped])

    assert result[0].sponsorship_signal == "explicit_yes"
    assert result[0].location == "New York, NY"


def test_aggregate_jobs_never_overwrites_kept_result_own_known_fields() -> None:
    """Backfill only fills GAPS -- a kept result's own real salary/
    location must never be clobbered by a dropped duplicate's data."""
    kept = _result(
        apply_url="https://boards.greenhouse.io/acme/jobs/1",
        source_tier=1.0,
        salary_min=150000,
        location="San Francisco, CA",
    )
    dropped = _result(
        apply_url="https://boards.greenhouse.io/acme/jobs/1",
        source_tier=2.0,
        salary_min=50000,
        location="Nowhere",
    )

    result = aggregate_jobs([kept, dropped])

    assert result[0].salary_min == 150000
    assert result[0].location == "San Francisco, CA"


def test_aggregate_jobs_keeps_distinct_jobs_separate() -> None:
    a = _result(apply_url="https://boards.greenhouse.io/acme/jobs/1", company="Acme")
    b = _result(apply_url="https://jobs.lever.co/notion/2", company="Notion", title="Designer")

    result = aggregate_jobs([a, b])

    assert len(result) == 2


# ── aggregator demotion ───────────────────────────────────────────────────


def test_aggregate_jobs_demotes_and_drops_aggregator_companies() -> None:
    real = _result(apply_url="https://boards.greenhouse.io/acme/jobs/1", company="Acme")
    agg = _result(
        apply_url="https://jobs.lever.co/toptal/2",
        company="Toptal",  # exact match against the AGGREGATORS set
        source_tier=1.0,  # classified tier 1 by URL, should still get demoted
        title="Different Title",
    )

    result = aggregate_jobs([real, agg])

    assert len(result) == 1
    assert result[0].company == "Acme"


def test_aggregate_jobs_aggregator_match_is_case_insensitive_exact_not_substring() -> None:
    # "Turing Labs" should NOT match the aggregator "turing" (exact match only).
    not_aggregator = _result(
        apply_url="https://boards.greenhouse.io/turinglabs/jobs/1", company="Turing Labs"
    )

    result = aggregate_jobs([not_aggregator])

    assert len(result) == 1
    assert result[0].source_tier == 1.0  # not demoted


# ── tier filter ───────────────────────────────────────────────────────────


def test_aggregate_jobs_drops_tier_3_and_above() -> None:
    tier2_5 = _result(apply_url="https://acme.com/careers/1", source_tier=2.5)
    tier3 = _result(apply_url="https://linkedin.com/jobs/2", source_tier=3.0, company="Other")
    tier4 = _result(apply_url="https://example.com/3", source_tier=4.0, company="Third")

    result = aggregate_jobs([tier2_5, tier3, tier4])

    assert len(result) == 1
    assert result[0].source_tier == 2.5


# ── sort ──────────────────────────────────────────────────────────────────


def test_aggregate_jobs_sorts_by_tier_then_recency_by_default() -> None:
    older_tier1 = _result(
        apply_url="https://boards.greenhouse.io/a/1",
        company="A",
        source_tier=1.0,
        posted_at="2026-08-01T00:00:00Z",
    )
    newer_tier2 = _result(
        apply_url="https://jobs.lever.co/b/2",
        company="B",
        source_tier=2.0,
        posted_at="2026-08-30T00:00:00Z",
    )
    newer_tier1 = _result(
        apply_url="https://jobs.lever.co/c/3",
        company="C",
        source_tier=1.0,
        posted_at="2026-08-30T00:00:00Z",
    )

    result = aggregate_jobs([older_tier1, newer_tier2, newer_tier1])

    assert [r.company for r in result] == ["C", "A", "B"]  # tier 1 first, newest within tier


def test_aggregate_jobs_sort_by_newest_ignores_tier() -> None:
    older_tier1 = _result(
        apply_url="https://boards.greenhouse.io/a/1",
        company="A",
        source_tier=1.0,
        posted_at="2026-08-01T00:00:00Z",
    )
    newer_tier2 = _result(
        apply_url="https://jobs.lever.co/b/2",
        company="B",
        source_tier=2.0,
        posted_at="2026-08-30T00:00:00Z",
    )

    result = aggregate_jobs([older_tier1, newer_tier2], sort_by="newest")

    assert [r.company for r in result] == ["B", "A"]


def test_aggregate_jobs_unparseable_date_sorts_as_oldest() -> None:
    good_date = _result(
        apply_url="https://boards.greenhouse.io/a/1",
        company="A",
        posted_at="2026-08-01T00:00:00Z",
    )
    bad_date = _result(
        apply_url="https://jobs.lever.co/b/2",
        company="B",
        posted_at="Mar 10, 2022",  # Serper's occasional human-readable format
    )

    result = aggregate_jobs([good_date, bad_date])

    assert [r.company for r in result] == ["A", "B"]  # good date sorts before unparseable


# ── cap ───────────────────────────────────────────────────────────────────


def test_aggregate_jobs_caps_at_150() -> None:
    many = [
        _result(apply_url=f"https://boards.greenhouse.io/co{i}/jobs/1", company=f"Co{i}")
        for i in range(200)
    ]

    result = aggregate_jobs(many)

    assert len(result) == 150


def test_aggregate_jobs_empty_input_returns_empty() -> None:
    assert aggregate_jobs([]) == []


# ── P5b: cohort lookup ───────────────────────────────────────────────────


def test_expand_cohort_maango() -> None:
    assert expand_cohort("MAANGO") == ["Meta", "Anthropic", "Amazon", "Nvidia", "Google", "OpenAI"]


def test_expand_cohort_faang_and_maang_are_the_same_list() -> None:
    assert expand_cohort("faang") == expand_cohort("MAANG")


def test_expand_cohort_is_case_insensitive() -> None:
    assert expand_cohort("big 4") == expand_cohort("BIG 4")


def test_expand_cohort_unknown_name_returns_none() -> None:
    assert expand_cohort("not a real cohort") is None


def test_expand_cohort_dream_has_36_companies() -> None:
    dream = expand_cohort("dream")
    assert dream is not None
    assert len(dream) == 36


# ── P5b: company filter ──────────────────────────────────────────────────


def test_filter_by_companies_matches_fuzzy_both_directions() -> None:
    jpmorgan = _result(company="JPMorgan Chase & Co.", apply_url="https://a.com/1")
    other = _result(company="Random Corp", apply_url="https://b.com/2")

    result = filter_by_companies([jpmorgan, other], ["JPMorgan"])

    assert result == [jpmorgan]


def test_filter_by_companies_empty_list_is_a_no_op() -> None:
    results = [_result(apply_url="https://a.com/1"), _result(apply_url="https://b.com/2")]

    assert filter_by_companies(results, []) == results


def test_filter_by_companies_drops_results_with_no_company() -> None:
    no_company = _result(company=None, apply_url="https://a.com/1")

    assert filter_by_companies([no_company], ["Meta"]) == []


def test_filter_by_companies_normalizes_punctuation() -> None:
    result = _result(company="P&G", apply_url="https://a.com/1")

    assert filter_by_companies([result], ["p g"]) == [result]


# ── P5b: role filter ──────────────────────────────────────────────────────


def test_filter_by_role_requires_all_query_terms() -> None:
    match = _result(title="Senior Backend Engineer", apply_url="https://a.com/1")
    no_match = _result(title="Senior Frontend Engineer", apply_url="https://b.com/2")

    result = filter_by_role([match, no_match], "backend engineer")

    assert result == [match]


def test_filter_by_role_uses_word_boundaries_not_substrings() -> None:
    """Real n8n bug (F2): naive substring matching let "AI Engineering
    Intern" match role "AI Engineer" via "engineer" inside
    "engineering." Word-boundary regex must not repeat that."""
    intern = _result(title="AI Engineering Intern", apply_url="https://a.com/1")
    real_match = _result(title="AI Engineer II", apply_url="https://b.com/2")

    result = filter_by_role([intern, real_match], "AI Engineer")

    assert result == [real_match]


def test_filter_by_role_empty_query_is_a_no_op() -> None:
    results = [_result(apply_url="https://a.com/1"), _result(apply_url="https://b.com/2")]

    assert filter_by_role(results, "") == results


def test_filter_by_role_excludes_matching_excluded_terms() -> None:
    engineer = _result(title="Backend Engineer", apply_url="https://a.com/1")
    support = _result(title="Backend Support Engineer", apply_url="https://b.com/2")

    result = filter_by_role(
        [engineer, support], "engineer", excluded_terms=["backend support engineer"]
    )

    assert result == [engineer]


def test_filter_by_role_single_letter_terms_are_ignored() -> None:
    # "a" is filtered out as noise (length > 1 threshold, matches n8n's own).
    results = [_result(title="Engineer A", apply_url="https://a.com/1")]

    assert filter_by_role(results, "a") == results


@pytest.mark.parametrize(
    ("title", "query"),
    [
        ("Senior .NET Developer", ".NET developer"),
        ("ASP.NET Developer", ".NET developer"),  # `\b` matched this one, so it must keep matching
        ("C# Backend Developer", "c# developer"),
        ("C++ Software Engineer", "c++ engineer"),
        ("Sr. Software Engineer", "Sr. Software Engineer"),
        ("Node.js Developer", "node.js developer"),
        ("Hiring: C++/Rust Engineer", "c++ engineer"),
    ],
)
def test_filter_by_role_matches_terms_that_start_or_end_with_a_symbol(
    title: str, query: str
) -> None:
    """`\\b` needs a word character next to it, so these terms could never match
    ("hiring a .NET developer" failed `.net`) -- the role filter silently dropped
    every post for a mainstream engineering role."""
    hit = _result(title=title, apply_url="https://a.com/1")
    other = _result(title="Senior Frontend Designer", apply_url="https://b.com/2")

    assert filter_by_role([hit, other], query) == [hit]


@pytest.mark.parametrize(
    ("title", "query"),
    [
        ("Abc++ Developer", "c++ developer"),  # a word character may not precede a word term
        ("AI Engineering Intern", "ai engineer"),
        ("Backend Engineers", "backend engineer"),
        ("Senior .NETWORK Developer", ".net developer"),  # nothing word-like may follow a term
        ("Xnet Developer", ".net developer"),  # the literal dot is still required
    ],
)
def test_filter_by_role_symbol_aware_boundaries_still_refuse_partial_words(
    title: str, query: str
) -> None:
    hit = _result(title=title, apply_url="https://a.com/1")

    assert filter_by_role([hit], query) == []


def test_role_term_patterns_boundaries() -> None:
    assert [p.pattern for p in role_term_patterns("Software Engineer")] == [
        r"(?<!\w)software(?!\w)",
        r"(?<!\w)engineer(?!\w)",
    ]
    # a term that begins with a symbol has no left boundary; every term keeps a right one
    assert [p.pattern for p in role_term_patterns(".net c++")] == [
        r"\.net(?!\w)",
        r"(?<!\w)c\+\+(?!\w)",
    ]
    # single characters are dropped, as in n8n
    assert role_term_patterns("a b c") == []


def test_role_term_patterns_match_a_term_ending_in_a_devanagari_vowel_sign() -> None:
    # U+0940 is a combining mark, not a `\\w` character: `\\b` after it never held
    patterns = role_term_patterns("नौकरी")
    assert matches_all_role_terms("यह नौकरी अच्छी है", patterns)
    assert not matches_all_role_terms("यह xनौकरी अच्छी है", patterns)


def test_matches_all_role_terms_requires_every_pattern_and_lowercases_the_haystack() -> None:
    patterns = role_term_patterns("backend engineer")
    assert matches_all_role_terms("Senior BACKEND Engineer", patterns)
    assert not matches_all_role_terms("Senior Backend Manager", patterns)
    assert matches_all_role_terms("anything", [])  # vacuous: callers check for emptiness first


# ── P5c: registry-lane query ─────────────────────────────────────────────


class _FakeRpcBuilder:
    def __init__(self, data: Any) -> None:
        self._data = data

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._data)


class _FakeSupabase:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.rpc_calls: list[tuple[str, dict[str, Any]]] = []

    def rpc(self, name: str, params: dict[str, Any]) -> _FakeRpcBuilder:
        self.rpc_calls.append((name, params))
        return _FakeRpcBuilder(self.rows)


def _registry_row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "title": "Backend Engineer",
        "company_name": "Acme",
        "location": "Remote",
        "remote": True,
        "apply_url": "https://boards.greenhouse.io/acme/jobs/1",
        "posted_at": "2026-08-20T00:00:00+00:00",
        "salary_min": 120000,
        "salary_max": 160000,
        "salary_currency": "USD",
        "sponsorship_signal": "unknown",
        "snippet": "Great backend role.",
    }
    base.update(overrides)
    return base


async def test_fetch_registry_lane_maps_fields_and_forces_tier_1() -> None:
    supabase = _FakeSupabase([_registry_row()])

    results = await fetch_registry_lane(supabase, query="backend engineer")  # type: ignore[arg-type]

    assert len(results) == 1
    r = results[0]
    assert r.provider == "registry"
    assert r.title == "Backend Engineer"
    assert r.company == "Acme"
    assert r.source_tier == 1.0  # always tier 1, unconditionally
    assert r.salary_min == 120000
    assert r.link_checked is True  # never routed through ats_liveness


async def test_fetch_registry_lane_passes_query_and_limit_through() -> None:
    supabase = _FakeSupabase([])

    await fetch_registry_lane(supabase, query="engineer", limit=50)  # type: ignore[arg-type]

    assert supabase.rpc_calls == [
        ("search_job_registry_postings", {"search_query": "engineer", "result_limit": 50})
    ]


async def test_fetch_registry_lane_empty_query_still_calls_through() -> None:
    """An empty query means "browse recent," matching discovery_store.py's
    own existing no-query behavior -- the SQL function itself (not this
    Python layer) decides to skip the tsvector filter."""
    supabase = _FakeSupabase([_registry_row()])

    results = await fetch_registry_lane(supabase, query="")  # type: ignore[arg-type]

    assert len(results) == 1
    assert supabase.rpc_calls[0][1]["search_query"] == ""


async def test_fetch_registry_lane_no_results() -> None:
    supabase = _FakeSupabase([])

    results = await fetch_registry_lane(supabase, query="nonexistent role")  # type: ignore[arg-type]

    assert results == []


# ── P5d: gazetteer-backed location filter ────────────────────────────────


def _test_gazetteer() -> Gazetteer:
    return build_gazetteer(
        [
            {
                "name": "New York",
                "ascii_name": "New York",
                "alt_names": ["NYC"],
                "country_code": "US",
                "population": 8000000,
            },
            {
                "name": "Berlin",
                "ascii_name": "Berlin",
                "alt_names": [],
                "country_code": "DE",
                "population": 3600000,
            },
        ]
    )


def test_filter_by_location_drops_confirmed_mismatches() -> None:
    us_job = _result(location="New York, NY", apply_url="https://a.com/1")
    de_job = _result(location="Berlin, Germany", apply_url="https://b.com/2", company="Other")

    result = filter_by_location([us_job, de_job], "New York", gazetteer=_test_gazetteer())

    assert len(result) == 1
    assert result[0].location == "New York, NY"


def test_filter_by_location_stamps_location_verified_true_on_match() -> None:
    us_job = _result(location="New York, NY", apply_url="https://a.com/1")

    result = filter_by_location([us_job], "New York", gazetteer=_test_gazetteer())

    assert result[0].location_verified is True


def test_filter_by_location_stamps_none_on_unknown_not_false() -> None:
    unknown_job = _result(location="Zzyzxville", apply_url="https://a.com/1")

    result = filter_by_location([unknown_job], "New York", gazetteer=_test_gazetteer())

    assert len(result) == 1  # unknown is kept, never dropped
    assert result[0].location_verified is None  # never False -- mismatches are dropped, not flagged


def test_filter_by_location_no_op_when_remote_only() -> None:
    de_job = _result(location="Berlin, Germany", apply_url="https://b.com/2")

    result = filter_by_location([de_job], "New York", gazetteer=_test_gazetteer(), remote_only=True)

    assert result == [de_job]


def test_filter_by_location_no_op_when_no_location_given() -> None:
    jobs = [_result(apply_url="https://a.com/1"), _result(apply_url="https://b.com/2")]

    assert filter_by_location(jobs, None, gazetteer=_test_gazetteer()) == jobs


def test_filter_by_location_no_op_with_empty_gazetteer() -> None:
    empty = Gazetteer(city_index={}, country_alias_to_code={}, country_names={})
    de_job = _result(location="Berlin, Germany", apply_url="https://b.com/2")

    result = filter_by_location([de_job], "New York", gazetteer=empty)

    assert result == [de_job]


def test_filter_by_location_no_op_when_request_location_does_not_resolve() -> None:
    de_job = _result(location="Berlin, Germany", apply_url="https://b.com/2")

    result = filter_by_location([de_job], "Zzyzxville Nowhereland", gazetteer=_test_gazetteer())

    assert result == [de_job]


def test_aggregate_jobs_sorts_location_verified_first() -> None:
    unverified_tier1 = _result(
        apply_url="https://a.com/1", company="A", source_tier=1.0, location_verified=None
    )
    verified_tier2 = _result(
        apply_url="https://b.com/2", company="B", source_tier=2.0, location_verified=True
    )

    result = aggregate_jobs([unverified_tier1, verified_tier2])

    assert [r.company for r in result] == ["B", "A"]  # verified wins even over a better tier


# ── apply_search_filters: the shared 3-step chain (code-review-fixes.md 4c) ──


class _FakeGazetteerTable:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def select(self, *_: Any, **__: Any) -> _FakeGazetteerTable:
        return self

    def order(self, *_: Any, **__: Any) -> _FakeGazetteerTable:
        return self

    def range(self, start: int, end: int) -> _FakeGazetteerTable:
        return _FakeGazetteerTable(self._rows[start : end + 1])

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeSupabaseForFilters:
    def __init__(self, gazetteer_rows: list[dict[str, Any]]) -> None:
        self._gazetteer_rows = gazetteer_rows
        self.gazetteer_calls = 0

    def table(self, name: str) -> _FakeGazetteerTable:
        assert name == "geo_gazetteer_cities"
        self.gazetteer_calls += 1
        return _FakeGazetteerTable(self._gazetteer_rows)


async def test_apply_search_filters_applies_companies_and_role_no_location() -> None:
    geo_gazetteer_module._cached_gazetteer = None
    jobs = [
        _result(apply_url="https://a.com/1", company="Anthropic", title="Backend Engineer"),
        _result(apply_url="https://b.com/2", company="OtherCo", title="Backend Engineer"),
    ]
    supabase = _FakeSupabaseForFilters([])

    filtered, gazetteer_active = await apply_search_filters(
        jobs,
        supabase=supabase,  # type: ignore[arg-type]
        companies=["Anthropic"],
        query="backend engineer",
        location=None,
        remote_only=False,
    )

    assert [r.company for r in filtered] == ["Anthropic"]
    assert gazetteer_active is False
    assert supabase.gazetteer_calls == 0  # never fetched -- no location/remote_only given
    geo_gazetteer_module._cached_gazetteer = None


async def test_apply_search_filters_gazetteer_active_true_when_populated() -> None:
    geo_gazetteer_module._cached_gazetteer = None
    jobs = [_result(apply_url="https://a.com/1", location="New York, NY")]
    supabase = _FakeSupabaseForFilters(
        [
            {
                "name": "New York City",
                "ascii_name": "New York City",
                "alt_names": ["New York"],
                "country_code": "US",
                "population": 8000000,
            }
        ]
    )

    _filtered, gazetteer_active = await apply_search_filters(
        jobs,
        supabase=supabase,  # type: ignore[arg-type]
        companies=None,
        query="",
        location="New York",
        remote_only=False,
    )

    assert gazetteer_active is True
    geo_gazetteer_module._cached_gazetteer = None


async def test_apply_search_filters_gazetteer_active_false_when_empty() -> None:
    """The fail-open case (code-review-fixes.md 2c): a location was
    requested, but the gazetteer table is empty/unreachable, so the
    filter genuinely never ran -- gazetteer_active must reflect that
    honestly rather than claim a filter happened."""
    geo_gazetteer_module._cached_gazetteer = None
    jobs = [_result(apply_url="https://a.com/1", location="New York, NY")]
    supabase = _FakeSupabaseForFilters([])

    filtered, gazetteer_active = await apply_search_filters(
        jobs,
        supabase=supabase,  # type: ignore[arg-type]
        companies=None,
        query="",
        location="New York",
        remote_only=False,
    )

    assert gazetteer_active is False
    assert filtered == jobs  # no-op, nothing dropped
    geo_gazetteer_module._cached_gazetteer = None


async def test_apply_search_filters_remote_only_short_circuits_location() -> None:
    geo_gazetteer_module._cached_gazetteer = None
    jobs = [_result(apply_url="https://a.com/1", location="Berlin, Germany")]
    supabase = _FakeSupabaseForFilters(
        [
            {
                "name": "New York City",
                "ascii_name": "New York City",
                "alt_names": [],
                "country_code": "US",
                "population": 8000000,
            }
        ]
    )

    filtered, gazetteer_active = await apply_search_filters(
        jobs,
        supabase=supabase,  # type: ignore[arg-type]
        companies=None,
        query="",
        location=None,
        remote_only=True,
    )

    # remote_only short-circuits filter_by_location to a no-op even
    # though the gazetteer itself is populated (matches n8n's own
    # remotePref !== 'remote_only' guard).
    assert filtered == jobs
    assert gazetteer_active is True
    geo_gazetteer_module._cached_gazetteer = None
