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

from .env import optional_env, refuse
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


GOOGLE_OAUTH_VARIABLES = (
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_OAUTH_REDIRECT_URI",
)
"""The three settings that turn Gmail on: all of them, or Gmail is off. A client id or secret
without the rest is a mistake and stops the boot (`check_gmail_configuration`); a redirect URI on
its own is not, and is ignored."""

GMAIL_DISABLED_MESSAGE = "Gmail isn't set up on this server."

GMAIL_RECONNECT_REASON = "gmail_reconnect_required"
"""The `details["reason"]` of every error that says Google no longer honours a person's Gmail
connection and they have to connect again. The web app turns it into a "Reconnect Gmail" prompt,
and the reply checker and the draft route use it to flag the connection (`gmail_access`)."""

GMAIL_RECONNECT_MESSAGE = (
    "Google has stopped accepting your Gmail connection (it may have expired or been revoked). "
    "Reconnect Gmail in Integrations to create drafts and check for replies again."
)

_INTEGRATIONS_PATH = "/profile/integrations"

_ACCESS_ENDED_OAUTH_ERRORS = frozenset({"invalid_grant", "admin_policy_enforced"})
"""The token endpoint's `error` values that mean the stored refresh token no longer works and
only the person can fix it: `invalid_grant` (expired, revoked, the password changed, too many
live tokens, or a session-control policy) and `admin_policy_enforced` (a Workspace administrator
restricted the app). Others are not about the person: `invalid_client` and `deleted_client` are
the operator's client settings, and flagging every connection over them would send everyone to a
reconnect that cannot help."""

_GMAIL_RATE_LIMIT_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})
"""Gmail reports a rate limit as HTTP 403 with one of these `reason` values, as well as 429."""

_GMAIL_SCOPE_REASONS = frozenset({"insufficientPermissions"})
"""The 403 `reason` for a token that lacks the scope a call needs (the person unticked a
permission on Google's consent screen, or withdrew one later): only reconnecting grants it."""


def reconnect_required_error() -> ApiError:
    """The one answer for "Google no longer honours this person's Gmail connection": a rejected
    provider call (so it is logged as an expected condition, not a bug) that names its reason
    and points at the Integrations page."""
    return ApiError(
        "PROVIDER_REJECTED",
        GMAIL_RECONNECT_MESSAGE,
        settings_path=_INTEGRATIONS_PATH,
        details={"reason": GMAIL_RECONNECT_REASON},
    )


def needs_reconnect(error: BaseException) -> bool:
    """Whether `error` is `reconnect_required_error`'s answer, as raised by any call below."""
    return isinstance(error, ApiError) and error.details.get("reason") == GMAIL_RECONNECT_REASON


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _text(value: object) -> str | None:
    """`value` when it is a string, else None. The only things read out of Google's answers are
    short machine tokens such as `invalid_grant` or `userRateLimitExceeded`, and only to check
    them against the sets above: its free-text messages are never read, kept or passed on."""
    return value if isinstance(value, str) else None


def _gmail_error_reason(response: httpx.Response) -> str | None:
    """The first `reason` in a Gmail API error body (`{"error": {"errors": [{"reason": ...}]}}`),
    or None when there is none or the body is not that shape."""
    error = _json_object(response).get("error")
    entries = error.get("errors") if isinstance(error, dict) else None
    if isinstance(entries, list) and entries and isinstance(entries[0], dict):
        return _text(entries[0].get("reason"))
    return None


def _raise_for_gmail_status(response: httpx.Response, *, doing: str) -> None:
    """Turns a failed Gmail API answer into the right `ApiError`, and does nothing for a success.

    Only some failures are about the person's connection (`reconnect_required_error`): a 401
    (the access token is invalid, or lacks authorization for the scopes) and a 403 for a missing
    scope. A rate limit (a 429, or a 403 with a rate-limit reason) and a server failure (5xx) are
    transient and say so. A 403 for anything else (the project's daily limit, a domain policy, a
    reason this code does not know) is a refusal that reconnecting would not change, so it is
    reported as one without sending the person on a pointless reconnect. `doing` completes
    "Gmail couldn't ..." for the messages."""
    status = response.status_code
    if status < 400:
        return
    if status == 401:
        raise reconnect_required_error()
    reason = _gmail_error_reason(response) if status == 403 else None
    if reason in _GMAIL_SCOPE_REASONS:
        raise reconnect_required_error()
    if status == 429 or reason in _GMAIL_RATE_LIMIT_REASONS:
        raise ApiError(
            "PROVIDER_RATE_LIMITED",
            "Gmail is limiting requests right now. Try again in a moment.",
            retryable=True,
        )
    if status == 403:
        raise ApiError("PROVIDER_REJECTED", f"Gmail refused to {doing}.")
    raise ApiError("PROVIDER_UNAVAILABLE", f"Gmail couldn't {doing} right now.", retryable=True)


def gmail_oauth_config() -> GmailOauthConfig | None:
    """Gmail's settings, or None when Gmail is not set up here: Gmail is OPTIONAL, like
    Telegram, so a server that never wants it leaves the three variables unset. A variable that
    is unset or empty reads as not set. Read from the environment on every call, and never
    logs: "not configured" is a normal, common state and not an error."""
    client_id = optional_env("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = optional_env("GOOGLE_OAUTH_CLIENT_SECRET")
    redirect_uri = optional_env("GOOGLE_OAUTH_REDIRECT_URI")
    if client_id and client_secret and redirect_uri:
        return GmailOauthConfig(
            client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri
        )
    return None


def gmail_enabled() -> bool:
    return gmail_oauth_config() is not None


def check_gmail_configuration() -> bool:
    """For the boot: whether Gmail is set up. False when no client id or secret is set; when one
    of them is set without the whole set, it stops the API from starting (`refuse`), naming the
    variables and never a value. That is a mistake, not "off" -- a client secret with nothing to
    use it, or a connect button that can never work -- the same all-or-nothing rule as the
    Telegram and Discord settings.

    A redirect URI on its own is NOT a mistake: nothing secret is set, nothing could ever be
    connected, and Gmail is simply off (`gmail_oauth_config` is None). It is exactly what an
    `.env` copied from an earlier `.env.example` holds, which shipped it filled in beside the
    two blank credentials; refusing to start over it would stop every self-hosted install that
    followed the README and never wanted Gmail."""
    if gmail_enabled():
        return True
    if optional_env("GOOGLE_OAUTH_CLIENT_ID") or optional_env("GOOGLE_OAUTH_CLIENT_SECRET"):
        unset = [name for name in GOOGLE_OAUTH_VARIABLES if not optional_env(name)]
        refuse(
            f"{', '.join(GOOGLE_OAUTH_VARIABLES)} must be set together, or all left unset to "
            f"run without Gmail (not set: {', '.join(unset)})"
        )
    return False


def require_gmail_oauth_config() -> GmailOauthConfig:
    """Gmail's settings, or a 404 FEATURE_DISABLED ("Gmail isn't set up on this server.") on a
    server that runs without Gmail, in the style of the Telegram routes. Every Gmail entry
    point goes through this (the connect route, the callback, pushing a draft, the reply
    checker), so none of them does anything -- or logs anything -- when Gmail is off."""
    config = gmail_oauth_config()
    if config is None:
        raise ApiError("FEATURE_DISABLED", GMAIL_DISABLED_MESSAGE)
    return config


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
    cost is a real, accepted tradeoff, not an oversight.

    Google answers a refresh token it no longer honours with a 4xx and an `error` of
    `invalid_grant` (in a project whose consent screen is in Testing mode that is every refresh
    token after 7 days): that is `reconnect_required_error`, and nothing else is. A network
    failure, a 429 and a 5xx are transient (`PROVIDER_UNAVAILABLE`, retryable), and any other
    refusal is a plain `PROVIDER_REJECTED`; none of those says the person has to reconnect."""
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
        if response.status_code == 429 or response.status_code >= 500:
            raise ApiError(
                "PROVIDER_UNAVAILABLE",
                "Google couldn't refresh Gmail access right now. Try again in a moment.",
                retryable=True,
            )
        if _text(_json_object(response).get("error")) in _ACCESS_ENDED_OAUTH_ERRORS:
            raise reconnect_required_error()
        raise ApiError("PROVIDER_REJECTED", "Google wouldn't refresh Gmail access.")
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
    _raise_for_gmail_status(response, doing="create that draft")
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
    _raise_for_gmail_status(response, doing="fetch that thread")

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
