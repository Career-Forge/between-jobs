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
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from supabase import AsyncClient

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

    Returns the function's own JSON result for the three EXPECTED
    outcomes: `{"ok": false, "reason": "invalid_code" | "expired_code" |
    "rate_limited"}` or `{"ok": true, "target_user_id": ..., "summary":
    {...}}`. A genuine merge collision (both accounts already have
    conflicting data) is NOT one of these -- it surfaces as a raised
    `postgrest.exceptions.APIError` with code "23505", which this
    function doesn't catch; callers that need to turn that into a clean
    user-facing message do so themselves."""
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
