"""HTTP surface for the /link one-time-code flow (Sprint 2.8c) --
Proposal §14's Telegram-linking step 1: "Signed-in web user requests a
short-lived one-time code."

Two channels are in the vocabulary, "telegram" and "discord"; anything else is
INVALID_INPUT, the same allow-list precedent credentials_routes.py set for
(service, provider) pairs. A channel in the vocabulary is not necessarily one
this server can serve: a code is only minted when the channel has an adapter
here (`channel_enabled`), because a code nobody can redeem is worse than a
refusal. Telegram has one when the bot is configured; Discord when its
application id and public key are (see `discord_config`), and its codes are
redeemable because `consume_link_code` accepts both channels. A code is only ever
redeemed on the channel it was minted for. A server with neither answers
FEATURE_DISABLED.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from supabase import AsyncClient

from .app_state import channel_enabled, get_supabase
from .auth import require_user_id
from .errors import ApiError
from .link_codes_store import mint_code
from .models import MintLinkCodeRequest

router = APIRouter(prefix="/link")

_SUPPORTED_CHANNELS = {"telegram", "discord"}

_CHANNEL_NAMES = {"telegram": "Telegram", "discord": "Discord"}


@router.post("/code", status_code=201)
async def mint_link_code_route(
    request: Request,
    body: MintLinkCodeRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    if body.channel not in _SUPPORTED_CHANNELS:
        raise ApiError("INVALID_INPUT", f"{body.channel!r} isn't a supported channel yet.")
    if not channel_enabled(request, body.channel):
        # A code nobody can redeem: there's no bot on this server to send it to.
        name = _CHANNEL_NAMES.get(body.channel, body.channel)
        raise ApiError("FEATURE_DISABLED", f"{name} isn't set up on this server.")

    code, expires_at = await mint_code(supabase, user_id, body.channel)
    return {"code": code, "channel": body.channel, "expires_at": expires_at}
