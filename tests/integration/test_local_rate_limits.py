"""`claim_rate_limit_slot` against a local Supabase stack: the real SQL, real concurrency, real
grants -- what no fake can show.

The question the counter has to answer correctly is the one a fake cannot: many requests from one
user arriving at the same instant must yield exactly the allowed number of admissions, no more
and no fewer, with no error for the unlucky ones.

Run with `pytest -m local_supabase` after `supabase start` and `supabase db reset --local`."""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from postgrest.exceptions import APIError

from between_jobs.api import rate_limits
from supabase import AsyncClient, acreate_client

from .conftest import World

pytestmark = pytest.mark.local_supabase


def _bucket() -> str:
    return f"test-{uuid.uuid4().hex[:12]}"


async def _claim(
    sb: AsyncClient, user_id: str, bucket: str, *, window: int, max_requests: int
) -> tuple[bool, int]:
    """The real function, called the way the backend calls it: an RPC as service_role."""
    result = await sb.rpc(
        "claim_rate_limit_slot",
        {
            "p_user_id": user_id,
            "p_bucket": bucket,
            "p_window_seconds": window,
            "p_max_requests": max_requests,
        },
    ).execute()
    rows = result.data
    assert isinstance(rows, list) and len(rows) == 1, rows
    row = rows[0]
    assert set(row) == {"allowed", "retry_after_seconds"}, row
    assert isinstance(row["allowed"], bool)
    assert isinstance(row["retry_after_seconds"], int)
    return row["allowed"], row["retry_after_seconds"]


async def _row(world: World, user_id: str, bucket: str) -> Any:
    return await world.pg.fetchrow(
        "select request_count, window_started_at from public.api_rate_limits "
        "where user_id = $1 and bucket = $2",
        uuid.UUID(user_id),
        bucket,
    )


@pytest.fixture
async def clients(local_stack: dict[str, str]) -> AsyncIterator[list[AsyncClient]]:
    """Several independent clients, so concurrent claims really are separate connections."""
    made = [
        await acreate_client(local_stack["API_URL"], local_stack["SERVICE_ROLE_KEY"])
        for _ in range(6)
    ]
    yield made


# -- the contract --------------------------------------------------------------------------


async def test_the_first_claims_are_allowed_up_to_the_maximum_then_refused(world: World) -> None:
    user = await world.web_user()
    bucket = _bucket()

    answers = [await _claim(world.sb, user, bucket, window=60, max_requests=3) for _ in range(5)]

    assert [allowed for allowed, _ in answers] == [True, True, True, False, False]
    assert [retry for allowed, retry in answers if allowed] == [0, 0, 0]
    row = await _row(world, user, bucket)
    assert row["request_count"] == 3  # a refusal is not counted


async def test_a_refusal_says_how_long_to_wait_between_one_second_and_the_window(
    world: World,
) -> None:
    user = await world.web_user()
    bucket = _bucket()
    await _claim(world.sb, user, bucket, window=30, max_requests=1)

    allowed, retry_after = await _claim(world.sb, user, bucket, window=30, max_requests=1)

    assert allowed is False
    assert 1 <= retry_after <= 30
    assert retry_after >= 28  # it has barely started


@pytest.mark.parametrize(
    ("elapsed", "window", "expected_retry_after"),
    [
        # What is left is x.3 s or x.7 s: a fraction that ceil, floor and round tell apart (a
        # rounding-down mutant says 2 where this says 3), and far enough from a whole second that
        # the few milliseconds between the update and the claim cannot change the answer.
        (7.3, 10, 3),  # 2.7 s left, rounded UP
        (7.7, 10, 3),  # 2.3 s left: UP, not to the nearest
        (3.7, 10, 7),  # 6.3 s left
        (100.7, 3600, 3500),  # 3499.3 s left
        (9.9, 10, 1),  # a hair left: never less than one second
        (0.0, 10, 10),  # a full window is the most it can ever say
        (1.0, 3600, 3599),  # 3599 s less a few ms is still 3599 once rounded up
    ],
)
async def test_the_wait_is_rounded_up_to_whole_seconds_and_stays_within_one_and_the_window(
    world: World, elapsed: float, window: int, expected_retry_after: int
) -> None:
    user = await world.web_user()
    bucket = _bucket()
    await _claim(world.sb, user, bucket, window=window, max_requests=1)
    await world.pg.execute(
        "update public.api_rate_limits set window_started_at = now() - $3::interval "
        "where user_id = $1 and bucket = $2",
        uuid.UUID(user),
        bucket,
        timedelta(seconds=elapsed),
    )

    allowed, retry_after = await _claim(world.sb, user, bucket, window=window, max_requests=1)

    assert allowed is False
    assert retry_after == expected_retry_after
    assert 1 <= retry_after <= window


async def test_the_window_resets_and_counting_starts_again(world: World) -> None:
    user = await world.web_user()
    bucket = _bucket()
    assert await _claim(world.sb, user, bucket, window=2, max_requests=2) == (True, 0)
    assert await _claim(world.sb, user, bucket, window=2, max_requests=2) == (True, 0)
    refused, retry_after = await _claim(world.sb, user, bucket, window=2, max_requests=2)
    assert refused is False
    assert 1 <= retry_after <= 2
    before = await _row(world, user, bucket)

    await asyncio.sleep(2.3)

    assert await _claim(world.sb, user, bucket, window=2, max_requests=2) == (True, 0)
    after = await _row(world, user, bucket)
    assert after["request_count"] == 1  # a fresh window, one request into it
    assert after["window_started_at"] > before["window_started_at"]
    assert await _claim(world.sb, user, bucket, window=2, max_requests=2) == (True, 0)
    assert (await _claim(world.sb, user, bucket, window=2, max_requests=2))[0] is False


async def test_a_window_that_has_ended_admits_even_after_a_long_refusal_streak(
    world: World,
) -> None:
    user = await world.web_user()
    bucket = _bucket()
    await _claim(world.sb, user, bucket, window=60, max_requests=1)
    for _ in range(3):
        assert (await _claim(world.sb, user, bucket, window=60, max_requests=1))[0] is False
    await world.pg.execute(
        "update public.api_rate_limits set window_started_at = now() - interval '61 seconds' "
        "where user_id = $1 and bucket = $2",
        uuid.UUID(user),
        bucket,
    )

    assert await _claim(world.sb, user, bucket, window=60, max_requests=1) == (True, 0)


async def test_another_user_and_another_bucket_are_independent(world: World) -> None:
    alice = await world.web_user()
    bob = await world.web_user()
    bucket, other_bucket = _bucket(), _bucket()

    assert (await _claim(world.sb, alice, bucket, window=60, max_requests=1))[0] is True
    assert (await _claim(world.sb, alice, bucket, window=60, max_requests=1))[0] is False

    assert (await _claim(world.sb, bob, bucket, window=60, max_requests=1))[0] is True  # a user
    assert (await _claim(world.sb, alice, other_bucket, window=60, max_requests=1))[0] is True


async def test_a_different_limit_for_the_same_bucket_is_applied_to_the_existing_count(
    world: World,
) -> None:
    """The numbers are parameters, not stored: lowering one takes effect on the very next call."""
    user = await world.web_user()
    bucket = _bucket()
    for _ in range(3):
        assert (await _claim(world.sb, user, bucket, window=60, max_requests=10))[0] is True

    assert (await _claim(world.sb, user, bucket, window=60, max_requests=3))[0] is False
    assert (await _claim(world.sb, user, bucket, window=60, max_requests=4))[0] is True


# -- atomicity -----------------------------------------------------------------------------


@pytest.mark.parametrize(("attempts", "maximum"), [(20, 5), (12, 12), (9, 1)])
async def test_concurrent_claims_admit_exactly_the_maximum_and_refuse_the_rest(
    world: World, clients: list[AsyncClient], attempts: int, maximum: int
) -> None:
    """The first-ever claims race too: nobody has a row yet, so this is the case where a
    read-then-insert version dies with a duplicate-key error."""
    user = await world.web_user()
    bucket = _bucket()

    results = await asyncio.gather(
        *(
            _claim(clients[i % len(clients)], user, bucket, window=60, max_requests=maximum)
            for i in range(attempts)
        )
    )

    allowed = [r for r in results if r[0]]
    refused = [r for r in results if not r[0]]
    assert len(allowed) == min(attempts, maximum)
    assert len(refused) == attempts - min(attempts, maximum)
    assert all(1 <= retry <= 60 for _, retry in refused)
    assert (await _row(world, user, bucket))["request_count"] == min(attempts, maximum)


async def test_concurrent_claims_across_a_window_reset_never_over_admit(
    world: World, clients: list[AsyncClient]
) -> None:
    """Many callers all find the window expired at once: exactly one resets it, the rest count
    into the new window, and the new window still admits only the maximum."""
    user = await world.web_user()
    bucket = _bucket()
    await _claim(world.sb, user, bucket, window=60, max_requests=3)
    await world.pg.execute(
        "update public.api_rate_limits set window_started_at = now() - interval '2 minutes' "
        "where user_id = $1 and bucket = $2",
        uuid.UUID(user),
        bucket,
    )

    results = await asyncio.gather(
        *(
            _claim(clients[i % len(clients)], user, bucket, window=60, max_requests=3)
            for i in range(10)
        )
    )

    assert sum(1 for allowed, _ in results if allowed) == 3
    assert (await _row(world, user, bucket))["request_count"] == 3


async def test_concurrent_users_are_counted_separately(
    world: World, clients: list[AsyncClient]
) -> None:
    users = [await world.web_user() for _ in range(3)]
    bucket = _bucket()

    results = await asyncio.gather(
        *(
            _claim(clients[i % len(clients)], user, bucket, window=60, max_requests=2)
            for i in range(6)
            for user in users
        )
    )

    assert sum(1 for allowed, _ in results if allowed) == 6  # two per user, three users


# -- the Python side, through the real function --------------------------------------------


async def test_the_python_claim_parses_the_real_answer(world: World) -> None:
    user = await world.web_user()
    # tests/conftest.py puts a double in place of this function for unit tests only; a
    # local_supabase test gets the real one.
    maximum, _window = rate_limits.RATE_LIMITS["prepare"]

    decisions = [
        await rate_limits.claim_rate_limit_slot(world.sb, user, "prepare")
        for _ in range(maximum + 1)
    ]

    assert all(d.allowed for d in decisions[:maximum])
    assert decisions[maximum].allowed is False
    assert 1 <= decisions[maximum].retry_after_seconds <= 3600
    await world.pg.execute("delete from public.api_rate_limits where user_id = $1", uuid.UUID(user))


# -- input checks --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {"p_bucket": ""},
        {"p_bucket": "   "},
        {"p_bucket": "b" * 101},
        {"p_window_seconds": 0},
        {"p_window_seconds": -5},
        {"p_max_requests": 0},
        {"p_max_requests": -1},
    ],
)
async def test_nonsense_inputs_are_an_error_and_write_nothing(
    world: World, params: dict[str, Any]
) -> None:
    user = await world.web_user()
    arguments: dict[str, Any] = {
        "p_user_id": user,
        "p_bucket": _bucket(),
        "p_window_seconds": 60,
        "p_max_requests": 5,
        **params,
    }

    with pytest.raises(APIError) as raised:
        await world.sb.rpc("claim_rate_limit_slot", arguments).execute()

    assert raised.value.code == "22023"  # invalid_parameter_value
    assert (
        await world.pg.fetchval(
            "select count(*) from public.api_rate_limits where user_id = $1", uuid.UUID(user)
        )
        == 0
    )


async def test_a_missing_user_id_is_an_error(world: World) -> None:
    with pytest.raises(APIError) as raised:
        await world.sb.rpc(
            "claim_rate_limit_slot",
            {
                "p_user_id": None,
                "p_bucket": _bucket(),
                "p_window_seconds": 60,
                "p_max_requests": 5,
            },
        ).execute()
    assert raised.value.code in {"22023", "PGRST202"}  # null is refused before or inside


async def test_a_user_that_does_not_exist_is_a_foreign_key_error_not_a_free_pass(
    world: World,
) -> None:
    with pytest.raises(APIError) as raised:
        await _claim(world.sb, str(uuid.uuid4()), _bucket(), window=60, max_requests=5)
    assert raised.value.code == "23503"


# -- who may touch it ----------------------------------------------------------------------


async def test_no_user_token_can_read_write_or_call_anything_here(
    world: World, local_stack: dict[str, str]
) -> None:
    email = f"rl-{uuid.uuid4().hex[:10]}@example.com"
    password = uuid.uuid4().hex
    created = await world.sb.auth.admin.create_user(
        {"email": email, "password": password, "email_confirm": True}
    )
    user_id = str(created.user.id)
    world.users.append(user_id)
    await _claim(world.sb, user_id, _bucket(), window=60, max_requests=5)

    anonymous = await acreate_client(local_stack["API_URL"], local_stack["ANON_KEY"])
    signed_in = await acreate_client(local_stack["API_URL"], local_stack["ANON_KEY"])
    session = await signed_in.auth.sign_in_with_password({"email": email, "password": password})
    assert session.session is not None  # now an `authenticated` caller

    row: dict[str, Any] = {
        "user_id": user_id,
        "bucket": "x",
        "window_started_at": "2026-01-01T00:00:00Z",
        "request_count": 0,
    }
    call = {"p_user_id": user_id, "p_bucket": "x", "p_window_seconds": 60, "p_max_requests": 5}
    for client in (anonymous, signed_in):
        with pytest.raises(APIError):
            await client.table("api_rate_limits").select("*").execute()
        with pytest.raises(APIError):
            await client.table("api_rate_limits").insert(row).execute()
        with pytest.raises(APIError):
            await client.rpc("claim_rate_limit_slot", call).execute()


async def test_the_grants_and_policies_are_exactly_service_role_only(world: World) -> None:
    pg = world.pg
    table = "public.api_rate_limits"
    function = "public.claim_rate_limit_slot(uuid, text, integer, integer)"

    assert await pg.fetchval("select relrowsecurity from pg_class where oid = $1::regclass", table)
    assert (
        await pg.fetchval("select count(*) from pg_policies where tablename = 'api_rate_limits'")
        == 0
    )
    for role in ("anon", "authenticated"):
        for privilege in ("select", "insert", "update", "delete"):
            assert not await pg.fetchval(
                "select has_table_privilege($1, $2, $3)", role, table, privilege
            ), f"{role} can {privilege} {table}"
        assert not await pg.fetchval(
            "select has_function_privilege($1, $2, 'execute')", role, function
        ), f"{role} can execute the function"
    for privilege in ("select", "insert", "update", "delete"):
        assert await pg.fetchval(
            "select has_table_privilege('service_role', $1, $2)", table, privilege
        )
    assert await pg.fetchval(
        "select has_function_privilege('service_role', $1, 'execute')", function
    )
    public_can_execute = await pg.fetchval(
        """
        select exists (
          select 1 from pg_proc p, aclexplode(p.proacl) a
          where p.oid = $1::regprocedure and a.grantee = 0 and a.privilege_type = 'EXECUTE'
        )
        """,
        function,
    )
    assert not public_can_execute


async def test_the_function_is_security_definer_with_an_empty_search_path(world: World) -> None:
    row = await world.pg.fetchrow(
        "select prosecdef, proconfig from pg_proc where proname = 'claim_rate_limit_slot'"
    )
    assert row["prosecdef"] is True
    assert "search_path=" in " ".join(row["proconfig"])
    assert any(setting in ('search_path=""', "search_path=") for setting in row["proconfig"])


# -- the account: deleting it, counting it, merging it ------------------------------------


async def test_the_residue_check_counts_the_table_and_deleting_the_user_removes_the_rows(
    world: World,
) -> None:
    user = await world.web_user()
    await _claim(world.sb, user, _bucket(), window=60, max_requests=5)
    await _claim(world.sb, user, _bucket(), window=60, max_requests=5)

    counts = await world.counts(user)

    assert {name.removeprefix("public.") for name in counts} == {"api_rate_limits"}
    assert next(iter(counts.values())) == 2

    await world.sb.auth.admin.delete_user(user)

    assert (
        await world.pg.fetchval(
            "select count(*) from public.api_rate_limits where user_id = $1", uuid.UUID(user)
        )
        == 0
    )
    assert await world.counts(user) == {}


async def test_a_link_merge_drops_the_source_accounts_counters_and_still_completes(
    world: World,
) -> None:
    """Rate-limit windows are ephemeral: there is no 'keep both'. A source holding one would
    otherwise fail the merge's completeness check."""
    src = await world.telegram_user()
    target = await world.web_user()
    await _claim(world.sb, src.id, _bucket(), window=60, max_requests=5)
    await _claim(world.sb, target, _bucket(), window=60, max_requests=5)

    result = await world.link(src.subject, await world.mint(target), src.id)

    assert result["ok"] is True
    assert (
        await world.pg.fetchval(
            "select count(*) from public.api_rate_limits where user_id = $1", uuid.UUID(src.id)
        )
        == 0
    )
    assert (
        await world.pg.fetchval(
            "select count(*) from public.api_rate_limits where user_id = $1", uuid.UUID(target)
        )
        == 1
    )  # the target's own counter is untouched


# -- the migration's own grants, replayed against prod's default privileges -----------------

_MIGRATION = next(
    (Path(__file__).parent.parent.parent / "supabase" / "migrations").glob(
        "*_create_api_rate_limits.sql"
    )
)


def _grant_and_revoke_statements() -> list[str]:
    """The migration's own GRANT and REVOKE statements, in file order. The function body
    contains semicolons, but nothing inside it starts with either keyword."""
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
    """A fresh local stack may grant new objects to nobody, so the checks above would pass even
    if the migration revoked nothing. Prod's default privileges grant new tables and functions
    to anon, authenticated and service_role. So: put these two objects in prod's state, replay
    exactly the statements the migration contains, and check what is left.

    All of it inside one transaction that is always rolled back (GRANT and REVOKE are
    transactional), so neither a pass nor a failure can leave this stack's ACLs changed for the
    tests that follow."""
    pg = world.pg
    table = "public.api_rate_limits"
    function = "public.claim_rate_limit_slot(uuid, text, integer, integer)"

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
            assert (
                len(statements) == 4
            )  # revoke table, grant table, revoke function, grant function
            for statement in statements:
                await pg.execute(statement)

            for role in ("anon", "authenticated"):
                for privilege in ("select", "insert", "update", "delete", "truncate", "references"):
                    assert not await pg.fetchval(
                        "select has_table_privilege($1, $2, $3)", role, table, privilege
                    ), f"{role} can still {privilege} {table}"
                assert not await pg.fetchval(
                    "select has_function_privilege($1, $2, 'execute')", role, function
                ), f"{role} can still execute the function"
            # service_role keeps what the backend uses and nothing prod's defaults added
            for privilege in ("select", "insert", "update", "delete"):
                assert await pg.fetchval(
                    "select has_table_privilege('service_role', $1, $2)", table, privilege
                )
            for privilege in ("truncate", "references", "trigger"):
                assert not await pg.fetchval(
                    "select has_table_privilege('service_role', $1, $2)", table, privilege
                ), f"service_role kept {privilege} from prod's default grant"
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
