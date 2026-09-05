"""HTTP surface for job discovery (Job Finder P8, job-finder-port.md's
own build order) -- the full native search pipeline, wiring together
every prior Job Finder phase into one request: P4 (9 BYOK live-search
providers) + P5c (the registry lane, P1-P3e's own ATS poller) merged via
P5a/b/d (dedup/tier/cohort/location filtering) -> P7 (per-platform
liveness verification) -> P6 (batch fit-scoring).

Replaces Horizon Sprint 4.1's discovery facade, which read n8n's own
live Postgres directly (`discovery_store.py`, `N8N_JOBS_DATABASE_URL`) --
dead code now that this platform owns its full, independent search
pipeline end to end, exactly as job-finder-port.md's own D1 always
intended. `discovery_store.py` and the `n8n_pool` app-state wiring are
removed in the same change, not left dangling.

Pipeline order matches n8n's own real node graph, confirmed directly
(`Filter Applied Jobs -> Verify Job Links -> Experience Filter -> Build
Scorer Input -> JobScorer`): liveness verification runs BEFORE scoring,
so a confirmed-dead posting never wastes an LLM scoring call. Runs fully
synchronously in one request (Pranav's own call, 2026-08-31) rather than
a two-phase fast-preview-then-enrich design -- simpler, one round trip;
typical latency is a few seconds, with `ats_liveness`'s own worst-case
budget (~18s) as a deliberately-accepted tail, not the common case.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, cast

import httpx
from fastapi import APIRouter, Depends, Query

from supabase import AsyncClient

from .app_state import get_http_client, get_supabase
from .applications_store import create_application
from .ats_liveness import verify_liveness
from .auth import require_user_id
from .company_tiers import get_company_tier_index
from .credential_resolver import resolve, try_get_secret, try_get_secret_pair
from .errors import ApiError
from .job_fit_scoring import ScoredJob, score_jobs
from .jobs_store import create_job_from_paste, lookup_registry_posting
from .llm_client import generate as llm_generate
from .models import TrackDiscoveredJobRequest
from .profile import ResumeTemplate
from .profile_store import get_active_version
from .search_aggregation import aggregate_jobs, apply_search_filters, fetch_registry_lane
from .search_providers import ProviderCredentials, SearchResult, search_jobs

router = APIRouter(prefix="/discover")

_LIVENESS_CANDIDATE_CAP = 50
"""How many LIVE-SEARCH-LANE (non-registry) aggregated/filtered results
get a real per-platform liveness check -- comfortably above P6's own
30-job scoring cap so a scored batch of 30 ALIVE jobs is still likely
even if several of the top candidates turn out dead. Registry-lane
results never count against this cap and are never probed at all (see
`fetch_registry_lane`'s own `link_checked=True`) -- registry results
sort ahead of most live-search-lane results (tier 1.0), so counting them
against this cap would silently starve the live-search-lane's own
liveness budget, the one thing this cap actually exists to protect.
Every checked candidate is checked concurrently (`verify_liveness`
already fans out via `asyncio.gather`), so raising this cap doesn't
multiply worst-case wall-clock time, only outbound request count.

The full `alive` list (up to this cap) is handed to `score_jobs` as-is,
NOT pre-sliced to 30 here -- `score_jobs` owns its own `_BATCH_SIZE=30`
slicing and returns whatever it doesn't score as `unscored`, exactly the
same convention `saved_search_matcher._match_one_search` already follows
(it never pre-slices before calling `score_jobs` either). Pre-slicing on
this end used to mean `score_jobs`' own `results[_BATCH_SIZE:]` was
always empty (`alive[:30]` never has a 31st item), so `alive[30:50]` --
real, liveness-verified-alive jobs -- silently disappeared from both
`scored` and `more`. Let `score_jobs` decide what's unscored; this route
only decides what never gets liveness-checked at all (`rest`)."""


async def _resolve_provider_credentials(supabase: AsyncClient, user_id: str) -> ProviderCredentials:
    """RemoteOK/Arbeitnow need no key and always run inside `search_jobs`
    itself; every other provider here degrades to "skipped" (never an
    error) when its own key is unset, matching `try_get_secret`'s own
    contract -- a user with zero search keys configured still gets a
    real, if narrower, search. All 7 lookups are keyed only on the fixed
    (supabase, user_id) pair with no data dependency between them, so
    they run concurrently rather than one round trip at a time."""
    # mypy's asyncio.gather overloads only cover a handful of positional
    # args before falling back to a union of every result type applied to
    # every position -- cast each homogeneous group explicitly rather
    # than trust the inferred tuple type.
    results = await asyncio.gather(
        try_get_secret(supabase, user_id, service="search", provider="you_com"),
        try_get_secret(supabase, user_id, service="search", provider="firecrawl"),
        try_get_secret(supabase, user_id, service="search", provider="serper"),
        try_get_secret(supabase, user_id, service="search", provider="brave"),
        try_get_secret(supabase, user_id, service="search", provider="jsearch"),
        try_get_secret_pair(supabase, user_id, service="search", provider="adzuna"),
        try_get_secret_pair(supabase, user_id, service="search", provider="usajobs"),
    )
    you_com_key, firecrawl_key, serper_key, brave_key, jsearch_key = cast(
        "tuple[str | None, str | None, str | None, str | None, str | None]", results[:5]
    )
    adzuna_pair, usajobs_pair = cast(
        "tuple[tuple[str, str] | None, tuple[str, str] | None]", results[5:]
    )
    credentials = ProviderCredentials(
        you_com_key=you_com_key,
        firecrawl_key=firecrawl_key,
        serper_key=serper_key,
        brave_key=brave_key,
        jsearch_key=jsearch_key,
    )
    if adzuna_pair:
        credentials = replace(
            credentials, adzuna_app_id=adzuna_pair[0], adzuna_app_key=adzuna_pair[1]
        )
    if usajobs_pair:
        credentials = replace(
            credentials, usajobs_key=usajobs_pair[0], usajobs_email=usajobs_pair[1]
        )
    return credentials


def _result_to_card(result: SearchResult, scored: ScoredJob | None) -> dict[str, Any]:
    card: dict[str, Any] = {
        "provider": result.provider,
        "title": result.title,
        "company": result.company,
        "location": result.location,
        "remote": result.remote,
        "apply_url": result.apply_url,
        "snippet": result.snippet,
        "posted_at": result.posted_at,
        "salary_min": result.salary_min,
        "salary_max": result.salary_max,
        "salary_currency": result.salary_currency,
        "sponsorship_signal": result.sponsorship_signal,
        "source_tier": result.source_tier,
        "location_verified": result.location_verified,
        "link_checked": result.link_checked,
        "score": None,
    }
    if scored is not None:
        card["score"] = {
            "fit_score": scored.fit_score,
            "one_liner": scored.one_liner,
            "score100": scored.score100,
            "bin": scored.bin,
            "bottleneck": scored.bottleneck,
            "sub_scores": scored.sub_scores.__dict__,
            "inapplicable_dims": list(scored.inapplicable_dims),
            "detected_location": scored.detected_location,
            "location_match": scored.location_match,
        }
    return card


@router.get("")
async def search_discover(
    q: str = Query(default=""),
    location: str | None = Query(default=None),
    companies: str | None = Query(default=None, description="Comma-separated company names"),
    remote_only: bool = Query(default=False),
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> dict[str, Any]:
    profile_version = await get_active_version(supabase, user_id)
    if profile_version is None:
        raise ApiError(
            "SETUP_REQUIRED",
            "Import and activate a resume profile before searching for jobs.",
            capability="profile",
            missing=["profile_version"],
            settings_path="/profile",
        )
    profile = ResumeTemplate.model_validate(profile_version["canonical_json"])

    llm_credential = await resolve(supabase, user_id, capability="job_scoring")
    provider_credentials = await _resolve_provider_credentials(supabase, user_id)

    company_list = [c.strip() for c in companies.split(",") if c.strip()] if companies else None

    # Independent of each other -- one is a live-provider HTTP fan-out,
    # the other a single Supabase RPC -- so the registry-lane call rides
    # for free inside the live-search fan-out's own window instead of
    # adding its own latency on top.
    (live_results, warnings), registry_results = await asyncio.gather(
        search_jobs(
            http,
            query=q,
            location=location,
            companies=company_list,
            remote_only=remote_only,
            credentials=provider_credentials,
        ),
        fetch_registry_lane(supabase, query=q),
    )

    combined = aggregate_jobs(live_results + registry_results, sort_by="relevance")
    combined, gazetteer_active = await apply_search_filters(
        combined,
        supabase=supabase,
        companies=company_list,
        query=q,
        location=location,
        remote_only=remote_only,
    )

    # Registry-lane results (`link_checked=True` already, stamped by
    # fetch_registry_lane) never go through verify_liveness -- they
    # already get liveness for free from the poller's own absence-based
    # mechanism, and ats_liveness's own module docstring says it's never
    # meant to run on them. Only the live-search-lane subset competes for
    # _LIVENESS_CANDIDATE_CAP, so registry volume can never dilute the
    # budget the live-search-lane actually needs it for.
    live_candidates = [r for r in combined if r.provider != "registry"]
    live_to_check = live_candidates[:_LIVENESS_CANDIDATE_CAP]
    live_checked_urls = {r.apply_url for r in live_to_check}

    alive_live, dead_removed = await verify_liveness(http, live_to_check)
    alive_live_by_url = {r.apply_url: r for r in alive_live}

    alive: list[SearchResult] = []
    rest: list[SearchResult] = []
    for r in combined:
        if r.provider == "registry":
            alive.append(r)
        elif r.apply_url in alive_live_by_url:
            alive.append(alive_live_by_url[r.apply_url])
        elif r.apply_url in live_checked_urls:
            continue  # confirmed dead, or dropped as an aggregator listing page
        else:
            rest.append(r)  # beyond the live-lane liveness cap, never checked

    tier_index = await get_company_tier_index(supabase)
    scored, unscored, strategy = await score_jobs(
        profile=profile,
        results=alive,
        llm_api_key=llm_credential.secret,
        llm_model=llm_credential.model,
        llm_base_url=llm_credential.base_url,
        location_requested=bool(location) and not remote_only and gazetteer_active,
        company_health_lookup=tier_index.lookup,
        generate=llm_generate,
    )

    by_url = {r.apply_url: r for r in alive}
    scored_cards = []
    for s in scored:
        result = by_url.get(s.apply_url)
        if result is None:
            continue
        scored_cards.append(_result_to_card(result, s))
    more_cards = [_result_to_card(r, None) for r in unscored] + [
        _result_to_card(r, None) for r in rest
    ]

    return {
        "query": q,
        "scored": scored_cards,
        "more": more_cards,
        "dead_removed": dead_removed,
        "scoring_strategy": strategy,
        "warnings": warnings,
    }


@router.post("/track", status_code=201)
async def track_discovered_job(
    body: TrackDiscoveredJobRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    """Hands the tracked posting to `create_job_from_paste` exactly as if
    the user had pasted it manually (Sprint 2.6f's own write path, no
    second one invented for this). A live-search-lane result (anything
    other than `provider="registry"`) has no full JD text available
    anywhere -- using the 500-char `snippet` as-is is a real, disclosed
    v1 limitation, not a silent guess (a full live re-fetch of the
    posting page is a genuinely separate feature, out of this phase's
    scope)."""
    title = body.title
    company = body.company
    location = body.location
    description_text = body.snippet
    if body.provider == "registry":
        details = await lookup_registry_posting(supabase, body.apply_url)
        if details is not None:
            title = details["title"] or title
            company = details["company"] or company
            location = details["location"] or location
            description_text = details["jd_text"] or description_text
    if not description_text:
        description_text = "(no description available)"

    job_row, snapshot = await create_job_from_paste(
        supabase,
        title=title,
        company_name=company or "Unknown Company",
        description_text=description_text,
        canonical_url=body.apply_url,
        location_text=location,
    )
    application = await create_application(
        supabase,
        user_id,
        job_id=job_row["id"],
        active_job_snapshot_id=snapshot["id"],
        source_channel="discover",
    )
    return {**application, "snapshot": snapshot}
