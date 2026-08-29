"""HTTP surface for the /link one-time-code flow (Sprint 2.8c) --
Proposal §14's Telegram-linking step 1: "Signed-in web user requests a
short-lived one-time code."

Only "telegram" is supported right now -- the only channel with a bot
command wired up to actually consume a code (Sprint 2.8e). Rejecting
anything else with INVALID_INPUT matches the same allow-list precedent
credentials_routes.py already set for (service, provider) pairs.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from supabase import AsyncClient

from .app_state import get_supabase
from .auth import require_user_id
from .errors import ApiError
from .link_codes_store import mint_code
from .models import MintLinkCodeRequest

router = APIRouter(prefix="/link")

_SUPPORTED_CHANNELS = {"telegram"}


@router.post("/code", status_code=201)
async def mint_link_code_route(
    body: MintLinkCodeRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    if body.channel not in _SUPPORTED_CHANNELS:
        raise ApiError("INVALID_INPUT", f"{body.channel!r} isn't a supported channel yet.")

    code, expires_at = await mint_code(supabase, user_id, body.channel)
    return {"code": code, "channel": body.channel, "expires_at": expires_at}
