"""Delete a user's account and everything they own (launch plan P4.5).

The order matters, and so does which steps may fail:

1. Remove every file under the user's folder in Storage. This one must succeed, so it goes first.
   Objects do not cascade with the user, so an account deleted around a failed removal would leave
   a stranger's resumes in the bucket with nobody left to ask for their removal. A failure here
   stops everything and, because nothing else has run yet, changes nothing at all: no connection
   is cut, no session ended. Sending the request again is safe.
2. Revoke the user's Gmail access at Google (best effort). It has to happen before the user is
   deleted: once the stored refresh token is gone there is no way to revoke it, and Google keeps
   honouring a token nobody can use any more.
3. End their sessions (best effort): extension tokens issued so far are rejected from now on, and
   every Supabase session for the user is signed out.
4. Forget the Telegram lockout counters kept for the user's linked chats (they are keyed by the
   Telegram subject, not by the user, so deleting the user does not reach them).
5. Delete the user. Every table that references the user does so with `on delete cascade`, so this
   removes every row they own in one step. It cannot be undone and afterwards there is nobody left
   who could retry anything, so it comes after every step that needs the user to still exist.
6. Remove what no cascade reaches: Supabase Auth's own audit log names the user, with their email,
   in its logins and in the entry the deletion itself writes. This runs after the deletion for
   that reason, with the email read before it. Best effort and reported.
7. Check, and log if anything is left. This should be impossible; the check is for a table added
   later without a cascade.

Every step is safe to run again, so a request that failed part-way is retried by sending it again.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, cast

import httpx
from supabase_auth.errors import AuthApiError

from supabase import AsyncClient

from .extension_auth import record_extension_sign_out
from .provider_credentials_store import CredentialNotFound, get_decrypted_credential

logger = logging.getLogger(__name__)

CONFIRMATION_PHRASE = "delete my account"
"""What the person types to confirm. The route and the web app both use it."""


def is_confirmed(typed: str) -> bool:
    """Whether what was typed is the confirmation phrase: case and surrounding or repeated
    whitespace do not matter, the words do."""
    return " ".join(typed.lower().split()) == CONFIRMATION_PHRASE


GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

_BUCKET = "artifacts"
_PAGE_SIZE = 100
_REMOVE_BATCH = 100
_MAX_LIST_DEPTH = 6
_MAX_REMOVE_PASSES = 3
"""A hard cap: files uploaded by a request still running when deletion starts can show up in a
second listing, but not forever."""
_GOOGLE_TIMEOUT_SECONDS = 10.0


class AccountDeletionBlocked(Exception):
    """Something that must be removed first could not be, so nothing was deleted."""


@dataclass(frozen=True)
class DeletionReport:
    gmail_revoked: bool | None
    """None: no Gmail connection. False: Google could not be reached or refused."""
    files_removed: int
    link_attempts_purged: int
    user_deleted: bool
    """False when the user was already gone (a request sent a second time)."""
    audit_entries_purged: int | None
    """Auth audit-log entries removed; None when that could not be done."""
    leftover_tables: dict[str, int]
    """What the user still owned afterwards, per table. Empty unless a table lacks a cascade."""


async def delete_account(
    supabase: AsyncClient,
    http: httpx.AsyncClient,
    user_id: str,
    *,
    access_token: str | None = None,
) -> DeletionReport:
    files_removed = await _remove_files(supabase, user_id)
    gmail_revoked = await _revoke_gmail(supabase, http, user_id)
    await _end_sessions(supabase, user_id, access_token)
    purged = await _forget_link_attempts(supabase, user_id)
    email = await _email_of(supabase, user_id)
    user_deleted = await _delete_user(supabase, user_id)
    audit_purged = await _purge_audit_log(supabase, user_id, email)
    leftovers = await _leftovers(supabase, user_id)
    report = DeletionReport(
        gmail_revoked, files_removed, purged, user_deleted, audit_purged, leftovers
    )
    logger.info(
        "account deleted",
        extra={
            "ctx": {
                "user_id": user_id,
                "gmail_revoked": gmail_revoked,
                "files_removed": files_removed,
                "link_attempts_purged": purged,
                "already_gone": not user_deleted,
                "audit_entries_purged": audit_purged,
            }
        },
    )
    return report


# -- 2. Gmail ---------------------------------------------------------------------------------


async def _revoke_gmail(
    supabase: AsyncClient, http: httpx.AsyncClient, user_id: str
) -> bool | None:
    try:
        credential = await get_decrypted_credential(
            supabase, user_id, service="oauth", provider="gmail"
        )
    except CredentialNotFound:
        return None
    except Exception:
        logger.warning("could not read the Gmail credential to revoke it", exc_info=True)
        return False
    try:
        response = await http.post(
            GOOGLE_REVOKE_URL,
            data={"token": credential["secret"]},
            timeout=_GOOGLE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        logger.warning("Google could not be reached to revoke a Gmail token", exc_info=True)
        return False
    if response.status_code == 200:
        return True
    if response.status_code == 400 and "invalid_token" in response.text:
        return True  # already expired or revoked: nothing left to revoke
    logger.warning(
        "Google refused to revoke a Gmail token", extra={"ctx": {"status": response.status_code}}
    )
    return False


# -- 3. sessions ------------------------------------------------------------------------------


async def _end_sessions(supabase: AsyncClient, user_id: str, access_token: str | None) -> None:
    try:
        await record_extension_sign_out(supabase, user_id)
    except Exception:
        logger.warning("could not sign the extension out", exc_info=True)
    if access_token is None:
        return
    try:
        await supabase.auth.admin.sign_out(access_token, "global")
    except Exception:
        # Deleting the user removes every session and refresh token anyway; this only narrows
        # the gap if a later step fails and the person keeps using the account.
        logger.warning("could not sign every session out", exc_info=True)


# -- 1. files ---------------------------------------------------------------------------------


async def _list_paths(bucket: Any, prefix: str, depth: int = 0) -> list[str]:
    """Every file under `prefix`, recursively. Storage lists one level at a time, a page at a time,
    and returns a folder as an entry with no id."""
    if depth > _MAX_LIST_DEPTH:
        raise AccountDeletionBlocked("the storage listing is nested too deeply")
    paths: list[str] = []
    offset = 0
    while True:
        page = cast(
            list[dict[str, Any]],
            await bucket.list(prefix, {"limit": _PAGE_SIZE, "offset": offset}),
        )
        for entry in page:
            path = f"{prefix}/{entry['name']}"
            if entry.get("id") is None:
                paths.extend(await _list_paths(bucket, path, depth + 1))
            else:
                paths.append(path)
        if len(page) < _PAGE_SIZE:
            return paths
        offset += _PAGE_SIZE


async def _remove_files(supabase: AsyncClient, user_id: str) -> int:
    bucket = supabase.storage.from_(_BUCKET)
    removed = 0
    try:
        for _ in range(_MAX_REMOVE_PASSES):
            paths = await _list_paths(bucket, user_id)
            if not paths:
                return removed
            for start in range(0, len(paths), _REMOVE_BATCH):
                batch = paths[start : start + _REMOVE_BATCH]
                await bucket.remove(batch)
                removed += len(batch)
    except AccountDeletionBlocked:
        raise
    except Exception as e:
        raise AccountDeletionBlocked("a stored file could not be removed") from e
    if await _list_paths(bucket, user_id):
        raise AccountDeletionBlocked("files remain after removing them")
    return removed


# -- 4. Telegram lockout counters -------------------------------------------------------------


async def _forget_link_attempts(supabase: AsyncClient, user_id: str) -> int:
    identities = cast(
        list[dict[str, Any]],
        (
            await supabase.table("channel_identities")
            .select("channel, external_subject")
            .eq("user_id", user_id)
            .execute()
        ).data,
    )
    purged = 0
    for identity in identities:
        deleted = (
            await supabase.table("link_code_attempts")
            .delete()
            .eq("channel", identity["channel"])
            .eq("external_subject", identity["external_subject"])
            .execute()
        )
        purged += len(deleted.data)
    return purged


# -- 5. the user ------------------------------------------------------------------------------


async def _delete_user(supabase: AsyncClient, user_id: str) -> bool:
    try:
        await supabase.auth.admin.delete_user(user_id)
    except AuthApiError as e:
        if getattr(e, "status", None) == 404 or getattr(e, "code", None) == "user_not_found":
            return False
        raise
    return True


# -- 6. the audit log ------------------------------------------------------------------------


async def _email_of(supabase: AsyncClient, user_id: str) -> str | None:
    """Read before the user is deleted: the audit-log entry the deletion itself writes names the
    email, and by then it can no longer be looked up."""
    try:
        return (await supabase.auth.admin.get_user_by_id(user_id)).user.email
    except Exception:
        # Not fatal: the audit log is then purged by id alone (its login entries carry it).
        logger.warning("could not read the account's email before deleting it", exc_info=True)
        return None


async def _purge_audit_log(supabase: AsyncClient, user_id: str, email: str | None) -> int | None:
    try:
        result = await supabase.rpc(
            "purge_user_auth_traces", {"p_user_id": user_id, "p_email": email}
        ).execute()
    except Exception:
        logger.error("could not remove a deleted account from the auth audit log", exc_info=True)
        return None
    return cast(int, result.data)


# -- 7. the check -----------------------------------------------------------------------------


async def _leftovers(supabase: AsyncClient, user_id: str) -> dict[str, int]:
    try:
        result = await supabase.rpc("user_owned_row_counts", {"p_user_id": user_id}).execute()
    except Exception:
        logger.warning("could not check what is left after deleting an account", exc_info=True)
        return {}
    left = cast(dict[str, int], result.data or {})
    if left:
        logger.error(
            "rows remain after deleting an account (a table without a cascade?)",
            extra={"ctx": {"user_id": user_id, "tables": left}},
        )
    return left
