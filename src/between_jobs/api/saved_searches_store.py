"""Persistence for saved searches (Job Finder P9a, today-feed-job-
matching.md) -- the only thing `saved_search_matcher.py` (P9b) scores
against. Plain per-user CRUD; the matcher itself owns `last_matched_at`
(this module doesn't set it except at creation).
"""

from __future__ import annotations

from typing import Any, cast

from supabase import AsyncClient


class SavedSearchNotFound(Exception):
    """A saved_search id doesn't exist, or belongs to another user --
    deliberately indistinguishable from the caller's side."""


async def create_saved_search(
    supabase: AsyncClient,
    user_id: str,
    *,
    query: str,
    location: str | None,
    companies: list[str],
    remote_only: bool,
) -> dict[str, Any]:
    result = (
        await supabase.table("saved_searches")
        .insert(
            {
                "user_id": user_id,
                "query": query,
                "location": location,
                "companies": companies,
                "remote_only": remote_only,
            }
        )
        .execute()
    )
    return cast(dict[str, Any], result.data[0])


async def get_saved_search(supabase: AsyncClient, user_id: str, search_id: str) -> dict[str, Any]:
    """Job Finder P10 (job-finder-p10-digest.md) -- lets `digest_listener.
    py` confirm a `job_registry.match_found.v1` event's own saved search
    still exists before inserting its Today item, the same "referenced
    entity already gone -> skip gracefully" check `get_application`
    already gives every other event type (a user can delete a saved
    search between the matcher publishing an event and the outbox
    worker processing it -- a real but rare race, not an error to
    surface, and not one to let corrupt an FK insert into a crash)."""
    result = (
        await supabase.table("saved_searches")
        .select("*")
        .eq("id", search_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise SavedSearchNotFound(search_id)
    return cast(dict[str, Any], result.data[0])


async def list_saved_searches(supabase: AsyncClient, user_id: str) -> list[dict[str, Any]]:
    result = (
        await supabase.table("saved_searches")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return cast(list[dict[str, Any]], result.data)


async def set_saved_search_active(
    supabase: AsyncClient, user_id: str, search_id: str, *, is_active: bool
) -> dict[str, Any]:
    result = (
        await supabase.table("saved_searches")
        .update({"is_active": is_active})
        .eq("id", search_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise SavedSearchNotFound(search_id)
    return cast(dict[str, Any], result.data[0])


async def delete_saved_search(supabase: AsyncClient, user_id: str, search_id: str) -> None:
    result = (
        await supabase.table("saved_searches")
        .delete()
        .eq("id", search_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise SavedSearchNotFound(search_id)
