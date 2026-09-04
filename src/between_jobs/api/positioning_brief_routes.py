"""HTTP surface for the positioning brief (outreach-v2-search-first.md
Phase I).
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends

from supabase import AsyncClient

from .app_state import get_http_client, get_supabase
from .applications_store import ApplicationNotFound, get_application, get_latest_prepare_result
from .auth import require_user_id
from .company_intel_store import get_claims_for_run as get_company_intel_claims
from .company_intel_store import get_latest_run as get_latest_company_intel_run
from .credential_resolver import resolve
from .errors import ApiError
from .jobs_store import SnapshotNotFound, get_snapshot
from .llm_client import generate as llm_generate
from .positioning_brief import generate_positioning_brief
from .positioning_brief_store import create_brief, get_latest_brief
from .profile_store import get_active_version
from .resume_documents_store import get_or_create_document
from .skills import canonicalize_skill, classify_skill
from .tailor_coverage import load_coverage_context

router = APIRouter(prefix="/applications/{application_id}/positioning-brief")


@router.get("")
async def get_brief(
    application_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    brief = await get_latest_brief(supabase, user_id, application_id)
    return {"brief": brief}


@router.post("", status_code=201)
async def generate_brief(
    application_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> dict[str, Any]:
    """Always runs a fresh pass, same "no idempotency key" reasoning as
    `company_intel_routes.generate_company_intel` and `contact_research_
    routes.generate_contacts` -- a refresh is a deliberate action with
    real cost (two real LLM calls: Step0 for live Tailor coverage, since
    nothing persists it, then this module's own picker) each time."""
    try:
        application = await get_application(supabase, user_id, application_id)
    except ApplicationNotFound as e:
        raise ApiError("NOT_FOUND", f"no application found for id {application_id!r}") from e

    try:
        job_snapshot = await get_snapshot(supabase, application["active_job_snapshot_id"])
    except SnapshotNotFound as e:
        raise ApiError("INTERNAL_ERROR", "This application's job snapshot is missing.") from e

    profile_version = await get_active_version(supabase, user_id)
    if profile_version is None:
        raise ApiError(
            "SETUP_REQUIRED",
            "Import and activate a resume profile before generating a positioning brief.",
            capability="profile",
            missing=["profile_version"],
            settings_path="/profile",
        )

    # Auto-creates the per-application resume_documents row on first use
    # (same as GET /resume-documents/mine's own get_or_create_document
    # call) -- a positioning brief shouldn't have a hard prerequisite on
    # the user having opened Tailor first.
    document = await get_or_create_document(
        supabase,
        user_id,
        application_id=application_id,
        profile_version_id=profile_version["id"],
        job_snapshot_id=job_snapshot["id"],
    )

    llm_credential = await resolve(supabase, user_id, capability="positioning_brief", service="llm")

    ctx = await load_coverage_context(supabase, http, user_id, document)
    skills = [
        {
            "skill": canonicalize_skill(term),
            "requested_as": term,
            "state": classify_skill(term, ctx.canonical_json),
        }
        for term in ctx.step0.key_terms
    ]

    company_intel_run = await get_latest_company_intel_run(supabase, user_id, application_id)
    company_intel_claims = (
        await get_company_intel_claims(supabase, company_intel_run["id"])
        if company_intel_run is not None
        else []
    )

    prepare_result = await get_latest_prepare_result(supabase, user_id, application_id)
    fit_data = prepare_result.get("fit") if prepare_result else None

    brief, warnings = await generate_positioning_brief(
        company=job_snapshot["company_name"],
        role_title=job_snapshot["title"],
        coverage=ctx.coverage,
        skills=skills,
        company_intel_claims=company_intel_claims,
        fit=fit_data,
        llm_api_key=llm_credential.secret,
        llm_model=llm_credential.model,
        llm_base_url=llm_credential.base_url,
        generate=llm_generate,
    )
    if brief is None:
        raise ApiError(
            "INSUFFICIENT_EVIDENCE",
            "Couldn't build a positioning brief yet -- " + "; ".join(warnings),
        )

    saved = await create_brief(
        supabase, user_id, application_id=application_id, brief=brief, rubric_warnings=warnings
    )
    return {"brief": saved}
