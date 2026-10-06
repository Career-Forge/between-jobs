"""Cross-tenant cases for `/applications` itself (the application list, one application, its
stage) and for the application-scoped routes that hang off it: the PDFs, the export checklist,
prepare, and the browser-extension surface (`/extension/*`, `.../extension-payload`). The
other routes under an application (company intel, contacts, ...) are in the other `cases_*`
modules.

Seeder kinds here are prefixed `app_` so they cannot collide with another module's.

Nothing leaves the machine, so routes that would reach latex-service or an LLM are driven to
the point where the ownership check has already run (see each case's `note`)."""

from __future__ import annotations

from typing import Any

from between_jobs.api import product_events
from between_jobs.api.applications_store import find_application_by_url
from between_jobs.api.extension_answers_store import match_approved_answer

from .harness import Case, Ctx, NotUserScoped, Req, Tenant, seeder

_PDF_BYTES = b"%PDF-1.4 cross-tenant fixture"
_LATEX = b"\\documentclass{article}\\begin{document}x\\end{document}"


# -- seeders -------------------------------------------------------------------------------------


async def _artifact(ctx: Ctx, tenant: Tenant, kind: str) -> dict[str, Any]:
    app = await ctx.need("application", tenant)
    version = await ctx.need("profile_version", tenant)
    row = await ctx.world.artifact_version(
        tenant.user_id, app["id"], version["id"], kind=kind, body=_LATEX
    )
    return {"id": row["id"], "application_id": app["id"], "row": row}


@seeder("app_resume_artifact", tables=("artifact_versions",))
async def seed_resume_artifact(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    return await _artifact(ctx, tenant, "resume")


@seeder("app_cover_letter_artifact", tables=("artifact_versions",))
async def seed_cover_letter_artifact(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    return await _artifact(ctx, tenant, "cover_letter")


@seeder("app_prepared_event", tables=("application_events",))
async def seed_prepared_event(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    """The `application.prepared` event the last /prepare run leaves behind; its payload is
    what prepare-result and extension-payload read back."""
    app = await ctx.need("application", tenant)
    ref = ctx.mark(tenant, ctx.tag("resume-ref"))
    gate_reason = ctx.mark(tenant, ctx.tag("gate-reason"))
    row = (
        await ctx.sb.table("application_events")
        .insert(
            {
                "application_id": app["id"],
                "user_id": tenant.user_id,
                "event_type": "application.prepared",
                "payload": {
                    "resume": {"artifact_id": ref},
                    "cover_letter": None,
                    "gate_reason": gate_reason,
                },
                "actor_type": "user",
                "actor_id": tenant.user_id,
                "idempotency_key": ctx.tag("seed-prepared"),
            }
        )
        .execute()
    ).data[0]
    return {"id": row["id"], "application_id": app["id"], "ref": ref, "gate_reason": gate_reason}


@seeder("app_approved_answer", tables=("approved_answers",))
async def seed_approved_answer(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    """The question text is deliberately NOT marked: B may legitimately type the same words,
    and the case wants to prove that gets B nothing of A's (the answer and the metadata are
    what is private)."""
    question = ctx.tag("are you open to relocation")
    answer = ctx.mark(tenant, ctx.tag("Answer"))
    intent = ctx.mark(tenant, ctx.tag("intent"))
    jurisdiction = ctx.mark(tenant, ctx.tag("juris"))
    row = (
        await ctx.sb.table("approved_answers")
        .insert(
            {
                "user_id": tenant.user_id,
                "normalized_question": question,
                "answer_text": answer,
                "canonical_intent": intent,
                "jurisdiction": jurisdiction,
            }
        )
        .execute()
    ).data[0]
    return {
        "id": row["id"],
        "question": question,
        "answer": answer,
        "intent": intent,
        "jurisdiction": jurisdiction,
    }


# -- list / read / stage (the first three cases) -------------------------------------------------


async def _list(ctx: Ctx) -> Req:
    await ctx.need("application", ctx.a)
    return Req("GET", "/applications")


async def _one(ctx: Ctx) -> Req:
    app = await ctx.need("application", ctx.a)
    return Req("GET", f"/applications/{app['id']}")


async def _stage(ctx: Ctx) -> Req:
    app = await ctx.need("application", ctx.a)
    key = ctx.tag("idem")
    return Req(
        "POST",
        f"/applications/{app['id']}/stage",
        json={"new_status": "applied", "idempotency_key": key},
    )


async def _stage_unchanged(ctx: Ctx) -> None:
    app = await ctx.need("application", ctx.a)
    row = (await ctx.sb.table("applications").select("status").eq("id", app["id"]).execute()).data[
        0
    ]
    assert row["status"] == "saved", "B moved A's application to another stage"


# -- creating an application ---------------------------------------------------------------------


async def _create(ctx: Ctx) -> Req:
    """B names A's job URL. Jobs are shared by canonical URL by design, so B's application
    lands on A's job row, but with B's own snapshot content."""
    job = await ctx.need("job", ctx.a)
    return Req(
        "POST",
        "/applications",
        json={
            "title": ctx.tag("pasted-title"),
            "company_name": ctx.tag("pasted-co"),
            "description_text": ctx.tag("pasted-description"),
            "canonical_url": job["url"],
        },
    )


async def _create_unchanged(ctx: Ctx) -> None:
    """The suite's two users are shared by every case, so look only at the one job."""
    job = await ctx.need("job", ctx.a)
    a = await ctx.need("application", ctx.a)
    mine = (
        await ctx.sb.table("applications")
        .select("id")
        .eq("user_id", ctx.a.user_id)
        .eq("job_id", job["job_id"])
        .execute()
    ).data
    assert [r["id"] for r in mine] == [a["id"]], "B's create added or changed an application of A's"
    row = (await ctx.sb.table("applications").select("*").eq("id", a["id"]).execute()).data[0]
    assert row["active_job_snapshot_id"] == a["row"]["active_job_snapshot_id"]
    assert row["status"] == a["row"]["status"]
    theirs = (
        await ctx.sb.table("applications")
        .select("id")
        .eq("user_id", ctx.b.user_id)
        .eq("job_id", job["job_id"])
        .execute()
    ).data
    assert len(theirs) == 1, "B's create did not land under B"


# -- PDFs and the export checklist ---------------------------------------------------------------

_COMPILE_NOTE = (
    "The seeded artifact is real (row + Storage object), but compiling it needs latex-service, "
    "which the loopback guard refuses (it raises ExternalCallBlocked at the socket, which the "
    "route does not map, so the owner gets 500 INTERNAL_ERROR; verified in the log to be the "
    "blocked compile connection). A 500 (not 404) proves the application, the artifact "
    "version and the Storage download all resolved for A."
)


async def _resume_pdf(ctx: Ctx) -> Req:
    art = await ctx.need("app_resume_artifact", ctx.a)
    return Req("GET", f"/applications/{art['application_id']}/resume.pdf")


async def _cover_pdf(ctx: Ctx) -> Req:
    art = await ctx.need("app_cover_letter_artifact", ctx.a)
    return Req("GET", f"/applications/{art['application_id']}/cover-letter.pdf")


async def _checklist(ctx: Ctx) -> Req:
    art = await ctx.need("app_resume_artifact", ctx.a)
    return Req("GET", f"/applications/{art['application_id']}/export-checklist")


async def _ext_resume_pdf(ctx: Ctx) -> Req:
    art = await ctx.need("app_resume_artifact", ctx.a)
    return Req("GET", f"/extension/{art['application_id']}/resume.pdf")


async def _ext_cover_pdf(ctx: Ctx) -> Req:
    art = await ctx.need("app_cover_letter_artifact", ctx.a)
    return Req("GET", f"/extension/{art['application_id']}/cover-letter.pdf")


# -- prepare and its result ----------------------------------------------------------------------


async def _prepare(ctx: Ctx) -> Req:
    app = await ctx.need("application", ctx.a)
    return Req(
        "POST",
        f"/applications/{app['id']}/prepare",
        json={"idempotency_key": ctx.tag("prepare-idem-0000")},
    )


async def _prepare_unchanged(ctx: Ctx) -> None:
    app = await ctx.need("application", ctx.a)
    events = (
        await ctx.sb.table("application_events")
        .select("id")
        .eq("application_id", app["id"])
        .execute()
    ).data
    assert events == [], "B's prepare wrote an event onto A's application"
    versions = (
        await ctx.sb.table("artifact_versions")
        .select("id")
        .eq("application_id", app["id"])
        .execute()
    ).data
    assert versions == [], "B's prepare wrote an artifact onto A's application"


async def _prepare_result(ctx: Ctx) -> Req:
    event = await ctx.need("app_prepared_event", ctx.a)
    return Req("GET", f"/applications/{event['application_id']}/prepare-result")


async def _extension_payload(ctx: Ctx) -> Req:
    event = await ctx.need("app_prepared_event", ctx.a)
    return Req("GET", f"/applications/{event['application_id']}/extension-payload")


# -- the extension surface -----------------------------------------------------------------------


async def _lookup(ctx: Ctx) -> Req:
    job = await ctx.need("job", ctx.a)
    await ctx.need("application", ctx.a)
    return Req("GET", "/extension/lookup", params={"url": job["url"]})


async def _lookup_control(ctx: Ctx) -> None:
    """B's `{"application_id": null}` is only meaningful if the very same URL does resolve for
    A (the control's 200 alone cannot tell a hit from a miss)."""
    job = await ctx.need("job", ctx.a)
    app = await ctx.need("application", ctx.a)
    found = await find_application_by_url(ctx.sb, ctx.a.user_id, job["url"])
    assert found is not None and found["id"] == app["id"], "the lookup URL does not match A's job"


async def _match(ctx: Ctx) -> Req:
    answer = await ctx.need("app_approved_answer", ctx.a)
    return Req("POST", "/extension/match-answer", json={"normalized_question": answer["question"]})


async def _match_control(ctx: Ctx) -> None:
    answer = await ctx.need("app_approved_answer", ctx.a)
    # jurisdiction-tagged answers are excluded when the caller names none, so ask with A's
    hit = await match_approved_answer(
        ctx.sb,
        ctx.a.user_id,
        normalized_question=answer["question"],
        jurisdiction=answer["jurisdiction"],
    )
    assert hit is not None and hit["answer_text"] == answer["answer"], "A's own match failed"


async def _save(ctx: Ctx) -> Req:
    """B saves an answer to the SAME question text A has an answer for."""
    answer = await ctx.need("app_approved_answer", ctx.a)
    fact = await ctx.need("career_fact", ctx.a)
    return Req(
        "POST",
        "/extension/approved-answers",
        json={
            "normalized_question": answer["question"],
            "answer_text": ctx.tag("their-own-answer"),
            "evidence_fact_ids": [fact["id"]],
        },
    )


async def _save_unchanged(ctx: Ctx) -> None:
    answer = await ctx.need("app_approved_answer", ctx.a)
    question = answer["question"]
    rows = (
        await ctx.sb.table("approved_answers")
        .select("*")
        .eq("user_id", ctx.a.user_id)
        .eq("normalized_question", question)
        .execute()
    ).data
    assert len(rows) == 1, "B's save added or removed a row under A"
    row = rows[0]
    assert row["id"] == answer["id"]
    assert row["answer_text"] == answer["answer"], "B overwrote A's approved answer"
    assert row["canonical_intent"] == answer["intent"]
    assert row["jurisdiction"] == answer["jurisdiction"]
    assert row["evidence_fact_ids"] == []
    mine = (
        await ctx.sb.table("approved_answers")
        .select("id, canonical_intent, jurisdiction")
        .eq("user_id", ctx.b.user_id)
        .eq("normalized_question", question)
        .execute()
    ).data
    assert len(mine) == 1, "B's save did not land under B"
    assert mine[0]["canonical_intent"] is None and mine[0]["jurisdiction"] is None, (
        "B's saved answer inherited A's metadata"
    )


async def _draft(ctx: Ctx) -> Req:
    app = await ctx.need("application", ctx.a)
    return Req(
        "POST",
        "/extension/draft-answer",
        json={"application_id": app["id"], "question_text": "Why do you want to work here?"},
    )


async def _fill_outcome(ctx: Ctx) -> Req:
    """B reports a fill that names A's application."""
    app = await ctx.need("application", ctx.a)
    return Req(
        "POST",
        "/extension/fill-outcome",
        json={
            "ats_type": "lever",
            "application_id": app["id"],
            "fields_attempted": 4,
            "fields_filled": 3,
            "outcome": "partial",
        },
    )


async def _fill_outcome_unchanged(ctx: Ctx) -> None:
    """The refused report recorded nothing: no event of anyone's mentions A's application. (The
    recording is a background task, so wait for any that is still in flight before looking.)"""
    await product_events.flush()
    app = await ctx.need("application", ctx.a)
    rows = (
        await ctx.sb.table("product_events")
        .select("id, user_id")
        .eq("application_id", app["id"])
        .execute()
    ).data
    assert rows == [], f"B's refused fill report left an event behind: {rows}"


CASES = [
    Case("GET /applications", _list, kind="list", foreign_status=200, b_seeds=("application",)),
    Case("GET /applications/{application_id}", _one),
    Case(
        "POST /applications/{application_id}/stage",
        _stage,
        unchanged=_stage_unchanged,
    ),
    Case(
        "POST /applications",
        _create,
        foreign_status=201,
        owner_status=frozenset({201}),
        unchanged=_create_unchanged,
        note="Creates for the caller, so there is no 404 to expect: B's request names A's job "
        "URL (jobs are shared by canonical URL by design) and must neither show A's job "
        "company nor touch A's application.",
    ),
    Case(
        "GET /applications/{application_id}/resume.pdf",
        _resume_pdf,
        owner_status=frozenset({500}),
        note=_COMPILE_NOTE,
    ),
    Case(
        "GET /applications/{application_id}/cover-letter.pdf",
        _cover_pdf,
        owner_status=frozenset({500}),
        note=_COMPILE_NOTE,
    ),
    Case(
        "GET /applications/{application_id}/export-checklist",
        _checklist,
        owner_status=frozenset({500}),
        note=_COMPILE_NOTE,
    ),
    Case(
        "GET /extension/{application_id}/resume.pdf",
        _ext_resume_pdf,
        owner_status=frozenset({500}),
        note=_COMPILE_NOTE,
    ),
    Case(
        "GET /extension/{application_id}/cover-letter.pdf",
        _ext_cover_pdf,
        owner_status=frozenset({500}),
        note=_COMPILE_NOTE,
    ),
    Case(
        "POST /applications/{application_id}/prepare",
        _prepare,
        owner_status=frozenset({409}),
        unchanged=_prepare_unchanged,
        note="A has no active profile, so the owner stops at 409 SETUP_REQUIRED, after the "
        "application ownership check and before any LLM or forge-engines call.",
    ),
    Case(
        "GET /applications/{application_id}/prepare-result",
        _prepare_result,
        foreign_status=200,
        note="The real contract is a 200 {'result': null} for a stranger (the read is scoped "
        "by user_id and does not check the application separately), the same answer as for an "
        "id that does not exist, so it is not an existence oracle.",
    ),
    Case("GET /applications/{application_id}/extension-payload", _extension_payload),
    Case(
        "GET /extension/lookup",
        _lookup,
        foreign_status=200,
        unchanged=_lookup_control,
        note="By URL, not id: a stranger gets {'application_id': null}. The unchanged hook "
        "proves the same URL does resolve to A's application, so the null is not a typo.",
    ),
    Case(
        "POST /extension/match-answer",
        _match,
        foreign_status=200,
        unchanged=_match_control,
        note="A stranger asking for the same question text gets {'answer': null}; the hook "
        "proves A's own match for it (with A's jurisdiction) returns the stored answer.",
    ),
    Case(
        "POST /extension/approved-answers",
        _save,
        foreign_status=201,
        owner_status=frozenset({201}),
        unchanged=_save_unchanged,
        note="Creates for the caller: B saves an answer to the very question text A has an "
        "answer for (and cites A's career fact id as evidence). A's row must be untouched "
        "and B's must land under B.",
    ),
    Case(
        "POST /extension/fill-outcome",
        _fill_outcome,
        owner_status=frozenset({204}),
        unchanged=_fill_outcome_unchanged,
        note="B names A's application in a fill report: the same 404 as for an id that does "
        "not exist, and no event is recorded. The owner's own report is a 204.",
    ),
    Case(
        "POST /extension/draft-answer",
        _draft,
        owner_status=frozenset({409}),
        note="A has no active profile, so the owner stops at 409 SETUP_REQUIRED after the "
        "application ownership and job-snapshot lookups, before the rate limit and any LLM.",
    ),
]


NOT_USER_SCOPED: dict[str, NotUserScoped] = {
    "GET /extension/field-maps/{ats_type}": NotUserScoped(
        "shared",
        "Keyed by ATS type, not by user: the route reads the shared ats_field_maps table (with "
        "the service role) and returns the signed row verbatim. The user id is only the auth "
        "gate; the same ats_type yields byte-identical content for every caller.",
    ),
    "POST /extension/sign-out": NotUserScoped(
        "own-identity",
        "Takes no id and no body: it upserts 'signed out now' for the caller's own user id "
        "only (extension_sign_outs, one row per user). It is also unsafe to run as a case, "
        "because signing a tenant out invalidates that tenant's token for every later case.",
    ),
    "POST /applications/from-url": NotUserScoped(
        "own-identity",
        "Takes only a URL and creates an application for the caller; it reads no user's "
        "data. It shares create_job_from_paste and create_application with POST "
        "/applications, which has the cross-tenant case. A meaningful control needs either "
        "a registry row (a shared table the harness has no way to clean up) or a Firecrawl "
        "call the loopback guard refuses.",
    ),
}
