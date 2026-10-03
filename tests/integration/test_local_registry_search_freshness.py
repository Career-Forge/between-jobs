"""The registry lane's freshness rules (launch plan P0.8), against the real SQL.

The fakes in the unit tests return rows verbatim, so they cannot show whether a stale
posting is excluded, whether the candidate window behaves, or whether the new functions
parse and run -- plpgsql bodies are only checked when first called. This does.

Every test seeds inside a transaction that is always rolled back, with timestamps
relative to the transaction's own `now()` (the database clock is what the functions use).

Run with `pytest -m local_supabase` after `supabase start` and
`supabase db reset --local`."""

from __future__ import annotations

import contextlib
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from .conftest import World

pytestmark = pytest.mark.local_supabase

_MIGRATION = next(
    (Path(__file__).parents[2] / "supabase" / "migrations").glob(
        "*_bound_registry_lane_freshness.sql"
    )
)


@contextlib.asynccontextmanager
async def _rolled_back(pg: Any) -> AsyncIterator[None]:
    transaction = pg.transaction()
    await transaction.start()
    try:
        yield
    finally:
        await transaction.rollback()


async def _company(
    pg: Any, *, polled_ago: str | None = "1 hour", failures: int = 0
) -> tuple[str, str]:
    """(company id, board). `polled_ago=None` is a company that has never been polled."""
    slug = f"fresh-{uuid.uuid4().hex[:10]}"
    company_id = await pg.fetchval(
        """
        insert into public.job_registry_companies
          (name, ats_type, slug, last_polled_at, consecutive_failures)
        values ($1, 'greenhouse', $2, now() - ($3::text)::interval, $4)
        returning id
        """,
        f"Company {slug}",
        slug,
        polled_ago,
        failures,
    )
    return str(company_id), f"greenhouse:{slug}:"


async def _posting(
    pg: Any,
    company_id: str,
    board: str,
    term: str,
    *,
    seen_ago: str = "1 day",
    posted_ago: str = "1 day",
    first_seen_ago: str = "10 days",
    status: str = "active",
) -> str:
    posting_id = await pg.fetchval(
        """
        insert into public.job_registry_postings
          (company_id, board, external_id, title, jd_text, apply_url, status, posted_at,
           first_seen, last_seen)
        values ($1::uuid, $2, $3, $4, $4, $5, $6,
                now() - ($7::text)::interval, now() - ($8::text)::interval,
                now() - ($9::text)::interval)
        returning id
        """,
        company_id,
        board,
        uuid.uuid4().hex,
        f"{term} engineer",
        f"https://example.com/{uuid.uuid4().hex}",
        status,
        posted_ago,
        first_seen_ago,
        seen_ago,
    )
    return str(posting_id)


def _term() -> str:
    return "zq" + uuid.uuid4().hex[:10]


async def _search(pg: Any, query: str, limit: int = 150) -> dict[str, dict[str, Any]]:
    rows = await pg.fetch("select * from public.search_job_registry_postings($1, $2)", query, limit)
    return {str(r["posting_id"]): dict(r) for r in rows}


async def _search_new(pg: Any, query: str, since: str, limit: int = 150) -> set[str]:
    rows = await pg.fetch(
        "select posting_id from public.search_new_job_registry_postings"
        "($1, now() - ($2::text)::interval, $3)",
        query,
        since,
        limit,
    )
    return {str(r["posting_id"]) for r in rows}


# -- fresh, stale, and the 7-day edge ---------------------------------------------------


@pytest.mark.parametrize("branch", ["text", "browse"])
async def test_a_fresh_posting_on_a_recently_polled_board_is_returned_and_checked(
    world: World, branch: str
) -> None:
    async with _rolled_back(world.pg):
        term = _term()
        company, board = await _company(world.pg)
        fresh = await _posting(world.pg, company, board, term, seen_ago="1 day")

        found = await _search(world.pg, term if branch == "text" else "")

        assert found[fresh]["link_fresh"] is True


@pytest.mark.parametrize("branch", ["text", "browse"])
async def test_a_posting_last_seen_more_than_seven_days_ago_is_not_returned(
    world: World, branch: str
) -> None:
    async with _rolled_back(world.pg):
        term = _term()
        company, board = await _company(world.pg)
        stale = await _posting(world.pg, company, board, term, seen_ago="8 days")
        fresh = await _posting(world.pg, company, board, term, seen_ago="1 day")

        found = await _search(world.pg, term if branch == "text" else "")

        assert fresh in found
        assert stale not in found


async def test_the_seven_day_edge(world: World) -> None:
    async with _rolled_back(world.pg):
        term = _term()
        company, board = await _company(world.pg)
        inside = await _posting(world.pg, company, board, term, seen_ago="6 days 23 hours")
        outside = await _posting(world.pg, company, board, term, seen_ago="7 days 1 hour")

        found = await _search(world.pg, term)

        assert inside in found
        assert outside not in found


# -- link_fresh: seen recently AND board polled recently AND board healthy -------------


async def test_a_recent_posting_on_a_board_not_polled_in_time_is_returned_but_not_checked(
    world: World,
) -> None:
    """The "fresh-but-company-unpolled" case: we cannot say the link is live, but it is
    inside the window, so it is shown as not verified rather than hidden."""
    async with _rolled_back(world.pg):
        term = _term()
        company, board = await _company(world.pg, polled_ago="3 days")
        posting = await _posting(world.pg, company, board, term, seen_ago="1 day")

        found = await _search(world.pg, term)

        assert found[posting]["link_fresh"] is False


async def test_a_board_that_has_never_been_polled_is_not_checked_and_not_null(
    world: World,
) -> None:
    async with _rolled_back(world.pg):
        term = _term()
        company, board = await _company(world.pg, polled_ago=None)
        posting = await _posting(world.pg, company, board, term, seen_ago="1 day")

        found = await _search(world.pg, term)

        assert found[posting]["link_fresh"] is False  # False, not None


async def test_a_board_that_keeps_failing_is_not_checked_even_though_it_was_just_polled(
    world: World,
) -> None:
    """A failed poll advances last_polled_at too, so "polled recently" alone proves nothing."""
    async with _rolled_back(world.pg):
        term = _term()
        company, board = await _company(world.pg, polled_ago="1 hour", failures=2)
        posting = await _posting(world.pg, company, board, term, seen_ago="1 day")

        found = await _search(world.pg, term)

        assert found[posting]["link_fresh"] is False


async def test_a_posting_not_seen_for_three_days_is_returned_but_not_checked(
    world: World,
) -> None:
    """A capped board is polled every few hours while its unfetched tail keeps an old
    last_seen. Three days = the once-a-day refresh of a 304 plus the 48 hour poll window."""
    async with _rolled_back(world.pg):
        term = _term()
        company, board = await _company(world.pg, polled_ago="1 hour")
        inside = await _posting(world.pg, company, board, term, seen_ago="2 days 23 hours")
        outside = await _posting(world.pg, company, board, term, seen_ago="3 days 1 hour")

        found = await _search(world.pg, term)

        assert found[inside]["link_fresh"] is True
        assert found[outside]["link_fresh"] is False


# -- the candidate window ---------------------------------------------------------------


async def test_stale_rows_inside_the_window_do_not_use_up_the_result_limit(
    world: World,
) -> None:
    """Three stale postings, all newer by posted_at than the one fresh posting, with a limit
    of 1: the freshness filter runs before the limit, so the fresh one comes back."""
    async with _rolled_back(world.pg):
        company, board = await _company(world.pg)
        for _ in range(3):
            await _posting(
                world.pg, company, board, "stale", seen_ago="9 days", posted_ago="1 hour"
            )
        fresh = await _posting(
            world.pg, company, board, "fresh", seen_ago="1 day", posted_ago="5 days"
        )

        found = await _search(world.pg, "", limit=1)

        assert set(found) == {fresh}


async def test_the_candidate_window_is_ten_times_the_limit_and_that_is_the_documented_bound(
    world: World,
) -> None:
    """With a limit of 1 the lane looks at the 10 newest candidates. If all ten are stale a
    fresh posting behind them is not found. This is the deliberate price of bounded cost
    (a bare filter took 1.8-7.8 s on production when the poller was behind); it only
    happens while most of the registry is stale, and heals as the poller catches up."""
    async with _rolled_back(world.pg):
        company, board = await _company(world.pg)
        for _ in range(10):
            await _posting(
                world.pg, company, board, "stale", seen_ago="9 days", posted_ago="1 hour"
            )
        behind = await _posting(
            world.pg, company, board, "fresh", seen_ago="1 day", posted_ago="5 days"
        )
        within = await _search(world.pg, "", limit=2)  # window 20: reaches it

        assert behind in within
        assert behind not in await _search(world.pg, "", limit=1)  # window 10: does not


# -- the saved-search matcher's function -------------------------------------------------


async def test_the_matcher_function_excludes_stale_postings_and_still_honours_the_watermark(
    world: World,
) -> None:
    async with _rolled_back(world.pg):
        term = _term()
        company, board = await _company(world.pg)
        new_fresh = await _posting(
            world.pg, company, board, term, seen_ago="1 day", first_seen_ago="2 days"
        )
        new_stale = await _posting(
            world.pg, company, board, term, seen_ago="8 days", first_seen_ago="9 days"
        )
        old_fresh = await _posting(
            world.pg, company, board, term, seen_ago="1 day", first_seen_ago="20 days"
        )

        for query in (term, ""):
            found = await _search_new(world.pg, query, since="10 days")
            assert new_fresh in found
            assert new_stale not in found  # outside the 7-day window
            assert old_fresh not in found  # before the watermark


# -- the nightly check, as a test ---------------------------------------------------------


async def test_no_registry_lane_result_has_a_last_seen_older_than_seven_days(
    world: World,
) -> None:
    """The plan's check query: join the function's output back to the table and count the
    rows whose last_seen is past the window. Must be 0 -- here on a mixed seed."""
    async with _rolled_back(world.pg):
        term = _term()
        company, board = await _company(world.pg)
        for age in ("1 hour", "3 days", "6 days", "8 days", "30 days"):
            await _posting(world.pg, company, board, term, seen_ago=age)

        for query in (term, ""):
            stale = await world.pg.fetchval(
                """
                select count(*) from public.search_job_registry_postings($1, 1000) r
                join public.job_registry_postings p on p.id = r.posting_id
                where p.last_seen < now() - interval '7 days'
                """,
                query,
            )
            assert stale == 0


# -- the browse branch's index ------------------------------------------------------------


async def test_the_browse_branch_orders_by_an_index_that_matches_its_order_by(
    world: World,
) -> None:
    """The browse branch used to order `posted_at desc nulls last` against an index that is
    `posted_at desc` (NULLs first), so it could never use it: 2.4 s on production for 150
    rows. With seq scans disabled the planner must find an index it can order by."""
    async with _rolled_back(world.pg):
        await world.pg.execute("set local enable_seqscan = off")
        plan = "\n".join(
            r[0]
            for r in await world.pg.fetch(
                """
                explain
                select * from public.job_registry_postings
                where status = 'active'
                order by posted_at desc nulls last
                limit 10
                """
            )
        )

        assert "job_registry_postings_active_posted_nulls_last_idx" in plan
        assert "Sort" not in plan


# -- confirming a board that answered 304 -------------------------------------------------


async def test_confirming_a_board_refreshes_only_its_active_rows_older_than_a_day(
    world: World,
) -> None:
    async with _rolled_back(world.pg):
        company, board = await _company(world.pg)
        other_company, other_board = await _company(world.pg)
        old_active = await _posting(world.pg, company, board, "a", seen_ago="5 days")
        yesterday_plus = await _posting(world.pg, company, board, "b", seen_ago="25 hours")
        touched_today = await _posting(world.pg, company, board, "c", seen_ago="2 hours")
        closed = await _posting(world.pg, company, board, "d", seen_ago="5 days", status="closed")
        elsewhere = await _posting(world.pg, other_company, other_board, "e", seen_ago="5 days")
        before = {
            p: await world.pg.fetchval(
                "select last_seen from public.job_registry_postings where id = $1::uuid", p
            )
            for p in (touched_today, closed, elsewhere)
        }

        touched = await world.pg.fetchval(
            "select public.confirm_job_registry_boards($1::text[])", [board]
        )

        assert touched == 2  # the 5-day and the 25-hour active rows
        age = "select now() - last_seen from public.job_registry_postings where id = $1::uuid"
        for refreshed in (old_active, yesterday_plus):
            seconds = (await world.pg.fetchval(age, refreshed)).total_seconds()
            assert seconds < 5  # last_seen = now(), the transaction's own clock
        for untouched, original in before.items():
            now_seen = await world.pg.fetchval(
                "select last_seen from public.job_registry_postings where id = $1::uuid", untouched
            )
            assert now_seen == original  # touched today, closed, and another board: unchanged
        status = await world.pg.fetchval(
            "select status from public.job_registry_postings where id = $1::uuid", closed
        )
        assert status == "closed"  # a 304 never resurrects a closed posting


async def test_confirming_no_boards_does_nothing(world: World) -> None:
    async with _rolled_back(world.pg):
        assert (
            await world.pg.fetchval("select public.confirm_job_registry_boards($1::text[])", [])
            == 0
        )


# -- the new functions are callable by the backend, and by nobody else -------------------


async def test_the_backend_can_call_all_four_through_the_api_and_the_shape_is_right(
    world: World,
) -> None:
    """The plpgsql bodies are only compiled when first called: a name clash between an
    output column and a table column (42702) would pass the migration and fail here."""
    for query in ("", "engineer"):
        result = await world.sb.rpc(
            "search_job_registry_postings", {"search_query": query, "result_limit": 5}
        ).execute()
        assert isinstance(result.data, list)
        for row in result.data:
            assert "link_fresh" in row
        await world.sb.rpc(
            "search_new_job_registry_postings",
            {"search_query": query, "since_timestamp": "2000-01-01T00:00:00Z", "result_limit": 5},
        ).execute()
    confirmed = await world.sb.rpc("confirm_job_registry_boards", {"boards": []}).execute()
    assert confirmed.data == 0
    polled = await world.sb.rpc("registry_last_polled_at", {}).execute()
    assert polled.data is None or isinstance(polled.data, str)


_FUNCTIONS = (
    "public.search_job_registry_postings(text, integer)",
    "public.search_new_job_registry_postings(text, timestamp with time zone, integer)",
    "public.confirm_job_registry_boards(text[])",
    "public.registry_last_polled_at()",
)


class _Rollback(Exception):
    """Raised to end the test's transaction so it is always rolled back."""


async def test_the_migrations_own_revokes_close_all_four_even_when_the_project_grants_by_default(
    world: World,
) -> None:
    """Prod grants new functions to anon, authenticated and service_role by default, and
    dropping and recreating a function discards its ACL. Put the three functions in prod's
    state, replay exactly the GRANT and REVOKE statements this migration contains, and
    check what is left. Inside a transaction that is always rolled back."""
    sql = re.sub(r"--[^\n]*", "", _MIGRATION.read_text())
    statements = [
        statement.strip()
        for statement in sql.split(";")
        if statement.strip().lower().startswith(("revoke", "grant"))
    ]
    assert len(statements) == 8  # a revoke and a grant for each of the four functions
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


# -- registry_last_polled_at: the empty-lane explanation ----------------------------------


async def test_registry_last_polled_at_is_the_newest_poll_of_an_active_board(world: World) -> None:
    async with _rolled_back(world.pg):
        await world.pg.execute("delete from public.job_registry_companies")
        assert await world.pg.fetchval("select public.registry_last_polled_at()") is None

        await _company(world.pg, polled_ago="5 days")
        await _company(world.pg, polled_ago="2 days")
        await _company(world.pg, polled_ago=None)
        inactive, _ = await _company(world.pg, polled_ago="1 hour")
        await world.pg.execute(
            "update public.job_registry_companies set is_active = false where id = $1::uuid",
            inactive,
        )

        age = await world.pg.fetchval("select now() - public.registry_last_polled_at()")

        assert (
            47 < age.total_seconds() / 3600 < 49
        )  # the 2-day board; the inactive hour-old one does not count
