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
from .company_intel_store import get_claims_for_run as get_company_intel_claims
from .company_intel_store import get_latest_run as get_latest_company_intel_run
from .company_tiers import get_company_tier_index
from .contact_enrichment import EnrichmentResult, enrich_candidate, enrich_hunter, find_linkedin_exa
from .contact_research import (
    apply_l2_search_filters,
    build_contact_query_plan,
    extract_product_term_candidates,
    find_contacts,
    guess_github_org_slug,
    pick_product_terms,
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
    save_linkedin_discovery,
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

    # Phase H -- per-company product vocabulary. A prior Company Intel run
    # is optional infrastructure (a user may never have run it for this
    # application); its absence just means an empty claims list, same
    # null-run handling company_intel_routes.py itself already uses.
    company_intel_run = await get_latest_company_intel_run(supabase, user_id, application_id)
    company_intel_claims = (
        await get_company_intel_claims(supabase, company_intel_run["id"])
        if company_intel_run is not None
        else []
    )
    product_term_candidates = extract_product_term_candidates(
        company_name,
        job_snapshot["description_text"],
        *(claim["claim_text"] for claim in company_intel_claims),
    )
    product_terms = await pick_product_terms(
        product_term_candidates,
        llm_api_key=llm_credential.secret,
        llm_model=llm_credential.model,
        llm_base_url=llm_credential.base_url,
        generate=llm_generate,
    )

    # Phase K -- the X/Twitter "we're hiring" lane only fires for a
    # company NOT found in the Fortune-500-based tier index. An empty
    # index (the fetch failed, or the table itself is genuinely empty)
    # must never be read as "every company is a startup" -- that would
    # silently fire this lane against a real Fortune-500 company the
    # moment the tier data fails to load, the same class of fail-open
    # signal corruption already fixed for the location filter elsewhere
    # in this codebase.
    tier_index = await get_company_tier_index(supabase)
    include_x_lane = (
        bool(tier_index.weights_by_normalized_name) and tier_index.lookup(company_name) is None
    )

    queries = build_contact_query_plan(
        company_name,
        job_snapshot["title"],
        product_terms=product_terms,
        include_x_lane=include_x_lane,
    )
    results, providers_used, warnings = await run_contact_research(
        http,
        queries,
        you_com_key=you_com_key,
        firecrawl_key=firecrawl_key,
        github_org_slug=guess_github_org_slug(company_name),
    )
    results, _dropped = apply_l2_search_filters(results, company=company_name)
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
        product_terms=product_terms,
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
    not by cross-checking `application_id` against the candidate.

    Phase K adds Hunter as a second provider, tried automatically -- not
    a separate button -- so the existing one-click UX gains resilience
    and yield for free. Apollo stays primary (unchanged behavior for
    every user who only has Apollo configured): tried first when
    configured, and its own failure (a bad key, a provider outage) only
    falls through to Hunter when Hunter is ALSO configured -- a user
    without Hunter still sees Apollo's own real error, exactly as
    before. When Apollo succeeds but finds no email, Hunter is tried as
    a genuine second attempt rather than accepting the first provider's
    "not found" as final.

    An adversarial review caught that the Hunter call itself was
    originally unguarded: if Apollo already produced a valid (if empty)
    result and Hunter then failed (a revoked key, an outage), the whole
    request used to hard-error and discard Apollo's own completed
    lookup -- a real regression for any user who configures Hunter, the
    opposite of Hunter adding resilience "for free." Hunter's own
    failure is now swallowed specifically when Apollo already produced a
    result (keeping Apollo's own answer, including a legitimate "no
    email found"); it still propagates when Hunter was the only real
    attempt (Apollo not configured, or Apollo itself failed too) --
    matching this same function's own established "surface the last
    real provider's error rather than a silent success" stance."""
    try:
        candidate = await get_owned_candidate(supabase, user_id, candidate_id)
    except CandidateNotFound as e:
        raise ApiError("NOT_FOUND", f"no contact found for id {candidate_id!r}") from e

    apollo_key = await try_get_secret(supabase, user_id, service="search", provider="apollo")
    hunter_key = await try_get_secret(supabase, user_id, service="search", provider="hunter")
    if apollo_key is None and hunter_key is None:
        raise ApiError(
            "SETUP_REQUIRED",
            "Contact enrichment needs an Apollo or Hunter key configured.",
            capability="contact_enrichment",
            missing=["apollo_credential", "hunter_credential"],
            settings_path="/profile/integrations",
        )

    result: EnrichmentResult | None = None
    provider_used: str | None = None

    if apollo_key is not None:
        try:
            result = await enrich_candidate(
                http,
                api_key=apollo_key,
                person_name=candidate["person_name"],
                company=candidate["company"],
            )
            provider_used = "apollo"
        except ApiError:
            if hunter_key is None:
                raise

    if hunter_key is not None and (result is None or result["email"] is None):
        try:
            hunter_result = await enrich_hunter(
                http,
                api_key=hunter_key,
                person_name=candidate["person_name"],
                company=candidate["company"],
            )
            result = hunter_result
            provider_used = "hunter"
        except ApiError:
            if result is None:
                raise

    assert result is not None and provider_used is not None  # a key existed, so one branch ran

    updated = await save_enrichment(
        supabase,
        candidate_id,
        email=result["email"],
        email_status=result["email_status"],
        provider=provider_used,
        enriched_at=datetime.now(UTC).isoformat(),
    )
    return updated


@router.post("/{candidate_id}/find-linkedin")
async def find_linkedin(
    application_id: str,
    candidate_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> dict[str, Any]:
    """Phase K -- Exa People Search, opt-in and human-gated, same shape
    as `enrich_contact` above. A distinct action, not folded into
    `enrich_contact`: Exa never resolves an email (a different result
    entirely), and a candidate found via a non-LinkedIn source (a GitHub
    org membership, a director/VP mention, an X hiring post) may have no
    LinkedIn URL in its evidence at all."""
    try:
        candidate = await get_owned_candidate(supabase, user_id, candidate_id)
    except CandidateNotFound as e:
        raise ApiError("NOT_FOUND", f"no contact found for id {candidate_id!r}") from e

    exa_key = await try_get_secret(supabase, user_id, service="search", provider="exa")
    if exa_key is None:
        raise ApiError(
            "SETUP_REQUIRED",
            "Finding a LinkedIn profile needs an Exa key configured.",
            capability="linkedin_discovery",
            missing=["exa_credential"],
            settings_path="/profile/integrations",
        )

    result = await find_linkedin_exa(
        http,
        api_key=exa_key,
        person_name=candidate["person_name"],
        company=candidate["company"],
        claimed_title=candidate["claimed_title"],
    )

    updated = await save_linkedin_discovery(
        supabase,
        candidate_id,
        linkedin_url=result["linkedin_url"],
        confidence=result["match_confidence"],
        provider="exa",
        discovered_at=datetime.now(UTC).isoformat(),
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
    real Gmail as an actual draft. Never sends: no scope this app ever
    requests (`gmail.compose`, widened to also include `gmail.readonly`
    for Gmail reply/status parsing -- see gmail_client.py's own module
    docstring) authorizes Gmail's send endpoint, and this function never
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
    gmail_draft = await create_gmail_draft(
        http,
        access_token=access_token,
        to_email=candidate["enriched_email"],
        subject=draft["subject"],
        body_text=draft["email_body"],
    )

    updated = await mark_pushed_to_gmail(
        supabase,
        draft["id"],
        gmail_draft_id=gmail_draft["id"],
        gmail_thread_id=gmail_draft["thread_id"],
        pushed_to_gmail_at=datetime.now(UTC).isoformat(),
    )
    return updated
