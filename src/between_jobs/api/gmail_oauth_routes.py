"""HTTP surface for connecting Gmail (outreach-contactfinder.md Phase F).

Two routes with genuinely different trust models, unlike every other
route in this codebase: `connect_gmail` is a normal authenticated route
the SPA calls via `apiFetch`; `gmail_callback` is hit by a raw browser
redirect FROM GOOGLE, carrying no JWT at all -- `oauth_states_store`
exists specifically to recover the calling user's identity across that
gap. `gmail_callback` deliberately renders plain HTML, not this
project's own JSON error envelope (`ApiError`/Appendix B) -- the same
reasoning `errors.py`'s own docstring gives for exempting the Telegram
webhook: Google's browser redirect is the caller here, not this
platform's own API client, so it doesn't parse (or care about) that
envelope shape.
"""

from __future__ import annotations

import html

import httpx
from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse

from supabase import AsyncClient

from .app_state import get_http_client, get_supabase
from .auth import require_user_id
from .errors import ApiError
from .gmail_client import build_authorize_url, exchange_code_for_tokens, require_gmail_oauth_config
from .oauth_states_store import consume_state, mint_state
from .provider_credentials_store import save_credential

router = APIRouter()

_PROVIDER = "gmail"

_SUCCESS_HTML = (
    "<html><body><h1>Gmail connected</h1>"
    "<p>You can close this tab and return to Between Jobs.</p></body></html>"
)
_FAILURE_HTML_TEMPLATE = "<html><body><h1>Couldn't connect Gmail</h1><p>{message}</p></body></html>"


@router.get("/profile/integrations/gmail/connect")
async def connect_gmail(
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, str]:
    """Returns a URL for the frontend to navigate the whole browser to
    (`window.location.href = ...`), not a redirect response itself --
    this route is called via the SPA's own authenticated `apiFetch`, and
    a fetch response's redirect can't hand control to the top-level
    browser window the way a real navigation needs to."""
    config = require_gmail_oauth_config()
    state = await mint_state(supabase, user_id, _PROVIDER)
    authorize_url = build_authorize_url(
        client_id=config["client_id"], redirect_uri=config["redirect_uri"], state=state
    )
    return {"authorize_url": authorize_url}


@router.get("/oauth/gmail/callback")
async def gmail_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> HTMLResponse:
    if error or not code or not state:
        return HTMLResponse(
            _FAILURE_HTML_TEMPLATE.format(message="Google didn't authorize that request."),
            status_code=400,
        )

    try:
        config = require_gmail_oauth_config()
    except ApiError as e:
        return HTMLResponse(
            _FAILURE_HTML_TEMPLATE.format(message=html.escape(e.message)), status_code=500
        )

    user_id = await consume_state(supabase, state, _PROVIDER)
    if user_id is None:
        return HTMLResponse(
            _FAILURE_HTML_TEMPLATE.format(
                message="This connection attempt expired -- try again from Integrations."
            ),
            status_code=400,
        )

    try:
        tokens = await exchange_code_for_tokens(
            http,
            code=code,
            client_id=config["client_id"],
            client_secret=config["client_secret"],
            redirect_uri=config["redirect_uri"],
        )
    except ApiError as e:
        return HTMLResponse(
            _FAILURE_HTML_TEMPLATE.format(message=html.escape(e.message)), status_code=400
        )

    if tokens["refresh_token"] is None:
        return HTMLResponse(
            _FAILURE_HTML_TEMPLATE.format(
                message="Google didn't grant offline access -- try reconnecting and approving "
                "the full consent screen."
            ),
            status_code=400,
        )

    await save_credential(
        supabase,
        user_id,
        service="oauth",
        provider=_PROVIDER,
        secret=tokens["refresh_token"],
        is_validated=True,
    )
    return HTMLResponse(_SUCCESS_HTML)
