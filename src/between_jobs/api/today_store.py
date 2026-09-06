"""Persistence for the Today feed (Horizon Sprint 4.0) -- Proposal §37.1,
scoped per `digest_listener.py`'s own docstring. Read-and-dismiss only:
`today_items` rows are written exclusively by the digest listener, never
by a route handling a direct user request. (Gmail reply/status parsing R4
adds two more direct-write routes, but they resolve the underlying
`application_status_proposals` row via `application_status_proposals_
store.py`, not this module -- a Today item itself is still only ever
dismissed here, same as always.)

Job Finder P9c adds `_with_job_matches`: a `kind="high_fit_job"` item's
job-specific payload lives in the separate `today_item_job_matches`
table (today-feed-job-matching.md D8), so listing embeds it with a
second, batched query -- mirroring `applications_routes.py`'s own
`_with_resume_exists`/`_with_snapshot` pattern (one query for the whole
page, merged in Python) rather than a PostgREST embedded-select join,
which this codebase has no precedent for. Gmail reply/status parsing R4
adds `_with_status_proposals` for `kind="status_proposal"` items, same
shape -- except its own companion table (`today_item_status_proposals`)
is only a thin link, not a data copy, so embedding needs a SECOND batched
query (the link rows, then the real `application_status_proposals` rows
they point to) rather than one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from supabase import AsyncClient


class TodayItemNotFound(Exception):
    """A today_item id doesn't exist, or belongs to another user --
    deliberately indistinguishable from the caller's side."""


async def _with_job_matches(
    supabase: AsyncClient, items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    match_item_ids = [item["id"] for item in items if item["kind"] == "high_fit_job"]
    matches_by_item_id: dict[str, dict[str, Any]] = {}
    if match_item_ids:
        result = (
            await supabase.table("today_item_job_matches")
            .select("*")
            .in_("today_item_id", match_item_ids)
            .execute()
        )
        matches_by_item_id = {
            row["today_item_id"]: row for row in cast(list[dict[str, Any]], result.data)
        }
    return [{**item, "job_match": matches_by_item_id.get(item["id"])} for item in items]


async def _with_status_proposals(
    supabase: AsyncClient, items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    proposal_item_ids = [item["id"] for item in items if item["kind"] == "status_proposal"]
    proposals_by_item_id: dict[str, dict[str, Any]] = {}
    if proposal_item_ids:
        links_result = (
            await supabase.table("today_item_status_proposals")
            .select("*")
            .in_("today_item_id", proposal_item_ids)
            .execute()
        )
        links = cast(list[dict[str, Any]], links_result.data)
        proposal_ids = [link["application_status_proposal_id"] for link in links]
        proposals_by_id: dict[str, dict[str, Any]] = {}
        if proposal_ids:
            proposals_result = (
                await supabase.table("application_status_proposals")
                .select("*")
                .in_("id", proposal_ids)
                .execute()
            )
            proposals_by_id = {
                row["id"]: row for row in cast(list[dict[str, Any]], proposals_result.data)
            }
        # A proposal can be gone by the time this lists (the underlying
        # application deleted, cascading it away) even though its Today
        # item survives -- `.get()` below embeds None for that item
        # rather than raising, same "unknown means labeled as unknown"
        # treatment `job_match` already gets.
        proposals_by_item_id = {
            link["today_item_id"]: proposals_by_id[link["application_status_proposal_id"]]
            for link in links
            if link["application_status_proposal_id"] in proposals_by_id
        }
    return [{**item, "status_proposal": proposals_by_item_id.get(item["id"])} for item in items]


async def list_today_items(supabase: AsyncClient, user_id: str) -> list[dict[str, Any]]:
    result = (
        await supabase.table("today_items")
        .select("*")
        .eq("user_id", user_id)
        .is_("dismissed_at", "null")
        .order("created_at", desc=True)
        .execute()
    )
    items = await _with_job_matches(supabase, cast(list[dict[str, Any]], result.data))
    return await _with_status_proposals(supabase, items)


async def dismiss_today_item(supabase: AsyncClient, user_id: str, item_id: str) -> dict[str, Any]:
    result = (
        await supabase.table("today_items")
        .update({"dismissed_at": datetime.now(UTC).isoformat()})
        .eq("id", item_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise TodayItemNotFound(item_id)
    return cast(dict[str, Any], result.data[0])
