"""The digest listener (Horizon Sprint 4.0) -- the first real
`event_outbox` subscriber. Proposal §20's own words: "the outbox exists
before its subscribers." This is that subscriber.

Turns claimed outbox rows into `today_items` rows the Today page reads --
scoped deliberately to the events this platform can genuinely produce
today: a job tracked (`application.created.v1`), a resume generated or
failed (`artifact.generated.v1` / `artifact.generation_failed.v1`), and a
stage change (`application.stage_changed.v1`). Proposal §37.1 lists five
more Today items -- high-fit new jobs, outreach followups, interviews,
stale applications, artifacts awaiting approval -- but every one of them
depends on a capability that doesn't exist yet (Discovery, Outreach,
Practice) or a mechanic nothing produces yet (an "approve" action, a
time-based staleness scan). Guessing at those now would mean either
fabricated content or a listener quietly doing nothing for events that
never arrive; leaving them out is the honest choice, not a shortcut.
"""

from __future__ import annotations

from typing import Any, TypedDict

from postgrest.exceptions import APIError

from supabase import AsyncClient

from .applications_store import ApplicationNotFound, get_application
from .jobs_store import SnapshotNotFound, get_snapshot

_UNIQUE_VIOLATION = "23505"


class _Rendered(TypedDict):
    kind: str
    headline: str
    detail: str | None


async def _job_label(supabase: AsyncClient, application: dict[str, Any]) -> str:
    try:
        snapshot = await get_snapshot(supabase, application["active_job_snapshot_id"])
    except SnapshotNotFound:
        return "Untitled"
    title = snapshot.get("title") or "Untitled"
    company = snapshot.get("company_name")
    return f"{title} @ {company}" if company else title


async def _render(supabase: AsyncClient, row: dict[str, Any]) -> _Rendered | None:
    """None means "not a today_item" -- either an event_type this
    listener doesn't recognize (a future subscriber's event landed in the
    same outbox), or the application it refers to is already gone (a
    real but rare race, not an error to surface)."""
    event_type = row["event_type"]
    user_id = row["user_id"]
    application_id = row["aggregate_id"]
    payload = row["payload"]

    try:
        application = await get_application(supabase, user_id, application_id)
    except ApplicationNotFound:
        return None

    job_label = await _job_label(supabase, application)

    if event_type == "application.created.v1":
        return _Rendered(kind="job_tracked", headline=f"🆕 Tracking {job_label}", detail=None)

    if event_type == "artifact.generated.v1":
        score = payload.get("final_score")
        detail = f"ATS score {int(score)}/100" if score is not None else None
        return _Rendered(
            kind="resume_ready", headline=f"📄 Resume ready for {job_label}", detail=detail
        )

    if event_type == "artifact.generation_failed.v1":
        warnings = payload.get("warnings") or []
        detail = "; ".join(warnings) if warnings else None
        return _Rendered(
            kind="resume_failed",
            headline=f"⚠️ Resume generation needs attention for {job_label}",
            detail=detail,
        )

    if event_type == "application.stage_changed.v1":
        new_stage = payload.get("new_stage", "")
        return _Rendered(
            kind="stage_changed", headline=f"📌 {job_label} moved to {new_stage}", detail=None
        )

    return None


async def handle_batch(supabase: AsyncClient, rows: list[dict[str, Any]]) -> int:
    """Renders and inserts one `today_items` row per claimed outbox row
    this listener recognizes. Idempotent on `source_outbox_event_id`
    (the migration's own unique index) -- a row already turned into a
    today_item (e.g. from a prior partial-batch failure) is silently
    skipped, never duplicated. Returns the count actually inserted."""
    inserted = 0
    for row in rows:
        rendered = await _render(supabase, row)
        if rendered is None:
            continue
        try:
            await (
                supabase.table("today_items")
                .insert(
                    {
                        "user_id": row["user_id"],
                        "application_id": row["aggregate_id"],
                        "kind": rendered["kind"],
                        "headline": rendered["headline"],
                        "detail": rendered["detail"],
                        "source_outbox_event_id": row["id"],
                    }
                )
                .execute()
            )
            inserted += 1
        except APIError as e:
            if e.code != _UNIQUE_VIOLATION:
                raise
    return inserted
