"""Fails if the migration files in this repo and the hosted Supabase project's recorded
history disagree (launch plan P2.15).

Reads the output of `supabase migration list --linked` on stdin:

    supabase migration list --linked | python scripts/check_migration_drift.py

Every row has a Local and a Remote column. They must be equal on every row. A migration
that is local-only means the code on this branch expects a database change the hosted
project does not have yet; one that is remote-only means the database was changed by
hand (the dashboard, an MCP `apply_migration`) and no file records it -- the drift this
repo's CLAUDE.md warns about. Either is worth failing for.

It also fails, rather than passes, when it cannot read a single row: a changed CLI output
format must not turn this check into a permanent green.
"""

from __future__ import annotations

import json
import re
import sys

_VERSION = re.compile(r"\d{14}|")  # a migration version, or an empty cell


def parse(text: str) -> list[tuple[str, str]]:
    """(local, remote) for each migration row; an empty string is "not there"."""
    stripped = text.strip()
    if stripped.startswith("{"):
        # The CLI answers in JSON when it believes a coding agent is calling it.
        rows = json.loads(stripped)["migrations"]
        return [(str(row.get("local") or ""), str(row.get("remote") or "")) for row in rows]
    parsed: list[tuple[str, str]] = []
    for line in text.splitlines():
        # Split on every '|' and strip nothing else off the line first: a migration that
        # exists only on the project has an EMPTY Local cell, so its row starts with the
        # delimiter, and trimming it would shift the remote version into the Local column.
        # The CLI's table has no outer pipes.
        cells = [cell.strip().strip("`").strip() for cell in line.split("|")]
        if len(cells) < 2 or not (_VERSION.fullmatch(cells[0]) and _VERSION.fullmatch(cells[1])):
            continue  # the header, the separator, blank lines, CLI chatter
        if cells[0] or cells[1]:
            parsed.append((cells[0], cells[1]))
    return parsed


def drift(rows: list[tuple[str, str]]) -> tuple[list[str], list[str], list[str]]:
    """(local only, remote only, present on both sides under different versions)."""
    local_only = [local for local, remote in rows if local and not remote]
    remote_only = [remote for local, remote in rows if remote and not local]
    mismatched = [
        f"{local} != {remote}" for local, remote in rows if local and remote and local != remote
    ]
    return local_only, remote_only, mismatched


def main(text: str) -> int:
    rows = parse(text)
    if not rows:
        print(
            "Could not read any migration rows from the CLI output; refusing to call that "
            "'no drift'. Output began:\n" + text[:400],
            file=sys.stderr,
        )
        return 2
    local_only, remote_only, mismatched = drift(rows)
    if not (local_only or remote_only or mismatched):
        print(f"No drift: {len(rows)} migrations, local and remote match.")
        return 0
    if local_only:
        listed = ", ".join(local_only)
        print(f"In this repo but not applied to the project ({len(local_only)}): {listed}")
    if remote_only:
        listed = ", ".join(remote_only)
        print(f"Applied to the project but no file here ({len(remote_only)}): {listed}")
    if mismatched:
        listed = ", ".join(mismatched)
        print(f"Recorded under a different version ({len(mismatched)}): {listed}")
    print(
        "Migrations ship only through the CLI (`supabase migration new`, `supabase db push`); see "
        'CLAUDE.md, "Database migrations".',
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.stdin.read()))
