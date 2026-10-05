"""One-time reference-data import: a GeoNames-derived city gazetteer -> the
`geo_gazetteer_cities` table, which the location filter (Job Finder location
filtering) uses to resolve free-text locations such as "Austin, TX" or "Remote -
Germany".

Optional. Without this table Discover still works: every location resolves to
"unknown" and the filter drops nothing.

No data file ships with this repository. Build one yourself, for example from
GeoNames' `cities15000` dump (https://www.geonames.org, CC BY 4.0, so keep the
attribution), and pass its path as `--source`. The expected shape is:

    {"cities": [
      {"n": "Munich", "a": "Munich", "alt": ["Muenchen", "Munchen"],
       "cc": "DE", "p": 1260391, "lat": 48.13743, "lon": 11.57549}
    ]}

`n` is the city name, `a` its ASCII name and `cc` its upper-case ISO 3166-1 alpha-2
country code; all three are required. `alt` (alternate names and spellings), `p`
(population, which breaks ties between cities sharing a name: the bigger one wins),
`lat` and `lon` are optional.

Unlike the job registry (a live, growing dataset the API's own poller keeps current),
this is STATIC reference data with no natural per-row key worth deduplicating on (city
names are not unique) -- re-running this script TRUNCATES the table and reinserts fresh
from the source file, the honest choice for "this reference dataset was regenerated,
replace it wholesale" rather than inventing upsert semantics it does not need.

The rows land only in your own Supabase project. Do not commit the data file to this
repository: it is reference data, not code, and the repository policy keeps datasets out.
`--dry-run` only counts the cities in the file; it validates nothing else.
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
    """One city entry from the source file (compact field names n/a/alt/cc/
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
    """Bounded retry-with-backoff on a statement timeout, the same guard this
    codebase uses for every bulk write against a table with index-maintenance
    cost (the job registry's upserts hit it first). A plain insert into a fresh
    table is unlikely to need it at this row count, but cheap insurance costs
    nothing when every insert here is naturally safe to retry (a partial batch
    failure just means retrying the same rows, no unique-constraint risk since
    there is no conflict target)."""
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
    summary = await import_gazetteer(supabase, source_path=args.source, dry_run=args.dry_run)

    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"{prefix}{summary}")


if __name__ == "__main__":
    asyncio.run(_main())
