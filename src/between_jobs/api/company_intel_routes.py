"""HTTP surface for company intelligence (Horizon Sprint 5.0)."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends

from supabase import AsyncClient

from .app_state import get_http_client, get_supabase
from .applications_store import ApplicationNotFound, get_application
from .auth import require_user_id
from .company_intel_pipeline import (
    build_query_plan,
    build_role_fingerprint,
    run_research,
    synthesize_dossier,
)
from .company_intel_store import create_run, get_claims_for_run, get_latest_run
from .credential_resolver import resolve, try_get_secret
from .errors import ApiError
from .interview_registry import synthesize_interview_process_model
from .interview_registry_store import create_registry_entry
from .jobs_store import SnapshotNotFound, get_snapshot
from .llm_client import generate as llm_generate

router = APIRouter(prefix="/applications/{application_id}/company-intel")


@router.get("")
async def get_company_intel(
    application_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    run = await get_latest_run(supabase, user_id, application_id)
    if run is None:
        return {"run": None, "claims": []}
    claims = await get_claims_for_run(supabase, run["id"])
    return {"run": run, "claims": claims}


@router.post("", status_code=201)
async def generate_company_intel(
    application_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> dict[str, Any]:
    """Always runs a fresh research pass -- no idempotency key, unlike
    prepare_application. A refresh is a deliberate action with a real
    cost (search calls + one LLM call) each time, same as `/prepare`
    already accepts for resume generation; there's no "already ran this
    exact request" concept for research the way there is for a
    structured resume-prep command."""
    try:
        application = await get_application(supabase, user_id, application_id)
    except ApplicationNotFound as e:
        raise ApiError("NOT_FOUND", f"no application found for id {application_id!r}") from e

    try:
        job_snapshot = await get_snapshot(supabase, application["active_job_snapshot_id"])
    except SnapshotNotFound as e:
        raise ApiError("INTERNAL_ERROR", "This application's job snapshot is missing.") from e

    llm_credential = await resolve(supabase, user_id, capability="company_intel", service="llm")
    you_com_key = await try_get_secret(supabase, user_id, service="search", provider="you_com")
    firecrawl_key = await try_get_secret(supabase, user_id, service="search", provider="firecrawl")

    fingerprint = build_role_fingerprint(job_snapshot)
    queries = build_query_plan(fingerprint)
    results, providers_used, warnings = await run_research(
        http, queries, you_com_key=you_com_key, firecrawl_key=firecrawl_key
    )
    claims = await synthesize_dossier(
        fingerprint,
        results,
        llm_api_key=llm_credential.secret,
        llm_model=llm_credential.model,
        llm_base_url=llm_credential.base_url,
        generate=llm_generate,
    )

    run = await create_run(
        supabase,
        user_id,
        application_id=application_id,
        company_name=fingerprint["company"],
        claims=claims,
        providers_used=providers_used,
        warnings=warnings,
    )
    # `claims` here is still the pre-insert pipeline output, which has no
    # `id` (ids are DB-assigned via `gen_random_uuid()` on insert) -- read
    # the persisted rows back so this response matches what GET returns.
    persisted_claims = await get_claims_for_run(supabase, run["id"])

    # InterviewForge R1 (interviewforge-v1.md): the registry populates
    # itself for free off the SAME already-verified claims this route
    # already produces -- no separate user action, no new provider calls.
    # Best-effort and deliberately fail-open: this is a bonus side effect
    # of a request that already succeeded (the claims above are already
    # persisted regardless), so a synthesis-call outage must never fail
    # the whole company-intel response -- caught broadly on purpose, same
    # reasoning as C4's `_verify_claims` fail-open wrapper in forge-engines.
    try:
        process_model = await synthesize_interview_process_model(
            fingerprint["company"],
            claims,
            llm_api_key=llm_credential.secret,
            llm_model=llm_credential.model,
            llm_base_url=llm_credential.base_url,
            generate=llm_generate,
        )
    except Exception:  # deliberately broad -- see comment above
        process_model = None
    if process_model is not None:
        await create_registry_entry(
            supabase,
            company_name=fingerprint["company"],
            model=process_model,
            source_run_id=run["id"],
        )

    return {"run": run, "claims": persisted_claims}
