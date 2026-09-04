"""Persistence for positioning briefs (outreach-v2-search-first.md Phase
I) -- directly user-owned, immutable, "current = most recent" for one
application, same convention as `outreach_writer_store.py`.
"""

from __future__ import annotations

from typing import Any, cast

from supabase import AsyncClient

from .positioning_brief import PositioningBrief


async def create_brief(
    supabase: AsyncClient,
    user_id: str,
    *,
    application_id: str,
    brief: PositioningBrief,
    rubric_warnings: list[str],
) -> dict[str, Any]:
    result = (
        await supabase.table("positioning_briefs")
        .insert(
            {
                "user_id": user_id,
                "application_id": application_id,
                "lead_with": brief["lead_with"],
                "lead_with_citation": brief["lead_with_citation"],
                "gap_that_matters": brief["gap_that_matters"],
                "gap_citation": brief["gap_citation"],
                "recommended_project": brief["recommended_project"],
                "rubric_warnings": rubric_warnings,
            }
        )
        .execute()
    )
    return cast(dict[str, Any], result.data[0])


async def get_latest_brief(
    supabase: AsyncClient, user_id: str, application_id: str
) -> dict[str, Any] | None:
    """`user_id` is a real filter, not decoration -- this backend queries
    with the service-role key (RLS never gates its own reads), so an
    ownership check missing here is a real cross-user data leak, not a
    defense-in-depth nicety. Mirrors `company_intel_store.get_latest_run`
    and `contact_research_store.get_latest_run`'s own exact double-`eq`
    shape for the identical "latest run for this application" pattern."""
    result = (
        await supabase.table("positioning_briefs")
        .select("*")
        .eq("user_id", user_id)
        .eq("application_id", application_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    return cast(dict[str, Any], result.data[0])
