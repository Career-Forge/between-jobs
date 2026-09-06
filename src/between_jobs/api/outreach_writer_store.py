"""Persistence for outreach drafts (outreach-contactfinder.md Phase E) --
directly user-owned, immutable, "current = most recent" for one
candidate, same convention as every other run-shaped table in this
codebase.
"""

from __future__ import annotations

from typing import Any, cast

from supabase import AsyncClient

from .outreach_writer import OutreachDraft


async def create_draft(
    supabase: AsyncClient,
    user_id: str,
    *,
    candidate_id: str,
    draft: OutreachDraft,
    rubric_warnings: list[str],
) -> dict[str, Any]:
    result = (
        await supabase.table("outreach_drafts")
        .insert(
            {
                "user_id": user_id,
                "candidate_id": candidate_id,
                "subject": draft["subject"],
                "email_body": draft["email_body"],
                "linkedin_message": draft["linkedin_message"],
                "follow_up_message": draft["follow_up_message"],
                "hook_evidence_id": draft["hook_evidence_id"],
                "rubric_warnings": rubric_warnings,
            }
        )
        .execute()
    )
    return cast(dict[str, Any], result.data[0])


async def get_latest_draft(supabase: AsyncClient, candidate_id: str) -> dict[str, Any] | None:
    result = (
        await supabase.table("outreach_drafts")
        .select("*")
        .eq("candidate_id", candidate_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    return cast(dict[str, Any], result.data[0])


async def mark_pushed_to_gmail(
    supabase: AsyncClient,
    draft_id: str,
    *,
    gmail_draft_id: str,
    gmail_thread_id: str,
    pushed_to_gmail_at: str,
) -> dict[str, Any]:
    """Phase F -- records that this exact outreach draft became a real
    Gmail draft, so a re-click on the same candidate returns the
    existing state (idempotent) instead of creating a second Gmail
    draft for the same message. `gmail_thread_id` (outreach-v2-search-
    first.md's Gmail reply/status parsing) is the one value the reply-
    checker poller needs on hand to ever ask Gmail what's happened in
    this thread since -- captured here rather than at poll time, since
    Gmail assigns it at draft-creation, before any send happens."""
    result = (
        await supabase.table("outreach_drafts")
        .update(
            {
                "gmail_draft_id": gmail_draft_id,
                "gmail_thread_id": gmail_thread_id,
                "pushed_to_gmail_at": pushed_to_gmail_at,
            }
        )
        .eq("id", draft_id)
        .execute()
    )
    return cast(dict[str, Any], result.data[0])
