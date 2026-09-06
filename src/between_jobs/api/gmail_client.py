"""Gmail draft integration (outreach-contactfinder.md Phase F; widened
outreach-v2-search-first.md Phase 5's second half, "Gmail reply/status
parsing") -- the plain REST Gmail API, deliberately NOT the "Gmail MCP
API" tile in the Google Cloud console: that server is Developer Preview
(not GA), is architected for AI-agent clients doing runtime tool
discovery, and has no service-account/backend-token path at all -- the
wrong shape for this module's fixed, deterministic actions. See
outreach-contactfinder.md's own Phase F research for the full comparison.

Scope is `gmail.compose gmail.readonly` -- draft-creation stays exactly
as narrow as Proposal §27.4/§28.7's "draft-only at launch" rule requires
(no function here ever calls Gmail's `send` endpoint, and `gmail.compose`
itself doesn't authorize one even if a future change added such a call
by mistake); `gmail.readonly` is the new grant the reply-checker poller
needs to read a thread it already knows the id of -- confirmed live
against Gmail's own API reference that `gmail.readonly` alone covers
both `threads.get` and `watch()`, never `gmail.modify` or the full
`mail.google.com` scope. A user who granted only the old `gmail.compose`
scope before this widening has a stored credential whose real granted
scope (captured in `provider_credentials.scope` at connect time) won't
include `gmail.readonly` -- callers that need read access check that
column and prompt a reconnect rather than assuming the wider grant."""

from __future__ import annotations

import base64
import re
from email.mime.text import MIMEText
from typing import Any, TypedDict
from urllib.parse import urlencode

import httpx

from .env import require_env
from .errors import ApiError

_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GMAIL_API_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
_DRAFTS_URL = f"{_GMAIL_API_URL}/drafts"
_SCOPES = (
    "https://www.googleapis.com/auth/gmail.compose https://www.googleapis.com/auth/gmail.readonly"
)
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
        "scope": _SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{_AUTHORIZE_URL}?{urlencode(params)}"


class TokenResult(TypedDict):
    access_token: str
    refresh_token: str | None
    scope: str | None


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
    return TokenResult(
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token"),
        # Confirmed live against Google's own current OAuth2 docs: the
        # token endpoint's response includes the actually-granted scope
        # string -- this is what provider_credentials.scope records, not
        # an assumption that whatever was requested was granted.
        scope=body.get("scope"),
    )


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


class DraftResult(TypedDict):
    id: str
    thread_id: str


async def create_draft(
    http: httpx.AsyncClient, *, access_token: str, to_email: str, subject: str, body_text: str
) -> DraftResult:
    """Creates a real Gmail draft -- never sends. Returns the Gmail draft
    id AND its thread id -- Gmail assigns a real thread/message id to a
    draft even before it's ever sent, confirmed live, so capturing it
    here (rather than discarding it the way this function originally
    did) is the one thing the reply-checker poller needs on hand to ever
    ask Gmail "what's happened in this thread since.\""""
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
    body = response.json()
    thread_id = body.get("message", {}).get("threadId")
    if "id" not in body or not thread_id:
        # An adversarial review caught that a bare body["message"]["threadId"]
        # chain would raise an uncaught KeyError -- an unstructured 500 --
        # on any 200 response shaped differently than expected, treating a
        # third-party API's response as fully trusted. This project treats
        # third-party/scraped content as untrusted input everywhere else;
        # this is that same discipline applied to a schema-valid-but-
        # unexpectedly-shaped Gmail response.
        raise ApiError(
            "PROVIDER_UNAVAILABLE", "Gmail's response didn't include the expected draft data."
        )
    return DraftResult(id=str(body["id"]), thread_id=str(thread_id))


class GmailMessage(TypedDict):
    id: str
    label_ids: list[str]
    internal_date_ms: int
    snippet: str
    body_text: str


class GmailThread(TypedDict):
    id: str
    messages: list[GmailMessage]


def _decode_body_data(data: str) -> str:
    # Gmail's own base64url alphabet, confirmed against its API
    # reference -- '-'/'_' instead of '+'/'/', and often missing the
    # trailing '=' padding a strict decoder would require.
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _extract_plain_text(payload: dict[str, Any]) -> str | None:
    """Walks a message's MIME `payload` for the first `text/plain` part
    -- a real Gmail message is either a single-part text/plain body or a
    multipart/* tree with a text/plain part nested at some depth (a
    plain single-level `parts` scan would miss a multipart/alternative
    nested inside a multipart/mixed, a real, common shape for a reply
    with a signature or a quoted-thread attachment)."""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data")
        # A real Gmail shape, not hypothetical: a large plain-text body is
        # returned as {attachmentId, size} with NO inline `data` at all.
        # An adversarial review caught that returning "" here (rather than
        # None) made get_thread's `is not None` check treat that as "found
        # empty content," silently swallowing a real message body instead
        # of falling back to the snippet the way a genuinely-absent
        # text/plain part already does.
        return _decode_body_data(data) if data else None
    for part in payload.get("parts") or []:
        found = _extract_plain_text(part)
        if found is not None:
            return found
    return None


_GMAIL_ID_PATTERN = re.compile(r"^[0-9a-f]+$")


async def get_thread(http: httpx.AsyncClient, *, access_token: str, thread_id: str) -> GmailThread:
    """Wraps `users.threads.get` (format=full -- one call returns every
    message's labels, timestamp, AND full body, so the reply-checker
    poller never needs a second `messages.get` call). 40 quota units per
    call against a 6,000/min/user budget, confirmed live -- trivial for
    any realistic tracked-thread count.

    `thread_id` is interpolated directly into the request URL's path --
    every caller today sources it from `outreach_drafts.gmail_thread_id`,
    itself written only from Gmail's own `create_draft` response, never
    from user/request input. An adversarial review flagged that a future
    caller could get this wrong, so this checks the real Gmail id shape
    (lowercase hex) before it ever reaches the URL, rather than relying
    on every future call site to remember thread_id must be trusted."""
    if not _GMAIL_ID_PATTERN.match(thread_id):
        raise ApiError("INVALID_INPUT", "That doesn't look like a real Gmail thread id.")
    try:
        response = await http.get(
            f"{_GMAIL_API_URL}/threads/{thread_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"format": "full"},
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
            "PROVIDER_UNAVAILABLE", "Gmail couldn't fetch that thread right now.", retryable=True
        )

    body = response.json()
    messages: list[GmailMessage] = []
    for message in body.get("messages") or []:
        payload = message.get("payload") or {}
        plain_text = _extract_plain_text(payload)
        messages.append(
            GmailMessage(
                id=str(message["id"]),
                label_ids=list(message.get("labelIds") or []),
                internal_date_ms=int(message.get("internalDate", "0")),
                snippet=str(message.get("snippet", "")),
                body_text=plain_text if plain_text is not None else str(message.get("snippet", "")),
            )
        )
    return GmailThread(id=str(body["id"]), messages=messages)
