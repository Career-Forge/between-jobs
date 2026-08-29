"""Persistence for the interview-process registry (InterviewForge R1,
interviewforge-v1.md).

The first genuinely SHARED, cross-user table in this schema -- every other
table here is per-user RLS-private. Append-only, same immutable-run shape
`company_intel_runs` already uses: a new entry never edits or replaces a
prior one, "the registry" for a company is whichever entry is most recent.
That sidesteps concurrent-write conflict handling entirely -- two users
researching the same company around the same time just both produce a real,
valid entry; the read side always resolves to one via `order by created_at
desc limit 1`, exactly like `company_intel_runs` already does.
"""

from __future__ import annotations

from typing import Any, cast

from supabase import AsyncClient

from .interview_registry import InterviewProcessModel


async def create_registry_entry(
    supabase: AsyncClient,
    *,
    company_name: str,
    model: InterviewProcessModel,
    source_run_id: str,
) -> dict[str, Any]:
    result = (
        await supabase.table("interview_process_registry")
        .insert(
            {
                "company_name": company_name,
                "rounds": cast("list[dict[str, Any]]", model["rounds"]),
                "typical_topics": model["typical_topics"],
                "difficulty_signal": model["difficulty_signal"],
                "values_signals": model["values_signals"],
                "confidence": model["confidence"],
                "source_run_id": source_run_id,
            }
        )
        .execute()
    )
    return cast(dict[str, Any], result.data[0])


async def get_latest_registry_entry(
    supabase: AsyncClient, *, company_name: str
) -> dict[str, Any] | None:
    """No `user_id` filter -- this is the one table in this schema meant to
    be read across every user, not scoped to whoever wrote it."""
    result = (
        await supabase.table("interview_process_registry")
        .select("*")
        .eq("company_name", company_name)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    return cast(dict[str, Any], result.data[0])
