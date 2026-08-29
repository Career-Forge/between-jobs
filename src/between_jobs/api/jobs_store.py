"""Persistence for jobs and job snapshots (Sprint 2.6b) -- Proposal §18.

Manual-paste job creation only, this sprint -- a client submits a
title/company/description[/url] directly. Firecrawl-driven URL scraping
(`ingest_job_url`) is Stage B / Sprint 3.0's job, layered on top of this
same store later, not a replacement for it: manual paste stays the real
fallback lane for postings a scraper can't reach (behind an auth wall,
forwarded by email, screenshotted), the same way the profile contract
keeps a manual-paste lane alongside anything more automated.

`jobs`/`job_snapshots` carry no user_id (Proposal §18's own DDL) -- shared
reference data, not per-user rows. This backend always uses the
service-role client, which bypasses RLS, same as every other store in
this project; RLS on these tables is defense-in-depth for a hypothetical
future direct-client read path.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, cast

from supabase import AsyncClient


class JobNotFound(Exception):
    """No job exists for that id."""


class SnapshotNotFound(Exception):
    """No job snapshot exists for that id."""


def _content_hash(structured: dict[str, Any]) -> str:
    # Same canonical (sorted-key) serialization as profile.py's
    # _content_hash -- semantically-identical content hashes the same
    # regardless of key order.
    normalized = json.dumps(structured, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def _find_job_by_canonical_url(
    supabase: AsyncClient, canonical_url: str
) -> dict[str, Any] | None:
    result = (
        await supabase.table("jobs")
        .select("*")
        .eq("canonical_url", canonical_url)
        .limit(1)
        .execute()
    )
    return cast(dict[str, Any], result.data[0]) if result.data else None


async def create_job_from_paste(
    supabase: AsyncClient,
    *,
    title: str,
    company_name: str,
    description_text: str,
    canonical_url: str | None,
    location_text: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The manual-paste path: find-or-create the job by its canonical URL
    (jobs with no URL always get their own row -- nothing to dedup
    against), then find-or-create a snapshot for this exact content under
    it. Returns (job, snapshot)."""
    job = await _find_job_by_canonical_url(supabase, canonical_url) if canonical_url else None
    if job is None:
        result = (
            await supabase.table("jobs")
            .insert({"canonical_url": canonical_url, "company_name": company_name})
            .execute()
        )
        job = cast(dict[str, Any], result.data[0])

    structured = {
        "title": title,
        "company_name": company_name,
        "location_text": location_text,
        "description_text": description_text,
    }
    content_hash = _content_hash(structured)

    existing_snapshot = (
        await supabase.table("job_snapshots")
        .select("*")
        .eq("job_id", job["id"])
        .eq("content_hash", content_hash)
        .limit(1)
        .execute()
    )
    if existing_snapshot.data:
        return job, cast(dict[str, Any], existing_snapshot.data[0])

    result = (
        await supabase.table("job_snapshots")
        .insert(
            {
                "job_id": job["id"],
                "source_url": canonical_url or "",
                "title": title,
                "company_name": company_name,
                "location_text": location_text,
                "description_text": description_text,
                "structured_json": structured,
                "content_hash": content_hash,
                "fetched_at": datetime.now(UTC).isoformat(),
                "source_kind": "manual_paste",
            }
        )
        .execute()
    )
    return job, cast(dict[str, Any], result.data[0])


async def get_job(supabase: AsyncClient, job_id: str) -> dict[str, Any]:
    result = await supabase.table("jobs").select("*").eq("id", job_id).execute()
    if not result.data:
        raise JobNotFound(job_id)
    return cast(dict[str, Any], result.data[0])


async def get_snapshot(supabase: AsyncClient, snapshot_id: str) -> dict[str, Any]:
    result = await supabase.table("job_snapshots").select("*").eq("id", snapshot_id).execute()
    if not result.data:
        raise SnapshotNotFound(snapshot_id)
    return cast(dict[str, Any], result.data[0])


async def get_snapshots(supabase: AsyncClient, snapshot_ids: list[str]) -> list[dict[str, Any]]:
    """Batch fetch for list views (the Applications page) -- avoids one
    round trip per row. Missing ids are silently absent from the result,
    not an error; callers map the response back onto their own rows by id."""
    if not snapshot_ids:
        return []
    result = await supabase.table("job_snapshots").select("*").in_("id", snapshot_ids).execute()
    return cast(list[dict[str, Any]], result.data)
