"""Persistence for company-intel research runs (Horizon Sprint 5.0) --
immutable, like artifact_versions: a run is a point-in-time result, never
edited in place. "The current dossier" for an application is whichever
run is most recent.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from postgrest.exceptions import APIError

from supabase import AsyncClient

from .company_intel_pipeline import Claim

logger = logging.getLogger(__name__)

_FUNCTION_NOT_FOUND = "PGRST202"


class CompanyIntelRunNotFound(Exception):
    """No research run exists yet for this application."""


async def create_run(
    supabase: AsyncClient,
    user_id: str,
    *,
    application_id: str,
    company_name: str,
    claims: list[Claim],
    providers_used: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    """Writes the run and all its claims in ONE database transaction (P0.10,
    `create_company_intel_run`). Two separate inserts left an empty run behind
    whenever the second failed, and `get_latest_run` then returned that empty
    dossier in place of the previous good one."""
    claim_rows = [
        {
            "category": claim["category"],
            "claim_text": claim["claim_text"],
            "source_url": claim["source_url"],
            "source_title": claim["source_title"],
            "confidence": claim["confidence"],
        }
        for claim in claims
    ]
    try:
        result = await supabase.rpc(
            "create_company_intel_run",
            {
                "p_user_id": user_id,
                "p_application_id": application_id,
                "p_company_name": company_name,
                "p_providers_used": providers_used,
                "p_warnings": warnings,
                "p_claims": claim_rows,
            },
        ).execute()
    except APIError as error:
        if error.code != _FUNCTION_NOT_FOUND:
            raise
        # TRANSITIONAL -- delete once the P0.10 migration is on prod. A database that has
        # not had the migration yet has no such function, and failing every company-intel
        # request until it does would be worse than the old two-step write it replaces. Only
        # "function not found" gets here; any other failure propagates, and it never
        # applies once the migration is in.
        logger.warning(
            "create_company_intel_run is not in this database; falling back to the "
            "non-atomic two-step write until the P0.10 migration is applied"
        )
        return await _create_run_in_two_steps(
            supabase,
            user_id,
            application_id=application_id,
            company_name=company_name,
            claim_rows=claim_rows,
            providers_used=providers_used,
            warnings=warnings,
        )
    return cast(dict[str, Any], result.data)


async def _create_run_in_two_steps(
    supabase: AsyncClient,
    user_id: str,
    *,
    application_id: str,
    company_name: str,
    claim_rows: list[dict[str, Any]],
    providers_used: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    run_result = (
        await supabase.table("company_intel_runs")
        .insert(
            {
                "user_id": user_id,
                "application_id": application_id,
                "company_name": company_name,
                "providers_used": providers_used,
                "warnings": warnings,
            }
        )
        .execute()
    )
    run = cast(dict[str, Any], run_result.data[0])
    if claim_rows:
        await (
            supabase.table("company_intel_claims")
            .insert([{"run_id": run["id"], **row} for row in claim_rows])
            .execute()
        )
    return run


async def get_latest_run(
    supabase: AsyncClient, user_id: str, application_id: str
) -> dict[str, Any] | None:
    result = (
        await supabase.table("company_intel_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("application_id", application_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    return cast(dict[str, Any], result.data[0])


async def get_claims_for_run(supabase: AsyncClient, run_id: str) -> list[dict[str, Any]]:
    result = (
        await supabase.table("company_intel_claims")
        .select("*")
        .eq("run_id", run_id)
        .order("category")
        .execute()
    )
    return cast(list[dict[str, Any]], result.data)
