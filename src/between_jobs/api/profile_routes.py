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

from typing import Any

import httpx
from fastapi import APIRouter, Depends

from supabase import AsyncClient

from .app_state import get_http_client, get_supabase
from .auth import require_user_id
from .credential_resolver import resolve
from .errors import ApiError
from .forge_engines_client import call_gap_answer_draft
from .models import GapInterviewApproveRequest, GapInterviewDraftRequest, ImportProfileRequest
from .profile import ProfileImportError, append_bullet_to_entity, entity_candidates, import_profile
from .profile_store import (
    VersionAlreadyActivated,
    VersionNotFound,
    activate_version,
    count_versions,
    create_pending_version,
    delete_pending_version,
    get_active_version,
    get_version,
    list_career_facts,
)

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


@router.post("/gap-interview/draft")
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
