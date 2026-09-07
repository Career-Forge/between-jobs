"""Persistence for `approved_answers` -- browser-extension.md E1's
"known-question memory." User-scoped: an answer to a screening question is
a property of the person, reused across every application, matching
Proposal §25's own design and this table's own migration comment.

Implements the deterministic first two tiers of Proposal §25's 5-step
match order only (exact normalized question, then a deterministic
canonical-intent alias) -- semantic similarity matching (tier 3) is
explicitly deferred past v1 (browser-extension.md), since even the paid
tier of the market leader (Simplify+) does exact-string matching only, and
a wrongly-reused cached answer for a subtly different question is worse
than not matching at all.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from supabase import AsyncClient


async def match_approved_answer(
    supabase: AsyncClient,
    user_id: str,
    *,
    normalized_question: str,
    canonical_intent: str | None = None,
    jurisdiction: str | None = None,
) -> dict[str, Any] | None:
    """Tier 1: exact normalized-question match. Tier 2 (only tried if tier
    1 misses and a `canonical_intent` was supplied): the user's most
    recently updated answer under that same intent, regardless of its
    exact question wording. An expired answer (`expires_at` in the past)
    is never returned by either tier.

    `jurisdiction`, when supplied, additionally excludes any stored answer
    tagged with a DIFFERENT jurisdiction -- Proposal §25's own hard
    exclusion ("work-related eligibility facts when a job's jurisdiction
    differs"), confirmed by adversarial review as unenforced here entirely
    until this fix: the exact-question tier is included too, not just the
    intent tier, since an identically-worded eligibility question can
    legitimately appear on postings in two different jurisdictions. An
    answer with no jurisdiction tag was never jurisdiction-specific and is
    always eligible. When the caller doesn't supply a jurisdiction at all,
    a jurisdiction-tagged answer is conservatively excluded too -- "unknown
    means labeled as unknown," never guessed at as a match."""
    now_iso = datetime.now(UTC).isoformat()

    def jurisdiction_ok(row: dict[str, Any]) -> bool:
        row_jurisdiction = row.get("jurisdiction")
        return row_jurisdiction is None or row_jurisdiction == jurisdiction

    exact = (
        await supabase.table("approved_answers")
        .select("*")
        .eq("user_id", user_id)
        .eq("normalized_question", normalized_question)
        .or_(f"expires_at.is.null,expires_at.gt.{now_iso}")
        .execute()
    )
    exact_matches = [row for row in cast(list[dict[str, Any]], exact.data) if jurisdiction_ok(row)]
    if exact_matches:
        return exact_matches[0]

    if canonical_intent is None:
        return None

    by_intent = (
        await supabase.table("approved_answers")
        .select("*")
        .eq("user_id", user_id)
        .eq("canonical_intent", canonical_intent)
        .or_(f"expires_at.is.null,expires_at.gt.{now_iso}")
        .order("updated_at", desc=True)
        .execute()
    )
    intent_matches = [
        row for row in cast(list[dict[str, Any]], by_intent.data) if jurisdiction_ok(row)
    ]
    return intent_matches[0] if intent_matches else None


async def save_approved_answer(
    supabase: AsyncClient,
    user_id: str,
    *,
    normalized_question: str,
    answer_text: str,
    canonical_intent: str | None = None,
    evidence_fact_ids: list[str] | None = None,
    jurisdiction: str | None = None,
    sensitive_category: str | None = None,
    expires_at: str | None = None,
) -> dict[str, Any]:
    """Upsert on `unique(user_id, normalized_question)` -- approving an
    answer a second time (e.g. after editing its text) replaces the
    answer_text but PRESERVES previously-saved metadata (canonical_intent,
    evidence_fact_ids, jurisdiction, sensitive_category, expires_at) for
    any of those omitted on this call, rather than silently wiping it back
    to unset. Adversarial review confirmed a naive full-row upsert would
    otherwise let a plain "re-approve this edited answer" call quietly
    erase D6's own sensitive_category opt-in gate and the jurisdiction
    exclusion tag. A caller that genuinely wants to clear one of these
    fields has no way to do that through this function today -- an
    accepted v1 gap, since nothing needs to yet."""
    existing = (
        await supabase.table("approved_answers")
        .select("*")
        .eq("user_id", user_id)
        .eq("normalized_question", normalized_question)
        .execute()
    )
    existing_row = cast(dict[str, Any], existing.data[0]) if existing.data else None

    def merged(new_value: Any, column: str) -> Any:
        if new_value not in (None, []):
            return new_value
        return existing_row.get(column) if existing_row is not None else new_value

    result = (
        await supabase.table("approved_answers")
        .upsert(
            {
                "user_id": user_id,
                "normalized_question": normalized_question,
                "answer_text": answer_text,
                "canonical_intent": merged(canonical_intent, "canonical_intent"),
                "evidence_fact_ids": merged(evidence_fact_ids or [], "evidence_fact_ids"),
                "jurisdiction": merged(jurisdiction, "jurisdiction"),
                "sensitive_category": merged(sensitive_category, "sensitive_category"),
                "expires_at": merged(expires_at, "expires_at"),
            },
            on_conflict="user_id,normalized_question",
        )
        .execute()
    )
    return cast(dict[str, Any], result.data[0])


async def record_answer_used(supabase: AsyncClient, user_id: str, answer_id: str) -> None:
    """Best-effort usage counter -- not idempotency-critical, so a lost
    increment under a race is an acceptable, low-stakes gap (same shape as
    this project's other non-critical counters)."""
    current = (
        await supabase.table("approved_answers")
        .select("times_used")
        .eq("id", answer_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not current.data:
        return
    row = cast(dict[str, Any], current.data[0])
    await (
        supabase.table("approved_answers")
        .update({"times_used": row["times_used"] + 1})
        .eq("id", answer_id)
        .eq("user_id", user_id)
        .execute()
    )
