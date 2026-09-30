"""`GET /capabilities` -- what this server was started with, so the web app can
leave out what isn't there instead of offering it and failing.

Today that is one flag, `telegram`: a server run without a bot (P2.4) has no
webhook and no way to redeem a link code, so the Integrations page hides its
Telegram card. Signed-in users only, like `GET /hiring-signals/status`, the
other route that reports a server-side switch. Flags only -- never a value,
token or URL -- and the answer is fixed for the life of the process, so a
client may keep it for a session."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from .app_state import telegram_enabled
from .auth import require_user_id

router = APIRouter()


@router.get("/capabilities", dependencies=[Depends(require_user_id)])
async def get_capabilities(request: Request) -> dict[str, bool]:
    return {"telegram": telegram_enabled(request)}
