"""`claim_worker_lease` against a local Supabase stack: the real SQL, real
concurrency, real grants -- what tests/lease_fakes.py can only imitate.

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
from lease_fakes import SCENARIO, run_scenario
from postgrest.exceptions import APIError

from supabase import acreate_client

from .conftest import World

pytestmark = pytest.mark.local_supabase


class _RealLease:
    """The real function, called the way the backend calls it: an RPC as service_role."""

    def __init__(self, world: World) -> None:
        self._world = world

    async def claim(self, worker: str, holder: str, ttl_seconds: int) -> bool:
        result = await self._world.sb.rpc(
            "claim_worker_lease",
            {"p_worker": worker, "p_holder": holder, "p_ttl_seconds": ttl_seconds},
        ).execute()
        assert result.data is True or result.data is False, result.data
        return bool(result.data)

    async def expire(self, worker: str) -> None:
        await self._world.pg.execute(
            "update public.worker_leases set expires_at = now() where worker = $1", worker
        )


@pytest.fixture
async def lease(world: World) -> AsyncIterator[_RealLease]:
    try:
        yield _RealLease(world)
    finally:
        await world.pg.execute("delete from public.worker_leases where worker like 'test-%'")


def _worker() -> str:
    return f"test-{uuid.uuid4().hex[:12]}"


async def test_the_real_function_gives_the_scenario_the_same_answers_as_the_fake(
    lease: _RealLease,
) -> None:
    assert await run_scenario(lease, _worker()) == SCENARIO


async def test_a_renewal_extends_the_lease_by_the_ttl_on_the_database_clock(
    lease: _RealLease, world: World
) -> None:
    worker = _worker()
    assert await lease.claim(worker, "A", 60)

    remaining = await world.pg.fetchval(
        "select extract(epoch from expires_at - now()) from public.worker_leases where worker = $1",
        worker,
    )

    assert 55 <= remaining <= 60  # the transaction's own now() + 60, a moment ago


async def test_a_refusal_changes_nothing(lease: _RealLease, world: World) -> None:
    worker = _worker()
    await lease.claim(worker, "A", 60)
    before = await world.pg.fetchrow("select * from public.worker_leases where worker = $1", worker)

    assert await lease.claim(worker, "B", 3600) is False

    after = await world.pg.fetchrow("select * from public.worker_leases where worker = $1", worker)
    assert dict(after) == dict(before)  # B's long TTL did not reach A's row


async def test_a_lease_that_runs_out_by_the_clock_can_be_taken(lease: _RealLease) -> None:
    worker = _worker()
    assert await lease.claim(worker, "A", 1)
    assert await lease.claim(worker, "B", 60) is False

    await asyncio.sleep(1.3)

    assert await lease.claim(worker, "B", 60) is True


async def test_eight_processes_claiming_a_fresh_lease_at_once_produce_one_holder(
    lease: _RealLease, world: World
) -> None:
    """Arbitrated by the primary-key index: the losers wait on the winner's
    uncommitted row and then find the WHERE false. No 23505 may escape."""
    worker = _worker()

    answers = await asyncio.gather(*(lease.claim(worker, f"holder-{n}", 60) for n in range(8)))

    assert sorted(answers) == [False] * 7 + [True]
    holder = await world.pg.fetchval(
        "select holder from public.worker_leases where worker = $1", worker
    )
    assert holder == f"holder-{answers.index(True)}"


async def test_eight_processes_racing_for_an_expired_lease_produce_one_holder(
    lease: _RealLease, world: World
) -> None:
    worker = _worker()
    await lease.claim(worker, "old", 60)
    await lease.expire(worker)

    answers = await asyncio.gather(*(lease.claim(worker, f"new-{n}", 60) for n in range(8)))

    assert sorted(answers) == [False] * 7 + [True]
    assert (
        await world.pg.fetchval("select holder from public.worker_leases where worker = $1", worker)
        == f"new-{answers.index(True)}"
    )


@pytest.mark.parametrize("ttl", [0, -5, 3601, None])
async def test_an_absurd_ttl_is_an_error_not_a_lease(
    lease: _RealLease, world: World, ttl: int | None
) -> None:
    worker = _worker()
    with pytest.raises(APIError) as refused:
        await world.sb.rpc(
            "claim_worker_lease", {"p_worker": worker, "p_holder": "A", "p_ttl_seconds": ttl}
        ).execute()
    assert refused.value.code == "22023"
    assert (
        await world.pg.fetchval(
            "select count(*) from public.worker_leases where worker = $1", worker
        )
        == 0
    )


@pytest.mark.parametrize(("worker", "holder"), [("", "A"), ("w", "")])
async def test_an_empty_worker_or_holder_is_refused_by_the_table(
    world: World, worker: str, holder: str
) -> None:
    with pytest.raises(APIError) as refused:
        await world.sb.rpc(
            "claim_worker_lease", {"p_worker": worker, "p_holder": holder, "p_ttl_seconds": 60}
        ).execute()
    assert refused.value.code == "23514"  # check_violation


async def test_only_the_backend_can_call_it_and_nobody_can_touch_the_table(world: World) -> None:
    stack = world.stack
    args: dict[str, Any] = {"p_worker": _worker(), "p_holder": "A", "p_ttl_seconds": 60}
    anon = await acreate_client(stack["API_URL"], stack["ANON_KEY"])
    password = uuid.uuid4().hex
    email = f"lease-{uuid.uuid4().hex}@example.com"
    await world.web_user(password=password, email=email)
    signed_in = await acreate_client(stack["API_URL"], stack["ANON_KEY"])
    await signed_in.auth.sign_in_with_password({"email": email, "password": password})

    for client in (anon, signed_in):
        with pytest.raises(APIError):
            await client.rpc("claim_worker_lease", args).execute()

    # The table: not even the backend's own role reads or writes it through the API
    # (the function is SECURITY DEFINER; the backend needs no table privileges).
    for client in (anon, signed_in, world.sb):
        with pytest.raises(APIError) as read:
            await client.table("worker_leases").select("*").execute()
        assert read.value.code == "42501"
        with pytest.raises(APIError) as write:
            await (
                client.table("worker_leases")
                .insert({"worker": "x", "holder": "y", "expires_at": "2030-01-01"})
                .execute()
            )
        assert write.value.code == "42501"


_MIGRATION = next(
    (Path(__file__).parents[2] / "supabase" / "migrations").glob("*_create_worker_leases.sql")
)


def _grant_and_revoke_statements() -> list[str]:
    """The migration's own GRANT and REVOKE statements, in file order. The function
    body contains semicolons, but nothing inside it starts with either keyword."""
    sql = re.sub(r"--[^\n]*", "", _MIGRATION.read_text())
    return [
        statement.strip()
        for statement in sql.split(";")
        if statement.strip().lower().startswith(("revoke", "grant"))
    ]


class _Rollback(Exception):
    """Raised to end the test's transaction so it is always rolled back."""


async def test_the_migrations_own_revokes_close_everything_even_when_the_project_grants_by_default(
    world: World,
) -> None:
    """A fresh local stack grants new objects to nobody, so the other tests here would
    pass even if the migration revoked nothing. Prod's default privileges grant new
    tables and functions to anon, authenticated and service_role -- which is how two
    internal link helpers ended up callable by service_role there. So: put these two
    objects in prod's state, replay exactly the statements the migration contains, and
    check what is left.

    All of it inside one transaction that is always rolled back (GRANT and REVOKE are
    transactional), so neither a pass nor a failure can leave this stack's ACLs changed
    for the tests that follow."""
    pg = world.pg
    table = "public.worker_leases"
    function = "public.claim_worker_lease(text, text, integer)"

    try:
        async with pg.transaction():
            await pg.execute(f"grant all on table {table} to anon, authenticated, service_role")
            await pg.execute(
                f"grant execute on function {function} to public, anon, authenticated, service_role"
            )
            assert await pg.fetchval("select has_table_privilege('anon', $1, 'select')", table)
            assert await pg.fetchval(
                "select has_function_privilege('authenticated', $1, 'execute')", function
            )

            statements = _grant_and_revoke_statements()
            assert len(statements) == 3  # revoke table, revoke function, grant function
            for statement in statements:
                await pg.execute(statement)

            for role in ("anon", "authenticated", "service_role"):
                for privilege in ("select", "insert", "update", "delete"):
                    assert not await pg.fetchval(
                        "select has_table_privilege($1, $2, $3)", role, table, privilege
                    ), f"{role} can still {privilege} {table}"
            for role in ("anon", "authenticated"):
                assert not await pg.fetchval(
                    "select has_function_privilege($1, $2, 'execute')", role, function
                ), f"{role} can still execute the function"
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
            assert not public_can_execute  # PUBLIC, which every role inherits
            raise _Rollback
    except _Rollback:
        pass
