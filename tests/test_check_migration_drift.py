"""scripts/check_migration_drift.py: the drift check CI runs against the hosted project."""

from __future__ import annotations

import json

import pytest

from scripts.check_migration_drift import drift, main, parse

_HEADER = """
  
   Local            | Remote           | Time (UTC)            
  ------------------|------------------|-----------------------
"""


def _table(*rows: tuple[str, str]) -> str:
    body = "".join(
        f"   `{a}` | `{b}` | `2026-01-01 00:00:00` \n"
        if a and b
        else (
            f"   `{a}` |                  | `2026-01-01 00:00:00` \n"
            if a
            else f"                    | `{b}` | `2026-01-01 00:00:00` \n"
        )
        for a, b in rows
    )
    return _HEADER + body


def test_a_clean_table_has_no_drift(capsys: pytest.CaptureFixture[str]) -> None:
    text = _table(("20260729135128", "20260729135128"), ("20260805083157", "20260805083157"))

    assert main(text) == 0
    assert "No drift: 2 migrations" in capsys.readouterr().out


def test_a_migration_here_that_the_project_lacks_is_drift(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The code on this branch expects a database change the project does not have."""
    text = _table(("20260729135128", "20260729135128"), ("20261001185820", ""))

    assert main(text) == 1
    assert "not applied to the project (1): 20261001185820" in capsys.readouterr().out


def test_a_change_made_by_hand_on_the_project_is_drift(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Applied through the dashboard or an MCP apply_migration: no file records it."""
    text = _table(("20260729135128", "20260729135128"), ("", "20260930999999"))

    assert main(text) == 1
    assert "no file here (1): 20260930999999" in capsys.readouterr().out


def test_both_directions_are_reported_together(capsys: pytest.CaptureFixture[str]) -> None:
    text = _table(("20260101000000", ""), ("", "20260202000000"))

    assert main(text) == 1
    out = capsys.readouterr().out
    assert "20260101000000" in out and "20260202000000" in out


def test_the_json_form_the_cli_uses_for_an_agent_is_read_too() -> None:
    clean = json.dumps({"migrations": [{"local": "20260729135128", "remote": "20260729135128"}]})
    behind = json.dumps(
        {
            "migrations": [
                {"local": "20260729135128", "remote": "20260729135128"},
                {"local": "20261001185820", "remote": ""},
            ]
        }
    )

    assert main(clean) == 0
    assert main(behind) == 1


@pytest.mark.parametrize(
    "text", ["", "   \n", "Connecting to remote database...\n", "not a table at all", _HEADER]
)
def test_output_it_cannot_read_fails_rather_than_passing(
    text: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """A changed CLI format must not turn this check into a permanent green."""
    assert main(text) == 2
    assert "Could not read any migration rows" in capsys.readouterr().err


def test_cli_chatter_around_the_table_is_ignored() -> None:
    text = "Initialising login role...\nConnecting to remote database...\n" + _table(
        ("20260729135128", "20260729135128")
    )

    assert parse(text) == [("20260729135128", "20260729135128")]
    assert main(text) == 0


def test_drift_classifies_each_kind() -> None:
    rows = [("1" * 14, "1" * 14), ("2" * 14, ""), ("", "3" * 14), ("4" * 14, "5" * 14)]

    assert drift(rows) == (["2" * 14], ["3" * 14], [f"{'4' * 14} != {'5' * 14}"])
