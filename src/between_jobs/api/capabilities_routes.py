"""`GET /capabilities` -- what this server was started with, so the web app can
leave out what isn't there instead of offering it and failing.

`telegram`: a server run without a bot (P2.4) has no webhook and no way to
redeem a link code, so the Settings page hides its Telegram card.
`telegram_bot_username` is the bot's public name (TELEGRAM_BOT_USERNAME, null if
not given or no bot), so the card can tell people which bot to message instead
of assuming the hosted service's.

`discord`, `discord_install_url`: present ONLY when the server has the Discord app configured (see
`discord_config`), so a server without it answers exactly what it always did. `discord` is then
true -- a Discord link code can be redeemed here, so the Settings page shows its Discord card --
and `discord_install_url` is the address people open to add the app (DISCORD_INSTALL_URL, or
Discord's own install link for the application when that is empty; null only when the operator's
value was not a usable https address). The web app reads a missing `discord` as false.

`gmail`: present ONLY when the server has Gmail set up (all three GOOGLE_OAUTH_* settings), and then
true, so a server without it answers exactly what it always did; Gmail is optional like Telegram,
and without it the connect and callback routes answer 404 FEATURE_DISABLED. A flag only: never a
client id, a secret or the redirect address. The web app reads a missing `gmail` as unknown.
Beside it, only then, come two more flags. `gmail_allowed` is about the CALLER: false when the
server limits Gmail to named people (GMAIL_ALLOWED_USER_IDS) and this person is not one of them,
so the Settings page can say Gmail is not available for them instead of offering a button
that would be refused; it is true for everyone when there is no list, and it never says who is on
one.
`gmail_testing_mode` is the operator's GMAIL_TESTING_MODE switch (default false): Google's OAuth
client is still in Testing mode, so a connection expires after 7 days and the page says so.

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
secret or URL -- and the answer is fixed for the life of the process (apart from
`gmail_allowed`, which is the caller's own and changes when the operator edits the
list), so a client may keep it for a session."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from .app_state import discord_enabled, telegram_enabled
from .auth import require_user_id
from .engine_gateway import engine_kind
from .gmail_access import gmail_testing_mode, user_may_use_gmail
from .gmail_client import gmail_enabled
from .tester_enrollment import tester_program_required

router = APIRouter()


@router.get("/capabilities")
async def get_capabilities(
    request: Request, user_id: str = Depends(require_user_id)
) -> dict[str, bool | str | None]:
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
    if gmail_enabled():
        capabilities["gmail"] = True
        capabilities["gmail_allowed"] = user_may_use_gmail(user_id)
        capabilities["gmail_testing_mode"] = gmail_testing_mode()
    return capabilities
