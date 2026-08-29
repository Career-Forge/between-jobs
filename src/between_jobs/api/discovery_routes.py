"""HTTP surface for job discovery (Horizon Sprint 4.1) -- a read-only
search over n8n's live job registry, plus a "track this" action that
reuses the exact same manual-paste ingestion the web/Telegram job-paste
flows already use (Sprint 2.6f/3.4a) rather than a second write path.
"""

from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Query

from supabase import AsyncClient

from .app_state import get_n8n_pool, get_supabase
from .applications_store import create_application
from .auth import require_user_id
from .discovery_store import get_job_detail, search_jobs
from .errors import ApiError
from .jobs_store import create_job_from_paste

router = APIRouter(prefix="/discover")


@router.get("")
async def search_discover(
    q: str | None = Query(default=None),
    _user_id: str = Depends(require_user_id),
    pool: asyncpg.Pool = Depends(get_n8n_pool),
) -> list[dict[str, Any]]:
    return await search_jobs(pool, query=q)


@router.post("/{job_id}/track", status_code=201)
async def track_discovered_job(
    job_id: int,
    user_id: str = Depends(require_user_id),
    pool: asyncpg.Pool = Depends(get_n8n_pool),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    """Fetches the full job (including `jd_text`, not returned by the
    list search above) and hands it to `create_job_from_paste` exactly as
    if the user had pasted it manually -- the between-jobs `jobs`/
    `job_snapshots` rows this creates carry no reference back to n8n's
    own `jobs.id`; n8n is the source, not a foreign key."""
    job = await get_job_detail(pool, job_id)
    if job is None:
        raise ApiError("NOT_FOUND", f"no discovered job found for id {job_id!r}")

    job_row, snapshot = await create_job_from_paste(
        supabase,
        title=job["title"],
        company_name=job["company_name"],
        description_text=job["jd_text"],
        canonical_url=job["apply_url"],
        location_text=job["location"],
    )
    application = await create_application(
        supabase,
        user_id,
        job_id=job_row["id"],
        active_job_snapshot_id=snapshot["id"],
        source_channel="discover",
    )
    return {**application, "snapshot": snapshot}
