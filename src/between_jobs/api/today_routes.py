"""HTTP surface for the Today feed (Horizon Sprint 4.0)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from supabase import AsyncClient

from .app_state import get_supabase
from .auth import require_user_id
from .errors import ApiError
from .today_store import TodayItemNotFound, dismiss_today_item, list_today_items

router = APIRouter(prefix="/today")


@router.get("")
async def list_my_today_items(
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> list[dict[str, Any]]:
    return await list_today_items(supabase, user_id)


@router.post("/{item_id}/dismiss")
async def dismiss_my_today_item(
    item_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    try:
        return await dismiss_today_item(supabase, user_id, item_id)
    except TodayItemNotFound as e:
        raise ApiError("NOT_FOUND", f"no today item found for id {item_id!r}") from e
