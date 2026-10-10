"""Cross-tenant cases for the canonical profile (`/profile/...`) and the resume documents built
on it (`/resume-documents/...`).

Nothing may leave the machine, so the routes that would call forge-engines or an LLM are kept
on this side of the network: no credential is seeded, so Tailor coverage and the Gap Interview
stop at SETUP_REQUIRED (409) for the owner, after the ownership lookup has run."""

from __future__ import annotations

from typing import Any

from .harness import Case, Ctx, NotUserScoped, Req, Tenant, seeder


@seeder("pf_active_profile", tables=("profile_versions",))
async def seed_active_profile(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    """An ACTIVATED profile version with one experience entry (the Gap Interview needs one)."""
    name = ctx.mark(tenant, ctx.tag("Pat"))
    headline = ctx.mark(tenant, ctx.tag("Headline"))
    company = ctx.mark(tenant, ctx.tag("Company"))
    row = await ctx.world.profile_version(
        tenant.user_id,
        canonical={
            "personal": {"name": name, "headline": headline},
            "experience": [
                {
                    "title": "Engineer",
                    "company": company,
                    "start_date": "2020-01",
                    "end_date": "2021-01",
                    "bullets": [ctx.mark(tenant, ctx.tag("Bullet"))],
                }
            ],
        },
        # Later than any activation a case makes with now(): another case activating a profile
        # (the activate route's own control does) must not displace the one the rest rely on.
        activated_at="2099-01-01T00:00:00+00:00",
    )
    return {"id": row["id"], "name": name, "headline": headline, "company": company, "row": row}


@seeder("pf_resume_doc", tables=("resume_documents", "career_facts", "applications"))
async def seed_resume_doc(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    """A per-application resume document bound to the tenant's active profile and the job's
    snapshot, holding one marked string in its header layout and one in its assertions."""
    profile = await ctx.need("pf_active_profile", tenant)
    app = await ctx.need("application", tenant)
    fact = await ctx.world.fact(
        tenant.user_id,
        profile["id"],
        pointer=f"/skills/{ctx.tag('s')}",
        value=ctx.mark(tenant, ctx.tag("Skill")),
    )
    layout = {"label": ctx.mark(tenant, ctx.tag("Layout"))}
    asserted = [ctx.mark(tenant, ctx.tag("Assertion"))]
    doc = await ctx.world.resume_document(
        tenant.user_id, profile["id"], application_id=app["id"], fact_ids=[]
    )
    await (
        ctx.sb.table("resume_documents")
        .update(
            {
                "job_snapshot_id": app["job"]["snapshot_id"],
                "header_layout": layout,
                "assertions": asserted,
            }
        )
        .eq("id", doc["id"])
        .execute()
    )
    return {"id": doc["id"], "fact_id": fact["id"], "layout": layout, "assertions": asserted}


async def _doc_row(ctx: Ctx) -> dict[str, Any]:
    doc = await ctx.need("pf_resume_doc", ctx.a)
    result = await ctx.sb.table("resume_documents").select("*").eq("id", doc["id"]).execute()
    row: dict[str, Any] = result.data[0]
    return row


async def _version_row(ctx: Ctx, kind: str) -> dict[str, Any]:
    version = await ctx.need(kind, ctx.a)
    result = await ctx.sb.table("profile_versions").select("*").eq("id", version["id"]).execute()
    row: dict[str, Any] = result.data[0]
    return row


async def _gap_versions(ctx: Ctx, tenant: Tenant) -> int:
    result = (
        await ctx.sb.table("profile_versions")
        .select("id")
        .eq("user_id", tenant.user_id)
        .eq("source_kind", "gap_interview")
        .execute()
    )
    return len(result.data)


async def _b_without_a_profile(ctx: Ctx) -> None:
    """The two tenants live for the whole module, so an earlier case may have given B a
    profile. These routes act on the caller's own active profile, so B must have none for the
    stranger's answer to be a 404 (a B with a profile would simply succeed on its own data)."""
    await ctx.sb.table("profile_versions").delete().eq("user_id", ctx.b.user_id).execute()


# -- /profile ----------------------------------------------------------------------------------


async def _current(ctx: Ctx) -> Req:
    await ctx.need("pf_active_profile", ctx.a)
    return Req("GET", "/profile/current")


async def _approve(ctx: Ctx) -> Req:
    await ctx.need("pf_active_profile", ctx.a)
    await _b_without_a_profile(ctx)
    return Req(
        "POST",
        "/profile/gap-interview/approve",
        json={"bullet": ctx.tag("Built a thing"), "entity_pointer": "/experience/0"},
    )


async def _approve_unchanged(ctx: Ctx) -> None:
    assert await _gap_versions(ctx, ctx.a) == 0, "B's approve added a version under A"
    assert await _gap_versions(ctx, ctx.b) == 0, "B's approve created a version for B"


async def _draft(ctx: Ctx) -> Req:
    await ctx.need("pf_active_profile", ctx.a)
    await _b_without_a_profile(ctx)
    return Req(
        "POST",
        "/profile/gap-interview/draft",
        json={"question": ctx.tag("Did you use X?"), "answer": ctx.tag("Yes, at work")},
    )


async def _version_get(ctx: Ctx) -> Req:
    v = await ctx.need("profile_version", ctx.a)
    return Req("GET", f"/profile/versions/{v['id']}")


async def _version_delete(ctx: Ctx) -> Req:
    v = await ctx.need("profile_version", ctx.a)
    return Req("DELETE", f"/profile/versions/{v['id']}")


async def _version_delete_unchanged(ctx: Ctx) -> None:
    await _version_row(ctx, "profile_version")  # raises IndexError if B deleted it


async def _version_activate(ctx: Ctx) -> Req:
    v = await ctx.need("profile_version", ctx.a)
    return Req("POST", f"/profile/versions/{v['id']}/activate")


async def _version_activate_unchanged(ctx: Ctx) -> None:
    row = await _version_row(ctx, "profile_version")
    assert row["activated_at"] is None, "B activated A's pending profile version"


async def _facts(ctx: Ctx) -> Req:
    fact = await ctx.need("career_fact", ctx.a)
    return Req("GET", f"/profile/versions/{fact['profile_version_id']}/career-facts")


# -- /resume-documents -------------------------------------------------------------------------


async def _mine(ctx: Ctx) -> Req:
    app = await ctx.need("application", ctx.a)
    await ctx.need("pf_active_profile", ctx.a)
    return Req("GET", "/resume-documents/mine", params={"application_id": app["id"]})


async def _mine_unchanged(ctx: Ctx) -> None:
    app = await ctx.need("application", ctx.a)
    rows = (
        await ctx.sb.table("resume_documents")
        .select("id")
        .eq("application_id", app["id"])
        .execute()
    ).data
    assert rows == [], "B's request created a resume document for A's application"


async def _header(ctx: Ctx) -> Req:
    doc = await ctx.need("pf_resume_doc", ctx.a)
    return Req(
        "PATCH",
        f"/resume-documents/{doc['id']}/header",
        json={"header_layout": {"label": "B was here"}},
    )


async def _header_unchanged(ctx: Ctx) -> None:
    doc = await ctx.need("pf_resume_doc", ctx.a)
    assert (await _doc_row(ctx))["header_layout"] == doc["layout"], "B rewrote A's header layout"


async def _evidence(ctx: Ctx) -> Req:
    doc = await ctx.need("pf_resume_doc", ctx.a)
    return Req(
        "PATCH",
        f"/resume-documents/{doc['id']}/evidence",
        json={"evidence_fact_ids": [doc["fact_id"]]},
    )


async def _evidence_unchanged(ctx: Ctx) -> None:
    row = await _doc_row(ctx)
    assert row["selected_evidence_fact_ids"] == [], "B changed A's selected evidence"


async def _assertions(ctx: Ctx) -> Req:
    doc = await ctx.need("pf_resume_doc", ctx.a)
    return Req(
        "PATCH",
        f"/resume-documents/{doc['id']}/assertions",
        json={"assertions": ["B asserts this"]},
    )


async def _assertions_unchanged(ctx: Ctx) -> None:
    doc = await ctx.need("pf_resume_doc", ctx.a)
    assert (await _doc_row(ctx))["assertions"] == doc["assertions"], "B changed A's assertions"


async def _coverage(ctx: Ctx) -> Req:
    doc = await ctx.need("pf_resume_doc", ctx.a)
    return Req("POST", f"/resume-documents/{doc['id']}/coverage")


async def _gap_interview(ctx: Ctx) -> Req:
    doc = await ctx.need("pf_resume_doc", ctx.a)
    return Req("POST", f"/resume-documents/{doc['id']}/gap-interview")


async def _preview(ctx: Ctx) -> Req:
    doc = await ctx.need("pf_resume_doc", ctx.a)
    return Req(
        "POST",
        f"/resume-documents/{doc['id']}/header/preview",
        json={"header_layout": {"label": "draft"}},
    )


async def _sections(ctx: Ctx) -> Req:
    doc = await ctx.need("pf_resume_doc", ctx.a)
    return Req(
        "PATCH",
        f"/resume-documents/{doc['id']}/sections",
        json={"section_order": ["experience"], "section_visibility": {"experience": False}},
    )


async def _sections_unchanged(ctx: Ctx) -> None:
    row = await _doc_row(ctx)
    assert row["section_order"] == [], "B reordered A's sections"
    assert row["section_visibility"] == {}, "B hid A's sections"


async def _shape(ctx: Ctx) -> Req:
    doc = await ctx.need("pf_resume_doc", ctx.a)
    return Req(
        "PATCH",
        f"/resume-documents/{doc['id']}/shape",
        json={"shape_overrides": {"page_count": "2", "density": "compact"}},
    )


async def _shape_unchanged(ctx: Ctx) -> None:
    assert (await _doc_row(ctx))["shape_overrides"] == {}, "B changed A's resume settings"


_NEEDS_SETUP = frozenset({409})

CASES = [
    Case(
        "GET /profile/current",
        _current,
        kind="list",
        foreign_status=200,
        b_seeds=("pf_active_profile",),
        note="a per-user singleton: B gets B's own profile, never A's",
    ),
    Case(
        "POST /profile/gap-interview/approve",
        _approve,
        owner_status=frozenset({201}),
        unchanged=_approve_unchanged,
        note="the route reads only the caller's own active profile and takes no id; B has no "
        "active profile, so B gets NOT_FOUND, and nothing is created for either user",
    ),
    Case(
        "POST /profile/gap-interview/draft",
        _draft,
        owner_status=_NEEDS_SETUP,
        note="A has no LLM credential, so the owner stops at SETUP_REQUIRED (409) before any "
        "network call; B has no active profile -> 404",
    ),
    Case("GET /profile/versions/{version_id}", _version_get),
    Case(
        "DELETE /profile/versions/{version_id}",
        _version_delete,
        owner_status=frozenset({204}),
        unchanged=_version_delete_unchanged,
    ),
    Case(
        "POST /profile/versions/{version_id}/activate",
        _version_activate,
        unchanged=_version_activate_unchanged,
    ),
    Case("GET /profile/versions/{version_id}/career-facts", _facts),
    Case(
        "GET /resume-documents/mine",
        _mine,
        unchanged=_mine_unchanged,
        note="exercised with A's application_id (the cross-tenant surface); without it the "
        "route only ever reads the caller's own master document",
    ),
    Case(
        "PATCH /resume-documents/{document_id}/header",
        _header,
        unchanged=_header_unchanged,
    ),
    Case(
        "PATCH /resume-documents/{document_id}/evidence",
        _evidence,
        unchanged=_evidence_unchanged,
        note="A's fact id is in B's body",
    ),
    Case(
        "PATCH /resume-documents/{document_id}/assertions",
        _assertions,
        unchanged=_assertions_unchanged,
    ),
    Case(
        "POST /resume-documents/{document_id}/coverage",
        _coverage,
        owner_status=_NEEDS_SETUP,
        note="A has no LLM credential: the owner stops at SETUP_REQUIRED (409) after the "
        "ownership lookup, before Step0 would call the network",
    ),
    Case(
        "POST /resume-documents/{document_id}/gap-interview",
        _gap_interview,
        owner_status=_NEEDS_SETUP,
        note="the owner stops at 409 after the ownership lookup: NOT_AVAILABLE_IN_GENERIC_ENGINE "
        "on the built-in engine the local-stack tests run on (a stranger gets 404 first)",
    ),
    Case(
        "POST /resume-documents/{document_id}/header/preview",
        _preview,
        owner_status=frozenset({200}),
        note="the preview is deterministic and runs on the engine built into the API here, so "
        "the owner's control really succeeds (after the ownership lookup passed)",
    ),
    Case(
        "PATCH /resume-documents/{document_id}/sections",
        _sections,
        unchanged=_sections_unchanged,
    ),
    Case(
        "PATCH /resume-documents/{document_id}/shape",
        _shape,
        unchanged=_shape_unchanged,
    ),
]

# Fixed: `GET /profile/versions/{id}` needs the by_id seeders registered in seeds_core.
NOT_USER_SCOPED = {
    "POST /profile/versions": NotUserScoped(
        "own-identity",
        "imports the pasted text as a new pending version for the caller; takes no id of "
        "anyone else's, and the owner is always the authenticated user",
    ),
    "POST /profile/import-document": NotUserScoped(
        "own-identity",
        "imports the uploaded file as a new pending version for the caller; takes no id of "
        "anyone else's and reads nothing of anyone else's (the only database calls are the "
        "caller's own credential lookup and `create_pending_version`, which the store cases "
        "already cover), and the owner is always the authenticated user",
    ),
    "GET /profile/integrations/gmail/connect": NotUserScoped(
        "own-identity",
        "mints an OAuth state for the caller and returns Google's authorize URL; no id, and "
        "needs the server's Google OAuth env, which the suite deliberately does not have",
    ),
}
