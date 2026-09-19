"""Job Finder P5a/P5b/P5c/P5d -- canonical aggregation, filtering,
registry-lane merge, and location filtering for live-search results
(live-search-track.md's own P5 scoping). A faithful port of n8n's real
`Aggregate Jobs` node (read directly from `aggregate_jobs.js`, not
paraphrased): composite-key dedup, aggregator demotion, tier filtering,
tier+recency sort (P5a); role-word-boundary filtering and the tiny
hardcoded cohort/target-company lookup (P5b); the registry-lane tsvector
query (P5c) -- merging `job_registry_postings` (the ATS-poller's own
registry, P1-P3e) into the same `SearchResult` pipeline the 9
live-search providers already produce; and the gazetteer-backed 3-state
location filter (P5d, `filter_by_location`, backed by `geo_gazetteer.
py`). The sponsorship hard-exclude n8n bundles alongside its own
location filter is deliberately NOT ported -- see `geo_gazetteer.py`'s
own module docstring for the real reason (no structured work-
authorization-country field exists yet to build it against without
guessing).

**Registry lane (P5c)**: unlike n8n's own "hybrid pgvector+tsvector RRF"
hybrid-cache lane, this ships with tsvector full-text search only --
`job_registry_postings.jd_tsv` already exists, already GIN-indexed,
already populated on every row by the poller. Full pgvector+RRF stays a
later refinement once embedding population (deferred since P1) is
separately justified, not a blocker to a first useful registry-lane
merge. Every registry-lane result gets `source_tier=1.0` unconditionally
-- the registry only ever contains postings ingested via direct ATS
adapters, structurally the same "direct ATS domain" signal the URL-tier
classifier assigns external-provider tier-1 hits, and `provider=
"registry"` (a new sentinel value, not one of the 9 external providers)
so a future freshness-exemption rule (P5d+, once a caller-configurable
freshness window exists) can identify these rows the same way n8n's own
`source === 'cache'` check does.

The SQL side (`search_job_registry_postings`, `supabase/migrations/
20260831130000_fix_job_registry_search_performance.sql`) needed a real
fix, found by this phase's own live verification, not a unit test: the
first version hit a genuine statement timeout (57014) against the real
89,000+-row registry. Root cause was two real, distinct query-planning
issues, not one -- a partial GIN index scoped to `where status='active'`
(the existing posted_at-based partial index wasn't actually selective,
so BitmapAnd-ing against it added real overhead for near-zero filtering)
and restructuring both branches to LIMIT inside a subquery BEFORE
joining to `job_registry_companies` (joining the full candidate set
first forced an expensive hash join across tens of thousands of rows).
The search-query branch also drops its own `ORDER BY posted_at`
entirely, on purpose -- this function's own result order was never
load-bearing, since `aggregate_jobs()` (P5a) always re-sorts the full
merged multi-lane result set by tier+recency before a caller ever sees
it, so returning an unordered-but-correct candidate set here costs
nothing downstream, while sorting a scattered multi-thousand-row match
set before slicing to 150 was the single most expensive part of the
original query.

`Aggregate Jobs` also does a freshness-cutoff DROP, deliberately left
out of this phase (unlike the location-verified sort key, which P5d now
makes genuinely reachable -- see `aggregate_jobs()` itself). Nothing in
this codebase yet exposes a caller-configurable freshness window to drop
against -- adding an unreachable-by-any-caller drop would be dead
machinery, not a faithful port of a real capability.

**Cohort lookup, a real correction from an earlier assumption**: reading
n8n's actual `Aggregate Jobs` source (not the capability map's
compressed description) found the "MAANGO/FAANG/fintech/Big-4/dream"
cohort filter is NOT a big dataset baked into that node at all -- it's a
tiny, ~40-company, 5-named-group lookup table hardcoded in a DIFFERENT,
upstream node's own prompt (`Expand Query`, `n8n/prompts/ExpandQuery.md`).
Small enough to hardcode directly here, verbatim, no data file needed.
Separately, n8n's `data/reference/company_tiers.json` (572 Fortune-500-
based companies) is NOT this cohort lookup and NOT referenced by
`Aggregate Jobs` at all -- confirmed by reading the actual node source --
it's a different, later-stage input (almost certainly P6's own batch
fit-scoring "company-health" factor), out of scope here.

**Role filtering, one real simplification from the reference**: n8n
matches word-boundary terms against `title + department`; `SearchResult`
(this project's own P4 design) carries no `department` field, so this
matches against `title` alone. n8n's own `excluded_roles` default list
(Technical Support/Customer Success/QA Engineer/Intern/Internship) is
deliberately NOT hardcoded here -- n8n only applies that default when its
own LLM query-expansion stage decides the user's message doesn't
explicitly ask for an internship; without that stage, blanket-excluding
those terms risks WRONGLY dropping results for a caller who does want
one, the exact kind of guessed exclusion this project's "unknown means
labeled as unknown, never guessed" charter warns against. `excluded_terms`
is caller-supplied here, defaulting to none.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any, Literal, cast

from supabase import AsyncClient

from .geo_gazetteer import Gazetteer, check_location_state, get_gazetteer, resolve_location
from .search_providers import SearchResult

_AGGREGATOR_COMPANIES = frozenset(
    {
        "jobgether",
        "weekdayworks",
        "weekday",
        "cutshort",
        "instahyre",
        "crossover",
        "turing",
        "braintrust",
        "toptal",
        "uplers",
        "remotebase",
        "hirist",
        "talentprise",
    }
)
"""Verbatim from n8n's real AGGREGATORS set -- recruiting agencies that
post to real ATS boards (so they'd otherwise classify tier 1 by domain)
but aren't the employer. Exact-match against the lowercased company
name, not a substring check."""

_URL_QUERY_FRAGMENT_RX = re.compile(r"[?#].*$")
_TRAILING_SLASH_RX = re.compile(r"/+$")
_WHITESPACE_RX = re.compile(r"\s+")

_TIER_DROP_THRESHOLD = 3.0


def _canonicalize_url(url: str) -> str:
    stripped = _TRAILING_SLASH_RX.sub("", _URL_QUERY_FRAGMENT_RX.sub("", url))
    return stripped.lower()


def _dedup_keys(result: SearchResult) -> list[str]:
    keys = []
    if result.apply_url:
        keys.append("u:" + _canonicalize_url(result.apply_url))
    ct = _WHITESPACE_RX.sub(" ", f"{result.company or ''}|{result.title or ''}".lower()).strip()
    if len(ct.replace("|", "")) > 4:
        keys.append("ct:" + ct)
    return keys


def _demote_aggregators(results: list[SearchResult]) -> list[SearchResult]:
    return [
        replace(r, source_tier=_TIER_DROP_THRESHOLD)
        if (r.company or "").lower() in _AGGREGATOR_COMPANIES
        else r
        for r in results
    ]


def _backfill(keep: SearchResult, drop: SearchResult) -> SearchResult:
    """Fills gaps on the kept result from the dropped duplicate --
    salary, sponsorship signal, location -- verbatim from n8n's own
    collision-merge logic. `SearchResult` has no `salary_period` field
    (a smaller shape than n8n's own job objects, decided when P4 first
    designed this dataclass), so nothing to backfill there."""
    updates: dict[str, object] = {}
    if keep.salary_min is None and drop.salary_min is not None:
        updates["salary_min"] = drop.salary_min
        updates["salary_max"] = drop.salary_max
        updates["salary_currency"] = drop.salary_currency
    if (
        (not keep.sponsorship_signal or keep.sponsorship_signal == "unknown")
        and drop.sponsorship_signal
        and drop.sponsorship_signal != "unknown"
    ):
        updates["sponsorship_signal"] = drop.sponsorship_signal
    if (not keep.location or keep.location == "unknown") and drop.location:
        updates["location"] = drop.location
    return replace(keep, **updates) if updates else keep  # type: ignore[arg-type]


def _dedupe(results: list[SearchResult]) -> list[SearchResult]:
    by_key: dict[str, SearchResult] = {}
    for result in results:
        keys = _dedup_keys(result)
        if not keys:
            continue
        existing = next((by_key[k] for k in keys if k in by_key), None)
        if existing is None:
            for k in keys:
                by_key[k] = result
            continue
        # Collision: keep the lower tier (structured/ATS preferred over
        # web-scraped), backfill gaps from the dropped duplicate.
        keep, drop = (
            (result, existing)
            if result.source_tier < existing.source_tier
            else (
                existing,
                result,
            )
        )
        merged = _backfill(keep, drop)
        for k in [*_dedup_keys(existing), *keys]:
            by_key[k] = merged

    seen: set[int] = set()
    deduped = []
    for r in by_key.values():
        if id(r) not in seen:
            seen.add(id(r))
            deduped.append(r)
    return deduped


def _parse_posted_at(posted_at: str | None) -> float:
    """Best-effort ISO-8601 parse for sorting -- unparseable (e.g.
    Serper's occasional human-readable `date` like "Mar 10, 2026," which
    this project deliberately doesn't add a new date-parsing dependency
    for) sorts as oldest, never guessed into a specific position."""
    if not posted_at:
        return 0.0
    try:
        return datetime.fromisoformat(posted_at.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _location_rank(r: SearchResult) -> int:
    """0 sorts first (a confirmed location match); 1 covers both a
    confirmed mismatch's absence from the list by the time sort runs
    (filter_by_location already dropped those) and the common case where
    location filtering never ran at all (`location_verified is None`) --
    a no-op tiebreak identical for every result, exactly mirroring n8n's
    own "undefined on every job" behavior when its own filter never
    fired."""
    return 0 if r.location_verified is True else 1


def aggregate_jobs(
    results: list[SearchResult], *, sort_by: Literal["relevance", "newest"] = "relevance"
) -> list[SearchResult]:
    """Dedup -> aggregator demotion -> tier filter -> sort -> cap at 150,
    the same pipeline order as n8n's real Aggregate Jobs (aggregator
    demotion happens before dedup there too, so a collision between an
    aggregator-posted duplicate and a real one already carries the
    correct demoted tier into the tier-collision comparison). Sort is
    location-verified-first, then (tier, recency) or pure recency per
    `sort_by` -- verbatim from n8n's own real sort, now genuinely
    reachable since P5d's `filter_by_location` can set
    `location_verified`."""
    demoted = _demote_aggregators(results)
    deduped = _dedupe(demoted)
    filtered = [r for r in deduped if r.source_tier < _TIER_DROP_THRESHOLD]

    if sort_by == "newest":
        filtered.sort(key=lambda r: (_location_rank(r), -_parse_posted_at(r.posted_at)))
    else:
        filtered.sort(
            key=lambda r: (_location_rank(r), r.source_tier, -_parse_posted_at(r.posted_at))
        )

    return filtered[:150]


# ── P5b: cohort/target-company lookup + filter ──────────────────────────

_COHORTS: dict[str, tuple[str, ...]] = {
    "maango": ("Meta", "Anthropic", "Amazon", "Nvidia", "Google", "OpenAI"),
    "maang": ("Meta", "Apple", "Amazon", "Netflix", "Google"),
    "faang": ("Meta", "Apple", "Amazon", "Netflix", "Google"),
    "fintech": (
        "Barclays",
        "Bloomberg",
        "JPMorgan Chase",
        "Morgan Stanley",
        "Citigroup",
        "Two Sigma",
        "Visa",
        "Mastercard",
        "Jane Street",
        "Point72",
    ),
    "big 4": ("Deloitte", "EY", "KPMG", "PwC"),
    "big4": ("Deloitte", "EY", "KPMG", "PwC"),
    "dream": (
        "Airbnb",
        "Amazon",
        "Anthropic",
        "Apple",
        "Barclays",
        "Bloomberg",
        "Citigroup",
        "Cloudflare",
        "Coinbase",
        "DoorDash",
        "Figma",
        "GEICO",
        "IBM",
        "Instacart",
        "Jane Street",
        "LinkedIn",
        "Mastercard",
        "Morgan Stanley",
        "Netflix",
        "Notion",
        "Nvidia",
        "OpenAI",
        "Palantir",
        "Perplexity",
        "Point72",
        "Reddit",
        "Robinhood",
        "Salesforce",
        "SpaceX",
        "SpaceX Global",
        "Spotify",
        "Target",
        "Two Sigma",
        "Visa",
        "Walmart",
        "xAI",
    ),
}
"""Verbatim from n8n's real ExpandQuery.md prompt's own COHORT
DEFINITIONS -- a tiny, ~40-company, 5-named-group lookup, NOT the same
thing as company_tiers.json (see module docstring). Exact-match on the
cohort name (case-insensitive); n8n's own fuzzy suffix matching
("MAANGO-style", "MAANG companies") was the LLM's job upstream, not
reproduced here -- a caller unsure of the exact name should pass
company names straight to `filter_by_companies` instead."""


def expand_cohort(name: str) -> list[str] | None:
    companies = _COHORTS.get(name.strip().lower())
    return list(companies) if companies else None


_NON_ALNUM_RX = re.compile(r"[^a-z0-9]+")


def _normalize_company(name: str | None) -> str:
    return _NON_ALNUM_RX.sub(" ", (name or "").lower()).strip()


def filter_by_companies(results: list[SearchResult], companies: list[str]) -> list[SearchResult]:
    """Verbatim from n8n's own WS3 cohort-scoped-search filter: normalize
    case/punctuation on both sides, substring match either direction (so
    "JPMorgan Chase & Co." matches a target of "JPMorgan"). Empty
    `companies` is a no-op, matching n8n's own "only fires when
    target_companies is set" behavior."""
    targets = [t for t in (_normalize_company(c) for c in companies) if t]
    if not targets:
        return results
    kept = []
    for r in results:
        c = _normalize_company(r.company)
        if c and any(t in c or c in t for t in targets):
            kept.append(r)
    return kept


# ── P5b: role-word-boundary filter ───────────────────────────────────────


def _role_term_pattern(term: str) -> re.Pattern[str]:
    left = r"(?<!\w)" if re.match(r"\w", term) else ""
    return re.compile(left + re.escape(term) + r"(?!\w)")


def role_term_patterns(query: str) -> list[re.Pattern[str]]:
    """Compiles `query` into the per-term word-boundary patterns
    `filter_by_role` matches against -- split out so a second caller with
    a different haystack (Hiring Signals matches title + snippet text of a
    search hit, which is not a job-shaped `SearchResult`) reuses the exact
    same tokenization and boundary rules instead of growing a second
    regex implementation. Terms of a single character are dropped (n8n
    behavior); an empty result means "no role constraint".

    Boundaries are lookarounds, not n8n's `\\b`: `\\b` needs a word
    character on the symbol's side, so a term that starts or ends with
    punctuation -- `c++`, `c#`, `.net`, `sr.`, or a word ending in a
    Devanagari vowel sign, which is not a `\\w` character -- could never
    match ("hiring a .NET developer" failed the term `.net`). A term that
    starts with a word character may not be preceded by one (`(?<!\\w)`);
    a term that starts with a symbol has no left boundary at all, so `.net`
    still finds `ASP.NET`, which `\\b` also matched. No term may be
    followed by a word character (`(?!\\w)`). For an ordinary word this is
    identical to `\\bterm\\b`; the deliberate divergence only widens matching
    for terms `\\b` was silently unable to find. One limit remains: a
    combining mark glued after a term's last letter is not a `\\w`
    character, so in a script that uses them the term can match the front
    of a longer word."""
    terms = [t for t in query.lower().split() if len(t) > 1]
    return [_role_term_pattern(t) for t in terms]


def matches_all_role_terms(haystack: str, term_patterns: Sequence[re.Pattern[str]]) -> bool:
    """The AND-term predicate at the core of `filter_by_role`: every
    pattern must find a whole-word hit in the lower-cased `haystack`. An
    empty pattern list is vacuously true -- callers that treat "no
    terms" as a no-op check for emptiness first, as `filter_by_role`
    does."""
    lowered = haystack.lower()
    return all(p.search(lowered) for p in term_patterns)


def filter_by_role(
    results: list[SearchResult], query: str, *, excluded_terms: list[str] | None = None
) -> list[SearchResult]:
    """Word-boundary term matching against `title` (see module docstring
    for why not `title + department`) -- verbatim word-boundary
    discipline from n8n's own real F2 fix (a naive substring match let
    "AI Engineering Intern" match "AI Engineer" via "engineer" inside
    "engineering"). ALL of `query`'s own terms (length > 1) must match,
    the same AND semantics n8n's own role_families check uses. An empty
    query is a no-op (matches everything), mirroring n8n's own "no
    role_families -> filter never runs" behavior. `excluded_terms`
    entries can be multi-word phrases (a literal space in the pattern
    matches a literal space in the haystack) -- unlike n8n, this has no
    hardcoded default list; see module docstring for why."""
    term_patterns = role_term_patterns(query)
    if not term_patterns:
        return results
    excluded_patterns = [
        re.compile(r"\b" + re.escape(t.lower()) + r"\b") for t in (excluded_terms or []) if t
    ]

    kept = []
    for r in results:
        haystack = r.title.lower()
        if excluded_patterns and any(p.search(haystack) for p in excluded_patterns):
            continue
        if matches_all_role_terms(haystack, term_patterns):
            kept.append(r)
    return kept


# ── P5c: registry-lane query ──────────────────────────────────────────────

_REGISTRY_LANE_PROVIDER = "registry"
_REGISTRY_LANE_LIMIT = 150


async def fetch_registry_lane(
    supabase: AsyncClient, *, query: str, limit: int = _REGISTRY_LANE_LIMIT
) -> list[SearchResult]:
    """The registry lane: `job_registry_postings` (the ATS poller's own
    registry, P1-P3e), full-text searched via its existing `jd_tsv`
    column, converted into the same `SearchResult` shape the 9 live-
    search providers already produce. An empty `query` returns the most
    recent active postings unfiltered, matching `discovery_store.py`'s
    own existing "no query -> browse recent" behavior rather than
    returning nothing.

    Stamps `link_checked=True` on every result -- a registry-lane posting
    is only ever returned here because `job_registry_poller.py`'s own
    absence-based mechanism (P1-P3e) still sees it as `status='active'`
    on its board, a real liveness signal `ats_liveness.verify_liveness`
    (P7) is explicitly never meant to duplicate (see that module's own
    docstring). Registry-lane results must never reach `verify_liveness`
    at all -- this flag is what a caller checks to route around it."""
    result = await supabase.rpc(
        "search_job_registry_postings", {"search_query": query, "result_limit": limit}
    ).execute()
    rows = cast("list[dict[str, Any]]", result.data)
    return [
        SearchResult(
            provider=_REGISTRY_LANE_PROVIDER,
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
            link_checked=True,
        )
        for row in rows
    ]


# ── P5d: gazetteer-backed 3-state location filter ────────────────────────


def filter_by_location(
    results: list[SearchResult],
    location: str | None,
    *,
    gazetteer: Gazetteer,
    remote_only: bool = False,
) -> list[SearchResult]:
    """Verbatim from n8n's own real location-filter block: no-op when
    `remote_only` (matches n8n's `remotePref !== 'remote_only'` guard --
    a remote-only search never location-filters at all), no-op when the
    gazetteer is empty/unavailable (fail-open, see geo_gazetteer.py), and
    no-op when the request's own `location` string doesn't resolve to any
    real city/country constraint (nothing to filter against). Otherwise
    drops every confirmed MISMATCH and stamps `location_verified` on
    every survivor (`True` for a confirmed match, `None` for genuinely
    unknown -- never `False`, since a mismatch is dropped outright, not
    kept-and-flagged)."""
    if remote_only or not location or not gazetteer.city_index:
        return results

    request_geo = resolve_location(gazetteer, location)
    request_cities = frozenset(c.lower() for c in request_geo.cities)
    request_country = next(iter(request_geo.countries), None)
    if not request_cities and not request_country:
        return results

    kept = []
    for r in results:
        state = check_location_state(
            gazetteer, r.location, request_cities=request_cities, request_country=request_country
        )
        if state == "mismatch":
            continue
        kept.append(replace(r, location_verified=True if state == "match" else None))
    return kept


async def apply_search_filters(
    results: list[SearchResult],
    *,
    supabase: AsyncClient,
    companies: list[str] | None,
    query: str,
    location: str | None,
    remote_only: bool,
) -> tuple[list[SearchResult], bool]:
    """The 3-step filter chain (companies -> role -> location) shared
    identically by `discovery_routes.py`'s synchronous `/discover` search
    and `saved_search_matcher.py`'s background matcher -- confirmed the
    same shape apart from `discovery_routes.py`'s own preceding
    `aggregate_jobs()` cross-provider dedup call, which stays OUTSIDE
    this function since `saved_search_matcher.py` is registry-lane-only
    and never needs it. Fetches the gazetteer lazily, only when it will
    actually be used, matching both callers' existing behavior. Returns
    `(filtered_results, gazetteer_active)` -- the second value is
    `bool(gazetteer.city_index)` when a location filter actually ran, or
    `False` when it never ran at all; both callers need this same signal
    to compute `location_requested` correctly (a location filter that
    silently no-ops because the gazetteer table is empty/unreachable must
    not be conflated with one that genuinely confirmed a match)."""
    if companies:
        results = filter_by_companies(results, companies)
    if query:
        results = filter_by_role(results, query)
    gazetteer_active = False
    if location or remote_only:
        gazetteer = await get_gazetteer(supabase)
        results = filter_by_location(
            results, location, gazetteer=gazetteer, remote_only=remote_only
        )
        gazetteer_active = bool(gazetteer.city_index)
    return results, gazetteer_active
