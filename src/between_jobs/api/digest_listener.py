"""The digest listener (Horizon Sprint 4.0) -- the first real
`event_outbox` subscriber. Proposal §20's own words: "the outbox exists
before its subscribers." This is that subscriber.

Turns claimed outbox rows into `today_items` rows the Today page reads --
scoped deliberately to the events this platform can genuinely produce
today: a job tracked (`application.created.v1`), a resume generated or
failed (`artifact.generated.v1` / `artifact.generation_failed.v1`), a
stage change (`application.stage_changed.v1`), and (Job Finder P9b) a
high-fit new job found by the saved-search matcher (`job_registry.
match_found.v1`). Proposal §37.1's remaining four bullets -- outreach
followups, interviews, stale applications, artifacts awaiting approval --
still depend on a capability that doesn't exist yet (Outreach, Practice)
or a mechanic nothing produces yet (an "approve" action, a time-based
staleness scan). Guessing at those now would mean either fabricated
content or a listener quietly doing nothing for events that never
arrive; leaving them out is the honest choice, not a shortcut.

`job_registry.match_found.v1` is NOT about an application -- its own
`aggregate_id` is a `saved_searches` row, not an `applications` one, so
`_render()` branches on `event_type` BEFORE unconditionally resolving
`aggregate_id` against `applications` the way every other event type
here still does.

Job Finder P10 (job-finder-p10-digest.md) adds a real-time Telegram push
for `high_fit_job` items specifically -- the one kind produced entirely
in the background, with no active user session to notice it. The
already-atomic `insert_high_fit_job_today_item` RPC succeeding IS the
"genuinely new" signal (the same guarantee P9 already proved live), so
the push needs no separate schedule or delivery-tracking of its own.
The other four kinds all fire in direct response to something the user
just did, so they stay pull-only (Today feed) for now -- a scheduled
digest covering those too is a deliberately deferred follow-up, not
built here.

Gmail reply/status parsing R3 adds `gmail_reply.status_proposed.v1` (a
below-threshold status proposal from `gmail_reply_checker.py`, worth a
human's review) -- like `job_registry.match_found.v1`, its own
`aggregate_id` isn't an `applications` row (it's the `application_status_
proposals` row itself), so it gets the same "branch before the generic
application lookup" treatment. Unlike that event, this one genuinely IS
about a real application -- its own resolved `application_id` rides
along in the payload -- so its Today item still carries `application_id`
for real, just sourced from the payload instead of `aggregate_id`. An
ABOVE-threshold proposal never reaches this listener at all: it
auto-applies via the existing `change_stage`, which already produces its
own `application.stage_changed.v1` event through the generic path above.
"""

from __future__ import annotations

from typing import Any, TypedDict

from postgrest.exceptions import APIError

from supabase import AsyncClient

from .application_status_proposals_store import StatusProposalNotFound, get_status_proposal
from .applications_store import ApplicationNotFound, get_application
from .jobs_store import SnapshotNotFound, get_snapshot
from .saved_searches_store import SavedSearchNotFound, get_saved_search
from .telegram_client import TelegramClient
from .telegram_identity import get_chat_id

_UNIQUE_VIOLATION = "23505"

_PROPOSAL_LABELS: dict[str, str] = {
    "application.acknowledged": "application acknowledged",
    "assessment.received": "assessment received",
    "interview.requested": "interview requested",
    "interview.scheduled": "interview scheduled",
    "application.rejected": "application rejected",
    "offer.received": "offer received",
    "recruiter.replied": "recruiter replied",
    "unknown": "new reply",
}
"""Human-readable labels for the classifier's 8-value taxonomy -- this
event only ever carries a below-threshold (`status='pending'`) proposal;
an above-threshold one auto-applies via `change_stage` instead, which
already produces its own `application.stage_changed.v1` Today item, so
there's no separate "high-confidence" rendering to add here."""


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


def _render_job_match(payload: dict[str, Any]) -> _Rendered:
    title = payload.get("title") or "Untitled"
    company = payload.get("company")
    headline = (
        f"🎯 High-fit match: {title} @ {company}" if company else f"🎯 High-fit match: {title}"
    )
    return _Rendered(kind="high_fit_job", headline=headline, detail=payload.get("one_liner"))


def _render_status_proposal(payload: dict[str, Any]) -> _Rendered:
    label = _PROPOSAL_LABELS.get(payload.get("proposed_type", "unknown"), "new reply")
    confidence_pct = int(payload.get("confidence", 0.0) * 100)
    return _Rendered(
        kind="status_proposal",
        headline=f"✉️ Possible update: {label}",
        detail=f"{confidence_pct}% confidence -- review and confirm",
    )


async def _render(supabase: AsyncClient, row: dict[str, Any]) -> _Rendered | None:
    """None means "not a today_item" -- either an event_type this
    listener doesn't recognize (a future subscriber's event landed in the
    same outbox), or the entity it refers to is already gone (a real but
    rare race, not an error to surface)."""
    event_type = row["event_type"]
    payload = row["payload"]

    if event_type == "job_registry.match_found.v1":
        # A user can delete a saved search between the matcher publishing
        # this event and the outbox worker processing it -- confirmed
        # existence up front the same way every other branch below
        # confirms its application exists, rather than letting a stale
        # reference surface as an uncaught foreign-key violation out of
        # `_insert_high_fit_job_item`'s RPC (a different SQLSTATE than
        # the unique-violation `handle_batch` already catches, which
        # would otherwise propagate out of `run_worker_forever`'s bare
        # `while True` loop and silently kill the whole outbox worker).
        try:
            await get_saved_search(supabase, row["user_id"], row["aggregate_id"])
        except SavedSearchNotFound:
            return None
        return _render_job_match(payload)

    if event_type == "gmail_reply.status_proposed.v1":
        # Gmail reply/status parsing R3 -- `aggregate_id` here is the
        # application_status_proposals row itself, not an application (a
        # user, or a cascading application delete, can remove the
        # underlying proposal between the poller publishing this event
        # and the outbox worker processing it -- the same "confirm it
        # still exists first" precedent job_registry.match_found.v1 uses
        # above, so a stale reference never surfaces as an uncaught
        # foreign-key violation out of the RPC insert below).
        try:
            await get_status_proposal(supabase, row["user_id"], row["aggregate_id"])
        except StatusProposalNotFound:
            return None
        return _render_status_proposal(payload)

    user_id = row["user_id"]
    application_id = row["aggregate_id"]

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


async def _insert_high_fit_job_item(
    supabase: AsyncClient, row: dict[str, Any], rendered: _Rendered
) -> None:
    """The two-table (today_items + today_item_job_matches) atomic
    insert, via the migration's own `insert_high_fit_job_today_item`
    Postgres function -- mirrors `change_application_stage`'s own "two
    tables change together, or neither does" precedent. A unique
    violation on EITHER the source_outbox_event_id boundary or the
    (saved_search_id, apply_url) dedup boundary rolls back both inserts
    and propagates the SAME way a plain `.insert()`'s violation would,
    so the caller's existing `except APIError` handling covers this too."""
    payload = row["payload"]
    await supabase.rpc(
        "insert_high_fit_job_today_item",
        {
            "p_user_id": row["user_id"],
            "p_headline": rendered["headline"],
            "p_detail": rendered["detail"],
            "p_source_outbox_event_id": row["id"],
            "p_saved_search_id": row["aggregate_id"],
            "p_apply_url": payload["apply_url"],
            "p_title": payload["title"],
            "p_company": payload.get("company"),
            "p_location": payload.get("location"),
            "p_score100": payload["score100"],
            "p_bin": payload["bin"],
            "p_snippet": payload.get("snippet", ""),
            "p_provider": payload.get("provider", "registry"),
        },
    ).execute()


async def _insert_status_proposal_item(
    supabase: AsyncClient, row: dict[str, Any], rendered: _Rendered
) -> None:
    """The two-table (today_items + today_item_status_proposals) atomic
    insert, via the migration's own `insert_status_proposal_today_item`
    Postgres function -- same "two tables change together, or neither
    does" precedent as `_insert_high_fit_job_item` above. Unlike that
    event, this one IS about a real application -- `p_application_id`
    comes straight off the payload the poller already resolved (a real
    three-table join it would be wasteful to redo here), so this Today
    item shows up associated with its application like every other kind
    except `high_fit_job`."""
    payload = row["payload"]
    await supabase.rpc(
        "insert_status_proposal_today_item",
        {
            "p_user_id": row["user_id"],
            "p_application_id": payload["application_id"],
            "p_headline": rendered["headline"],
            "p_detail": rendered["detail"],
            "p_source_outbox_event_id": row["id"],
            "p_application_status_proposal_id": row["aggregate_id"],
        },
    ).execute()


async def _push_job_match(
    supabase: AsyncClient, telegram: TelegramClient, row: dict[str, Any], rendered: _Rendered
) -> None:
    """Best-effort real-time push (Job Finder P10) -- only ever called
    right after `_insert_high_fit_job_item` just genuinely succeeded, so
    the today_item is already durably persisted by the time this runs. A
    user with no linked Telegram identity, a network blip, or the bot
    being blocked never loses or rolls back the already-correct data,
    only the proactive nudge -- caught broadly on purpose, same
    reasoning as `company_intel_routes.py`'s own fail-open precedent for
    a bonus side effect of work that already succeeded."""
    try:
        chat_id = await get_chat_id(supabase, row["user_id"])
        if chat_id is None:
            return
        apply_url = row["payload"].get("apply_url", "")
        text = f"{rendered['headline']}\n\n{rendered['detail'] or ''}\n\n{apply_url}".strip()
        await telegram.send_message(chat_id, text)
    except Exception:  # deliberately broad -- see docstring above
        pass


async def handle_batch(
    supabase: AsyncClient, rows: list[dict[str, Any]], *, telegram: TelegramClient | None = None
) -> int:
    """Renders and inserts one `today_items` row per claimed outbox row
    this listener recognizes. Idempotent on `source_outbox_event_id`
    (the migration's own unique index) -- a row already turned into a
    today_item (e.g. from a prior partial-batch failure) is silently
    skipped, never duplicated. Returns the count actually inserted.

    `telegram` is optional so this stays testable/usable with no push
    capability configured -- when given, a genuinely new `high_fit_job`
    item also triggers `_push_job_match`."""
    inserted = 0
    for row in rows:
        rendered = await _render(supabase, row)
        if rendered is None:
            continue
        try:
            if row["event_type"] == "job_registry.match_found.v1":
                await _insert_high_fit_job_item(supabase, row, rendered)
                if telegram is not None:
                    await _push_job_match(supabase, telegram, row, rendered)
            elif row["event_type"] == "gmail_reply.status_proposed.v1":
                await _insert_status_proposal_item(supabase, row, rendered)
            else:
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
