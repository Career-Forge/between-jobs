"""Who may use Gmail on this server, and what happens to a connection Google stops honouring.

Two things sit on top of `gmail_client` (which talks to Google and knows whether the server has
a Google OAuth client at all):

**The invite list.** Google's two Gmail permissions here (compose and read-only) are
"restricted" scopes. Until the OAuth client passes Google's verification it stays in Testing
mode, which allows at most 100 named test users, so a server can limit Gmail to the people it
has added there. `GMAIL_ALLOWED_USER_IDS` (a comma-separated list of user ids, read by the same
helper and with the same rules as the Hiring signals list) is that list on this side. Unset or
blank means every signed-in user may connect Gmail, which is what a self-hosted server wants. Set,
only the people named may, and everyone else gets one answer wherever they ask: 404
`FEATURE_DISABLED` ("Gmail isn't available for your account yet."), the same status and code as a
server that has no Gmail at all, and saying nothing about who is on the list.

The list is checked on every entry point that starts or uses a connection: connecting (the
connect route, and the callback, which must not let a person who is not listed complete a flow
they started before they were removed), creating a draft in Gmail, and the background reply
checker (which leaves a person who is not listed alone, without a call to Google). Listing and
disconnecting are the generic credential routes and are never gated: a person who is not (or no
longer) listed can always see that a Gmail connection exists and delete it, and with it our copy
of their token. Deleting an account still asks Google to revoke the token, listed or not.

**A person who is removed from the list** keeps whatever connection they already had: nothing
is deleted and nothing is revoked. The reply checker and the draft route stop using it, the web
app shows them that Gmail is not available for their account (with "Disconnect Gmail" still
there), and adding them back makes the same connection usable again.

**A connection Google no longer honours.** While the OAuth client is in Testing mode, Google
expires a refresh token after 7 days; people also revoke access, change their password, or have
an administrator restrict the app. Each shows up as `gmail_client.reconnect_required_error`.
`flag_reconnect_required` records it on the connection's own `provider_credentials` row
(`is_validated` goes false; the encrypted token stays), so that

- the reply checker stops asking Google about that person's drafts every tick, and the draft
  route answers the same error without a call to Google;
- the web app reads the row (`GET /credentials`) and shows "Needs reconnect";
- reconnecting (the callback saves the credential again, `is_validated` true) clears it.

Network failures and Google's own 5xx answers are never recorded: they are transient and the
next tick tries again.

`GMAIL_TESTING_MODE` (on or off, default off) only changes what the web app tells people: that
Google may ask them to reconnect about once a week while Gmail is in testing. It switches no
behaviour here, which works the same either way.
"""

from __future__ import annotations

import logging

from supabase import AsyncClient

from .env import optional_uuid_set, strict_on_off
from .errors import ApiError
from .gmail_client import GmailOauthConfig, require_gmail_oauth_config
from .provider_credentials_store import mark_credential_unvalidated

logger = logging.getLogger(__name__)

_ALLOWED_USER_IDS_ENV = "GMAIL_ALLOWED_USER_IDS"
_TESTING_MODE_ENV = "GMAIL_TESTING_MODE"

GMAIL_NOT_AVAILABLE_MESSAGE = "Gmail isn't available for your account yet."


def gmail_allowlist() -> frozenset[str] | None:
    """The user ids (lower-cased) allowed to use Gmail, or None when no list is set and everyone
    is. A malformed list stops the API from starting; `check_gmail_access_settings` reads it once
    at boot for that reason."""
    return optional_uuid_set(_ALLOWED_USER_IDS_ENV)


def gmail_testing_mode() -> bool:
    """Whether the operator says the Google OAuth client is in Testing mode, so Google expires
    connections after 7 days. Anything but `on` or `off` stops the API from starting."""
    return strict_on_off(_TESTING_MODE_ENV, default=False)


def check_gmail_access_settings() -> None:
    """For the boot: reads both settings so that a malformed one stops the API from starting
    (naming the setting, never its value) instead of deciding, one request at a time, who may
    use Gmail or what people are told."""
    gmail_allowlist()
    gmail_testing_mode()


def is_listed(allowed: frozenset[str] | None, user_id: str) -> bool:
    """Whether an id is on the allowlist that was read: everyone is when there is no list, and
    otherwise a user is by the lower-case form of their id, since the list is kept lower-cased
    and a token's `sub` may be written in capitals. The one membership test."""
    return allowed is None or user_id.lower() in allowed


def user_may_use_gmail(user_id: str) -> bool:
    """Whether the list lets this (already verified) user have Gmail. It says nothing about
    whether the server has Gmail at all (`gmail_client.gmail_enabled`)."""
    return is_listed(gmail_allowlist(), user_id)


def require_gmail_user(user_id: str) -> GmailOauthConfig:
    """Gmail's settings for a user who may use it. A server without Gmail answers first, with
    its own message; then a user who is not on the list gets `GMAIL_NOT_AVAILABLE_MESSAGE`. Both
    are 404 `FEATURE_DISABLED`, so a client cannot tell them apart by anything but the sentence.
    Every route that starts or uses a connection goes through this."""
    config = require_gmail_oauth_config()
    if not user_may_use_gmail(user_id):
        raise ApiError("FEATURE_DISABLED", GMAIL_NOT_AVAILABLE_MESSAGE)
    return config


async def flag_reconnect_required(supabase: AsyncClient, user_id: str) -> None:
    """Records that Google no longer honours this person's Gmail connection (see the module
    docstring), and logs one WARNING for the transition: the line carries the user id and
    nothing Google said, and not the token. Called again for a connection that is already
    flagged (the second of two drafts that failed in one tick, a click on a stale page) it does
    nothing at all, so the log has one line per person per expiry.

    One accepted race: a reconnect that lands in the few seconds between a worker reading the
    old token and this call is flagged again by that late failure, and the person sees "Needs
    reconnect" once more; reconnecting again clears it. It is limited to workers that already
    hold the old token. The reply checker shares one refresh among the drafts that hold the same
    token, never with a draft that read the new connection, so a draft that starts after the
    reconnect does not repeat the old token's failure."""
    if await mark_credential_unvalidated(supabase, user_id, service="oauth", provider="gmail"):
        logger.warning(
            "a Gmail connection needs to be reconnected",
            extra={"ctx": {"user_id": user_id, "reason": "gmail_reconnect_required"}},
        )
