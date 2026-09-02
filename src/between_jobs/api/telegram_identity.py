"""Resolve a Telegram user to a Supabase auth user, auto-provisioning one
on first contact.

This is the concrete answer to the identity-bootstrap question Sprint 2.2
deliberately deferred: Phase 2 ships the Telegram bridge before the web
app, so a user's FIRST identity is often "whoever is messaging this bot,"
not an email signup. `channel_identities` (generalized in Sprint 2.8a from
the Telegram-only `telegram_links`, Sprint 2.1) is what later lets this
same identity link a real web account via the `/link CODE` flow (Sprint
2.8c-e), migrating any data accumulated under the auto-provisioned
identity to the linked web account.

CORRECTED in Sprint 2.5, live-verified: `email`/`phone` being `NotRequired`
on `AdminUserAttributes`' TYPE is not the same as the Auth SERVER accepting
their absence -- it rejects `create_user({})` with "Cannot create a user
without either an email or phone" (confirmed against the real project, not
assumed from the type stub). Every truly-new Telegram user hit this; only
already-linked users worked, which prior live testing never exercised.
Fixed with a synthetic, deterministic, non-deliverable email plus
`email_confirm: True` (so Supabase never attempts to actually send mail to
it) -- the standard workaround for headless/social-only Supabase Auth
users.

Uses the admin API, not `sign_in_anonymously()` -- the latter mutates the
CALLING client's own session state, which would hijack the shared,
service-role-authenticated `app.state.supabase` client used for every
other trusted-backend operation. Admin operations don't touch the caller's
session.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from postgrest.exceptions import APIError

from supabase import AsyncClient

_UNIQUE_VIOLATION = "23505"

CHANNEL = "telegram"
EXTERNAL_TENANT = ""
"""Telegram has no tenant concept (unlike, say, a multi-workspace Slack
app) -- every channel_identities row for Telegram uses the same empty
tenant, matching the unique(channel, external_tenant, external_subject)
constraint's shape for a channel that doesn't need it."""


def _synthetic_email(telegram_user_id: int) -> str:
    return f"telegram-{telegram_user_id}@users.between-jobs.tech"


async def resolve_or_create_user_id(supabase: AsyncClient, telegram_user_id: int) -> str:
    external_subject = str(telegram_user_id)
    existing = (
        await supabase.table("channel_identities")
        .select("user_id")
        .eq("channel", CHANNEL)
        .eq("external_tenant", EXTERNAL_TENANT)
        .eq("external_subject", external_subject)
        .execute()
    )
    if existing.data:
        row = cast(dict[str, Any], existing.data[0])
        return str(row["user_id"])

    created = await supabase.auth.admin.create_user(
        {
            "email": _synthetic_email(telegram_user_id),
            "email_confirm": True,
            "app_metadata": {"provider": "telegram"},
        }
    )
    new_user_id = created.user.id

    try:
        await (
            supabase.table("channel_identities")
            .insert(
                {
                    "user_id": new_user_id,
                    "channel": CHANNEL,
                    "external_subject": external_subject,
                    "external_tenant": EXTERNAL_TENANT,
                    "verified_at": datetime.now(UTC).isoformat(),
                }
            )
            .execute()
        )
    except APIError as e:
        if e.code != _UNIQUE_VIOLATION:
            raise
        # Lost a race with a concurrent webhook call for the same
        # telegram_user_id (e.g. a rapid message burst on first contact).
        # The new auth user we just created is an orphan -- acceptable
        # cost for a race this narrow, not worth a distributed lock over.
        # Fall back to whichever request's row actually landed.
        winner = (
            await supabase.table("channel_identities")
            .select("user_id")
            .eq("channel", CHANNEL)
            .eq("external_tenant", EXTERNAL_TENANT)
            .eq("external_subject", external_subject)
            .execute()
        )
        winner_row = cast(dict[str, Any], winner.data[0])
        return str(winner_row["user_id"])

    return str(new_user_id)


async def get_chat_id(supabase: AsyncClient, user_id: str) -> int | None:
    """The reverse of `resolve_or_create_user_id` (Job Finder P10's
    real-time push) -- given a Supabase user_id, find their linked
    Telegram chat_id, or None if they have no linked Telegram identity.
    Telegram's own private-chat semantics mean chat_id == the Telegram
    user_id for a 1:1 bot conversation (already relied on implicitly by
    every webhook handler in this codebase), so `external_subject`
    doubles as the chat_id with no separate column needed."""
    result = (
        await supabase.table("channel_identities")
        .select("external_subject")
        .eq("user_id", user_id)
        .eq("channel", CHANNEL)
        .execute()
    )
    if not result.data:
        return None
    row = cast(dict[str, Any], result.data[0])
    return int(cast(str, row["external_subject"]))


async def unlink(supabase: AsyncClient, telegram_user_id: int) -> None:
    """Removes this Telegram account's channel_identities row (Sprint
    2.8e's `/unlink`). The auth user it pointed at is left as-is, even if
    that leaves it empty -- deleting auth users is a deliberate, narrow
    action this module only takes on a successful merge (Sprint 2.8e's
    /link handler), not here; an empty orphaned account is a small,
    accepted, harmless gap, matching this codebase's existing precedent
    (e.g. the sessions.updated_at gap) rather than something worth
    inventing extra logic to avoid. The NEXT message from this Telegram
    account auto-provisions a fresh identity again, same as first
    contact -- `resolve_or_create_user_id`'s own behavior, unchanged."""
    await (
        supabase.table("channel_identities")
        .delete()
        .eq("channel", CHANNEL)
        .eq("external_tenant", EXTERNAL_TENANT)
        .eq("external_subject", str(telegram_user_id))
        .execute()
    )
