"""The Job Finder registry poller worker (P2, job-finder-port.md D2/D8).

A faithful port of n8n's own `CareerForge_ATS_Poller.json` tick pipeline
(`Select Due Companies` -> `Build Requests`/`Fetch ATS`/`Parse Jobs` ->
`Upsert Jobs` -> `Tick Bookkeeping` -> `Close Stale Jobs`/`Advance Poll
State`/`Penalize Failed Boards`), read directly from the live workflow --
the SQL in this migration's own functions and the CASE-based tier state
machine are unchanged from the reference. Two things are genuinely
adapted, not copied:

- D8 (Pranav, 2026-08-30): between-jobs stores every posting, not just
  ones matching n8n's own AI/ML title regex (see job_registry_adapters.py
  for the full rationale). That regex was also what made n8n's own
  `relevant` count meaningful -- "count of AI/ML-title-matching postings
  on the board, capped at 25" stays small for almost every board.
  Without a title filter, a raw "how many postings are on this board"
  count would saturate the tier CASE logic's own `>= 3` threshold on
  nearly every real company's first tick and never come back down,
  collapsing the whole point of tiering (poll active/large boards often,
  quiet ones rarely). So `relevant` here means something different but
  serves the same purpose: **count of postings first-seen this tick**
  (new listings, via the upsert function's own `xmax = 0` return) -- a
  genuine hiring-activity signal that behaves like n8n's own small
  thresholds expect, computed in the SAME upsert statement as the write,
  no extra query and no race.
- Sweep-start-cutoff bookkeeping (the "microsoft lost all 85" fix) isn't
  needed here -- confirmed in this plan's own P1 research that it only
  matters for n8n's two adapters that persist a page cursor across
  multiple 15-minute ticks (google/microsoft, deferred to P3+). All four
  of P2's adapters return (or, for Workday, page through) a complete
  listing within a single tick, so every close-target here uses that
  tick's own `run_start` as the cutoff, exactly like n8n's own
  non-paginated adapters.

Mirrors outbox_store.py's own shape (`run_*_once`/`run_*_forever`, a
plain sleep loop, no scheduler library) rather than introducing a new
worker pattern into this codebase.

A real board-collision bug was found and fixed via this module's own
live verification, not caught by any unit test: `job_registry_postings
.board` was `ats_type:slug` only (P1's schema, matching n8n's own
`jobs.board` shape exactly), and Workday tenants turned out to routinely
reuse generic site slugs ("External", "external_careers", "careers", ...)
across totally unrelated companies -- confirmed live, not assumed: 18
different real companies (PNC, GEICO, T-Mobile, Micron, Travelers, and
13 others) shared the exact board `workday:External`. Because
`close_stale_job_registry_postings`/`advance_job_registry_poll_state`
match purely on `board`, a single company's poll result was being
applied to every company sharing its board -- a real tick that fetched
72 companies updated tier/poll state on 93. Fixed in migration
`20260830130000_fix_job_registry_board_collision.sql`: `board` is now
the same 3-part key (`ats_type:slug:api_base`) `job_registry_companies`
already uses to disambiguate itself, applied both to the 4 SQL functions
here and as a one-time backfill of every already-imported posting
(including resolving 313 real duplicate pairs the backfill surfaced --
the same real posting, double-counted under two casing variants of the
old 2-part board string). `scripts/import_job_registry_seed.py` was
fixed in the same pass so a future re-run doesn't regress this.

P3c adds Eightfold's own JD-backfill lane (`run_eightfold_jd_backfill`),
run every tick alongside `run_poll_tick` rather than on a separate
schedule -- mirrors n8n's own real architecture, where `Select JD
Backfill Batch -> Fetch Eightfold JDs -> Update Job Descriptions` is a
second branch off the exact same 15-minute `Poll Schedule` trigger, not
a standalone cron. Confirmed live (P3c research) this lane is load-
bearing, not a nicety, for Eightfold specifically: its list endpoint
NEVER returns real description text on either of its two tiers, so
without this lane Eightfold rows would carry a permanently empty
jd_text and never qualify for salary/sponsorship extraction at all.

P3e adds Google's own sweep-start-cutoff bookkeeping (the real "microsoft
lost all 85 postings this way" fix, s151) -- confirmed live its board is
far too large (85+ pages, 20 cards each) to fully re-walk in one 15-
minute tick, unlike every other adapter here. `fetch_google` itself has
zero awareness of "sweep" as a multi-tick concept -- it only reports
`hit_end` for its own tick's own page range, same shape as every other
adapter's own AdapterResult. ALL cross-tick sweep bookkeeping lives here,
in `_PAGINATED_ATS_TYPES`'s own branch of `run_poll_tick`: a sweep's
start timestamp and cumulative posting count carry over via two new
`job_registry_companies` columns (the page cursor itself keeps reusing
the existing `etag` column, already proven), and a close-targets entry
is only emitted when a sweep both completes (`hit_end`) AND has seen at
least one posting somewhere along the way -- the same zero-sweep-guard
shape as the real incident's own fix, preventing a broken scraper's
all-empty sweep from wiping the whole board. Deliberately NOT guarded:
a sweep resuming mid-board (Google's real starting etag, "25", predates
this bookkeeping) will only close postings unseen since ITS OWN start,
not page 1 -- a still-live posting on an unrevisited early page reopens
itself once the next sweep (which always restarts at page 1) reaches it
again. Self-healing, not the permanent-loss failure class the real
incident was about, so left as an accepted characteristic rather than
extra machinery -- see the migration's own comment for the full
reasoning.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, cast

import httpx

from supabase import AsyncClient

from .job_posting_extraction import extract_all
from .job_registry_adapters import (
    ADAPTERS,
    AdapterResult,
    DueCompany,
    ParsedPosting,
    extract_eightfold_position_id,
    fetch_eightfold_detail,
)
from .supabase_helpers import retry_on_statement_timeout

_DEFAULT_POLL_INTERVAL_SECONDS = 900.0  # 15 minutes, matching n8n's own scheduleTrigger
_PER_COMPANY_TIMEOUT_SECONDS = 90.0  # Workday's own worst case: 5 sequential page fetches
_MIN_EXTRACTABLE_JD_LENGTH = 50  # matches n8n's own push() gate on jd_text length

# A real 72-company tick against the live registry (dream/hot/warm lanes
# all due at once) hit Postgres's own statement timeout (57014) batching
# every posting from every company into one upsert call -- the identical
# failure mode P1's seed-import script hit against this same table, for
# the same reason (a GIN tsvector index + an HNSW vector index to
# maintain per row). Same fix: smaller batches plus a bounded
# retry-with-backoff on that specific SQLSTATE, matching this project's
# own "every retry has a hard cap" rule -- safe to retry since every
# write is an idempotent upsert on the table's own natural-key
# constraint.
_POSTING_UPSERT_BATCH_SIZE = 100
_MAX_UPSERT_ATTEMPTS = 4

# n8n's own real batch size for the Eightfold JD-backfill lane -- a
# steady drip, not a bulk catch-up job (see run_eightfold_jd_backfill).
_EIGHTFOLD_JD_BACKFILL_MIN_JD_LENGTH = 50

# ats_types whose own adapter pages across MULTIPLE ticks (reports
# hit_end=False when its own per-tick page budget runs out before the
# board does) and therefore needs the sweep-start-cutoff bookkeeping --
# every other adapter here completes its full listing within one tick, so
# a plain run_start cutoff (unchanged since P2) stays correct for them.
_PAGINATED_ATS_TYPES = frozenset({"google"})


async def select_due_companies(supabase: AsyncClient) -> list[DueCompany]:
    result = await supabase.rpc("select_due_job_registry_companies", {}).execute()
    rows = cast("list[dict[str, Any]]", result.data)
    return [
        DueCompany(
            company_id=row["company_id"],
            name=row["name"],
            ats_type=row["ats_type"],
            slug=row["slug"],
            api_base=row["api_base"],
            board=row["board"],
            etag=row["etag"],
            sweep_started_at=row["sweep_started_at"],
            sweep_posting_count=row["sweep_posting_count"],
        )
        for row in rows
    ]


def _extract_if_eligible(posting: ParsedPosting) -> dict[str, Any]:
    """Mirrors n8n's push(): a posting only gets salary/sponsorship
    extraction when its jd_text is long enough to be worth regexing --
    the six ATS types with no JD text at ingest (Workday among them) stay
    honestly unextracted rather than guessed."""
    row = asdict(posting)
    if len(posting.jd_text) >= _MIN_EXTRACTABLE_JD_LENGTH:
        extracted = extract_all(f"{posting.title} {posting.jd_text}")
        row["salary_min"] = extracted.salary_min
        row["salary_max"] = extracted.salary_max
        row["salary_currency"] = extracted.salary_currency
        row["salary_period"] = extracted.salary_period
        row["sponsorship_signal"] = extracted.sponsorship_signal
        row["extraction_version"] = extracted.extraction_version
        row["extracted_at"] = datetime.now(UTC).isoformat()
    else:
        row["salary_min"] = None
        row["salary_max"] = None
        row["salary_currency"] = None
        row["salary_period"] = None
        row["sponsorship_signal"] = "unknown"
        row["extraction_version"] = None
        row["extracted_at"] = None
    return row


async def _upsert_postings_in_batches(
    supabase: AsyncClient, postings: list[dict[str, Any]]
) -> dict[str, int]:
    """Upserts in bounded batches (each with its own retry-with-backoff on
    a real statement timeout) and returns the new-posting count per board
    -- the same signal every "ok" board's `relevant` count is computed
    from, so a genuinely large tick can't silently undercount it by
    dropping a batch."""
    new_counts: dict[str, int] = {}
    for start in range(0, len(postings), _POSTING_UPSERT_BATCH_SIZE):
        batch = postings[start : start + _POSTING_UPSERT_BATCH_SIZE]
        for row in await _upsert_batch_with_retry(supabase, batch):
            if row["is_new"]:
                new_counts[row["board"]] = new_counts.get(row["board"], 0) + 1
    return new_counts


async def _upsert_batch_with_retry(
    supabase: AsyncClient, batch: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    async def _op() -> list[dict[str, Any]]:
        result = await supabase.rpc("upsert_job_registry_postings", {"postings": batch}).execute()
        return cast("list[dict[str, Any]]", result.data)

    return await retry_on_statement_timeout(_op, max_attempts=_MAX_UPSERT_ATTEMPTS)


def _dedupe_postings(postings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A real live tick (P3a) hit Postgres error 21000 ("ON CONFLICT DO
    UPDATE command cannot affect row a second time") -- a single
    company's own paginated fetch (SmartRecruiters) produced two rows
    with the same external_id in one tick, almost certainly because the
    board's real ordering shifted between sequential page fetches (a
    posting straddling the offset boundary got returned on both pages).
    Postgres's upsert can't apply twice to the same conflict target
    within one statement, so any duplicate here would crash the whole
    batch, not just misprocess one row. Deduping defensively, keyed on
    the same (board, external_id) the DB's own unique constraint uses,
    last-occurrence-wins (the later page/company in iteration order is
    no more or less authoritative than the earlier one for a genuine
    same-tick duplicate, so this is just a tie-break, not a correctness
    claim)."""
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for posting in postings:
        deduped[(posting["board"], posting["external_id"])] = posting
    return list(deduped.values())


async def _fetch_one(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
    adapter = ADAPTERS.get(company.ats_type)
    if adapter is None:
        return AdapterResult(status="failed")
    try:
        return await asyncio.wait_for(adapter(http, company), timeout=_PER_COMPANY_TIMEOUT_SECONDS)
    except TimeoutError:
        return AdapterResult(status="failed")


async def run_poll_tick(http: httpx.AsyncClient, supabase: AsyncClient) -> int:
    """Runs one tick: select due -> fetch each due company concurrently ->
    upsert everything fetched -> close stale postings -> advance/penalize
    poll state. Returns the number of companies processed (0 when nothing
    was due)."""
    run_start = datetime.now(UTC).isoformat()
    companies = await select_due_companies(supabase)
    if not companies:
        return 0

    results = await asyncio.gather(*(_fetch_one(http, c) for c in companies))

    postings_batch: list[dict[str, Any]] = []
    close_targets: list[dict[str, Any]] = []
    poll_state_results: list[dict[str, Any]] = []
    failed_boards: list[str] = []
    gone_boards: list[str] = []

    for company, outcome in zip(companies, results, strict=True):
        if outcome.status in ("ok", "partial"):
            postings_batch.extend(_extract_if_eligible(p) for p in outcome.postings)
            sweep_started_at: str | None = None
            sweep_posting_count = 0
            if company.ats_type in _PAGINATED_ATS_TYPES:
                sweep_started_at = company.sweep_started_at or run_start
                sweep_posting_count = company.sweep_posting_count + len(outcome.postings)
                if outcome.hit_end:
                    # Sweep complete. Zero-sweep-guard: only close when
                    # this sweep actually saw at least one posting
                    # somewhere along the way -- a broken scraper
                    # returning empty on every page must never be read as
                    # "board is now empty" (the real incident this
                    # bookkeeping exists to prevent). Use the sweep's own
                    # start as the cutoff, not this tick's run_start,
                    # since a multi-tick sweep's earliest-fetched pages
                    # were refreshed ticks ago.
                    if sweep_posting_count > 0:
                        close_targets.append({"board": company.board, "cutoff": sweep_started_at})
                    # Reset for the next sweep regardless of whether this
                    # one closed anything.
                    sweep_started_at = None
                    sweep_posting_count = 0
            elif outcome.status == "ok":
                close_targets.append({"board": company.board, "cutoff": run_start})
            # else: outcome.status == "partial" on a non-paginated board --
            # upsert whatever was collected before the mid-pagination
            # failure, but skip close_targets: closing stale postings off
            # an admittedly incomplete fetch would wrongly mark still-open
            # postings closed just because a later page 404'd, timed out,
            # or came back malformed. Deferred to a future tick that
            # (hopefully) completes cleanly.
            # `relevant` is filled in below, once the upsert tells us how
            # many of this board's postings were genuinely new.
            poll_state_results.append(
                {
                    "board": company.board,
                    "etag": outcome.new_etag,
                    "relevant": 0,
                    "sweep_started_at": sweep_started_at,
                    "sweep_posting_count": sweep_posting_count,
                }
            )
        elif outcome.status == "not_modified":
            # Matches n8n's own 304 sentinel exactly: -1 falls through
            # every tier/interval CASE branch untouched, but still resets
            # last_polled_at/consecutive_failures.
            poll_state_results.append(
                {
                    "board": company.board,
                    "etag": None,
                    "relevant": -1,
                    "sweep_started_at": company.sweep_started_at,
                    "sweep_posting_count": company.sweep_posting_count,
                }
            )
        elif outcome.status == "gone":
            gone_boards.append(company.board)
        else:
            failed_boards.append(company.board)

    new_counts = await _upsert_postings_in_batches(supabase, _dedupe_postings(postings_batch))

    for entry in poll_state_results:
        if entry["relevant"] != -1:  # leave the 304 sentinel untouched
            entry["relevant"] = new_counts.get(entry["board"], 0)

    if close_targets:
        await supabase.rpc(
            "close_stale_job_registry_postings", {"close_targets": close_targets}
        ).execute()
    if poll_state_results:
        await supabase.rpc(
            "advance_job_registry_poll_state", {"results": poll_state_results}
        ).execute()
    if failed_boards or gone_boards:
        await supabase.rpc(
            "penalize_failed_job_registry_boards",
            {"failed_boards": failed_boards, "gone_boards": gone_boards},
        ).execute()

    return len(companies)


async def run_eightfold_jd_backfill(http: httpx.AsyncClient, supabase: AsyncClient) -> int:
    """Drip-feeds real job-description text into Eightfold rows the main
    list-fetch can never populate (confirmed live, P3c research: jd_text
    is ALWAYS empty on Eightfold's own list endpoint, both tiers) --
    mirrors n8n's own "Select JD Backfill Batch -> Fetch Eightfold
    JDs -> Update Job Descriptions" branch. Confirmed live: the detail
    endpoint is smartapply-shaped regardless of which tier a company's
    list data came through, so no tier-awareness is needed here -- and
    confirmed live that its own `work_location_option` field is
    unreliable (null for every company tested even when the list
    endpoint had a real value for the same job), so this backfill
    deliberately never touches `remote`, only jd_text/apply_url/salary+
    sponsorship extraction. Returns the number of rows actually updated
    (0 when nothing was eligible, or every fetch came back too short/
    failed -- a candidate that doesn't clear the bar this tick simply
    stays eligible for the next one)."""
    result = await supabase.rpc("select_eightfold_jd_backfill_candidates", {}).execute()
    candidates = cast("list[dict[str, Any]]", result.data)
    if not candidates:
        return 0

    updates: list[dict[str, Any]] = []
    for candidate in candidates:
        position_id = extract_eightfold_position_id(candidate["apply_url"])
        if position_id is None:
            continue
        detail = await fetch_eightfold_detail(
            http, candidate["api_base"], candidate["slug"], position_id
        )
        if detail is None or len(detail.jd_text) < _EIGHTFOLD_JD_BACKFILL_MIN_JD_LENGTH:
            continue
        extracted = extract_all(detail.jd_text)
        updates.append(
            {
                "job_id": candidate["id"],
                "jd_text": detail.jd_text,
                "apply_url": detail.apply_url,
                "salary_min": extracted.salary_min,
                "salary_max": extracted.salary_max,
                "salary_currency": extracted.salary_currency,
                "salary_period": extracted.salary_period,
                "sponsorship_signal": extracted.sponsorship_signal,
                "extracted_at": datetime.now(UTC).isoformat(),
                "extraction_version": extracted.extraction_version,
            }
        )

    if updates:
        await supabase.rpc(
            "backfill_job_registry_posting_descriptions", {"updates": updates}
        ).execute()
    return len(updates)


async def run_poller_forever(
    http: httpx.AsyncClient,
    supabase: AsyncClient,
    *,
    poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
) -> None:
    """A plain sleep loop, matching outbox_store.run_worker_forever's own
    shape -- not a scheduler, not backoff-aware at the loop level (each
    tick's own failure handling is inside run_poll_tick). Shutdown is
    asyncio.CancelledError propagating out of the sleep/RPC await, same
    as the outbox worker -- app.py's lifespan cancels this task directly."""
    while True:
        await run_poll_tick(http, supabase)
        await run_eightfold_jd_backfill(http, supabase)
        await asyncio.sleep(poll_interval_seconds)
