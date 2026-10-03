"""`create_company_intel_run` against a local Supabase stack (launch plan P0.10): a run and its
claims are written together or not at all. The unit tests fake the database, so only this can
show that a failing claims insert really leaves no run behind.

Run with `pytest -m local_supabase` after `supabase start` and
`supabase db reset --local`."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from postgrest.exceptions import APIError

from supabase import acreate_client

from .conftest import World

pytestmark = pytest.mark.local_supabase

_MIGRATION = next(
    (Path(__file__).parents[2] / "supabase" / "migrations").glob("*_company_intel_atomic_write.sql")
)
_FUNCTION = "public.create_company_intel_run(uuid, uuid, text, text[], jsonb, jsonb)"


def _claim(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "category": "product_and_mission",
        "claim_text": "Acme builds banking software.",
        "source_url": "https://acme.example/about",
        "source_title": "About Acme",
        "confidence": "high",
    }
    base.update(overrides)
    return base


async def _call(world: World, user: str, application: str, claims: Any, **overrides: Any) -> Any:
    params: dict[str, Any] = {
        "p_user_id": user,
        "p_application_id": application,
        "p_company_name": "Acme",
        "p_providers_used": ["you_com"],
        "p_warnings": [],
        "p_claims": claims,
    }
    params.update(overrides)
    result = await world.sb.rpc("create_company_intel_run", params).execute()
    return result.data


async def _counts(world: World, application: str) -> tuple[int, int]:
    runs = await world.pg.fetchval(
        "select count(*) from public.company_intel_runs where application_id = $1::uuid",
        application,
    )
    claims = await world.pg.fetchval(
        "select count(*) from public.company_intel_claims c "
        "join public.company_intel_runs r on r.id = c.run_id where r.application_id = $1::uuid",
        application,
    )
    return runs, claims


async def _application(world: World) -> tuple[str, str]:
    user = await world.web_user()
    application = await world.application(user, await world.job())
    return user, str(application["id"])


async def test_a_run_and_all_its_claims_are_written_and_the_run_row_is_returned(
    world: World,
) -> None:
    user, application = await _application(world)

    run = await _call(
        world,
        user,
        application,
        [_claim(), _claim(category="hiring_activity", source_title=None)],
        p_providers_used=["you_com", "firecrawl"],
        p_warnings=["note one"],
    )

    assert run["user_id"] == user
    assert run["application_id"] == application
    assert run["company_name"] == "Acme"
    assert run["providers_used"] == ["you_com", "firecrawl"]
    assert run["warnings"] == ["note one"]
    assert run["id"] and run["created_at"]
    assert await _counts(world, application) == (1, 2)
    rows = await world.pg.fetch(
        "select category, claim_text, source_url, source_title, confidence "
        "from public.company_intel_claims where run_id = $1::uuid order by category",
        run["id"],
    )
    assert [r["category"] for r in rows] == ["hiring_activity", "product_and_mission"]
    assert rows[0]["source_title"] is None
    assert rows[1]["source_url"] == "https://acme.example/about"


async def test_a_claims_insert_that_fails_leaves_no_run_behind(world: World) -> None:
    """The point of P0.10. A claim with no source_url violates the table's NOT NULL; before,
    the run row had already been inserted by then and stayed."""
    user, application = await _application(world)

    with pytest.raises(APIError) as failed:
        await _call(world, user, application, [_claim(), _claim(source_url=None)])

    assert failed.value.code == "23502"  # not_null_violation, from the claims insert
    assert await _counts(world, application) == (0, 0)


async def test_a_failed_refresh_leaves_the_previous_good_dossier_as_the_latest(
    world: World,
) -> None:
    user, application = await _application(world)
    good = await _call(world, user, application, [_claim()])

    with pytest.raises(APIError):
        await _call(world, user, application, [_claim(source_url=None)])

    latest = await world.pg.fetchval(
        "select id from public.company_intel_runs where application_id = $1::uuid "
        "order by created_at desc limit 1",
        application,
    )
    assert str(latest) == good["id"]
    assert await _counts(world, application) == (1, 1)


async def test_a_run_with_no_claims_is_still_a_run(world: World) -> None:
    user, application = await _application(world)

    run = await _call(world, user, application, [], p_warnings=["You.com key missing"])

    assert await _counts(world, application) == (1, 0)
    assert run["warnings"] == ["You.com key missing"]


async def test_missing_optional_arguments_take_the_tables_defaults(world: World) -> None:
    user, application = await _application(world)

    run = await _call(world, user, application, None, p_providers_used=None, p_warnings=None)

    assert run["providers_used"] == []
    assert run["warnings"] == []
    assert await _counts(world, application) == (1, 0)


async def test_an_application_that_is_not_the_users_gets_nothing(world: World) -> None:
    _owner, application = await _application(world)
    stranger = await world.web_user()

    with pytest.raises(APIError) as refused:
        await _call(world, stranger, application, [_claim()])

    assert refused.value.code == "P0002"
    assert await _counts(world, application) == (0, 0)


async def test_an_application_that_does_not_exist_gets_nothing(world: World) -> None:
    user = await world.web_user()

    with pytest.raises(APIError) as refused:
        await _call(world, user, str(uuid.uuid4()), [_claim()])

    assert refused.value.code == "P0002"


async def test_claims_that_are_not_a_json_array_are_refused_before_anything_is_written(
    world: World,
) -> None:
    user, application = await _application(world)

    with pytest.raises(APIError) as refused:
        await _call(world, user, application, {"category": "x"})

    assert refused.value.code == "22023"
    assert await _counts(world, application) == (0, 0)


async def test_only_the_backend_can_call_it(world: World) -> None:
    stack = world.stack
    user, application = await _application(world)
    anon = await acreate_client(stack["API_URL"], stack["ANON_KEY"])
    password = uuid.uuid4().hex
    email = f"intel-{uuid.uuid4().hex}@example.com"
    await world.web_user(password=password, email=email)
    signed_in = await acreate_client(stack["API_URL"], stack["ANON_KEY"])
    await signed_in.auth.sign_in_with_password({"email": email, "password": password})
    args = {
        "p_user_id": user,
        "p_application_id": application,
        "p_company_name": "Acme",
        "p_providers_used": [],
        "p_warnings": [],
        "p_claims": [],
    }

    for client in (anon, signed_in):
        with pytest.raises(APIError):
            await client.rpc("create_company_intel_run", args).execute()

    assert await _counts(world, application) == (0, 0)


class _Rollback(Exception):
    """Raised to end the test's transaction so it is always rolled back."""


async def test_the_migrations_own_revokes_close_it_even_when_the_project_grants_by_default(
    world: World,
) -> None:
    """Prod grants new functions to anon, authenticated and service_role by default. Put the
    function in that state, replay exactly the GRANT and REVOKE statements this migration
    contains, and check what is left -- inside a transaction that is always rolled back."""
    sql = re.sub(r"--[^\n]*", "", _MIGRATION.read_text())
    statements = [
        statement.strip()
        for statement in sql.split(";")
        if statement.strip().lower().startswith(("revoke", "grant"))
    ]
    assert len(statements) == 2
    pg: Any = world.pg
    try:
        async with pg.transaction():
            await pg.execute(
                f"grant execute on function {_FUNCTION} "
                "to public, anon, authenticated, service_role"
            )
            for statement in statements:
                await pg.execute(statement)
            for role in ("anon", "authenticated"):
                assert not await pg.fetchval(
                    "select has_function_privilege($1, $2, 'execute')", role, _FUNCTION
                ), f"{role} can still execute it"
            assert await pg.fetchval(
                "select has_function_privilege('service_role', $1, 'execute')", _FUNCTION
            )
            public_can_execute = await pg.fetchval(
                """
                select exists (
                  select 1 from pg_proc p, aclexplode(p.proacl) a
                  where p.oid = $1::regprocedure and a.grantee = 0
                    and a.privilege_type = 'EXECUTE'
                )
                """,
                _FUNCTION,
            )
            assert not public_can_execute
            raise _Rollback
    except _Rollback:
        pass
