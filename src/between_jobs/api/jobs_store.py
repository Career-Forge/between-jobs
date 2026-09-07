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
from typing import Any, TypedDict, cast
from urllib.parse import urlparse

from supabase import AsyncClient


class JobNotFound(Exception):
    """No job exists for that id."""


class SnapshotNotFound(Exception):
    """No job snapshot exists for that id."""


class RegistryPostingDetails(TypedDict):
    title: str | None
    location: str | None
    company: str | None
    jd_text: str | None


async def lookup_registry_posting(
    supabase: AsyncClient, apply_url: str
) -> RegistryPostingDetails | None:
    """Extracted out of `discovery_routes.py`'s own private `_lookup_
    registry_posting_details` (Job Finder P8) once outreach-v2-search-
    first.md Phase J needed the identical "does this exact URL already
    exist in the registry" check -- moved here, not duplicated, since
    `jobs_store.py`'s own docstring already anticipated Phase J needing
    real job data by URL. A plain equality match against `apply_url`,
    same as the original: no unique constraint exists on that column
    (only `(board, external_id)` is unique) and no canonicalization is
    applied on either side -- a pasted URL differing from the stored one
    only by tracking params, trailing slash, or case won't match. A real,
    disclosed limitation inherited unchanged from the P8 precedent this
    mirrors, not a new gap Phase J introduces."""
    posting_result = (
        await supabase.table("job_registry_postings")
        .select("title, location, jd_text, company_id")
        .eq("apply_url", apply_url)
        .limit(1)
        .execute()
    )
    if not posting_result.data:
        return None
    posting = cast(dict[str, Any], posting_result.data[0])

    company_name: str | None = None
    company_id = posting.get("company_id")
    if company_id:
        company_result = (
            await supabase.table("job_registry_companies")
            .select("name")
            .eq("id", company_id)
            .limit(1)
            .execute()
        )
        if company_result.data:
            company_name = cast(dict[str, Any], company_result.data[0])["name"]

    return RegistryPostingDetails(
        title=posting.get("title"),
        location=posting.get("location"),
        company=company_name,
        jd_text=posting.get("jd_text") or None,
    )


_SHARED_ATS_HOSTS = frozenset({"job-boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com"})
"""Real hosts confirmed against this project's own registry data
(job_registry_postings.apply_url) -- these three ATS platforms serve
every tenant off ONE shared hostname with the company slug as the first
path segment (e.g. job-boards.greenhouse.io/caylent/jobs/...), unlike a
company's own custom domain (e.g. coinbase.com) where the domain label
itself IS the company. Deliberately not exhaustive -- a platform not
listed here (Workday's own {tenant}.wdN.myworkdayjobs.com shape, for
one) still gets a correct-by-coincidence guess from the plain domain-
label fallback below, verified against a real posting before writing
this; a platform that's neither shared-host nor tenant-subdomain-shaped
just gets whatever its own domain's first label is."""


def guess_company_name_from_url(url: str) -> str:
    """Deterministic, no LLM -- outreach-v2-search-first.md Phase J.
    A real Firecrawl scrape response carries no clean, structured
    "company name" field (verified live against a real Coinbase
    Greenhouse posting: `metadata.title` is a human-readable page title,
    not a parseable field), so the URL itself is the best available
    signal. This is a best-effort operational guess, never presented as
    a verified claim -- unlike a skill-state citation, a wrong guess here
    only degrades search quality for company-scoped features downstream,
    so a simple heuristic is an acceptable, disclosed tradeoff rather
    than something requiring per-ATS-adapter-level correctness."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    if host in _SHARED_ATS_HOSTS:
        segments = [s for s in parsed.path.split("/") if s]
        slug = segments[0] if segments else ""
    else:
        labels = host.removeprefix("www.").split(".")
        slug = labels[0] if labels else ""

    if not slug:
        return "Unknown Company"
    return slug.replace("-", " ").replace("_", " ").title()


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
    source_kind: str = "manual_paste",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The manual-paste path: find-or-create the job by its canonical URL
    (jobs with no URL always get their own row -- nothing to dedup
    against), then find-or-create a snapshot for this exact content under
    it. Returns (job, snapshot).

    `source_kind` defaults to this function's own original, only value
    ("manual_paste") so every existing caller (web paste, Telegram paste,
    Job Finder P8's track route) is unaffected -- outreach-v2-search-
    first.md Phase J is the first caller to pass a real, distinct value
    ("url_ingest") for a snapshot that came from a live Firecrawl scrape
    or a registry hit rather than a human pasting text directly. No CHECK
    constraint exists on this column, so no migration is needed for a new
    value."""
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
                "source_kind": source_kind,
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


async def get_jobs(supabase: AsyncClient, job_ids: list[str]) -> list[dict[str, Any]]:
    """Batch fetch, mirroring `get_snapshots` -- browser-extension.md E1's
    URL-to-application lookup needs `canonical_url` across a user's whole
    application list in one round trip, not per-application."""
    if not job_ids:
        return []
    result = await supabase.table("jobs").select("*").in_("id", job_ids).execute()
    return cast(list[dict[str, Any]], result.data)
