"""The account-deletion drill (launch plan P4.5), against a real local stack.

A throwaway user gets rows in every table the cross-tenant seeders can fill, files in the private
bucket (one of them in a nested folder), a connected Gmail account and a linked Telegram chat.
They delete their account through the real route with their real token. Then: the steps ran in the
documented order, nothing is left anywhere, a bystander's data is untouched, and a failed file
removal stops the whole thing with the account intact.

Google is a recording stand-in (nothing leaves the machine; the harness refuses every socket but
the local stack's).

Run with `pytest -m local_supabase` after `supabase start` and `supabase db reset --local`."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from supabase_auth.errors import AuthApiError

from between_jobs.api.account_deletion import GOOGLE_REVOKE_URL
from between_jobs.api.artifact_versions_store import create_version
from between_jobs.api.provider_credentials_store import save_credential
from supabase import acreate_client

from .conftest import World
from .cross_tenant.harness import SEEDERS, Ctx, Pair, Tenant, load_cases, make_tenant

pytestmark = [pytest.mark.local_supabase, pytest.mark.asyncio(loop_scope="module")]

load_cases()  # importing the case modules registers every seeder the drill fills

_REFRESH_TOKEN = "throwaway-refresh-token-for-the-drill"
_PHRASE = {"confirm": "delete my account"}


class _Recorder:
    """Puts what the route does to the real client on one timeline."""

    def __init__(self) -> None:
        self.events: list[tuple[str, ...]] = []
        self.fail_remove = False
        self.after_delete: Any = None  # awaited right after the real delete_user: a generation
        # that was already running when the deletion began

    def kinds(self) -> list[str]:
        order: list[str] = []
        for event in self.events:
            if event[0] not in order:
                order.append(event[0])
        return order


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch, tenant_client: httpx.AsyncClient) -> _Recorder:
    from between_jobs.api.app import app

    rec = _Recorder()

    def google(request: httpx.Request) -> httpx.Response:
        rec.events.append(("revoke", str(request.url), request.content.decode()))
        return httpx.Response(200)

    monkeypatch.setattr(app.state, "http", httpx.AsyncClient(transport=httpx.MockTransport(google)))

    supabase = app.state.supabase
    real_delete_user = supabase.auth.admin.delete_user
    real_from = supabase.storage.from_

    async def delete_user(user_id: str, *args: Any, **kwargs: Any) -> Any:
        rec.events.append(("delete_user", user_id))
        result = await real_delete_user(user_id, *args, **kwargs)
        if rec.after_delete:
            await rec.after_delete()
        return result

    class _Bucket:
        def __init__(self, bucket: Any) -> None:
            self._bucket = bucket

        def __getattr__(self, name: str) -> Any:
            return getattr(self._bucket, name)

        async def remove(self, paths: list[str]) -> Any:
            if rec.fail_remove:
                raise RuntimeError("storage is down")
            rec.events.append(("remove", str(len(paths))))
            return await self._bucket.remove(paths)

    monkeypatch.setattr(supabase.auth.admin, "delete_user", delete_user)
    monkeypatch.setattr(supabase.storage, "from_", lambda name: _Bucket(real_from(name)))
    return rec


async def _fill(ctx: Ctx, tenant: Tenant) -> list[str]:
    """A row in every table a seeder can fill, a nested file, Gmail and a Telegram chat."""
    for kind in SEEDERS:
        await ctx.need(kind, tenant)
    application = await ctx.need("application", tenant)
    version = await ctx.need("profile_version", tenant)
    world = ctx.world
    for number in (50, 51):  # the seeders already uploaded the low versions
        await world.artifact_version(
            tenant.user_id,
            application["id"],
            version["id"],
            kind="resume",
            version=number,
            body=b"%PDF-1.4 " + ctx.tag("pdf").encode(),
        )
    await world.sb.storage.from_("artifacts").upload(
        f"{tenant.user_id}/notes/deeper/extra.txt", b"nested", {"content-type": "text/plain"}
    )
    await save_credential(
        world.sb,
        tenant.user_id,
        service="oauth",
        provider="gmail",
        secret=_REFRESH_TOKEN,
        scope="https://www.googleapis.com/auth/gmail.readonly",
        is_validated=True,
    )
    channel = (
        await world.sb.table("channel_identities")
        .select("external_subject")
        .eq("user_id", tenant.user_id)
        .execute()
    )
    subjects = [row["external_subject"] for row in channel.data]
    for subject in subjects:
        await (
            world.sb.table("link_code_attempts")
            .upsert(
                {"channel": "telegram", "external_subject": subject, "failed_count": 2},
                on_conflict="channel,external_subject",
            )
            .execute()
        )
    return subjects


async def _stored(world: World, user_id: str) -> list[str]:
    return await world.storage_keys(user_id)


async def _exists(world: World, user_id: str) -> bool:
    return await world.user_exists(user_id)


class _Drill:
    def __init__(
        self,
        victim: Tenant,
        bystander: Tenant,
        victim_subjects: list[str],
        bystander_subjects: list[str],
    ) -> None:
        self.victim = victim
        self.bystander = bystander
        self.victim_subjects = victim_subjects
        self.bystander_subjects = bystander_subjects


async def _drill(world: World) -> _Drill:
    """Two fresh users, each filled with data; the first is the one who will leave."""
    victim = await make_tenant(world, "A")
    bystander = await make_tenant(world, "B")
    ctx = Ctx(world, Pair(victim, bystander))
    return _Drill(victim, bystander, await _fill(ctx, victim), await _fill(ctx, bystander))


async def _attempts(world: World, subjects: list[str]) -> int:
    return int(
        await world.pg.fetchval(
            "select count(*) from public.link_code_attempts "
            "where external_subject = any($1::text[])",
            subjects,
        )
    )


async def test_a_deleted_account_leaves_nothing_behind_and_touches_nobody_else(
    tenant_world: World,
    tenant_client: httpx.AsyncClient,
    recorder: _Recorder,
    local_stack: dict[str, str],
) -> None:
    world = tenant_world
    drill = await _drill(world)
    victim, bystander = drill.victim, drill.bystander
    assert await _attempts(world, drill.victim_subjects) >= 1
    before = await world.counts(victim.user_id)
    bystander_before = await world.counts(bystander.user_id)
    assert len(before) >= 18, f"the drill only filled {len(before)} tables: {sorted(before)}"
    # the product-event log and the tester enrollment (with its sponsorship answer) are in it
    assert {"public.product_events", "public.tester_enrollments"} <= set(before)
    assert await _stored(world, victim.user_id), "the drill put no files in the bucket"

    response = await tenant_client.post("/account/delete", json=_PHRASE, headers=victim.headers)

    assert response.status_code == 204, response.text
    # the order: the files first (the step that must not fail), Google, the user last
    kinds = recorder.kinds()
    assert kinds.index("remove") < kinds.index("revoke") < kinds.index("delete_user"), kinds
    revoke = next(e for e in recorder.events if e[0] == "revoke")
    assert revoke[1] == GOOGLE_REVOKE_URL
    assert _REFRESH_TOKEN in revoke[2]
    # nothing of the user's is left
    assert await world.counts(victim.user_id) == {}
    assert await _stored(world, victim.user_id) == []
    assert not await _exists(world, victim.user_id)
    assert await _attempts(world, drill.victim_subjects) == 0
    # ... and nothing of anyone else's was touched
    assert await world.counts(bystander.user_id) == bystander_before
    assert await _attempts(world, drill.bystander_subjects) >= 1
    assert await _stored(world, bystander.user_id)
    assert await _exists(world, bystander.user_id)
    # the old session cannot be refreshed or signed in again
    anon = await acreate_client(local_stack["API_URL"], local_stack["ANON_KEY"])
    with pytest.raises(AuthApiError):
        await anon.auth.sign_in_with_password({"email": victim.email, "password": "any"})


async def test_a_file_that_cannot_be_removed_leaves_the_account_whole_and_a_retry_finishes_it(
    tenant_world: World,
    tenant_client: httpx.AsyncClient,
    recorder: _Recorder,
) -> None:
    world = tenant_world
    victim = (await _drill(world)).victim
    before = await world.counts(victim.user_id)
    files = await _stored(world, victim.user_id)
    recorder.fail_remove = True

    blocked = await tenant_client.post("/account/delete", json=_PHRASE, headers=victim.headers)

    assert blocked.status_code == 503
    assert blocked.json()["error"]["retryable"] is True
    assert recorder.events == []  # no Google call, no sign-out, no deletion: nothing changed
    assert await _exists(world, victim.user_id)
    assert await world.counts(victim.user_id) == before
    assert await _stored(world, victim.user_id) == files

    recorder.fail_remove = False
    done = await tenant_client.post("/account/delete", json=_PHRASE, headers=victim.headers)

    assert done.status_code == 204
    assert not await _exists(world, victim.user_id)
    assert await _stored(world, victim.user_id) == []


async def test_a_wrong_phrase_deletes_nothing(
    tenant_world: World,
    tenant_client: httpx.AsyncClient,
    recorder: _Recorder,
) -> None:
    world = tenant_world
    victim = (await _drill(world)).victim
    before = await world.counts(victim.user_id)

    refused = await tenant_client.post(
        "/account/delete", json={"confirm": "please"}, headers=victim.headers
    )

    assert refused.status_code == 422
    assert recorder.events == []
    assert await _exists(world, victim.user_id)
    assert await world.counts(victim.user_id) == before


async def test_sending_it_twice_is_not_an_error_the_second_time(
    tenant_world: World,
    tenant_client: httpx.AsyncClient,
    recorder: _Recorder,
) -> None:
    world = tenant_world
    victim = (await _drill(world)).victim

    first = await tenant_client.post("/account/delete", json=_PHRASE, headers=victim.headers)
    second = await tenant_client.post("/account/delete", json=_PHRASE, headers=victim.headers)

    assert (first.status_code, second.status_code) == (204, 204)
    assert not await _exists(world, victim.user_id)


async def _traces(world: World, *needles: str) -> dict[str, int]:
    """Every column, in every table of the schemas that can hold a user's data, whose text holds
    one of `needles` (the user's id or email). Searches by text, not by foreign key, so a copy of
    the id in a JSON blob, a log or an audit table is found too."""
    columns = await world.pg.fetch(
        """
        select c.table_schema, c.table_name, c.column_name
        from information_schema.columns c
        join information_schema.tables t
          on t.table_schema = c.table_schema and t.table_name = c.table_name
        where t.table_type = 'BASE TABLE'
          and c.table_schema in ('public', 'auth', 'storage')
          and c.data_type in ('uuid', 'text', 'character varying', 'json', 'jsonb', 'ARRAY')
        """
    )
    found: dict[str, int] = {}
    for column in columns:
        table = f'"{column["table_schema"]}"."{column["table_name"]}"'
        name = f'"{column["column_name"]}"'
        hits = await world.pg.fetchval(
            f"select count(*) from {table} where {name}::text ilike any($1::text[])",
            [f"%{needle}%" for needle in needles],
        )
        if hits:
            found[f"{column['table_schema']}.{column['table_name']}.{column['column_name']}"] = hits
    return found


async def test_no_table_anywhere_still_mentions_a_deleted_user(
    tenant_world: World,
    tenant_client: httpx.AsyncClient,
    recorder: _Recorder,
) -> None:
    world = tenant_world
    drill = await _drill(world)
    victim = drill.victim
    needles = (victim.user_id, victim.email, *drill.victim_subjects)
    assert await _traces(world, *needles), "the scan finds nothing even before the deletion"

    response = await tenant_client.post("/account/delete", json=_PHRASE, headers=victim.headers)

    assert response.status_code == 204
    assert await _traces(world, *needles) == {}


async def test_a_generation_that_uploads_after_the_deletion_began_leaves_no_file(
    tenant_world: World,
    tenant_client: httpx.AsyncClient,
    recorder: _Recorder,
) -> None:
    """A /prepare still running when the person deletes their account uploads a PDF after the
    first listing. Its row is gone with the user, so the file is all that is left: the sweep after
    the deletion must find it."""
    world = tenant_world
    victim = (await _drill(world)).victim

    async def in_flight_generation() -> None:
        await world.sb.storage.from_("artifacts").upload(
            f"{victim.user_id}/late/in-flight.pdf",
            b"%PDF-1.4 late",
            {"content-type": "application/pdf"},
        )

    recorder.after_delete = in_flight_generation

    response = await tenant_client.post("/account/delete", json=_PHRASE, headers=victim.headers)

    assert response.status_code == 204
    assert await _stored(world, victim.user_id) == []


async def test_a_file_whose_row_cannot_be_written_for_a_deleted_user_is_taken_back_out(
    tenant_world: World,
) -> None:
    """The other end of the same race: a generation that finishes after the sweep. The row's
    foreign key to the (gone) user fails, and `create_version` removes the file it just uploaded."""
    world = tenant_world
    victim = await make_tenant(world, "A")
    await world.sb.auth.admin.delete_user(victim.user_id)
    other_uuid = "00000000-0000-4000-8000-000000000042"

    with pytest.raises(Exception, match=r"violates foreign key|23503"):
        await create_version(
            world.sb,
            victim.user_id,
            application_id=other_uuid,
            document_kind="resume",
            content=b"%PDF-1.4 orphan",
            media_type="application/pdf",
            generator="drill",
            generator_version="1",
            profile_version_id=other_uuid,
            job_snapshot_id=None,
            evidence_fact_ids=[],
            warnings=[],
        )

    assert await _stored(world, victim.user_id) == []
