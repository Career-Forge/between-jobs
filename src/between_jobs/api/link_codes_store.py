"""Persistence for the /link one-time-code flow (Sprint 2.8c/2.8d) --
Proposal §14's Telegram-linking flow: mint a short-lived code, store only
its hash, and consume it transactionally (hash-match, expiry, rate
limit, and the full account merge, all in one Postgres transaction --
see the `consume_link_code`/`merge_user_data` migration for why this
can't be split across Python round trips without reopening a race).

The raw code exists in memory only for the duration of `mint_code` (and
whatever the caller does with the return value) -- it is never written to
a log or a database row, only its sha256 hash. Callers own not logging it
either; nothing here can enforce that past its own boundary.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from postgrest.exceptions import APIError

from supabase import AsyncClient

logger = logging.getLogger(__name__)

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
"""Excludes 0/O and 1/I/L -- easy to misread when a human is retyping this
into a Telegram chat by hand."""

_CODE_LENGTH = 8
_CODE_TTL_SECONDS = 10 * 60


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _generate_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


async def mint_code(supabase: AsyncClient, user_id: str, channel: str) -> tuple[str, str]:
    """Returns (raw_code, expires_at_iso) -- the only place the raw code
    is ever returned; losing it means minting a new one, not recovering
    this one. Invalidates any other still-pending code for this exact
    (user, channel) first -- only one valid code at a time, so a /link
    attempt is never ambiguous about which code it's redeeming."""
    await (
        supabase.table("link_codes")
        .delete()
        .eq("user_id", user_id)
        .eq("channel", channel)
        .is_("consumed_at", "null")
        .execute()
    )

    code = _generate_code()
    expires_at = datetime.now(UTC) + timedelta(seconds=_CODE_TTL_SECONDS)
    await (
        supabase.table("link_codes")
        .insert(
            {
                "user_id": user_id,
                "channel": channel,
                "code_hash": _hash_code(code),
                "expires_at": expires_at.isoformat(),
            }
        )
        .execute()
    )
    return code, expires_at.isoformat()


async def consume_link_code(
    supabase: AsyncClient,
    *,
    channel: str,
    external_subject: str,
    code: str,
    source_user_id: str,
) -> dict[str, Any]:
    """Wraps the `consume_link_code` Postgres function (Sprint 2.8d) --
    hash-matching, expiry, rate-limiting, marking the code consumed, and
    running the full account merge all happen inside that one atomic
    transaction, not here; this is a thin RPC call, not its own consume
    logic (splitting that across two Python round trips would reopen the
    exact race the SQL function exists to close).

    Returns the function's own JSON result. Refusals are
    `{"ok": false, "reason": ...}`, the reason being one of
    "invalid_code", "expired_code", "rate_limited", "source_mismatch" (a
    concurrent link for the same Telegram account got there first),
    "source_already_linked" (this account is already linked to a web
    account) or "target_linked_elsewhere" (the code's owner already has a
    different Telegram account). Success is `{"ok": true,
    "target_user_id": ..., "source_user_id": ..., "summary": {...}}`;
    the same account resubmitting a code it already used gets
    `{"ok": true, "resumed": true, "target_user_id": ...,
    "source_user_id": ...}` with no summary, and a code minted by the
    account itself comes back with target and source the same.

    Committing the merge is not the end of a link: `finish_link` (in
    link_completion.py) moves what the database can't and retires the
    source account. Anything the SQL raises -- a unique violation (23505)
    from two accounts racing to link the same web account, a statement
    timeout, a refused merge (BJ00x) -- rolled the whole transaction back
    and surfaces as a raised `postgrest.exceptions.APIError`, which this
    function doesn't catch; callers turn it into a user-facing message
    themselves."""
    result = await supabase.rpc(
        "consume_link_code",
        {
            "p_channel": channel,
            "p_external_subject": external_subject,
            "p_code": code,
            "p_source_user_id": source_user_id,
        },
    ).execute()
    return cast(dict[str, Any], result.data)


async def finish_link_merge(
    supabase: AsyncClient,
    *,
    channel: str,
    subject: str,
    source_user_id: str,
    target_user_id: str,
) -> dict[str, Any]:
    """Runs the merge again for an already-consumed link (safe to repeat:
    every write in it is scoped to what the source still owns, so once
    that's nothing it's a no-op). `{"ok": true, "source_gone": true}` when
    the source account no longer exists; otherwise `{"ok": true,
    "source_gone": false, "summary": {...}}`. Raises `APIError` unless
    `target_user_id` is the confirmed target of a link this Telegram
    subject consumed in the last 24 hours."""
    result = await supabase.rpc(
        "finish_link_merge",
        {
            "p_channel": channel,
            "p_subject": subject,
            "p_source": source_user_id,
            "p_target": target_user_id,
        },
    ).execute()
    return cast(dict[str, Any], result.data)


async def finish_link_delete_source(
    supabase: AsyncClient,
    *,
    channel: str,
    subject: str,
    source_user_id: str,
    target_user_id: str,
) -> dict[str, Any]:
    """Deletes the source auth user -- but only if it owns nothing at all,
    checked again inside the function under a row lock. `{"ok": true,
    "source_gone": true}` once it's gone; `{"ok": true, "source_gone":
    false, "counts": {...}}` naming what it still owns otherwise."""
    result = await supabase.rpc(
        "finish_link_delete_source",
        {
            "p_channel": channel,
            "p_subject": subject,
            "p_source": source_user_id,
            "p_target": target_user_id,
        },
    ).execute()
    return cast(dict[str, Any], result.data)


async def user_owned_row_counts(supabase: AsyncClient, user_id: str) -> dict[str, int]:
    """What a user still owns, by table -- every table with a foreign key
    to auth.users, plus three references that aren't foreign keys:
    `application_events.actor_id`, the denormalized
    `artifact_versions.storage_key`, and storage objects under the user's
    prefix. Empty means nothing. Table names and counts only, never row
    contents."""
    result = await supabase.rpc("user_owned_row_counts", {"p_user_id": user_id}).execute()
    return cast(dict[str, int], result.data)


_PROBE_USER_ID = "00000000-0000-0000-0000-000000000000"
_FUNCTION_MISSING = "PGRST202"
_schema_ready = False


async def link_schema_ready(supabase: AsyncClient) -> bool:
    """Whether the database has the functions the link flow now calls.

    A deploy that reaches the server before the migration would run the new
    `/link` code against the old `consume_link_code`, which merges without
    the checks and hands back none of what `finish_link` needs. Asking for a
    function only the migration creates catches that: False means "don't
    start a link", not "something is wrong with this user". Once it's seen
    ready it stays ready for the life of the process -- a migration is never
    rolled back under a running server."""
    global _schema_ready
    if _schema_ready:
        return True
    try:
        await user_owned_row_counts(supabase, _PROBE_USER_ID)
    except APIError as e:
        # PGRST202 is PostgREST saying no such function; anything else (its
        # schema cache reloading right after the migration, a gateway error, a
        # timeout) says nothing about whether the migration is there.
        message = (
            "the link functions aren't installed"
            if e.code == _FUNCTION_MISSING
            else "couldn't check that the link functions are installed"
        )
        logger.error(message, extra={"ctx": {"code": e.code}})
        return False
    _schema_ready = True
    return True
