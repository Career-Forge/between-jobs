"""Persistence for canonical profile versions (Sprint 2.5c).

Split from profile.py's pure validation/import logic -- this module is the
only place that touches Supabase for profile_versions/career_facts. Both
the HTTP endpoints (app.py) and the Telegram flow (telegram_webhook.py,
Sprint 2.5d) call these functions directly rather than looping back
through HTTP -- there's one FastAPI process, and an in-process call is
strictly better than a self-referential network round trip.

Every function takes a verified `user_id` and filters by it explicitly in
the query -- this backend always uses the service-role client, which
bypasses RLS, so these WHERE clauses (not RLS) are the actual enforcement
boundary here. RLS stays on as defense-in-depth for a hypothetical future
direct-client path (e.g. Realtime), matching every other table in this
project.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from postgrest.types import CountMethod

from supabase import AsyncClient

from .profile import ImportedProfile


class VersionNotFound(Exception):
    """A version id doesn't exist, or exists but belongs to another user --
    deliberately indistinguishable from the caller's side: never leak
    whether an id exists for someone else."""


class VersionAlreadyActivated(Exception):
    """Raised when a caller tries to delete/cancel a version that's
    already activated -- versions are immutable once activated, by
    design (Proposal §15: content never changes after insert)."""


async def create_pending_version(
    supabase: AsyncClient,
    user_id: str,
    imported: ImportedProfile,
    source_kind: str,
    *,
    supersedes_id: str | None = None,
) -> dict[str, Any]:
    """Inserts an immutable version (`activated_at` null -- "pending") plus
    its derived career_facts. A pre-existing identical version (same
    content_hash) is returned as-is rather than duplicated -- the table's
    own `unique (user_id, content_hash)` constraint is what makes this
    safe to detect ahead of the insert.

    `supersedes_id` (S4b, honest-score-surfaces.md): the version this one
    was explicitly built on top of -- the Gap Interview's own apply step is
    the first caller to populate it. Column has existed since Proposal §15
    but nothing wrote to it before this; every other caller still omits it
    (defaults null), unchanged."""
    existing = (
        await supabase.table("profile_versions")
        .select("*")
        .eq("user_id", user_id)
        .eq("content_hash", imported.content_hash)
        .execute()
    )
    if existing.data:
        return cast(dict[str, Any], existing.data[0])

    row: dict[str, Any] = {
        "user_id": user_id,
        "schema_version": imported.schema_version,
        "source_kind": source_kind,
        "canonical_json": imported.canonical_json,
        "content_hash": imported.content_hash,
    }
    if supersedes_id is not None:
        row["supersedes_id"] = supersedes_id

    result = await supabase.table("profile_versions").insert(row).execute()
    version = cast(dict[str, Any], result.data[0])

    if imported.career_facts:
        await (
            supabase.table("career_facts")
            .insert(
                [
                    {
                        "user_id": user_id,
                        "profile_version_id": version["id"],
                        "fact_type": f.fact_type,
                        "entity_key": f.entity_key,
                        "value_json": f.value_json,
                        "source_pointer": f.source_pointer,
                    }
                    for f in imported.career_facts
                ]
            )
            .execute()
        )

    return version


async def get_version(supabase: AsyncClient, user_id: str, version_id: str) -> dict[str, Any]:
    result = (
        await supabase.table("profile_versions")
        .select("*")
        .eq("id", version_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise VersionNotFound(version_id)
    return cast(dict[str, Any], result.data[0])


async def list_career_facts(
    supabase: AsyncClient, user_id: str, version_id: str
) -> list[dict[str, Any]]:
    """The evidence a Tailor-mode evidence picker (Sprint 3.3) reads from --
    written once at import time (`create_pending_version`, above), never
    since. Scoped to a specific version, not "whatever's active now": a
    resume_document references one profile_version_id precisely so its
    evidence stays reproducible after the user's active profile changes
    (Proposal §24.5.1) -- callers that want the CURRENT profile's facts
    resolve the active version id first, same as any other version-scoped
    read."""
    result = (
        await supabase.table("career_facts")
        .select("*")
        .eq("profile_version_id", version_id)
        .eq("user_id", user_id)
        .execute()
    )
    return cast(list[dict[str, Any]], result.data)


async def get_active_version(supabase: AsyncClient, user_id: str) -> dict[str, Any] | None:
    """The user's current profile: the row with the latest non-null
    `activated_at`. Returns None if the user has never activated one."""
    result = (
        await supabase.table("profile_versions")
        .select("*")
        .eq("user_id", user_id)
        .not_.is_("activated_at", "null")
        .order("activated_at", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    return cast(dict[str, Any], result.data[0])


async def count_versions(supabase: AsyncClient, user_id: str) -> int:
    """Every version ever imported for a user, pending or activated --
    lets the edit UI say "Saving creates version N" honestly instead of
    inventing a number (Sprint 3.1e). `head=True` fetches only the count
    header, not the rows."""
    result = (
        await supabase.table("profile_versions")
        .select("id", count=CountMethod.exact, head=True)
        .eq("user_id", user_id)
        .execute()
    )
    return result.count or 0


async def activate_version(supabase: AsyncClient, user_id: str, version_id: str) -> dict[str, Any]:
    """Confirms a pending version as the user's current profile. Setting
    `activated_at` on an already-activated row is harmless and re-confirms
    it as the latest (supports deliberately re-activating an older
    version -- Proposal §15's "supersedes_id" reactivation case)."""
    result = (
        await supabase.table("profile_versions")
        .update({"activated_at": datetime.now(UTC).isoformat()})
        .eq("id", version_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise VersionNotFound(version_id)
    return cast(dict[str, Any], result.data[0])


async def delete_pending_version(supabase: AsyncClient, user_id: str, version_id: str) -> None:
    """Cancels a draft. Only ever deletes a version that was never
    activated -- career_facts cascade with it via the FK's ON DELETE
    CASCADE. Refuses to delete an activated version outright rather than
    silently no-op-ing, so a caller's mistaken "cancel" on the wrong id
    surfaces as an error instead of quietly doing the wrong thing."""
    version = await get_version(supabase, user_id, version_id)
    if version["activated_at"] is not None:
        raise VersionAlreadyActivated(version_id)

    await (
        supabase.table("profile_versions")
        .delete()
        .eq("id", version_id)
        .eq("user_id", user_id)
        .execute()
    )
