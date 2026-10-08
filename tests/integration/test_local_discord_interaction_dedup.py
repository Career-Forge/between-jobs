"""`claim_discord_interaction` and its companions against a local Supabase stack: the real SQL,
real concurrency, real grants -- what the in-memory ledger in tests/discord_fakes.py can only
imitate. The same scenario the Telegram claim functions are held to (tests/update_ledger_fakes.py)
runs here against the real functions and must give the same answers as the fake, which is what
stops the fake drifting from the SQL it stands in for.

Run with `pytest -m local_supabase` after `supabase start` and
`supabase db reset --local`."""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from discord_fakes import FakeInteractionLedger
from postgrest.exceptions import APIError
from update_ledger_fakes import SCENARIO_EXPECTED, run_scenario

from supabase import acreate_client

from .conftest import World

pytestmark = pytest.mark.local_supabase

_MIGRATION = next(
    (Path(__file__).parents[2] / "supabase" / "migrations").glob(
        "*_discord_link_and_interaction_claims.sql"
    )
)
_FUNCTIONS = (
    "public.claim_discord_interaction(text, integer)",
    "public.complete_discord_interaction(text)",
    "public.release_discord_interaction(text)",
)
_TABLE = "public.discord_processed_interactions"


class _RealLedger:
    """The real functions, called the way the backend calls them: RPCs as service_role. Ids are
    offset by a per-test base so concurrent or crashed runs never share a row."""

    def __init__(self, world: World, base: int) -> None:
        self._world = world
        self.base = base

    def _id(self, interaction_id: int) -> str:
        return str(self.base + interaction_id)

    async def claim(self, interaction_id: int, lease_seconds: int = 900) -> str:
        result = await self._world.sb.rpc(
            "claim_discord_interaction",
            {"p_interaction_id": self._id(interaction_id), "p_lease_seconds": lease_seconds},
        ).execute()
        assert result.data in ("claimed", "done", "in_progress"), result.data
        return str(result.data)

    async def complete(self, interaction_id: int) -> None:
        await self._world.sb.rpc(
            "complete_discord_interaction", {"p_interaction_id": self._id(interaction_id)}
        ).execute()

    async def release(self, interaction_id: int) -> None:
        await self._world.sb.rpc(
            "release_discord_interaction", {"p_interaction_id": self._id(interaction_id)}
        ).execute()

    async def age(self, interaction_id: int, seconds: float) -> None:
        await self._world.pg.execute(
            f"update {_TABLE} set claimed_at = claimed_at - make_interval(secs => $2) "
            "where interaction_id = $1",
            self._id(interaction_id),
            seconds,
        )

    async def known(self, interaction_id: int) -> bool:
        return bool(
            await self._world.pg.fetchval(
                f"select exists (select 1 from {_TABLE} where interaction_id = $1)",
                self._id(interaction_id),
            )
        )


@pytest.fixture
async def ledger(world: World) -> AsyncIterator[_RealLedger]:
    base = 1_100_000_000_000_000_000 + (uuid.uuid4().int % 1_000_000) * 1000
    try:
        yield _RealLedger(world, base)
    finally:
        await world.pg.execute(
            f"delete from {_TABLE} where interaction_id between $1 and $2",
            str(base),
            str(base + 999),
        )


async def test_the_real_functions_give_the_scenario_the_same_answers_as_the_fake(
    ledger: _RealLedger,
) -> None:
    # The scenario's leases are Telegram's (600 s and up); it uses its own explicit ones, so the
    # Discord default (900 s) is pinned separately below.
    assert await run_scenario(ledger) == SCENARIO_EXPECTED
    assert await run_scenario(FakeInteractionLedger()) == SCENARIO_EXPECTED


async def test_eight_deliveries_of_the_same_interaction_at_once_produce_one_owner(
    ledger: _RealLedger,
) -> None:
    """Arbitrated by the primary-key index: the losers wait on the winner's uncommitted row and
    then find the WHERE false. No 23505 may escape."""
    answers = await asyncio.gather(*(ledger.claim(1) for _ in range(8)))

    assert sorted(answers) == ["claimed"] + ["in_progress"] * 7


async def test_eight_deliveries_racing_for_an_expired_claim_produce_one_owner(
    ledger: _RealLedger,
) -> None:
    await ledger.claim(1, 600)
    await ledger.age(1, 700)

    answers = await asyncio.gather(*(ledger.claim(1, 600) for _ in range(8)))

    assert sorted(answers) == ["claimed"] + ["in_progress"] * 7


async def test_a_completed_interaction_is_never_taken_over_however_old(
    ledger: _RealLedger,
) -> None:
    await ledger.claim(1)
    await ledger.complete(1)
    await ledger.age(1, 6 * 86400)  # still inside the 7 days a row is kept

    assert await ledger.claim(1, 1) == "done"  # even with a 1 second lease


async def test_the_default_lease_is_fifteen_minutes_the_life_of_an_interaction_token(
    ledger: _RealLedger, world: World
) -> None:
    async def claim_with_default() -> Any:
        result = await world.sb.rpc(
            "claim_discord_interaction", {"p_interaction_id": ledger._id(1)}
        ).execute()
        return result.data

    assert await claim_with_default() == "claimed"
    await ledger.age(1, 899)
    assert await claim_with_default() == "in_progress"  # 899 s old is inside the default
    await ledger.age(1, 2)
    assert await claim_with_default() == "claimed"  # 901 s old: expired under the default


@pytest.mark.parametrize("lease", [0, -1, 86401, None])
async def test_an_absurd_lease_is_an_error_not_a_claim(
    ledger: _RealLedger, world: World, lease: int | None
) -> None:
    with pytest.raises(APIError) as refused:
        await world.sb.rpc(
            "claim_discord_interaction",
            {"p_interaction_id": ledger._id(1), "p_lease_seconds": lease},
        ).execute()
    assert refused.value.code == "22023"
    assert not await ledger.known(1)


@pytest.mark.parametrize(
    "interaction_id",
    ["", "abc", "12 34", "-5", "1.5", "1" * 26, "\uff11\uff12\uff13", "1; drop table x"],
)
async def test_an_id_that_is_not_a_discord_id_is_refused_and_nothing_is_stored(
    world: World, interaction_id: str
) -> None:
    before = await world.pg.fetchval(f"select count(*) from {_TABLE}")
    for name in (
        "claim_discord_interaction",
        "complete_discord_interaction",
        "release_discord_interaction",
    ):
        params: dict[str, Any] = {"p_interaction_id": interaction_id}
        if name == "claim_discord_interaction":
            with pytest.raises(APIError) as refused:
                await world.sb.rpc(name, params).execute()
            assert refused.value.code == "22023"
        else:
            await world.sb.rpc(name, params).execute()  # matches nothing, changes nothing
    assert await world.pg.fetchval(f"select count(*) from {_TABLE}") == before


async def test_the_table_itself_refuses_an_id_that_is_not_digits(world: World) -> None:
    with pytest.raises(Exception) as refused:
        await world.pg.execute(f"insert into {_TABLE} (interaction_id) values ('not-digits')")
    assert "check" in str(refused.value).lower()


async def test_the_largest_snowflakes_fit_because_ids_are_text(
    ledger: _RealLedger, world: World
) -> None:
    huge = "9" * 20  # beyond a signed 64-bit integer: Discord ids are up to 64 bits unsigned
    try:
        answer = await world.sb.rpc(
            "claim_discord_interaction", {"p_interaction_id": huge}
        ).execute()
        assert answer.data == "claimed"
    finally:
        await world.pg.execute(f"delete from {_TABLE} where interaction_id = $1", huge)


async def test_a_claim_released_between_the_conflict_and_the_lookup_reads_as_in_progress(
    ledger: _RealLedger,
) -> None:
    assert await ledger.claim(1) == "claimed"
    await ledger.release(1)

    assert await ledger.claim(1) == "claimed"  # the plain case: released means free


async def test_the_purge_is_bounded_per_claim(ledger: _RealLedger, world: World) -> None:
    """Each claim deletes at most 200 rows older than seven days, so one claim can never turn
    into a long delete."""
    await world.pg.execute(
        f"insert into {_TABLE} (interaction_id, claimed_at, completed_at) "
        "select ($1::numeric + n)::text, now() - interval '8 days', now() - interval '8 days' "
        "from generate_series(100, 449) n",
        str(ledger.base),
    )

    await ledger.claim(1)

    remaining = await world.pg.fetchval(
        f"select count(*) from {_TABLE} "
        "where interaction_id::numeric between $1::numeric + 100 and $1::numeric + 449",
        str(ledger.base),
    )
    assert remaining == 150  # 350 old rows, 200 deleted by that one claim


async def test_only_the_backend_can_call_them_and_nobody_can_touch_the_table(
    world: World,
) -> None:
    stack = world.stack
    anon = await acreate_client(stack["API_URL"], stack["ANON_KEY"])
    password = uuid.uuid4().hex
    email = f"dedup-{uuid.uuid4().hex}@example.com"
    await world.web_user(password=password, email=email)
    signed_in = await acreate_client(stack["API_URL"], stack["ANON_KEY"])
    await signed_in.auth.sign_in_with_password({"email": email, "password": password})

    for client in (anon, signed_in):
        for name in (
            "claim_discord_interaction",
            "complete_discord_interaction",
            "release_discord_interaction",
        ):
            with pytest.raises(APIError):
                await client.rpc(name, {"p_interaction_id": "1"}).execute()

    for client in (anon, signed_in, world.sb):
        with pytest.raises(APIError) as read:
            await client.table("discord_processed_interactions").select("*").execute()
        assert read.value.code == "42501"
        with pytest.raises(APIError) as write:
            await (
                client.table("discord_processed_interactions")
                .insert({"interaction_id": "1"})
                .execute()
            )
        assert write.value.code == "42501"


async def test_row_level_security_is_on_and_no_policy_exists(world: World) -> None:
    pg: Any = world.pg
    assert await pg.fetchval("select relrowsecurity from pg_class where oid = $1::regclass", _TABLE)
    assert (
        await pg.fetchval(
            "select count(*) from pg_policies where tablename = 'discord_processed_interactions'"
        )
        == 0
    )


async def test_the_functions_pin_their_search_path(world: World) -> None:
    pg: Any = world.pg
    for function in _FUNCTIONS:
        config = await pg.fetchval(
            "select proconfig from pg_proc where oid = $1::regprocedure", function
        )
        assert config is not None and any(c.startswith("search_path=") for c in config), function
        assert await pg.fetchval(
            "select prosecdef from pg_proc where oid = $1::regprocedure", function
        )


class _Rollback(Exception):
    """Raised to end the test's transaction so it is always rolled back."""


async def test_the_migrations_own_revokes_close_everything_even_when_the_project_grants_by_default(
    world: World,
) -> None:
    """Prod grants new tables and functions to anon, authenticated and service_role by default.
    Put the objects in that state, replay exactly the GRANT and REVOKE statements this migration
    contains for them, and check what is left -- inside a transaction that is always rolled
    back."""
    sql = re.sub(r"--[^\n]*", "", _MIGRATION.read_text())
    statements = [
        statement.strip()
        for statement in sql.split(";")
        if statement.strip().lower().startswith(("revoke", "grant"))
        and ("discord_processed_interactions" in statement or "discord_interaction" in statement)
    ]
    assert len(statements) == 7  # the table, then a revoke and a grant per function
    pg: Any = world.pg
    try:
        async with pg.transaction():
            await pg.execute(f"grant all on table {_TABLE} to anon, authenticated, service_role")
            for function in _FUNCTIONS:
                await pg.execute(
                    f"grant execute on function {function} "
                    "to public, anon, authenticated, service_role"
                )
            for statement in statements:
                await pg.execute(statement)

            for role in ("anon", "authenticated", "service_role"):
                for privilege in ("select", "insert", "update", "delete"):
                    assert not await pg.fetchval(
                        "select has_table_privilege($1, $2, $3)", role, _TABLE, privilege
                    ), f"{role} can still {privilege} the table"
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
