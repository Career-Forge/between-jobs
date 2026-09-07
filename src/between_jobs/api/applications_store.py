"""Persistence for applications and their event log (Sprint 2.6c/2.6d) --
Proposal §19-20.

`applications` is the current projection; `application_events` is the
append-only audit trail -- "never try to reconstruct history from
updated_at" (§19's own words).

`create_application` stays as two sequential Postgrest calls (insert
application, insert its "created" event) -- a real but small and explicit
gap versus true atomicity, same spirit as this project's existing
`sessions.updated_at` gap: flagged, not hidden, and harmless here since a
failure between the two calls only costs the audit-trail row, never
invents an application from nothing.

`change_stage`, below, is different: Proposal §20 shows a status
transition and its emitted event committing in the SAME transaction as an
outbox append, and this platform's whole event-driven design depends on
that pairing never drifting apart. This backend only has Postgrest
(HTTP), not a raw connection to wrap several statements in
`async with db.transaction()` -- the real equivalent is a Postgres
function called via `.rpc(...)`, so the whole body runs as one
transaction. `change_application_stage` (Sprint 2.6d's migration) is that
function; this module just calls it.

`status` was originally unconstrained text at the DB layer (Proposal's own
DDL choice) but is now a real enforced vocabulary of 7 values, membership-
only (Applications Kanban K1, applications-kanban.md D1/D2) -- a Postgres
CHECK constraint plus `change_application_stage`'s own copy of that same
check, not a gated state machine; any-to-any moves among the 7 stay legal.
_DEFAULT_STATUS below is just the value a freshly created application
starts at ("saved" is one of the 7).
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from postgrest.exceptions import APIError

from supabase import AsyncClient

from .jobs_store import get_jobs, get_snapshots

_DEFAULT_STATUS = "saved"

# SQLSTATE for a plain `raise exception '...'` in plpgsql -- confirmed
# live against change_application_stage's own "not found" raise before
# relying on it here, rather than matching on the message text (which is
# an implementation detail of the function body, not a contract).
_RAISED_EXCEPTION_SQLSTATE = "P0001"

# invalid_parameter_value -- change_application_stage's OWN status-check
# raises with this explicit errcode (K1), distinct from the plain P0001
# above, so a bad status and a missing application never get confused with
# each other here.
_INVALID_STATUS_SQLSTATE = "22023"


class ApplicationNotFound(Exception):
    """An application id doesn't exist, or belongs to another user --
    deliberately indistinguishable from the caller's side."""


class InvalidApplicationStatus(Exception):
    """`new_status` isn't one of the 7 enforced values (K1, applications-
    kanban.md D2) -- raised by `change_application_stage`'s own check, the
    load-bearing enforcement for callers that never go through
    `ChangeApplicationStageRequest`'s Pydantic Literal (the Telegram
    bridge's stage-change callback parses `new_status` straight out of a
    forgeable callback_data string)."""


async def create_application(
    supabase: AsyncClient,
    user_id: str,
    *,
    job_id: str,
    active_job_snapshot_id: str,
    source_channel: str,
) -> dict[str, Any]:
    """Idempotent on `unique(user_id, job_id)` -- pasting the same job
    twice returns the existing application rather than erroring, matching
    `profile_store.create_pending_version`'s dedup-by-lookup shape."""
    existing = (
        await supabase.table("applications")
        .select("*")
        .eq("user_id", user_id)
        .eq("job_id", job_id)
        .execute()
    )
    if existing.data:
        return cast(dict[str, Any], existing.data[0])

    result = (
        await supabase.table("applications")
        .insert(
            {
                "user_id": user_id,
                "job_id": job_id,
                "active_job_snapshot_id": active_job_snapshot_id,
                "status": _DEFAULT_STATUS,
                "source_channel": source_channel,
            }
        )
        .execute()
    )
    application = cast(dict[str, Any], result.data[0])

    await record_event(
        supabase,
        user_id,
        application_id=application["id"],
        event_type="application.created",
        payload={"job_id": job_id, "status": _DEFAULT_STATUS},
        actor_type="user",
        actor_id=user_id,
        idempotency_key=f"application.created:{application['id']}",
        outbox_event_type="application.created.v1",
    )
    return application


async def list_applications(supabase: AsyncClient, user_id: str) -> list[dict[str, Any]]:
    result = (
        await supabase.table("applications")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return cast(list[dict[str, Any]], result.data)


async def find_application_by_url(
    supabase: AsyncClient, user_id: str, url: str
) -> dict[str, Any] | None:
    """browser-extension.md E1 -- resolves a browser tab's current URL back
    to an existing tracked application, so the extension's hybrid page-
    detection (D3) can tell "already tracked" from "offer to track" before
    fetching a prepared payload.

    Exact match only against `job_snapshots.source_url` and
    `jobs.canonical_url` for this user's own applications -- no query-
    string/tracking-param normalization. A real, disclosed v1 limitation:
    an ATS appending its own tracking params to the URL the extension
    reads from `window.location.href` (vs. what was stored when the job
    was tracked) will miss. Batch-fetches this user's applications' own
    snapshots/jobs and compares in Python, mirroring
    `applications_routes._with_snapshot`'s own batch-then-merge shape --
    fine at this table's real per-user scale (tens, not thousands, of
    rows), not worth a dedicated SQL function for a v1 exact match.

    An empty `url` never matches, even against a real stored empty string
    (`jobs_store.create_job_from_paste` stores `source_url` as `""` for a
    URL-less manually-pasted job, a real live path, not a hypothetical) --
    adversarially confirmed as a real false-positive otherwise: any
    accidental empty-string lookup would resolve to that job's application
    as "already tracked."""
    if not url:
        return None

    applications = await list_applications(supabase, user_id)
    if not applications:
        return None

    snapshot_ids = list({a["active_job_snapshot_id"] for a in applications})
    job_ids = list({a["job_id"] for a in applications})
    snapshot_by_id = {s["id"]: s for s in await get_snapshots(supabase, snapshot_ids)}
    jobs = await get_jobs(supabase, job_ids)
    canonical_url_by_job_id = {j["id"]: j.get("canonical_url") for j in jobs}

    for application in applications:
        snapshot = snapshot_by_id.get(application["active_job_snapshot_id"])
        if snapshot is not None and snapshot.get("source_url") == url:
            return application
        if canonical_url_by_job_id.get(application["job_id"]) == url:
            return application
    return None


async def get_application(
    supabase: AsyncClient, user_id: str, application_id: str
) -> dict[str, Any]:
    result = (
        await supabase.table("applications")
        .select("*")
        .eq("id", application_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise ApplicationNotFound(application_id)
    return cast(dict[str, Any], result.data[0])


async def get_event_by_idempotency_key(
    supabase: AsyncClient, user_id: str, idempotency_key: str
) -> dict[str, Any] | None:
    """Pre-check for a caller that needs to know *before* doing expensive
    work whether this exact command already ran (Sprint 3.0e's
    prepare_application, which would otherwise re-spend LLM tokens on a
    retried request) -- `record_event`'s own idempotent insert-or-return
    only helps once the work is already done."""
    result = (
        await supabase.table("application_events")
        .select("*")
        .eq("user_id", user_id)
        .eq("idempotency_key", idempotency_key)
        .execute()
    )
    return cast(dict[str, Any], result.data[0]) if result.data else None


async def get_latest_prepare_result(
    supabase: AsyncClient, user_id: str, application_id: str
) -> dict[str, Any] | None:
    """outreach-v2-search-first.md Phase I: reads the most recent real
    `application.prepared` event's payload back out -- the durable home
    for `fit`/`gate_outcome` (engine_contract.PrepareApplicationResult),
    computed live on every `/prepare` call and previously never re-
    readable once the HTTP response was consumed. A `None` return means
    no prepare has ever run for this application; an old event recorded
    before `gate_outcome` started being captured just has that key
    absent from its stored payload, same forward-compat shape as every
    other optional field on that model."""
    result = (
        await supabase.table("application_events")
        .select("*")
        .eq("user_id", user_id)
        .eq("application_id", application_id)
        .eq("event_type", "application.prepared")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    row = cast(dict[str, Any], result.data[0])
    return cast(dict[str, Any], row["payload"])


async def record_event(
    supabase: AsyncClient,
    user_id: str,
    *,
    application_id: str,
    event_type: str,
    payload: dict[str, Any],
    actor_type: str,
    actor_id: str,
    idempotency_key: str | None = None,
    outbox_event_type: str | None = None,
) -> dict[str, Any]:
    """Idempotent on `unique(user_id, idempotency_key)` -- a retried
    command with the same key returns the already-recorded event instead
    of writing a duplicate. Callers that don't need idempotency (e.g. a
    one-off system note) can omit it; a random key still satisfies the
    unique constraint without protecting against retries.

    `outbox_event_type` (Horizon Sprint 4.0) additionally appends a real
    `event_outbox` row -- Appendix A's versioned form (e.g.
    `"application.created.v1"`), distinct from `event_type`'s own
    unversioned timeline label, since a timeline entry and a bus event
    don't have to share a name (`run_prepare_application` uses one
    `event_type` for its timeline row regardless of outcome, but two
    different `outbox_event_type`s -- `artifact.generated.v1` vs.
    `artifact.generation_failed.v1` -- for real subscribers that care
    which happened). Omitted entirely by any caller not ready to publish
    yet, matching `change_application_stage`'s own precedent of being the
    only thing writing to the bus before this.

    Not atomic with the `application_events` insert above it -- Postgrest
    is HTTP-only here, so there's no shared transaction across two
    `.insert()` calls the way `change_application_stage`'s Postgres
    function gets for free. Same accepted-gap shape as
    `create_application`'s own two-sequential-call gap: low-stakes for a
    human-paced action, and this whole function already short-circuits on
    a retried idempotency_key before ever reaching this second insert, so
    a retry can't double-publish either."""
    key = idempotency_key or f"{event_type}:{uuid.uuid4()}"
    existing = (
        await supabase.table("application_events")
        .select("*")
        .eq("user_id", user_id)
        .eq("idempotency_key", key)
        .execute()
    )
    if existing.data:
        return cast(dict[str, Any], existing.data[0])

    result = (
        await supabase.table("application_events")
        .insert(
            {
                "application_id": application_id,
                "user_id": user_id,
                "event_type": event_type,
                "payload": payload,
                "actor_type": actor_type,
                "actor_id": actor_id,
                "idempotency_key": key,
            }
        )
        .execute()
    )
    row = cast(dict[str, Any], result.data[0])

    if outbox_event_type is not None:
        await (
            supabase.table("event_outbox")
            .insert(
                {
                    "user_id": user_id,
                    "aggregate_type": "application",
                    "aggregate_id": application_id,
                    "event_type": outbox_event_type,
                    "event_version": 1,
                    "payload": payload,
                    "idempotency_key": f"outbox:{key}",
                }
            )
            .execute()
        )

    return row


async def change_stage(
    supabase: AsyncClient,
    user_id: str,
    application_id: str,
    *,
    new_status: str,
    idempotency_key: str,
    actor_type: str = "user",
    actor_id: str | None = None,
) -> dict[str, Any]:
    """Transactional stage change (Sprint 2.6d) -- calls the
    `change_application_stage` Postgres function, which updates the
    application, records the paired application_events row, and appends
    to event_outbox all in one database transaction. Idempotent on
    `idempotency_key`: a retried call returns the current row without
    writing a second event or outbox row.

    `new_status` is plain `str`, not `models.ApplicationStatus` (K1) --
    this function is also called from the Telegram bridge with a value
    parsed straight out of a callback_data string, never validated as a
    Literal. `change_application_stage` itself is the real enforcement
    boundary; see `InvalidApplicationStatus`."""
    try:
        result = await supabase.rpc(
            "change_application_stage",
            {
                "p_user_id": user_id,
                "p_application_id": application_id,
                "p_new_status": new_status,
                "p_idempotency_key": idempotency_key,
                "p_actor_type": actor_type,
                "p_actor_id": actor_id or user_id,
            },
        ).execute()
    except APIError as e:
        if e.code == _RAISED_EXCEPTION_SQLSTATE:
            raise ApplicationNotFound(application_id) from e
        if e.code == _INVALID_STATUS_SQLSTATE:
            raise InvalidApplicationStatus(new_status) from e
        raise
    return cast(dict[str, Any], result.data)
