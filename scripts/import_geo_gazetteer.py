"""One-time reference-data import: n8n's real GeoNames-derived city
gazetteer -> between-jobs' own `geo_gazetteer_cities` (Job Finder P5d,
live-search-track.md).

Unlike `import_job_registry_seed.py` (a live, growing system this
platform now owns and polls independently), this is STATIC reference
data with no natural per-row key worth deduplicating on (city names
aren't unique, and GeoNames doesn't expose a stable external id this
project already tracks) -- re-running this script TRUNCATES the table
and reinserts fresh from the source file, the honest choice for "this
reference dataset was regenerated, replace it wholesale" rather than
inventing upsert semantics reference data doesn't need.

Reads directly from n8n's own frozen reference repo
(`data/reference/geonames_cities.json`, real, CC BY 4.0-licensed GeoNames
data, already built by n8n's own `scripts/build_reference_data.js`) --
this project never modifies that repo, only reads real data out of it,
same precedent as `import_job_registry_seed.py`'s own registry-seed
import. The 34,006 city rows land ONLY in the real Supabase project,
never as a git-tracked file in this repo, per this project's own "never
commit registry or interview-intel datasets" rule -- only this script
and the schema migration are public.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from postgrest.exceptions import APIError

from between_jobs.api.supabase_client import create_supabase_client
from supabase import AsyncClient

_BATCH_SIZE = 1000
_STATEMENT_TIMEOUT_SQLSTATE = "57014"
_MAX_INSERT_ATTEMPTS = 4


def city_row_to_insert(city: dict[str, Any]) -> dict[str, Any]:
    """A GeoNames city entry (n8n's own compact field names: n/a/alt/cc/
    p/lat/lon) -> a `geo_gazetteer_cities` insert payload with real
    column names -- pure and total, every field the source provides maps
    directly, nothing guessed or defaulted beyond the schema's own
    `population`/`alt_names` defaults for a genuinely missing value."""
    return {
        "name": city["n"],
        "ascii_name": city["a"],
        "alt_names": city.get("alt") or [],
        "country_code": city["cc"],
        "population": city.get("p") or 0,
        "latitude": city.get("lat"),
        "longitude": city.get("lon"),
    }


async def _insert_in_batches(
    supabase: AsyncClient, rows: list[dict[str, Any]], *, batch_size: int = _BATCH_SIZE
) -> None:
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        await _insert_batch_with_retry(supabase, batch)


async def _insert_batch_with_retry(supabase: AsyncClient, batch: list[dict[str, Any]]) -> None:
    """Same bounded retry-with-backoff this codebase already leans on for
    every real bulk write against a Supabase table with index-maintenance
    cost (job_registry_postings' own seed import hit this first) -- a
    plain insert into a fresh table is unlikely to need it at this row
    count, but the table carries a real index and cheap insurance costs
    nothing when every insert here is naturally idempotent-safe to retry
    (a partial batch failure just means retrying the same rows, no
    unique-constraint risk since there's no conflict target)."""
    for attempt in range(1, _MAX_INSERT_ATTEMPTS + 1):
        try:
            await supabase.table("geo_gazetteer_cities").insert(batch).execute()
            return
        except APIError as e:
            if e.code != _STATEMENT_TIMEOUT_SQLSTATE or attempt == _MAX_INSERT_ATTEMPTS:
                raise
            await asyncio.sleep(2**attempt)


class ImportSummary:
    def __init__(self) -> None:
        self.cities_seen = 0
        self.cities_inserted = 0

    def __str__(self) -> str:
        return f"cities: {self.cities_inserted}/{self.cities_seen} inserted"


async def import_gazetteer(
    supabase: AsyncClient, *, source_path: Path, dry_run: bool
) -> ImportSummary:
    summary = ImportSummary()
    with source_path.open() as f:
        data = json.load(f)
    cities = data["cities"]
    summary.cities_seen = len(cities)

    if dry_run:
        return summary

    await supabase.table("geo_gazetteer_cities").delete().gte("id", 0).execute()
    rows = [city_row_to_insert(c) for c in cities]
    await _insert_in_batches(supabase, rows)
    summary.cities_inserted = len(rows)
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
        help="Path to n8n's real geonames_cities.json (the frozen reference repo's own copy).",
    )
    args = parser.parse_args()

    supabase, _url = await create_supabase_client()
    summary = await import_gazetteer(supabase, source_path=args.source, dry_run=args.dry_run)

    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"{prefix}{summary}")


if __name__ == "__main__":
    asyncio.run(_main())
