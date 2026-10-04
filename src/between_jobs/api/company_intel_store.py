"""Persistence for company-intel research runs (Horizon Sprint 5.0) --
immutable, like artifact_versions: a run is a point-in-time result, never
edited in place. "The current dossier" for an application is whichever
run is most recent.
"""

from __future__ import annotations

from typing import Any, cast

from supabase import AsyncClient

from .company_intel_pipeline import Claim


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
    return cast(dict[str, Any], result.data)


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
