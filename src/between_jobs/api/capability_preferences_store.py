"""Persistence for per-capability execution-mode/provider/model
preferences (Sprint 2.7d) -- Proposal §41-42.

Deliberately separate from provider_credentials (Sprint 2.7c) -- a
preference says WHAT a capability should use; a credential is the secret
that makes it possible. CredentialResolver (Sprint 2.7e) joins the two at
the moment of resolution, in Python, not via a SQL view or RPC -- this
backend always uses the service-role client and does its own user_id
filtering, same as every other store here, so there's no browser-facing
authorization boundary a SQL join would need to provide.
"""

from __future__ import annotations

from typing import Any, cast

from supabase import AsyncClient

DEFAULT_CAPABILITY = "default"
"""The fallback row a capability with no specific override resolves to."""


async def get_preference(
    supabase: AsyncClient, user_id: str, capability: str
) -> dict[str, Any] | None:
    result = (
        await supabase.table("capability_preferences")
        .select("*")
        .eq("user_id", user_id)
        .eq("capability", capability)
        .execute()
    )
    if not result.data:
        return None
    return cast(dict[str, Any], result.data[0])


async def list_preferences(supabase: AsyncClient, user_id: str) -> list[dict[str, Any]]:
    result = (
        await supabase.table("capability_preferences").select("*").eq("user_id", user_id).execute()
    )
    return cast(list[dict[str, Any]], result.data)


async def set_preference(
    supabase: AsyncClient,
    user_id: str,
    *,
    capability: str,
    execution_mode: str,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    result = (
        await supabase.table("capability_preferences")
        .upsert(
            {
                "user_id": user_id,
                "capability": capability,
                "execution_mode": execution_mode,
                "provider": provider,
                "model": model,
            },
            on_conflict="user_id,capability",
        )
        .execute()
    )
    return cast(dict[str, Any], result.data[0])


async def delete_preference(supabase: AsyncClient, user_id: str, capability: str) -> None:
    await (
        supabase.table("capability_preferences")
        .delete()
        .eq("user_id", user_id)
        .eq("capability", capability)
        .execute()
    )
