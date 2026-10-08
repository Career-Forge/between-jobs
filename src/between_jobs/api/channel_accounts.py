"""Who a chat sender is when the bot has never met them, and how a chat account is let go: the
channel-neutral front of account provisioning.

`channel_core` needs three things from the accounts behind a channel, and they are the same
question for every channel:

- `resolve_or_create_user_id_for_subject`: the user a sender belongs to, making one on first
  contact. The account made is a BOT-ONLY one: an auth user with a synthetic, non-deliverable
  email (random, never derived from the sender's id, so nobody can register it on the web) whose
  `app_metadata` -- which only the service role can write -- says which channel and which sender
  it was made for. It is what a later `/link CODE` finds and merges away, and the only kind of
  account the database will merge away (`is_auto_provisioned_channel_user`).
- `is_auto_provisioned_for_subject`: whether a user is exactly that, for this sender; false for a
  web account the sender has since linked to. `/unlink` and `/learn` ask it.
- `unlink_subject`: removes the sender's identity row, detaching it from the web account it was
  linked to.

Telegram's three live in `telegram_identity` and are left exactly as they are (its own tests pin
that it refuses every other channel, so a Telegram sender can never be filed under another
channel's rules and the reverse); this module hands Telegram to them, and holds the same rules
for Discord, whose accounts differ only in what the metadata is called. A channel with neither is
refused, so a sender of a channel nobody wrote the rules for is never given an account by
accident.

DISCORD, and why the Telegram pattern is safe for it. A Discord sender is the `user.id` Discord
puts in a SIGNED interaction (`discord_webhook` verifies the signature before anything else), so it
is as trustworthy as a Telegram update's `from.id` behind the webhook secret: a person cannot run a
command as another person's id. A bot-only account is made on the first command from anyone, as
for Telegram, and costs one auth user and one identity row; the same orphan on a lost race
applies, and the same limits (a Discord account is not free to make in bulk). Nothing in a
bot-only account can be reached from the web: it has no credential and its email is random. So
the account is a bot-only user until the person links it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from postgrest.exceptions import APIError

from supabase import AsyncClient

from . import telegram_identity
from .channel_identity import NO_TENANT, get_user_id_for_subject

_UNIQUE_VIOLATION = "23505"

DISCORD = "discord"
"""The channel's name in `channel_identities.channel`."""


def _discord_email() -> str:
    return f"discord-{uuid.uuid4().hex}@users.between-jobs.tech"


def _metadata_subject_key(channel: str) -> str:
    return f"bj_{channel}_subject"


def _require_known(channel: str) -> None:
    if channel not in (telegram_identity.CHANNEL, DISCORD):
        raise ValueError(f"no identity provisioning for channel {channel!r}")


async def resolve_or_create_user_id_for_subject(
    supabase: AsyncClient, channel: str, subject: str
) -> str:
    """The user this sender of `channel` is, made on first contact (see the module docstring)."""
    _require_known(channel)
    if channel == telegram_identity.CHANNEL:
        return await telegram_identity.resolve_or_create_user_id_for_subject(
            supabase, channel, subject
        )
    existing = await get_user_id_for_subject(supabase, channel, subject, tenant=NO_TENANT)
    if existing is not None:
        return existing

    created = await supabase.auth.admin.create_user(
        {
            "email": _discord_email(),
            "email_confirm": True,
            "app_metadata": {
                # `provider` is left out: Supabase has a Discord sign-in provider of its own, and
                # this account has no sign-in at all.
                "bj_provisioned_by": channel,
                _metadata_subject_key(channel): subject,
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
                    "channel": channel,
                    "external_subject": subject,
                    "external_tenant": NO_TENANT,
                    "verified_at": datetime.now(UTC).isoformat(),
                }
            )
            .execute()
        )
    except APIError as e:
        if e.code != _UNIQUE_VIOLATION:
            raise
        # Lost a race with a concurrent interaction from the same sender (two commands in quick
        # succession on first contact). The auth user just made is an orphan: acceptable for a
        # race this narrow. Fall back to whichever request's row landed.
        winner = await get_user_id_for_subject(supabase, channel, subject, tenant=NO_TENANT)
        if winner is None:
            raise RuntimeError("the identity row that won the race could not be read back") from e
        return winner
    return str(new_user_id)


async def is_auto_provisioned_for_subject(
    supabase: AsyncClient, user_id: str, channel: str, subject: str
) -> bool:
    """True only for the auth user `resolve_or_create_user_id_for_subject` made for this sender
    on first contact: its `app_metadata` names this channel and this sender."""
    _require_known(channel)
    if channel == telegram_identity.CHANNEL:
        return await telegram_identity.is_auto_provisioned_for_subject(
            supabase, user_id, channel, subject
        )
    user = (await supabase.auth.admin.get_user_by_id(user_id)).user
    app_metadata = user.app_metadata or {}
    return (
        app_metadata.get("bj_provisioned_by") == channel
        and app_metadata.get(_metadata_subject_key(channel)) == subject
    )


async def unlink_subject(supabase: AsyncClient, channel: str, subject: str) -> None:
    """Removes this sender's identity row. The web account keeps everything; the sender's next
    command makes a fresh bot-only account, as first contact does. Callers only unlink a linked
    account: unlinking a bot-only one would strand its data under an auth user nothing points
    at."""
    _require_known(channel)
    if channel == telegram_identity.CHANNEL:
        await telegram_identity.unlink_subject(supabase, channel, subject)
        return
    await (
        supabase.table("channel_identities")
        .delete()
        .eq("channel", channel)
        .eq("external_tenant", NO_TENANT)
        .eq("external_subject", subject)
        .execute()
    )
