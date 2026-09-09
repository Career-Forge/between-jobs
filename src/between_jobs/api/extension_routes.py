"""HTTP surface for the browser extension (browser-extension.md E1).

This module carries E1's URL lookup + known-question-memory match/save,
E3b's LLM-answer draft endpoint, and E3c's signed field-map serving.
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
from .ats_field_maps import get_latest_field_map
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


@router.get("/field-maps/{ats_type}")
async def get_field_map(
    ats_type: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    """browser-extension.md E3c -- the curated, genuinely ATS-idiosyncratic
    part of the field map (never the generic name/email/phone/resume-
    selector defaults the extension already ships open-source). Auth-gated
    (`user_id` isn't otherwise used) for consistency and rate-limiting
    with every other `/extension/*` route, not because the map is secret
    -- the Ed25519 signature, not this auth check, is what the extension
    actually depends on for trust. Returns the exact `payload_canonical`
    bytes `scripts/sign_and_publish_ats_field_map.py` signed, untouched,
    so the extension's own signature check verifies the identical
    content -- this route never re-parses or re-serializes it. A missing
    row is a clean 404: unlike a genuine Supabase error (which propagates
    and 500s), an ATS with no published map is a real, distinct state
    worth telling apart from a backend fault."""
    row = await get_latest_field_map(supabase, ats_type)
    if row is None:
        raise ApiError("NOT_FOUND", f"no published field map for ats_type={ats_type!r}")
    return {
        "ats_type": row["ats_type"],
        "version": row["version"],
        "schema": row["schema"],
        "payload_canonical": row["payload_canonical"],
        "signature_b64": row["signature_b64"],
        "signing_key_id": row["signing_key_id"],
    }


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
