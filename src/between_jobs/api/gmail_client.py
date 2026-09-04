"""Gmail draft integration (outreach-contactfinder.md Phase F) -- the
plain REST Gmail API, deliberately NOT the "Gmail MCP API" tile in the
Google Cloud console: that server is Developer Preview (not GA), is
architected for AI-agent clients doing runtime tool discovery, and has no
service-account/backend-token path at all -- the wrong shape for this
module's one fixed, deterministic action ("create a draft"). See
outreach-contactfinder.md's own Phase F research for the full comparison.

Scope requested is `gmail.compose` only -- Proposal §27.4/§28.7's exact
"draft-only at launch" rule, enforced structurally here, not just by
convention: no function in this module ever calls Gmail's `send`
endpoint, and `gmail.compose` itself doesn't authorize one even if a
future change added such a call by mistake.
"""

from __future__ import annotations

import base64
from email.mime.text import MIMEText
from typing import TypedDict
from urllib.parse import urlencode

import httpx

from .env import require_env
from .errors import ApiError

_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_DRAFTS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
_SCOPE = "https://www.googleapis.com/auth/gmail.compose"
_TIMEOUT_SECONDS = 15.0


class GmailOauthConfig(TypedDict):
    client_id: str
    client_secret: str
    redirect_uri: str


def require_gmail_oauth_config() -> GmailOauthConfig:
    """Wraps `require_env` for this module's own three env vars,
    mapping a missing one to a clean SETUP_REQUIRED error rather than a
    bare `RuntimeError` (a raw 500 with no explanation). Gmail draft
    integration is an OPTIONAL feature -- a self-hoster who never wants
    it shouldn't need these set just to boot the app -- so hitting one
    of these routes without it configured is an expected, common case,
    not a bug to crash on."""
    try:
        return GmailOauthConfig(
            client_id=require_env("GOOGLE_OAUTH_CLIENT_ID"),
            client_secret=require_env("GOOGLE_OAUTH_CLIENT_SECRET"),
            redirect_uri=require_env("GOOGLE_OAUTH_REDIRECT_URI"),
        )
    except RuntimeError as e:
        raise ApiError(
            "SETUP_REQUIRED",
            "Gmail draft integration isn't configured on this server yet.",
            capability="gmail_draft",
            missing=["google_oauth_env"],
        ) from e


def build_authorize_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    """`access_type=offline` + `prompt=consent` -- without both, Google
    only returns a refresh_token on a user's FIRST-ever consent for this
    client, silently omitting it on every later reconnect. This backend
    needs a refresh_token every time (it's the only thing stored; access
    tokens are minted on demand), so both are non-negotiable, not a
    tuning knob."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": _SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{_AUTHORIZE_URL}?{urlencode(params)}"


class TokenResult(TypedDict):
    access_token: str
    refresh_token: str | None


async def exchange_code_for_tokens(
    http: httpx.AsyncClient, *, code: str, client_id: str, client_secret: str, redirect_uri: str
) -> TokenResult:
    try:
        response = await http.post(
            _TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "Couldn't reach Google to finish connecting Gmail.",
            retryable=True,
        ) from e
    if response.status_code >= 400:
        raise ApiError("PROVIDER_REJECTED", "Google rejected that authorization code.")

    body = response.json()
    return TokenResult(access_token=body["access_token"], refresh_token=body.get("refresh_token"))


async def refresh_access_token(
    http: httpx.AsyncClient, *, refresh_token: str, client_id: str, client_secret: str
) -> str:
    """Called before every real Gmail API call -- access tokens are
    short-lived (~1hr) and never stored; only the refresh_token is, so
    each use costs one extra round trip to mint a fresh access token.
    Usage here is inherently rare and one-candidate-at-a-time, so that
    cost is a real, accepted tradeoff, not an oversight."""
    try:
        response = await http.post(
            _TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
            },
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE", "Couldn't reach Google to refresh Gmail access.", retryable=True
        ) from e
    if response.status_code >= 400:
        raise ApiError(
            "PROVIDER_REJECTED",
            "Google rejected the stored Gmail connection -- reconnect Gmail in Integrations.",
        )
    return str(response.json()["access_token"])


def _build_raw_message(*, to_email: str, subject: str, body_text: str) -> str:
    message = MIMEText(body_text)
    message["To"] = to_email
    message["Subject"] = subject
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


async def create_draft(
    http: httpx.AsyncClient, *, access_token: str, to_email: str, subject: str, body_text: str
) -> str:
    """Creates a real Gmail draft -- never sends. Returns the Gmail
    draft id."""
    raw = _build_raw_message(to_email=to_email, subject=subject, body_text=body_text)
    try:
        response = await http.post(
            _DRAFTS_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            json={"message": {"raw": raw}},
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE", "Couldn't reach Gmail. Try again in a moment.", retryable=True
        ) from e
    if response.status_code in (401, 403):
        raise ApiError(
            "PROVIDER_REJECTED",
            "Gmail rejected that request -- try reconnecting Gmail in Integrations.",
        )
    if response.status_code >= 400:
        raise ApiError(
            "PROVIDER_UNAVAILABLE", "Gmail couldn't create that draft right now.", retryable=True
        )
    return str(response.json()["id"])
