"""The limits on saved searches, against a real local stack: the CHECK constraints, the
per-user cap and its trigger, and what the matcher reads.

The cap is the part no fake can show: two requests from one user arriving together must not
both take the last slot, which is what the trigger's advisory lock is for.

Run with `pytest -m local_supabase` after `supabase start` and `supabase db reset --local`."""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from postgrest.exceptions import APIError

from between_jobs.api import models
from between_jobs.api.saved_search_matcher import (
    _MAX_SEARCHES_PER_TICK,
    _select_active_saved_searches,
)
from between_jobs.api.saved_searches_store import (
    MAX_SAVED_SEARCHES,
    SavedSearchLimitReached,
    create_saved_search,
)
from supabase import AsyncClient, acreate_client

from .conftest import World

pytestmark = pytest.mark.local_supabase

_MIGRATION = next(
    (Path(__file__).parents[2] / "supabase" / "migrations").glob("*_bound_saved_searches.sql")
)


async def _make(
    sb: AsyncClient, user_id: str, *, query: str = "backend", **extra: Any
) -> dict[str, Any]:
    return await create_saved_search(
        sb,
        user_id,
        query=query,
        location=extra.get("location"),
        companies=extra.get("companies", []),
        remote_only=False,
    )


async def _count(world: World, user_id: str) -> int:
    return int(
        await world.pg.fetchval(
            "select count(*) from public.saved_searches where user_id = $1", uuid.UUID(user_id)
        )
    )


@pytest.fixture
async def clients(local_stack: dict[str, str]) -> AsyncIterator[list[AsyncClient]]:
    """Several independent clients, so concurrent inserts really are separate connections."""
    yield [
        await acreate_client(local_stack["API_URL"], local_stack["SERVICE_ROLE_KEY"])
        for _ in range(8)
    ]


# -- the numbers agree ---------------------------------------------------------------------


async def test_the_apis_limits_are_the_databases(world: World) -> None:
    """The request model and the table hold the same numbers: what the API accepts, the
    database stores, and one past any of them is refused by both."""
    user = await world.web_user()
    q, loc = models.SAVED_SEARCH_MAX_QUERY_CHARS, models.SAVED_SEARCH_MAX_LOCATION_CHARS
    names, name_len = models.SAVED_SEARCH_MAX_COMPANIES, models.SAVED_SEARCH_MAX_COMPANY_CHARS

    stored = await _make(
        world.sb, user, query="q" * q, location="l" * loc, companies=["c" * name_len] * names
    )
    assert len(stored["query"]) == q and len(stored["location"]) == loc
    assert len(stored["companies"]) == names

    for field, value in [
        ("query", "q" * (q + 1)),
        ("location", "l" * (loc + 1)),
        ("companies", ["c"] * (names + 1)),
    ]:
        with pytest.raises(APIError) as refused:
            await (
                world.sb.table("saved_searches")
                .insert({"user_id": user, "query": "x", field: value})
                .execute()
            )
        assert refused.value.code == "23514", field  # check_violation


async def test_the_company_check_bounds_the_total_so_a_few_huge_names_do_not_fit(
    world: World,
) -> None:
    """The API caps each name; the database, which cannot look inside an array in a CHECK,
    caps the total (20 x 100 characters), which is what costs memory."""
    user = await world.web_user()

    with pytest.raises(APIError) as refused:
        await (
            world.sb.table("saved_searches")
            .insert({"user_id": user, "companies": ["c" * 1001, "c" * 1000]})
            .execute()
        )

    assert refused.value.code == "23514"


async def test_the_cap_in_python_is_the_cap_in_the_trigger(world: World) -> None:
    user = await world.web_user()

    for _ in range(MAX_SAVED_SEARCHES):
        await _make(world.sb, user)
    with pytest.raises(SavedSearchLimitReached):
        await _make(world.sb, user)

    assert await _count(world, user) == MAX_SAVED_SEARCHES


# -- the cap ---------------------------------------------------------------------------------


async def test_deleting_one_frees_a_slot_and_another_user_is_not_affected(world: World) -> None:
    alice, bob = await world.web_user(), await world.web_user()
    rows = [await _make(world.sb, alice, query=f"q{i}") for i in range(MAX_SAVED_SEARCHES)]
    with pytest.raises(SavedSearchLimitReached):
        await _make(world.sb, alice)

    await _make(world.sb, bob)  # someone else's cap is their own

    await world.sb.table("saved_searches").delete().eq("id", rows[0]["id"]).execute()
    await _make(world.sb, alice, query="one more")
    with pytest.raises(SavedSearchLimitReached):
        await _make(world.sb, alice)


async def test_concurrent_inserts_never_take_more_than_the_cap(
    world: World, clients: list[AsyncClient]
) -> None:
    """The race the trigger's advisory lock exists for: every insert would count 19 rows if
    they did not wait for each other."""
    user = await world.web_user()
    for _ in range(MAX_SAVED_SEARCHES - 4):
        await _make(world.sb, user)

    results = await asyncio.gather(
        *(_make(clients[i % len(clients)], user, query=f"race-{i}") for i in range(16)),
        return_exceptions=True,
    )

    won = [r for r in results if isinstance(r, dict)]
    refused = [r for r in results if isinstance(r, SavedSearchLimitReached)]
    assert len(won) == 4
    assert len(refused) == 12
    assert await _count(world, user) == MAX_SAVED_SEARCHES


async def test_a_reparenting_update_is_not_refused_by_the_cap(world: World) -> None:
    """Merging a Telegram-only account into a web one moves rows with an UPDATE; refusing that
    would lose a user's searches halfway through a merge. Only an INSERT is capped."""
    alice, bob = await world.web_user(), await world.web_user()
    for _ in range(MAX_SAVED_SEARCHES):
        await _make(world.sb, alice)
    moved = await _make(world.sb, bob)

    await world.pg.execute(
        "update public.saved_searches set user_id = $1 where id = $2",
        uuid.UUID(alice),
        uuid.UUID(moved["id"]),
    )

    assert await _count(world, alice) == MAX_SAVED_SEARCHES + 1
    with pytest.raises(SavedSearchLimitReached):
        await _make(world.sb, alice)


async def test_the_trigger_function_is_not_callable_by_any_role(world: World) -> None:
    for role in ("anon", "authenticated", "service_role"):
        can_run = await world.pg.fetchval(
            "select has_function_privilege($1, 'public.enforce_saved_search_limit()', 'execute')",
            role,
        )
        assert can_run is False, role
    owned_by_public = await world.pg.fetchval(
        "select count(*) from pg_proc p, "
        "aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a "
        "where p.proname = 'enforce_saved_search_limit' and a.grantee = 0"
    )
    assert owned_by_public == 0
    config = await world.pg.fetchval(
        "select proconfig from pg_proc where proname = 'enforce_saved_search_limit'"
    )
    assert config == ['search_path=""']


# -- what the matcher reads --------------------------------------------------------------------


async def test_the_matcher_reads_a_bounded_number_oldest_first_from_a_real_table(
    world: World,
) -> None:
    """Inactive rows are never read, and a tick takes the least recently matched first. Uses a
    handful of users (each is capped) and checks the order the real index serves."""
    users = [await world.web_user() for _ in range(3)]
    ids: list[str] = []
    for index, user in enumerate(users):
        row = await _make(world.sb, user, query=f"matcher-{index}")
        ids.append(row["id"])
    # The first user's search was matched most recently, the last user's longest ago; the
    # middle one is paused.
    await world.pg.execute(
        "update public.saved_searches set last_matched_at = now() - interval '1 hour' "
        "where id = $1",
        uuid.UUID(ids[0]),
    )
    await world.pg.execute(
        "update public.saved_searches set last_matched_at = now() - interval '3 days' "
        "where id = $1",
        uuid.UUID(ids[2]),
    )
    await world.pg.execute(
        "update public.saved_searches set is_active = false where id = $1", uuid.UUID(ids[1])
    )

    taken = await _select_active_saved_searches(world.sb)

    assert len(taken) <= _MAX_SEARCHES_PER_TICK
    ours = [row["id"] for row in taken if row["id"] in ids]
    assert ours == [ids[2], ids[0]]  # the paused one is absent; the older watermark is first
    assert set(taken[0]) == {
        "id",
        "user_id",
        "query",
        "location",
        "companies",
        "remote_only",
        "last_matched_at",
    }
    watermarks = [row["last_matched_at"] for row in taken]
    assert watermarks == sorted(watermarks)


# -- the migration's own trimming ----------------------------------------------------------------


def _trim_statements() -> list[str]:
    """The migration's three UPDATE statements, as written in the file."""
    text = _MIGRATION.read_text()
    statements = re.findall(r"^update public\.saved_searches.*?;\s*$", text, re.S | re.M)
    assert len(statements) == 3, statements
    return statements


async def test_the_migration_trims_rows_that_already_broke_a_limit_and_keeps_the_rest(
    world: World,
) -> None:
    """Replays the real trimming SQL against rows made oversized the only way they could have
    been (before the constraints existed), inside a transaction that is rolled back."""
    user = await world.web_user()
    pg: asyncpg.Connection = world.pg

    class _Rollback(Exception):
        pass

    with pytest.raises(_Rollback):
        async with pg.transaction():
            await pg.execute(
                "alter table public.saved_searches drop constraint saved_searches_query_length"
            )
            await pg.execute(
                "alter table public.saved_searches drop constraint saved_searches_location_length"
            )
            await pg.execute(
                "alter table public.saved_searches drop constraint saved_searches_companies_size"
            )
            await pg.execute(
                "alter table public.saved_searches disable trigger saved_searches_enforce_limit"
            )
            names = [f"company-{i:02d}-" + "x" * 150 for i in range(30)]
            big = await pg.fetchval(
                "insert into public.saved_searches (user_id, query, location, companies) "
                "values ($1, $2, $3, $4) returning id",
                uuid.UUID(user),
                "q" * 5000,
                "l" * 400,
                names,
            )
            small = await pg.fetchval(
                "insert into public.saved_searches (user_id, query, location, companies) "
                "values ($1, 'keep me', 'Jersey City', array['Anthropic', 'Stripe']) returning id",
                uuid.UUID(user),
            )

            for statement in _trim_statements():
                await pg.execute(statement)

            trimmed = await pg.fetchrow("select * from public.saved_searches where id = $1", big)
            kept = await pg.fetchrow("select * from public.saved_searches where id = $1", small)
            assert trimmed["query"] == "q" * 200
            assert trimmed["location"] == "l" * 100
            assert len(trimmed["companies"]) == 20
            assert all(len(name) == 100 for name in trimmed["companies"])
            assert trimmed["companies"][0].startswith("company-00-")  # the first twenty, in order
            assert trimmed["companies"][19].startswith("company-19-")
            assert (kept["query"], kept["location"], list(kept["companies"])) == (
                "keep me",
                "Jersey City",
                ["Anthropic", "Stripe"],
            )
            # and everything left satisfies the constraints the migration then adds
            await pg.execute(
                "alter table public.saved_searches add constraint saved_searches_query_length "
                "check (char_length(query) <= 200)"
            )
            await pg.execute(
                "alter table public.saved_searches add constraint saved_searches_companies_size "
                "check (cardinality(companies) <= 20 "
                "and char_length(array_to_string(companies, '')) <= 2000)"
            )
            raise _Rollback

    # The rollback put the constraints back.
    with pytest.raises(APIError) as refused:
        await (
            world.sb.table("saved_searches").insert({"user_id": user, "query": "q" * 201}).execute()
        )
    assert refused.value.code == "23514"
