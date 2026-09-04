"""HTTP surface for jobs and applications (Sprint 2.6f, prepare added 3.0e).

Manual-paste job creation only -- a client submits the job content
directly (Proposal §18's fallback lane; Firecrawl URL scraping is a later
sprint's job, layered on top of jobs_store, not a replacement for this).
List/get responses embed each application's active job snapshot
(title/company/location for display) via a batch fetch in this routes
layer, not a Postgrest join -- kept explicit and simple to match every
other store in this codebase, none of which use Postgrest's
relationship-embed syntax yet.
"""

from __future__ import annotations

from typing import Any, cast

import httpx
from fastapi import APIRouter, Depends, Response

from supabase import AsyncClient

from .app_state import get_http_client, get_supabase
from .applications_store import (
    ApplicationNotFound,
    change_stage,
    create_application,
    get_application,
    get_latest_prepare_result,
    list_applications,
)
from .artifact_versions_store import artifact_id_for, get_existing_artifact_ids, get_latest_version
from .auth import require_user_id
from .errors import ApiError
from .export_checklist import ChecklistItem, build_checklist
from .jobs_store import create_job_from_paste, get_snapshots
from .models import (
    ChangeApplicationStageRequest,
    CreateApplicationFromPasteRequest,
    PrepareApplicationRequest,
)
from .prepare_orchestrator import (
    latest_cover_letter_pdf,
    latest_resume_pdf,
    run_prepare_application,
)

router = APIRouter(prefix="/applications")


async def _with_snapshot(
    supabase: AsyncClient, applications: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    snapshot_ids = list({a["active_job_snapshot_id"] for a in applications})
    snapshots_by_id = {s["id"]: s for s in await get_snapshots(supabase, snapshot_ids)}
    return [
        {**a, "snapshot": snapshots_by_id.get(a["active_job_snapshot_id"])} for a in applications
    ]


async def _with_resume_exists(
    supabase: AsyncClient, user_id: str, applications: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Applications Kanban K1 (applications-kanban.md D5) -- the real
    "Resume ✓" badge for a whole page of applications, one batch query via
    `get_existing_artifact_ids` rather than `get_my_application`'s own
    per-row `get_latest_version` (fine for a single fetch, would be N+1
    here)."""
    ids_by_application = {a["id"]: artifact_id_for(a["id"], "resume") for a in applications}
    existing = await get_existing_artifact_ids(supabase, user_id, list(ids_by_application.values()))
    return [{**a, "resume_exists": ids_by_application[a["id"]] in existing} for a in applications]


@router.post("", status_code=201)
async def create_application_from_paste(
    body: CreateApplicationFromPasteRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    job, snapshot = await create_job_from_paste(
        supabase,
        title=body.title,
        company_name=body.company_name,
        description_text=body.description_text,
        canonical_url=body.canonical_url,
        location_text=body.location_text,
    )
    application = await create_application(
        supabase,
        user_id,
        job_id=job["id"],
        active_job_snapshot_id=snapshot["id"],
        source_channel="web",
    )
    return {**application, "snapshot": snapshot}


@router.get("")
async def list_my_applications(
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> list[dict[str, Any]]:
    applications = await list_applications(supabase, user_id)
    with_snapshot = await _with_snapshot(supabase, applications)
    return await _with_resume_exists(supabase, user_id, with_snapshot)


@router.get("/{application_id}")
async def get_my_application(
    application_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    """Sprint 3.3d -- the application workspace's own single-record fetch.
    `list_my_applications` batch-embeds snapshots for a whole page of rows;
    this embeds the one snapshot the workspace actually needs the same
    way, not via a second code path.

    `resume_exists` (Sprint 3.3f) lets the workspace show Download/
    Checklist actions for a resume generated in an earlier session,
    without forcing a fresh (real-LLM-spending) regenerate just to
    re-download it."""
    try:
        application = await get_application(supabase, user_id, application_id)
    except ApplicationNotFound as e:
        raise ApiError("NOT_FOUND", f"no application found for id {application_id!r}") from e
    embedded = await _with_snapshot(supabase, [application])
    resume_version = await get_latest_version(supabase, user_id, application_id, "resume")
    return {**embedded[0], "resume_exists": resume_version is not None}


@router.post("/{application_id}/stage")
async def change_application_stage(
    application_id: str,
    body: ChangeApplicationStageRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    # No `except InvalidApplicationStatus` here (K1) -- `body.new_status` is
    # already `models.ApplicationStatus` (a Literal), so FastAPI's own
    # Pydantic validation rejects an invalid value with a 422 before this
    # route body ever runs; catching it here would be handling a state
    # this call site can't actually reach. The Telegram bridge's stage-
    # change callback is the real, reachable caller of that exception --
    # see telegram_webhook.py, which never goes through this Literal.
    try:
        return await change_stage(
            supabase,
            user_id,
            application_id,
            new_status=body.new_status,
            idempotency_key=body.idempotency_key,
        )
    except ApplicationNotFound as e:
        raise ApiError("NOT_FOUND", f"no application found for id {application_id!r}") from e


@router.post("/{application_id}/prepare", status_code=201)
async def prepare_application(
    application_id: str,
    body: PrepareApplicationRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> dict[str, Any]:
    """Thin wrapper over `prepare_orchestrator.run_prepare_application`
    (Sprint 3.4b extracted the actual logic there so the Telegram bridge
    can call the exact same code -- see that module's docstring)."""
    return await run_prepare_application(
        supabase,
        http,
        user_id,
        application_id,
        body.idempotency_key,
        force_generate=body.force_generate,
        generate_cover_letter=body.generate_cover_letter,
    )


@router.get("/{application_id}/prepare-result")
async def get_prepare_result(
    application_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    """outreach-v2-search-first.md Phase I: lets the frontend restore the
    last real `/prepare` outcome (fit, gate_outcome, ats_attempts, ...)
    after a page reload -- previously only ever available in-memory from
    the live POST response, with no way back to it once that state was
    lost. Doesn't re-run the engine or check application ownership
    separately: `get_latest_prepare_result` is already scoped to
    `user_id`, matching every other read in this file."""
    result = await get_latest_prepare_result(supabase, user_id, application_id)
    return {"result": result}


@router.get("/{application_id}/resume.pdf")
async def download_resume_pdf(
    application_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> Response:
    """Sprint 3.3f -- compiles the latest generated resume artifact to a
    PDF via latex-service, on demand. No second (PDF) artifact version is
    stored; see `prepare_orchestrator.latest_resume_pdf`'s docstring for
    why."""
    _version_row, pdf_bytes = await latest_resume_pdf(supabase, http, user_id, application_id)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="resume.pdf"'},
    )


@router.get("/{application_id}/cover-letter.pdf")
async def download_cover_letter_pdf(
    application_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> Response:
    """C1/C2 (coverforge-port.md) -- same shape as `download_resume_pdf`,
    own `document_kind`."""
    _version_row, pdf_bytes = await latest_cover_letter_pdf(supabase, http, user_id, application_id)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="cover-letter.pdf"'},
    )


@router.get("/{application_id}/export-checklist")
async def get_export_checklist(
    application_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> dict[str, list[ChecklistItem]]:
    """Sprint 3.3f, upgraded R6 -- Proposal §24.5.7, scoped per
    `export_checklist.py`'s own docstring to the checks this platform can
    genuinely automate today."""
    version_row, pdf_bytes = await latest_resume_pdf(supabase, http, user_id, application_id)
    warnings = cast(list[str], version_row.get("warnings") or [])
    shape_report = cast("dict[str, Any] | None", version_row.get("shape_report") or None)
    return {"items": build_checklist(pdf_bytes, warnings, shape_report)}
