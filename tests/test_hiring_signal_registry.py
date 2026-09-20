"""Tests for the registry read behind `ats_echo` matching
(`hiring_signal_registry`): what it fetches, what it never fetches, and how far
it reads. Runs against the in-memory fake in `hiring_signal_fakes`."""

from __future__ import annotations

import uuid
from typing import Any, cast

import httpx
import pytest
from hiring_signal_fakes import FakeSupabase, FakeTable, as_client
from postgrest import AsyncPostgrestClient

from between_jobs.api.hiring_signal_company import CompanyNames, company_names
from between_jobs.api.hiring_signal_registry import (
    MAX_BATCH_REGISTRY_COMPANIES,
    MAX_COMPANIES,
    MAX_POSTINGS,
    MAX_ROLE_PATTERN_WORDS,
    RegistryLookup,
    echo_registry_state,
    fetch_registry_lookup,
    fetch_registry_lookup_for_pages,
    is_country_level,
)
from between_jobs.api.hiring_signals import HiringSignal, RegistryCandidate
from supabase import AsyncClient


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


# ── the batch read's own bounds hold whatever the caller passes ──────────


def _or_patterns(table: FakeTable) -> list[str]:
    (call,) = table.calls
    (clauses,) = (value for op, _, value in call[1] if op == "or_ilike")
    return [pattern for _, pattern in clauses]


async def test_the_batch_read_uses_at_most_max_companies_names_however_many_are_passed() -> None:
    """The service caps the distinct pages before it calls; this is the function's OWN
    bound, which its docstring promises "whatever the caller passes"."""
    fake, companies, _ = _supabase([_company(1, "Acme Systems")], [])
    names = [_names(f"Company{c} Systems") for c in "abcdefghijklmnopqrstuvwxyzABCD"]
    assert len(names) > MAX_COMPANIES

    await fetch_registry_lookup_for_pages(as_client(fake), names, ["Data Engineer"])

    assert len(_or_patterns(companies)) == MAX_COMPANIES


async def test_the_batch_read_uses_at_most_max_companies_roles_however_many_are_passed() -> None:
    fake, _, postings = _supabase([_company(1, "Acme Systems")], [_posting(1, "co-1")])
    roles = [f"Role{c} Engineer" for c in "abcdefghijklmnopqrstuvwxyzABCD"]
    assert len(roles) > MAX_COMPANIES

    await fetch_registry_lookup_for_pages(as_client(fake), [_names("Acme Systems")], roles)

    assert len(_or_patterns(postings)) == MAX_COMPANIES


async def test_a_role_is_narrowed_by_its_first_few_words_only() -> None:
    fake, _, postings = _supabase([_company(1, "Acme Systems")], [_posting(1, "co-1")])
    role = " ".join(f"word{i}" for i in range(MAX_ROLE_PATTERN_WORDS + 14))

    await fetch_registry_lookup_for_pages(as_client(fake), [_names("Acme Systems")], [role])

    (pattern,) = _or_patterns(postings)
    words = [w for w in pattern.split("%") if w]
    assert words == [f"word{i}" for i in range(MAX_ROLE_PATTERN_WORDS)]


# ── what a lookup says about an echo whose place is only a country ───────


def _echo(place: str | None) -> HiringSignal:
    return HiringSignal(
        activity_id="7506381452083381426",
        post_url="https://www.linkedin.com/posts/northwind-labs_hiring-activity-7506381452083381426",
        author_handle="northwind-labs",
        author_name="Northwind Labs",
        age=None,
        species="ats_echo",
        comment_count=None,
        echo_role="Software Engineer",
        echo_location=place,
    )


_AUSTIN = RegistryLookup(
    companies=("Northwind Labs, Inc.",),
    candidates=[
        RegistryCandidate(
            company="Northwind Labs, Inc.",
            title="Software Engineer",
            ref="p1",
            location="Austin, Texas",
        )
    ],
)


@pytest.mark.parametrize(
    ("place", "state"),
    [
        ("Austin, Texas", "matched"),  # the same opening
        ("Pune", "unmatched"),  # a different place: the registry has the company, not this
        ("United States", "possible"),  # a country cannot say WHICH opening: unknown, not "no"
        ("Remote", "possible"),
        (None, "possible"),
    ],
)
def test_an_echo_whose_place_names_no_city_is_never_reported_as_untracked(
    place: str | None, state: str
) -> None:
    """The same title at the same company in two cities is two openings. An echo that
    says only `United States` may be any of them, so a registry listing in Austin is a
    `possible` match -- never `unmatched` (it does track that title) and never
    `matched` (it cannot tell)."""
    assert echo_registry_state(_echo(place), _AUSTIN) == state


# ── the size of the real request the batch builds ────────────────────────


class _RealRequestBuilders:
    """Just enough of a Supabase client to run the registry code over the REAL
    PostgREST request builders and see the url they would send: `table()` returns a
    real builder whose transport is a stub that records the request and answers as
    the database would."""

    def __init__(self, handler: Any) -> None:
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.postgrest = AsyncPostgrestClient(
            "https://example.supabase.co/rest/v1", http_client=self.http
        )

    def table(self, name: str) -> Any:
        return self.postgrest.from_(name)


async def test_the_batch_postings_request_at_its_documented_maximum_fits_a_request_line() -> None:
    """A full batch: `MAX_COMPANIES` pages, `MAX_COMPANIES` roles of `MAX_ROLE_PATTERN_
    WORDS` ordinary words, and as many registry companies as the read allows. The
    postings request names every company in its url, so its size is a real limit (a
    reverse proxy refuses about 8 KB), and no fake table can see a url."""
    company_ids = [str(uuid.uuid4()) for _ in range(MAX_BATCH_REGISTRY_COMPANIES)]
    urls: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        table = request.url.path.rsplit("/", 1)[-1]
        urls[table] = str(request.url)
        if table == "job_registry_companies":
            rows = [{"id": i, "name": f"Acme Systems {n}"} for n, i in enumerate(company_ids)]
            return httpx.Response(200, json=rows, request=request)
        return httpx.Response(200, json=[], request=request)

    names = [_names(f"Company{c} Systems") for c in "abcdefghijklmnopqrst"]
    roles = [
        " ".join(f"word{c}{i}abcdef" for i in range(MAX_ROLE_PATTERN_WORDS))
        for c in "abcdefghijklmnopqrst"
    ]
    assert len(names) == len(roles) == MAX_COMPANIES

    builders = _RealRequestBuilders(handler)
    lookup = await fetch_registry_lookup_for_pages(cast(AsyncClient, builders), names, roles)

    assert len(lookup.companies) == MAX_BATCH_REGISTRY_COMPANIES
    postings_url = urls["job_registry_postings"]
    assert postings_url.count("company_id=in.") == 1
    assert len(postings_url) < 7_000, len(postings_url)
    assert len(urls["job_registry_companies"]) < 4_000
    await builders.http.aclose()
