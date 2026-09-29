"""The account merge behind /link, exercised against the real SQL on a local
Supabase stack: consume_link_code, merge_user_data and their helpers, called
the way the backend calls them (the service_role RPC path).

Run with `pytest -m local_supabase` after `supabase start` and
`supabase db reset --local`."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from postgrest.exceptions import APIError

from between_jobs.api.artifact_versions_store import artifact_id_for
from supabase import acreate_client

from .conftest import World

pytestmark = pytest.mark.local_supabase

# Every table with a foreign key to auth.users. Adding one means deciding how a
# merge treats it -- see the fast static guard in tests/test_link_migration_static.py
# and merge_user_data itself -- and this list is where that decision is recorded.
_USER_OWNED_TABLES = frozenset(
    {
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
        "event_outbox",
        "extension_draft_answer_rate_limits",
        "extension_sign_outs",
        "hiring_signal_saves",
        "hiring_signal_searches",
        "interview_sessions",
        "link_codes",
        "oauth_states",
        "outreach_drafts",
        "positioning_briefs",
        "profile_versions",
        "provider_credentials",
        "resume_documents",
        "saved_searches",
        "sessions",
        "today_items",
        "warm_path_runs",
        "working_sets",
    }
)


def _linkedin_save(
    user_id: str, activity_id: str, application_id: str | None = None
) -> dict[str, Any]:
    """hiring_signal_saves checks that the url is the canonical LinkedIn one."""
    return {
        "user_id": user_id,
        "application_id": application_id,
        "activity_id": activity_id,
        "url": f"https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}",
        "source": "test",
    }


async def _drained(world: World, user_id: str) -> dict[str, int]:
    """What the user still owns, minus the storage references that the
    Python-side move (link_completion) fixes after the merge."""
    counts = await world.counts(user_id)
    counts.pop("artifact_versions.storage_key", None)
    counts.pop("storage.objects", None)
    return counts


# --- what moves ------------------------------------------------------------


async def test_a_plain_link_moves_what_the_source_owned_and_drains_it(world: World) -> None:
    src = await world.telegram_user()
    target = await world.web_user()
    pv = await world.profile_version(src.id)
    await world.fact(src.id, pv["id"], pointer="skills.0")
    await (
        world.sb.table("provider_credentials")
        .insert(
            {"user_id": src.id, "service": "llm", "provider": "openrouter", "secret_encrypted": "e"}
        )
        .execute()
    )
    await world.sb.table("saved_searches").insert({"user_id": src.id, "query": "ml"}).execute()

    result = await world.link(src.subject, await world.mint(target), src.id)

    assert result["ok"] is True
    assert result["target_user_id"] == target
    assert result["source_user_id"] == src.id
    summary = result["summary"]
    assert (summary["profile_versions"], summary["career_facts"]) == (1, 1)
    assert (summary["provider_credentials"], summary["saved_searches"]) == (1, 1)
    assert await _drained(world, src.id) == {}
    identity = (
        await world.sb.table("channel_identities")
        .select("user_id")
        .eq("external_subject", src.subject)
        .execute()
    ).data
    assert identity == [{"user_id": target}]


async def test_a_moved_credential_still_decrypts(world: World) -> None:
    """Encryption uses one vault key, not anything derived from the user id."""
    src = await world.telegram_user()
    target = await world.web_user()
    encrypted = (
        await world.sb.rpc("encrypt_secret", {"p_plaintext": "sk-test-not-real"}).execute()
    ).data
    await (
        world.sb.table("provider_credentials")
        .insert(
            {
                "user_id": src.id,
                "service": "llm",
                "provider": "openrouter",
                "secret_encrypted": encrypted,
            }
        )
        .execute()
    )

    await world.link(src.subject, await world.mint(target), src.id)

    moved = (
        await world.sb.table("provider_credentials")
        .select("secret_encrypted")
        .eq("user_id", target)
        .execute()
    ).data[0]["secret_encrypted"]
    decrypted = (await world.sb.rpc("decrypt_secret", {"p_ciphertext": moved}).execute()).data
    assert decrypted == "sk-test-not-real"


async def test_a_shared_job_collapses_into_one_application(world: World) -> None:
    src = await world.telegram_user()
    target = await world.web_user()
    job = await world.job()
    src_app = await world.application(
        src.id, job, status="applied", date_applied="2026-09-01T00:00:00Z"
    )
    tgt_app = await world.application(target, job, status="interviewing")
    src_pv = await world.profile_version(src.id)
    tgt_pv = await world.profile_version(target)
    for version in (1, 2, 3):
        await world.artifact_version(src.id, src_app["id"], src_pv["id"], version=version)
    await world.artifact_version(target, tgt_app["id"], tgt_pv["id"], version=1)
    await world.artifact_version(src.id, src_app["id"], src_pv["id"], kind="cover_letter")

    result = await world.link(src.subject, await world.mint(target), src.id)

    assert result["ok"] is True
    apps = (
        await world.sb.table("applications")
        .select("id,status,date_applied")
        .eq("job_id", job[0])
        .execute()
    ).data
    assert [a["id"] for a in apps] == [tgt_app["id"]]
    assert apps[0]["status"] == "interviewing"  # the target's own status wins
    assert apps[0]["date_applied"].startswith("2026-09-01")  # earliest non-null
    resumes = (
        await world.sb.table("artifact_versions")
        .select("version")
        .eq("artifact_id", artifact_id_for(tgt_app["id"], "resume"))
        .order("version")
        .execute()
    ).data
    assert [r["version"] for r in resumes] == [1, 2, 3, 4]
    covers = (
        await world.sb.table("artifact_versions")
        .select("id")
        .eq("artifact_id", artifact_id_for(tgt_app["id"], "cover_letter"))
        .execute()
    ).data
    assert len(covers) == 1  # a kind only the source had moves across intact
    merged = (
        await world.sb.table("application_events")
        .select("payload")
        .eq("application_id", tgt_app["id"])
        .eq("event_type", "application.merged")
        .execute()
    ).data
    assert [m["payload"]["merged_from_application_id"] for m in merged] == [src_app["id"]]
    assert await _drained(world, src.id) == {}


async def test_keys_scoped_to_an_application_follow_the_collapse(world: World) -> None:
    """After the shared job is folded into the target's application, a save or
    a resume the source had for it collides with the target's own -- and loses."""
    src = await world.telegram_user()
    target = await world.web_user()
    job = await world.job()
    src_app = await world.application(src.id, job)
    tgt_app = await world.application(target, job)
    src_pv = await world.profile_version(src.id)
    tgt_pv = await world.profile_version(target)
    await (
        world.sb.table("hiring_signal_saves")
        .insert(_linkedin_save(src.id, "777002", src_app["id"]))
        .execute()
    )
    await (
        world.sb.table("hiring_signal_saves")
        .insert(_linkedin_save(target, "777002", tgt_app["id"]))
        .execute()
    )
    await world.resume_document(src.id, src_pv["id"], application_id=src_app["id"])
    await world.resume_document(target, tgt_pv["id"], application_id=tgt_app["id"])

    result = await world.link(src.subject, await world.mint(target), src.id)

    assert result["ok"] is True
    saves = (
        await world.sb.table("hiring_signal_saves")
        .select("user_id")
        .eq("application_id", tgt_app["id"])
        .execute()
    ).data
    assert saves == [{"user_id": target}]
    docs = (
        await world.sb.table("resume_documents")
        .select("user_id")
        .eq("application_id", tgt_app["id"])
        .execute()
    ).data
    assert docs == [{"user_id": target}]
    assert await _drained(world, src.id) == {}


async def test_target_wins_on_the_unique_keys_the_two_accounts_share(world: World) -> None:
    src = await world.telegram_user()
    target = await world.web_user()
    sb = world.sb
    for user, secret in ((target, "target-secret"), (src.id, "source-secret")):
        await (
            sb.table("provider_credentials")
            .insert(
                {
                    "user_id": user,
                    "service": "llm",
                    "provider": "openrouter",
                    "secret_encrypted": secret,
                }
            )
            .execute()
        )
    for user, mode in ((target, "byok"), (src.id, "hosted")):
        await (
            sb.table("capability_preferences")
            .insert({"user_id": user, "capability": "default", "execution_mode": mode})
            .execute()
        )
    for user, text in ((target, "target answer"), (src.id, "source answer")):
        await (
            sb.table("approved_answers")
            .insert(
                {
                    "user_id": user,
                    "normalized_question": "why us",
                    "answer_text": text,
                    "evidence_fact_ids": [],
                }
            )
            .execute()
        )
    # matched on the LOWERED query and location, not the raw columns
    await (
        sb.table("hiring_signal_searches")
        .insert({"user_id": target, "query": "ML Engineer", "location": "NYC"})
        .execute()
    )
    await (
        sb.table("hiring_signal_searches")
        .insert({"user_id": src.id, "query": "ml engineer", "location": "nyc"})
        .execute()
    )
    await sb.table("hiring_signal_saves").insert(_linkedin_save(target, "777001")).execute()
    await sb.table("hiring_signal_saves").insert(_linkedin_save(src.id, "777001")).execute()
    tgt_pv = await world.profile_version(target)
    src_pv = await world.profile_version(src.id)
    await world.resume_document(target, tgt_pv["id"])
    await world.resume_document(src.id, src_pv["id"])

    result = await world.link(src.subject, await world.mint(target), src.id)

    assert result["ok"] is True
    creds = (
        await sb.table("provider_credentials")
        .select("secret_encrypted")
        .eq("user_id", target)
        .execute()
    ).data
    assert creds == [{"secret_encrypted": "target-secret"}]
    prefs = (
        await sb.table("capability_preferences")
        .select("execution_mode")
        .eq("user_id", target)
        .execute()
    ).data
    assert prefs == [{"execution_mode": "byok"}]
    answers = (
        await sb.table("approved_answers").select("answer_text").eq("user_id", target).execute()
    ).data
    assert answers == [{"answer_text": "target answer"}]
    searches = (
        await sb.table("hiring_signal_searches").select("query").eq("user_id", target).execute()
    ).data
    assert searches == [{"query": "ML Engineer"}]
    saves = (
        await sb.table("hiring_signal_saves").select("id").eq("user_id", target).execute()
    ).data
    assert len(saves) == 1
    masters = (
        await sb.table("resume_documents")
        .select("id")
        .eq("user_id", target)
        .is_("application_id", "null")
        .execute()
    ).data
    assert len(masters) == 1
    assert await _drained(world, src.id) == {}


async def test_application_events_keep_every_row(world: World) -> None:
    """An audit trail: a colliding idempotency key is renamed, not dropped, and
    what the source did as the Telegram identity is re-attributed to the target."""
    src = await world.telegram_user()
    target = await world.web_user()
    src_app = await world.application(src.id, await world.job())
    tgt_app = await world.application(target, await world.job())

    def event(user: str, app: str) -> dict[str, Any]:
        return {
            "application_id": app,
            "user_id": user,
            "event_type": "created",
            "payload": {},
            "actor_type": "user",
            "actor_id": user,
            "idempotency_key": "shared-key",
        }

    await world.sb.table("application_events").insert(event(target, tgt_app["id"])).execute()
    src_event = (
        await world.sb.table("application_events").insert(event(src.id, src_app["id"])).execute()
    ).data[0]

    await world.link(src.subject, await world.mint(target), src.id)

    survivor = (
        await world.sb.table("application_events")
        .select("user_id,actor_id,idempotency_key")
        .eq("id", src_event["id"])
        .execute()
    ).data[0]
    assert survivor == {
        "user_id": target,
        "actor_id": target,
        "idempotency_key": f"merged:{src_event['id']}:shared-key",
    }
    both = (
        await world.sb.table("application_events")
        .select("id")
        .eq("user_id", target)
        .eq("event_type", "created")
        .execute()
    ).data
    assert len(both) == 2


async def test_another_channel_both_accounts_share_is_resolved_for_the_target(world: World) -> None:
    """channel_identities is unique on (user_id, channel). Nothing writes a
    non-Telegram identity yet, but the check constraint already allows them;
    without this the link would fail on every retry."""
    src = await world.telegram_user()
    target = await world.web_user()
    await world.identity(target, "extension", f"ext-{uuid.uuid4().hex[:8]}")
    src_ext = f"ext-{uuid.uuid4().hex[:8]}"
    await world.identity(src.id, "extension", src_ext)

    result = await world.link(src.subject, await world.mint(target), src.id)

    assert result["ok"] is True
    rows = (
        await world.sb.table("channel_identities")
        .select("user_id,external_subject")
        .eq("channel", "extension")
        .in_("user_id", [target, src.id])
        .execute()
    ).data
    assert [r["user_id"] for r in rows] == [target]
    assert rows[0]["external_subject"] != src_ext


# --- profile versions ------------------------------------------------------


async def test_the_same_profile_on_both_sides_is_deduplicated(world: World) -> None:
    src = await world.telegram_user()
    target = await world.web_user()
    shared = {"same": True}
    tgt_pv = await world.profile_version(
        target, content_hash="shared-hash", canonical=shared, activated_at="2026-08-01T00:00:00Z"
    )
    src_pv = await world.profile_version(
        src.id, content_hash="shared-hash", canonical=shared, activated_at="2026-09-01T00:00:00Z"
    )
    matched_on_target = await world.fact(target, tgt_pv["id"], pointer="skills.0")
    matched_on_source = await world.fact(src.id, src_pv["id"], pointer="skills.0")
    only_on_source = await world.fact(src.id, src_pv["id"], pointer="skills.1")
    await world.resume_document(src.id, src_pv["id"], fact_ids=[matched_on_source["id"]])

    result = await world.link(src.subject, await world.mint(target), src.id)

    assert result["ok"] is True
    remaining = (
        await world.sb.table("profile_versions")
        .select("id")
        .eq("content_hash", "shared-hash")
        .execute()
    ).data
    assert [r["id"] for r in remaining] == [tgt_pv["id"]]
    unique = (
        await world.sb.table("career_facts")
        .select("profile_version_id")
        .eq("id", only_on_source["id"])
        .execute()
    ).data
    assert unique == [
        {"profile_version_id": tgt_pv["id"]}
    ]  # real content moves onto the kept version
    duplicate = (
        await world.sb.table("career_facts")
        .select("id")
        .eq("id", matched_on_source["id"])
        .execute()
    ).data
    assert duplicate == []  # a genuine duplicate goes with the deleted version
    document = (
        await world.sb.table("resume_documents")
        .select("selected_evidence_fact_ids")
        .eq("user_id", target)
        .execute()
    ).data
    assert document == [{"selected_evidence_fact_ids": [matched_on_target["id"]]}]
    restamped = (
        await world.sb.table("profile_versions")
        .select("activated_at")
        .eq("id", tgt_pv["id"])
        .execute()
    ).data[0]["activated_at"]
    assert restamped > "2026-09-01"  # the target's own profile stays the active one


async def test_two_source_facts_sharing_a_slot_both_survive(world: World) -> None:
    """Two facts on the source with the same (fact_type, source_pointer) that
    match nothing on the target are new content, not duplicates of it. An
    earlier version re-queried mid-loop, saw its own move, and destroyed the
    second one."""
    src = await world.telegram_user()
    target = await world.web_user()
    shared = {"same": True}
    tgt_pv = await world.profile_version(target, content_hash="h-slot", canonical=shared)
    src_pv = await world.profile_version(src.id, content_hash="h-slot", canonical=shared)
    first = await world.fact(src.id, src_pv["id"], pointer="dup.slot", value="FIRST")
    second = await world.fact(src.id, src_pv["id"], pointer="dup.slot", value="SECOND")
    await world.resume_document(src.id, src_pv["id"], fact_ids=[second["id"]])

    await world.link(src.subject, await world.mint(target), src.id)

    facts = (
        await world.sb.table("career_facts")
        .select("id,profile_version_id")
        .in_("id", [first["id"], second["id"]])
        .execute()
    ).data
    assert sorted(f["id"] for f in facts) == sorted([first["id"], second["id"]])
    assert {f["profile_version_id"] for f in facts} == {tgt_pv["id"]}
    document = (
        await world.sb.table("resume_documents")
        .select("selected_evidence_fact_ids")
        .eq("user_id", target)
        .execute()
    ).data
    assert document == [{"selected_evidence_fact_ids": [second["id"]]}]


async def test_equal_hashes_over_different_content_are_refused_and_rolled_back(
    world: World,
) -> None:
    """content_hash can be rewritten by the user, so equal hashes never prove
    equal content."""
    src = await world.telegram_user()
    target = await world.web_user()
    await world.profile_version(target, content_hash="collide", canonical={"who": "target"})
    await world.profile_version(src.id, content_hash="collide", canonical={"who": "source"})
    code = await world.mint(target)

    with pytest.raises(APIError) as refused:
        await world.link(src.subject, code, src.id)

    assert refused.value.code == "BJ002"
    consumed = (
        await world.sb.table("link_codes").select("consumed_at").eq("user_id", target).execute()
    ).data
    assert consumed == [{"consumed_at": None}]  # the raise rolled back the burn too
    still_source = (
        await world.sb.table("profile_versions").select("id").eq("user_id", src.id).execute()
    ).data
    assert len(still_source) == 1


# --- the merge refuses to lose what it doesn't know about ------------------


async def test_a_table_the_merge_does_not_know_makes_the_link_refuse(world: World, pg: Any) -> None:
    """The completeness check: a future table with a foreign key to auth.users
    that merge_user_data hasn't been taught about refuses the link, rather
    than letting the source's rows be cascade-deleted later."""
    src = await world.telegram_user()
    target = await world.web_user()
    await pg.execute("drop table if exists public._probe_unhandled")
    await pg.execute(
        """
        create table public._probe_unhandled (
            id uuid primary key default gen_random_uuid(),
            user_id uuid not null references auth.users (id) on delete cascade
        )
        """
    )
    try:
        await pg.execute("grant all on public._probe_unhandled to service_role")
        await pg.execute(
            "insert into public._probe_unhandled (user_id) values ($1)", uuid.UUID(src.id)
        )

        with pytest.raises(APIError) as refused:
            await world.link(src.subject, await world.mint(target), src.id)

        assert refused.value.code == "BJ003"
        assert await pg.fetchval("select count(*) from public._probe_unhandled") == 1
        owner = (
            await world.sb.table("channel_identities")
            .select("user_id")
            .eq("external_subject", src.subject)
            .execute()
        ).data
        assert owner == [{"user_id": src.id}]  # nothing moved
    finally:
        await pg.execute("drop table if exists public._probe_unhandled")


async def test_a_composite_foreign_key_into_a_collapsed_table_refuses_the_link(
    world: World, pg: Any
) -> None:
    """assert_unreferenced checks single-column foreign keys; rather than
    silently skip a composite one, it refuses."""
    src = await world.telegram_user()
    target = await world.web_user()
    job = await world.job()
    await world.application(src.id, job)
    await world.application(target, job)
    await pg.execute("drop table if exists public._probe_composite_fk")
    await pg.execute(
        """
        create table public._probe_composite_fk (
            user_id uuid not null, job_id uuid not null,
            foreign key (user_id, job_id) references public.applications (user_id, job_id)
                on delete cascade
        )
        """
    )
    try:
        await pg.execute("grant all on public._probe_composite_fk to service_role")
        await pg.execute(
            "insert into public._probe_composite_fk values ($1, $2)",
            uuid.UUID(src.id),
            uuid.UUID(job[0]),
        )

        with pytest.raises(APIError) as refused:
            await world.link(src.subject, await world.mint(target), src.id)

        assert refused.value.code == "BJ008"
        assert await pg.fetchval("select count(*) from public._probe_composite_fk") == 1
    finally:
        await pg.execute("drop table if exists public._probe_composite_fk")


async def test_the_set_of_user_owned_tables_is_the_one_the_merge_was_written_for(pg: Any) -> None:
    """Fails when a migration adds or drops a table with a foreign key to
    auth.users, so whoever does it decides how a merge treats it."""
    rows = await pg.fetch(
        """
        select distinct c.conrelid::regclass::text as tbl
        from pg_constraint c
        where c.contype = 'f' and c.confrelid = 'auth.users'::regclass
          and c.connamespace = 'public'::regnamespace
        """
    )
    assert {r["tbl"].removeprefix("public.") for r in rows} == _USER_OWNED_TABLES


async def test_the_sql_artifact_id_matches_the_python_one(pg: Any) -> None:
    for application_id, kind in (
        (str(uuid.uuid4()), "resume"),
        (str(uuid.uuid4()), "cover_letter"),
        ("00000000-0000-0000-0000-000000000001", "application_answers"),
    ):
        derived = await pg.fetchval(
            "select extensions.uuid_generate_v5('6f6e9b64-6b8e-4c3e-9a0c-2f6a2b1a2d4e'::uuid, $1)",
            f"{application_id}:{kind}",
        )
        assert str(derived) == artifact_id_for(application_id, kind)


# --- who may link ----------------------------------------------------------


async def test_an_account_already_linked_to_the_web_cannot_be_merged_away(world: World) -> None:
    """The hijack: a Telegram account linked to web account A sends a code
    minted by web account B. The RPC refuses, burns the code, moves nothing."""
    subject = f"9{uuid.uuid4().int % 10**11:011d}"
    victim = await world.web_user()
    attacker_target = await world.web_user()
    world.subjects.append(subject)
    await world.identity(victim, "telegram", subject)
    await (
        world.sb.table("saved_searches")
        .insert({"user_id": victim, "query": "victim's search"})
        .execute()
    )
    code = await world.mint(attacker_target)

    result = await world.link(subject, code, victim)

    assert result == {"ok": False, "reason": "source_already_linked"}
    burned = (
        await world.sb.table("link_codes")
        .select("consumed_at")
        .eq("user_id", attacker_target)
        .execute()
    ).data
    assert burned[0]["consumed_at"] is not None
    assert (await world.counts(victim)).get("public.saved_searches") == 1
    assert await world.user_exists(victim)


async def test_markers_a_web_user_can_write_do_not_make_it_a_telegram_account(world: World) -> None:
    """Only app_metadata, which the service role alone writes, counts.
    user_metadata is editable by the user."""
    subject = f"9{uuid.uuid4().int % 10**11:011d}"
    world.subjects.append(subject)
    created = await world.sb.auth.admin.create_user(
        {
            "email": f"spoof-{uuid.uuid4().hex}@example.com",
            "password": uuid.uuid4().hex,
            "email_confirm": True,
            "user_metadata": {"bj_provisioned_by": "telegram", "bj_telegram_subject": subject},
        }
    )
    spoofer = created.user.id
    world.users.append(spoofer)
    await world.identity(spoofer, "telegram", subject)
    target = await world.web_user()

    result = await world.link(subject, await world.mint(target), spoofer)

    assert result == {"ok": False, "reason": "source_already_linked"}


async def test_a_web_account_with_a_telegram_already_refuses_a_second(world: World) -> None:
    src = await world.telegram_user()
    target = await world.web_user()
    await world.identity(target, "telegram", f"9{uuid.uuid4().int % 10**11:011d}")

    result = await world.link(src.subject, await world.mint(target), src.id)

    assert result == {"ok": False, "reason": "target_linked_elsewhere"}
    owner = (
        await world.sb.table("channel_identities")
        .select("user_id")
        .eq("external_subject", src.subject)
        .execute()
    ).data
    assert owner == [{"user_id": src.id}]


async def test_a_stale_resolution_of_the_account_is_refused(world: World) -> None:
    """The caller resolved a user before a concurrent link moved the identity."""
    src = await world.telegram_user()
    target = await world.web_user()
    stranger = await world.web_user()

    result = await world.link(src.subject, await world.mint(target), stranger)

    assert result == {"ok": False, "reason": "source_mismatch"}


async def test_an_account_linking_with_its_own_code_is_a_noop(world: World) -> None:
    subject = f"9{uuid.uuid4().int % 10**11:011d}"
    user = await world.web_user()
    world.subjects.append(subject)
    await world.identity(user, "telegram", subject)

    result = await world.link(subject, await world.mint(user), user)

    assert result == {"ok": True, "target_user_id": user, "source_user_id": user}


# --- the code ---------------------------------------------------------------


async def test_the_same_account_resending_a_used_code_is_recognized_as_resuming(
    world: World,
) -> None:
    src = await world.telegram_user()
    target = await world.web_user()
    code = await world.mint(target)
    await world.link(src.subject, code, src.id)

    # The Telegram account resolves to the target now, so that's who resends.
    again = await world.link(src.subject, code, target)

    assert again == {
        "ok": True,
        "resumed": True,
        "target_user_id": target,
        "source_user_id": src.id,
    }


async def test_another_account_reusing_a_used_code_is_told_it_is_invalid(world: World) -> None:
    src = await world.telegram_user()
    other = await world.telegram_user()
    target = await world.web_user()
    code = await world.mint(target)
    await world.link(src.subject, code, src.id)

    result = await world.link(other.subject, code, other.id)

    assert result == {"ok": False, "reason": "invalid_code"}
    assert (
        await world.sb.table("channel_identities")
        .select("user_id")
        .eq("external_subject", other.subject)
        .execute()
    ).data == [{"user_id": other.id}]


async def test_resending_a_refused_code_is_invalid_not_a_resumed_link(world: World) -> None:
    """A refusal burns the code but completed no merge. Sending it again must
    not read as "you're linked" -- nothing was."""
    subject = f"9{uuid.uuid4().int % 10**11:011d}"
    world.subjects.append(subject)
    already_linked = await world.web_user()
    await world.identity(already_linked, "telegram", subject)
    other_target = await world.web_user()
    code = await world.mint(other_target)

    first = await world.link(subject, code, already_linked)
    again = await world.link(subject, code, already_linked)

    assert first == {"ok": False, "reason": "source_already_linked"}
    assert again == {"ok": False, "reason": "invalid_code"}

    src = await world.telegram_user()
    taken_target = await world.web_user()
    await world.identity(taken_target, "telegram", f"9{uuid.uuid4().int % 10**11:011d}")
    code = await world.mint(taken_target)

    first = await world.link(src.subject, code, src.id)
    again = await world.link(src.subject, code, src.id)

    assert first == {"ok": False, "reason": "target_linked_elsewhere"}
    assert again == {"ok": False, "reason": "invalid_code"}


async def test_resending_a_used_code_after_unlinking_is_not_resumed(world: World) -> None:
    """The link was real, then undone. The Telegram account is a fresh one
    now, and the old code has nothing left to finish."""
    src = await world.telegram_user()
    target = await world.web_user()
    code = await world.mint(target)
    await world.link(src.subject, code, src.id)
    await (
        world.sb.table("channel_identities")
        .delete()
        .eq("user_id", target)
        .eq("channel", "telegram")
        .execute()
    )
    fresh = await world.web_user()
    await world.identity(fresh, "telegram", src.subject)

    result = await world.link(src.subject, code, fresh)

    assert result == {"ok": False, "reason": "invalid_code"}


async def test_an_expired_code_is_refused(world: World) -> None:
    src = await world.telegram_user()
    target = await world.web_user()
    code = await world.mint(target, expires_in=timedelta(minutes=-1))

    result = await world.link(src.subject, code, src.id)

    assert result == {"ok": False, "reason": "expired_code"}


async def test_failures_lock_the_account_out_and_a_success_does_not_undo_it(world: World) -> None:
    src = await world.telegram_user()
    target = await world.web_user()
    for _ in range(3):
        assert (await world.link(src.subject, "WRONGONE", src.id))["reason"] == "invalid_code"

    good = await world.link(src.subject, await world.mint(target), src.id)
    assert good["ok"] is True  # still under the limit

    attempts = (
        await world.sb.table("link_code_attempts")
        .select("failed_count")
        .eq("external_subject", src.subject)
        .execute()
    ).data
    assert attempts == [{"failed_count": 3}]  # a success never resets it


async def test_five_failures_lock_the_subject_even_against_a_valid_code(world: World) -> None:
    src = await world.telegram_user()
    target = await world.web_user()
    for _ in range(5):
        await world.link(src.subject, "WRONGONE", src.id)

    result = await world.link(src.subject, await world.mint(target), src.id)

    assert result["reason"] == "rate_limited"


async def test_old_failures_stop_counting(world: World, pg: Any) -> None:
    src = await world.telegram_user()
    for _ in range(4):
        await world.link(src.subject, "WRONGONE", src.id)
    await pg.execute(
        "update link_code_attempts set updated_at = $1 where external_subject = $2",
        datetime.now(UTC) - timedelta(minutes=20),
        src.subject,
    )

    await world.link(src.subject, "WRONGONE", src.id)

    attempts = (
        await world.sb.table("link_code_attempts")
        .select("failed_count,locked_until")
        .eq("external_subject", src.subject)
        .execute()
    ).data
    assert attempts == [{"failed_count": 1, "locked_until": None}]


# --- finishing and retiring -------------------------------------------------


async def test_finish_link_merge_is_safe_to_repeat(world: World) -> None:
    src = await world.telegram_user()
    target = await world.web_user()
    await world.link(src.subject, await world.mint(target), src.id)
    args = {
        "p_channel": "telegram",
        "p_subject": src.subject,
        "p_source": src.id,
        "p_target": target,
    }

    first = (await world.sb.rpc("finish_link_merge", args).execute()).data
    second = (await world.sb.rpc("finish_link_merge", args).execute()).data

    assert first["ok"] is True and second["ok"] is True
    assert second["source_gone"] is False


@pytest.mark.parametrize("function", ["finish_link_merge", "finish_link_delete_source"])
async def test_finishing_is_refused_unless_the_target_owns_the_identity(
    world: World, function: str
) -> None:
    src = await world.telegram_user()
    target = await world.web_user()

    with pytest.raises(APIError) as refused:
        await world.sb.rpc(
            function,
            {
                "p_channel": "telegram",
                "p_subject": src.subject,
                "p_source": src.id,
                "p_target": target,
            },
        ).execute()

    assert refused.value.code == "BJ006"


@pytest.mark.parametrize("function", ["finish_link_merge", "finish_link_delete_source"])
async def test_finishing_is_refused_without_a_consumed_code_between_the_two(
    world: World, function: str
) -> None:
    """The target owns the identity, but no link between it and this source was
    ever consumed -- so a caller can't name an arbitrary account as the source."""
    subject = f"9{uuid.uuid4().int % 10**11:011d}"
    world.subjects.append(subject)
    target = await world.web_user()
    await world.identity(target, "telegram", subject)
    bystander = await world.web_user()

    with pytest.raises(APIError) as refused:
        await world.sb.rpc(
            function,
            {
                "p_channel": "telegram",
                "p_subject": subject,
                "p_source": bystander,
                "p_target": target,
            },
        ).execute()

    assert refused.value.code == "BJ007"
    assert await world.user_exists(bystander)


async def test_the_source_is_not_deleted_while_it_still_owns_anything(world: World) -> None:
    src = await world.telegram_user()
    target = await world.web_user()
    await world.link(src.subject, await world.mint(target), src.id)
    await world.sb.table("saved_searches").insert({"user_id": src.id, "query": "late"}).execute()

    deleted = (
        await world.sb.rpc(
            "finish_link_delete_source",
            {
                "p_channel": "telegram",
                "p_subject": src.subject,
                "p_source": src.id,
                "p_target": target,
            },
        ).execute()
    ).data

    assert deleted == {"ok": True, "source_gone": False, "counts": {"public.saved_searches": 1}}
    assert await world.user_exists(src.id)


async def test_an_emptied_source_is_deleted_and_asking_again_is_harmless(world: World) -> None:
    src = await world.telegram_user()
    target = await world.web_user()
    await world.link(src.subject, await world.mint(target), src.id)
    args = {
        "p_channel": "telegram",
        "p_subject": src.subject,
        "p_source": src.id,
        "p_target": target,
    }

    first = (await world.sb.rpc("finish_link_delete_source", args).execute()).data
    second = (await world.sb.rpc("finish_link_delete_source", args).execute()).data

    assert first == {"ok": True, "source_gone": True}
    assert second == {"ok": True, "source_gone": True}
    assert not await world.user_exists(src.id)


# --- who can call what ------------------------------------------------------

_USER = str(uuid.uuid4())
_CALLS: dict[str, dict[str, Any]] = {
    "consume_link_code": {
        "p_channel": "telegram",
        "p_external_subject": "x",
        "p_code": "x",
        "p_source_user_id": _USER,
    },
    "finish_link_merge": {
        "p_channel": "telegram",
        "p_subject": "x",
        "p_source": _USER,
        "p_target": _USER,
    },
    "finish_link_delete_source": {
        "p_channel": "telegram",
        "p_subject": "x",
        "p_source": _USER,
        "p_target": _USER,
    },
    "user_owned_row_counts": {"p_user_id": _USER},
    "merge_user_data": {"p_source_user_id": _USER, "p_target_user_id": _USER, "p_subject": "x"},
    "is_auto_provisioned_telegram_user": {"p_user_id": _USER, "p_subject": "x"},
    "assert_unreferenced": {"p_table": "public.applications", "p_id": _USER},
}
_SERVICE_ROLE_MAY_CALL = {
    "consume_link_code",
    "finish_link_merge",
    "finish_link_delete_source",
    "user_owned_row_counts",
}


@pytest.mark.parametrize("function", sorted(_CALLS))
async def test_only_the_backend_can_call_the_link_functions(world: World, function: str) -> None:
    stack = world.stack
    anon = await acreate_client(stack["API_URL"], stack["ANON_KEY"])
    password = uuid.uuid4().hex
    email = f"authn-{uuid.uuid4().hex}@example.com"
    await world.web_user(password=password, email=email)
    signed_in = await acreate_client(stack["API_URL"], stack["ANON_KEY"])
    await signed_in.auth.sign_in_with_password({"email": email, "password": password})

    for client in (anon, signed_in):
        with pytest.raises(APIError):
            await client.rpc(function, _CALLS[function]).execute()

    if function in _SERVICE_ROLE_MAY_CALL:
        try:
            await world.sb.rpc(function, _CALLS[function]).execute()
        except APIError as e:
            # Refusing this made-up input is fine; being refused as a role isn't.
            assert e.code != "42501"
    else:
        with pytest.raises(APIError) as refused:
            await world.sb.rpc(function, _CALLS[function]).execute()
        assert refused.value.code in {"42501", "PGRST202"}


def test_the_code_hash_is_the_plain_sha256_the_python_side_mints() -> None:
    """consume_link_code hashes with digest(code, 'sha256'); mint_code in
    link_codes_store hashes with hashlib -- they have to agree."""
    from between_jobs.api.link_codes_store import _hash_code

    assert _hash_code("ABCD2345") == hashlib.sha256(b"ABCD2345").hexdigest()
