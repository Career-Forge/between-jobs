"""HTTP surface for the canonical profile (Sprint 2.5c).

A separate router, not routes bolted onto app.py -- matches
telegram_webhook.py's shape (app.py stays a thin skeleton that includes
feature routers, per its own docstring).

This is the REST API for external clients (the future web app). The
Telegram flow (Sprint 2.5d) does NOT call these HTTP endpoints -- it calls
profile.py/profile_store.py directly, in-process, since it's the same
FastAPI app; looping a server call back through its own HTTP layer would
be pure overhead with no isolation benefit.

Error handling here uses Proposal Appendix B's structured error contract
(Sprint 2.6a) -- ApiError, caught by the handler registered in app.py.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query, Request

from supabase import AsyncClient

from .app_state import get_http_client, get_supabase
from .auth import require_user_id
from .credential_resolver import resolve
from .errors import ApiError
from .forge_engines_client import call_gap_answer_draft
from .llm_client import generate as llm_generate
from .models import GapInterviewApproveRequest, GapInterviewDraftRequest, ImportProfileRequest
from .profile import ProfileImportError, append_bullet_to_entity, entity_candidates, import_profile
from .profile_import import (
    PROFILE_IMPORT_CAPABILITY,
    ConversionRejected,
    ModelAnswerUnusable,
    convert_document_text,
)
from .profile_import_extract import (
    ExtractionError,
    check_declared_type,
    clean_filename,
    extract_document_text,
    refuse_multipart,
    sniff_document_kind,
)
from .profile_import_guard import utf16_offsets
from .profile_store import (
    VersionAlreadyActivated,
    VersionNotFound,
    activate_version,
    count_versions,
    create_pending_version,
    delete_pending_version,
    get_active_version,
    get_version,
    is_active_version,
    list_career_facts,
)
from .rate_limits import limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profile")


@router.post("/versions", status_code=201)
async def import_profile_version(
    body: ImportProfileRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    try:
        imported = import_profile(body.raw_text)
    except ProfileImportError as e:
        raise ApiError("INVALID_INPUT", str(e)) from e

    version = await create_pending_version(supabase, user_id, imported, "web_json_paste")
    return {**version, "stats": imported.stats, "warnings": list(imported.warnings)}


_BUSY_RETRY_SECONDS = 5


def _file_error(error: ExtractionError) -> ApiError:
    """A file that could not be read, as the platform's error: 415 for a kind of file this
    route does not take, 422 for a file of the right kind that cannot be used, and a retryable
    429 when the readers are all busy (nothing is wrong with the file)."""
    if error.reason == "unsupported_type":
        return ApiError("UNSUPPORTED_MEDIA_TYPE", error.message)
    if error.reason == "busy":
        return ApiError(
            "RATE_LIMITED",
            error.message,
            retryable=True,
            details={"retry_after_seconds": _BUSY_RETRY_SECONDS},
        )
    return ApiError("INVALID_INPUT", error.message)


@router.post("/import-document", status_code=201, dependencies=[Depends(limit("profile_import"))])
async def import_profile_document(
    request: Request,
    filename: str | None = Query(default=None, max_length=300),
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    """Imports a resume FILE (PDF or DOCX) as a draft profile.

    The body is the raw file bytes (no multipart), sent with Content-Type `application/pdf`
    or the DOCX type and an optional `?filename=`. What the file is comes from its bytes; a
    Content-Type or extension that disagrees is a 415. The text is extracted in a short-lived
    child process with its own memory and time limits (profile_import_extract; a 429 when
    every reader is busy), converted with the caller's own model (capability
    `profile_import`, falling back to their default), and every value that is not in the
    document is dropped and reported (profile_import_guard).

    The result is stored as a PENDING profile version and returned for review, with the
    extracted text and where each value came from. This route never activates anything: the
    person reviews the draft and activates it, or cancels it, with the version routes."""
    body = await request.body()
    content_type = request.headers.get("content-type")
    try:
        refuse_multipart(content_type)
        kind = await sniff_document_kind(body)
        check_declared_type(kind, content_type, filename)
    except ExtractionError as e:
        raise _file_error(e) from e

    # Before the work: a person with no model key set up learns that now, not after a
    # file has been parsed.
    credential = await resolve(supabase, user_id, capability=PROFILE_IMPORT_CAPABILITY)

    try:
        extracted = await extract_document_text(body, kind)
    except ExtractionError as e:
        raise _file_error(e) from e

    try:
        converted = await convert_document_text(
            extracted.text,
            llm_api_key=credential.secret,
            llm_model=credential.model,
            llm_base_url=credential.base_url,
            generate=llm_generate,
            page_breaks=extracted.page_breaks,
        )
    except ConversionRejected as e:
        raise ApiError("INVALID_INPUT", e.message) from e
    except ModelAnswerUnusable as e:
        raise ApiError(
            "RUN_FAILED",
            "The model's answer couldn't be read as a profile. Try again, or paste the "
            "JSON from your own AI tool instead.",
            retryable=True,
        ) from e

    version = await create_pending_version(
        supabase, user_id, converted.imported, f"web_{kind}_import"
    )
    # "Already active" means this IS the profile in use, not that it was once: a version that
    # was activated and then replaced by a later one is a draft the person can re-activate.
    # (A version that was never activated cannot be the active one, so that read is skipped.)
    already_active = version.get("activated_at") is not None and await is_active_version(
        supabase, user_id, version["id"]
    )
    # Counts only: never the document's text or anything in it.
    logger.info(
        "resume file imported as a pending profile",
        extra={
            "ctx": {
                "kind": kind,
                "characters": len(extracted.text),
                "kept": converted.kept,
                "dropped": len(converted.dropped),
                "llm_attempts": converted.llm_attempts,
            }
        },
    )
    to_utf16 = utf16_offsets(extracted.text)
    return {
        "version_id": version["id"],
        "already_active": already_active,
        "profile": converted.imported.canonical_json,
        # Where each value came from in `extracted_text`, in UTF-16 code units: what a browser's
        # `String.prototype.slice` counts. (Python counts code points, which drift by one
        # for every emoji before the value.)
        "span_unit": "utf16",
        "source_spans": {
            path: {"start": to_utf16(span.start), "end": to_utf16(span.end)}
            for path, span in converted.source_spans.items()
        },
        "dropped": [
            {"path": d.path, "reason": d.reason, "detail": d.detail, "value": d.value}
            for d in converted.dropped
        ],
        "assumptions": [
            {"path": a.path, "value": a.value, "note": a.note} for a in converted.assumptions
        ],
        "extracted_text": extracted.text,
        "stats": {
            **converted.imported.stats,
            "kept_fields": converted.kept,
            "dropped_fields": len(converted.dropped),
            "characters": len(extracted.text),
            "llm_attempts": converted.llm_attempts,
        },
        "warnings": [*converted.imported.warnings, *extracted.notices],
        "document": {
            "kind": kind,
            "filename": clean_filename(filename),
            "pages_total": extracted.pages_total,
            "pages_read": extracted.pages_read,
            "truncated": extracted.truncated,
            "column_pages": list(extracted.column_pages),
        },
    }


@router.get("/versions/{version_id}")
async def get_profile_version(
    version_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    try:
        return await get_version(supabase, user_id, version_id)
    except VersionNotFound as e:
        raise ApiError("NOT_FOUND", f"no profile version found for id {version_id!r}") from e


@router.get("/versions/{version_id}/career-facts")
async def get_career_facts(
    version_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> list[dict[str, Any]]:
    try:
        await get_version(supabase, user_id, version_id)
    except VersionNotFound as e:
        raise ApiError("NOT_FOUND", f"no profile version found for id {version_id!r}") from e
    return await list_career_facts(supabase, user_id, version_id)


@router.post("/versions/{version_id}/activate")
async def activate_profile_version(
    version_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    try:
        return await activate_version(supabase, user_id, version_id)
    except VersionNotFound as e:
        raise ApiError("NOT_FOUND", f"no profile version found for id {version_id!r}") from e


@router.delete("/versions/{version_id}", status_code=204)
async def cancel_profile_version(
    version_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> None:
    try:
        await delete_pending_version(supabase, user_id, version_id)
    except VersionNotFound as e:
        raise ApiError("NOT_FOUND", f"no profile version found for id {version_id!r}") from e
    except VersionAlreadyActivated as e:
        raise ApiError(
            "CONFLICT", "that version has already been activated and can't be cancelled"
        ) from e


@router.get("/current")
async def get_current_profile(
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    version = await get_active_version(supabase, user_id)
    if version is None:
        raise ApiError("NOT_FOUND", "no active profile yet -- import one first")
    version_count = await count_versions(supabase, user_id)
    return {**version, "version_count": version_count}


@router.post("/gap-interview/draft", dependencies=[Depends(limit("gap_interview"))])
async def draft_gap_interview_fact(
    body: GapInterviewDraftRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> dict[str, Any]:
    """S4b (honest-score-surfaces.md): drafts one resume bullet from a
    candidate's answer to a Gap Interview question, and proposes which
    existing experience/project entry it belongs to. Read-only -- nothing
    is written to the profile until the candidate explicitly approves via
    `/gap-interview/approve`, possibly editing this draft first."""
    version = await get_active_version(supabase, user_id)
    if version is None:
        raise ApiError("NOT_FOUND", "no active profile yet -- import one first")

    candidates = entity_candidates(version["canonical_json"])
    if not candidates:
        raise ApiError(
            "INVALID_INPUT",
            "Your profile has no experience or project entries to attach a new fact to.",
        )

    credential = await resolve(supabase, user_id, capability="prepare_application")
    draft = await call_gap_answer_draft(
        http,
        question=body.question,
        answer=body.answer,
        candidates=[{"pointer": c["pointer"], "label": c["label"]} for c in candidates],
        credential=credential,
    )
    return {
        "bullet": draft.bullet,
        "entity_pointer": draft.entity_pointer,
        "candidates": candidates,
    }


@router.post("/gap-interview/approve", status_code=201)
async def approve_gap_interview_fact(
    body: GapInterviewApproveRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    """S4b: the deterministic apply step, once a candidate has explicitly
    approved a drafted (or edited) fact -- no LLM call here at all. Builds
    a new PENDING profile_version (`source_kind="gap_interview"`,
    `supersedes_id` pointing at the version this was built on top of) via
    the exact same validate/derive-facts/hash pipeline every other import
    goes through. The human still has to explicitly activate it
    (`/versions/{id}/activate`, unchanged) -- this alone never becomes the
    active profile."""
    active = await get_active_version(supabase, user_id)
    if active is None:
        raise ApiError("NOT_FOUND", "no active profile yet -- import one first")

    try:
        imported = append_bullet_to_entity(
            active["canonical_json"], body.entity_pointer, body.bullet
        )
    except ProfileImportError as e:
        raise ApiError("INVALID_INPUT", str(e)) from e

    version = await create_pending_version(
        supabase, user_id, imported, "gap_interview", supersedes_id=active["id"]
    )
    return {**version, "stats": imported.stats, "warnings": list(imported.warnings)}
