"""Persistence for the Today feed (Horizon Sprint 4.0) -- Proposal §37.1,
scoped per `digest_listener.py`'s own docstring. Read-and-dismiss only:
`today_items` rows are written exclusively by the digest listener, never
by a route handling a direct user request.

Job Finder P9c adds `_with_job_matches`: a `kind="high_fit_job"` item's
job-specific payload lives in the separate `today_item_job_matches`
table (today-feed-job-matching.md D8), so listing embeds it with a
second, batched query -- mirroring `applications_routes.py`'s own
`_with_resume_exists`/`_with_snapshot` pattern (one query for the whole
page, merged in Python) rather than a PostgREST embedded-select join,
which this codebase has no precedent for.
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


async def list_today_items(supabase: AsyncClient, user_id: str) -> list[dict[str, Any]]:
    result = (
        await supabase.table("today_items")
        .select("*")
        .eq("user_id", user_id)
        .is_("dismissed_at", "null")
        .order("created_at", desc=True)
        .execute()
    )
    return await _with_job_matches(supabase, cast(list[dict[str, Any]], result.data))


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
