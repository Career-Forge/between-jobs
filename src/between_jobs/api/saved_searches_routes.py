"""HTTP surface for saved searches (Job Finder P9a, today-feed-job-
matching.md)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from supabase import AsyncClient

from .app_state import get_supabase
from .auth import require_user_id
from .errors import ApiError
from .models import CreateSavedSearchRequest, SetSavedSearchActiveRequest
from .saved_searches_store import (
    SavedSearchNotFound,
    create_saved_search,
    delete_saved_search,
    list_saved_searches,
    set_saved_search_active,
)

router = APIRouter(prefix="/saved-searches")


@router.get("")
async def list_my_saved_searches(
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> list[dict[str, Any]]:
    return await list_saved_searches(supabase, user_id)


@router.post("", status_code=201)
async def create_my_saved_search(
    body: CreateSavedSearchRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    return await create_saved_search(
        supabase,
        user_id,
        query=body.query,
        location=body.location,
        companies=body.companies,
        remote_only=body.remote_only,
    )


@router.patch("/{search_id}")
async def update_my_saved_search_active_state(
    search_id: str,
    body: SetSavedSearchActiveRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    try:
        return await set_saved_search_active(supabase, user_id, search_id, is_active=body.is_active)
    except SavedSearchNotFound as e:
        raise ApiError("NOT_FOUND", f"no saved search found for id {search_id!r}") from e


@router.delete("/{search_id}", status_code=204)
async def delete_my_saved_search(
    search_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> None:
    try:
        await delete_saved_search(supabase, user_id, search_id)
    except SavedSearchNotFound as e:
        raise ApiError("NOT_FOUND", f"no saved search found for id {search_id!r}") from e
