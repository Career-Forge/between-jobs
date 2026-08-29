"""Read-only facade over n8n's live job registry (Horizon Sprint 4.1) --
Proposal §23, scoped per the v12 plan's own words for this sprint: "Job
Intelligence facade reads n8n's Postgres read-only first; the poller
stays in n8n indefinitely." NOT a port of §23's full 6-lane search/4-lane
poller/registry-auto-growth vision (that's MASTER_PLAN.md §5.1's longer-
term destination, not this sprint's scope) -- n8n's `CareerForge_ATS_
Poller` workflow is live, currently polling ~2,800 companies/day into a
real Postgres database (confirmed before writing any code: 15,956
companies, 80,492 jobs, most recent poll ~7 minutes old at the time of
that check), so between-jobs reads that data directly rather than
duplicating a pipeline that already works.

This is a genuinely different kind of dependency than anything else in
this codebase: a raw Postgres connection (asyncpg, no PostgREST/RLS
layer) into a database this platform doesn't own and must never write
to -- n8n is frozen, reference-only, no exceptions (CLAUDE.local.md).
Every query in this module runs inside an asyncpg read-only transaction
(`conn.transaction(readonly=True)`) -- real Postgres-level enforcement
via `START TRANSACTION READ ONLY`, not just an application-level
convention that a future edit could quietly break.

Schema (n8n's `db/schema.sql`, read but never touched):
  companies(id, name, ats_type, slug, api_base, is_active, tier, ...)
  jobs(id, company_id, board, external_id, title, jd_text, location,
       remote, apply_url, posted_at, status, jd_tsv, ...)
`jd_tsv` is n8n's own generated tsvector column (already GIN-indexed) --
`search_jobs` uses it directly rather than standing up a second search
index for the same data.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from .env import require_env

_DEFAULT_LIMIT = 30
_MAX_LIMIT = 100

_SEARCH_COLUMNS = """
    j.id, j.title, c.name as company_name, j.location, j.remote,
    j.apply_url, j.posted_at
"""


async def create_pool() -> asyncpg.Pool:
    """A small pool -- this backend is a read-only guest of n8n's own
    database, not a primary consumer; no reason to compete for
    connections with the live poller."""
    dsn = require_env("N8N_JOBS_DATABASE_URL")
    return await asyncpg.create_pool(dsn, min_size=1, max_size=3)


async def search_jobs(
    pool: asyncpg.Pool, *, query: str | None, limit: int = _DEFAULT_LIMIT
) -> list[dict[str, Any]]:
    limit = min(limit, _MAX_LIMIT)
    async with pool.acquire() as conn, conn.transaction(readonly=True):
        if query:
            rows = await conn.fetch(
                f"""
                select {_SEARCH_COLUMNS}
                from jobs j
                join companies c on c.id = j.company_id
                where j.status = 'active' and j.jd_tsv @@ plainto_tsquery('english', $1)
                order by j.posted_at desc nulls last
                limit $2
                """,
                query,
                limit,
            )
        else:
            rows = await conn.fetch(
                f"""
                select {_SEARCH_COLUMNS}
                from jobs j
                join companies c on c.id = j.company_id
                where j.status = 'active'
                order by j.posted_at desc nulls last
                limit $1
                """,
                limit,
            )
    return [dict(row) for row in rows]


async def get_job_detail(pool: asyncpg.Pool, job_id: int) -> dict[str, Any] | None:
    """The one query that also fetches `jd_text` -- kept out of
    `search_jobs`'s list view so a page of results doesn't drag full job
    descriptions over the wire; only needed at track-time."""
    async with pool.acquire() as conn, conn.transaction(readonly=True):
        row = await conn.fetchrow(
            """
            select j.id, j.title, c.name as company_name, j.location, j.remote,
                   j.apply_url, j.posted_at, j.jd_text
            from jobs j
            join companies c on c.id = j.company_id
            where j.id = $1
            """,
            job_id,
        )
    return dict(row) if row is not None else None
