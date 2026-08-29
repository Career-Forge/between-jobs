"""Persistence for interview-practice sessions (InterviewForge R3,
interviewforge-v1.md).

Per-user, unlike the shared `interview_process_registry` (R1) -- a practice
session is the candidate's own private record. `interview_session_questions`
carries no `user_id` of its own (same "no direct policy, reachable only by
joining through a [parent] the user owns" stance `company_intel_claims`
already uses) -- every function here that touches a question trusts its
caller already verified session ownership via `get_session`, exactly the
same trust boundary `get_claims_for_run` already relies on.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from supabase import AsyncClient

from .interview_practice import AnswerFeedback, PracticeQuestion


class InterviewSessionNotFound(Exception):
    """No session with this id exists for this user."""


async def create_session(
    supabase: AsyncClient,
    user_id: str,
    *,
    application_id: str,
    company_name: str,
    resume_evidence: str,
    registry_entry_id: str | None,
    questions: list[PracticeQuestion],
) -> dict[str, Any]:
    session_result = (
        await supabase.table("interview_sessions")
        .insert(
            {
                "user_id": user_id,
                "application_id": application_id,
                "company_name": company_name,
                "resume_evidence": resume_evidence,
                "registry_entry_id": registry_entry_id,
            }
        )
        .execute()
    )
    session = cast(dict[str, Any], session_result.data[0])

    if questions:
        await (
            supabase.table("interview_session_questions")
            .insert(
                [
                    {
                        "session_id": session["id"],
                        "ordinal": i,
                        "question_text": q["question"],
                        "question_type": q["type"],
                        "target_skill": q["target_skill"],
                        "grounded_in": q["grounded_in"],
                    }
                    for i, q in enumerate(questions)
                ]
            )
            .execute()
        )

    persisted_questions = await get_questions_for_session(supabase, session["id"])
    return {"session": session, "questions": persisted_questions}


async def get_questions_for_session(supabase: AsyncClient, session_id: str) -> list[dict[str, Any]]:
    result = (
        await supabase.table("interview_session_questions")
        .select("*")
        .eq("session_id", session_id)
        .order("ordinal")
        .execute()
    )
    return cast(list[dict[str, Any]], result.data)


async def get_session(supabase: AsyncClient, user_id: str, session_id: str) -> dict[str, Any]:
    """Raises, rather than returning `None` -- fetching THIS SPECIFIC
    session by id (like `applications_store.get_application`), not reading
    "the current state, which may legitimately not exist yet" (like
    `company_intel_store.get_latest_run`). A session id the caller already
    has should exist and be owned by them; if it doesn't, that's a real
    404, not a normal empty state."""
    result = (
        await supabase.table("interview_sessions")
        .select("*")
        .eq("id", session_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise InterviewSessionNotFound(session_id)
    return cast(dict[str, Any], result.data[0])


async def list_sessions(
    supabase: AsyncClient, user_id: str, application_id: str
) -> list[dict[str, Any]]:
    result = (
        await supabase.table("interview_sessions")
        .select("*")
        .eq("user_id", user_id)
        .eq("application_id", application_id)
        .order("started_at", desc=True)
        .execute()
    )
    return cast(list[dict[str, Any]], result.data)


async def get_next_unanswered_question(
    supabase: AsyncClient, session_id: str
) -> dict[str, Any] | None:
    result = (
        await supabase.table("interview_session_questions")
        .select("*")
        .eq("session_id", session_id)
        .is_("answered_at", "null")
        .order("ordinal")
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    return cast(dict[str, Any], result.data[0])


async def record_answer(
    supabase: AsyncClient, question_id: str, *, answer_text: str, feedback: AnswerFeedback
) -> dict[str, Any]:
    result = (
        await supabase.table("interview_session_questions")
        .update(
            {
                "answer_text": answer_text,
                "score": feedback["score"],
                "feedback": cast("dict[str, Any]", dict(feedback)),
                "answered_at": datetime.now(UTC).isoformat(),
            }
        )
        .eq("id", question_id)
        .execute()
    )
    return cast(dict[str, Any], result.data[0])


async def complete_session(supabase: AsyncClient, session_id: str) -> dict[str, Any]:
    result = (
        await supabase.table("interview_sessions")
        .update({"status": "completed", "completed_at": datetime.now(UTC).isoformat()})
        .eq("id", session_id)
        .execute()
    )
    return cast(dict[str, Any], result.data[0])
