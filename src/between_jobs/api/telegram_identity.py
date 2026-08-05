"""Resolve a Telegram user to a Supabase auth user, auto-provisioning one
on first contact.

This is the concrete answer to the identity-bootstrap question Sprint 2.2
deliberately deferred: Phase 2 ships the Telegram bridge before the web
app, so a user's FIRST identity is often "whoever is messaging this bot,"
not an email signup. `auth.admin.create_user()` supports a user with
neither email nor phone (both NotRequired on the underlying type) --
exactly the "headless" anchor a Telegram-only user needs. `telegram_links`
(Sprint 2.1) is what later lets this same identity link a real web account
via the master plan's one-time-code flow, without starting fresh.

Uses the admin API, not `sign_in_anonymously()` -- the latter mutates the
CALLING client's own session state, which would hijack the shared,
service-role-authenticated `app.state.supabase` client used for every
other trusted-backend operation. Admin operations don't touch the caller's
session.
"""

from __future__ import annotations

from typing import Any, cast

from postgrest.exceptions import APIError
from supabase import AsyncClient

_UNIQUE_VIOLATION = "23505"


async def resolve_or_create_user_id(supabase: AsyncClient, telegram_user_id: int) -> str:
    existing = (
        await supabase.table("telegram_links")
        .select("user_id")
        .eq("telegram_user_id", telegram_user_id)
        .execute()
    )
    if existing.data:
        row = cast(dict[str, Any], existing.data[0])
        return str(row["user_id"])

    created = await supabase.auth.admin.create_user({"app_metadata": {"provider": "telegram"}})
    new_user_id = created.user.id

    try:
        await (
            supabase.table("telegram_links")
            .insert({"telegram_user_id": telegram_user_id, "user_id": new_user_id})
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
            await supabase.table("telegram_links")
            .select("user_id")
            .eq("telegram_user_id", telegram_user_id)
            .execute()
        )
        winner_row = cast(dict[str, Any], winner.data[0])
        return str(winner_row["user_id"])

    return str(new_user_id)
