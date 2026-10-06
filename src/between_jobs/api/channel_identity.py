"""Who is linked on which channel: the lookups that belong to no channel in particular.

`channel_identities` holds one row per (user, channel) -- the database refuses a second one
(`channel_identities_user_id_channel_key`) -- and one row per (channel, tenant, subject). These
functions read it from either end:

- `get_user_id_for_subject`: the user a channel's sender belongs to, or None for a sender nobody
  has met. A message's text never decides this; the caller passes the (channel, subject) its
  adapter read off the verified update.
- `get_chat_ref`: where an unprompted message to a user goes on one channel, or None when they
  have not linked it.
- `list_linked_channels`: every channel a user has linked, which is what a push fans out over.

What they do NOT do is provision anything. Creating the user that a first message from a new
sender belongs to depends on how that channel's accounts are made (what the auth user carries in
`app_metadata`, how a later `/link` finds it), so it stays with the channel: see
`telegram_identity`, which keeps its refusal of every other channel for that reason.

Every function filters by what it is given and never reads another user's row: the backend uses
the service-role client, which bypasses row-level security, so these filters are the boundary.
"""

from __future__ import annotations

from typing import Any, cast

from supabase import AsyncClient

NO_TENANT = ""
"""The `external_tenant` of a channel that has no workspace concept. Telegram's rows all use it;
a channel with tenants (a Slack workspace) passes its own."""


async def get_user_id_for_subject(
    supabase: AsyncClient, channel: str, subject: str, *, tenant: str = NO_TENANT
) -> str | None:
    """The user linked to this sender of `channel`, or None if no identity row exists for it."""
    result = (
        await supabase.table("channel_identities")
        .select("user_id")
        .eq("channel", channel)
        .eq("external_tenant", tenant)
        .eq("external_subject", subject)
        .execute()
    )
    if not result.data:
        return None
    return str(cast(dict[str, Any], result.data[0])["user_id"])


async def get_chat_ref(supabase: AsyncClient, user_id: str, channel: str) -> str | None:
    """Where a message to this user is addressed on `channel`, or None if they have not linked
    it.

    This is the identity row's subject. That is a chat reference only for a channel whose
    one-to-one conversation is addressed by the person's own id, which is Telegram's rule for a
    private chat and the only channel with an adapter today. A channel for which it does not
    hold (one that must open a conversation first and gets a different id for it) has to
    resolve its chat from the subject inside its own notifier; it must not send to this value."""
    result = (
        await supabase.table("channel_identities")
        .select("external_subject")
        .eq("user_id", user_id)
        .eq("channel", channel)
        .execute()
    )
    if not result.data:
        return None
    return str(cast(dict[str, Any], result.data[0])["external_subject"])


async def list_linked_channels(supabase: AsyncClient, user_id: str) -> list[str]:
    """Every channel this user has linked, once each and in a stable (alphabetical) order."""
    result = (
        await supabase.table("channel_identities")
        .select("channel")
        .eq("user_id", user_id)
        .execute()
    )
    rows = cast(list[dict[str, Any]], result.data)
    return sorted({str(row["channel"]) for row in rows})
