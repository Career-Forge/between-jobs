"""Resume storage (Sprint 2.4).

Stores the raw text as given -- no parsing/structuring yet. Turning free
text into forge-engines' structured resume_template is separate, real
scope (needs an extraction step) that belongs with actually wiring up
apply/score, not with getting storage working. "One current resume per
user" is enforced by user_id being the `resumes` table's own primary
key, not an application-level check.
"""

from __future__ import annotations

from typing import Any, cast

from supabase import AsyncClient


async def save_resume(supabase: AsyncClient, user_id: str, raw_text: str) -> None:
    # PostgREST's default upsert conflict target is the table's primary
    # key -- user_id here -- so no explicit on_conflict is needed.
    await supabase.table("resumes").upsert({"user_id": user_id, "raw_text": raw_text}).execute()


async def get_resume(supabase: AsyncClient, user_id: str) -> dict[str, Any] | None:
    result = (
        await supabase.table("resumes")
        .select("raw_text, updated_at")
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        return None
    return cast(dict[str, Any], result.data[0])
