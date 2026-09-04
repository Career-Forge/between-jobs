"""Shared "load a document's live Tailor coverage" orchestration --
extracted from `resume_documents_routes.py` (outreach-v2-search-first.md
Phase I) once a second real caller needed the exact same fetch sequence.
Was previously private to that file (`_load_coverage_context`); made
importable rather than duplicated, matching this codebase's own "collapse
duplicated logic into a shared helper" precedent (`supabase_helpers.py`).

No persistence exists for Tailor's coverage/skill-state result anywhere
in this schema (confirmed by a real research pass before this module was
written) -- every call here re-runs Step0, a real LLM call, there is no
cheaper cached-read path today. Callers that only need this occasionally
(a positioning brief, once per generation) should treat that cost the
same way `outreach_writer.py`'s own draft call is already treated: one
bounded, disclosed LLM call, not something to avoid calling.
"""

from __future__ import annotations

from typing import Any, NamedTuple

import httpx

from supabase import AsyncClient

from .credential_resolver import ResolvedCredential, resolve
from .engine_contract import Step0Result
from .errors import ApiError
from .forge_engines_client import call_step0
from .jobs_store import SnapshotNotFound, get_snapshot
from .profile_store import get_version, list_career_facts
from .tailor import ClusterCoverage, compute_coverage


class CoverageContext(NamedTuple):
    step0: Step0Result
    coverage: list[ClusterCoverage]
    canonical_json: dict[str, Any]
    credential: ResolvedCredential


async def load_coverage_context(
    supabase: AsyncClient, http: httpx.AsyncClient, user_id: str, document: dict[str, Any]
) -> CoverageContext:
    """The fetch sequence every live-coverage caller needs: resolve the
    document's job + credential, run Step0 (the one real LLM call this
    makes), and cross it against the bound profile_version
    deterministically. Requires a job to tailor against -- the master/
    default document (no `job_snapshot_id`) has no JD, so coverage isn't
    meaningful for it."""
    job_snapshot_id = document.get("job_snapshot_id")
    if job_snapshot_id is None:
        raise ApiError(
            "INVALID_INPUT",
            "This document has no job to tailor against -- coverage only "
            "applies to a per-application document.",
        )

    try:
        snapshot = await get_snapshot(supabase, job_snapshot_id)
    except SnapshotNotFound as e:
        raise ApiError("NOT_FOUND", "This document's job snapshot no longer exists.") from e

    credential = await resolve(supabase, user_id, capability="prepare_application")
    step0 = await call_step0(
        http,
        job_description=snapshot.get("description_text") or "",
        credential=credential,
    )
    profile_version = await get_version(supabase, user_id, document["profile_version_id"])
    facts = await list_career_facts(supabase, user_id, document["profile_version_id"])
    coverage = compute_coverage([c.model_dump() for c in step0.clusters], facts)
    return CoverageContext(step0, coverage, profile_version["canonical_json"], credential)
