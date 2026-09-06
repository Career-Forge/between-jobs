"""HTTP surface for the Today feed (Horizon Sprint 4.0)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from supabase import AsyncClient

from .app_state import get_supabase
from .application_status_proposals_store import (
    STAGE_MAP,
    StatusProposalNotFound,
    dismiss_other_pending_proposals,
    get_status_proposal_for_today_item,
    resolve_status_proposal,
)
from .applications_store import ApplicationNotFound, change_stage
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


@router.post("/{item_id}/accept-proposal")
async def accept_my_status_proposal(
    item_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    """Applies a `status_proposal` Today item's own suggested Kanban
    stage -- a human deciding the same thing `gmail_reply_checker.py`'s
    auto-apply path decides above `AUTO_TRACK_THRESHOLD`, via the exact
    same `change_stage` call, just with `actor_type="user"` instead of
    `"email_monitor"`."""
    try:
        proposal = await get_status_proposal_for_today_item(supabase, user_id, item_id)
    except StatusProposalNotFound as e:
        raise ApiError("NOT_FOUND", f"no status proposal found for today item {item_id!r}") from e

    # An adversarial review found the original code had no guard here at
    # all -- a stale request against an already-resolved proposal (two
    # browser tabs, a duplicate click) could re-trigger a real stage
    # change after the human already dismissed it, or silently overwrite
    # an already-recorded decision. `resolve_status_proposal`'s own
    # write is ALSO scoped to status='pending' as the real, race-safe
    # enforcement underneath this -- this check just gives the common
    # (non-racing) case a clearer error than a bare 404.
    if proposal["status"] != "pending":
        raise ApiError(
            "CONFLICT", f"this proposal was already {proposal['status']}, not pending anymore"
        )

    new_status = STAGE_MAP.get(proposal["proposed_type"])
    if new_status is None:
        raise ApiError(
            "INVALID_INPUT",
            f"{proposal['proposed_type']!r} has no Kanban stage to accept into -- dismiss it "
            "instead.",
        )

    idempotency_key = f"gmail_reply.status_proposal_accepted:{proposal['id']}"
    try:
        await change_stage(
            supabase,
            user_id,
            proposal["application_id"],
            new_status=new_status,
            idempotency_key=idempotency_key,
        )
    except ApplicationNotFound as e:
        raise ApiError(
            "NOT_FOUND", f"no application found for id {proposal['application_id']!r}"
        ) from e

    await resolve_status_proposal(supabase, user_id, proposal["id"], status="accepted")
    await dismiss_other_pending_proposals(
        supabase, user_id, proposal["application_id"], except_proposal_id=proposal["id"]
    )
    try:
        return await dismiss_today_item(supabase, user_id, item_id)
    except TodayItemNotFound as e:
        raise ApiError("NOT_FOUND", f"no today item found for id {item_id!r}") from e


@router.post("/{item_id}/dismiss-proposal")
async def dismiss_my_status_proposal(
    item_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    """Records a human's decision that a `status_proposal` isn't worth
    acting on -- no stage change, just resolving the proposal row and
    retiring the Today item that surfaced it."""
    try:
        proposal = await get_status_proposal_for_today_item(supabase, user_id, item_id)
    except StatusProposalNotFound as e:
        raise ApiError("NOT_FOUND", f"no status proposal found for today item {item_id!r}") from e

    if proposal["status"] != "pending":
        raise ApiError(
            "CONFLICT", f"this proposal was already {proposal['status']}, not pending anymore"
        )

    await resolve_status_proposal(supabase, user_id, proposal["id"], status="dismissed")
    try:
        return await dismiss_today_item(supabase, user_id, item_id)
    except TodayItemNotFound as e:
        raise ApiError("NOT_FOUND", f"no today item found for id {item_id!r}") from e
