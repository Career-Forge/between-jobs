"""Static guards on the Discord migration (`*_discord_link_and_interaction_claims.sql`): run in the
fast suite, no database needed. They read the file as text, so they are approximate by nature; the
exact answers (what the functions do, what each role may call) are in
tests/integration/test_local_discord_link.py and
tests/integration/test_local_discord_interaction_dedup.py against a local stack.

What is pinned here is the thing a database test cannot see: that the three functions this
migration re-creates (`merge_user_data`, `consume_link_code`, `finish_link_merge`) are the
EXISTING ones with exactly the intended lines changed. They are long, and a copy that drifted by a
typing slip would change the merge for Telegram too."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_MIGRATIONS = Path(__file__).parent.parent / "supabase" / "migrations"
_DISCORD = next(_MIGRATIONS.glob("*_discord_link_and_interaction_claims.sql"))
_MERGE_BEFORE = next(
    _MIGRATIONS.glob("*_merge_user_data_handles_product_events_and_enrollments.sql")
)
_LINK_BEFORE = next(_MIGRATIONS.glob("*_fix_merge_user_data.sql"))


def _function(sql: str, name: str, *, ends_before: str) -> str:
    start = sql.index(f"create or replace function public.{name}(")
    return sql[start : sql.index(ends_before, start)]


def _lines(text: str) -> list[str]:
    return text.splitlines()


def _changed(before: str, after: str) -> tuple[list[str], list[str]]:
    """(lines only in `before`, lines only in `after`), as multisets in order."""
    import difflib

    removed: list[str] = []
    added: list[str] = []
    for line in difflib.unified_diff(_lines(before), _lines(after), lineterm="", n=0):
        if line.startswith(("---", "+++", "@@")):
            continue
        (removed if line.startswith("-") else added).append(line[1:])
    return removed, added


def test_merge_user_data_differs_from_its_previous_body_in_exactly_two_lines() -> None:
    before = _function(
        _MERGE_BEFORE.read_text(),
        "merge_user_data",
        ends_before="revoke execute on function public.merge_user_data",
    )
    after = _function(
        _DISCORD.read_text(),
        "merge_user_data",
        ends_before="revoke execute on function public.merge_user_data",
    )
    removed, added = _changed(before, after)
    assert [line.strip() for line in removed] == [
        "p_source_user_id uuid, p_target_user_id uuid, p_subject text",
        "if not public.is_auto_provisioned_telegram_user(p_source_user_id, p_subject) then",
    ]
    assert [line.strip() for line in added] == [
        "p_source_user_id uuid, p_target_user_id uuid, p_channel text, p_subject text",
        "if not public.is_auto_provisioned_channel_user("
        "p_source_user_id, p_channel, p_subject) then",
    ]


def test_consume_link_code_differs_from_its_previous_body_only_where_intended() -> None:
    before = _function(
        _LINK_BEFORE.read_text(),
        "consume_link_code",
        ends_before="revoke execute on function public.consume_link_code",
    )
    after = _function(
        _DISCORD.read_text(),
        "consume_link_code",
        ends_before="revoke execute on function public.consume_link_code",
    )
    removed, added = _changed(before, after)
    code_removed = [line.strip() for line in removed if not line.strip().startswith("--")]
    code_added = [line.strip() for line in added if not line.strip().startswith("--")]
    assert code_removed == [
        "if p_channel <> 'telegram' then",
        "if not public.is_auto_provisioned_telegram_user("
        "p_source_user_id, p_external_subject) then",
        "v_summary := public.merge_user_data("
        "p_source_user_id, v_code_row.user_id, p_external_subject);",
    ]
    assert code_added == [
        "if p_channel not in ('telegram', 'discord') then",
        "if not public.is_auto_provisioned_channel_user("
        "p_source_user_id, p_channel, p_external_subject) then",
        "v_summary := public.merge_user_data(",
        "p_source_user_id, v_code_row.user_id, p_channel, p_external_subject",
        ");",
    ]
    # The rest of the change is wording in comments: "Telegram" became "chat".
    comment_changes = [line for line in removed + added if line.strip().startswith("--")]
    assert comment_changes and all(
        "Telegram" in line or "chat" in line or "finishing the storage move" in line
        for line in comment_changes
    )


def test_finish_link_merge_differs_from_its_previous_body_only_where_intended() -> None:
    before = _function(
        _LINK_BEFORE.read_text(),
        "finish_link_merge",
        ends_before="revoke execute on function public.finish_link_merge",
    )
    after = _function(
        _DISCORD.read_text(),
        "finish_link_merge",
        ends_before="revoke execute on function public.finish_link_merge",
    )
    removed, added = _changed(before, after)
    assert [line.strip() for line in removed if not line.strip().startswith("--")] == [
        "'summary', public.merge_user_data(p_source, p_target, p_subject)"
    ]
    assert [line.strip() for line in added if not line.strip().startswith("--")] == [
        "'summary', public.merge_user_data(p_source, p_target, p_channel, p_subject)"
    ]


def test_the_telegram_only_guard_and_the_three_argument_merge_are_dropped() -> None:
    sql = re.sub(r"--[^\n]*", "", _DISCORD.read_text())
    assert "drop function public.merge_user_data(uuid, uuid, text);" in sql
    assert "drop function public.is_auto_provisioned_telegram_user(uuid, text);" in sql
    # and nothing the migration creates still calls the dropped functions
    assert "is_auto_provisioned_telegram_user(p_" not in sql
    assert not re.search(
        r"merge_user_data\(\s*p_source_user_id,\s*[^,]+,\s*p_(external_)?subject\s*\)", sql
    )


def test_the_new_table_is_closed_to_every_api_role_and_has_row_level_security() -> None:
    sql = re.sub(r"--[^\n]*", "", _DISCORD.read_text())
    assert "create table public.discord_processed_interactions" in sql
    assert "alter table public.discord_processed_interactions enable row level security" in sql
    assert re.search(
        r"revoke all on table public\.discord_processed_interactions\s+"
        r"from public, anon, authenticated, service_role",
        sql,
    )
    assert "create policy" not in sql.lower()
    assert not re.search(r"grant\s+[^;]*on table public\.discord_processed_interactions", sql)


@pytest.mark.parametrize(
    "name",
    ["claim_discord_interaction", "complete_discord_interaction", "release_discord_interaction"],
)
def test_each_claim_function_is_security_definer_pins_its_search_path_and_is_for_the_backend_only(
    name: str,
) -> None:
    sql = re.sub(r"--[^\n]*", "", _DISCORD.read_text())
    definition = sql[sql.index(f"create function public.{name}(") :].split("$$;")[0]
    assert "security definer" in definition
    assert "set search_path = ''" in definition
    assert re.search(
        rf"revoke\s+execute\s+on\s+function\s+public\.{name}\s*\([^)]*\)\s+"
        r"from\s+public, anon, authenticated",
        sql,
    )
    assert re.search(
        rf"grant\s+execute\s+on\s+function\s+public\.{name}\s*\([^)]*\)\s+to\s+service_role", sql
    )


def test_the_two_internal_functions_are_reachable_by_no_api_role() -> None:
    sql = re.sub(r"--[^\n]*", "", _DISCORD.read_text())
    for name, args in (
        ("merge_user_data", r"uuid, uuid, text, text"),
        ("is_auto_provisioned_channel_user", r"uuid, text, text"),
    ):
        assert re.search(
            rf"revoke\s+execute\s+on\s+function\s+public\.{name}\s*\({args}\)\s+"
            r"from\s+public, anon, authenticated, service_role",
            sql,
        ), name
        assert not re.search(rf"grant\s+execute\s+on\s+function\s+public\.{name}\b", sql), name


def test_the_guard_names_the_channel_in_both_metadata_keys_and_only_for_known_channels() -> None:
    sql = re.sub(r"--[^\n]*", "", _DISCORD.read_text())
    guard = sql[sql.index("create or replace function public.is_auto_provisioned_channel_user(") :]
    guard = guard.split("$$;")[0]
    assert "p_channel in ('telegram', 'discord')" in guard
    assert "'bj_provisioned_by') = p_channel" in guard
    assert "'bj_' || p_channel || '_subject'" in guard
    assert (
        "raw_app_meta_data" in guard and "raw_user_meta_data" not in guard
    )  # user_metadata is editable


def test_the_claim_id_and_lease_are_validated_and_the_purge_is_bounded() -> None:
    sql = re.sub(r"--[^\n]*", "", _DISCORD.read_text())
    assert "interaction_id text primary key check (interaction_id ~ '^[0-9]{1,25}$')" in sql
    assert "p_interaction_id !~ '^[0-9]{1,25}$'" in sql
    assert "p_lease_seconds < 1 or p_lease_seconds > 86400" in sql
    assert "interval '7 days'" in sql and "limit 200" in sql
    assert "p_lease_seconds integer default 900" in sql


def test_the_migration_ends_with_a_written_revert() -> None:
    text = _DISCORD.read_text()
    tail = text[text.rindex("-- Revert") :]
    assert "drop table public.discord_processed_interactions" in tail
    assert "merge_user_data(uuid, uuid, text, text)" in tail


def test_the_written_revert_keeps_service_role_off_the_recreated_guard_and_merge() -> None:
    # Recreated as new functions, they would get EXECUTE for service_role from the project's
    # default privileges; the revert has to say that the revoke includes it.
    text = _DISCORD.read_text()
    tail = " ".join(re.sub(r"(?m)^\s*--[ \t]*(--)?", " ", text[text.rindex("-- Revert") :]).split())
    assert "revoked from public, anon, authenticated AND service_role" in tail
    assert "20260930181212_lock_link_helpers_from_service_role.sql" in tail
    assert (
        "consume_link_code and finish_link_merge are revoked from public, anon, authenticated"
        in tail
    )
    assert "exactly as the file that defined them states" not in tail
