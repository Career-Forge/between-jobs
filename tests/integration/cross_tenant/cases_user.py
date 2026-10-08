"""Cross-tenant cases for the caller-scoped account routes: credentials, saved job searches,
hiring-signal saves and searches, the Today feed (including status proposals) and
`/discover/track`.

Seeder kinds are prefixed `u_` so they cannot collide with another module's."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from between_jobs.api.hiring_signal_saves_store import canonical_post_url
from between_jobs.api.provider_credentials_store import save_credential

from .harness import Case, Ctx, NotUserScoped, Req, Tenant, seeder

_CRED_SERVICE = "search"
_CRED_PROVIDER = "serper"


# -- seeders -----------------------------------------------------------------------------------


@seeder("u_credential", tables=("provider_credentials",))
async def seed_credential(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    """A BYOK key through the real encrypt RPC (a throwaway value). Both tenants get the same
    (service, provider): that is exactly what makes `DELETE /credentials/{service}/{provider}`
    a meaningful isolation case."""
    model = ctx.mark(tenant, ctx.tag("model"))
    secret = ctx.mark(tenant, ctx.tag("throwaway-secret"))
    row = await save_credential(
        ctx.sb,
        tenant.user_id,
        service=_CRED_SERVICE,
        provider=_CRED_PROVIDER,
        secret=secret,
        model=model,
        is_validated=True,
    )
    return {"id": row["id"], "model": model, "secret": secret}


@seeder("u_saved_search", tables=("saved_searches",))
async def seed_saved_search(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    query = ctx.mark(tenant, ctx.tag("query"))
    company = ctx.mark(tenant, ctx.tag("Company"))
    row = (
        await ctx.sb.table("saved_searches")
        .insert(
            {
                "user_id": tenant.user_id,
                "query": query,
                "location": None,
                "companies": [company],
                "remote_only": False,
            }
        )
        .execute()
    ).data[0]
    return {"id": row["id"], "query": query, "company": company}


@seeder("u_hiring_save", tables=("hiring_signal_saves",))
async def seed_hiring_save(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    """A standalone save (no application): the kind `/hiring-signals/saves` lists."""
    activity_id = ctx.mark(tenant, f"7{uuid.uuid4().int % 10**18:018d}")
    row = (
        await ctx.sb.table("hiring_signal_saves")
        .insert(
            {
                "user_id": tenant.user_id,
                "application_id": None,
                "url": canonical_post_url(activity_id),
                "activity_id": activity_id,
            }
        )
        .execute()
    ).data[0]
    return {"id": row["id"], "activity_id": activity_id}


@seeder("u_hiring_search", tables=("hiring_signal_searches",))
async def seed_hiring_search(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    query = ctx.mark(tenant, ctx.tag("Role"))
    location = ctx.mark(tenant, ctx.tag("Metro"))
    row = (
        await ctx.sb.table("hiring_signal_searches")
        .insert({"user_id": tenant.user_id, "query": query, "location": location})
        .execute()
    ).data[0]
    return {"id": row["id"], "query": query, "location": location}


async def _outbox_event(ctx: Ctx, tenant: Tenant, event_type: str) -> str:
    row = (
        await ctx.sb.table("event_outbox")
        .insert(
            {
                "user_id": tenant.user_id,
                "aggregate_type": "application",
                "aggregate_id": str(uuid.uuid4()),
                "event_type": event_type,
                "event_version": 1,
                "payload": {},
                "idempotency_key": ctx.tag("u-idem"),
                "published_at": datetime.now(UTC).isoformat(),
            }
        )
        .execute()
    ).data[0]
    return str(row["id"])


@seeder("u_today_item", tables=("event_outbox", "today_items"))
async def seed_today_item(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    headline = ctx.mark(tenant, ctx.tag("Headline"))
    detail = ctx.mark(tenant, ctx.tag("Detail"))
    event_id = await _outbox_event(ctx, tenant, "application.stage_changed")
    row = (
        await ctx.sb.table("today_items")
        .insert(
            {
                "user_id": tenant.user_id,
                "application_id": None,
                "kind": "stage_changed",
                "headline": headline,
                "detail": detail,
                "source_outbox_event_id": event_id,
            }
        )
        .execute()
    ).data[0]
    return {"id": row["id"], "headline": headline, "detail": detail}


@seeder(
    "u_status_proposal",
    tables=(
        "applications",
        "contact_research_runs",
        "contact_candidates",
        "outreach_drafts",
        "application_status_proposals",
        "event_outbox",
        "today_items",
        "today_item_status_proposals",
    ),
)
async def seed_status_proposal(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    """A pending 'interview.requested' proposal and the Today item that surfaces it, the way
    the Gmail reply checker leaves them: application -> research run -> candidate -> outreach
    draft -> proposal -> Today item (through the real atomic function)."""
    app = await ctx.need("application", tenant)
    company = ctx.mark(tenant, ctx.tag("ProposalCo"))
    run = (
        await ctx.sb.table("contact_research_runs")
        .insert({"user_id": tenant.user_id, "application_id": app["id"], "company_name": company})
        .execute()
    ).data[0]
    candidate = (
        await ctx.sb.table("contact_candidates")
        .insert(
            {
                "run_id": run["id"],
                "person_name": ctx.mark(tenant, ctx.tag("Person")),
                "company": company,
                "persona": "recruiter",
                "relevance_reason": "seeded",
                "priority_score": 1,
            }
        )
        .execute()
    ).data[0]
    draft = (
        await ctx.sb.table("outreach_drafts")
        .insert(
            {
                "user_id": tenant.user_id,
                "candidate_id": candidate["id"],
                "subject": "s",
                "email_body": "e",
                "linkedin_message": "l",
                "follow_up_message": "f",
            }
        )
        .execute()
    ).data[0]
    proposal = (
        await ctx.sb.table("application_status_proposals")
        .insert(
            {
                "user_id": tenant.user_id,
                "application_id": app["id"],
                "outreach_draft_id": draft["id"],
                "proposed_type": "interview.requested",
                "confidence": 0.5,
                "source_gmail_message_id": ctx.tag("msg"),
            }
        )
        .execute()
    ).data[0]
    headline = ctx.mark(tenant, ctx.tag("ProposalHeadline"))
    event_id = await _outbox_event(ctx, tenant, "gmail_reply.status_proposed")
    item = (
        await ctx.sb.rpc(
            "insert_status_proposal_today_item",
            {
                "p_user_id": tenant.user_id,
                "p_application_id": app["id"],
                "p_headline": headline,
                "p_detail": ctx.mark(tenant, ctx.tag("ProposalDetail")),
                "p_source_outbox_event_id": event_id,
                "p_application_status_proposal_id": proposal["id"],
            },
        ).execute()
    ).data
    if isinstance(item, list):
        item = item[0]
    return {
        "item_id": item["id"],
        "proposal_id": proposal["id"],
        "application_id": app["id"],
        "headline": headline,
    }


# -- credentials -------------------------------------------------------------------------------


async def _credentials_list(ctx: Ctx) -> Req:
    await ctx.need("u_credential", ctx.a)
    return Req("GET", "/credentials")


async def _credentials_delete(ctx: Ctx) -> Req:
    await ctx.need("u_credential", ctx.a)
    await ctx.need("u_credential", ctx.b)
    return Req("DELETE", f"/credentials/{_CRED_SERVICE}/{_CRED_PROVIDER}")


async def _credentials_delete_unchanged(ctx: Ctx) -> None:
    for tenant, expected in ((ctx.a, 1), (ctx.b, 0)):
        rows = (
            await ctx.sb.table("provider_credentials")
            .select("id")
            .eq("user_id", tenant.user_id)
            .eq("service", _CRED_SERVICE)
            .eq("provider", _CRED_PROVIDER)
            .execute()
        ).data
        who = "A's key must survive B's delete" if tenant is ctx.a else "B's own key must be gone"
        assert len(rows) == expected, who


# -- saved searches (job finder) ---------------------------------------------------------------


async def _saved_searches_list(ctx: Ctx) -> Req:
    await ctx.need("u_saved_search", ctx.a)
    return Req("GET", "/saved-searches")


async def _saved_search_patch(ctx: Ctx) -> Req:
    s = await ctx.need("u_saved_search", ctx.a)
    return Req("PATCH", f"/saved-searches/{s['id']}", json={"is_active": False})


async def _saved_search_patch_unchanged(ctx: Ctx) -> None:
    s = await ctx.need("u_saved_search", ctx.a)
    row = (await ctx.sb.table("saved_searches").select("*").eq("id", s["id"]).execute()).data[0]
    assert row["is_active"] is True, "B paused A's saved search"
    assert row["query"] == s["query"]


async def _saved_search_delete(ctx: Ctx) -> Req:
    s = await ctx.need("u_saved_search", ctx.a)
    return Req("DELETE", f"/saved-searches/{s['id']}")


async def _saved_search_delete_unchanged(ctx: Ctx) -> None:
    s = await ctx.need("u_saved_search", ctx.a)
    rows = (await ctx.sb.table("saved_searches").select("id").eq("id", s["id"]).execute()).data
    assert len(rows) == 1, "B deleted A's saved search"


# -- hiring signals ----------------------------------------------------------------------------


async def _hs_saves_list(ctx: Ctx) -> Req:
    await ctx.need("u_hiring_save", ctx.a)
    return Req("GET", "/hiring-signals/saves")


async def _hs_save_delete(ctx: Ctx) -> Req:
    s = await ctx.need("u_hiring_save", ctx.a)
    return Req("DELETE", f"/hiring-signals/saves/{s['id']}")


async def _hs_save_delete_unchanged(ctx: Ctx) -> None:
    s = await ctx.need("u_hiring_save", ctx.a)
    rows = (await ctx.sb.table("hiring_signal_saves").select("id").eq("id", s["id"]).execute()).data
    assert len(rows) == 1, "B deleted A's saved post"


async def _hs_searches_list(ctx: Ctx) -> Req:
    await ctx.need("u_hiring_search", ctx.a)
    return Req("GET", "/hiring-signals/searches")


async def _hs_search_delete(ctx: Ctx) -> Req:
    s = await ctx.need("u_hiring_search", ctx.a)
    return Req("DELETE", f"/hiring-signals/searches/{s['id']}")


async def _hs_search_delete_unchanged(ctx: Ctx) -> None:
    s = await ctx.need("u_hiring_search", ctx.a)
    rows = (
        await ctx.sb.table("hiring_signal_searches").select("id").eq("id", s["id"]).execute()
    ).data
    assert len(rows) == 1, "B deleted A's saved hiring search"


# -- discover/track ----------------------------------------------------------------------------


async def _track(ctx: Ctx) -> Req:
    """B tracks the very posting URL A's job was made from: the shared `jobs` row is found by
    URL, so this is where a snapshot or application of A's could be handed to B."""
    job = await ctx.need("job", ctx.a)
    return Req(
        "POST",
        "/discover/track",
        json={
            "apply_url": job["url"],
            "title": "B's own title",
            "company": "B's own company",
            "snippet": "B's own pasted description",
            "provider": "manual",
        },
    )


async def _track_unchanged(ctx: Ctx) -> None:
    """A is the module-wide owner other cases seed applications for, so look only at A's
    applications on this job."""
    job = await ctx.need("job", ctx.a)
    rows = (
        await ctx.sb.table("applications")
        .select("id")
        .eq("user_id", ctx.a.user_id)
        .eq("job_id", job["job_id"])
        .execute()
    ).data
    assert rows == [], "B's track created an application under A"


# -- today -------------------------------------------------------------------------------------


async def _today_list(ctx: Ctx) -> Req:
    await ctx.need("u_today_item", ctx.a)
    return Req("GET", "/today")


async def _today_dismiss(ctx: Ctx) -> Req:
    item = await ctx.need("u_today_item", ctx.a)
    return Req("POST", f"/today/{item['id']}/dismiss")


async def _today_dismiss_unchanged(ctx: Ctx) -> None:
    item = await ctx.need("u_today_item", ctx.a)
    row = (await ctx.sb.table("today_items").select("*").eq("id", item["id"]).execute()).data[0]
    assert row["dismissed_at"] is None, "B dismissed A's Today item"


async def _proposal_accept(ctx: Ctx) -> Req:
    p = await ctx.need("u_status_proposal", ctx.a)
    return Req("POST", f"/today/{p['item_id']}/accept-proposal")


async def _proposal_dismiss(ctx: Ctx) -> Req:
    p = await ctx.need("u_status_proposal", ctx.a)
    return Req("POST", f"/today/{p['item_id']}/dismiss-proposal")


async def _proposal_unchanged(ctx: Ctx) -> None:
    p = await ctx.need("u_status_proposal", ctx.a)
    proposal = (
        await ctx.sb.table("application_status_proposals")
        .select("status,resolved_at")
        .eq("id", p["proposal_id"])
        .execute()
    ).data[0]
    assert proposal["status"] == "pending", "B resolved A's status proposal"
    item = (
        await ctx.sb.table("today_items").select("dismissed_at").eq("id", p["item_id"]).execute()
    ).data[0]
    assert item["dismissed_at"] is None, "B dismissed A's proposal Today item"
    app = (
        await ctx.sb.table("applications").select("status").eq("id", p["application_id"]).execute()
    ).data[0]
    assert app["status"] == "saved", "B moved A's application through a proposal"


CASES = [
    Case(
        "GET /credentials",
        _credentials_list,
        kind="list",
        foreign_status=200,
        b_seeds=("u_credential",),
    ),
    Case(
        "DELETE /credentials/{service}/{provider}",
        _credentials_delete,
        foreign_status=204,
        owner_status=frozenset({204}),
        unchanged=_credentials_delete_unchanged,
        note=(
            "Credentials are keyed by (service, provider) for the CALLER, so B has no way to "
            "name A's key. B's identical request is a 204 that removes only B's own key "
            "(asserted: A's survives, B's is gone). 204 is the route's real contract here, "
            "never a 404, so there is no existence oracle either."
        ),
    ),
    Case(
        "GET /saved-searches",
        _saved_searches_list,
        kind="list",
        foreign_status=200,
        b_seeds=("u_saved_search",),
    ),
    Case(
        "PATCH /saved-searches/{search_id}",
        _saved_search_patch,
        unchanged=_saved_search_patch_unchanged,
    ),
    Case(
        "DELETE /saved-searches/{search_id}",
        _saved_search_delete,
        owner_status=frozenset({204}),
        unchanged=_saved_search_delete_unchanged,
    ),
    Case(
        "GET /hiring-signals/saves",
        _hs_saves_list,
        kind="list",
        foreign_status=200,
        b_seeds=("u_hiring_save",),
    ),
    Case(
        "DELETE /hiring-signals/saves/{save_id}",
        _hs_save_delete,
        owner_status=frozenset({204}),
        unchanged=_hs_save_delete_unchanged,
    ),
    Case(
        "GET /hiring-signals/searches",
        _hs_searches_list,
        kind="list",
        foreign_status=200,
        b_seeds=("u_hiring_search",),
    ),
    Case(
        "DELETE /hiring-signals/searches/{search_id}",
        _hs_search_delete,
        owner_status=frozenset({204}),
        unchanged=_hs_search_delete_unchanged,
    ),
    Case(
        "POST /discover/track",
        _track,
        foreign_status=201,
        owner_status=frozenset({201}),
        unchanged=_track_unchanged,
        note=(
            "Takes a posting URL, not an id of A's. jobs are shared and found by canonical URL, "
            "so B tracking A's URL must give B an application and snapshot of B's own content, "
            "none of A's seeded strings, and create nothing under A. 201 is the real contract."
        ),
    ),
    Case("GET /today", _today_list, kind="list", foreign_status=200, b_seeds=("u_today_item",)),
    Case(
        "POST /today/{item_id}/dismiss",
        _today_dismiss,
        unchanged=_today_dismiss_unchanged,
    ),
    Case(
        "POST /today/{item_id}/accept-proposal",
        _proposal_accept,
        unchanged=_proposal_unchanged,
    ),
    Case(
        "POST /today/{item_id}/dismiss-proposal",
        _proposal_dismiss,
        unchanged=_proposal_unchanged,
    ),
]


NOT_USER_SCOPED: dict[str, NotUserScoped] = {
    "POST /account/delete": NotUserScoped(
        "own-identity",
        "takes no id: it deletes the caller's own account and nothing else (the user comes from "
        "the token), and only after the caller types the confirmation phrase. A stranger cannot "
        "name another user. It is also destructive, so it cannot be a case in a module whose two "
        "users persist; it has its own drill in tests/integration/test_local_account_deletion.py",
    ),
    "GET /capabilities": NotUserScoped(
        "public",
        "signed-in only, but returns two server-wide switches (telegram on/off, the bot's public "
        "username) read from process state; no user row is read, so nothing of A's can leak",
    ),
    "GET /hiring-signals/status": NotUserScoped(
        "own-identity",
        "no id: answers, for the caller's own verified user id, whether the feature is on for "
        "them (the process-wide DISABLE_HIRING_SIGNALS switch and the operator's "
        "HIRING_SIGNALS_ALLOWED_USER_IDS list); reads no user row, so nothing of A's can leak",
    ),
    "GET /discover": NotUserScoped(
        "own-identity",
        "no id parameter: it reads the caller's own active profile and credentials (by the "
        "verified token's user id) and merges them with the shared job registry and live job "
        "boards. Returns no user rows; the control would call an LLM and job boards, which the "
        "suite forbids",
    ),
    "POST /credentials": NotUserScoped(
        "own-identity",
        "no id of anyone else's: upserts the caller's own (service, provider) key from the "
        "verified token's user id. The request must pass a live key validator (a provider call), "
        "which the suite forbids; the delete and list cases cover the per-user keying",
    ),
    "POST /hiring-signals/search": NotUserScoped(
        "own-identity",
        "no id: runs for the caller using the caller's own provider keys (SETUP_REQUIRED without "
        "them). The shared query cache holds public posts keyed without a user, by design "
        "(see hiring_signal_cache), and is only reachable with the caller's own key",
    ),
    "POST /hiring-signals/saves": NotUserScoped(
        "own-identity",
        "no id of anyone else's: inserts a pointer row for the caller (user id from the token); "
        "the duplicate check is scoped to the caller, so another user's save is never reported",
    ),
    "POST /hiring-signals/searches": NotUserScoped(
        "own-identity",
        "no id: inserts the caller's own typed role and metro; the dedupe and the cap count only "
        "the caller's rows",
    ),
    "POST /saved-searches": NotUserScoped(
        "own-identity",
        "no id: inserts a row for the caller from the verified token's user id; takes only the "
        "caller's own filter fields",
    ),
    "POST /link/code": NotUserScoped(
        "own-identity",
        "no id: mints a one-time code for the caller's own user id and returns it only to the "
        "caller (only its hash is stored)",
    ),
    "GET /oauth/gmail/callback": NotUserScoped(
        "secret",
        "authenticated by the single-use `state` minted for a signed-in user (not a bearer "
        "token); the user is resolved from the consumed state, and the route also needs a Google "
        "code exchange, which the suite forbids",
    ),
    "POST /discord/interactions": NotUserScoped(
        "secret",
        "authenticated by Discord's Ed25519 signature over the timestamp and the raw body, "
        "verified before the body is parsed or the database touched; the acting user is resolved "
        "from the Discord sender's own linked identity (the signed interaction's user id), so a "
        "sender can only ever act as the account linked to them",
    ),
    "POST /telegram/webhook": NotUserScoped(
        "secret",
        "authenticated by the X-Telegram-Bot-Api-Secret-Token header (constant-time compare), "
        "not a user token; the acting user is resolved from the Telegram sender's own linked "
        "identity, so a sender can only ever act as the account linked to them",
    ),
}
