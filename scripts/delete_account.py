"""Delete a user's account from the command line (launch plan P4.5): the operator's way in.

For a deletion request that arrives by email rather than through the app's "Delete my account"
button. It runs the same code as the button (`between_jobs.api.account_deletion`): files, the
Gmail revoke, sessions, the user and everything that cascades from them, and the auth audit log.

Run it against the right project or not at all. The target comes from the shell environment, never
from `.env` (this script does not load it), and `--confirm-ref` must name the project the URL
points at, typed out, so a stale or swapped variable cannot delete somebody on the wrong project:

    SUPABASE_URL=https://<ref>.supabase.co SUPABASE_SERVICE_ROLE_KEY=... \\
      python scripts/delete_account.py --email person@example.com --confirm-ref <ref> --dry-run

`--dry-run` reads and reports what would go (rows per table, stored files, whether Gmail is
connected) and changes nothing. Without it the account is deleted for good, immediately, so
check the dry run first.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any
from urllib.parse import urlparse

import httpx

from between_jobs.api.account_deletion import _list_paths, delete_account
from between_jobs.api.provider_credentials_store import CredentialNotFound, get_decrypted_credential
from supabase import AsyncClient, acreate_client

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_USERS_PER_PAGE = 200
_MAX_USER_PAGES = 100  # 20,000 users: a hard stop, not a promise


class Refusal(Exception):
    """The run was refused before it changed anything."""


def project_ref(url: str) -> str:
    """`<ref>` for `https://<ref>.supabase.co`, `local` for a local stack; anything else is
    refused, because a destination that cannot be named cannot be confirmed."""
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    labels = host.split(".")
    if host in _LOCAL_HOSTS:
        return "local"
    plain_ref = bool(labels[0]) and all(
        c.isascii() and (c.isalnum() or c == "-") for c in labels[0]
    )
    if (
        parsed.scheme == "https"
        and host.endswith(".supabase.co")
        and len(labels) == 3
        and plain_ref
    ):
        return labels[0]
    raise Refusal(f"cannot tell which Supabase project {url!r} is")


def check_target(url: str, confirm_ref: str) -> None:
    actual = project_ref(url)
    if confirm_ref != actual:
        raise Refusal(
            f"--confirm-ref {confirm_ref!r} does not match the project in SUPABASE_URL "
            f"({actual!r}); the project must be named exactly"
        )


async def resolve_user(
    supabase: AsyncClient, *, user_id: str | None, email: str | None
) -> tuple[str, str | None]:
    """(id, email) of the one account asked for. By id it must exist; by email it must match
    exactly one account."""
    admin = supabase.auth.admin
    if user_id is not None:
        found = (await admin.get_user_by_id(user_id)).user
        return str(found.id), found.email
    assert email is not None
    wanted = email.strip().lower()
    matches: list[Any] = []
    for page in range(1, _MAX_USER_PAGES + 1):
        users = await admin.list_users(page=page, per_page=_USERS_PER_PAGE)
        matches.extend(u for u in users if (u.email or "").lower() == wanted)
        if len(users) < _USERS_PER_PAGE:
            break
    if len(matches) != 1:
        raise Refusal(f"{len(matches)} accounts match {email!r}; expected exactly one")
    return str(matches[0].id), matches[0].email


async def describe(supabase: AsyncClient, user_id: str) -> dict[str, Any]:
    """What deleting the account would remove. Reads only."""
    counts = (await supabase.rpc("user_owned_row_counts", {"p_user_id": user_id}).execute()).data
    files = await _list_paths(supabase.storage.from_("artifacts"), user_id)
    try:
        await get_decrypted_credential(supabase, user_id, service="oauth", provider="gmail")
        gmail = True
    except CredentialNotFound:
        gmail = False
    return {"rows_per_table": counts, "stored_files": len(files), "gmail_connected": gmail}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--user-id")
    who.add_argument("--email")
    parser.add_argument(
        "--confirm-ref", required=True, help="the project ref of SUPABASE_URL, typed out"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would go, change nothing"
    )
    return parser


async def run(args: argparse.Namespace, url: str, key: str) -> int:
    check_target(url, args.confirm_ref)
    supabase = await acreate_client(url, key)
    user_id, email = await resolve_user(supabase, user_id=args.user_id, email=args.email)
    print(f"account: {user_id} ({email})")
    summary = await describe(supabase, user_id)
    print(f"owns: {summary['rows_per_table']}")
    print(f"stored files: {summary['stored_files']}; Gmail connected: {summary['gmail_connected']}")
    if args.dry_run:
        print("[DRY RUN] nothing was changed")
        return 0
    async with httpx.AsyncClient() as http:
        report = await delete_account(supabase, http, user_id)
    print(f"deleted: {report}")
    return 1 if report.leftover_tables else 0


def main() -> None:
    args = _parser().parse_args()
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        sys.exit("refused: set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the shell")
    try:
        sys.exit(asyncio.run(run(args, url, key)))
    except Refusal as refusal:
        sys.exit(f"refused: {refusal}")


if __name__ == "__main__":
    main()
