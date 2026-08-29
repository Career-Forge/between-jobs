"""HTTP surface for interview-practice sessions (InterviewForge R3,
interviewforge-v1.md).

This is where R2's engine (`interview_practice.py`) finally runs through
the real credential-resolution flow -- `credential_resolver.resolve` only
works inside a route handler, so this is also where R2's own LLM calls get
their first live verification, same split CoverForge's C1 -> C2 already
used.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import httpx
from fastapi import APIRouter, Depends

from supabase import AsyncClient

from .app_state import get_http_client, get_supabase
from .applications_store import ApplicationNotFound, get_application
from .auth import require_user_id
from .credential_resolver import resolve
from .errors import ApiError
from .forge_engines_client import call_ingest, call_personal
from .interview_practice import (
    AnswerFeedback,
    PracticeQuestion,
    ScoredAnswer,
    build_practice_context,
    build_session_report,
    coerce_question_type,
    generate_practice_questions,
    score_answer,
)
from .interview_practice_store import (
    InterviewSessionNotFound,
    complete_session,
    create_session,
    get_next_unanswered_question,
    get_questions_for_session,
    get_session,
    list_sessions,
    record_answer,
)
from .interview_registry import InterviewProcessModel
from .interview_registry_store import get_latest_registry_entry
from .jobs_store import SnapshotNotFound, get_snapshot
from .llm_client import generate as llm_generate
from .models import SubmitInterviewAnswerRequest
from .profile_store import get_active_version

router = APIRouter(prefix="/applications/{application_id}/interview-practice")

_CAPABILITY = "interview_practice"


def _registry_model_from_row(row: dict[str, Any]) -> InterviewProcessModel:
    return InterviewProcessModel(
        company_name=row["company_name"],
        rounds=row.get("rounds") or [],
        typical_topics=row.get("typical_topics") or [],
        difficulty_signal=row.get("difficulty_signal") or "unknown",
        values_signals=row.get("values_signals") or [],
        confidence=row.get("confidence") or "low",
    )


def _practice_question_from_row(row: dict[str, Any]) -> PracticeQuestion:
    return PracticeQuestion(
        question=row["question_text"],
        type=coerce_question_type(row.get("question_type")),
        target_skill=row.get("target_skill") or "",
        grounded_in=row.get("grounded_in"),
    )


async def _job_context_for(job_snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_title": job_snapshot.get("title") or "",
        "company": job_snapshot.get("company_name") or "",
        "location": job_snapshot.get("location_text") or "",
        "job_description": job_snapshot.get("description_text") or "",
    }


async def _resume_evidence_for(
    http: httpx.AsyncClient,
    supabase: AsyncClient,
    user_id: str,
    job_context: dict[str, Any],
) -> str:
    profile_version = await get_active_version(supabase, user_id)
    if profile_version is None:
        raise ApiError(
            "SETUP_REQUIRED",
            "Import and activate a resume profile before practicing interviews.",
            capability="profile",
            missing=["profile_version"],
            settings_path="/profile",
        )
    resume_doc = await call_ingest(
        http, template=profile_version["canonical_json"], now=datetime.now(UTC).isoformat()
    )
    personal_result = await call_personal(http, resume_doc=resume_doc, job_context=job_context)
    return cast(str, personal_result["resume_text"])


async def _application_and_snapshot(
    supabase: AsyncClient, user_id: str, application_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        application = await get_application(supabase, user_id, application_id)
    except ApplicationNotFound as e:
        raise ApiError("NOT_FOUND", f"no application found for id {application_id!r}") from e
    try:
        job_snapshot = await get_snapshot(supabase, application["active_job_snapshot_id"])
    except SnapshotNotFound as e:
        raise ApiError("INTERNAL_ERROR", "This application's job snapshot is missing.") from e
    return application, job_snapshot


@router.post("/sessions", status_code=201)
async def start_practice_session(
    application_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> dict[str, Any]:
    _application, job_snapshot = await _application_and_snapshot(supabase, user_id, application_id)
    company_name = job_snapshot.get("company_name") or ""
    job_context = await _job_context_for(job_snapshot)

    llm_credential = await resolve(supabase, user_id, capability=_CAPABILITY)
    resume_evidence = await _resume_evidence_for(http, supabase, user_id, job_context)

    registry_row = await get_latest_registry_entry(supabase, company_name=company_name)
    registry_entry = _registry_model_from_row(registry_row) if registry_row else None

    context = build_practice_context(
        company_name=company_name,
        job_title=job_snapshot.get("title") or "",
        job_description=job_snapshot.get("description_text") or "",
        resume_evidence=resume_evidence,
        registry_entry=registry_entry,
    )
    questions = await generate_practice_questions(
        context,
        llm_api_key=llm_credential.secret,
        llm_model=llm_credential.model,
        llm_base_url=llm_credential.base_url,
        generate=llm_generate,
    )
    if not questions:
        raise ApiError(
            "RUN_FAILED", "Couldn't generate practice questions for this role. Try again."
        )

    return await create_session(
        supabase,
        user_id,
        application_id=application_id,
        company_name=company_name,
        resume_evidence=resume_evidence,
        registry_entry_id=registry_row["id"] if registry_row else None,
        questions=questions,
    )


@router.get("/sessions")
async def list_practice_sessions(
    application_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    sessions = await list_sessions(supabase, user_id, application_id)
    return {"sessions": sessions}


@router.get("/sessions/{session_id}")
async def get_practice_session(
    application_id: str,
    session_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    session = await _owned_session(supabase, user_id, application_id, session_id)
    questions = await get_questions_for_session(supabase, session_id)
    return {"session": session, "questions": questions}


async def _owned_session(
    supabase: AsyncClient, user_id: str, application_id: str, session_id: str
) -> dict[str, Any]:
    try:
        session = await get_session(supabase, user_id, session_id)
    except InterviewSessionNotFound as e:
        raise ApiError("NOT_FOUND", f"no interview session found for id {session_id!r}") from e
    if session["application_id"] != application_id:
        raise ApiError("NOT_FOUND", f"no interview session found for id {session_id!r}")
    return session


@router.post("/sessions/{session_id}/answers", status_code=201)
async def submit_practice_answer(
    application_id: str,
    session_id: str,
    body: SubmitInterviewAnswerRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    session = await _owned_session(supabase, user_id, application_id, session_id)

    question_row = await get_next_unanswered_question(supabase, session_id)
    if question_row is None:
        raise ApiError("RUN_FAILED", "This session has no unanswered questions left.")
    practice_question = _practice_question_from_row(question_row)

    llm_credential = await resolve(supabase, user_id, capability=_CAPABILITY)
    feedback = await score_answer(
        practice_question,
        body.answer_text,
        session["resume_evidence"],
        llm_api_key=llm_credential.secret,
        llm_model=llm_credential.model,
        llm_base_url=llm_credential.base_url,
        generate=llm_generate,
    )
    if feedback is None:
        raise ApiError("RUN_FAILED", "Couldn't score that answer. Try again.")

    await record_answer(
        supabase, question_row["id"], answer_text=body.answer_text, feedback=feedback
    )

    remaining = await get_next_unanswered_question(supabase, session_id)
    session_report = None
    if remaining is None:
        await complete_session(supabase, session_id)
        all_questions = await get_questions_for_session(supabase, session_id)
        scored = [
            ScoredAnswer(
                question=_practice_question_from_row(q),
                answer_text=q["answer_text"],
                feedback=cast(AnswerFeedback, q["feedback"]),
            )
            for q in all_questions
            if q["answered_at"] is not None
        ]
        session_report = build_session_report(len(all_questions), scored)

    return {"feedback": feedback, "next_question": remaining, "session_report": session_report}
