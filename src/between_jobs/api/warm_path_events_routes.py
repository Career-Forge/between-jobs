"""HTTP surface for the events warm-path engine (outreach-
contactfinder.md Phase D). On-demand for now -- see `warm_path_events.py`'s
own module docstring for why the proactive trigger is a disclosed,
deferred follow-up rather than built here.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends
from pydantic import ValidationError

from supabase import AsyncClient

from .app_state import get_http_client, get_supabase
from .applications_store import ApplicationNotFound, get_application
from .auth import require_user_id
from .credential_resolver import resolve, try_get_secret
from .errors import ApiError
from .jobs_store import SnapshotNotFound, get_snapshot
from .llm_client import generate as llm_generate
from .profile import ResumeTemplate
from .profile_store import get_active_version
from .warm_path_events import build_event_query_plan, find_warm_path_events, run_event_research
from .warm_path_events_store import create_run, get_events_for_run, get_latest_run

router = APIRouter(prefix="/applications/{application_id}/warm-path-events")


def _user_metro(profile_version: dict[str, Any] | None) -> str | None:
    """A best-effort refinement of the query, not a hard requirement --
    unlike `saved_search_matcher.py`'s own full-profile validate (where
    the profile is essential to scoring), a metro string just narrows
    wording here. Fails open to "unknown metro" on any validation error
    rather than 500ing the whole request over one optional field."""
    if profile_version is None:
        return None
    try:
        profile = ResumeTemplate.model_validate(profile_version["canonical_json"])
    except ValidationError:
        return None
    city = profile.personal.location.city.strip()
    return city or None


@router.get("")
async def get_warm_path_events(
    application_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    run = await get_latest_run(supabase, user_id, application_id)
    if run is None:
        return {"run": None, "events": []}
    events = await get_events_for_run(supabase, run["id"])
    return {"run": run, "events": events}


@router.post("", status_code=201)
async def generate_warm_path_events(
    application_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> dict[str, Any]:
    """Same "always fresh, no idempotency key" reasoning as company-intel
    and ContactFinder's own generate routes -- a refresh is a deliberate,
    real-cost action each time."""
    try:
        application = await get_application(supabase, user_id, application_id)
    except ApplicationNotFound as e:
        raise ApiError("NOT_FOUND", f"no application found for id {application_id!r}") from e

    try:
        job_snapshot = await get_snapshot(supabase, application["active_job_snapshot_id"])
    except SnapshotNotFound as e:
        raise ApiError("INTERNAL_ERROR", "This application's job snapshot is missing.") from e

    llm_credential = await resolve(supabase, user_id, capability="warm_path_events", service="llm")
    you_com_key = await try_get_secret(supabase, user_id, service="search", provider="you_com")
    firecrawl_key = await try_get_secret(supabase, user_id, service="search", provider="firecrawl")

    profile_version = await get_active_version(supabase, user_id)
    metro = _user_metro(profile_version)

    company_name = job_snapshot["company_name"]
    queries = build_event_query_plan(company_name, metro)
    results, providers_used, warnings = await run_event_research(
        http, queries, you_com_key=you_com_key, firecrawl_key=firecrawl_key
    )
    events = await find_warm_path_events(
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
        events=events,
        providers_used=providers_used,
        warnings=warnings,
    )
    persisted_events = await get_events_for_run(supabase, run["id"])

    return {"run": run, "events": persisted_events}
