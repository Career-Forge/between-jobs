"""Row-level security, tested directly (launch plan P4.7, P§52).

The API reaches Postgres with the service role, which bypasses RLS, so isolation there rests on
every query filtering by `user_id` (the route suite covers that). RLS is the second wall, for
anything that reaches PostgREST with a user's own token -- and anyone who signs up has one,
together with the public anon key. This file checks the walls themselves:

* the catalog: RLS is on for every table, every policy on an owned table is bound to the caller,
  and no table without an owner column is readable with a user token;
* the behaviour: with B's real access token, straight to PostgREST, B cannot read, change,
  delete or insert rows that belong to A -- including a row of her own that points at A's
  application -- and A can still read her own. A user token writes nothing at all: every write
  goes through the API.

Run with `pytest -m local_supabase` after `supabase start` and `supabase db reset --local`."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from .conftest import World
from .cross_tenant.harness import SEEDERS, Ctx, Pair, load_cases

pytestmark = [pytest.mark.local_supabase, pytest.mark.asyncio(loop_scope="module")]

load_cases()  # importing the case modules registers every seeder

# Owned tables (a `user_id` column) that carry policies for signed-in users: each is tried as a
# stranger below. The catalog test fails when this list and the database disagree.
OWNED_WITH_POLICIES = [
    "application_events",
    "application_status_proposals",
    "applications",
    "approved_answers",
    "artifact_versions",
    "capability_preferences",
    "career_facts",
    "channel_identities",
    "company_intel_runs",
    "contact_research_runs",
    "hiring_signal_saves",
    "hiring_signal_searches",
    "interview_sessions",
    "link_codes",
    "outreach_drafts",
    "positioning_briefs",
    "profile_versions",
    "provider_credentials",
    "resume_documents",
    "saved_searches",
    "sessions",
    "tester_enrollments",
    "warm_path_runs",
    "working_sets",
]

# Owned tables with RLS on and no policy at all: a user token reads and writes nothing, only
# the service role does. The catalog test checks that stays true.
SERVICE_ROLE_ONLY = [
    "api_rate_limits",
    "event_outbox",
    "extension_draft_answer_rate_limits",
    "extension_sign_outs",
    "oauth_states",
    "product_events",
    "today_items",
]

# Tables with no owner column that a user token may read. Empty on purpose: the web client and
# the extension use Supabase for sign-in only and read every table through the API, so nothing
# needs these open, and a table readable by any sign-up is a table anyone can download. Add to
# this only with a reason a reviewer accepts.
SHARED_READABLE: dict[str, str] = {}


# -- the catalog -------------------------------------------------------------------------------


async def _owned_tables(world: World) -> set[str]:
    rows = await world.pg.fetch(
        """
        select c.table_name from information_schema.columns c
        join information_schema.tables t
          on t.table_schema = c.table_schema and t.table_name = c.table_name
        where c.table_schema = 'public' and c.column_name = 'user_id'
          and t.table_type = 'BASE TABLE'
        """
    )
    return {r["table_name"] for r in rows}


async def test_every_public_table_has_row_level_security(tenant_world: World) -> None:
    rows = await tenant_world.pg.fetch(
        """
        select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'public' and c.relkind in ('r', 'p') and not c.relrowsecurity
        order by 1
        """
    )
    assert [r["relname"] for r in rows] == []


async def test_the_lists_in_this_file_match_the_database(tenant_world: World) -> None:
    owned = await _owned_tables(tenant_world)
    policies = await tenant_world.pg.fetch(
        "select distinct tablename from pg_policies where schemaname = 'public'"
    )
    with_policies = {r["tablename"] for r in policies}

    assert owned & with_policies == set(OWNED_WITH_POLICIES)
    assert owned - with_policies == set(SERVICE_ROLE_ONLY)


async def test_policies_on_owned_tables_are_bound_to_the_caller(tenant_world: World) -> None:
    """Most policies say nothing about roles (so they apply to PUBLIC, anon included). That is
    safe only because every expression compares `user_id` with `auth.uid()`, which is NULL for
    an anonymous caller: so it is the expression that is checked here, for every policy."""
    rows = await tenant_world.pg.fetch(
        """
        select p.tablename, p.policyname, p.qual, p.with_check
        from pg_policies p
        where p.schemaname = 'public'
          and exists (select 1 from information_schema.columns c
                      where c.table_schema = 'public' and c.table_name = p.tablename
                        and c.column_name = 'user_id')
        order by 1, 2
        """
    )
    bad: list[str] = []
    for r in rows:
        expression = " ".join(filter(None, [r["qual"], r["with_check"]]))
        if "auth.uid()" not in expression or "user_id" not in expression:
            bad.append(f"{r['tablename']}.{r['policyname']}: {expression!r}")
    assert bad == []


async def test_no_policy_lets_a_user_token_write_anything(tenant_world: World) -> None:
    """The web app and the extension use Supabase for sign-in only: every write goes through
    the API with the service role. A write policy for a user role is therefore only a way to
    skip the API's validation, caps and rate limits, and (an INSERT policy that checks only
    `user_id`) a way to point a row of one's own at another user's application."""
    rows = await tenant_world.pg.fetch(
        """
        select schemaname || '.' || tablename || ':' || policyname || ' (' || cmd || ')' as policy
        from pg_policies
        where cmd <> 'SELECT'
          and (schemaname = 'public' or (schemaname = 'storage' and tablename = 'objects'))
        order by 1
        """
    )
    assert [r["policy"] for r in rows] == []


# Functions a user token may call over RPC. Only a trigger function, which cannot be called.
CALLABLE_BY_USERS = {"set_updated_at"}


async def test_no_function_is_callable_by_a_user_token(tenant_world: World) -> None:
    """The API trusts the `p_user_id` it passes to its SQL functions, so a function executable by
    `anon` or `authenticated` would let any signed-in user act as any other. Every migration
    revokes execute explicitly; this sweeps for the one that forgot."""
    rows = await tenant_world.pg.fetch(
        """
        select p.proname, r.rolname
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        cross join (select rolname from pg_roles where rolname in ('anon', 'authenticated')) r
        where n.nspname = 'public'
          and has_function_privilege(r.rolname, p.oid, 'EXECUTE')
          and not exists (select 1 from pg_depend d
                          where d.objid = p.oid and d.deptype = 'e')  -- extension-owned
        order by 1, 2
        """
    )
    exposed = {(r["proname"], r["rolname"]) for r in rows if r["proname"] not in CALLABLE_BY_USERS}
    assert exposed == set(), f"callable with a user token: {sorted(exposed)}"


async def test_no_table_without_an_owner_column_is_readable_with_a_user_token(
    tenant_world: World,
) -> None:
    owned = await _owned_tables(tenant_world)
    rows = await tenant_world.pg.fetch(
        "select distinct tablename from pg_policies where schemaname = 'public' order by 1"
    )
    unowned_with_policy = {r["tablename"] for r in rows} - owned

    assert unowned_with_policy == set(SHARED_READABLE), (
        "tables a signed-in user can read with no owner column: "
        f"{sorted(unowned_with_policy - set(SHARED_READABLE))}"
    )


# -- the behaviour -----------------------------------------------------------------------------


class _Rest:
    """PostgREST as a client with only the public anon key and a user's own token sees it."""

    def __init__(self, stack: dict[str, str]) -> None:
        self.base = f"{stack['API_URL']}/rest/v1"
        self.anon = stack["ANON_KEY"]

    def headers(self, token: str) -> dict[str, str]:
        return {
            "apikey": self.anon,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }


def _denied_or_empty(response: httpx.Response) -> bool:
    """RLS answers an unseen row with an empty result, a missing privilege with 401/403."""
    if response.status_code in {401, 403}:
        return True
    if response.status_code == 204:
        return True
    return response.status_code == 200 and response.json() == []


async def _generated_columns(world: World, table: str) -> set[str]:
    rows = await world.pg.fetch(
        """
        select column_name from information_schema.columns
        where table_schema = 'public' and table_name = $1
          and (is_generated = 'ALWAYS' or identity_generation = 'ALWAYS')
        """,
        table,
    )
    return {r["column_name"] for r in rows}


@pytest.mark.parametrize("table", OWNED_WITH_POLICIES)
async def test_a_stranger_with_a_user_token_cannot_read_change_or_steal_a_row(
    table: str, tenant_world: World, tenant_pair: Pair, local_stack: dict[str, str]
) -> None:
    world, a, b = tenant_world, tenant_pair.a, tenant_pair.b
    kinds = [kind for kind, s in SEEDERS.items() if table in s.tables]
    assert kinds, f"no seeder fills {table}: add one (seeds_rls.py) so this test has a row to guard"
    ctx = Ctx(world, tenant_pair)
    await ctx.need(kinds[0], a)
    await ctx.need(kinds[0], b)
    rest = _Rest(local_stack)
    url = f"{rest.base}/{table}"

    async def mine(user_id: str) -> list[dict[str, Any]]:
        result = await world.sb.table(table).select("*").eq("user_id", user_id).execute()
        return list(result.data)

    a_before = await mine(a.user_id)
    assert a_before, f"seeding {kinds[0]!r} for A put no row in {table}"

    async with httpx.AsyncClient(timeout=20) as http:
        # B reads: by A's id, and everything B can see
        r = await http.get(
            url, params={"select": "*", "user_id": f"eq.{a.user_id}"}, headers=rest.headers(b.token)
        )
        assert _denied_or_empty(r), f"{table}: B read A's rows: {r.status_code} {r.text[:200]}"
        r = await http.get(url, params={"select": "*"}, headers=rest.headers(b.token))
        assert r.status_code == 200, f"{table}: B's own read -> {r.status_code} {r.text[:200]}"
        seen = r.json()
        assert all(row.get("user_id") == b.user_id for row in seen), f"{table}: B saw foreign rows"
        assert seen, f"{table}: B sees none of her own rows, so 'none of A's' proves nothing"

        # A reads her own: the policy lets the owner in
        r = await http.get(url, params={"select": "*"}, headers=rest.headers(a.token))
        assert r.status_code == 200
        assert any(row.get("user_id") == a.user_id for row in r.json()), (
            f"{table}: A cannot read her own row"
        )

        # B tries to change and delete A's rows, then to hand them to herself
        r = await http.patch(
            url,
            params={"user_id": f"eq.{a.user_id}"},
            json={"user_id": b.user_id},
            headers=rest.headers(b.token),
        )
        assert _denied_or_empty(r), (
            f"{table}: B's update touched A's rows: {r.status_code} {r.text[:200]}"
        )
        r = await http.delete(
            url, params={"user_id": f"eq.{a.user_id}"}, headers=rest.headers(b.token)
        )
        assert _denied_or_empty(r), (
            f"{table}: B's delete touched A's rows: {r.status_code} {r.text[:200]}"
        )
        assert await mine(a.user_id) == a_before, f"{table}: A's rows changed after B's attempts"

        # Nobody writes through PostgREST, so every insert is refused by row-level security: B
        # planting a row as A, B planting a row of her OWN that points at A's application or
        # profile version (the parent ids are copied from A's row), and A writing her own
        generated = await _generated_columns(world, table)
        base = {k: v for k, v in a_before[0].items() if k not in generated}
        for who, token, owner in (
            ("B as A", b.token, a.user_id),
            ("B as herself, pointing at A's parent", b.token, b.user_id),
            ("A as herself", a.token, a.user_id),
        ):
            r = await http.post(url, json={**base, "user_id": owner}, headers=rest.headers(token))
            assert r.status_code in {401, 403}, (
                f"{table}: an insert by {who} was not refused: {r.status_code} {r.text[:200]}"
            )
            if r.status_code == 403:
                assert "42501" in r.text, (
                    f"{table}: refused, but not by row-level security: {r.text[:200]}"
                )
        # and A cannot change or delete her own rows either
        r = await http.patch(
            url,
            params={"user_id": f"eq.{a.user_id}"},
            json={"user_id": a.user_id},
            headers=rest.headers(a.token),
        )
        assert _denied_or_empty(r), f"{table}: A's own update went through: {r.status_code}"
        r = await http.delete(
            url, params={"user_id": f"eq.{a.user_id}"}, headers=rest.headers(a.token)
        )
        assert _denied_or_empty(r), f"{table}: A's own delete went through: {r.status_code}"
        assert await mine(a.user_id) == a_before, f"{table}: A's rows changed after all attempts"
        assert len(await mine(b.user_id)) == len(seen), f"{table}: a row appeared under B"


# -- Storage -----------------------------------------------------------------------------------
#
# Generated resumes and cover letters live in a private bucket, one folder per user. Objects do
# not cascade with a user and the API serves them with the service role, so the bucket's own
# policies are the only thing between a signed-in stranger and another user's PDF.


async def test_a_stranger_cannot_read_replace_plant_or_delete_another_users_files(
    tenant_world: World, tenant_pair: Pair, local_stack: dict[str, str]
) -> None:
    world, a, b = tenant_world, tenant_pair.a, tenant_pair.b
    ctx = Ctx(world, tenant_pair)
    application = await ctx.need("application", a)
    version = await ctx.need("profile_version", a)
    original = b"%PDF-1.4 A's resume " + ctx.tag("a-pdf").encode()
    row = await world.artifact_version(
        a.user_id, application["id"], version["id"], kind="resume", version=7, body=original
    )
    key = row["storage_key"]
    base = f"{local_stack['API_URL']}/storage/v1"

    def headers(token: str, **extra: str) -> dict[str, str]:
        return {"apikey": local_stack["ANON_KEY"], "Authorization": f"Bearer {token}", **extra}

    async def service_copy() -> bytes | None:
        try:
            return bytes(await world.sb.storage.from_("artifacts").download(key))
        except Exception:
            return None

    async with httpx.AsyncClient(timeout=20) as http:
        # the owner reads her own file: the channel works, so a stranger's refusal means something
        r = await http.get(f"{base}/object/authenticated/artifacts/{key}", headers=headers(a.token))
        assert r.status_code == 200 and r.content == original, "A cannot read her own file"

        # a stranger cannot read it, nor see it in a listing
        r = await http.get(f"{base}/object/authenticated/artifacts/{key}", headers=headers(b.token))
        assert r.status_code != 200 and original not in r.content, "B downloaded A's file"
        r = await http.post(
            f"{base}/object/list/artifacts",
            json={"prefix": f"{a.user_id}/", "limit": 100},
            headers=headers(b.token),
        )
        assert r.status_code in {200, 400, 403, 404}
        assert r.status_code != 200 or r.json() == [], "B listed A's folder"
        r = await http.post(
            f"{base}/object/list/artifacts",
            json={"prefix": f"{a.user_id}/", "limit": 100},
            headers=headers(a.token),
        )
        assert r.status_code == 200 and r.json(), "A cannot list her own folder"

        # a stranger cannot plant a file under A's folder, replace A's file or delete it
        r = await http.post(
            f"{base}/object/artifacts/{a.user_id}/planted-by-b.pdf",
            content=b"%PDF planted",
            headers=headers(b.token, **{"Content-Type": "application/pdf"}),
        )
        assert r.status_code in {400, 401, 403}, f"B planted a file in A's folder: {r.status_code}"
        r = await http.put(
            f"{base}/object/artifacts/{key}",
            content=b"%PDF replaced by B",
            headers=headers(b.token, **{"Content-Type": "application/pdf"}),
        )
        assert r.status_code in {400, 401, 403, 404}, f"B replaced A's file: {r.status_code}"
        r = await http.delete(f"{base}/object/artifacts/{key}", headers=headers(b.token))
        assert r.status_code in {200, 400, 401, 403, 404}
        assert await service_copy() == original, "A's file changed or vanished after B's attempts"

        planted = await world.pg.fetch(
            "select name from storage.objects where bucket_id = 'artifacts' and name = $1",
            f"{a.user_id}/planted-by-b.pdf",
        )
        assert planted == [], "B's file exists under A's folder"

        # nobody uploads through Storage: the API writes the files, B into her own folder included
        for who, token, owner in (("B", b.token, b.user_id), ("A", a.token, a.user_id)):
            r = await http.post(
                f"{base}/object/artifacts/{owner}/own-{ctx.tag('f')}.pdf",
                content=b"%PDF own",
                headers=headers(token, **{"Content-Type": "application/pdf"}),
            )
            assert r.status_code in {400, 401, 403}, (
                f"{who} uploaded to her own folder: {r.status_code}"
            )
