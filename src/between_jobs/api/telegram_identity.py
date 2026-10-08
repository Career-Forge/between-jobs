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
Fixed with a synthetic, non-deliverable email plus `email_confirm: True`
(so Supabase never attempts to actually send mail to it) -- the standard
workaround for headless/social-only Supabase Auth users. The address is
random, not derived from the Telegram id: a derived one could be registered
on the web by anyone who knows the id, and a leftover auto-provisioned user
(after a link or an unlink) held it forever, so the account's next first
contact failed on a duplicate email. What identifies an auto-provisioned
user is its app_metadata, which only the service role can write -- see
`is_auto_provisioned`.

Uses the admin API, not `sign_in_anonymously()` -- the latter mutates the
CALLING client's own session state, which would hijack the shared,
service-role-authenticated `app.state.supabase` client used for every
other trusted-backend operation. Admin operations don't touch the caller's
session.

The lookups that are not Telegram's alone -- the user of a (channel, subject), the chat of a
user, the channels a user has linked -- are `channel_identity`'s, and this module calls them
for its own. The business logic does not call this module directly: `channel_accounts` is its
channel-neutral front, which hands Telegram to the functions here unchanged and holds the same
rules for Discord. What stays here depends on how a Telegram account is provisioned (the auth user's
`app_metadata`) or detached, so each function that does refuses any other channel
(`_require_telegram`): a second channel gets its own rules, and until it does its subjects
must not be filed under Telegram's.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from postgrest.exceptions import APIError

from supabase import AsyncClient

from .channel_identity import NO_TENANT, get_chat_ref, get_user_id_for_subject

_UNIQUE_VIOLATION = "23505"

CHANNEL = "telegram"
EXTERNAL_TENANT = NO_TENANT
"""Telegram has no tenant concept (unlike, say, a multi-workspace Slack
app) -- every channel_identities row for Telegram uses the same empty
tenant, matching the unique(channel, external_tenant, external_subject)
constraint's shape for a channel that doesn't need it."""


PROVISIONED_BY = "telegram"
"""`app_metadata.bj_provisioned_by` on an auth user created on first contact."""


def _synthetic_email() -> str:
    return f"telegram-{uuid.uuid4().hex}@users.between-jobs.tech"


def _require_telegram(channel: str) -> None:
    """Identity is only implemented for Telegram so far. Another channel gets its own
    provisioning rules (what its auth users carry in `app_metadata`) when it arrives;
    refusing here is what keeps its subjects from being filed under Telegram's."""
    if channel != CHANNEL:
        raise ValueError(f"no identity provisioning for channel {channel!r}")


async def resolve_or_create_user_id(supabase: AsyncClient, telegram_user_id: int) -> str:
    return await resolve_or_create_user_id_for_subject(supabase, CHANNEL, str(telegram_user_id))


async def resolve_or_create_user_id_for_subject(
    supabase: AsyncClient, channel: str, subject: str
) -> str:
    """`resolve_or_create_user_id` for a channel-neutral caller: the sender is the
    (channel, subject) pair an `InboundMessage` carries, the provider's own user id as text."""
    _require_telegram(channel)
    external_subject = subject
    existing_user_id = await get_user_id_for_subject(
        supabase, CHANNEL, external_subject, tenant=EXTERNAL_TENANT
    )
    if existing_user_id is not None:
        return existing_user_id

    created = await supabase.auth.admin.create_user(
        {
            "email": _synthetic_email(),
            "email_confirm": True,
            "app_metadata": {
                "provider": "telegram",
                "bj_provisioned_by": PROVISIONED_BY,
                "bj_telegram_subject": external_subject,
            },
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
        winner_user_id = await get_user_id_for_subject(
            supabase, CHANNEL, external_subject, tenant=EXTERNAL_TENANT
        )
        if winner_user_id is None:
            raise RuntimeError("the identity row that won the race could not be read back") from e
        return winner_user_id

    return str(new_user_id)


async def is_auto_provisioned(supabase: AsyncClient, user_id: str, telegram_user_id: int) -> bool:
    return await is_auto_provisioned_for_subject(supabase, user_id, CHANNEL, str(telegram_user_id))


async def is_auto_provisioned_for_subject(
    supabase: AsyncClient, user_id: str, channel: str, subject: str
) -> bool:
    """True only for an auth user `resolve_or_create_user_id` created for
    this Telegram account on first contact: its app_metadata -- which only
    the service role can write, never a web session -- says so and names
    this account. A Telegram account already linked to a web account
    resolves to that web user, which carries neither key."""
    _require_telegram(channel)
    user = (await supabase.auth.admin.get_user_by_id(user_id)).user
    app_metadata = user.app_metadata or {}
    return (
        app_metadata.get("bj_provisioned_by") == PROVISIONED_BY
        and app_metadata.get("bj_telegram_subject") == subject
    )


async def get_chat_id(supabase: AsyncClient, user_id: str) -> int | None:
    """The reverse of `resolve_or_create_user_id` (Job Finder P10's
    real-time push) -- given a Supabase user_id, find their linked
    Telegram chat_id, or None if they have no linked Telegram identity.
    Telegram's own private-chat semantics mean chat_id == the Telegram
    user_id for a 1:1 bot conversation (already relied on implicitly by
    every webhook handler in this codebase), so `external_subject`
    doubles as the chat_id with no separate column needed. The lookup itself is
    `channel_identity.get_chat_ref`, which any channel can use; this is its
    Telegram form, with the id as the integer Telegram's API takes."""
    chat_ref = await get_chat_ref(supabase, user_id, CHANNEL)
    return int(chat_ref) if chat_ref is not None else None


async def unlink(supabase: AsyncClient, telegram_user_id: int) -> None:
    await unlink_subject(supabase, CHANNEL, str(telegram_user_id))


async def unlink_subject(supabase: AsyncClient, channel: str, subject: str) -> None:
    """Removes this Telegram account's channel_identities row (Sprint
    2.8e's `/unlink`), detaching it from the web account it's linked to.
    The web account keeps everything. The NEXT message from this Telegram
    account auto-provisions a fresh identity, same as first contact.
    Callers only unlink a linked account: unlinking an auto-provisioned
    identity would strand its data under an auth user nothing points at."""
    _require_telegram(channel)
    await (
        supabase.table("channel_identities")
        .delete()
        .eq("channel", CHANNEL)
        .eq("external_tenant", EXTERNAL_TENANT)
        .eq("external_subject", subject)
        .execute()
    )
