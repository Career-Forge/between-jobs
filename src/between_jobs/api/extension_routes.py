"""HTTP surface for the browser extension (browser-extension.md E1).

Selector-map serving is deliberately NOT here yet -- its real design
(signing, key management, D4's fail-closed verification behavior) depends
on what E2's unsigned dev-served map actually looks like once built, so it
lands in E3c. This module carries E1's URL lookup + known-question-memory
match/save, and E3b's LLM-answer draft endpoint.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from supabase import AsyncClient

from .app_state import get_supabase
from .application_answer_generator import (
    flagged_answer_warnings,
    generate_answer,
    is_generation_eligible,
    verify_answer_claims,
)
from .applications_store import ApplicationNotFound, find_application_by_url, get_application
from .auth import require_user_id
from .credential_resolver import resolve
from .errors import ApiError
from .extension_answers_store import match_approved_answer, save_approved_answer
from .job_fit_scoring import summarize_profile
from .jobs_store import SnapshotNotFound, get_snapshot
from .llm_client import generate as llm_generate
from .models import DraftAnswerRequest, MatchApprovedAnswerRequest, SaveApprovedAnswerRequest
from .profile import ResumeTemplate
from .profile_store import get_active_version

_ANSWER_GENERATION_CAPABILITY = "application_answer_generation"

router = APIRouter(prefix="/extension")


@router.get("/lookup")
async def lookup_application_by_url(
    url: str = Query(min_length=1),
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    application = await find_application_by_url(supabase, user_id, url)
    return {"application_id": application["id"] if application else None}


@router.post("/match-answer")
async def match_answer(
    body: MatchApprovedAnswerRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    answer = await match_approved_answer(
        supabase,
        user_id,
        normalized_question=body.normalized_question,
        canonical_intent=body.canonical_intent,
        jurisdiction=body.jurisdiction,
    )
    return {"answer": answer}


@router.post("/approved-answers", status_code=201)
async def save_answer(
    body: SaveApprovedAnswerRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    return await save_approved_answer(
        supabase,
        user_id,
        normalized_question=body.normalized_question,
        answer_text=body.answer_text,
        canonical_intent=body.canonical_intent,
        evidence_fact_ids=body.evidence_fact_ids,
        jurisdiction=body.jurisdiction,
        sensitive_category=body.sensitive_category,
        expires_at=body.expires_at,
    )


@router.post("/draft-answer")
async def draft_answer(
    body: DraftAnswerRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    """browser-extension.md E3b -- drafts, never fills or saves anything
    on its own. The extension always shows this draft (plus any
    grounding warnings) to the human for review/edit before it touches
    the real page or `approved_answers`, matching this project's own
    "surface, never auto-rewrite" precedent from C4.

    `eligible: False` (no LLM call made at all) covers both the
    deterministic length pre-filter and -- once a call IS made -- the
    model's own decision that the input wasn't really a question; both
    are real, disclosed v1 gaps in what this project can automatically
    tell apart, not a bug to route around."""
    try:
        application = await get_application(supabase, user_id, body.application_id)
    except ApplicationNotFound as e:
        raise ApiError("NOT_FOUND", f"no application found for id {body.application_id!r}") from e

    if not is_generation_eligible(body.question_text):
        return {"eligible": False, "answer_text": None, "declined_reason": None, "warnings": []}

    try:
        snapshot = await get_snapshot(supabase, application["active_job_snapshot_id"])
    except SnapshotNotFound as e:
        raise ApiError("INTERNAL_ERROR", "This application's job snapshot is missing.") from e
    job_description = snapshot.get("description_text") or ""

    profile_version = await get_active_version(supabase, user_id)
    if profile_version is None:
        raise ApiError(
            "SETUP_REQUIRED",
            "Import and activate a resume profile before drafting an answer.",
            capability="profile",
            missing=["profile_version"],
            settings_path="/profile",
        )
    profile = ResumeTemplate.model_validate(profile_version["canonical_json"])
    profile_summary = summarize_profile(profile)

    llm_credential = await resolve(supabase, user_id, capability=_ANSWER_GENERATION_CAPABILITY)

    generated = await generate_answer(
        question_text=body.question_text,
        profile_summary=profile_summary,
        job_description=job_description,
        llm_api_key=llm_credential.secret,
        llm_model=llm_credential.model,
        llm_base_url=llm_credential.base_url,
        generate=llm_generate,
    )
    if generated["answer_text"] is None:
        return {
            "eligible": True,
            "answer_text": None,
            "declined_reason": generated["declined_reason"],
            "warnings": [],
        }

    # Fail-open, deliberately broad: the draft above already succeeded and
    # is the real deliverable here -- a verification-call outage (rate
    # limit, provider hiccup) must never throw away a perfectly usable
    # draft the human is about to review anyway. Same reasoning as C4's
    # `_verify_claims` fail-open wrapper in forge-engines and company_intel_
    # routes.py's own synthesis-call wrapper; a verifier outage and
    # "nothing to flag" are indistinguishable downstream, same as there.
    try:
        verification = await verify_answer_claims(
            answer_text=generated["answer_text"],
            profile_summary=profile_summary,
            job_description=job_description,
            llm_api_key=llm_credential.secret,
            llm_model=llm_credential.model,
            llm_base_url=llm_credential.base_url,
            generate=llm_generate,
        )
        warnings = flagged_answer_warnings(verification)
    except Exception:  # deliberately broad -- see comment above
        warnings = []
    return {
        "eligible": True,
        "answer_text": generated["answer_text"],
        "declined_reason": None,
        "warnings": warnings,
    }
