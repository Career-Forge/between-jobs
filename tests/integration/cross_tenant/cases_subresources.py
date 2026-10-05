"""Cross-tenant cases for everything hanging off one application: company intel, contacts (and
their outreach drafts, enrichment, LinkedIn lookup and Gmail push), hiring-signal saves and
search, interview practice, the positioning brief and warm-path events.

Two things shape these cases:

* Several tables here have no `user_id` (claims, candidates, evidence, session questions,
  warm-path events): their ownership is enforced through the parent run, session or
  application, which is where a bug would hide. So a case that carries a child id (a candidate,
  a session) also tries that id as B under B's OWN application: a stranger must not get further
  by pairing A's child with an application of their own.
* Nothing may leave the machine, so every POST that would call a provider is stopped by a
  missing credential (409 `SETUP_REQUIRED`) for the owner. The ownership lookup always runs
  before the credential lookup, so a 404 for B and a 409 for A prove both halves."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from .harness import Case, Ctx, Req, Tenant, seeder
from .seeds_core import (
    seed_application as _seed_application,  # noqa: F401  (registers "application")
)

_NO_CREDENTIAL = frozenset({409})

# -- seeders ------------------------------------------------------------------------------------


@seeder("sub_company_intel_run", tables=("company_intel_runs", "company_intel_claims"))
async def seed_company_intel_run(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    app = await ctx.need("application", tenant)
    company = ctx.mark(tenant, ctx.tag("IntelCo"))
    run = (
        await ctx.sb.table("company_intel_runs")
        .insert({"user_id": tenant.user_id, "application_id": app["id"], "company_name": company})
        .execute()
    ).data[0]
    claim = ctx.mark(tenant, ctx.tag("Claim"))
    await (
        ctx.sb.table("company_intel_claims")
        .insert(
            {
                "run_id": run["id"],
                "category": "culture",
                "claim_text": claim,
                "source_url": f"https://example.com/{ctx.tag('src')}",
                "confidence": "high",
            }
        )
        .execute()
    )
    return {"run_id": run["id"], "company": company, "claim": claim, "application_id": app["id"]}


@seeder(
    "sub_contact",
    tables=("contact_research_runs", "contact_candidates", "contact_candidate_evidence"),
)
async def seed_contact(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    app = await ctx.need("application", tenant)
    run = (
        await ctx.sb.table("contact_research_runs")
        .insert(
            {
                "user_id": tenant.user_id,
                "application_id": app["id"],
                "company_name": ctx.mark(tenant, ctx.tag("ContactCo")),
            }
        )
        .execute()
    ).data[0]
    person = ctx.mark(tenant, ctx.tag("Person"))
    email = ctx.mark(tenant, f"{ctx.tag('mail')}@example.com")
    cand = (
        await ctx.sb.table("contact_candidates")
        .insert(
            {
                "run_id": run["id"],
                "person_name": person,
                "claimed_title": ctx.mark(tenant, ctx.tag("Title")),
                "company": ctx.mark(tenant, ctx.tag("EmployerCo")),
                "persona": "hiring_manager",
                "relevance_reason": ctx.mark(tenant, ctx.tag("Reason")),
                "priority_score": 5,
                "enriched_email": email,
                "enriched_email_status": "verified",
                "enrichment_provider": "seed",
            }
        )
        .execute()
    ).data[0]
    snippet = ctx.mark(tenant, ctx.tag("Snippet"))
    await (
        ctx.sb.table("contact_candidate_evidence")
        .insert(
            {
                "candidate_id": cand["id"],
                "source_url": f"https://example.com/{ctx.tag('ev')}",
                "source_title": ctx.mark(tenant, ctx.tag("EvTitle")),
                "source_snippet": snippet,
                "observed_at": "2026-01-01T00:00:00+00:00",
                "evidence_kind": "post",
                "confidence": "high",
            }
        )
        .execute()
    )
    return {
        "application_id": app["id"],
        "run_id": run["id"],
        "candidate_id": cand["id"],
        "person": person,
        "email": email,
    }


@seeder("sub_outreach_draft", tables=("outreach_drafts",))
async def seed_outreach_draft(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    contact = await ctx.need("sub_contact", tenant)
    subject = ctx.mark(tenant, ctx.tag("Subject"))
    row = (
        await ctx.sb.table("outreach_drafts")
        .insert(
            {
                "user_id": tenant.user_id,
                "candidate_id": contact["candidate_id"],
                "subject": subject,
                "email_body": ctx.mark(tenant, ctx.tag("Body")),
                "linkedin_message": ctx.mark(tenant, ctx.tag("Li")),
                "follow_up_message": ctx.mark(tenant, ctx.tag("Follow")),
            }
        )
        .execute()
    ).data[0]
    return {"id": row["id"], "subject": subject}


@seeder("sub_interview_session", tables=("interview_sessions", "interview_session_questions"))
async def seed_interview_session(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    app = await ctx.need("application", tenant)
    session = (
        await ctx.sb.table("interview_sessions")
        .insert(
            {
                "user_id": tenant.user_id,
                "application_id": app["id"],
                "company_name": ctx.mark(tenant, ctx.tag("PracticeCo")),
                "resume_evidence": ctx.mark(tenant, ctx.tag("Evidence")),
            }
        )
        .execute()
    ).data[0]
    question = ctx.mark(tenant, ctx.tag("Question"))
    q = (
        await ctx.sb.table("interview_session_questions")
        .insert(
            {
                "session_id": session["id"],
                "ordinal": 1,
                "question_text": question,
                "question_type": "behavioral",
                "target_skill": ctx.mark(tenant, ctx.tag("Skill")),
            }
        )
        .execute()
    ).data[0]
    return {
        "application_id": app["id"],
        "session_id": session["id"],
        "question_id": q["id"],
        "question": question,
    }


@seeder("sub_positioning_brief", tables=("positioning_briefs",))
async def seed_positioning_brief(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    app = await ctx.need("application", tenant)
    lead = ctx.mark(tenant, ctx.tag("Lead"))
    await (
        ctx.sb.table("positioning_briefs")
        .insert(
            {
                "user_id": tenant.user_id,
                "application_id": app["id"],
                "lead_with": lead,
                "lead_with_citation": ctx.mark(tenant, ctx.tag("Cite")),
                "gap_that_matters": ctx.mark(tenant, ctx.tag("Gap")),
                "gap_citation": ctx.mark(tenant, ctx.tag("GapCite")),
                "recommended_project": ctx.mark(tenant, ctx.tag("Project")),
            }
        )
        .execute()
    )
    return {"application_id": app["id"], "lead": lead}


@seeder("sub_warm_path", tables=("warm_path_runs", "warm_path_events"))
async def seed_warm_path(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    app = await ctx.need("application", tenant)
    run = (
        await ctx.sb.table("warm_path_runs")
        .insert(
            {
                "user_id": tenant.user_id,
                "application_id": app["id"],
                "company_name": ctx.mark(tenant, ctx.tag("WarmCo")),
            }
        )
        .execute()
    ).data[0]
    event = ctx.mark(tenant, ctx.tag("Event"))
    await (
        ctx.sb.table("warm_path_events")
        .insert(
            {
                "run_id": run["id"],
                "event_name": event,
                "event_url": f"https://example.com/{ctx.tag('ev')}",
                "certainty": "confirmed",
                "source_title": ctx.mark(tenant, ctx.tag("SrcTitle")),
                "source_snippet": ctx.mark(tenant, ctx.tag("SrcSnip")),
            }
        )
        .execute()
    )
    return {"application_id": app["id"], "event": event}


@seeder("sub_hiring_save", tables=("hiring_signal_saves",))
async def seed_hiring_save(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    app = await ctx.need("application", tenant)
    activity_id = ctx.mark(tenant, str(uuid.uuid4().int % 10**17 + 10**17))
    await (
        ctx.sb.table("hiring_signal_saves")
        .insert(
            {
                "user_id": tenant.user_id,
                "application_id": app["id"],
                "url": f"https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}",
                "activity_id": activity_id,
                "discovered_via_query": ctx.mark(tenant, ctx.tag("Label")),
            }
        )
        .execute()
    )
    return {"application_id": app["id"], "activity_id": activity_id}


# -- helpers ------------------------------------------------------------------------------------


async def _send(tenant: Tenant, method: str, url: str, **kwargs: Any) -> httpx.Response:
    """One more request to the already-running app, for the extra checks a Case cannot express
    (a mismatched application/child pair, the owner reading her own data back)."""
    from between_jobs.api.app import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        return await http.request(method, url, headers=tenant.headers, **kwargs)


async def _b_with_own_application(ctx: Ctx, method: str, template: str, **kwargs: Any) -> None:
    """B asks for A's child id under B's OWN application: still a stranger's 404, no leak."""
    own = await ctx.need("application", ctx.b)
    response = await _send(ctx.b, method, template.format(app=own["id"]), **kwargs)
    assert response.status_code == 404, (
        f"{method} {template} with A's child under B's own application -> "
        f"{response.status_code} {response.text[:300]!r}"
    )
    leaked = [m for m in ctx.pair.marks["A"] if m in response.text]
    assert not leaked, f"mismatched pair showed B something of A's: {leaked[:3]}"


async def _owner_sees(ctx: Ctx, url: str, mark: str) -> None:
    """The control for a GET whose stranger answer is an empty 200: the owner really does get
    the seeded data from the same url, so the emptiness for B is not a missing seed."""
    response = await _send(ctx.a, "GET", url)
    assert response.status_code == 200 and mark in response.text, (
        f"GET {url} as A (control) did not show the seeded row: {response.status_code}"
    )


async def _count(ctx: Ctx, table: str, **eq: Any) -> int:
    query = ctx.sb.table(table).select("id")
    for column, value in eq.items():
        query = query.eq(column, value)
    return len((await query.execute()).data)


def _nothing_created(table: str, per: str = "application_id") -> Any:
    """`unchanged` for a POST that makes a row: A's application holds none (B's attempt made
    nothing), and neither does B."""

    async def check(ctx: Ctx) -> None:
        app = await ctx.need("application", ctx.a)
        assert await _count(ctx, table, **{per: app["id"]}) == 0, f"B's attempt created {table}"
        assert await _count(ctx, table, user_id=ctx.b.user_id) == 0, f"{table} row for B"

    return check


# -- company intel ------------------------------------------------------------------------------


async def _intel_get(ctx: Ctx) -> Req:
    run = await ctx.need("sub_company_intel_run", ctx.a)
    return Req("GET", f"/applications/{run['application_id']}/company-intel")


async def _intel_get_unchanged(ctx: Ctx) -> None:
    run = await ctx.need("sub_company_intel_run", ctx.a)
    await _owner_sees(ctx, f"/applications/{run['application_id']}/company-intel", run["claim"])


async def _intel_post(ctx: Ctx) -> Req:
    app = await ctx.need("application", ctx.a)
    return Req("POST", f"/applications/{app['id']}/company-intel")


# -- contacts -----------------------------------------------------------------------------------


async def _contacts_get(ctx: Ctx) -> Req:
    c = await ctx.need("sub_contact", ctx.a)
    return Req("GET", f"/applications/{c['application_id']}/contacts")


async def _contacts_get_unchanged(ctx: Ctx) -> None:
    c = await ctx.need("sub_contact", ctx.a)
    await _owner_sees(ctx, f"/applications/{c['application_id']}/contacts", c["person"])


async def _contacts_post(ctx: Ctx) -> Req:
    app = await ctx.need("application", ctx.a)
    return Req("POST", f"/applications/{app['id']}/contacts")


async def _candidate_req(ctx: Ctx, method: str, suffix: str) -> Req:
    c = await ctx.need("sub_contact", ctx.a)
    return Req(method, f"/applications/{c['application_id']}/contacts/{c['candidate_id']}/{suffix}")


async def _candidate_unchanged(ctx: Ctx, suffix: str, method: str) -> None:
    c = await ctx.need("sub_contact", ctx.a)
    row = (
        await ctx.sb.table("contact_candidates").select("*").eq("id", c["candidate_id"]).execute()
    ).data[0]
    assert row["enriched_email"] == c["email"] and row["enrichment_provider"] == "seed", (
        "B changed A's candidate enrichment"
    )
    assert row["discovered_linkedin_url"] is None, "B changed A's candidate LinkedIn lookup"
    await _b_with_own_application(
        ctx, method, "/applications/{app}/contacts/" + c["candidate_id"] + "/" + suffix
    )


async def _enrich(ctx: Ctx) -> Req:
    return await _candidate_req(ctx, "POST", "enrich")


async def _enrich_unchanged(ctx: Ctx) -> None:
    await _candidate_unchanged(ctx, "enrich", "POST")


async def _linkedin(ctx: Ctx) -> Req:
    return await _candidate_req(ctx, "POST", "find-linkedin")


async def _linkedin_unchanged(ctx: Ctx) -> None:
    await _candidate_unchanged(ctx, "find-linkedin", "POST")


async def _draft_get(ctx: Ctx) -> Req:
    await ctx.need("sub_outreach_draft", ctx.a)
    return await _candidate_req(ctx, "GET", "draft-outreach")


async def _draft_get_unchanged(ctx: Ctx) -> None:
    c = await ctx.need("sub_contact", ctx.a)
    d = await ctx.need("sub_outreach_draft", ctx.a)
    await _owner_sees(
        ctx,
        f"/applications/{c['application_id']}/contacts/{c['candidate_id']}/draft-outreach",
        d["subject"],
    )
    await _b_with_own_application(
        ctx, "GET", "/applications/{app}/contacts/" + c["candidate_id"] + "/draft-outreach"
    )


async def _draft_post(ctx: Ctx) -> Req:
    return await _candidate_req(ctx, "POST", "draft-outreach")


async def _draft_post_unchanged(ctx: Ctx) -> None:
    c = await ctx.need("sub_contact", ctx.a)
    assert await _count(ctx, "outreach_drafts", candidate_id=c["candidate_id"]) == 0, (
        "B's attempt created an outreach draft for A's candidate"
    )
    await _b_with_own_application(
        ctx, "POST", "/applications/{app}/contacts/" + c["candidate_id"] + "/draft-outreach"
    )


async def _push(ctx: Ctx) -> Req:
    await ctx.need("sub_outreach_draft", ctx.a)
    return await _candidate_req(ctx, "POST", "push-to-gmail")


async def _push_unchanged(ctx: Ctx) -> None:
    c = await ctx.need("sub_contact", ctx.a)
    d = await ctx.need("sub_outreach_draft", ctx.a)
    row = (await ctx.sb.table("outreach_drafts").select("*").eq("id", d["id"]).execute()).data[0]
    assert row["gmail_draft_id"] is None and row["pushed_to_gmail_at"] is None, (
        "B pushed A's draft to Gmail"
    )
    await _b_with_own_application(
        ctx, "POST", "/applications/{app}/contacts/" + c["candidate_id"] + "/push-to-gmail"
    )


# -- hiring signals -----------------------------------------------------------------------------


async def _saves_get(ctx: Ctx) -> Req:
    s = await ctx.need("sub_hiring_save", ctx.a)
    return Req("GET", f"/applications/{s['application_id']}/hiring-signals/saves")


async def _saves_get_unchanged(ctx: Ctx) -> None:
    s = await ctx.need("sub_hiring_save", ctx.a)
    await _owner_sees(
        ctx, f"/applications/{s['application_id']}/hiring-signals/saves", s["activity_id"]
    )


async def _saves_post(ctx: Ctx) -> Req:
    app = await ctx.need("application", ctx.a)
    activity = str(uuid.uuid4().int % 10**17 + 10**17)
    ctx.state["activity_id"] = activity
    return Req(
        "POST",
        f"/applications/{app['id']}/hiring-signals/saves",
        json={"activity_id": activity},
    )


async def _saves_post_unchanged(ctx: Ctx) -> None:
    app = await ctx.need("application", ctx.a)
    assert await _count(ctx, "hiring_signal_saves", application_id=app["id"]) == 0, (
        "B's attempt saved a post under A's application"
    )
    assert await _count(ctx, "hiring_signal_saves", activity_id=ctx.state["activity_id"]) == 0, (
        "B's attempt left a save behind"
    )


async def _hiring_search(ctx: Ctx) -> Req:
    app = await ctx.need("application", ctx.a)
    # The owner's control stops at "no search key" (409) before any provider call. Another case
    # may have saved A a key, so make sure she has none.
    await (
        ctx.sb.table("provider_credentials")
        .delete()
        .eq("user_id", ctx.a.user_id)
        .eq("service", "search")
        .execute()
    )
    return Req("POST", f"/applications/{app['id']}/hiring-signals/search")


# -- interview practice -------------------------------------------------------------------------


async def _sessions_list(ctx: Ctx) -> Req:
    s = await ctx.need("sub_interview_session", ctx.a)
    return Req("GET", f"/applications/{s['application_id']}/interview-practice/sessions")


async def _sessions_list_unchanged(ctx: Ctx) -> None:
    s = await ctx.need("sub_interview_session", ctx.a)
    url = f"/applications/{s['application_id']}/interview-practice/sessions"
    response = await _send(ctx.a, "GET", url)
    assert response.status_code == 200 and s["session_id"] in response.text, (
        "the owner's session list does not show her seeded session"
    )


async def _sessions_post(ctx: Ctx) -> Req:
    app = await ctx.need("application", ctx.a)
    return Req("POST", f"/applications/{app['id']}/interview-practice/sessions")


async def _session_get(ctx: Ctx) -> Req:
    s = await ctx.need("sub_interview_session", ctx.a)
    return Req(
        "GET", f"/applications/{s['application_id']}/interview-practice/sessions/{s['session_id']}"
    )


async def _session_get_unchanged(ctx: Ctx) -> None:
    s = await ctx.need("sub_interview_session", ctx.a)
    await _b_with_own_application(
        ctx, "GET", "/applications/{app}/interview-practice/sessions/" + s["session_id"]
    )
    # A's own session under another application of A's is a 404 too (the route pairs them).
    url = f"https://example.com/{ctx.tag('job2')}"
    job = (
        await ctx.sb.table("jobs")
        .insert({"company_name": ctx.tag("Co2"), "canonical_url": url})
        .execute()
    ).data[0]
    ctx.world.job_ids.append(job["id"])
    snap = (
        await ctx.sb.table("job_snapshots")
        .insert(
            {
                "job_id": job["id"],
                "source_url": url,
                "title": ctx.tag("T2"),
                "company_name": ctx.tag("Co2"),
                "description_text": ctx.tag("D2"),
                "structured_json": {},
                "content_hash": ctx.tag("snap"),
                "fetched_at": "2026-01-01T00:00:00+00:00",
                "source_kind": "manual",
            }
        )
        .execute()
    ).data[0]
    other = await ctx.world.application(ctx.a.user_id, (job["id"], snap["id"]))
    response = await _send(
        ctx.a,
        "GET",
        f"/applications/{other['id']}/interview-practice/sessions/{s['session_id']}",
    )
    assert response.status_code == 404, (
        f"mismatched application/session pair -> {response.status_code}"
    )


async def _answer(ctx: Ctx) -> Req:
    s = await ctx.need("sub_interview_session", ctx.a)
    return Req(
        "POST",
        f"/applications/{s['application_id']}/interview-practice/sessions/{s['session_id']}/answers",
        json={"answer_text": "an answer"},
    )


async def _answer_unchanged(ctx: Ctx) -> None:
    s = await ctx.need("sub_interview_session", ctx.a)
    row = (
        await ctx.sb.table("interview_session_questions")
        .select("*")
        .eq("id", s["question_id"])
        .execute()
    ).data[0]
    assert row["answer_text"] is None and row["answered_at"] is None, "B answered A's question"
    status = (
        await ctx.sb.table("interview_sessions")
        .select("status")
        .eq("id", s["session_id"])
        .execute()
    ).data[0]["status"]
    assert status == "in_progress", "B completed A's session"
    await _b_with_own_application(
        ctx,
        "POST",
        "/applications/{app}/interview-practice/sessions/" + s["session_id"] + "/answers",
        json={"answer_text": "an answer"},
    )


# -- positioning brief and warm-path events -----------------------------------------------------


async def _brief_get(ctx: Ctx) -> Req:
    b = await ctx.need("sub_positioning_brief", ctx.a)
    return Req("GET", f"/applications/{b['application_id']}/positioning-brief")


async def _brief_get_unchanged(ctx: Ctx) -> None:
    b = await ctx.need("sub_positioning_brief", ctx.a)
    await _owner_sees(ctx, f"/applications/{b['application_id']}/positioning-brief", b["lead"])


async def _brief_post(ctx: Ctx) -> Req:
    app = await ctx.need("application", ctx.a)
    return Req("POST", f"/applications/{app['id']}/positioning-brief")


async def _warm_get(ctx: Ctx) -> Req:
    w = await ctx.need("sub_warm_path", ctx.a)
    return Req("GET", f"/applications/{w['application_id']}/warm-path-events")


async def _warm_get_unchanged(ctx: Ctx) -> None:
    w = await ctx.need("sub_warm_path", ctx.a)
    await _owner_sees(ctx, f"/applications/{w['application_id']}/warm-path-events", w["event"])


async def _warm_post(ctx: Ctx) -> Req:
    app = await ctx.need("application", ctx.a)
    return Req("POST", f"/applications/{app['id']}/warm-path-events")


_GET_NOTE = (
    "The route filters by (user, application) and answers an empty 200 for an application that "
    "is not the caller's, never a 404, so the stranger answer is 200 with none of A's rows; "
    "the owner's real read of the same url is checked in `unchanged`."
)
_POST_NOTE = (
    "The ownership lookup runs before the credential lookup, so B gets 404 and the owner gets "
    "409 SETUP_REQUIRED (no credential is saved, so nothing reaches a provider)."
)

CASES = [
    Case(
        "GET /applications/{application_id}/company-intel",
        _intel_get,
        foreign_status=200,
        unchanged=_intel_get_unchanged,
        note=_GET_NOTE,
    ),
    Case(
        "POST /applications/{application_id}/company-intel",
        _intel_post,
        owner_status=_NO_CREDENTIAL,
        unchanged=_nothing_created("company_intel_runs"),
        note=_POST_NOTE,
    ),
    Case(
        "GET /applications/{application_id}/contacts",
        _contacts_get,
        foreign_status=200,
        unchanged=_contacts_get_unchanged,
        note=_GET_NOTE,
    ),
    Case(
        "POST /applications/{application_id}/contacts",
        _contacts_post,
        owner_status=_NO_CREDENTIAL,
        unchanged=_nothing_created("contact_research_runs"),
        note=_POST_NOTE,
    ),
    Case(
        "GET /applications/{application_id}/contacts/{candidate_id}/draft-outreach",
        _draft_get,
        unchanged=_draft_get_unchanged,
    ),
    Case(
        "POST /applications/{application_id}/contacts/{candidate_id}/draft-outreach",
        _draft_post,
        owner_status=_NO_CREDENTIAL,
        unchanged=_draft_post_unchanged,
        note=_POST_NOTE,
    ),
    Case(
        "POST /applications/{application_id}/contacts/{candidate_id}/enrich",
        _enrich,
        owner_status=_NO_CREDENTIAL,
        unchanged=_enrich_unchanged,
        note="No Apollo/Hunter key saved: the owner stops at 409 before any provider call.",
    ),
    Case(
        "POST /applications/{application_id}/contacts/{candidate_id}/find-linkedin",
        _linkedin,
        owner_status=_NO_CREDENTIAL,
        unchanged=_linkedin_unchanged,
        note="No Exa key saved: the owner stops at 409 before any provider call.",
    ),
    Case(
        "POST /applications/{application_id}/contacts/{candidate_id}/push-to-gmail",
        _push,
        owner_status=_NO_CREDENTIAL,
        unchanged=_push_unchanged,
        note="The seeded candidate has an email and a draft, so the owner reaches the Gmail "
        "credential lookup and stops at 409 (Gmail not connected) before any Google call.",
    ),
    Case(
        "GET /applications/{application_id}/hiring-signals/saves",
        _saves_get,
        unchanged=_saves_get_unchanged,
    ),
    Case(
        "POST /applications/{application_id}/hiring-signals/saves",
        _saves_post,
        owner_status=frozenset({201}),
        unchanged=_saves_post_unchanged,
    ),
    Case(
        "POST /applications/{application_id}/hiring-signals/search",
        _hiring_search,
        owner_status=_NO_CREDENTIAL,
        note="No search key saved: the owner stops at 409 after the application lookup.",
    ),
    Case(
        "GET /applications/{application_id}/interview-practice/sessions",
        _sessions_list,
        foreign_status=200,
        unchanged=_sessions_list_unchanged,
        note=_GET_NOTE,
    ),
    Case(
        "POST /applications/{application_id}/interview-practice/sessions",
        _sessions_post,
        owner_status=_NO_CREDENTIAL,
        unchanged=_nothing_created("interview_sessions"),
        note=_POST_NOTE,
    ),
    Case(
        "GET /applications/{application_id}/interview-practice/sessions/{session_id}",
        _session_get,
        unchanged=_session_get_unchanged,
    ),
    Case(
        "POST /applications/{application_id}/interview-practice/sessions/{session_id}/answers",
        _answer,
        owner_status=_NO_CREDENTIAL,
        unchanged=_answer_unchanged,
        note="The session and its unanswered question are seeded; with no LLM credential the "
        "owner stops at 409 before scoring.",
    ),
    Case(
        "GET /applications/{application_id}/positioning-brief",
        _brief_get,
        foreign_status=200,
        unchanged=_brief_get_unchanged,
        note=_GET_NOTE,
    ),
    Case(
        "POST /applications/{application_id}/positioning-brief",
        _brief_post,
        owner_status=_NO_CREDENTIAL,
        unchanged=_nothing_created("positioning_briefs"),
        note="The owner has no active profile, so she stops at 409 before any LLM call.",
    ),
    Case(
        "GET /applications/{application_id}/warm-path-events",
        _warm_get,
        foreign_status=200,
        unchanged=_warm_get_unchanged,
        note=_GET_NOTE,
    ),
    Case(
        "POST /applications/{application_id}/warm-path-events",
        _warm_post,
        owner_status=_NO_CREDENTIAL,
        unchanged=_nothing_created("warm_path_runs"),
        note=_POST_NOTE,
    ),
]

NOT_USER_SCOPED: dict[str, Any] = {}
