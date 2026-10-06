"""`GET /capabilities` -- what this server was started with, so the web app can
leave out what isn't there instead of offering it and failing.

`telegram`: a server run without a bot (P2.4) has no webhook and no way to
redeem a link code, so the Integrations page hides its Telegram card.
`telegram_bot_username` is the bot's public name (TELEGRAM_BOT_USERNAME, null if
not given or no bot), so the card can tell people which bot to message instead
of assuming the hosted service's.

`tester_program_required`: the operator has switched the tester programme on (see
`tester_enrollment.py`), so the costly features answer 403 ENROLLMENT_REQUIRED until the person
has accepted the tester agreement. The web app uses it to send people to the enrollment page
instead of letting them find out one failed request at a time.

Signed-in users only, like `GET /hiring-signals/status`, the other route that
reports a server-side switch. Flags and a public display name -- never a token,
secret or URL -- and the answer is fixed for the life of the process, so a
client may keep it for a session."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from .app_state import telegram_enabled
from .auth import require_user_id
from .tester_enrollment import tester_program_required

router = APIRouter()


@router.get("/capabilities", dependencies=[Depends(require_user_id)])
async def get_capabilities(request: Request) -> dict[str, bool | str | None]:
    enabled = telegram_enabled(request)
    return {
        "telegram": enabled,
        "telegram_bot_username": request.app.state.telegram_bot_username if enabled else None,
        "tester_program_required": tester_program_required(),
    }
