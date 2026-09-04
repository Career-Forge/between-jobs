"""Short-lived correlation tokens for OAuth redirect flows (outreach-
contactfinder.md Phase F). Google's OAuth callback is an unauthenticated
browser GET (no JWT attached, unlike every other route in this
codebase) -- this is the only way the callback route recovers which
between-jobs user started the flow. Single-use, 10-minute TTL, matching
`link_codes.py`'s own constant.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from supabase import AsyncClient

_STATE_TTL_SECONDS = 10 * 60


async def mint_state(supabase: AsyncClient, user_id: str, provider: str) -> str:
    state = secrets.token_urlsafe(32)
    await (
        supabase.table("oauth_states")
        .insert({"state": state, "user_id": user_id, "provider": provider})
        .execute()
    )
    return state


async def consume_state(supabase: AsyncClient, state: str, provider: str) -> str | None:
    """Returns the user_id this state was minted for, or None if the
    state doesn't exist, doesn't match this provider, or has expired.
    Deletes the row either way -- a state can never be replayed,
    successful or not."""
    result = await supabase.table("oauth_states").select("*").eq("state", state).execute()
    await supabase.table("oauth_states").delete().eq("state", state).execute()

    if not result.data:
        return None
    row = cast(dict[str, Any], result.data[0])
    if row["provider"] != provider:
        return None

    created_at = datetime.fromisoformat(row["created_at"])
    if datetime.now(UTC) - created_at > timedelta(seconds=_STATE_TTL_SECONDS):
        return None
    return cast(str, row["user_id"])
