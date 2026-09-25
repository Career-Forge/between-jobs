"""The saved-search matcher (Job Finder P9b, today-feed-job-matching.md)
-- the background job that produces "high-fit new job" Today items.

Scores ONLY registry postings that already passed a saved search's own
query/location/companies/remote_only filters (D1) -- never the full
newly-polled set against every user's profile, which would be a genuine
N x M cost explosion (confirmed directly: the poller processes up to ~72
companies/tick, ~96 ticks/day, hundreds of new postings/tick, and scoring
is a real LLM call). A user with no saved search never triggers any
scoring here at all.

Reads ONLY the registry lane (P1-P3e), never re-runs the 9 BYOK live-
search providers (P4) -- this is scoring against postings already polled
and stored; re-fetching from Firecrawl/Serper/etc. on a background tick
would reintroduce real per-provider cost and rate-limit risk for data the
registry already has. Never calls `ats_liveness.verify_liveness` (P7)
either -- registry-lane postings already get liveness for free from the
poller's own absence-based mechanism, confirmed directly in that
module's own docstring; re-verifying here would be pure waste.

Only "Strong"-bin matches (score100 >= 70, D5) become Today items, capped
at the top 5 per saved search per run -- a curated "Today" feed, not a
firehose. No per-search custom threshold in v1, a deliberate, disclosed
scope cut, not a hidden limit.

Publishes through the SAME `event_outbox` -> `digest_listener.py` ->
`today_items` pipeline every other Today item already uses (D7) --
`aggregate_type='saved_search'`, `aggregate_id=<the saved search's own
id>`, since there is no application this event is about. Does NOT reuse
`applications_store.record_event` -- that function is hardcoded to also
write a paired `application_events` audit row, which has no meaning for
an event that isn't about an application; this module writes directly
to `event_outbox` instead, with its own idempotency key.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, cast

from postgrest.exceptions import APIError

from supabase import AsyncClient

from .company_tiers import get_company_tier_index
from .credential_resolver import resolve
from .errors import ApiError
from .job_fit_scoring import ScoredJob, score_jobs
from .llm_client import generate as llm_generate
from .profile import ResumeTemplate
from .profile_store import get_active_version
from .search_aggregation import apply_search_filters
from .search_providers import SearchResult
from .supabase_helpers import fetch_all_pages

logger = logging.getLogger(__name__)

_UNIQUE_VIOLATION = "23505"
_STRONG_THRESHOLD = 70
"""Matches `job_fit_scoring`'s own real bin boundary (`score100 >= 70 ->
"Strong"`) -- not a separately-invented number."""
_MAX_MATCHES_PER_SEARCH_PER_RUN = 5
_CANDIDATE_LIMIT = 30
"""Mirrors `job_fit_scoring._BATCH_SIZE` -- `score_jobs` would only ever
score the first 30 of whatever it's given anyway."""
_FETCH_PAGE_SIZE = 1000
"""The same PostgREST-1000-row-default guard already proven necessary
three times in this codebase (`import_job_registry_seed.py`, `geo_
gazetteer.get_gazetteer`, `company_tiers.get_company_tier_index`) --
`saved_searches` is expected to stay small for a long while, but this
project has been burned by trusting that "for now" before."""


async def _select_active_saved_searches(supabase: AsyncClient) -> list[dict[str, Any]]:
    async def _page(start: int, end: int) -> list[dict[str, Any]]:
        result = (
            await supabase.table("saved_searches")
            .select("*")
            .eq("is_active", True)
            .range(start, end)
            .execute()
        )
        return cast("list[dict[str, Any]]", result.data)

    return await fetch_all_pages(_page, page_size=_FETCH_PAGE_SIZE)


async def _fetch_new_registry_postings(
    supabase: AsyncClient, *, query: str, since: str, limit: int
) -> list[SearchResult]:
    result = await supabase.rpc(
        "search_new_job_registry_postings",
        {"search_query": query, "since_timestamp": since, "result_limit": limit},
    ).execute()
    rows = cast("list[dict[str, Any]]", result.data)
    return [
        SearchResult(
            provider="registry",
            title=row["title"],
            company=row["company_name"],
            location=row["location"],
            remote=row["remote"],
            apply_url=row["apply_url"],
            snippet=row["snippet"] or "",
            posted_at=row["posted_at"],
            salary_min=row["salary_min"],
            salary_max=row["salary_max"],
            salary_currency=row["salary_currency"],
            sponsorship_signal=row["sponsorship_signal"],
            source_tier=1.0,
        )
        for row in rows
    ]


async def _advance_watermark(supabase: AsyncClient, search_id: str, new_watermark: str) -> None:
    await (
        supabase.table("saved_searches")
        .update({"last_matched_at": new_watermark})
        .eq("id", search_id)
        .execute()
    )


async def _publish_match(
    supabase: AsyncClient, search: dict[str, Any], scored: ScoredJob, result: SearchResult
) -> None:
    """Direct `event_outbox` insert, not `applications_store.record_
    event` -- that function is hardcoded to a paired `application_events`
    audit row, which has no meaning here. Idempotency key is deterministic
    (search + job), so a retried tick can't double-publish the same
    match even before the companion table's own unique constraint would
    catch it."""
    idempotency_key = f"job_registry.match_found:{search['id']}:{result.apply_url}"
    try:
        await (
            supabase.table("event_outbox")
            .insert(
                {
                    "user_id": search["user_id"],
                    "aggregate_type": "saved_search",
                    "aggregate_id": search["id"],
                    "event_type": "job_registry.match_found.v1",
                    "event_version": 1,
                    "payload": {
                        "apply_url": result.apply_url,
                        "title": result.title,
                        "company": result.company,
                        "location": result.location,
                        "score100": scored.score100,
                        "bin": scored.bin,
                        "one_liner": scored.one_liner,
                        "snippet": result.snippet,
                        "provider": result.provider,
                    },
                    "idempotency_key": idempotency_key,
                }
            )
            .execute()
        )
    except APIError as e:
        if e.code != _UNIQUE_VIOLATION:
            raise


async def _match_one_search(
    supabase: AsyncClient,
    search: dict[str, Any],
    tier_index: Any,
) -> None:
    tick_start = datetime.now(UTC).isoformat()
    candidates = await _fetch_new_registry_postings(
        supabase, query=search["query"], since=search["last_matched_at"], limit=_CANDIDATE_LIMIT
    )
    candidates, gazetteer_active = await apply_search_filters(
        candidates,
        supabase=supabase,
        companies=search["companies"],
        query=search["query"],
        location=search["location"],
        remote_only=search["remote_only"],
    )

    if not candidates:
        await _advance_watermark(supabase, search["id"], tick_start)
        return

    profile_version = await get_active_version(supabase, search["user_id"])
    if profile_version is None:
        # No active profile -> nothing to score against. Fail open (skip
        # this search this tick), same as any other missing-prerequisite
        # case here -- never crash the whole tick over one user's
        # incomplete setup.
        await _advance_watermark(supabase, search["id"], tick_start)
        return
    profile = ResumeTemplate.model_validate(profile_version["canonical_json"])

    try:
        llm_credential = await resolve(supabase, search["user_id"], capability="job_scoring")
    except ApiError as e:
        logger.info(
            "saved search skipped: no job_scoring model resolves",
            extra={"ctx": {"saved_search_id": search["id"], "code": e.code}},
        )
        await _advance_watermark(supabase, search["id"], tick_start)
        return

    scored, _unscored, _strategy = await score_jobs(
        profile=profile,
        results=candidates,
        llm_api_key=llm_credential.secret,
        llm_model=llm_credential.model,
        llm_base_url=llm_credential.base_url,
        location_requested=bool(search["location"])
        and not search["remote_only"]
        and gazetteer_active,
        company_health_lookup=tier_index.lookup,
        generate=llm_generate,
    )

    by_url = {r.apply_url: r for r in candidates}
    strong_matches = [s for s in scored if s.score100 >= _STRONG_THRESHOLD][
        :_MAX_MATCHES_PER_SEARCH_PER_RUN
    ]
    for scored_job in strong_matches:
        result = by_url.get(scored_job.apply_url)
        if result is None:
            continue
        await _publish_match(supabase, search, scored_job, result)

    await _advance_watermark(supabase, search["id"], tick_start)


_MAX_CONCURRENT_SEARCHES = 5
"""Different searches share no state (each reads/writes only its own
row; `tier_index` is a shared read-only lookup, safe to reuse across
concurrent calls), so they don't need to run one at a time -- but each
can trigger a real LLM scoring call, and the number of active saved
searches is unbounded, unlike the small fixed fan-outs elsewhere in this
codebase. A bare `asyncio.gather` would turn "at most 1 concurrent LLM
call" into "up to N concurrent," a real cost/rate-limit exposure change,
not just a latency win -- bounded via a semaphore instead."""


async def run_match_tick(supabase: AsyncClient) -> int:
    """Runs one tick across every active saved search, up to
    `_MAX_CONCURRENT_SEARCHES` at a time. Returns the number of saved
    searches processed (0 when none are active). Each search's own
    failure (no profile, no credential) is contained to that search --
    one user's incomplete setup never blocks another user's matching.
    Takes no `httpx.AsyncClient` -- unlike the registry poller or live
    search, everything here is a Supabase RPC/table call or an LLM call
    through the openai SDK, never a raw HTTP fetch."""
    searches = await _select_active_saved_searches(supabase)
    if not searches:
        return 0

    tier_index = await get_company_tier_index(supabase)
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_SEARCHES)

    async def _bounded(search: dict[str, Any]) -> None:
        async with semaphore:
            await _match_one_search(supabase, search, tier_index)

    await asyncio.gather(*(_bounded(search) for search in searches))
    return len(searches)


_DEFAULT_MATCH_INTERVAL_SECONDS = 21600.0
"""6 hours -- deliberately decoupled from the registry poller's own
15-minute cadence (D9). Polling cadence bounds data FRESHNESS; this
cadence bounds LLM-scoring COST, which scales with how often it runs,
not with how fresh the underlying data is."""


async def run_matcher_forever(
    supabase: AsyncClient, *, poll_interval_seconds: float = _DEFAULT_MATCH_INTERVAL_SECONDS
) -> None:
    """A plain sleep loop, matching `job_registry_poller.run_poller_
    forever`'s and `outbox_store.run_worker_forever`'s own shape --
    shutdown is `asyncio.CancelledError` propagating out of the sleep/
    RPC await, same as both."""
    while True:
        await run_match_tick(supabase)
        await asyncio.sleep(poll_interval_seconds)
