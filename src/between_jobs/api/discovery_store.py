"""A read-only `asyncpg` connection pool into n8n's own live Postgres --
the separate, frozen reference system this platform reads but never owns
and never writes to (CLAUDE.local.md).

Historically also powered a live discovery-facade route (Horizon Sprint
4.1, `discovery_routes.py`'s own original implementation) that searched
n8n's `jobs` table directly on every request. Job Finder P8 replaced
that facade with this platform's own full, independent search pipeline
(P1-P7) -- the live app no longer reads n8n's Postgres at all. The only
remaining real use of `create_pool()` is the one-time, standalone
`scripts/import_job_registry_seed.py` (Job Finder P1), which bootstraps
between-jobs' own `job_registry_companies`/`job_registry_postings`
tables from n8n's already-seeded data. `search_jobs`/`get_job_detail`
(the old facade's own read queries) were removed along with the routes
that were their only caller, not left as dead code.
"""

from __future__ import annotations

import asyncpg

from .env import require_env


async def create_pool() -> asyncpg.Pool:
    """A small pool -- this is a one-time, standalone script's own
    read-only guest connection into n8n's database, not a live-app
    dependency; no reason to hold more than a couple of connections."""
    dsn = require_env("N8N_JOBS_DATABASE_URL")
    return await asyncpg.create_pool(dsn, min_size=1, max_size=3)
