"""One-time reference-data import: n8n's real Fortune-500-based company
tier data -> between-jobs' own `company_tiers` table (Job Finder P6b,
live-search-track.md).

Static reference data with no natural per-row key worth deduplicating
on beyond `normalized_name` itself -- re-running this script TRUNCATES
the table and reinserts fresh from the source file, the same choice
`import_geo_gazetteer.py` already made for the same reason: "this
reference dataset was regenerated, replace it wholesale" is the honest
contract for data like this, not upsert semantics it doesn't need.

Reads directly from n8n's own frozen reference repo
(`data/reference/company_tiers.json`, real, CC BY 4.0-licensed Fortune
500 2025 data + a hand-curated tier overlay, already merged by n8n's
own `scripts/build_reference_data.js`) -- this project never modifies
that repo, only reads real data out of it, same precedent as
`import_job_registry_seed.py` and `import_geo_gazetteer.py`. The 576
company rows land ONLY in the real Supabase project, never as a
git-tracked file in this repo, per this project's own "never commit
registry or interview-intel datasets" rule -- only this script and the
schema migration are public.
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
    """`company_tiers.json`'s `companies` dict is already keyed by
    normalized name -- this just reshapes one (key, value) pair into an
    insert payload with real column names, nothing guessed."""
    return {
        "normalized_name": normalized_name,
        "display_name": entry["name"],
        "weight": entry["w"],
        "tier": entry["tier"],
        "rank": entry.get("rank"),
        "hq": entry.get("hq"),
    }


async def _insert_in_batches(
    supabase: AsyncClient, rows: list[dict[str, Any]], *, batch_size: int = _BATCH_SIZE
) -> None:
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        await _insert_batch_with_retry(supabase, batch)


async def _insert_batch_with_retry(supabase: AsyncClient, batch: list[dict[str, Any]]) -> None:
    """Same bounded retry-with-backoff `import_geo_gazetteer.py` already
    uses -- 576 rows in one batch is unlikely to ever hit a statement
    timeout, but the cost of this guard is near zero and every insert
    here is naturally idempotent-safe to retry (no conflict target, a
    partial-batch failure just means retrying the same rows)."""
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

    if dry_run:
        return summary

    await supabase.table("company_tiers").delete().gte("id", 0).execute()
    rows = [
        company_row_to_insert(normalized_name, entry)
        for normalized_name, entry in companies.items()
    ]
    # Sanity check, not a defensive guard against untrusted input: the
    # source file's own keys are already the output of normalizeCompanyName
    # (confirmed by reading company_tiers.json directly), so re-deriving
    # each key here should be a no-op -- if it isn't, the source format
    # changed and inserting anyway would silently break lookups at score time.
    for normalized_name, entry in companies.items():
        assert normalize_company_name(entry["name"]) == normalized_name, (
            f"normalizer mismatch for {entry['name']!r}: "
            f"expected {normalized_name!r}, got {normalize_company_name(entry['name'])!r}"
        )
    await _insert_in_batches(supabase, rows)
    summary.companies_inserted = len(rows)
    return summary


async def _main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report counts without writing anything to between-jobs' Supabase project.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Path to n8n's real company_tiers.json (the frozen reference repo's own copy).",
    )
    args = parser.parse_args()

    supabase, _url = await create_supabase_client()
    summary = await import_company_tiers(supabase, source_path=args.source, dry_run=args.dry_run)

    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"{prefix}{summary}")


if __name__ == "__main__":
    asyncio.run(_main())
