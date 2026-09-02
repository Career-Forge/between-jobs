"""One-time seed import: n8n's live job registry -> between-jobs' own
`job_registry_companies`/`job_registry_postings` (Job Finder P1,
job-finder-port.md D2).

This is a real, deliberate data migration, not a recurring job -- run by
hand, once, to bootstrap between-jobs' own registry from n8n's already-
validated data before Job Finder P2's poller takes over ongoing polling.
Safe to re-run: every write is an upsert keyed on the same natural-key
unique constraints the schema itself enforces
(`(ats_type, slug, api_base)` for companies, `(board, external_id)` for
postings), so a second run reconciles rather than duplicates.

Reads n8n's `companies`/`jobs` tables via the exact same read-only,
transaction-guarded connection `discovery_store.create_pool()` already
uses (n8n is frozen, reference-only -- this script never writes to it).
Writes to between-jobs' own Supabase project via the service-role client,
same as every other write path in this codebase.

Deliberately NOT ported here (out of scope for P1, or superseded already):
- n8n's own `users`/`resumes`/`matches` tables -- n8n's single-user
  Telegram-bot simplification; between-jobs already has its own real
  multi-user profile/scoring model, nothing to import.
- `company_intel`/`company_writing_profiles` -- between-jobs already has
  its own, more rigorously anti-fabrication-backed company-intel pipeline
  (Horizon Sprint 5.0); n8n's cached-dossier table would be a regression,
  not an upgrade.
- `tool_cost_log`, `app_settings` (geo_reference, ingest_title_filter,
  etc.) -- real, useful reference data, but a poller-config/cost-tracking
  concern for Job Finder P2, not this schema-seeding phase.
- `embedding` is intentionally left NULL on every imported posting --
  populating it needs a real embedding-generation call, which belongs to
  whichever later phase actually builds hybrid search, not this one-time
  import.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping
from typing import Any, cast

import asyncpg
from dotenv import load_dotenv

from between_jobs.api.discovery_store import create_pool as create_n8n_pool
from between_jobs.api.supabase_client import create_supabase_client
from between_jobs.api.supabase_helpers import fetch_all_pages, retry_on_statement_timeout
from supabase import AsyncClient

_BATCH_SIZE = 500
"""Company upsert batch size, and the page size for `_select_all_companies`
-- proven reliable against the real ~16k-row table (no timeout)."""

_POSTING_BATCH_SIZE = 100
"""Postings carry much more per row than companies (full `jd_text`, plus 5
indexes to maintain per insert including a GIN tsvector and an HNSW vector
index) -- a real 500-row batch hit Postgres's own statement timeout
(`57014`) against the live ~90k-row import; 100 is small enough to stay
comfortably under it while still batching, not a guess."""


def company_row_to_upsert(row: Mapping[str, Any]) -> dict[str, Any]:
    """n8n `companies` row -> a between-jobs `job_registry_companies`
    upsert payload. Pure and total: every real column n8n's schema defines
    has a mapped field here, `id` excluded (a fresh uuid is minted on
    insert; the natural key below is what makes re-running idempotent)."""
    return {
        "name": row["name"],
        "ats_type": row["ats_type"],
        "slug": row["slug"],
        "api_base": row["api_base"] or "",
        "is_active": row["is_active"],
        "poll_interval": _interval_to_postgres_literal(row["poll_interval"]),
        "next_poll_at": row["next_poll_at"].isoformat(),
        "last_polled_at": row["last_polled_at"].isoformat() if row["last_polled_at"] else None,
        "etag": row["etag"],
        "last_modified": row["last_modified"],
        "consecutive_failures": row["consecutive_failures"],
        "tier": row["tier"],
        "relevant_yield": row["relevant_yield"],
        "tier_weight": float(row["tier_weight"]) if row["tier_weight"] is not None else None,
    }


def _interval_to_postgres_literal(value: Any) -> str:
    """asyncpg returns an INTERVAL column as `datetime.timedelta` --
    Postgrest wants a string Postgres can parse back into `interval`, and
    a plain `str(timedelta)` (e.g. "6:00:00") isn't valid interval syntax.
    `f"{total_seconds} seconds"` is unambiguous and round-trips exactly."""
    return f"{value.total_seconds()} seconds"


def job_row_to_upsert(
    row: Mapping[str, Any], company_id_by_natural_key: Mapping[tuple[str, str, str], str]
) -> dict[str, Any] | None:
    """n8n `jobs` row -> a between-jobs `job_registry_postings` upsert
    payload. Returns None (never a guessed/dropped-silently row) only when
    the posting's own company can't be resolved -- shouldn't happen given
    n8n's own FK integrity, but this is a one-time import script reading a
    system this platform doesn't own, so a defensive skip beats a crash
    partway through a multi-thousand-row batch."""
    company_key = (
        row["company_ats_type"],
        row["company_slug"],
        row["company_api_base"] or "",
    )
    company_id = company_id_by_natural_key.get(company_key)
    if company_id is None:
        return None
    return {
        "company_id": company_id,
        # Recomputed from the resolved company natural key, NOT n8n's own
        # row["board"] -- a real bug, found only by running the P2 poller
        # live against this data: n8n's `board` is `ats_type:slug` alone
        # (this function's own docstring already named the risk, but the
        # code below it didn't act on it). Two Workday companies sharing a
        # generic slug ("External", "external", "careers", ...) share that
        # exact board string, so `close_stale`/`advance_poll_state` -- both
        # keyed on `board` -- silently applied one company's poll outcome
        # to every company sharing it. Fixed everywhere in the same
        # migration (job-finder-port.md P2 section, 20260830130000): board
        # is now the same 3-part key (ats_type:slug:api_base) the
        # companies table already uses to disambiguate itself.
        "board": f"{company_key[0]}:{company_key[1]}:{company_key[2]}",
        "external_id": row["external_id"],
        "title": row["title"],
        "jd_text": row["jd_text"],
        "location": row["location"],
        "remote": row["remote"],
        "apply_url": row["apply_url"],
        "posted_at": row["posted_at"].isoformat() if row["posted_at"] else None,
        "status": row["status"],
        "first_seen": row["first_seen"].isoformat(),
        "last_seen": row["last_seen"].isoformat(),
        "closed_at": row["closed_at"].isoformat() if row["closed_at"] else None,
        "salary_min": float(row["salary_min"]) if row["salary_min"] is not None else None,
        "salary_max": float(row["salary_max"]) if row["salary_max"] is not None else None,
        "salary_currency": row["salary_currency"],
        "salary_period": row["salary_period"],
        "sponsorship_signal": row["sponsorship_signal"],
        "extracted_at": row["extracted_at"].isoformat() if row["extracted_at"] else None,
        "extraction_version": row["extraction_version"],
    }


async def _fetch_n8n_companies(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    async with pool.acquire() as conn, conn.transaction(readonly=True):
        rows = await conn.fetch(
            """
            select id, name, ats_type, slug, api_base, is_active, poll_interval,
                   next_poll_at, last_polled_at, etag, last_modified,
                   consecutive_failures, tier, relevant_yield, tier_weight
            from companies
            """
        )
        return cast("list[asyncpg.Record]", rows)


async def _fetch_n8n_jobs(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    """Joins to `companies` for the 3 columns that make up its real natural
    key (ats_type/slug/api_base) -- n8n's own `j.board` is deliberately NOT
    selected here; it's `ats_type:slug` without `api_base`, which isn't
    precise enough on its own (n8n's own s74 incident: two tenants can
    share one board string) and job_row_to_upsert recomputes `board` from
    this resolved natural key instead of trusting it. Selected as 3 plain
    aliased columns, not a composite `row(...)` -- asyncpg has no default
    codec for an anonymous composite type, so a plain scalar triple is the
    reliable choice here, not a premature optimization."""
    async with pool.acquire() as conn, conn.transaction(readonly=True):
        rows = await conn.fetch(
            """
            select j.id, j.external_id, j.title, j.jd_text, j.location,
                   j.remote, j.apply_url, j.posted_at, j.status, j.first_seen,
                   j.last_seen, j.closed_at, j.salary_min, j.salary_max,
                   j.salary_currency, j.salary_period, j.sponsorship_signal,
                   j.extracted_at, j.extraction_version,
                   c.ats_type as company_ats_type, c.slug as company_slug,
                   c.api_base as company_api_base
            from jobs j
            join companies c on c.id = j.company_id
            """
        )
        return cast("list[asyncpg.Record]", rows)


def _company_natural_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (row["ats_type"], row["slug"], row["api_base"] or "")


async def _select_all_companies(supabase: AsyncClient) -> list[dict[str, Any]]:
    """PostgREST caps an unranged `.select()` at its own default row limit
    (1,000 here) -- a real bug caught only by running against the real
    ~16k-row registry, not the small fixtures in this file's own unit
    tests: the first live run silently resolved company ids for only the
    first page, skipping the other ~93% of postings as "unresolved."
    Pages via `.range()` until a short page confirms there's nothing left,
    rather than trusting a single unbounded call."""

    async def _page(start: int, end: int) -> list[dict[str, Any]]:
        result = (
            await supabase.table("job_registry_companies")
            .select("id, ats_type, slug, api_base")
            .range(start, end)
            .execute()
        )
        return cast("list[dict[str, Any]]", result.data)

    return await fetch_all_pages(_page, page_size=_BATCH_SIZE)


_MAX_UPSERT_ATTEMPTS = 4  # a hard cap -- 3 retries, exponential backoff, then a real failure


async def _upsert_in_batches(
    supabase: AsyncClient,
    table: str,
    rows: list[dict[str, Any]],
    *,
    on_conflict: str,
    batch_size: int = _BATCH_SIZE,
) -> None:
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        await _upsert_batch_with_retry(supabase, table, batch, on_conflict=on_conflict)


async def _upsert_batch_with_retry(
    supabase: AsyncClient, table: str, batch: list[dict[str, Any]], *, on_conflict: str
) -> None:
    """A real, live import against the ~90k-row postings table hit
    Postgres's own statement timeout (57014) partway through -- per-upsert
    cost against a table with a GENERATED tsvector column and an HNSW
    index grows as the table does, so no single fixed batch size is
    guaranteed safe for the LAST batch the way it is for the first. Retry
    is idempotent (every write here is an upsert on the same natural-key
    constraint the schema enforces) and hard-capped, not indefinite."""
    await retry_on_statement_timeout(
        lambda: supabase.table(table).upsert(batch, on_conflict=on_conflict).execute(),
        max_attempts=_MAX_UPSERT_ATTEMPTS,
    )


class ImportSummary:
    def __init__(self) -> None:
        self.companies_seen = 0
        self.companies_upserted = 0
        self.jobs_seen = 0
        self.jobs_upserted = 0
        self.jobs_skipped_unresolved_company = 0

    def __str__(self) -> str:
        return (
            f"companies: {self.companies_upserted}/{self.companies_seen} upserted; "
            f"postings: {self.jobs_upserted}/{self.jobs_seen} upserted "
            f"({self.jobs_skipped_unresolved_company} skipped, unresolved company)"
        )


async def import_registry(
    n8n_pool: asyncpg.Pool, supabase: AsyncClient, *, dry_run: bool
) -> ImportSummary:
    summary = ImportSummary()

    n8n_companies = await _fetch_n8n_companies(n8n_pool)
    summary.companies_seen = len(n8n_companies)
    company_upserts = [company_row_to_upsert(row) for row in n8n_companies]
    if not dry_run and company_upserts:
        await _upsert_in_batches(
            supabase,
            "job_registry_companies",
            company_upserts,
            on_conflict="ats_type,slug,api_base",
        )
    summary.companies_upserted = len(company_upserts)

    # Real ids, not a client-side guess: re-select after upsert so a
    # concurrent/previous partial run's existing rows resolve correctly
    # too, not just the ones this run just inserted.
    company_id_by_natural_key: dict[tuple[str, str, str], str] = {}
    if not dry_run:
        for row in await _select_all_companies(supabase):
            company_id_by_natural_key[(row["ats_type"], row["slug"], row["api_base"] or "")] = row[
                "id"
            ]
    else:
        # Dry run never writes, so there's nothing to select back -- fake
        # resolution against the batch we WOULD have inserted, purely to
        # report a realistic would-be-skipped count.
        company_id_by_natural_key = {
            _company_natural_key(row): "dry-run-placeholder" for row in n8n_companies
        }

    n8n_jobs = await _fetch_n8n_jobs(n8n_pool)
    summary.jobs_seen = len(n8n_jobs)
    job_upserts: list[dict[str, Any]] = []
    for row in n8n_jobs:
        payload = job_row_to_upsert(row, company_id_by_natural_key)
        if payload is None:
            summary.jobs_skipped_unresolved_company += 1
            continue
        job_upserts.append(payload)

    if not dry_run and job_upserts:
        await _upsert_in_batches(
            supabase,
            "job_registry_postings",
            job_upserts,
            on_conflict="board,external_id",
            batch_size=_POSTING_BATCH_SIZE,
        )
    summary.jobs_upserted = len(job_upserts)

    return summary


async def _main() -> None:
    # app.py loads .env at FastAPI startup -- this script runs standalone,
    # outside that process, so it has to load its own env here rather than
    # assuming require_env() below will already find anything.
    load_dotenv()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report counts without writing anything to between-jobs' Supabase project.",
    )
    args = parser.parse_args()

    n8n_pool = await create_n8n_pool()
    try:
        supabase, _url = await create_supabase_client()
        summary = await import_registry(n8n_pool, supabase, dry_run=args.dry_run)
    finally:
        await n8n_pool.close()

    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"{prefix}{summary}")


if __name__ == "__main__":
    asyncio.run(_main())
