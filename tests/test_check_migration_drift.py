"""scripts/check_migration_drift.py: the drift check CI runs against the hosted project."""

from __future__ import annotations

import json
from pathlib import Path

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


# --- a row that is not read must never turn into "no drift" -------------------------------


def test_a_version_that_is_not_fourteen_digits_is_still_compared(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI takes any digit run as a version; dropping such a row hid it entirely."""
    text = _table(("20260729135128", "20260729135128"), ("001", ""))

    assert main(text) == 1
    assert "not applied to the project (1): 001" in capsys.readouterr().out


@pytest.mark.parametrize(
    "row",
    [
        "   `2026abc` | `20260729135128` | `2026-01-01 00:00:00` \n",  # not a version
        "   `20260729135128` \n | x\n",  # a cell that is not a version
        "                    |                  | `2026-01-01 00:00:00` \n",  # nothing at all
        "::error::pwned | `20260729135128` | t\n",  # chatter that has pipes and is not a row
    ],
)
def test_a_table_row_that_does_not_parse_fails_rather_than_being_skipped(
    row: str, capsys: pytest.CaptureFixture[str]
) -> None:
    text = _table(("20260729135128", "20260729135128")) + row

    assert main(text) == 2
    err = capsys.readouterr().err
    assert err.startswith("Cannot tell whether there is drift")


@pytest.mark.parametrize(
    "payload",
    [
        {"migrations": [{"local_version": "20260729135128", "remote_version": "20260729135128"}]},
        {"migrations": [{"local": "", "remote": ""}]},
        {"migrations": [{"local": "not-a-version", "remote": ""}]},
        {"migrations": ["20260729135128"]},
        {"rows": []},
        {"migrations": None},
    ],
)
def test_json_it_does_not_understand_fails_rather_than_reporting_no_drift(
    payload: object,
) -> None:
    """With the keys renamed, every row used to read as ('', '') and pass."""
    assert main(json.dumps(payload)) == 2


def test_the_json_the_cli_really_prints_is_read() -> None:
    real = (
        '{"migrations":[{"local":"20260729135128","remote":"20260729135128",'
        '"time":"2026-07-29 13:51:28"},{"local":"20260805083157","remote":"",'
        '"time":"2026-08-05 08:31:57"}]}'
    )

    assert parse(real) == [("20260729135128", "20260729135128"), ("20260805083157", "")]
    assert main(real) == 1


# --- every migration file must have been compared ----------------------------------------


def _migrations_dir(tmp_path: Path, *names: str) -> Path:
    for name in names:
        (tmp_path / name).write_text("select 1;")
    return tmp_path


def test_every_file_listed_by_the_cli_passes(tmp_path: Path) -> None:
    folder = _migrations_dir(tmp_path, "20260729135128_a.sql", "20260805083157_b.sql")
    text = _table(("20260729135128", "20260729135128"), ("20260805083157", "20260805083157"))

    assert main(text, folder) == 0


def test_a_file_the_cli_did_not_list_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The output parsed, but a migration in the checkout was never compared."""
    folder = _migrations_dir(tmp_path, "20260729135128_a.sql", "20260805083157_b.sql")

    assert main(_table(("20260729135128", "20260729135128")), folder) == 2
    assert "20260805083157_b.sql" in capsys.readouterr().err


def test_a_file_the_cli_would_skip_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`supabase db push` ignores a name that is not <digits>_<name>.sql: so does the list."""
    folder = _migrations_dir(tmp_path, "20260729135128_a.sql", "add_index.sql")

    assert main(_table(("20260729135128", "20260729135128")), folder) == 2
    assert "add_index.sql" in capsys.readouterr().err


def test_a_missing_migrations_directory_fails(tmp_path: Path) -> None:
    assert main(_table(("20260729135128", "20260729135128")), tmp_path / "nope") == 2


def test_the_real_migrations_directory_is_readable_by_the_check() -> None:
    """Every real file name has the shape the check (and the CLI) expects, so a clean CLI
    answer for them passes -- and if someone adds an oddly named file, this fails first."""
    folder = Path(__file__).resolve().parent.parent / "supabase" / "migrations"
    versions = sorted(path.name.split("_", 1)[0] for path in folder.glob("*.sql"))
    assert versions, "no migrations found"

    assert main(_table(*[(v, v) for v in versions]), folder) == 0
