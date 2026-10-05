"""Store-level cross-tenant cases for the application-centred stores: applications and their
events, status proposals, artifact versions, working sets, the Today feed and the per-application
research stores (positioning brief, warm paths, company intel, contacts, interview practice).

Each case seeds user A's rows, calls the real store function as B with A's ids and checks what a
stranger must get, then calls it as A (the control) and reads A's rows back. Seeder kinds that
other modules already register are reused; the few extra rows are inserted with the service role.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from between_jobs.api import (
    application_status_proposals_store as proposals,
)
from between_jobs.api import (
    applications_store,
    artifact_versions_store,
    company_intel_store,
    contact_research_store,
    interview_practice_store,
    positioning_brief_store,
    today_store,
    warm_path_events_store,
    working_sets_store,
)

from . import (
    cases_applications as _cases_applications,  # noqa: F401  (registers the app_* seeders)
)
from . import (
    cases_subresources as _cases_subresources,  # noqa: F401  (registers the sub_* seeders)
)
from . import (
    cases_user as _cases_user,  # noqa: F401  (registers the u_* seeders)
)
from .harness import Ctx, StoreCase

# -- applications_store ------------------------------------------------------------------------


async def _create_application(ctx: Ctx) -> None:
    app = await ctx.need("application", ctx.a)
    job = app["job"]

    as_b = await applications_store.create_application(
        ctx.sb,
        ctx.b.user_id,
        job_id=job["job_id"],
        active_job_snapshot_id=job["snapshot_id"],
        source_channel="web",
    )
    assert as_b["user_id"] == ctx.b.user_id, "B's create_application returned a row not owned by B"
    assert as_b["id"] != app["id"], "B's create_application returned A's application"

    rows = (
        await ctx.sb.table("applications").select("*").eq("job_id", job["job_id"]).execute()
    ).data
    a_rows = [r for r in rows if r["user_id"] == ctx.a.user_id]
    assert [r["id"] for r in a_rows] == [app["id"]], "B's call changed or added to A's applications"
    assert a_rows[0]["status"] == "saved"

    as_a = await applications_store.create_application(
        ctx.sb,
        ctx.a.user_id,
        job_id=job["job_id"],
        active_job_snapshot_id=job["snapshot_id"],
        source_channel="web",
    )
    assert as_a["id"] == app["id"], "A's create_application did not return her existing row"


async def _get_application(ctx: Ctx) -> None:
    app = await ctx.need("application", ctx.a)
    with pytest.raises(applications_store.ApplicationNotFound):
        await applications_store.get_application(ctx.sb, ctx.b.user_id, app["id"])
    row = await applications_store.get_application(ctx.sb, ctx.a.user_id, app["id"])
    assert row["id"] == app["id"]


async def _get_event_by_idempotency_key(ctx: Ctx) -> None:
    event = await ctx.need("app_prepared_event", ctx.a)
    key = (
        await ctx.sb.table("application_events")
        .select("idempotency_key")
        .eq("id", event["id"])
        .execute()
    ).data[0]["idempotency_key"]
    assert await applications_store.get_event_by_idempotency_key(ctx.sb, ctx.b.user_id, key) is None
    found = await applications_store.get_event_by_idempotency_key(ctx.sb, ctx.a.user_id, key)
    assert found is not None
    assert found["id"] == event["id"]


async def _get_latest_prepare_result(ctx: Ctx) -> None:
    event = await ctx.need("app_prepared_event", ctx.a)
    app_id = event["application_id"]
    assert await applications_store.get_latest_prepare_result(ctx.sb, ctx.b.user_id, app_id) is None
    payload = await applications_store.get_latest_prepare_result(ctx.sb, ctx.a.user_id, app_id)
    assert payload is not None
    assert payload["gate_reason"] == event["gate_reason"]


async def _list_applications(ctx: Ctx) -> None:
    app_a = await ctx.need("application", ctx.a)
    app_b = await ctx.need("application", ctx.b)
    as_b = {r["id"] for r in await applications_store.list_applications(ctx.sb, ctx.b.user_id)}
    assert app_b["id"] in as_b, "B's own application is missing: the empty-of-A's check is void"
    assert app_a["id"] not in as_b, "B's list_applications showed A's application"
    as_a = {r["id"] for r in await applications_store.list_applications(ctx.sb, ctx.a.user_id)}
    assert app_a["id"] in as_a
    assert app_b["id"] not in as_a


async def _record_event(ctx: Ctx) -> None:
    app = await ctx.need("application", ctx.a)
    key = ctx.tag("cross-tenant-key")

    async def a_events() -> list[dict[str, Any]]:
        return list(
            (
                await ctx.sb.table("application_events")
                .select("*")
                .eq("application_id", app["id"])
                .execute()
            ).data
        )

    # B writes an event against A's application id: refused, and nothing is written.
    with pytest.raises(applications_store.ApplicationNotFound):
        await applications_store.record_event(
            ctx.sb,
            ctx.b.user_id,
            application_id=app["id"],
            event_type="application.note",
            payload={"note": ctx.mark(ctx.b, ctx.tag("b-note"))},
            actor_type="user",
            actor_id=ctx.b.user_id,
            idempotency_key=key,
            outbox_event_type="application.note.v1",
        )

    # B reusing A's idempotency key must not return A's event.
    a_event = await ctx.need("app_prepared_event", ctx.a)
    a_key = (
        await ctx.sb.table("application_events")
        .select("idempotency_key")
        .eq("id", a_event["id"])
        .execute()
    ).data[0]["idempotency_key"]
    b_app = await ctx.need("application", ctx.b)
    got = await applications_store.record_event(
        ctx.sb,
        ctx.b.user_id,
        application_id=b_app["id"],
        event_type="application.note",
        payload={},
        actor_type="user",
        actor_id=ctx.b.user_id,
        idempotency_key=a_key,
    )
    assert got["id"] != a_event["id"], "B got A's event back by presenting A's idempotency key"
    assert got["user_id"] == ctx.b.user_id

    # control: A records an event on her own application
    as_a = await applications_store.record_event(
        ctx.sb,
        ctx.a.user_id,
        application_id=app["id"],
        event_type="application.note",
        payload={"note": "a"},
        actor_type="user",
        actor_id=ctx.a.user_id,
        idempotency_key=key,
    )
    assert as_a["user_id"] == ctx.a.user_id
    assert as_a["application_id"] == app["id"]
    again = await applications_store.record_event(
        ctx.sb,
        ctx.a.user_id,
        application_id=app["id"],
        event_type="application.note",
        payload={"note": "a"},
        actor_type="user",
        actor_id=ctx.a.user_id,
        idempotency_key=key,
    )
    assert again["id"] == as_a["id"]

    after = await a_events()
    new_rows = [e for e in after if e["user_id"] != ctx.a.user_id]
    assert not new_rows, (
        "B's record_event attached a row to A's application: "
        f"{[(e['user_id'], e['event_type']) for e in new_rows]}"
    )
    foreign_outbox = (
        await ctx.sb.table("event_outbox")
        .select("id")
        .eq("aggregate_id", app["id"])
        .eq("user_id", ctx.b.user_id)
        .execute()
    ).data
    assert not foreign_outbox, "B's record_event queued an outbox row about A's application"


async def _write_event(ctx: Ctx) -> None:
    """The writer both `record_event` and `create_application` use. It has no ownership check
    (callers own that), but its idempotency lookup is scoped to the caller, so B presenting A's
    key writes a row of her own instead of being handed A's."""
    a_event = await ctx.need("app_prepared_event", ctx.a)
    a_key = (
        await ctx.sb.table("application_events")
        .select("idempotency_key")
        .eq("id", a_event["id"])
        .execute()
    ).data[0]["idempotency_key"]
    b_app = await ctx.need("application", ctx.b)

    got = await applications_store._write_event(
        ctx.sb,
        ctx.b.user_id,
        application_id=b_app["id"],
        event_type="application.note",
        payload={},
        actor_type="user",
        actor_id=ctx.b.user_id,
        idempotency_key=a_key,
    )

    assert got["id"] != a_event["id"], "B got A's event back by presenting A's idempotency key"
    assert got["user_id"] == ctx.b.user_id
    own = await applications_store._write_event(
        ctx.sb,
        ctx.a.user_id,
        application_id=a_event["application_id"],
        event_type="application.note",
        payload={},
        actor_type="user",
        actor_id=ctx.a.user_id,
        idempotency_key=a_key,
    )
    assert own["id"] == a_event["id"], "A's own retry no longer finds her event"


# -- application_status_proposals_store --------------------------------------------------------


async def _proposal_status(ctx: Ctx, proposal_id: str) -> dict[str, Any]:
    return dict(
        (
            await ctx.sb.table("application_status_proposals")
            .select("*")
            .eq("id", proposal_id)
            .execute()
        ).data[0]
    )


async def _get_status_proposal(ctx: Ctx) -> None:
    seed = await ctx.need("u_status_proposal", ctx.a)
    with pytest.raises(proposals.StatusProposalNotFound):
        await proposals.get_status_proposal(ctx.sb, ctx.b.user_id, seed["proposal_id"])
    row = await proposals.get_status_proposal(ctx.sb, ctx.a.user_id, seed["proposal_id"])
    assert row["id"] == seed["proposal_id"]


async def _resolve_status_proposal(ctx: Ctx) -> None:
    seed = await ctx.need("u_status_proposal", ctx.a)
    for status in ("accepted", "dismissed"):
        with pytest.raises(proposals.StatusProposalNotFound):
            await proposals.resolve_status_proposal(
                ctx.sb, ctx.b.user_id, seed["proposal_id"], status=status
            )
    row = await _proposal_status(ctx, seed["proposal_id"])
    assert row["status"] == "pending", "B resolved A's proposal"
    assert row["resolved_at"] is None

    resolved = await proposals.resolve_status_proposal(
        ctx.sb, ctx.a.user_id, seed["proposal_id"], status="accepted"
    )
    assert resolved["status"] == "accepted"


async def _dismiss_other_pending_proposals(ctx: Ctx) -> None:
    seed = await ctx.need("u_status_proposal", ctx.a)
    first = await _proposal_status(ctx, seed["proposal_id"])
    sibling = (
        await ctx.sb.table("application_status_proposals")
        .insert(
            {
                "user_id": ctx.a.user_id,
                "application_id": seed["application_id"],
                "outreach_draft_id": first["outreach_draft_id"],
                "proposed_type": "assessment.received",
                "confidence": 0.5,
                "source_gmail_message_id": ctx.tag("msg"),
            }
        )
        .execute()
    ).data[0]

    # B names A's application and A's accepted proposal; the sibling must stay pending.
    await proposals.dismiss_other_pending_proposals(
        ctx.sb, ctx.b.user_id, seed["application_id"], except_proposal_id=seed["proposal_id"]
    )
    assert (await _proposal_status(ctx, sibling["id"]))["status"] == "pending", (
        "B dismissed A's pending proposal"
    )
    # ... and also naming the sibling as the one to keep must change nothing.
    await proposals.dismiss_other_pending_proposals(
        ctx.sb, ctx.b.user_id, seed["application_id"], except_proposal_id=sibling["id"]
    )
    assert (await _proposal_status(ctx, seed["proposal_id"]))["status"] == "pending", (
        "B dismissed A's pending proposal"
    )

    await proposals.dismiss_other_pending_proposals(
        ctx.sb, ctx.a.user_id, seed["application_id"], except_proposal_id=seed["proposal_id"]
    )
    assert (await _proposal_status(ctx, sibling["id"]))["status"] == "dismissed"
    assert (await _proposal_status(ctx, seed["proposal_id"]))["status"] == "pending"


# -- artifact_versions_store -------------------------------------------------------------------


async def _get_existing_artifact_ids(ctx: Ctx) -> None:
    art = await ctx.need("app_resume_artifact", ctx.a)
    artifact_id = art["row"]["artifact_id"]
    ids = [
        artifact_id,
        artifact_versions_store.artifact_id_for(art["application_id"], "cover_letter"),
    ]
    assert (
        await artifact_versions_store.get_existing_artifact_ids(ctx.sb, ctx.b.user_id, ids) == set()
    )
    assert await artifact_versions_store.get_existing_artifact_ids(ctx.sb, ctx.a.user_id, ids) == {
        artifact_id
    }


async def _get_latest_version(ctx: Ctx) -> None:
    art = await ctx.need("app_resume_artifact", ctx.a)
    app_id = art["application_id"]
    assert (
        await artifact_versions_store.get_latest_version(ctx.sb, ctx.b.user_id, app_id, "resume")
        is None
    )
    row = await artifact_versions_store.get_latest_version(ctx.sb, ctx.a.user_id, app_id, "resume")
    assert row is not None
    assert row["id"] == art["id"]


# -- working_sets_store ------------------------------------------------------------------------


async def _a_working_set(ctx: Ctx) -> tuple[str, str]:
    kind = ctx.tag("kind")
    row = (
        await ctx.sb.table("working_sets")
        .insert(
            {
                "user_id": ctx.a.user_id,
                "kind": kind,
                "source_channel": "web",
                "items": [{"note": ctx.mark(ctx.a, ctx.tag("ws"))}],
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            }
        )
        .execute()
    ).data[0]
    return str(row["id"]), kind


async def _get_working_set(ctx: Ctx) -> None:
    ws_id, _ = await _a_working_set(ctx)
    with pytest.raises(working_sets_store.WorkingSetNotFound):
        await working_sets_store.get_working_set(ctx.sb, ctx.b.user_id, ws_id)
    row = await working_sets_store.get_working_set(ctx.sb, ctx.a.user_id, ws_id)
    assert row["id"] == ws_id


async def _get_active_working_set(ctx: Ctx) -> None:
    ws_id, kind = await _a_working_set(ctx)
    assert await working_sets_store.get_active_working_set(ctx.sb, ctx.b.user_id, kind) is None
    row = await working_sets_store.get_active_working_set(ctx.sb, ctx.a.user_id, kind)
    assert row is not None
    assert row["id"] == ws_id


# -- today_store -------------------------------------------------------------------------------


async def _list_today_items(ctx: Ctx) -> None:
    plain = await ctx.need("u_today_item", ctx.a)
    proposal = await ctx.need("u_status_proposal", ctx.a)
    b_item = await ctx.need("u_today_item", ctx.b)
    await ctx.need("u_status_proposal", ctx.b)

    as_b = await today_store.list_today_items(ctx.sb, ctx.b.user_id)
    b_ids = {i["id"] for i in as_b}
    assert b_item["id"] in b_ids, "B's own item is missing: the empty-of-A's check is void"
    assert plain["id"] not in b_ids
    assert proposal["item_id"] not in b_ids
    leaked = [m for m in ctx.pair.marks["A"] if m in repr(as_b)]
    assert not leaked, f"B's Today list contained something of A's: {leaked[:3]}"

    as_a = await today_store.list_today_items(ctx.sb, ctx.a.user_id)
    by_id = {i["id"]: i for i in as_a}
    assert plain["id"] in by_id
    assert proposal["item_id"] in by_id
    embedded = by_id[proposal["item_id"]]["status_proposal"]
    assert embedded is not None
    assert embedded["id"] == proposal["proposal_id"]
    assert b_item["id"] not in by_id


async def _dismiss_today_item(ctx: Ctx) -> None:
    plain = await ctx.need("u_today_item", ctx.a)
    proposal = await ctx.need("u_status_proposal", ctx.a)
    for item_id in (plain["id"], proposal["item_id"]):
        with pytest.raises(today_store.TodayItemNotFound):
            await today_store.dismiss_today_item(ctx.sb, ctx.b.user_id, item_id)
        row = (await ctx.sb.table("today_items").select("*").eq("id", item_id).execute()).data[0]
        assert row["dismissed_at"] is None, "B dismissed A's Today item"
    done = await today_store.dismiss_today_item(ctx.sb, ctx.a.user_id, plain["id"])
    assert done["dismissed_at"] is not None


# -- per-application research stores -----------------------------------------------------------


async def _get_latest_brief(ctx: Ctx) -> None:
    seed = await ctx.need("sub_positioning_brief", ctx.a)
    app_id = seed["application_id"]
    assert await positioning_brief_store.get_latest_brief(ctx.sb, ctx.b.user_id, app_id) is None
    row = await positioning_brief_store.get_latest_brief(ctx.sb, ctx.a.user_id, app_id)
    assert row is not None
    assert row["lead_with"] == seed["lead"]


async def _warm_path_get_latest_run(ctx: Ctx) -> None:
    seed = await ctx.need("sub_warm_path", ctx.a)
    app_id = seed["application_id"]
    assert await warm_path_events_store.get_latest_run(ctx.sb, ctx.b.user_id, app_id) is None
    row = await warm_path_events_store.get_latest_run(ctx.sb, ctx.a.user_id, app_id)
    assert row is not None
    assert row["user_id"] == ctx.a.user_id
    assert row["application_id"] == app_id


async def _company_intel_get_latest_run(ctx: Ctx) -> None:
    seed = await ctx.need("sub_company_intel_run", ctx.a)
    app_id = seed["application_id"]
    assert await company_intel_store.get_latest_run(ctx.sb, ctx.b.user_id, app_id) is None
    row = await company_intel_store.get_latest_run(ctx.sb, ctx.a.user_id, app_id)
    assert row is not None
    assert row["id"] == seed["run_id"]


async def _contact_get_latest_run(ctx: Ctx) -> None:
    seed = await ctx.need("sub_contact", ctx.a)
    app_id = seed["application_id"]
    assert await contact_research_store.get_latest_run(ctx.sb, ctx.b.user_id, app_id) is None
    row = await contact_research_store.get_latest_run(ctx.sb, ctx.a.user_id, app_id)
    assert row is not None
    assert row["id"] == seed["run_id"]


async def _get_owned_candidate(ctx: Ctx) -> None:
    seed = await ctx.need("sub_contact", ctx.a)
    b_seed = await ctx.need("sub_contact", ctx.b)
    with pytest.raises(contact_research_store.CandidateNotFound):
        await contact_research_store.get_owned_candidate(
            ctx.sb, ctx.b.user_id, seed["candidate_id"]
        )
    mine = await contact_research_store.get_owned_candidate(
        ctx.sb, ctx.b.user_id, b_seed["candidate_id"]
    )
    assert mine["id"] == b_seed["candidate_id"]
    row = await contact_research_store.get_owned_candidate(
        ctx.sb, ctx.a.user_id, seed["candidate_id"]
    )
    assert row["id"] == seed["candidate_id"]
    with pytest.raises(contact_research_store.CandidateNotFound):
        await contact_research_store.get_owned_candidate(
            ctx.sb, ctx.a.user_id, b_seed["candidate_id"]
        )


async def _get_session(ctx: Ctx) -> None:
    seed = await ctx.need("sub_interview_session", ctx.a)
    with pytest.raises(interview_practice_store.InterviewSessionNotFound):
        await interview_practice_store.get_session(ctx.sb, ctx.b.user_id, seed["session_id"])
    row = await interview_practice_store.get_session(ctx.sb, ctx.a.user_id, seed["session_id"])
    assert row["id"] == seed["session_id"]


async def _list_sessions(ctx: Ctx) -> None:
    seed = await ctx.need("sub_interview_session", ctx.a)
    b_seed = await ctx.need("sub_interview_session", ctx.b)
    app_id = seed["application_id"]
    assert await interview_practice_store.list_sessions(ctx.sb, ctx.b.user_id, app_id) == []
    # A's session id must not appear under B's own application either.
    under_b = await interview_practice_store.list_sessions(
        ctx.sb, ctx.b.user_id, b_seed["application_id"]
    )
    assert [s["id"] for s in under_b] == [b_seed["session_id"]]
    rows = await interview_practice_store.list_sessions(ctx.sb, ctx.a.user_id, app_id)
    assert [s["id"] for s in rows] == [seed["session_id"]]


STORE_CASES = [
    StoreCase(
        "application_status_proposals_store.dismiss_other_pending_proposals",
        _dismiss_other_pending_proposals,
    ),
    StoreCase("application_status_proposals_store.get_status_proposal", _get_status_proposal),
    StoreCase(
        "application_status_proposals_store.resolve_status_proposal", _resolve_status_proposal
    ),
    StoreCase("applications_store.create_application", _create_application),
    StoreCase("applications_store.get_application", _get_application),
    StoreCase("applications_store.get_event_by_idempotency_key", _get_event_by_idempotency_key),
    StoreCase("applications_store.get_latest_prepare_result", _get_latest_prepare_result),
    StoreCase("applications_store.list_applications", _list_applications),
    StoreCase("applications_store.record_event", _record_event),
    StoreCase("applications_store._write_event", _write_event),
    StoreCase("artifact_versions_store.get_existing_artifact_ids", _get_existing_artifact_ids),
    StoreCase("artifact_versions_store.get_latest_version", _get_latest_version),
    StoreCase("working_sets_store.get_active_working_set", _get_active_working_set),
    StoreCase("working_sets_store.get_working_set", _get_working_set),
    StoreCase("today_store.dismiss_today_item", _dismiss_today_item),
    StoreCase("today_store.list_today_items", _list_today_items),
    StoreCase("positioning_brief_store.get_latest_brief", _get_latest_brief),
    StoreCase("warm_path_events_store.get_latest_run", _warm_path_get_latest_run),
    StoreCase("company_intel_store.get_latest_run", _company_intel_get_latest_run),
    StoreCase("contact_research_store.get_latest_run", _contact_get_latest_run),
    StoreCase("contact_research_store.get_owned_candidate", _get_owned_candidate),
    StoreCase("interview_practice_store.get_session", _get_session),
    StoreCase("interview_practice_store.list_sessions", _list_sessions),
]
