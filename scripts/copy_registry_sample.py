"""Copy a stratified sample of the job registry from one Supabase project into another.

Built for the dev project (launch plan P2.8): the full registry is over a gigabyte, so a
dev or self-hosted project gets a few hundred companies spread across every ATS type
(so every adapter has something to poll) plus a bounded number of their active postings
(any of them, not the newest). The data moves project to project; nothing is written to disk
and nothing is committed.

Source and target are two explicit sets of environment variables, never the app's own
`SUPABASE_URL`, and this script does not load `.env`:

    SAMPLE_SOURCE_SUPABASE_URL   SAMPLE_SOURCE_SERVICE_ROLE_KEY   (read only)
    SAMPLE_TARGET_SUPABASE_URL   SAMPLE_TARGET_SERVICE_ROLE_KEY   (written)

It refuses to run, before it writes anything, when:
  * the target is the source (by URL or by project ref);
  * `--target-ref` does not match the project ref in the target URL, so the human types
    the destination instead of trusting whichever variables happen to be exported;
  * the target registry is larger than the source's -- a sample only ever flows from a
    bigger registry to a smaller one, which is what stops a swapped pair of variables
    from rewriting a production registry with a sample of dev.

Re-running is safe: every write is an upsert on the natural keys the schema enforces
(`(ats_type, slug, api_base)` for companies, `(board, external_id)` for postings). A
re-run writes the same fields again: the copied companies' polling state starts over, and
their tier, poll interval and yield, and the copied postings, take the source's values.
Whatever the target's own poller has learned about them since is overwritten.

Poll state is reset on purpose, not copied: an etag from the source would make the
target's first poll answer 304 and never refresh anything.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import Any, cast
from urllib.parse import urlparse

from postgrest.types import CountMethod, ReturnMethod

from between_jobs.api.supabase_helpers import fetch_all_pages, retry_on_statement_timeout
from supabase import AsyncClient, acreate_client

_SOURCE_URL_ENV = "SAMPLE_SOURCE_SUPABASE_URL"
_SOURCE_KEY_ENV = "SAMPLE_SOURCE_SERVICE_ROLE_KEY"
_TARGET_URL_ENV = "SAMPLE_TARGET_SUPABASE_URL"
_TARGET_KEY_ENV = "SAMPLE_TARGET_SERVICE_ROLE_KEY"

DEFAULT_COMPANIES = 500
DEFAULT_POSTINGS_PER_COMPANY = 40
DEFAULT_MIN_PER_TYPE = 5
_MAX_COMPANIES = 2000
_MAX_POSTINGS_PER_COMPANY = 200

_PAGE_SIZE = 1000
_COMPANY_BATCH = 200
_POSTING_BATCH = 100
"""Postings carry a full job description and feed a generated tsvector plus several indexes,
so a bigger batch hits the statement timeout on a small project; the importer that first
loaded this table settled on the same size."""
_SOURCE_READ_CONCURRENCY = 4
"""Reads against the live source registry run a few at a time, not all at once."""

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_REF_PATTERN = re.compile(r"[a-z0-9-]+")
_SUPABASE_SUFFIX = ".supabase.co"

_COMPANY_COLUMNS = (
    "name, ats_type, slug, api_base, poll_interval, tier, relevant_yield, tier_weight"
)
_POSTING_COLUMNS = (
    "board, external_id, title, jd_text, location, remote, apply_url, posted_at, first_seen, "
    "last_seen, salary_min, salary_max, salary_currency, salary_period, sponsorship_signal, "
    "extracted_at, extraction_version"
)


class SampleRefusal(Exception):
    """The run was refused before it wrote anything."""


# -- guards ---------------------------------------------------------------------------------


def project_ref(url: str) -> str:
    """The project ref a URL points at: the first label of `<ref>.supabase.co`, or `local` for
    a local stack. Anything else is refused, because a destination that can't be named can't
    be confirmed.

    The ref must be a plain ASCII `[a-z0-9-]` label. Without that, a fullwidth spelling of the
    source's own host compares unequal here, but the HTTP client normalizes it back to the
    source and the "target is the source" guard is silently skipped.
    """
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if host in _LOCAL_HOSTS and parsed.scheme in {"http", "https"}:
        return "local"
    labels = host.split(".")
    if (
        parsed.scheme == "https"
        and host.endswith(_SUPABASE_SUFFIX)
        and len(labels) == 3
        and _REF_PATTERN.fullmatch(labels[0])
    ):
        return labels[0]
    raise SampleRefusal(
        f"cannot tell which Supabase project {url!r} is: expected https://<ref>.supabase.co "
        "or a local stack address"
    )


def check_guards(source_url: str, target_url: str, target_ref: str) -> None:
    """Refuses a source/target pair that could write somewhere it shouldn't."""
    source_ref = project_ref(source_url)
    actual_target_ref = project_ref(target_url)
    if source_ref == actual_target_ref:
        raise SampleRefusal(
            f"the target is the source (project {actual_target_ref!r}); "
            "refusing to copy a registry onto itself"
        )
    if target_ref != actual_target_ref:
        raise SampleRefusal(
            f"--target-ref {target_ref!r} does not match the target URL's project "
            f"{actual_target_ref!r}; the destination must be named exactly"
        )


def check_direction(source_companies: int, target_companies: int) -> None:
    if target_companies > source_companies:
        raise SampleRefusal(
            f"the target registry ({target_companies} companies) is larger than the source's "
            f"({source_companies}); a sample flows from a bigger registry to a smaller one, "
            "so the source and target look swapped"
        )


# -- sampling -------------------------------------------------------------------------------


def allocate_per_type(
    group_sizes: Mapping[str, int], total: int, min_per_type: int
) -> dict[str, int]:
    """How many companies to take from each ATS type.

    Every type with companies gets up to `min_per_type` first (so a small adapter is never
    squeezed out by greenhouse), then the rest is shared by remaining capacity using largest
    remainders. Never more than a type has, never more than `total` overall. When `total` is
    smaller than the number of types the floor is handed out one company at a time in name
    order, so the result is still deterministic.
    """
    types = sorted(t for t, size in group_sizes.items() if size > 0)
    alloc = dict.fromkeys(types, 0)
    if total <= 0 or not types:
        return alloc

    for _ in range(max(min_per_type, 0)):
        for t in types:
            if sum(alloc.values()) >= total:
                return alloc
            if alloc[t] < group_sizes[t]:
                alloc[t] += 1

    remaining = total - sum(alloc.values())
    while remaining > 0:
        capacity = {t: group_sizes[t] - alloc[t] for t in types if group_sizes[t] > alloc[t]}
        if not capacity:
            break
        capacity_total = sum(capacity.values())
        shares = {t: remaining * c / capacity_total for t, c in capacity.items()}
        granted = {t: min(int(share), capacity[t]) for t, share in shares.items()}
        leftover = remaining - sum(granted.values())
        by_remainder = sorted(capacity, key=lambda t: (-(shares[t] - int(shares[t])), t))
        for t in by_remainder:
            if leftover <= 0:
                break
            if granted[t] < capacity[t]:
                granted[t] += 1
                leftover -= 1
        gained = sum(granted.values())
        if gained == 0:
            break
        for t, count in granted.items():
            alloc[t] += count
        remaining -= gained
    return alloc


def _stable_rank(seed: int, company: Mapping[str, Any]) -> str:
    key = f"{seed}|{company['ats_type']}|{company['slug']}|{company['api_base'] or ''}"
    return hashlib.sha256(key.encode()).hexdigest()


def pick_sample(
    companies: Sequence[Mapping[str, Any]], *, total: int, min_per_type: int, seed: int
) -> list[Mapping[str, Any]]:
    """The sampled companies: stratified by ATS type, and the same companies for the same seed."""
    by_type: dict[str, list[Mapping[str, Any]]] = {}
    for company in companies:
        by_type.setdefault(company["ats_type"], []).append(company)
    alloc = allocate_per_type({t: len(rows) for t, rows in by_type.items()}, total, min_per_type)
    picked: list[Mapping[str, Any]] = []
    for ats_type in sorted(alloc):
        ranked = sorted(by_type[ats_type], key=lambda c: _stable_rank(seed, c))
        picked.extend(ranked[: alloc[ats_type]])
    return picked


# -- row mapping ----------------------------------------------------------------------------


def board_for(company: Mapping[str, Any]) -> str:
    """A company's board key, the same three-part key the poller and the postings use."""
    return f"{company['ats_type']}:{company['slug']}:{company['api_base'] or ''}"


def company_to_target_row(company: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    """A source company as it is written to the target: identity and scheduling weight are
    kept, polling state starts over."""
    return {
        "name": company["name"],
        "ats_type": company["ats_type"],
        "slug": company["slug"],
        "api_base": company["api_base"] or "",
        "is_active": True,
        "poll_interval": company["poll_interval"],
        "tier": company["tier"],
        "relevant_yield": company["relevant_yield"],
        "tier_weight": company["tier_weight"],
        "next_poll_at": now.isoformat(),
        "last_polled_at": None,
        "etag": None,
        "last_modified": None,
        "consecutive_failures": 0,
        "sweep_started_at": None,
        "sweep_posting_count": 0,
    }


def posting_to_target_row(posting: Mapping[str, Any], company_id: str) -> dict[str, Any]:
    row = {column.strip(): posting[column.strip()] for column in _POSTING_COLUMNS.split(",")}
    row["company_id"] = company_id
    row["status"] = "active"
    row["closed_at"] = None
    return row


# -- reading and writing --------------------------------------------------------------------


async def _count_companies(client: AsyncClient) -> int:
    result = await (
        client.table("job_registry_companies")
        .select("id", count=CountMethod.exact, head=True)
        .execute()
    )
    return int(result.count or 0)


async def _fetch_eligible_companies(source: AsyncClient) -> list[dict[str, Any]]:
    """Active companies whose last polls succeeded: a company that is failing in the source
    would only fail the same way in the target."""

    async def page(start: int, end: int) -> list[dict[str, Any]]:
        result = await (
            source.table("job_registry_companies")
            .select(_COMPANY_COLUMNS)
            .eq("is_active", True)
            .eq("consecutive_failures", 0)
            .order("id")
            .range(start, end)
            .execute()
        )
        return cast("list[dict[str, Any]]", result.data)

    rows = await fetch_all_pages(page, page_size=_PAGE_SIZE)
    return _unique_companies(rows)


def _unique_companies(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Offset paging over a table that is being written to can return a row on two pages (the
    source is a live registry whose poller changes `consecutive_failures` while this reads).
    A duplicated company would reach the target as two identical rows in one upsert batch,
    which Postgres rejects ("cannot affect row a second time"), so keep the first of each."""
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        key = (row["ats_type"], row["slug"], row["api_base"] or "")
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


async def _fetch_postings(
    source: AsyncClient, company: Mapping[str, Any], limit: int
) -> list[dict[str, Any]]:
    """Up to `limit` of the company's active postings.

    Deliberately not sorted: `last_seen` is unindexed, so `order by last_seen desc` has to
    fetch every active row of the board before it can return the first one. Measured on
    prod, that is 16,372 rows and 3.9 s for the Amazon board alone, close enough to the
    statement timeout to matter on a read against a live database. Without it the read stops
    after `limit` index entries, and a dev sample gains nothing from the newest rows anyway
    (they keep the source's timestamps until the target's own poller refreshes them)."""
    if limit <= 0:
        return []

    async def read() -> list[dict[str, Any]]:
        result = await (
            source.table("job_registry_postings")
            .select(_POSTING_COLUMNS)
            .eq("board", board_for(company))
            .eq("status", "active")
            .limit(limit)
            .execute()
        )
        return cast("list[dict[str, Any]]", result.data)

    return await retry_on_statement_timeout(read)


async def _upsert_in_batches(
    target: AsyncClient,
    table: str,
    rows: list[dict[str, Any]],
    *,
    on_conflict: str,
    batch_size: int,
) -> None:
    async def write(batch: list[dict[str, Any]]) -> None:
        await (
            target.table(table)
            .upsert(batch, on_conflict=on_conflict, returning=ReturnMethod.minimal)
            .execute()
        )

    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        await retry_on_statement_timeout(partial(write, batch))


async def _target_company_ids(target: AsyncClient) -> dict[tuple[str, str, str], str]:
    async def page(start: int, end: int) -> list[dict[str, Any]]:
        result = await (
            target.table("job_registry_companies")
            .select("id, ats_type, slug, api_base")
            .order("id")
            .range(start, end)
            .execute()
        )
        return cast("list[dict[str, Any]]", result.data)

    rows = await fetch_all_pages(page, page_size=_PAGE_SIZE)
    return {(r["ats_type"], r["slug"], r["api_base"] or ""): r["id"] for r in rows}


@dataclass
class SampleSummary:
    dry_run: bool = False
    eligible_companies: int = 0
    companies_per_type: dict[str, int] = field(default_factory=dict)
    postings_per_type: dict[str, int] = field(default_factory=dict)
    companies_without_postings: int = 0
    postings_skipped_unresolved_company: int = 0

    @property
    def companies(self) -> int:
        return sum(self.companies_per_type.values())

    @property
    def postings(self) -> int:
        return sum(self.postings_per_type.values())

    def __str__(self) -> str:
        prefix = "[DRY RUN] " if self.dry_run else ""
        lines = [
            f"{prefix}{self.companies} companies sampled from {self.eligible_companies} eligible; "
            f"{self.postings} active postings; {self.companies_without_postings} companies had "
            "no active postings in the source",
        ]
        for ats_type in sorted(self.companies_per_type):
            lines.append(
                f"  {ats_type:<16} {self.companies_per_type[ats_type]:>4} companies "
                f"{self.postings_per_type.get(ats_type, 0):>6} postings"
            )
        if self.postings_skipped_unresolved_company:
            lines.append(
                f"  {self.postings_skipped_unresolved_company} postings skipped: company not "
                "found in the target after the upsert"
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class SampledRegistry:
    """What was read from the source: the sampled companies and, in the same order, each one's
    postings. Nothing in here has touched the target."""

    eligible_companies: int
    companies: list[Mapping[str, Any]]
    postings: list[list[dict[str, Any]]]


async def read_sample(
    source: AsyncClient,
    *,
    companies: int,
    postings_per_company: int,
    min_per_type: int,
    seed: int,
) -> SampledRegistry:
    eligible = await _fetch_eligible_companies(source)
    sample = pick_sample(eligible, total=companies, min_per_type=min_per_type, seed=seed)

    gate = asyncio.Semaphore(_SOURCE_READ_CONCURRENCY)

    async def read_postings(company: Mapping[str, Any]) -> list[dict[str, Any]]:
        async with gate:
            return await _fetch_postings(source, company, postings_per_company)

    posting_lists = await asyncio.gather(*(read_postings(c) for c in sample))
    return SampledRegistry(
        eligible_companies=len(eligible), companies=sample, postings=list(posting_lists)
    )


def summarize(sampled: SampledRegistry, *, dry_run: bool) -> SampleSummary:
    summary = SampleSummary(dry_run=dry_run, eligible_companies=sampled.eligible_companies)
    for company, rows in zip(sampled.companies, sampled.postings, strict=True):
        ats_type = company["ats_type"]
        summary.companies_per_type[ats_type] = summary.companies_per_type.get(ats_type, 0) + 1
        summary.postings_per_type[ats_type] = summary.postings_per_type.get(ats_type, 0) + len(rows)
        if not rows:
            summary.companies_without_postings += 1
    return summary


async def write_sample(target: AsyncClient, sampled: SampledRegistry, *, now: datetime) -> int:
    """Upserts the sample into the target; returns how many postings found no company."""
    await _upsert_in_batches(
        target,
        "job_registry_companies",
        [company_to_target_row(c, now=now) for c in sampled.companies],
        on_conflict="ats_type,slug,api_base",
        batch_size=_COMPANY_BATCH,
    )
    ids = await _target_company_ids(target)

    unresolved = 0
    posting_rows: list[dict[str, Any]] = []
    for company, rows in zip(sampled.companies, sampled.postings, strict=True):
        company_id = ids.get((company["ats_type"], company["slug"], company["api_base"] or ""))
        for posting in rows:
            if company_id is None:
                unresolved += 1
                continue
            posting_rows.append(posting_to_target_row(posting, company_id))
    await _upsert_in_batches(
        target,
        "job_registry_postings",
        posting_rows,
        on_conflict="board,external_id",
        batch_size=_POSTING_BATCH,
    )
    return unresolved


async def copy_sample(
    source: AsyncClient,
    target: AsyncClient,
    *,
    source_url: str,
    target_url: str,
    target_ref: str,
    companies: int = DEFAULT_COMPANIES,
    postings_per_company: int = DEFAULT_POSTINGS_PER_COMPANY,
    min_per_type: int = DEFAULT_MIN_PER_TYPE,
    seed: int = 0,
    dry_run: bool = False,
    now: datetime | None = None,
) -> SampleSummary:
    if not 1 <= companies <= _MAX_COMPANIES:
        raise SampleRefusal(f"--companies must be between 1 and {_MAX_COMPANIES}")
    if not 0 <= postings_per_company <= _MAX_POSTINGS_PER_COMPANY:
        raise SampleRefusal(
            f"--postings-per-company must be between 0 and {_MAX_POSTINGS_PER_COMPANY}"
        )
    check_guards(source_url, target_url, target_ref)
    check_direction(await _count_companies(source), await _count_companies(target))

    sampled = await read_sample(
        source,
        companies=companies,
        postings_per_company=postings_per_company,
        min_per_type=min_per_type,
        seed=seed,
    )
    summary = summarize(sampled, dry_run=dry_run)
    if dry_run:
        return summary
    summary.postings_skipped_unresolved_company = await write_sample(
        target, sampled, now=now or datetime.now(UTC)
    )
    return summary


# -- command line ---------------------------------------------------------------------------


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SampleRefusal(f"{name} is not set")
    return value


async def _main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--target-ref",
        required=True,
        help="the project ref of the target, typed out; must match the target URL",
    )
    parser.add_argument("--companies", type=int, default=DEFAULT_COMPANIES)
    parser.add_argument("--postings-per-company", type=int, default=DEFAULT_POSTINGS_PER_COMPANY)
    parser.add_argument("--min-per-type", type=int, default=DEFAULT_MIN_PER_TYPE)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true", help="read and report, write nothing")
    args = parser.parse_args()

    try:
        source_url = _require(_SOURCE_URL_ENV)
        target_url = _require(_TARGET_URL_ENV)
        source = await acreate_client(source_url, _require(_SOURCE_KEY_ENV))
        target = await acreate_client(target_url, _require(_TARGET_KEY_ENV))
        summary = await copy_sample(
            source,
            target,
            source_url=source_url,
            target_url=target_url,
            target_ref=args.target_ref,
            companies=args.companies,
            postings_per_company=args.postings_per_company,
            min_per_type=args.min_per_type,
            seed=args.seed,
            dry_run=args.dry_run,
        )
    except SampleRefusal as refusal:
        raise SystemExit(f"refused: {refusal}") from refusal
    print(summary)


if __name__ == "__main__":
    asyncio.run(_main())
