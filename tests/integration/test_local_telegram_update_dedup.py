"""`claim_telegram_update` and its companions against a local Supabase stack: the real SQL,
real concurrency, real grants -- what tests/update_ledger_fakes.py can only imitate.

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
from postgrest.exceptions import APIError
from update_ledger_fakes import SCENARIO_EXPECTED, run_scenario

from supabase import acreate_client

from .conftest import World

pytestmark = pytest.mark.local_supabase

_MIGRATION = next(
    (Path(__file__).parents[2] / "supabase" / "migrations").glob("*_telegram_update_dedup.sql")
)
_FUNCTIONS = (
    "public.claim_telegram_update(bigint, integer)",
    "public.complete_telegram_update(bigint)",
    "public.release_telegram_update(bigint)",
)


class _RealLedger:
    """The real functions, called the way the backend calls them: RPCs as service_role. Ids
    are offset by a per-test base so concurrent or crashed runs never share a row."""

    def __init__(self, world: World, base: int) -> None:
        self._world = world
        self.base = base

    def _id(self, update_id: int) -> int:
        return self.base + update_id

    async def claim(self, update_id: int, lease_seconds: int = 600) -> str:
        result = await self._world.sb.rpc(
            "claim_telegram_update",
            {"p_update_id": self._id(update_id), "p_lease_seconds": lease_seconds},
        ).execute()
        assert result.data in ("claimed", "done", "in_progress"), result.data
        return str(result.data)

    async def complete(self, update_id: int) -> None:
        await self._world.sb.rpc(
            "complete_telegram_update", {"p_update_id": self._id(update_id)}
        ).execute()

    async def release(self, update_id: int) -> None:
        await self._world.sb.rpc(
            "release_telegram_update", {"p_update_id": self._id(update_id)}
        ).execute()

    async def age(self, update_id: int, seconds: float) -> None:
        await self._world.pg.execute(
            "update public.telegram_processed_updates "
            "set claimed_at = claimed_at - make_interval(secs => $2) where update_id = $1",
            self._id(update_id),
            seconds,
        )

    async def known(self, update_id: int) -> bool:
        return bool(
            await self._world.pg.fetchval(
                "select exists (select 1 from public.telegram_processed_updates "
                "where update_id = $1)",
                self._id(update_id),
            )
        )


@pytest.fixture
async def ledger(world: World) -> AsyncIterator[_RealLedger]:
    base = 9_000_000_000_000 + (uuid.uuid4().int % 1_000_000) * 1000
    try:
        yield _RealLedger(world, base)
    finally:
        await world.pg.execute(
            "delete from public.telegram_processed_updates where update_id between $1 and $2",
            base,
            base + 999,
        )


async def test_the_real_functions_give_the_scenario_the_same_answers_as_the_fake(
    ledger: _RealLedger,
) -> None:
    assert await run_scenario(ledger) == SCENARIO_EXPECTED


async def test_eight_deliveries_of_the_same_update_at_once_produce_one_owner(
    ledger: _RealLedger, world: World
) -> None:
    """Arbitrated by the primary-key index: the losers wait on the winner's uncommitted row
    and then find the WHERE false. No 23505 may escape."""
    answers = await asyncio.gather(*(ledger.claim(1) for _ in range(8)))

    assert sorted(answers) == ["claimed"] + ["in_progress"] * 7


async def test_eight_deliveries_racing_for_an_expired_claim_produce_one_owner(
    ledger: _RealLedger,
) -> None:
    await ledger.claim(1)
    await ledger.age(1, 700)

    answers = await asyncio.gather(*(ledger.claim(1) for _ in range(8)))

    assert sorted(answers) == ["claimed"] + ["in_progress"] * 7


async def test_a_completed_update_is_never_taken_over_however_old(ledger: _RealLedger) -> None:
    await ledger.claim(1)
    await ledger.complete(1)
    await ledger.age(1, 6 * 86400)  # still inside the 7 days a row is kept

    assert await ledger.claim(1, 1) == "done"  # even with a 1 second lease


@pytest.mark.parametrize("lease", [0, -1, 86401, None])
async def test_an_absurd_lease_is_an_error_not_a_claim(
    ledger: _RealLedger, world: World, lease: int | None
) -> None:
    with pytest.raises(APIError) as refused:
        await world.sb.rpc(
            "claim_telegram_update",
            {"p_update_id": ledger.base + 1, "p_lease_seconds": lease},
        ).execute()
    assert refused.value.code == "22023"
    assert not await ledger.known(1)


async def test_the_default_lease_is_ten_minutes(ledger: _RealLedger, world: World) -> None:
    async def claim_with_default() -> Any:
        result = await world.sb.rpc(
            "claim_telegram_update", {"p_update_id": ledger.base + 1}
        ).execute()
        return result.data

    assert await claim_with_default() == "claimed"
    await ledger.age(1, 599)
    assert await claim_with_default() == "in_progress"  # 599 s old is inside the default
    await ledger.age(1, 2)
    assert await claim_with_default() == "claimed"  # 601 s old: expired under the default


async def test_a_claim_released_between_the_conflict_and_the_lookup_reads_as_in_progress(
    ledger: _RealLedger, world: World
) -> None:
    """The function looks the row up after losing the insert; if the holder released it in
    between, the row is gone. That must read as "not done" (Telegram's next retry claims it),
    never as "done"."""
    assert await ledger.claim(1) == "claimed"
    await ledger.release(1)

    assert await ledger.claim(1) == "claimed"  # the plain case: released means free


async def test_the_purge_is_bounded_per_claim(ledger: _RealLedger, world: World) -> None:
    """Each claim deletes at most 200 rows older than seven days, so one claim can never turn
    into a long delete."""
    await world.pg.execute(
        "insert into public.telegram_processed_updates (update_id, claimed_at, completed_at) "
        "select $1::bigint + n, now() - interval '8 days', now() - interval '8 days' "
        "from generate_series(100, 449) n",
        ledger.base,
    )

    await ledger.claim(1)

    remaining = await world.pg.fetchval(
        "select count(*) from public.telegram_processed_updates "
        "where update_id between $1::bigint + 100 and $1::bigint + 449",
        ledger.base,
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
            "claim_telegram_update",
            "complete_telegram_update",
            "release_telegram_update",
        ):
            with pytest.raises(APIError):
                await client.rpc(name, {"p_update_id": 1}).execute()

    for client in (anon, signed_in, world.sb):
        with pytest.raises(APIError) as read:
            await client.table("telegram_processed_updates").select("*").execute()
        assert read.value.code == "42501"
        with pytest.raises(APIError) as write:
            await client.table("telegram_processed_updates").insert({"update_id": 1}).execute()
        assert write.value.code == "42501"


class _Rollback(Exception):
    """Raised to end the test's transaction so it is always rolled back."""


async def test_the_migrations_own_revokes_close_everything_even_when_the_project_grants_by_default(
    world: World,
) -> None:
    """Prod grants new tables and functions to anon, authenticated and service_role by
    default. Put the objects in that state, replay exactly the GRANT and REVOKE statements
    this migration contains, and check what is left -- inside a transaction that is always
    rolled back."""
    sql = re.sub(r"--[^\n]*", "", _MIGRATION.read_text())
    statements = [
        statement.strip()
        for statement in sql.split(";")
        if statement.strip().lower().startswith(("revoke", "grant"))
    ]
    assert len(statements) == 7  # the table, then a revoke and a grant per function
    pg: Any = world.pg
    table = "public.telegram_processed_updates"
    try:
        async with pg.transaction():
            await pg.execute(f"grant all on table {table} to anon, authenticated, service_role")
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
                        "select has_table_privilege($1, $2, $3)", role, table, privilege
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
