"""Static guards on the account-merge migration (P0.7) -- run in the fast suite,
no database needed.

They read the migration files as text, so they're approximate by nature (a
regex, not a SQL parser). What they buy is an early, cheap failure when a new
table or function is added without teaching the merge about it; the exact
answer -- what the database's own catalog says -- is checked by
tests/integration/test_local_link_merge.py against a local stack."""

from __future__ import annotations

import re
from pathlib import Path

from between_jobs.api.artifact_versions_store import _ARTIFACT_ID_NAMESPACE

_MIGRATIONS = Path(__file__).parent.parent / "supabase" / "migrations"
_MERGE = next(_MIGRATIONS.glob("*_fix_merge_user_data.sql"))

# Migrations from this one on grant explicitly (see CLAUDE.md, "Grant explicitly").
_EXPLICIT_GRANTS_FROM = "20260925114000"


def _without_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _balanced(sql: str, open_paren: int) -> str:
    """The text inside the parenthesis opened at `open_paren`."""
    depth = 0
    for i in range(open_paren, len(sql)):
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return sql[open_paren + 1 : i]
    raise AssertionError("unbalanced parenthesis in a migration")


def _tables_referencing_auth_users() -> set[str]:
    tables: set[str] = set()
    for path in sorted(_MIGRATIONS.glob("*.sql")):
        sql = _without_comments(path.read_text())
        for m in re.finditer(
            r"create\s+table\s+(?:if\s+not\s+exists\s+)?public\.(\w+)\s*\(", sql, re.IGNORECASE
        ):
            if re.search(r"references\s+auth\.users", _balanced(sql, m.end() - 1), re.IGNORECASE):
                tables.add(m.group(1))
        for m in re.finditer(
            r"alter\s+table\s+(?:only\s+)?public\.(\w+)\s+add\s+column[^;]*?references\s+auth\.users",
            sql,
            re.IGNORECASE,
        ):
            tables.add(m.group(1))
        for m in re.finditer(
            r"drop\s+table\s+(?:if\s+exists\s+)?public\.(\w+)", sql, re.IGNORECASE
        ):
            tables.discard(m.group(1))
    return tables


def _merge_function_body() -> str:
    sql = _without_comments(_MERGE.read_text())
    start = sql.index("create or replace function public.merge_user_data(")
    end = sql.index("revoke execute on function public.merge_user_data")
    return sql[start:end]


def test_every_table_owned_by_a_user_is_handled_by_merge_user_data() -> None:
    """A table with a foreign key to auth.users that merge_user_data never
    mentions would make every link that touches it refuse (the completeness
    check at the end of the function) -- or, before that check existed, lose
    the rows to a cascade. This fails first, in the fast suite."""
    body = _merge_function_body()
    unhandled = sorted(
        table for table in _tables_referencing_auth_users() if f"public.{table}" not in body
    )
    assert unhandled == []


def test_the_scan_finds_the_tables_it_should() -> None:
    """Guards the guard: if the regexes above stopped matching, the test
    before this one would pass on an empty set."""
    tables = _tables_referencing_auth_users()
    assert len(tables) >= 28
    assert {"applications", "profile_versions", "career_facts", "channel_identities"} <= tables


def test_every_function_from_the_explicit_grants_migration_on_revokes_public_access() -> None:
    """New Supabase projects grant nothing by default but prod grants EXECUTE
    on new functions to anon and authenticated; nothing may rely on either."""
    offenders = []
    for path in sorted(_MIGRATIONS.glob("*.sql")):
        if path.name.split("_")[0] < _EXPLICIT_GRANTS_FROM:
            continue
        sql = _without_comments(path.read_text())
        for m in re.finditer(
            r"create\s+(?:or\s+replace\s+)?function\s+public\.(\w+)\s*\(", sql, re.IGNORECASE
        ):
            name = m.group(1)
            revoked = re.search(
                rf"revoke\s+execute\s+on\s+function\s+public\.{name}\s*\([^)]*\)\s+from\s+public",
                sql,
                re.IGNORECASE,
            )
            if not revoked:
                offenders.append(f"{path.name}: {name}")
    assert offenders == []


def test_the_merge_migration_uses_the_python_artifact_namespace() -> None:
    """The collapse recomputes artifact ids in SQL; they must equal what
    artifact_versions_store.artifact_id_for derives in Python."""
    match = re.search(r"v_ns\s+constant\s+uuid\s*:=\s*'([0-9a-f-]+)'", _MERGE.read_text())
    assert match is not None
    assert match.group(1) == str(_ARTIFACT_ID_NAMESPACE)


def test_merge_user_data_is_reachable_by_no_api_role() -> None:
    """Only consume_link_code and finish_link_merge (both owned by postgres)
    call it; an RPC-reachable merge would be a hijack on its own."""
    sql = _without_comments(_MERGE.read_text())
    assert re.search(
        r"revoke\s+execute\s+on\s+function\s+public\.merge_user_data\s*\([^)]*\)\s+"
        r"from\s+public,\s*anon,\s*authenticated,\s*service_role",
        sql,
        re.IGNORECASE,
    )
