"""HTTP surface for the browser extension (browser-extension.md E1).

This module carries E1's URL lookup + known-question-memory match/save,
E3b's LLM-answer draft endpoint, E3c's signed field-map serving, and
(E6 continuation) the extension-scoped sign-out liveness check.

Every route below depends on `require_active_extension_user_id`
(extension_auth.py), never plain `require_user_id` -- see that module's
own docstring for why this router gets its own auth dependency instead of
widening the app's general one.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query, Response

from supabase import AsyncClient

from .app_state import get_http_client, get_supabase
from .application_answer_generator import (
    flagged_answer_warnings,
    generate_answer,
    is_generation_eligible,
    is_sensitive_self_id_text,
    verify_answer_claims,
)
from .applications_store import ApplicationNotFound, find_application_by_url, get_application
from .ats_field_maps import get_latest_field_map
from .credential_resolver import resolve
from .errors import ApiError
from .extension_answers_store import match_approved_answer, save_approved_answer
from .extension_auth import record_extension_sign_out, require_active_extension_user_id
from .extension_rate_limit import claim_draft_answer_slot
from .job_fit_scoring import summarize_profile
from .jobs_store import SnapshotNotFound, get_snapshot
from .llm_client import generate as llm_generate
from .models import DraftAnswerRequest, MatchApprovedAnswerRequest, SaveApprovedAnswerRequest
from .prepare_orchestrator import latest_cover_letter_pdf, latest_resume_pdf
from .profile import ResumeTemplate
from .profile_store import get_active_version

_ANSWER_GENERATION_CAPABILITY = "application_answer_generation"

router = APIRouter(prefix="/extension")


@router.get("/lookup")
async def lookup_application_by_url(
    url: str = Query(min_length=1),
    user_id: str = Depends(require_active_extension_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    application = await find_application_by_url(supabase, user_id, url)
    return {"application_id": application["id"] if application else None}


@router.post("/sign-out", status_code=204)
async def sign_out(
    user_id: str = Depends(require_active_extension_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> None:
    """E6 continuation -- the scoped "server-side revocation" the original
    spec asked for, built exactly as narrow as Pranav's go-ahead: this
    records "signed out now" for the caller, so every extension token
    issued before this call (this one included -- see
    `extension_auth.record_extension_sign_out`'s own docstring) is
    rejected by `require_active_extension_user_id` from this point on.
    Nothing else in the app reads this table, so the web session and
    every other route are entirely unaffected -- see
    `tests/test_extension_auth.py`."""
    await record_extension_sign_out(supabase, user_id)


@router.get("/{application_id}/resume.pdf")
async def download_resume_pdf(
    application_id: str,
    user_id: str = Depends(require_active_extension_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> Response:
    """E6 continuation, part 2 -- extension-only mirror of
    `applications_routes.download_resume_pdf`, gated by
    `require_active_extension_user_id` instead of `require_user_id`. Real
    gap this closes: `sign_out` above revokes an extension token for every
    OTHER `/extension/*` route, but before this route existed the actual
    Fill flow fetched the résumé PDF from the WEB APP's own
    `/applications/{id}/resume.pdf` -- which is genuinely shared with
    `GeneratePanel.tsx`'s own "Download resume" button and stays on
    `require_user_id` on purpose, since gating a route the web app also
    calls on the extension's own sign-out state would 401 an ordinary web
    download for anyone who has ever signed out of the extension. This
    route reuses the identical fetch (`prepare_orchestrator.
    latest_resume_pdf`) so there is no second implementation to drift from
    the web app's own; only the auth dependency and the URL differ. The
    extension's `background.ts` calls this one, never the web app's."""
    _version_row, pdf_bytes = await latest_resume_pdf(supabase, http, user_id, application_id)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="resume.pdf"'},
    )


@router.get("/{application_id}/cover-letter.pdf")
async def download_cover_letter_pdf(
    application_id: str,
    user_id: str = Depends(require_active_extension_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> Response:
    """E6 continuation, part 2 -- same shape and same reasoning as
    `download_resume_pdf` right above, for the cover letter."""
    _version_row, pdf_bytes = await latest_cover_letter_pdf(supabase, http, user_id, application_id)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="cover-letter.pdf"'},
    )


@router.get("/field-maps/{ats_type}")
async def get_field_map(
    ats_type: str,
    user_id: str = Depends(require_active_extension_user_id),
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
    user_id: str = Depends(require_active_extension_user_id),
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
    user_id: str = Depends(require_active_extension_user_id),
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
    user_id: str = Depends(require_active_extension_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    """browser-extension.md E3b -- drafts, never fills or saves anything
    on its own. The extension always shows this draft (plus any
    grounding warnings) to the human for review/edit before it touches
    the real page or `approved_answers`, matching this project's own
    "surface, never auto-rewrite" precedent from C4.

    `eligible: False` (no LLM call made at all) covers three independent
    reasons, none distinguished from each other in the response -- real,
    disclosed v1 gaps in what this project can automatically tell apart,
    not a bug to route around: the deterministic length pre-filter
    (`is_generation_eligible`), the server-side D6 topic gate
    (`is_sensitive_self_id_text` -- E6 continuation: the extension's own
    client-side D6 check already keeps a real self-ID question off this
    path under normal use, but nothing before this stopped a modified
    client or a direct API call from sending one anyway), and -- once a
    call IS made -- the model's own decision that the input wasn't really
    a question."""
    try:
        application = await get_application(supabase, user_id, body.application_id)
    except ApplicationNotFound as e:
        raise ApiError("NOT_FOUND", f"no application found for id {body.application_id!r}") from e

    if not is_generation_eligible(body.question_text) or is_sensitive_self_id_text(
        body.question_text
    ):
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

    # extension_rate_limit.py -- checked here, right before the real LLM
    # spend begins, so a request that was going to be declined above
    # (ineligible or D6) never counts against the caller's own budget.
    if not await claim_draft_answer_slot(supabase, user_id):
        raise ApiError(
            "PROVIDER_RATE_LIMITED",
            "You're drafting answers too quickly -- wait a bit and try again.",
            retryable=True,
        )

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
