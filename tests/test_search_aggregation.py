"""Tests for canonical dedup, aggregator demotion, tier filtering, sort,
cohort/role filtering, the registry lane, and (see the dedicated section) the
gazetteer-backed location filter. Ported verbatim from n8n's real
Aggregate Jobs node (aggregate_jobs.js)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from place_fixtures import places_gazetteer as _places_gazetteer

from between_jobs.api import geo_gazetteer as geo_gazetteer_module
from between_jobs.api.geo_gazetteer import Gazetteer, build_gazetteer
from between_jobs.api.location_place import Place, read_place
from between_jobs.api.search_aggregation import (
    _canonicalize_url,
    _dedup_keys,
    aggregate_jobs,
    apply_search_filters,
    expand_cohort,
    fetch_registry_lane,
    fetch_registry_last_polled_at,
    filter_by_companies,
    filter_by_location,
    filter_by_role,
    matches_all_role_terms,
    rejudge_corrected_locations,
    role_term_patterns,
    sort_results,
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


# ── cohort lookup ───────────────────────────────────────────────────


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


# ── company filter ──────────────────────────────────────────────────


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


# ── role filter ──────────────────────────────────────────────────────


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


# ── registry-lane query ─────────────────────────────────────────────


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
        "link_fresh": True,
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
    assert r.link_checked is True  # the database said the link is fresh (P0.8)


async def test_fetch_registry_lane_link_checked_follows_the_databases_link_fresh() -> None:
    """P0.8: a fresh posting on a recently polled board is checked; one on a board that
    was not polled in time (or has been failing) is still returned, just not checked."""
    supabase = _FakeSupabase(
        [
            _registry_row(apply_url="https://boards.greenhouse.io/acme/jobs/1", link_fresh=True),
            _registry_row(apply_url="https://boards.greenhouse.io/acme/jobs/2", link_fresh=False),
        ]
    )

    results = await fetch_registry_lane(supabase, query="engineer")  # type: ignore[arg-type]

    assert [(r.apply_url[-1], r.link_checked) for r in results] == [("1", True), ("2", False)]
    assert all(r.provider == "registry" and r.source_tier == 1.0 for r in results)


async def test_fetch_registry_lane_on_an_unmigrated_database_degrades_to_unverified(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Until the P0.8 migration is applied the function returns no link_fresh column. That
    must show every posting as unverified (never verified), not crash the Discover request --
    and say so in the log, so a missing migration does not hide."""
    row = _registry_row()
    del row["link_fresh"]
    supabase = _FakeSupabase([row])

    with caplog.at_level("WARNING"):
        results = await fetch_registry_lane(supabase, query="engineer")  # type: ignore[arg-type]

    assert [r.link_checked for r in results] == [False]
    assert "link_fresh" in caplog.text


@pytest.mark.parametrize("value", [None, 0, 1, "true", "yes"])
async def test_only_a_real_true_makes_a_registry_result_checked(value: object) -> None:
    supabase = _FakeSupabase([_registry_row(link_fresh=value)])

    results = await fetch_registry_lane(supabase, query="engineer")  # type: ignore[arg-type]

    assert results[0].link_checked is False


async def test_fetch_registry_last_polled_at_parses_the_timestamp() -> None:
    supabase = _FakeSupabase("2026-10-01T12:00:00+00:00")  # type: ignore[arg-type]

    got = await fetch_registry_last_polled_at(supabase)  # type: ignore[arg-type]

    assert got == datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    assert supabase.rpc_calls == [("registry_last_polled_at", {})]


async def test_fetch_registry_last_polled_at_treats_a_naive_timestamp_as_utc() -> None:
    got = await fetch_registry_last_polled_at(_FakeSupabase("2026-10-01T12:00:00"))  # type: ignore[arg-type]

    assert got == datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


async def test_fetch_registry_last_polled_at_is_none_when_nothing_was_ever_polled() -> None:
    assert await fetch_registry_last_polled_at(_FakeSupabase(None)) is None  # type: ignore[arg-type]


async def test_fetch_registry_last_polled_at_refuses_an_answer_that_is_not_a_timestamp() -> None:
    """ "I could not read it" must not be reported as "never polled"."""
    with pytest.raises(TypeError):
        await fetch_registry_last_polled_at(_FakeSupabase(12345))  # type: ignore[arg-type]


async def test_fetch_registry_lane_passes_query_and_limit_through() -> None:
    supabase = _FakeSupabase([])

    await fetch_registry_lane(supabase, query="engineer", limit=50)  # type: ignore[arg-type]

    assert supabase.rpc_calls == [
        ("search_job_registry_postings", {"search_query": "engineer", "result_limit": 50})
    ]


async def test_fetch_registry_lane_empty_query_still_calls_through() -> None:
    """An empty query means "browse recent": the SQL function itself (not
    this Python layer) decides to skip the tsvector filter."""
    supabase = _FakeSupabase([_registry_row()])

    results = await fetch_registry_lane(supabase, query="")  # type: ignore[arg-type]

    assert len(results) == 1
    assert supabase.rpc_calls[0][1]["search_query"] == ""


async def test_fetch_registry_lane_no_results() -> None:
    supabase = _FakeSupabase([])

    results = await fetch_registry_lane(supabase, query="nonexistent role")  # type: ignore[arg-type]

    assert results == []


# ── gazetteer-backed location filter ────────────────────────────────


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


def test_rejudge_corrected_locations_confirms_a_board_api_match() -> None:
    job = _result(location="New York, NY", apply_url="https://a.com/1", location_source="ats_api")

    result = rejudge_corrected_locations([job], "New York", gazetteer=_test_gazetteer())

    assert [r.location for r in result] == ["New York, NY"]
    assert result[0].location_verified is True


@pytest.mark.parametrize("job_location", ["Berlin, Germany", "Zzyzxville"])
def test_rejudge_corrected_locations_never_drops_a_board_api_result(job_location: str) -> None:
    """A probe reads one location per posting and the gazetteer can misread a place, so a mismatch
    or a place it cannot read keeps the result in, as unknown, instead of removing it."""
    job = _result(
        location=job_location,
        apply_url="https://a.com/1",
        location_source="ats_api",
        location_verified=True,
    )

    result = rejudge_corrected_locations([job], "New York", gazetteer=_test_gazetteer())

    assert [r.location for r in result] == [job_location]
    assert result[0].location_verified is None


@pytest.mark.parametrize("job_location", ["New York, NY", "Berlin, Germany", "Zzyzxville"])
def test_rejudge_corrected_locations_leaves_a_page_read_location_alone(job_location: str) -> None:
    """A location scraped from a job page is a claim by whatever host served the page: it is not
    confirmed, and it is not a reason to hide the result either."""
    job = _result(location=job_location, apply_url="https://a.com/1", location_source="ats_page")

    result = rejudge_corrected_locations([job], "New York", gazetteer=_test_gazetteer())

    assert result == [job]
    assert result[0].location_verified is None


def test_rejudge_corrected_locations_leaves_an_uncorrected_result_alone() -> None:
    """The first pass already judged these. A provider's own location that happens to match is
    not this pass's to confirm, and a provider's own confirmed match is not this pass's to
    withdraw, even when the location would read as a mismatch now."""
    matches_but_unconfirmed = _result(location="New York, NY", apply_url="https://a.com/1")
    confirmed_elsewhere = _result(
        location="Berlin, Germany", apply_url="https://b.com/2", location_verified=True
    )

    result = rejudge_corrected_locations(
        [matches_but_unconfirmed, confirmed_elsewhere], "New York", gazetteer=_test_gazetteer()
    )

    assert result == [matches_but_unconfirmed, confirmed_elsewhere]
    assert [r.location_verified for r in result] == [None, True]


@pytest.mark.parametrize(
    ("search", "board_location"),
    [
        # a large city that no place in another country shares, or the country named
        ("New York", "New York, NY"),
        ("New York", "New York"),
        ("New York", "New York, New York"),
        ("New York City", "New York   City, NY"),  # whitespace inside a name is collapsed
        ("New York", "Remote - New York"),
        ("New York", "Remote: New York, NY"),
        ("New York", "Remote \u2013 New York, NY"),
        ("New York City", "New York City, NY"),
        ("Egypt", "Cairo"),
        ("Toronto", "Toronto, ON"),
        ("Toronto", "Toronto, Ontario"),
        ("United Kingdom", "Cambridge, United Kingdom"),
        ("Cambridge, United Kingdom", "Cambridge, United Kingdom"),
        # a state code or name between the city and the named country is a region
        ("United States", "Cambridge, MA, United States"),
        ("Cambridge, United States", "Cambridge, MA, United States"),
        ("Portland, OR", "Portland, Oregon, United States"),
        ("Portland", "Portland, Oregon, United States"),
        ("Portland, Oregon, United States", "Portland, OR"),
        ("Portland, Oregon, United States", "Portland, Oregon, United States"),
        ("United States", "Remote - US"),
        ("United States", "Remote (US), USA"),
        ("United States", "Austin, TX"),
        ("United States", "Austin, Texas"),
        ("Austin", "Austin, TX"),  # several rows, all in one country
        ("Austin, Texas", "Austin, TX"),
        ("Dallas, TX", "Dallas, TX"),
        ("Portland, ME", "Portland, ME"),
        ("Dallas", "Dallas, OR (Remote)"),  # Oregon's code, not the word "or"
        ("Dallas, OR", "Dallas, OR (Remote)"),
        # a search that names no region is satisfied by any region of the city
        ("Austin", "Austin, MN"),
        # a town the gazetteer lacks, with a state and the country the text names
        ("United States", "Smalltown, TX, United States"),
        # a part that is not read leaves the country established: a country search is confirmed
        ("United States", "Portland, Downtown, United States"),
        ("United States", "Austin, PK, United States"),  # a code that is no state is not read
        # "CA" after a Canadian city is Canada's own code, or at worst a region Canada can have
        ("Toronto", "Toronto, CA"),
        ("Canada", "Toronto, CA"),
        # the same country written twice adds nothing, and a probe appends the board's own country
        # to a location that may already end in it
        ("United States", "Austin, TX, United States, United States"),
        ("Austin, TX", "Austin, TX, United States, United States"),
        ("United Kingdom", "London, United Kingdom, United Kingdom"),
        ("United Arab Emirates", "UAE, United Arab Emirates"),
        # the country's name at the end of a place, and a name for part of a country there too
        ("United Kingdom", "London, England"),
        ("United Arab Emirates", "Dubai, United Arab Emirates"),
        ("Dubai", "Dubai"),
        ("Dubai, United Arab Emirates", "Dubai, United Arab Emirates"),
        # a US state code beside another country's city is an unread part, not that country's
        ("Brazil", "Sao Paulo, SP, Brazil"),
        ("Brazil", "Recife, PE, Brazil"),
        ("India", "Chennai, TN, India"),
        # an accent-free spelling is the city's name without its accents; the accented one exactly
        ("S\u00e3o Paulo", "Sao Paulo"),
        ("Spain", "Malaga"),
        ("Spain", "M\u00e1laga"),
        ("Malaga", "M\u00e1laga, Spain"),
        # Oregon's code before a work mode or the end is not the word "or"
        ("Dallas, OR", "Dallas, OR "),
        # a country's name that is also a town's, first and last with a region between
        ("Singapore", "Singapore, South West, Singapore"),
        # "USA" and "India" are alternate names of towns, and here are the countries
        ("USA", "Remote - USA"),
        ("USA", "Remote - US"),
        ("USA", "Austin, TX"),
        ("India", "Pune, India"),
        ("India", "Remote - India"),
        ("Nigeria", "Benin City"),
        # a large city under its own name, with or without its accents; the floor is inclusive
        ("China", "Beijing"),
        ("Brazil", "Sao Paulo"),
        ("Brazil", "S\u00e3o Paulo"),
        ("United States", "Exactville"),
    ],
)
def test_rejudge_corrected_locations_confirms_evidence_for_the_geography_searched(
    search: str, board_location: str
) -> None:
    job = _result(location=board_location, apply_url="https://a.com/1", location_source="ats_api")

    result = rejudge_corrected_locations([job], search, gazetteer=_places_gazetteer())

    assert [r.location for r in result] == [board_location]
    assert result[0].location_verified is True


@pytest.mark.parametrize(
    ("search", "board_location"),
    [
        # a name shared by several countries, not qualified by one: the largest is a guess
        ("United Kingdom", "Cambridge, MA"),
        ("United States", "Cambridge, MA"),
        ("Cambridge", "Cambridge, MA"),
        # a name large in two countries: nothing else blocks these, only the two countries do
        ("Venezuela", "Valencia"),
        ("Spain", "Valencia"),
        # a bare name that a place in another country shares, though the other one is smaller
        ("United Kingdom", "London"),
        ("Canada", "London"),
        # a name that is large in one country and shared with a small place in another
        ("United States", "Boston, MA"),
        ("Boston", "Boston, MA"),
        ("Boston", "Boston"),  # the same bare name on both sides is still two countries' worth
        ("United Kingdom", "Boston"),
        # another place in the searched city's country, or the same name in another region
        ("New York", "Austin, TX"),
        ("Dallas, TX", "Dallas, OR"),
        ("Portland, ME", "Portland, OR"),
        ("Portland, ME", "Portland, Oregon, United States"),
        ("Portland, Maine", "Portland, OR"),
        # the city is the text a person wrote, not the gazetteer's city: two names of one differ
        ("New York", "New York City, NY"),
        ("New York City", "New York, NY"),
        # a search that names a region is not satisfied by a board that names none
        ("Dallas, TX", "Dallas"),
        ("Dallas, TX", "Dallas, OR (Remote)"),
        # a part between the city and the country that is not read leaves the city unknown, so no
        # city search is confirmed by it, and a search that has one is unreadable
        ("Portland", "Portland, Downtown, United States"),
        ("Portland, OR", "Portland, Downtown, United States"),
        ("Portland, Downtown, United States", "Portland, OR"),
        ("Portland, Downtown, United States", "Portland, Downtown, United States"),
        # a place that is only a state, or a country-level location, for a search about a city
        ("New York", "United States"),
        ("New York", "Remote"),
        ("Austin, Texas", "Texas"),
        # a country the board names that the city cannot be in, and the other country
        ("Ireland", "Austin, Ireland"),
        ("New York", "New York, United Kingdom"),
        ("United States", "Cambridge, United Kingdom"),
        # an alternate name shared with a town in another country is not evidence
        ("Germany", "Augusta, GA"),
        # a state belongs to its own country, and a code that is neither a state nor a province
        # is not a region
        ("Canada", "Toronto, OH"),
        ("United States", "Toronto, OH"),
        ("Toronto", "Toronto, OH"),
        ("Toronto", "Toronto, Texas"),
        ("Egypt", "Cairo, ON"),
        ("Egypt", "Cairo, Ontario"),
        ("United States", "Austin, ON"),
        ("Canada", "Austin, ON"),
        ("Colombia", "Bogota, PE"),
        ("Colombia", "Bogota, VE"),
        ("Australia", "Sydney, NSW"),
        ("Georgia", "Tbilisi, Georgia"),  # a state of the same name is not the country
        # a town the gazetteer cannot place beside a smaller foreign namesake of the same name
        ("United Kingdom", "Greenwich, CT"),
        ("United States", "Greenwich, CT"),
        ("United States", "Greenwich"),  # below the population floor, and nothing else blocks it
        ("United States", "Nearville"),  # one below the floor
        ("Egypt", "Cairo, IL"),  # Cairo is the large, only Cairo here: the state belongs to the US
        ("Canada", "Vancouver, WA"),  # Vancouver is the only Vancouver here, and in Canada
        ("United States", "Vancouver, WA"),
        ("United States", "Smallville, NY"),  # below the floor
        # a town the table lacks, with nothing but the country to say that it is a place at all
        ("United States", "Smalltown, United States"),
        # a single word is not read without its trailing "City" ("Benin" is a country; "Bénin" its
        # name in French), though "New York" is read as New York City
        ("Nigeria", "B\u00e9nin"),
        ("Philippines", "Cebu"),
        # an alternate spelling or a code of a large city is not its name
        ("India", "Bombay"),
        ("India", "BOM"),
        ("China", "Peking"),
        ("China", "BJS"),
        ("United States", "NYC"),
        # a name written as a fragment or inside a wrapper is not read, and neither are region
        # words and placeholders that happen to be the names of small towns
        ("Ghana", "Seattle-WA"),
        ("United States", "Seattle-WA"),
        ("Ghana", "Remote, WA"),
        ("Uganda", "Remote - APAC"),
        ("Uganda", "APAC"),
        ("United States", "TBD"),
        ("Canada", "Remote - Ontario"),
        # a region or a placeholder before the country names no place
        ("United States", "Remote - EMEA, United States"),
        ("United States", "Remote (Europe), United States"),
        ("United States", "North America, United States"),
        ("Germany", "Remote - DACH, Germany"),
        # a city with more written after it than a region and a country is not read
        ("United States", "Austin, TX, Remote Office"),
        ("New York", "New York, NY, Hybrid Flex"),
        ("United States", "Austin, TX, United States (anywhere)"),
        ("Canada", "Toronto, OH, Canada"),
        # a text that starts with a country and goes on is a city, not the country, as a board
        # and as a search: it would confirm every board in the country
        ("United States", "US, Texas"),
        ("United States", "US, Paris"),
        ("United States", "Remote - US (East)"),
        ("United States", "Remote - US (Must reside in CA, TX, NY)"),
        ("United Kingdom", "UK Remote, London"),
        ("United States", "United States, Austin, TX, United States"),
        # Oregon's code beside a work mode, without its comma, is not read; nor is "or" before one
        ("Dallas", "Dallas OR (Remote)"),
        ("Dallas, OR", "Dallas OR Remote"),
        ("Austin, TX", "Austin, TX or Remote"),
        ("Seattle, WA", "SEATTLE OR REMOTE"),
        ("Dallas, OR", "DALLAS OR REMOTE"),
        ("Portland, ME", "Portland, OR - Remote"),
        # a search that starts with a country and goes on is not the whole country
        ("US, Texas", "Seattle, WA"),
        ("US (Texas)", "Seattle, WA"),
        ("Remote (US), Texas", "Seattle, WA"),
        ("US, Texas, USA", "Seattle, WA, United States"),
        ("United States, Austin, TX, United States", "Seattle, WA, United States"),
        ("Germany, Berlin, Germany", "Berlin, Germany"),
        ("Canada (Ontario), Canada", "Toronto, ON"),
        # a place the gazetteer has only elsewhere after the country is a list, not a qualifier
        ("United States", "US, Berlin"),
        # two countries are a list, even where a table also has the first one as a town
        ("United States", "Ireland, United States of America"),
        ("India", "India, Serbia"),
        ("United States", "Jamaica, United States"),
        # the state called California is not Canada's code
        ("Canada", "Toronto, California"),
        ("United States", "Toronto, California"),
        # the lower-case word is the conjunction, not Oregon
        ("Dallas, OR", "Dallas, or"),
        # a country code beside other words is not the country
        ("London UK Remote", "London, United Kingdom"),
        ("United Kingdom", "London UK Remote"),
        ("United Kingdom", "UK Remote USA"),
        ("United States", "US \u6771\u4eac"),
        ("US \u6771\u4eac", "Austin, TX"),
        # a name for part of a country is not the country: a Scotland search must not confirm a
        # London board, nor Dubai an Abu Dhabi one
        ("Scotland", "London, UK"),
        ("England", "London, UK"),
        ("Dubai", "Abu Dhabi"),
        ("Abu Dhabi", "Dubai, United Arab Emirates"),
        ("Dubai, United Arab Emirates", "Abu Dhabi, United Arab Emirates"),
        # a state code of the named country's neighbour, with the country that is named
        ("United States", "Chennai, TN, India"),
        ("Spain", "M\u00e0laga"),
        # a parenthesis is read like a comma: Jamaica is the borough of New York, not the country
        ("Jamaica", "Jamaica, NY"),
        ("Jamaica", "Jamaica (NY)"),
        # Oregon's code before a work mode is not the word "or": the town is not the country,
        # with or without the comma; and an "OR" that is followed by a place is the word
        ("Lebanon", "Lebanon, OR (Remote)"),
        ("Lebanon", "Lebanon OR (Remote)"),
        ("Lebanon", "Lebanon OR Remote"),
        ("Canada", "Toronto, Canada (OR United States)"),
        ("United Kingdom", "LONDON, UNITED KINGDOM (OR IRELAND)"),
        ("United States", "Austin, TX, United States, OR Canada"),
        ("United Kingdom", "London, UK, OR France"),
        ("United States", "Canada - OR, US"),
        # several places, several countries, or a list of countries
        ("United States", "Remote - UK or Ireland"),
        ("New York", "New York, NY / Austin, TX"),
        ("New York", "New York, NY OR Austin, TX"),
        ("New York", "New York, NY, or Austin, TX"),
        ("United States", "USA, Canada"),
        ("Canada", "USA, Canada"),
        ("United States", "Remote - US & Canada"),
        ("United States", "US + Canada"),
        ("United States", "US-Canada"),
        ("Canada", "US-Canada"),
        ("United States", "US Virgin Islands"),
        ("United States", "US Remote, United Kingdom"),
        ("United Kingdom", "US Remote, United Kingdom"),
        ("United States", "Canada (or United States)"),
        ("Canada", "Canada (or United States)"),
        ("United States", "Remote - Canada (or United States)"),
        ("United States", "Canada, or United States"),
        ("Germany (or Austria)", "Berlin, Germany"),
        ("United Kingdom (or Ireland)", "London, United Kingdom"),
        # worldwide text, even beside a city and a country
        ("United States", "Anywhere in Europe"),
        ("India", "Global Technology Center, Pune"),
        ("United States", "Global HQ, Austin, TX"),
        ("United States", "Global HQ, Austin, TX, United States"),
        ("United States", "Anywhere, United States"),
        ("India", "Worldwide, India"),
        ("New York", "New York, NY or anywhere"),
        # a search that is itself a guess or a list is held to the same bar
        ("Cambridge, MA", "Cambridge, United Kingdom"),
        ("Cambridge, MA", "Cambridge, MA, United States"),
        ("London or New York", "London, UK"),
        ("London or New York", "New York, NY"),
        # another country than the one named, and a country code that the alias table lacks
        ("India", "Remote - Pakistan"),
        ("London, UK", "London, GB"),
    ],
)
def test_rejudge_corrected_locations_does_not_confirm_what_is_not_evidence(
    search: str, board_location: str
) -> None:
    """Every case stays in the results, unconfirmed: nothing here is dropped."""
    job = _result(location=board_location, apply_url="https://a.com/1", location_source="ats_api")

    result = rejudge_corrected_locations([job], search, gazetteer=_places_gazetteer())

    assert [r.location for r in result] == [board_location]
    assert result[0].location_verified is None


@pytest.mark.parametrize(
    "error", [ValueError, TypeError, IndexError, KeyError, UnicodeError, AttributeError]
)
def test_rejudge_corrected_locations_leaves_a_result_as_unknown_when_its_text_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, error: type[Exception]
) -> None:
    """A reading bug is not the search's to fail, whatever it raises, on the board's side or the
    search's; and it is said once per text in the log, without the text or a traceback."""
    import between_jobs.api.search_aggregation as module

    real = read_place

    def raising(gazetteer: Gazetteer, text: str | None) -> Place | None:
        if text == "Explode":
            raise error(f"leaked {text}")
        return real(gazetteer, text)

    monkeypatch.setattr(module, "read_place", raising)
    exploding = _result(location="Explode", apply_url="https://a.com/1", location_source="ats_api")
    fine = _result(location="Austin, TX", apply_url="https://a.com/2", location_source="ats_api")

    with caplog.at_level("WARNING", logger=module.logger.name):
        on_the_board = rejudge_corrected_locations(
            [exploding, fine], "United States", gazetteer=_places_gazetteer()
        )
        on_the_search = rejudge_corrected_locations(
            [fine], "Explode", gazetteer=_places_gazetteer()
        )

    assert [r.location_verified for r in on_the_board] == [None, True]
    assert on_the_search == [fine]
    warnings = [r for r in caplog.records if "could not read a location text" in r.getMessage()]
    assert len(warnings) == 2
    for record in warnings:
        # the error's type is named; its message (which here carries the text) and the text are not
        assert record.levelname == "WARNING"
        assert error.__name__ in record.getMessage()
        assert "Explode" not in record.getMessage()
        assert "leaked" not in record.getMessage()
        assert record.exc_info is None
        assert "Explode" not in str(record.__dict__)  # nor in a structured field


def test_rejudge_corrected_locations_is_a_no_op_when_nothing_constrains_the_search() -> None:
    """Each call below leaves a board location that matches the search unconfirmed. An empty
    gazetteer and a remote-only search are no-ops even when the text itself names a country, and
    a missing or unresolvable search constrains nothing."""
    job = _result(location="New York, NY", apply_url="https://a.com/1", location_source="ats_api")
    empty = Gazetteer(city_index={}, country_alias_to_code={}, country_names={})

    assert rejudge_corrected_locations([job], "New York", gazetteer=empty) == [job]
    named_country = _result(
        location="Remote - US", apply_url="https://c.com/3", location_source="ats_api"
    )
    assert rejudge_corrected_locations([named_country], "US", gazetteer=empty) == [named_country]
    assert rejudge_corrected_locations([job], None, gazetteer=_test_gazetteer()) == [job]
    assert rejudge_corrected_locations([job], "Zzyzxville", gazetteer=_test_gazetteer()) == [job]
    assert rejudge_corrected_locations(
        [job], "New York", gazetteer=_test_gazetteer(), remote_only=True
    ) == [job]


def test_filter_by_location_ignores_where_a_location_came_from() -> None:
    """The first pass judges a location the same way whatever `location_source` says: that label
    only matters to the second pass."""
    matching = [
        _result(location="New York, NY", apply_url="https://a.com/1", location_source=source)
        for source in (None, "ats_api", "ats_page")
    ]
    elsewhere = _result(
        location="Berlin, Germany", apply_url="https://b.com/2", location_source="ats_page"
    )

    result = filter_by_location([*matching, elsewhere], "New York", gazetteer=_test_gazetteer())

    assert [r.location_verified for r in result] == [True, True, True]
    assert len(result) == 3


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


def test_sort_results_newest_also_puts_a_verified_result_first() -> None:
    newer_unverified = _result(
        apply_url="https://a.com/1", company="A", posted_at="2026-08-30T00:00:00Z"
    )
    older_verified = _result(
        apply_url="https://b.com/2",
        company="B",
        posted_at="2026-08-01T00:00:00Z",
        location_verified=True,
    )

    result = sort_results([newer_unverified, older_verified], sort_by="newest")

    assert [r.company for r in result] == ["B", "A"]


def test_sort_results_lifts_a_result_stamped_verified_after_the_first_sort() -> None:
    """`filter_by_location` stamps `location_verified` AFTER `aggregate_jobs` has sorted, so
    the first order cannot know. Sorting again must lift the newly verified result over a
    better tier and a newer date, and must return a new list, not reorder the caller's."""
    newer_tier1 = _result(
        apply_url="https://a.com/1",
        company="A",
        source_tier=1.0,
        posted_at="2026-08-30T00:00:00Z",
    )
    older_tier2 = _result(
        apply_url="https://b.com/2",
        company="B",
        source_tier=2.0,
        posted_at="2026-08-01T00:00:00Z",
    )
    first_pass = aggregate_jobs([older_tier2, newer_tier1])
    assert [r.company for r in first_pass] == ["A", "B"]  # nothing verified yet: tier, then recency

    stamped = [replace(r, location_verified=True) if r.company == "B" else r for r in first_pass]
    result = sort_results(stamped)

    assert [r.company for r in result] == ["B", "A"]
    assert [r.company for r in stamped] == ["A", "B"]  # the caller's list is left as it was


# ── apply_search_filters: the shared 3-step chain ──


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
    """The fail-open case: a location was
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
