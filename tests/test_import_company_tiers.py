"""Tests for `scripts/import_company_tiers.py`: a source file whose keys are not the
normalized form of their names is refused before the table is touched, with or without
`--dry-run`, and a good file is counted without a write on a dry run."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.import_company_tiers import check_keys_are_normalized, import_company_tiers


class _RecordingSupabase:
    """Fails the test on any table access, so "nothing was touched" is asserted by
    construction rather than by inspecting a call log."""

    def table(self, name: str) -> Any:
        raise AssertionError(f"unexpected table access: {name}")


def _write_source(tmp_path: Path, companies: dict[str, Any]) -> Path:
    path = tmp_path / "company_tiers.json"
    path.write_text(json.dumps({"companies": companies}))
    return path


_GOOD = {"acme": {"name": "Acme Corp.", "w": 0.8, "tier": "enterprise"}}
_BAD_KEY = {"Stripe, Inc.": {"name": "Stripe, Inc.", "w": 0.9, "tier": "startup"}}


def test_a_key_that_is_the_normalized_name_is_accepted() -> None:
    check_keys_are_normalized(_GOOD)


def test_a_key_that_is_not_the_normalized_name_is_refused() -> None:
    with pytest.raises(ValueError, match="not the normalized form"):
        check_keys_are_normalized(_BAD_KEY)


@pytest.mark.parametrize("dry_run", [True, False])
async def test_a_bad_file_is_refused_before_the_table_is_touched(
    tmp_path: Path, dry_run: bool
) -> None:
    source = _write_source(tmp_path, _BAD_KEY)

    with pytest.raises(ValueError, match="not the normalized form"):
        await import_company_tiers(
            _RecordingSupabase(),  # type: ignore[arg-type]
            source_path=source,
            dry_run=dry_run,
        )


async def test_a_dry_run_of_a_good_file_counts_without_writing(tmp_path: Path) -> None:
    source = _write_source(tmp_path, _GOOD)

    summary = await import_company_tiers(
        _RecordingSupabase(),  # type: ignore[arg-type]
        source_path=source,
        dry_run=True,
    )

    assert summary.companies_seen == 1
    assert summary.companies_inserted == 0
