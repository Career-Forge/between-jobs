"""Tests for the registry read behind `ats_echo` matching
(`hiring_signal_registry`): what it fetches, what it never fetches, and how far
it reads. Runs against the in-memory fake in `hiring_signal_fakes`."""

from __future__ import annotations

from typing import Any

import pytest
from hiring_signal_fakes import FakeSupabase, FakeTable, as_client

from between_jobs.api.hiring_signal_company import CompanyNames, company_names
from between_jobs.api.hiring_signal_registry import (
    MAX_COMPANIES,
    MAX_POSTINGS,
    fetch_registry_lookup,
    is_country_level,
)


def _names(company: str) -> CompanyNames:
    names = company_names(company)
    assert names is not None
    return names


def _company(i: int, name: str) -> dict[str, Any]:
    return {"id": f"co-{i}", "name": name}


def _posting(i: int, company_id: str, *, status: str = "active") -> dict[str, Any]:
    return {
        "id": f"post-{i}",
        "company_id": company_id,
        "title": f"Engineer {i}",
        "location": "Dublin",
        "status": status,
        "first_seen": f"2026-09-{1 + i % 28:02d}T00:00:00+00:00",
    }


def _supabase(
    companies: list[dict[str, Any]], postings: list[dict[str, Any]]
) -> tuple[FakeSupabase, FakeTable, FakeTable]:
    company_table = FakeTable(companies)
    posting_table = FakeTable(postings)
    fake = FakeSupabase(
        {"job_registry_companies": company_table, "job_registry_postings": posting_table}
    )
    return fake, company_table, posting_table


async def test_the_lookup_returns_matching_companies_and_their_active_postings() -> None:
    fake, _, _ = _supabase(
        [_company(1, "Stripe, Inc."), _company(2, "Someone Else")],
        [_posting(1, "co-1"), _posting(2, "co-2")],
    )
    lookup = await fetch_registry_lookup(as_client(fake), _names("Stripe"))

    assert lookup.companies == ("Stripe, Inc.",)
    assert [c.company for c in lookup.candidates] == ["Stripe, Inc."]


async def test_a_company_with_no_open_postings_is_still_a_known_company() -> None:
    fake, _, _ = _supabase([_company(1, "Stripe, Inc.")], [])
    lookup = await fetch_registry_lookup(as_client(fake), _names("Stripe"))

    assert lookup.companies == ("Stripe, Inc.",)
    assert lookup.candidates == []


async def test_a_closed_posting_is_never_a_candidate() -> None:
    fake, _, _ = _supabase(
        [_company(1, "Stripe, Inc.")],
        [_posting(1, "co-1", status="closed"), _posting(2, "co-1", status="active")],
    )
    lookup = await fetch_registry_lookup(as_client(fake), _names("Stripe"))

    assert [c.ref for c in lookup.candidates] == ["post-2"]


async def test_at_most_max_companies_are_read() -> None:
    fake, _, _ = _supabase(
        [_company(i, f"Stripe Partner {i}") for i in range(MAX_COMPANIES + 15)], []
    )
    lookup = await fetch_registry_lookup(as_client(fake), _names("Stripe"))

    assert len(lookup.companies) == MAX_COMPANIES == 20


async def test_at_most_max_postings_are_read() -> None:
    fake, _, _ = _supabase(
        [_company(1, "Stripe, Inc.")], [_posting(i, "co-1") for i in range(MAX_POSTINGS + 100)]
    )
    lookup = await fetch_registry_lookup(as_client(fake), _names("Stripe"))

    assert len(lookup.candidates) == MAX_POSTINGS == 1000


async def test_a_name_with_no_words_reads_nothing_at_all() -> None:
    """An empty pattern (`%%`) would match EVERY company: with no words, the
    registry must not be queried."""
    fake, company_table, posting_table = _supabase(
        [_company(1, "Stripe, Inc.")], [_posting(1, "co-1")]
    )
    nameless = CompanyNames(query_name="x", variants=(), registry_words=())
    lookup = await fetch_registry_lookup(as_client(fake), nameless)

    assert lookup.companies == () and lookup.candidates == []
    assert company_table.calls == [] and posting_table.calls == []


async def test_the_pattern_is_built_from_the_shortest_spelling_of_the_name() -> None:
    """`Meta Platforms, Inc.` is read as `%meta%`, so a registry company stored
    as plain `Meta` is fetched (identity is decided by the caller)."""
    fake, _, _ = _supabase([_company(1, "Meta"), _company(2, "Unrelated")], [])
    lookup = await fetch_registry_lookup(as_client(fake), _names("Meta Platforms, Inc."))

    assert lookup.companies == ("Meta",)


# ── country-level locations (YH-7) ───────────────────────────────────────


@pytest.mark.parametrize(
    "location",
    [
        "United States",
        "USA",
        "U.S.",
        "India",
        "Remote",
        "Anywhere",
        "Remote - US",
        "Remote, United States",
        "United States, Canada",
    ],
)
def test_a_location_that_names_no_place_narrower_than_a_country(location: str) -> None:
    assert is_country_level(location)


@pytest.mark.parametrize(
    "location",
    ["Austin, Texas, United States", "Bengaluru", "Dublin, Ireland", "Remote, Toronto", "", None],
)
def test_a_location_that_names_a_city_is_not_country_level(location: str | None) -> None:
    assert not is_country_level(location)
