"""HTTP surface for ContactFinder (outreach-contactfinder.md Phase B/C/E/F)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends

from supabase import AsyncClient

from .app_state import get_http_client, get_supabase
from .applications_store import ApplicationNotFound, get_application
from .auth import require_user_id
from .contact_enrichment import enrich_candidate
from .contact_research import (
    build_contact_query_plan,
    find_contacts,
    guess_github_org_slug,
    run_contact_research,
)
from .contact_research_store import (
    CandidateNotFound,
    create_run,
    get_candidates_with_evidence,
    get_evidence_for_candidate,
    get_latest_run,
    get_owned_candidate,
    save_enrichment,
)
from .credential_resolver import resolve, try_get_secret
from .errors import ApiError
from .gmail_client import create_draft as create_gmail_draft
from .gmail_client import refresh_access_token, require_gmail_oauth_config
from .jobs_store import SnapshotNotFound, get_snapshot
from .llm_client import generate as llm_generate
from .outreach_writer import generate_outreach_draft
from .outreach_writer_store import create_draft, get_latest_draft, mark_pushed_to_gmail
from .provider_credentials_store import CredentialNotFound, get_decrypted_credential

router = APIRouter(prefix="/applications/{application_id}/contacts")


@router.get("")
async def get_contacts(
    application_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    run = await get_latest_run(supabase, user_id, application_id)
    if run is None:
        return {"run": None, "candidates": []}
    candidates = await get_candidates_with_evidence(supabase, run["id"])
    return {"run": run, "candidates": candidates}


@router.post("", status_code=201)
async def generate_contacts(
    application_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> dict[str, Any]:
    """Always runs a fresh research pass, same "no idempotency key"
    reasoning as `company_intel_routes.generate_company_intel` -- a
    refresh is a deliberate action with real cost (search calls plus one
    LLM call) each time."""
    try:
        application = await get_application(supabase, user_id, application_id)
    except ApplicationNotFound as e:
        raise ApiError("NOT_FOUND", f"no application found for id {application_id!r}") from e

    try:
        job_snapshot = await get_snapshot(supabase, application["active_job_snapshot_id"])
    except SnapshotNotFound as e:
        raise ApiError("INTERNAL_ERROR", "This application's job snapshot is missing.") from e

    llm_credential = await resolve(supabase, user_id, capability="contact_research", service="llm")
    you_com_key = await try_get_secret(supabase, user_id, service="search", provider="you_com")
    firecrawl_key = await try_get_secret(supabase, user_id, service="search", provider="firecrawl")

    company_name = job_snapshot["company_name"]
    queries = build_contact_query_plan(company_name, job_snapshot["title"])
    results, providers_used, warnings = await run_contact_research(
        http,
        queries,
        you_com_key=you_com_key,
        firecrawl_key=firecrawl_key,
        github_org_slug=guess_github_org_slug(company_name),
    )
    candidates = await find_contacts(
        company_name,
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
        company_name=company_name,
        candidates=candidates,
        providers_used=providers_used,
        warnings=warnings,
    )
    # Read persisted rows back, same reasoning as company_intel_routes --
    # `candidates` here has no DB-assigned ids yet.
    persisted_candidates = await get_candidates_with_evidence(supabase, run["id"])

    return {"run": run, "candidates": persisted_candidates}


@router.post("/{candidate_id}/enrich")
async def enrich_contact(
    application_id: str,
    candidate_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> dict[str, Any]:
    """Phase C -- opt-in, one candidate at a time, never part of
    discovery. `application_id` is only used for the URL's own scoping
    convention (matching every other nested route here); ownership is
    actually enforced by `get_owned_candidate` joining through the run,
    not by cross-checking `application_id` against the candidate."""
    try:
        candidate = await get_owned_candidate(supabase, user_id, candidate_id)
    except CandidateNotFound as e:
        raise ApiError("NOT_FOUND", f"no contact found for id {candidate_id!r}") from e

    apollo_key = await try_get_secret(supabase, user_id, service="search", provider="apollo")
    if apollo_key is None:
        raise ApiError(
            "SETUP_REQUIRED",
            "Contact enrichment needs an Apollo key configured.",
            capability="contact_enrichment",
            missing=["apollo_credential"],
            settings_path="/profile/integrations",
        )

    result = await enrich_candidate(
        http, api_key=apollo_key, person_name=candidate["person_name"], company=candidate["company"]
    )

    updated = await save_enrichment(
        supabase,
        candidate_id,
        email=result["email"],
        email_status=result["email_status"],
        provider="apollo",
        enriched_at=datetime.now(UTC).isoformat(),
    )
    return updated


@router.get("/{candidate_id}/draft-outreach")
async def get_outreach_draft(
    application_id: str,
    candidate_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    try:
        await get_owned_candidate(supabase, user_id, candidate_id)
    except CandidateNotFound as e:
        raise ApiError("NOT_FOUND", f"no contact found for id {candidate_id!r}") from e

    draft = await get_latest_draft(supabase, candidate_id)
    return {"draft": draft}


@router.post("/{candidate_id}/draft-outreach", status_code=201)
async def generate_outreach(
    application_id: str,
    candidate_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    """Phase E -- one candidate at a time, in-app only. No send
    integration exists here (Phase F, a separate OAuth surface) -- the
    draft is displayed/copyable, matching what both reference repos
    actually ship today."""
    try:
        candidate = await get_owned_candidate(supabase, user_id, candidate_id)
    except CandidateNotFound as e:
        raise ApiError("NOT_FOUND", f"no contact found for id {candidate_id!r}") from e

    try:
        application = await get_application(supabase, user_id, application_id)
    except ApplicationNotFound as e:
        raise ApiError("NOT_FOUND", f"no application found for id {application_id!r}") from e

    try:
        job_snapshot = await get_snapshot(supabase, application["active_job_snapshot_id"])
    except SnapshotNotFound as e:
        raise ApiError("INTERNAL_ERROR", "This application's job snapshot is missing.") from e

    evidence = await get_evidence_for_candidate(supabase, candidate_id)
    llm_credential = await resolve(supabase, user_id, capability="outreach_writer", service="llm")

    draft, warnings = await generate_outreach_draft(
        person_name=candidate["person_name"],
        claimed_title=candidate["claimed_title"],
        company=candidate["company"],
        role_title=job_snapshot["title"],
        evidence=evidence,
        llm_api_key=llm_credential.secret,
        llm_model=llm_credential.model,
        llm_base_url=llm_credential.base_url,
        generate=llm_generate,
    )
    if draft is None:
        raise ApiError(
            "INSUFFICIENT_EVIDENCE",
            "Couldn't draft an outreach message -- " + "; ".join(warnings),
        )

    saved = await create_draft(
        supabase, user_id, candidate_id=candidate_id, draft=draft, rubric_warnings=warnings
    )
    return {"draft": saved}


@router.post("/{candidate_id}/push-to-gmail")
async def push_outreach_to_gmail(
    application_id: str,
    candidate_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> dict[str, Any]:
    """Phase F -- lands the already-approved Phase E draft in the user's
    real Gmail as an actual draft. Never sends: the stored credential's
    own scope (`gmail.compose`, minted in gmail_oauth_routes.py) doesn't
    authorize Gmail's send endpoint at all, and this function never
    calls it regardless. Re-clicking after a successful push is a safe
    no-op (returns the existing state) rather than creating a second
    Gmail draft for the same message -- Proposal §28.7's own "no recent
    duplicate draft to the same person" rule, satisfied structurally by
    checking `gmail_draft_id` rather than a separate rate-limit table."""
    try:
        candidate = await get_owned_candidate(supabase, user_id, candidate_id)
    except CandidateNotFound as e:
        raise ApiError("NOT_FOUND", f"no contact found for id {candidate_id!r}") from e

    if not candidate.get("enriched_email"):
        raise ApiError(
            "INVALID_INPUT", "Find this contact's work email before creating a Gmail draft."
        )

    draft = await get_latest_draft(supabase, candidate_id)
    if draft is None:
        raise ApiError("INVALID_INPUT", "Draft an outreach message before creating a Gmail draft.")

    if draft.get("gmail_draft_id"):
        return draft

    try:
        credential = await get_decrypted_credential(
            supabase, user_id, service="oauth", provider="gmail"
        )
    except CredentialNotFound as e:
        raise ApiError(
            "SETUP_REQUIRED",
            "Connect Gmail before creating a draft there.",
            capability="gmail_draft",
            missing=["gmail_oauth"],
            settings_path="/profile/integrations",
        ) from e

    config = require_gmail_oauth_config()
    access_token = await refresh_access_token(
        http,
        refresh_token=credential["secret"],
        client_id=config["client_id"],
        client_secret=config["client_secret"],
    )
    gmail_draft_id = await create_gmail_draft(
        http,
        access_token=access_token,
        to_email=candidate["enriched_email"],
        subject=draft["subject"],
        body_text=draft["email_body"],
    )

    updated = await mark_pushed_to_gmail(
        supabase,
        draft["id"],
        gmail_draft_id=gmail_draft_id,
        pushed_to_gmail_at=datetime.now(UTC).isoformat(),
    )
    return updated
