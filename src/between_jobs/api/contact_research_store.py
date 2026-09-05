"""Persistence for ContactFinder research runs (outreach-contactfinder.md
Phase A/B) -- per-user, immutable-run shape, same precedent as
`company_intel_store.py`, deliberately NOT the shared cross-user registry
pattern `job_registry_companies`/`interview_process_registry` use. See
`contact_research.py`'s own module docstring for why: contact evidence is
about named private individuals, assembled fresh per (user, application)
request rather than pooled globally across users.
"""

from __future__ import annotations

from typing import Any, cast

from supabase import AsyncClient

from .contact_research import ContactCandidate
from .outreach_writer import build_hook_context


class CandidateNotFound(Exception):
    """No candidate with this id exists, or it belongs to a run this user
    doesn't own -- both cases treated identically, matching this codebase's
    own established pattern of never distinguishing "not found" from "not
    yours" in the response, only in that they both raise the same way."""


async def create_run(
    supabase: AsyncClient,
    user_id: str,
    *,
    application_id: str,
    company_name: str,
    candidates: list[ContactCandidate],
    providers_used: list[str],
    warnings: list[str],
    product_terms: list[str] | None = None,
) -> dict[str, Any]:
    run_result = (
        await supabase.table("contact_research_runs")
        .insert(
            {
                "user_id": user_id,
                "application_id": application_id,
                "company_name": company_name,
                "providers_used": providers_used,
                "warnings": warnings,
                "product_terms": product_terms or [],
            }
        )
        .execute()
    )
    run = cast(dict[str, Any], run_result.data[0])

    if candidates:
        candidate_result = (
            await supabase.table("contact_candidates")
            .insert(
                [
                    {
                        "run_id": run["id"],
                        "person_name": c["person_name"],
                        "claimed_title": c["claimed_title"],
                        "claimed_team": c["claimed_team"],
                        "company": c["company"],
                        "persona": c["persona"],
                        "relevance_reason": c["relevance_reason"],
                        "priority_score": c["priority_score"],
                    }
                    for c in candidates
                ]
            )
            .execute()
        )
        persisted_candidates = cast(list[dict[str, Any]], candidate_result.data)

        evidence_rows = [
            {
                "candidate_id": candidate_row["id"],
                "source_url": evidence["source_url"],
                "source_title": evidence["source_title"],
                "source_snippet": evidence["source_snippet"],
                "observed_at": evidence["observed_at"],
                "evidence_kind": evidence["evidence_kind"],
                "confidence": evidence["confidence"],
            }
            for candidate_row, candidate in zip(persisted_candidates, candidates, strict=True)
            for evidence in candidate["evidence"]
        ]
        if evidence_rows:
            await supabase.table("contact_candidate_evidence").insert(evidence_rows).execute()

    return run


async def get_latest_run(
    supabase: AsyncClient, user_id: str, application_id: str
) -> dict[str, Any] | None:
    result = (
        await supabase.table("contact_research_runs")
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


async def get_candidates_with_evidence(supabase: AsyncClient, run_id: str) -> list[dict[str, Any]]:
    """One batched second query for evidence, mirroring `today_store.
    _with_job_matches`'s own established pattern -- never N+1 per
    candidate."""
    candidate_result = (
        await supabase.table("contact_candidates")
        .select("*")
        .eq("run_id", run_id)
        .order("priority_score", desc=True)
        .execute()
    )
    candidates = cast(list[dict[str, Any]], candidate_result.data)
    if not candidates:
        return []

    candidate_ids = [c["id"] for c in candidates]
    evidence_result = (
        await supabase.table("contact_candidate_evidence")
        .select("*")
        .in_("candidate_id", candidate_ids)
        .order("observed_at", desc=True)
        .execute()
    )
    evidence_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for row in cast(list[dict[str, Any]], evidence_result.data):
        evidence_by_candidate.setdefault(row["candidate_id"], []).append(row)

    for candidate in candidates:
        candidate_evidence = evidence_by_candidate.get(candidate["id"], [])
        candidate["evidence"] = candidate_evidence
        # outreach-v2-search-first.md Phase I: "a one-liner ... that
        # doubles as OutreachWriter's hook" -- literally IS that hook,
        # not a paraphrase of it. Zero extra LLM cost: the same
        # deterministic single-strongest-evidence pick outreach_writer.py
        # already makes when actually drafting for this candidate.
        hook_text, _hook_evidence_id = build_hook_context(candidate_evidence)
        candidate["approach_hint"] = hook_text or None
    return candidates


async def get_evidence_for_candidate(
    supabase: AsyncClient, candidate_id: str
) -> list[dict[str, Any]]:
    """Single-candidate sibling of `get_candidates_with_evidence`'s own
    batched query -- Phase E's OutreachWriter only ever drafts for one
    already-selected candidate, never a whole run."""
    result = (
        await supabase.table("contact_candidate_evidence")
        .select("*")
        .eq("candidate_id", candidate_id)
        .order("observed_at", desc=True)
        .execute()
    )
    return cast(list[dict[str, Any]], result.data)


async def get_owned_candidate(
    supabase: AsyncClient, user_id: str, candidate_id: str
) -> dict[str, Any]:
    """Ownership check for a single candidate (Phase C's enrich route),
    the same "no direct policy -- reachable only by joining through a run
    the user owns" stance the migration's own comments describe, enforced
    here in Python since this backend always reads via the service-role
    client rather than relying on RLS alone."""
    candidate_result = (
        await supabase.table("contact_candidates").select("*").eq("id", candidate_id).execute()
    )
    if not candidate_result.data:
        raise CandidateNotFound(candidate_id)
    candidate = cast(dict[str, Any], candidate_result.data[0])

    run_result = (
        await supabase.table("contact_research_runs")
        .select("id")
        .eq("id", candidate["run_id"])
        .eq("user_id", user_id)
        .execute()
    )
    if not run_result.data:
        raise CandidateNotFound(candidate_id)
    return candidate


async def save_enrichment(
    supabase: AsyncClient,
    candidate_id: str,
    *,
    email: str | None,
    email_status: str | None,
    provider: str,
    enriched_at: str,
) -> dict[str, Any]:
    result = (
        await supabase.table("contact_candidates")
        .update(
            {
                "enriched_email": email,
                "enriched_email_status": email_status,
                "enrichment_provider": provider,
                "enriched_at": enriched_at,
            }
        )
        .eq("id", candidate_id)
        .execute()
    )
    return cast(dict[str, Any], result.data[0])


async def save_linkedin_discovery(
    supabase: AsyncClient,
    candidate_id: str,
    *,
    linkedin_url: str | None,
    confidence: str,
    provider: str,
    discovered_at: str,
) -> dict[str, Any]:
    """outreach-v2-search-first.md Phase K -- same single-overwrite shape
    as `save_enrichment` above, not an evidence-log append. A `None`
    `linkedin_url` (Exa found nothing) is still recorded, same "no
    result" != "never tried" distinction `save_enrichment` already
    draws via `enriched_at`."""
    result = (
        await supabase.table("contact_candidates")
        .update(
            {
                "discovered_linkedin_url": linkedin_url,
                "linkedin_discovery_confidence": confidence,
                "linkedin_discovery_provider": provider,
                "linkedin_discovered_at": discovered_at,
            }
        )
        .eq("id", candidate_id)
        .execute()
    )
    return cast(dict[str, Any], result.data[0])
