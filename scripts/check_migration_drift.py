"""Fails if the migration files in this repo and the hosted Supabase project's recorded
history disagree (launch plan P2.15).

Reads the output of `supabase migration list --linked` on stdin:

    supabase migration list --linked > migrations.txt
    python scripts/check_migration_drift.py < migrations.txt

Every row has a Local and a Remote column. They must be equal on every row. A migration
that is local-only means the code on this branch expects a database change the hosted
project does not have yet; one that is remote-only means the database was changed by
hand (the dashboard, an MCP `apply_migration`) and no file records it -- the drift this
repo's CLAUDE.md warns about. Either is worth failing for.

It also fails, rather than passes, whenever it cannot be sure it read everything:
  - no rows at all (a changed CLI output format must not turn this into a permanent green);
  - a row that looks like a table row but does not parse, or a JSON row without the keys
    it expects (a silently skipped row is a migration nobody compared);
  - a migration file in supabase/migrations that the CLI did not list, or one whose name
    the CLI would skip (it applies only `<digits>_<name>.sql`).
Exit codes: 0 no drift, 1 drift, 2 could not tell. Both 1 and 2 are red.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_VERSION = re.compile(r"\d+")  # the CLI takes any digit run; `migration new` makes 14
_FILE_NAME = re.compile(r"(\d+)_.*\.sql")  # what the CLI treats as a migration file
_SEPARATOR = re.compile(r"[-\s|:+]*")


class Unreadable(ValueError):
    """The CLI output (or the migrations directory) is not what this script understands."""


def _cell(value: object, where: str) -> str:
    text = "" if value is None else str(value).strip().strip("`").strip()
    if text and not _VERSION.fullmatch(text):
        raise Unreadable(f"{where}: {text!r} is not a migration version")
    return text


def parse(text: str) -> list[tuple[str, str]]:
    """(local, remote) for each migration row; an empty string is "not there".

    Raises Unreadable for anything that looks like a row but cannot be read as one."""
    stripped = text.strip()
    if stripped.startswith("{"):
        # The CLI answers in JSON when it believes a coding agent is calling it.
        try:
            rows = json.loads(stripped)["migrations"]
        except (ValueError, KeyError, TypeError) as error:
            raise Unreadable(f"JSON output without a 'migrations' list ({error!r})") from error
        if not isinstance(rows, list):
            raise Unreadable(f"JSON 'migrations' is not a list: {str(rows)[:80]!r}")
        parsed_json: list[tuple[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                raise Unreadable(f"JSON row is not an object: {str(row)[:80]!r}")
            pair = (_cell(row.get("local"), "JSON local"), _cell(row.get("remote"), "JSON remote"))
            if not (pair[0] or pair[1]):
                raise Unreadable("JSON row with no local or remote version (keys renamed?)")
            parsed_json.append(pair)
        return parsed_json
    parsed: list[tuple[str, str]] = []
    for line in text.splitlines():
        if "|" not in line or _SEPARATOR.fullmatch(line):
            continue  # CLI chatter, blank lines, the separator under the header
        # Split on every '|' and strip nothing else off the line first: a migration that
        # exists only on the project has an EMPTY Local cell, so its row starts with the
        # delimiter, and trimming it would shift the remote version into the Local column.
        # The CLI's table has no outer pipes.
        cells = [cell.strip().strip("`").strip() for cell in line.split("|")]
        if cells[0].lower() == "local":
            continue  # the header
        if len(cells) < 2:
            raise Unreadable(f"table row with fewer than two cells: {line.strip()[:80]!r}")
        pair = (_cell(cells[0], "table Local"), _cell(cells[1], "table Remote"))
        if not (pair[0] or pair[1]):
            raise Unreadable(f"table row with neither a local nor a remote version: {line!r}")
        parsed.append(pair)
    return parsed


def check_files(rows: list[tuple[str, str]], migrations_dir: Path) -> None:
    """Every migration file must have been compared. Raises Unreadable if one was not."""
    if not migrations_dir.is_dir():
        raise Unreadable(f"{migrations_dir} is not a directory")
    listed = {local for local, _ in rows if local}
    skipped, unlisted = [], []
    for path in sorted(migrations_dir.glob("*.sql")):
        match = _FILE_NAME.fullmatch(path.name)
        if match is None:
            skipped.append(path.name)
        elif match.group(1) not in listed:
            unlisted.append(path.name)
    if skipped:
        raise Unreadable(f"the CLI skips these files (name is not <digits>_<name>.sql): {skipped}")
    if unlisted:
        raise Unreadable(f"the CLI did not list these migration files: {unlisted}")


def drift(rows: list[tuple[str, str]]) -> tuple[list[str], list[str], list[str]]:
    """(local only, remote only, present on both sides under different versions)."""
    local_only = [local for local, remote in rows if local and not remote]
    remote_only = [remote for local, remote in rows if remote and not local]
    mismatched = [
        f"{local} != {remote}" for local, remote in rows if local and remote and local != remote
    ]
    return local_only, remote_only, mismatched


def main(text: str, migrations_dir: Path | None = None) -> int:
    try:
        rows = parse(text)
        if rows and migrations_dir is not None:
            check_files(rows, migrations_dir)
    except Unreadable as error:
        print(f"Cannot tell whether there is drift: {error}", file=sys.stderr)
        return 2
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
    _default_dir = Path(__file__).resolve().parent.parent / "supabase" / "migrations"
    sys.exit(main(sys.stdin.read(), Path(sys.argv[1]) if len(sys.argv) > 1 else _default_dir))
