"""One-time reference-data import: a company-tier list -> the `company_tiers` table,
which backs the company-health part of the Discover fit score (Job Finder
company-health scoring).

Optional. Without this table the company-health sub-score is marked not applicable
and drops out of the weighted average instead of dragging the score.

No data file ships with this repository: the weights are a list you curate yourself
(for example from a public company ranking plus your own judgment). Pass its path as
`--source`. The expected shape is:

    {"companies": {
      "acme": {"name": "Acme Corp.", "w": 0.8, "tier": "enterprise",
               "rank": 120, "hq": "Austin, TX"}
    }}

Each key is the company name in its normalized form: exactly what
`normalize_company_name` (in `between_jobs.api.company_tiers`) returns for `name` --
lower case, "&" turned into "and", punctuation and legal suffixes such as "Inc" or
"Corp" dropped. The script checks every key before it changes anything, with or without
`--dry-run`, and refuses a file whose keys differ. `w` is a weight from 0 to 1 that feeds
the company-health score, `tier` is a free-form label, and `rank` and `hq` are optional.

Static reference data with no natural per-row key beyond `normalized_name` itself --
re-running this script TRUNCATES the table and reinserts fresh from the source file, the
same choice `import_geo_gazetteer.py` makes for the same reason: "this reference
dataset was regenerated, replace it wholesale" is the honest contract for data like
this, not upsert semantics it does not need.

The rows land only in your own Supabase project. Do not commit the data file to this
repository: it is reference data, not code, and the repository policy keeps datasets out.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from postgrest.exceptions import APIError

from between_jobs.api.company_tiers import normalize_company_name
from between_jobs.api.supabase_client import create_supabase_client
from supabase import AsyncClient

_BATCH_SIZE = 1000
_STATEMENT_TIMEOUT_SQLSTATE = "57014"
_MAX_INSERT_ATTEMPTS = 4


def company_row_to_insert(normalized_name: str, entry: dict[str, Any]) -> dict[str, Any]:
    """The source file's `companies` dict is keyed by normalized name --
    this reshapes one (key, value) pair into an insert payload with real
    column names, nothing guessed."""
    return {
        "normalized_name": normalized_name,
        "display_name": entry["name"],
        "weight": entry["w"],
        "tier": entry["tier"],
        "rank": entry.get("rank"),
        "hq": entry.get("hq"),
    }


def check_keys_are_normalized(companies: dict[str, dict[str, Any]]) -> None:
    """Raises ValueError unless every key equals `normalize_company_name` of its
    entry's `name`. Lookups at score time normalize the posting's company name the
    same way, so an entry stored under any other key could never be found."""
    for normalized_name, entry in companies.items():
        expected = normalize_company_name(entry["name"])
        if expected != normalized_name:
            raise ValueError(
                f"key {normalized_name!r} is not the normalized form of name "
                f"{entry['name']!r}: expected {expected!r}"
            )


async def _insert_in_batches(
    supabase: AsyncClient, rows: list[dict[str, Any]], *, batch_size: int = _BATCH_SIZE
) -> None:
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        await _insert_batch_with_retry(supabase, batch)


async def _insert_batch_with_retry(supabase: AsyncClient, batch: list[dict[str, Any]]) -> None:
    """Same bounded retry-with-backoff `import_geo_gazetteer.py` uses -- a
    few hundred rows in one batch is unlikely to ever hit a statement
    timeout, but the cost of this guard is near zero and every insert
    here is naturally safe to retry (no conflict target, a partial-batch
    failure just means retrying the same rows)."""
    for attempt in range(1, _MAX_INSERT_ATTEMPTS + 1):
        try:
            await supabase.table("company_tiers").insert(batch).execute()
            return
        except APIError as e:
            if e.code != _STATEMENT_TIMEOUT_SQLSTATE or attempt == _MAX_INSERT_ATTEMPTS:
                raise
            await asyncio.sleep(2**attempt)


class ImportSummary:
    def __init__(self) -> None:
        self.companies_seen = 0
        self.companies_inserted = 0

    def __str__(self) -> str:
        return f"companies: {self.companies_inserted}/{self.companies_seen} inserted"


async def import_company_tiers(
    supabase: AsyncClient, *, source_path: Path, dry_run: bool
) -> ImportSummary:
    summary = ImportSummary()
    with source_path.open() as f:
        data = json.load(f)
    companies = data["companies"]
    summary.companies_seen = len(companies)

    # Validate before anything is deleted, and under --dry-run too: a key that
    # is not the normalized form of its name would never match a lookup at score
    # time, and a file that fails here must not be able to empty the table first.
    check_keys_are_normalized(companies)

    if dry_run:
        return summary

    rows = [
        company_row_to_insert(normalized_name, entry)
        for normalized_name, entry in companies.items()
    ]
    await supabase.table("company_tiers").delete().gte("id", 0).execute()
    await _insert_in_batches(supabase, rows)
    summary.companies_inserted = len(rows)
    return summary


async def _main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report counts without writing anything to between-jobs' Supabase project.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Path to a JSON file in the shape described above.",
    )
    args = parser.parse_args()

    supabase, _url = await create_supabase_client()
    summary = await import_company_tiers(supabase, source_path=args.source, dry_run=args.dry_run)

    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"{prefix}{summary}")


if __name__ == "__main__":
    asyncio.run(_main())
