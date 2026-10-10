"""`GET /capabilities` -- what this server was started with, so the web app can
leave out what isn't there instead of offering it and failing.

`telegram`: a server run without a bot (P2.4) has no webhook and no way to
redeem a link code, so the Integrations page hides its Telegram card.
`telegram_bot_username` is the bot's public name (TELEGRAM_BOT_USERNAME, null if
not given or no bot), so the card can tell people which bot to message instead
of assuming the hosted service's.

`discord`, `discord_install_url`: present ONLY when the server has the Discord app configured (see
`discord_config`), so a server without it answers exactly what it always did. `discord` is then
true -- a Discord link code can be redeemed here, so the Integrations page shows its Discord card --
and `discord_install_url` is the address people open to add the app (DISCORD_INSTALL_URL, or
Discord's own install link for the application when that is empty; null only when the operator's
value was not a usable https address). The web app reads a missing `discord` as false.

`tester_program_required`: the operator has switched the tester programme on (see
`tester_enrollment.py`), so the costly features answer 403 ENROLLMENT_REQUIRED until the person
has accepted the tester agreement. The web app uses it to send people to the enrollment page
instead of letting them find out one failed request at a time.

`engine`: which resume engine this server runs, `"remote"` (a separate service the operator
runs, set with FORGE_ENGINES_BASE_URL) or `"generic"` (the engine built into the API, when that
is unset). A flag only, never the address: it lets a client tell which engine it is talking to
(the web app parses it and does not draw anything from it yet). A server that predates it sends
nothing, which the web app reads as unknown.

Signed-in users only, like `GET /hiring-signals/status`, the other route that
reports a server-side switch. Flags and a public display name -- never a token,
secret or URL -- and the answer is fixed for the life of the process, so a
client may keep it for a session."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from .app_state import discord_enabled, telegram_enabled
from .auth import require_user_id
from .engine_gateway import engine_kind
from .tester_enrollment import tester_program_required

router = APIRouter()


@router.get("/capabilities", dependencies=[Depends(require_user_id)])
async def get_capabilities(request: Request) -> dict[str, bool | str | None]:
    enabled = telegram_enabled(request)
    capabilities: dict[str, bool | str | None] = {
        "telegram": enabled,
        "telegram_bot_username": request.app.state.telegram_bot_username if enabled else None,
        "tester_program_required": tester_program_required(),
        "engine": engine_kind(),
    }
    if discord_enabled(request):
        config = getattr(request.app.state, "discord_config", None)
        capabilities["discord"] = True
        capabilities["discord_install_url"] = config.install_url if config is not None else None
    return capabilities
