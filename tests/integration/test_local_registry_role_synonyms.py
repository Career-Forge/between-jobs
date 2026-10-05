"""Role synonyms through the registry's real SQL, against a local stack.

The unit tests fake the database, so they cannot show that `websearch_to_tsquery` reads the
backend's expanded text the way the expansion assumes: that `or` separates alternatives, that
AND binds tighter than `or`, that a quoted run is an adjacent phrase, that a query with no
searchable word browses instead of returning nothing, and that the two replaced plpgsql
bodies parse and run (plpgsql is only compiled on first call). This does.

Every test seeds inside a transaction that is always rolled back, and clears the registry
first so rows already in the local database cannot crowd the seeded ones out of the result
limit.

NOT YET RUN when it was written: it needs `supabase start` and a `supabase db reset --local`
that includes the `expand_registry_search_for_role_synonyms` migration.

Run with `pytest -m local_supabase`."""

from __future__ import annotations

import contextlib
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from between_jobs.api.role_synonyms import registry_search_text
from between_jobs.api.search_aggregation import filter_by_role
from between_jobs.api.search_providers import SearchResult

from .conftest import World

pytestmark = pytest.mark.local_supabase

_MIGRATION = next(
    (Path(__file__).parents[2] / "supabase" / "migrations").glob(
        "*_expand_registry_search_for_role_synonyms.sql"
    )
)

_FUNCTIONS = (
    "public.search_job_registry_postings(text, integer)",
    "public.search_new_job_registry_postings(text, timestamp with time zone, integer)",
)

_FAMILY_TITLES = [
    "SDE II",
    "Software Engineer",
    "Machine Learning Engineer",
    "ML Engineer",
    "Quality Assurance Analyst",
]
_DISTRACTOR_TITLES = [
    "Sales Engineer",
    "Mechanical Engineer",
    "Machine Operator",
    "Marketing Manager",
    "Quality Control Inspector",
]


@contextlib.asynccontextmanager
async def _rolled_back(pg: Any) -> AsyncIterator[None]:
    transaction = pg.transaction()
    await transaction.start()
    try:
        await pg.execute("delete from public.job_registry_postings")
        yield
    finally:
        await transaction.rollback()


async def _company(pg: Any) -> tuple[str, str]:
    slug = f"syn-{uuid.uuid4().hex[:10]}"
    company_id = await pg.fetchval(
        """
        insert into public.job_registry_companies
          (name, ats_type, slug, last_polled_at, consecutive_failures)
        values ($1, 'greenhouse', $2, now() - interval '1 hour', 0)
        returning id
        """,
        f"Company {slug}",
        slug,
    )
    return str(company_id), f"greenhouse:{slug}:"


async def _posting(pg: Any, company_id: str, board: str, title: str) -> str:
    """A fresh, active posting whose whole text is its title, so the tsquery is judged on the
    title alone."""
    posting_id = await pg.fetchval(
        """
        insert into public.job_registry_postings
          (company_id, board, external_id, title, jd_text, apply_url, status, posted_at,
           first_seen, last_seen)
        values ($1::uuid, $2, $3, $4, $4, $5, 'active',
                now() - interval '1 day', now() - interval '1 day', now() - interval '1 day')
        returning id
        """,
        company_id,
        board,
        uuid.uuid4().hex,
        title,
        f"https://example.com/{uuid.uuid4().hex}",
    )
    return str(posting_id)


async def _seed(pg: Any, titles: list[str]) -> dict[str, str]:
    """title -> posting id, all on one company."""
    company_id, board = await _company(pg)
    return {title: await _posting(pg, company_id, board, title) for title in titles}


async def _search(pg: Any, query: str, limit: int = 500) -> list[str]:
    """Titles the browse/text function returns for `query` AS THE BACKEND SENDS IT."""
    rows = await pg.fetch(
        "select title from public.search_job_registry_postings($1, $2)",
        registry_search_text(query),
        limit,
    )
    return [r["title"] for r in rows]


async def _search_new(pg: Any, query: str, limit: int = 500) -> list[str]:
    rows = await pg.fetch(
        "select title from public.search_new_job_registry_postings"
        "($1, now() - interval '30 days', $2)",
        registry_search_text(query),
        limit,
    )
    return [r["title"] for r in rows]


def _title_filtered(titles: list[str], query: str) -> list[str]:
    """The role filter the backend runs on whatever the registry returned."""
    results = [
        SearchResult(
            provider="registry",
            title=title,
            company="Acme",
            location=None,
            remote=None,
            apply_url=f"https://example.com/{i}",
            snippet="",
            posted_at=None,
            salary_min=None,
            salary_max=None,
            salary_currency=None,
            sponsorship_signal="unknown",
            source_tier=1.0,
        )
        for i, title in enumerate(titles)
    ]
    return [r.title for r in filter_by_role(results, query)]


_EXPECTED = [
    ("sde", {"SDE II", "Software Engineer"}),
    ("swe", {"SDE II", "Software Engineer"}),
    ("software engineer", {"SDE II", "Software Engineer"}),
    ("ml engineer", {"Machine Learning Engineer", "ML Engineer"}),
    ("machine learning engineer", {"Machine Learning Engineer", "ML Engineer"}),
    ("qa", {"Quality Assurance Analyst"}),
    ("quality assurance", {"Quality Assurance Analyst"}),
]


@pytest.mark.parametrize(
    "function", ["search_job_registry_postings", "search_new_job_registry_postings"]
)
@pytest.mark.parametrize(("query", "expected"), _EXPECTED)
async def test_a_synonym_query_retrieves_every_member_of_the_family_through_the_real_sql(
    world: World, function: str, query: str, expected: set[str]
) -> None:
    search = _search if function == "search_job_registry_postings" else _search_new
    async with _rolled_back(world.pg):
        await _seed(world.pg, _FAMILY_TITLES + _DISTRACTOR_TITLES)

        retrieved = await search(world.pg, query)

        assert expected <= set(retrieved), (query, expected - set(retrieved))
        assert not set(retrieved) & set(_DISTRACTOR_TITLES)
        # and what the backend then keeps after its own title filter is exactly the family
        assert set(_title_filtered(retrieved, query)) == expected


async def test_a_two_slot_compound_retrieves_its_whole_family_in_either_word_order(
    world: World,
) -> None:
    """The alternatives budget is spent by value, not by position, so both slots' synonyms are
    in the search whichever word comes first."""
    family = [
        "Backend SDE",
        "Backend Software Engineer",
        "Back-End Software Developer",
        "Staff Software Engineer, Backend",
    ]
    distractors = ["Frontend Software Engineer", "Backend Sales Manager", "Mechanical Engineer"]
    async with _rolled_back(world.pg):
        await _seed(world.pg, family + distractors)

        for query in ("backend sde", "sde backend"):
            retrieved = await _search(world.pg, query)

            assert set(family) <= set(retrieved), (query, set(family) - set(retrieved))
            assert not set(retrieved) & set(distractors), query
            assert set(_title_filtered(retrieved, query)) == set(family), query


async def test_qa_does_not_retrieve_manufacturing_hardware_or_aerospace_roles(
    world: World,
) -> None:
    family = ["SDET", "Software Test Engineer", "Quality Assurance Analyst"]
    distractors = [
        "Supplier Quality Engineer",
        "Flight Test Engineer",
        "RF Test Engineer",
        "Hardware Test Engineer",
    ]
    async with _rolled_back(world.pg):
        await _seed(world.pg, family + distractors)

        for query in ("qa", "sdet", "quality assurance"):
            retrieved = await _search(world.pg, query)

            assert set(family) <= set(retrieved), (query, set(family) - set(retrieved))
            assert not set(retrieved) & set(distractors), query


async def test_text_without_synonyms_still_means_every_word_and_nothing_else(
    world: World,
) -> None:
    """The functions do not expand anything themselves: the backend sends the alternatives.
    Plain text is ANDed exactly as it was under plainto_tsquery."""
    async with _rolled_back(world.pg):
        await _seed(world.pg, _FAMILY_TITLES + _DISTRACTOR_TITLES)

        rows = await world.pg.fetch(
            "select title from public.search_job_registry_postings($1, 500)", "ml engineer"
        )

        assert {r["title"] for r in rows} == {"ML Engineer"}


async def test_and_binds_tighter_than_or_in_the_text_the_backend_sends(world: World) -> None:
    """The expansion is `a b or c d`, which is only right if it means (a AND b) OR (c AND d).
    A posting with one word from each side must not match."""
    async with _rolled_back(world.pg):
        await _seed(
            world.pg,
            [
                "ML Researcher",
                "Machine Learning Researcher",
                "Data Engineer",
                "Staff ML Engineer, Platform",
                "Machine Learning Engineer",
            ],
        )

        found = await _search(world.pg, "ml engineer")

        assert set(found) == {"Staff ML Engineer, Platform", "Machine Learning Engineer"}


async def test_a_quoted_run_is_an_adjacent_phrase(world: World) -> None:
    async with _rolled_back(world.pg):
        await _seed(
            world.pg,
            ["Software Engineer", "Software Quality Engineer", "Engineer, Software Platform"],
        )

        rows = await world.pg.fetch(
            "select title from public.search_job_registry_postings($1, 500)",
            '"software engineer"',
        )

        assert {r["title"] for r in rows} == {"Software Engineer"}


@pytest.mark.parametrize(
    "function", ["search_job_registry_postings", "search_new_job_registry_postings"]
)
@pytest.mark.parametrize("text", ["", "the", "the and of", "-- ++"])
async def test_a_query_with_nothing_searchable_browses_instead_of_returning_nothing(
    world: World, function: str, text: str
) -> None:
    async with _rolled_back(world.pg):
        seeded = await _seed(world.pg, ["Software Engineer", "Marketing Manager"])
        if function == "search_job_registry_postings":
            rows = await world.pg.fetch(f"select posting_id from public.{function}($1, 500)", text)
        else:
            rows = await world.pg.fetch(
                f"select posting_id from public.{function}($1, now() - interval '30 days', 500)",
                text,
            )

        assert {str(r["posting_id"]) for r in rows} == set(seeded.values())


async def test_the_backend_can_call_both_through_the_api_with_the_expanded_text(
    world: World,
) -> None:
    """The text carries double quotes and ` or `; it must survive the JSON round trip through
    PostgREST, and the replaced plpgsql bodies must compile (an output column clashing with a
    table column raises 42702 only at first call)."""
    text = registry_search_text("sde")
    assert '"' in text
    first = await world.sb.rpc(
        "search_job_registry_postings", {"search_query": text, "result_limit": 5}
    ).execute()
    second = await world.sb.rpc(
        "search_new_job_registry_postings",
        {"search_query": text, "since_timestamp": "2000-01-01T00:00:00Z", "result_limit": 5},
    ).execute()

    assert isinstance(first.data, list)
    for row in first.data:
        assert "link_fresh" in row  # the P0.8 return shape is unchanged
    assert isinstance(second.data, list)


class _Rollback(Exception):
    """Raised to end the test's transaction so it is always rolled back."""


async def test_the_migrations_own_revokes_close_both_even_when_the_project_grants_by_default(
    world: World,
) -> None:
    """Prod grants new functions to anon, authenticated and service_role by default. Put the
    functions in that state, replay exactly the GRANT and REVOKE statements this migration
    contains, and check what is left. Inside a transaction that is always rolled back."""
    sql = re.sub(r"--[^\n]*", "", _MIGRATION.read_text())
    statements = [
        statement.strip()
        for statement in sql.split(";")
        if statement.strip().lower().startswith(("revoke", "grant"))
    ]
    assert len(statements) == 4  # a revoke and a grant for each of the two functions
    pg = world.pg
    try:
        async with pg.transaction():
            for function in _FUNCTIONS:
                await pg.execute(
                    f"grant execute on function {function} "
                    "to public, anon, authenticated, service_role"
                )
            for statement in statements:
                await pg.execute(statement)
            for function in _FUNCTIONS:
                for role in ("anon", "authenticated"):
                    assert not await pg.fetchval(
                        "select has_function_privilege($1, $2, 'execute')", role, function
                    ), f"{role} can still execute {function}"
                assert await pg.fetchval(
                    "select has_function_privilege('service_role', $1, 'execute')", function
                )
                public_can_execute = await pg.fetchval(
                    """
                    select exists (
                      select 1 from pg_proc p, aclexplode(p.proacl) a
                      where p.oid = $1::regprocedure and a.grantee = 0
                        and a.privilege_type = 'EXECUTE'
                    )
                    """,
                    function,
                )
                assert not public_can_execute, f"PUBLIC can still execute {function}"
            raise _Rollback
    except _Rollback:
        pass
