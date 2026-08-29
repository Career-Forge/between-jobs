"""Persistence for working sets (Sprint 2.6e) -- Proposal §21.

"When the user says 'apply to #3,' the number belongs to a working set,
not global memory." Nothing resolves a spoken "#3" yet -- that's
Telegram's numbered-reference UX, Stage D -- this module only proves the
mechanic a future channel adapter will call into: mint a working set from
some ordered list of items, look it up, resolve an index against it, and
get a clear, typed reason when that fails (expired, out of range) instead
of a KeyError or a silently wrong item.

A working set is never revised in place -- a changed list becomes a new
working set with a fresh id, `version` stays at its DB default of 1.
Nothing manages `version` beyond that yet, matching the migration's own
"nothing needs it" note.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from supabase import AsyncClient

_DEFAULT_TTL_SECONDS = 30 * 60


class WorkingSetNotFound(Exception):
    """A working set id doesn't exist, or belongs to another user."""


class WorkingSetExpired(Exception):
    """The working set exists but its `expires_at` has passed -- per §21,
    this must surface as "requires clarification," never a stale item."""


class ReferenceOutOfRange(Exception):
    """The requested 1-based index doesn't exist in this working set."""


async def create_working_set(
    supabase: AsyncClient,
    user_id: str,
    *,
    kind: str,
    source_channel: str,
    items: list[Any],
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
    result = (
        await supabase.table("working_sets")
        .insert(
            {
                "user_id": user_id,
                "kind": kind,
                "source_channel": source_channel,
                "items": items,
                "expires_at": expires_at.isoformat(),
            }
        )
        .execute()
    )
    return cast(dict[str, Any], result.data[0])


async def get_working_set(
    supabase: AsyncClient, user_id: str, working_set_id: str
) -> dict[str, Any]:
    result = (
        await supabase.table("working_sets")
        .select("*")
        .eq("id", working_set_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise WorkingSetNotFound(working_set_id)
    return cast(dict[str, Any], result.data[0])


async def get_active_working_set(
    supabase: AsyncClient, user_id: str, kind: str
) -> dict[str, Any] | None:
    """The most recently created working set of this kind for this user,
    regardless of whether it has expired -- callers that care (e.g. a
    reference resolver) check `expires_at` themselves via
    `resolve_reference`, which is where §21 says expiry actually matters."""
    result = (
        await supabase.table("working_sets")
        .select("*")
        .eq("user_id", user_id)
        .eq("kind", kind)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    return cast(dict[str, Any], result.data[0])


async def resolve_reference(
    supabase: AsyncClient, user_id: str, working_set_id: str, index: int
) -> Any:
    """Resolves a 1-based reference like "#3" against a working set's
    `items`. Raises WorkingSetExpired or ReferenceOutOfRange rather than
    returning a wrong or missing item -- §21: "Expired or ambiguous
    references require clarification," not a best-effort guess."""
    working_set = await get_working_set(supabase, user_id, working_set_id)
    expires_at = datetime.fromisoformat(working_set["expires_at"])
    if datetime.now(UTC) >= expires_at:
        raise WorkingSetExpired(working_set_id)

    items = cast(list[Any], working_set["items"])
    if index < 1 or index > len(items):
        raise ReferenceOutOfRange(index)
    return items[index - 1]
