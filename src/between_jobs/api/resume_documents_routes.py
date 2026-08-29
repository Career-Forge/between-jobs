"""HTTP surface for resume-document composition (Sprint 3.2c/3.2d) --
Proposal §24.5.1/§24.5.3.

The Header Composer (3.2c) reads/updates a document's `header_layout` and
gets a deterministic (zero-LLM) preview of what it resolves to. Deriving
the flat `Personal` a header needs from a profile_version's
`canonical_json` goes through forge-engines' real `/ingest` + `/personal`
(both pure, no LLM) rather than a second, from-scratch implementation of
"which email/phone/link is primary" here -- reusing the exact logic the
real generated artifact will use, not a parallel guess at it (the same
drift concern `resolve_header_chips` itself exists to avoid, one layer
up).

Section drag/hide (3.2d) needs no such round trip: "does this section
have content" and "what order/visibility does the user want" are both
answerable from data the client already has (the profile's own
`canonical_json`) without forge-engines involved at all -- unlike the
header, there's no per-field display-mode/hyperlink logic to keep in
sync with the LaTeX renderer, just a name, an order, and a boolean.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, NamedTuple, cast

import httpx
from fastapi import APIRouter, Depends, Query

from supabase import AsyncClient

from .app_state import get_http_client, get_supabase
from .applications_store import ApplicationNotFound, get_application
from .auth import require_user_id
from .credential_resolver import ResolvedCredential, resolve
from .engine_contract import Step0Result
from .errors import ApiError
from .forge_engines_client import (
    call_gap_interview,
    call_ingest,
    call_personal,
    call_step0,
    resolve_header_chips,
)
from .jobs_store import SnapshotNotFound, get_snapshot
from .models import (
    PreviewHeaderRequest,
    UpdateAssertionsRequest,
    UpdateHeaderLayoutRequest,
    UpdateSectionsRequest,
    UpdateSelectedEvidenceRequest,
    UpdateShapeOverridesRequest,
)
from .profile_store import get_active_version, get_version, list_career_facts
from .resume_documents_store import (
    DocumentNotFound,
    get_document,
    get_or_create_document,
    update_assertions,
    update_header_layout,
    update_sections,
    update_selected_evidence,
    update_shape_overrides,
)
from .skills import canonicalize_skill, classify_skill
from .tailor import ClusterCoverage, compute_coverage, pick_gap_interview_questions

router = APIRouter(prefix="/resume-documents")


async def _job_context_for(supabase: AsyncClient, job_snapshot_id: str | None) -> dict[str, Any]:
    if job_snapshot_id is None:
        return {}
    try:
        snapshot = await get_snapshot(supabase, job_snapshot_id)
    except SnapshotNotFound:
        return {}
    return {
        "job_title": snapshot.get("title") or "",
        "company": snapshot.get("company_name") or "",
        "location": snapshot.get("location_text") or "",
        "job_description": snapshot.get("description_text") or "",
        "job_url": snapshot.get("source_url") or "",
    }


async def _personal_for_document(
    supabase: AsyncClient, http: httpx.AsyncClient, user_id: str, document: dict[str, Any]
) -> dict[str, Any]:
    profile_version = await get_version(supabase, user_id, document["profile_version_id"])
    resume_doc = await call_ingest(
        http, template=profile_version["canonical_json"], now=datetime.now(UTC).isoformat()
    )
    job_context = await _job_context_for(supabase, document.get("job_snapshot_id"))
    personal_result = await call_personal(http, resume_doc=resume_doc, job_context=job_context)
    return cast(dict[str, Any], personal_result["personal"])


@router.get("/mine")
async def get_my_document(
    application_id: str | None = Query(default=None),
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    """`application_id` omitted -> the master/default document (Proposal
    §24.5.1's "per-application or master-default"). Idempotent: opening
    the Studio for the same application twice returns the same row."""
    job_snapshot_id: str | None = None
    if application_id is not None:
        try:
            application = await get_application(supabase, user_id, application_id)
        except ApplicationNotFound as e:
            raise ApiError("NOT_FOUND", f"no application found for id {application_id!r}") from e
        job_snapshot_id = application["active_job_snapshot_id"]

    profile_version = await get_active_version(supabase, user_id)
    if profile_version is None:
        raise ApiError(
            "SETUP_REQUIRED",
            "Import and activate a resume profile before opening the Studio.",
            capability="profile",
            missing=["profile_version"],
            settings_path="/profile",
        )

    return await get_or_create_document(
        supabase,
        user_id,
        application_id=application_id,
        profile_version_id=profile_version["id"],
        job_snapshot_id=job_snapshot_id,
    )


@router.patch("/{document_id}/header")
async def update_header(
    document_id: str,
    body: UpdateHeaderLayoutRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    try:
        return await update_header_layout(supabase, user_id, document_id, body.header_layout)
    except DocumentNotFound as e:
        raise ApiError("NOT_FOUND", f"no resume document found for id {document_id!r}") from e


@router.patch("/{document_id}/evidence")
async def update_evidence(
    document_id: str,
    body: UpdateSelectedEvidenceRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    try:
        return await update_selected_evidence(
            supabase, user_id, document_id, body.evidence_fact_ids
        )
    except DocumentNotFound as e:
        raise ApiError("NOT_FOUND", f"no resume document found for id {document_id!r}") from e


@router.patch("/{document_id}/assertions")
async def update_document_assertions(
    document_id: str,
    body: UpdateAssertionsRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    try:
        return await update_assertions(supabase, user_id, document_id, body.assertions)
    except DocumentNotFound as e:
        raise ApiError("NOT_FOUND", f"no resume document found for id {document_id!r}") from e


class _CoverageContext(NamedTuple):
    step0: Step0Result
    coverage: list[ClusterCoverage]
    canonical_json: dict[str, Any]
    credential: ResolvedCredential


async def _load_coverage_context(
    supabase: AsyncClient, http: httpx.AsyncClient, user_id: str, document: dict[str, Any]
) -> _CoverageContext:
    """The fetch sequence `/coverage` and `/gap-interview` both need:
    resolve the document's job + credential, run Step0 (the one real LLM
    call either endpoint makes), and cross it against the bound
    profile_version deterministically. Requires a job to tailor against --
    the master/default document (no `job_snapshot_id`) has no JD, so
    neither coverage nor a gap interview is meaningful for it."""
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
    return _CoverageContext(step0, coverage, profile_version["canonical_json"], credential)


@router.post("/{document_id}/coverage")
async def get_coverage(
    document_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> dict[str, Any]:
    """Tailor mode's requirement-coverage map (Proposal §24.5.4): Step0
    clusters (forge-engines' one real LLM call for this) crossed
    deterministically against the document's own career_facts -- no
    second LLM stage, see tailor.py."""
    try:
        document = await get_document(supabase, user_id, document_id)
    except DocumentNotFound as e:
        raise ApiError("NOT_FOUND", f"no resume document found for id {document_id!r}") from e

    ctx = await _load_coverage_context(supabase, http, user_id, document)
    skills = [
        {
            "skill": canonicalize_skill(term),
            "requested_as": term,
            "state": classify_skill(term, ctx.canonical_json),
        }
        for term in ctx.step0.key_terms
    ]
    return {"step0": ctx.step0.model_dump(), "coverage": ctx.coverage, "skills": skills}


@router.post("/{document_id}/gap-interview")
async def get_gap_interview(
    document_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> dict[str, Any]:
    """S4a (honest-score-surfaces.md): up to GAP_INTERVIEW_MAX_QUESTIONS
    honest interview questions for THIS document's zero-coverage must-have
    clusters -- `pick_gap_interview_questions` (tailor.py) decides which
    gaps have a real, curated bridge to the candidate's own profile
    evidence; the LLM only words the question for whatever it picks. No
    profile writes happen here -- read-only, same as `/coverage`. A
    document with no eligible gap returns an empty list without spending
    the (optional) second LLM call at all."""
    try:
        document = await get_document(supabase, user_id, document_id)
    except DocumentNotFound as e:
        raise ApiError("NOT_FOUND", f"no resume document found for id {document_id!r}") from e

    ctx = await _load_coverage_context(supabase, http, user_id, document)
    candidates = pick_gap_interview_questions(ctx.coverage, ctx.canonical_json)
    if not candidates:
        return {"questions": []}
    questions = await call_gap_interview(
        http,
        items=[
            {"cluster_name": c["cluster_name"], "bridge_skill": c["bridge_skill"]}
            for c in candidates
        ],
        credential=ctx.credential,
    )
    return {"questions": [q.model_dump() for q in questions]}


@router.post("/{document_id}/header/preview")
async def preview_header(
    document_id: str,
    body: PreviewHeaderRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> dict[str, Any]:
    try:
        document = await get_document(supabase, user_id, document_id)
    except DocumentNotFound as e:
        raise ApiError("NOT_FOUND", f"no resume document found for id {document_id!r}") from e

    personal = await _personal_for_document(supabase, http, user_id, document)
    layout = body.header_layout if body.header_layout is not None else document["header_layout"]
    chips = await resolve_header_chips(http, personal=personal, header_layout=layout)
    return {"chips": chips}


@router.patch("/{document_id}/shape")
async def update_document_shape_overrides(
    document_id: str,
    body: UpdateShapeOverridesRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    try:
        return await update_shape_overrides(
            supabase, user_id, document_id, body.shape_overrides.model_dump(exclude_none=True)
        )
    except DocumentNotFound as e:
        raise ApiError("NOT_FOUND", f"no resume document found for id {document_id!r}") from e


@router.patch("/{document_id}/sections")
async def update_document_sections(
    document_id: str,
    body: UpdateSectionsRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    try:
        return await update_sections(
            supabase,
            user_id,
            document_id,
            section_order=body.section_order,
            section_visibility=body.section_visibility,
        )
    except DocumentNotFound as e:
        raise ApiError("NOT_FOUND", f"no resume document found for id {document_id!r}") from e
