"""Persistence for the Today feed (Horizon Sprint 4.0) -- Proposal §37.1,
scoped per `digest_listener.py`'s own docstring. Read-and-dismiss only:
`today_items` rows are written exclusively by the digest listener, never
by a route handling a direct user request.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from supabase import AsyncClient


class TodayItemNotFound(Exception):
    """A today_item id doesn't exist, or belongs to another user --
    deliberately indistinguishable from the caller's side."""


async def list_today_items(supabase: AsyncClient, user_id: str) -> list[dict[str, Any]]:
    result = (
        await supabase.table("today_items")
        .select("*")
        .eq("user_id", user_id)
        .is_("dismissed_at", "null")
        .order("created_at", desc=True)
        .execute()
    )
    return cast(list[dict[str, Any]], result.data)


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
