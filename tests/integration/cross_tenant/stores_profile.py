"""Store-level cross-tenant cases for the profile, resume-document, saved-search and hiring-signal
stores: each function is called directly as user B with user A's ids (see `harness.StoreCase`)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from between_jobs.api import (
    hiring_signal_saves_store as saves,
)
from between_jobs.api import (
    hiring_signal_searches_store as searches,
)
from between_jobs.api import (
    profile_store,
    resume_documents_store,
    saved_searches_store,
)
from between_jobs.api.profile import CareerFact, ImportedProfile

from . import cases_profile, cases_user  # noqa: F401  (importing registers their seeders)
from .harness import Ctx, StoreCase, Tenant


async def _one(ctx: Ctx, table: str, row_id: str) -> dict[str, Any] | None:
    rows = (await ctx.sb.table(table).select("*").eq("id", row_id).execute()).data
    return dict(rows[0]) if rows else None


async def _ids(ctx: Ctx, table: str, user_id: str) -> set[str]:
    rows = (await ctx.sb.table(table).select("id").eq("user_id", user_id).execute()).data
    return {r["id"] for r in rows}


def _imported(ctx: Ctx, *, with_fact: bool = False) -> ImportedProfile:
    name = ctx.tag("Imported")
    facts = (
        (CareerFact("project", ctx.tag("e"), {"name": name}, f"/projects/{ctx.tag('p')}"),)
        if with_fact
        else ()
    )
    return ImportedProfile(
        canonical_json={"personal": {"name": name}},
        content_hash=ctx.tag("hash"),
        schema_version="v1",
        career_facts=facts,
    )


# -- profile_store -----------------------------------------------------------------------------


async def _get_version(ctx: Ctx) -> None:
    v = await ctx.need("profile_version", ctx.a)
    with pytest.raises(profile_store.VersionNotFound):
        await profile_store.get_version(ctx.sb, ctx.b.user_id, v["id"])
    got = await profile_store.get_version(ctx.sb, ctx.a.user_id, v["id"])
    assert got["id"] == v["id"] and got["user_id"] == ctx.a.user_id


async def _list_career_facts(ctx: Ctx) -> None:
    fact = await ctx.need("career_fact", ctx.a)
    vid = fact["profile_version_id"]
    assert await profile_store.list_career_facts(ctx.sb, ctx.b.user_id, vid) == []
    mine = await profile_store.list_career_facts(ctx.sb, ctx.a.user_id, vid)
    assert fact["id"] in {f["id"] for f in mine}


async def _get_active_version(ctx: Ctx) -> None:
    # activated later than anything else in the suite, so it wins for A and would for any caller
    # it leaked to (other cases use 2099; a tie would make "the active version" ambiguous)
    row = await ctx.world.profile_version(ctx.a.user_id, activated_at="2100-01-01T00:00:00+00:00")
    got_b = await profile_store.get_active_version(ctx.sb, ctx.b.user_id)
    assert got_b is None or (got_b["user_id"] == ctx.b.user_id and got_b["id"] != row["id"])
    got_a = await profile_store.get_active_version(ctx.sb, ctx.a.user_id)
    assert got_a is not None and got_a["id"] == row["id"]


async def _count_versions(ctx: Ctx) -> None:
    await ctx.need("profile_version", ctx.a)
    await ctx.world.profile_version(ctx.a.user_id)
    a_ids = await _ids(ctx, "profile_versions", ctx.a.user_id)
    b_ids = await _ids(ctx, "profile_versions", ctx.b.user_id)
    assert len(a_ids) >= 2
    assert await profile_store.count_versions(ctx.sb, ctx.b.user_id) == len(b_ids)
    assert await profile_store.count_versions(ctx.sb, ctx.a.user_id) == len(a_ids)


async def _activate_version(ctx: Ctx) -> None:
    v = await ctx.need("profile_version", ctx.a)
    with pytest.raises(profile_store.VersionNotFound):
        await profile_store.activate_version(ctx.sb, ctx.b.user_id, v["id"])
    row = await _one(ctx, "profile_versions", v["id"])
    assert row is not None and row["activated_at"] is None, "B's call activated A's version"
    done = await profile_store.activate_version(ctx.sb, ctx.a.user_id, v["id"])
    assert done["id"] == v["id"] and done["activated_at"] is not None


async def _delete_pending_version(ctx: Ctx) -> None:
    v = await ctx.need("profile_version", ctx.a)
    fact = await ctx.world.fact(ctx.a.user_id, v["id"], pointer=f"/skills/{ctx.tag('s')}")
    with pytest.raises(profile_store.VersionNotFound):
        await profile_store.delete_pending_version(ctx.sb, ctx.b.user_id, v["id"])
    assert await _one(ctx, "profile_versions", v["id"]) is not None, "B deleted A's version"
    assert await _one(ctx, "career_facts", fact["id"]) is not None
    await profile_store.delete_pending_version(ctx.sb, ctx.a.user_id, v["id"])
    assert await _one(ctx, "profile_versions", v["id"]) is None


async def _create_pending_version(ctx: Ctx) -> None:
    a_imp = _imported(ctx, with_fact=True)
    a_row = await profile_store.create_pending_version(ctx.sb, ctx.a.user_id, a_imp, "json_paste")
    assert a_row["user_id"] == ctx.a.user_id
    a_before = await _ids(ctx, "profile_versions", ctx.a.user_id)
    a_facts_before = await _ids(ctx, "career_facts", ctx.a.user_id)

    # B submitting the SAME content hash must not get A's row back: it gets (and owns) its own.
    b_row = await profile_store.create_pending_version(ctx.sb, ctx.b.user_id, a_imp, "json_paste")
    assert b_row["user_id"] == ctx.b.user_id and b_row["id"] != a_row["id"]
    assert await _ids(ctx, "profile_versions", ctx.a.user_id) == a_before
    assert await _ids(ctx, "career_facts", ctx.a.user_id) == a_facts_before
    facts = (
        await ctx.sb.table("career_facts")
        .select("user_id")
        .eq("profile_version_id", b_row["id"])
        .execute()
    ).data
    assert facts and all(f["user_id"] == ctx.b.user_id for f in facts)

    # B naming A's version as the one it supersedes must not link B's row to A's.
    other = _imported(ctx)
    try:
        linked = await profile_store.create_pending_version(
            ctx.sb, ctx.b.user_id, other, "json_paste", supersedes_id=a_row["id"]
        )
    except Exception:
        linked = None
    assert linked is None or linked.get("supersedes_id") != a_row["id"], (
        "B created a version that supersedes A's version"
    )
    assert await _ids(ctx, "profile_versions", ctx.a.user_id) == a_before


# -- resume_documents_store --------------------------------------------------------------------


async def _doc(ctx: Ctx) -> tuple[dict[str, Any], dict[str, Any]]:
    seeded = await ctx.need("pf_resume_doc", ctx.a)
    row = await _one(ctx, "resume_documents", seeded["id"])
    assert row is not None
    return seeded, row


async def _get_document(ctx: Ctx) -> None:
    seeded, _ = await _doc(ctx)
    with pytest.raises(resume_documents_store.DocumentNotFound):
        await resume_documents_store.get_document(ctx.sb, ctx.b.user_id, seeded["id"])
    got = await resume_documents_store.get_document(ctx.sb, ctx.a.user_id, seeded["id"])
    assert got["id"] == seeded["id"]


async def _get_document_for(ctx: Ctx) -> None:
    seeded, row = await _doc(ctx)
    app_id = row["application_id"]
    assert app_id is not None
    assert (
        await resume_documents_store.get_document_for(ctx.sb, ctx.b.user_id, application_id=app_id)
        is None
    )
    master_b = await resume_documents_store.get_document_for(
        ctx.sb, ctx.b.user_id, application_id=None
    )
    assert master_b is None or master_b["id"] != seeded["id"]
    mine = await resume_documents_store.get_document_for(
        ctx.sb, ctx.a.user_id, application_id=app_id
    )
    assert mine is not None and mine["id"] == seeded["id"]


async def _check_update(ctx: Ctx, fn_name: str, kwargs: dict[str, Any], column: str) -> None:
    seeded, before = await _doc(ctx)
    fn = getattr(resume_documents_store, fn_name)
    with pytest.raises(resume_documents_store.DocumentNotFound):
        await fn(ctx.sb, ctx.b.user_id, seeded["id"], **kwargs)
    after = await _one(ctx, "resume_documents", seeded["id"])
    assert after == before, f"B's {fn_name} changed A's document"
    # mismatched: B asking for a document id that does not exist is the same answer
    with pytest.raises(resume_documents_store.DocumentNotFound):
        await fn(ctx.sb, ctx.b.user_id, str(uuid.uuid4()), **kwargs)
    updated = await fn(ctx.sb, ctx.a.user_id, seeded["id"], **kwargs)
    assert updated["id"] == seeded["id"]
    assert updated[column] == next(iter(kwargs.values()))


async def _update_header_layout(ctx: Ctx) -> None:
    await _check_update(
        ctx, "update_header_layout", {"header_layout": {"label": ctx.tag("B")}}, "header_layout"
    )


async def _update_selected_evidence(ctx: Ctx) -> None:
    await _check_update(
        ctx,
        "update_selected_evidence",
        {"evidence_fact_ids": [str(uuid.uuid4())]},
        "selected_evidence_fact_ids",
    )


async def _update_assertions(ctx: Ctx) -> None:
    await _check_update(ctx, "update_assertions", {"assertions": [ctx.tag("B")]}, "assertions")


async def _update_shape_overrides(ctx: Ctx) -> None:
    await _check_update(
        ctx, "update_shape_overrides", {"shape_overrides": {"density": "x"}}, "shape_overrides"
    )


async def _update_sections(ctx: Ctx) -> None:
    seeded, before = await _doc(ctx)
    kwargs: dict[str, Any] = {
        "section_order": ["skills", "experience"],
        "section_visibility": {"skills": False},
    }
    with pytest.raises(resume_documents_store.DocumentNotFound):
        await resume_documents_store.update_sections(ctx.sb, ctx.b.user_id, seeded["id"], **kwargs)
    assert await _one(ctx, "resume_documents", seeded["id"]) == before
    updated = await resume_documents_store.update_sections(
        ctx.sb, ctx.a.user_id, seeded["id"], **kwargs
    )
    assert updated["section_order"] == kwargs["section_order"]
    assert updated["section_visibility"] == kwargs["section_visibility"]


# -- saved_searches_store ----------------------------------------------------------------------


async def _get_saved_search(ctx: Ctx) -> None:
    s = await ctx.need("u_saved_search", ctx.a)
    with pytest.raises(saved_searches_store.SavedSearchNotFound):
        await saved_searches_store.get_saved_search(ctx.sb, ctx.b.user_id, s["id"])
    got = await saved_searches_store.get_saved_search(ctx.sb, ctx.a.user_id, s["id"])
    assert got["id"] == s["id"]


async def _list_saved_searches(ctx: Ctx) -> None:
    s = await ctx.need("u_saved_search", ctx.a)
    await ctx.need("u_saved_search", ctx.b)
    listed_b = await saved_searches_store.list_saved_searches(ctx.sb, ctx.b.user_id)
    assert s["id"] not in {r["id"] for r in listed_b}
    assert all(r["user_id"] == ctx.b.user_id for r in listed_b)
    assert listed_b, "B's own saved search is missing, so the empty-of-A's answer proves nothing"
    listed_a = await saved_searches_store.list_saved_searches(ctx.sb, ctx.a.user_id)
    assert s["id"] in {r["id"] for r in listed_a}


async def _set_saved_search_active(ctx: Ctx) -> None:
    s = await ctx.need("u_saved_search", ctx.a)
    before = await _one(ctx, "saved_searches", s["id"])
    assert before is not None
    with pytest.raises(saved_searches_store.SavedSearchNotFound):
        await saved_searches_store.set_saved_search_active(
            ctx.sb, ctx.b.user_id, s["id"], is_active=not before["is_active"]
        )
    assert await _one(ctx, "saved_searches", s["id"]) == before
    done = await saved_searches_store.set_saved_search_active(
        ctx.sb, ctx.a.user_id, s["id"], is_active=not before["is_active"]
    )
    assert done["is_active"] is (not before["is_active"])


async def _delete_saved_search(ctx: Ctx) -> None:
    s = await ctx.need("u_saved_search", ctx.a)
    before = await _one(ctx, "saved_searches", s["id"])
    with pytest.raises(saved_searches_store.SavedSearchNotFound):
        await saved_searches_store.delete_saved_search(ctx.sb, ctx.b.user_id, s["id"])
    assert await _one(ctx, "saved_searches", s["id"]) == before
    await saved_searches_store.delete_saved_search(ctx.sb, ctx.a.user_id, s["id"])
    assert await _one(ctx, "saved_searches", s["id"]) is None


# -- hiring_signal_saves_store -----------------------------------------------------------------


async def _app_save(ctx: Ctx, tenant: Tenant, application_id: str) -> str:
    activity_id = ctx.mark(tenant, f"8{uuid.uuid4().int % 10**18:018d}")
    await (
        ctx.sb.table("hiring_signal_saves")
        .insert(
            {
                "user_id": tenant.user_id,
                "application_id": application_id,
                "url": saves.canonical_post_url(activity_id),
                "activity_id": activity_id,
            }
        )
        .execute()
    )
    return activity_id


async def _find(ctx: Ctx) -> None:
    standalone = await ctx.need("u_hiring_save", ctx.a)
    app = await ctx.need("application", ctx.a)
    scoped = await _app_save(ctx, ctx.a, app["id"])
    for app_id, activity in ((None, standalone["activity_id"]), (app["id"], scoped)):
        assert await saves._find(ctx.sb, ctx.b.user_id, app_id, activity) is None
        mine = await saves._find(ctx.sb, ctx.a.user_id, app_id, activity)
        assert mine is not None and mine["user_id"] == ctx.a.user_id
    # crossed: the other namespace never answers
    assert await saves._find(ctx.sb, ctx.a.user_id, None, scoped) is None


async def _list_saves(ctx: Ctx) -> None:
    standalone = await ctx.need("u_hiring_save", ctx.a)
    app = await ctx.need("application", ctx.a)
    scoped = await _app_save(ctx, ctx.a, app["id"])
    b_standalone = await ctx.need("u_hiring_save", ctx.b)
    b_list = await saves.list_saves(ctx.sb, ctx.b.user_id, None)
    assert standalone["activity_id"] not in {p["activity_id"] for p in b_list}
    assert b_standalone["activity_id"] in {p["activity_id"] for p in b_list}
    assert await saves.list_saves(ctx.sb, ctx.b.user_id, app["id"]) == []
    a_list = await saves.list_saves(ctx.sb, ctx.a.user_id, None)
    assert standalone["activity_id"] in {p["activity_id"] for p in a_list}
    a_scoped = await saves.list_saves(ctx.sb, ctx.a.user_id, app["id"])
    assert {p["activity_id"] for p in a_scoped} == {scoped}


async def _saved_activity_ids(ctx: Ctx) -> None:
    standalone = await ctx.need("u_hiring_save", ctx.a)
    app = await ctx.need("application", ctx.a)
    scoped = await _app_save(ctx, ctx.a, app["id"])
    ids = {standalone["activity_id"], scoped}
    assert await saves.saved_activity_ids(ctx.sb, ctx.b.user_id, None, ids) == set()
    assert await saves.saved_activity_ids(ctx.sb, ctx.b.user_id, app["id"], ids) == set()
    assert await saves.saved_activity_ids(ctx.sb, ctx.a.user_id, None, ids) == {
        standalone["activity_id"]
    }
    assert await saves.saved_activity_ids(ctx.sb, ctx.a.user_id, app["id"], ids) == {scoped}


async def _delete_save(ctx: Ctx) -> None:
    s = await ctx.need("u_hiring_save", ctx.a)
    before = await _one(ctx, "hiring_signal_saves", s["id"])
    assert before is not None
    with pytest.raises(saves.HiringSignalSaveNotFound):
        await saves.delete_save(ctx.sb, ctx.b.user_id, s["id"])
    assert await _one(ctx, "hiring_signal_saves", s["id"]) == before
    await saves.delete_save(ctx.sb, ctx.a.user_id, s["id"])
    assert await _one(ctx, "hiring_signal_saves", s["id"]) is None


# -- hiring_signal_searches_store --------------------------------------------------------------


async def _rows(ctx: Ctx) -> None:
    s = await ctx.need("u_hiring_search", ctx.a)
    b = await ctx.need("u_hiring_search", ctx.b)
    b_rows = await searches._rows(ctx.sb, ctx.b.user_id)
    assert s["id"] not in {r["id"] for r in b_rows}
    assert all(r["user_id"] == ctx.b.user_id for r in b_rows)
    assert b["id"] in {r["id"] for r in b_rows}
    assert s["id"] in {r["id"] for r in await searches._rows(ctx.sb, ctx.a.user_id)}


async def _create_search(ctx: Ctx) -> None:
    s = await ctx.need("u_hiring_search", ctx.a)
    a_before = await _ids(ctx, "hiring_signal_searches", ctx.a.user_id)
    row_before = await _one(ctx, "hiring_signal_searches", s["id"])
    # B saves the very same search A has: it must get its OWN new row, never A's.
    saved, created = await searches.create_search(
        ctx.sb, ctx.b.user_id, query=s["query"], location=s["location"]
    )
    try:
        assert created is True and saved["id"] != s["id"]
        stored = await _one(ctx, "hiring_signal_searches", saved["id"])
        assert stored is not None and stored["user_id"] == ctx.b.user_id
        assert await _ids(ctx, "hiring_signal_searches", ctx.a.user_id) == a_before
        assert await _one(ctx, "hiring_signal_searches", s["id"]) == row_before
    finally:
        await ctx.sb.table("hiring_signal_searches").delete().eq("id", saved["id"]).execute()
    again, created_a = await searches.create_search(
        ctx.sb, ctx.a.user_id, query=s["query"], location=s["location"]
    )
    assert created_a is False and again["id"] == s["id"]


async def _delete_search(ctx: Ctx) -> None:
    s = await ctx.need("u_hiring_search", ctx.a)
    before = await _one(ctx, "hiring_signal_searches", s["id"])
    assert before is not None
    with pytest.raises(searches.HiringSignalSearchNotFound):
        await searches.delete_search(ctx.sb, ctx.b.user_id, s["id"])
    assert await _one(ctx, "hiring_signal_searches", s["id"]) == before
    await searches.delete_search(ctx.sb, ctx.a.user_id, s["id"])
    assert await _one(ctx, "hiring_signal_searches", s["id"]) is None


def _case(target: str, run: Any, note: str = "") -> StoreCase:
    return StoreCase(target, run, note)


STORE_CASES = [
    _case("profile_store.activate_version", _activate_version),
    _case("profile_store.count_versions", _count_versions),
    _case("profile_store.create_pending_version", _create_pending_version),
    _case(
        "profile_store.delete_pending_version",
        _delete_pending_version,
        "its own user_id filter on the delete is shadowed by the get_version check above it",
    ),
    _case("profile_store.get_active_version", _get_active_version),
    _case("profile_store.get_version", _get_version),
    _case("profile_store.list_career_facts", _list_career_facts),
    _case("resume_documents_store.get_document", _get_document),
    _case("resume_documents_store.get_document_for", _get_document_for),
    _case("resume_documents_store.update_assertions", _update_assertions),
    _case("resume_documents_store.update_header_layout", _update_header_layout),
    _case("resume_documents_store.update_sections", _update_sections),
    _case("resume_documents_store.update_selected_evidence", _update_selected_evidence),
    _case("resume_documents_store.update_shape_overrides", _update_shape_overrides),
    _case("saved_searches_store.delete_saved_search", _delete_saved_search),
    _case("saved_searches_store.get_saved_search", _get_saved_search),
    _case("saved_searches_store.list_saved_searches", _list_saved_searches),
    _case("saved_searches_store.set_saved_search_active", _set_saved_search_active),
    _case("hiring_signal_saves_store._find", _find),
    _case("hiring_signal_saves_store.delete_save", _delete_save),
    _case("hiring_signal_saves_store.list_saves", _list_saves),
    _case("hiring_signal_saves_store.saved_activity_ids", _saved_activity_ids),
    _case("hiring_signal_searches_store._rows", _rows),
    _case("hiring_signal_searches_store.create_search", _create_search),
    _case("hiring_signal_searches_store.delete_search", _delete_search),
]
