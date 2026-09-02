"""Live-search providers for Job Finder P4 (live-search-track.md).

A faithful port of n8n's real `find_jobs` fan-out (read directly from
`CareerForge_Master_local.json`'s `Build <X> Queries -> <X> Fetch ->
Normalize <X>` node chains, not redesigned from a description of what it
does) for the providers n8n actually has (Serper/You.com/Firecrawl/
RemoteOK/Adzuna), plus this session's own additions with no n8n
precedent (Arbeitnow, USAJobs, Brave -- see live-search-track.md for
why).

All 9 providers are now genuinely usable end-to-end (P4a+P4b+P4c):
RemoteOK and Arbeitnow (no auth at all); You.com and Firecrawl (BYOK,
reusing the credential slots already saved for company-intel); Serper,
Brave, and JSearch (BYOK, single-secret `("search", provider)`
credentials); Adzuna and USAJobs (BYOK, the two providers needing a
SECOND real value -- `credentials.secret_2`, added in P4c -- Adzuna's
`app_key` alongside `app_id`, USAJobs' registered email alongside its
Authorization-Key). All credential registration/validation lives in
`credentials_routes.py`; this module only ever receives already-resolved
plaintext values (`try_get_secret`/`try_get_secret_pair`), same
separation of concerns as `research_clients.py`.

JSearch deliberately sends no remote-filter request param: live research
(live-search-track.md) found RapidAPI's own listing may have renamed it
(`remote_jobs_only` -> `work_from_home`) since n8n's own port, unconfirmed
against the live playground. Rather than risk a wrong/deprecated param
name, `fetch_jsearch` relies on the response's own `job_is_remote` field
instead -- add the request-side filter later once the rename is verified
against a real key.

Two providers here have no n8n source to port from (USAJobs, Arbeitnow --
n8n's own reference never wired USAJobs at all, "Parked - Lanes not
wired" per its SETUP.md, and never used Arbeitnow either) -- their
request/response shapes were verified directly against each provider's
real live API before writing this, same live-first discipline the
registry adapters (job_registry_adapters.py) already established.

Deliberately NOT ported from n8n: the gazetteer-driven free-text location
extraction (`extractLocationFromText`/`ngramLocationScan` in n8n's real
Normalize Serper/You.com/Firecrawl nodes) -- it depends on a 34k-city
GeoNames dataset living in n8n's own private file storage, not something
this project has or should bundle for a first cut. Serper/You.com/
Firecrawl results carry `location=None` here rather than a best-effort
guess -- this is the honest "unknown means labeled as unknown, never
guessed" call, not a shortcut: P5's own 3-state location filter already
treats unknown as "kept but flagged," so nothing downstream breaks,
it just means more search-lane results carry that flag than n8n's own
gazetteer-assisted version would. The URL-tier classifier and
company-from-URL extractor ARE ported verbatim (pure regex, no external
dataset needed).

Fan-out shape mirrors `company_intel_pipeline.run_research` (fail-open,
per-provider try/except swallowed into a `warnings: list[str]`, never
aborts the whole search on one provider's failure) rather than the
registry's own `AdapterResult`/board-keyed dispatch -- that shape is
built around poll-cycle/ETag/pagination state a live, per-query search
call doesn't have. Applies the fix n8n's own real bug needed (You.com is
the one provider missing `onError: continueRegularOutput` there) to
every provider uniformly from the start, not just the one that needed
patching.

No LLM query-expansion stage exists here (deliberately, see
live-search-track.md's own architecture-decisions section) -- callers
pass a free-text `query` (used as the literal role/keyword phrase),
optional `location`, and optional `companies` list directly, matching
`discovery_routes.py`'s own existing simple `q`-param shape. Deterministic
`site:`-scoped sub-queries are built per-provider from that input, porting
n8n's real per-provider domain groupings and caps verbatim (they're not
identical across providers in the reference, so this keeps 3 small
per-provider builders rather than forcing one shared "unified" builder
that would lose that fidelity).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from .errors import ApiError
from .job_posting_extraction import extract_all, extract_sponsorship

_TIMEOUT_SECONDS = 20.0

_HTML_TAG_RX = re.compile(r"<[^>]+>")
_WHITESPACE_RX = re.compile(r"\s+")
_HTML_ENTITIES = (
    ("&amp;", "&"),
    ("&lt;", "<"),
    ("&gt;", ">"),
    ("&quot;", '"'),
    ("&#39;", "'"),
    ("&#x27;", "'"),
)


def _decode_entities(s: str) -> str:
    for entity, ch in _HTML_ENTITIES:
        s = s.replace(entity, ch)
    return s


def _strip_html(s: str | None, *, max_length: int) -> str:
    text = _decode_entities(_decode_entities(s or ""))
    text = _HTML_TAG_RX.sub(" ", text)
    return _WHITESPACE_RX.sub(" ", text).strip()[:max_length]


_REMOTE_RX = re.compile(r"\bremote\b", re.IGNORECASE)


def _is_remote(*parts: str | None) -> bool:
    return any(_REMOTE_RX.search(p) for p in parts if p)


@dataclass(frozen=True)
class SearchResult:
    provider: str
    title: str
    company: str | None
    location: str | None
    remote: bool | None
    apply_url: str
    snippet: str
    posted_at: str | None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    sponsorship_signal: str = "unknown"
    source_tier: float = 4.0
    location_verified: bool | None = None
    """None (default) means location filtering wasn't applied at all --
    a no-op sort key, matching n8n's own real behavior where this field
    is undefined on every job until location filtering is actually
    active (Job Finder P5d). True/False only ever get set by
    `search_aggregation.filter_by_location`."""
    link_checked: bool = False
    """True once a real liveness signal has confirmed this result --
    either `ats_liveness.verify_liveness` (Job Finder P7) ran a live
    per-platform probe against it (live-search-lane results), or it's a
    registry-lane result, which `search_aggregation.fetch_registry_lane`
    always stamps `True` at construction -- the ATS poller's own
    absence-based mechanism (P1-P3e) already re-confirms these on every
    poll tick, a real liveness signal `verify_liveness` is explicitly
    never meant to duplicate (registry-lane results must never reach it
    at all). Only a live-search-lane result that predates P7 running
    keeps the default `False`."""


# ── URL-tier classifier (ported verbatim from n8n's real classifyUrlTier,
# CareerForge_Master_local.json's "Normalize Serper results" node -- same
# function shared, unchanged, across n8n's own Serper/You.com/Firecrawl
# normalizers) ────────────────────────────────────────────────────────────

_TIER_1_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?:boards|job-boards)\.greenhouse\.io"), "ats:greenhouse"),
    (re.compile(r"jobs\.lever\.co"), "ats:lever"),
    (re.compile(r"jobs\.ashbyhq\.com"), "ats:ashby"),
    (re.compile(r"[\w-]+\.wd\d+\.myworkdayjobs\.com"), "ats:workday"),
    (re.compile(r"apply\.workable\.com"), "ats:workable"),
    (re.compile(r"jobs\.smartrecruiters\.com"), "ats:smartrecruiters"),
    (re.compile(r"workatastartup\.com/jobs/"), "ats:yc"),
    (re.compile(r"[\w-]+\.recruitee\.com"), "ats:recruitee"),
    (re.compile(r"[\w-]+\.personio\.(?:com|de)"), "ats:personio"),
    (re.compile(r"[\w-]+\.bamboohr\.com"), "ats:bamboohr"),
    (re.compile(r"[\w-]+\.jobvite\.com"), "ats:jobvite"),
    (re.compile(r"careers-[\w-]+\.icims\.com"), "ats:icims"),
    (re.compile(r"[\w-]+\.taleo\.net"), "ats:taleo"),
    (re.compile(r"successfactors\.(?:com|eu)"), "ats:successfactors"),
    (re.compile(r"[\w-]+\.eightfold\.ai"), "ats:eightfold"),
    (re.compile(r"[\w-]+\.avature\.net"), "ats:avature"),
    (re.compile(r"google\.com/about/careers/applications/jobs/results"), "ats:google"),
    (re.compile(r"deshaw\.com/careers"), "ats:deshaw"),
    (re.compile(r"apply\.careers\.microsoft\.com/careers/job"), "ats:microsoft"),
)

_TIER_2_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"weworkremotely\.com/(?:remote-jobs|listings)/"), "curated:wwr"),
    (re.compile(r"remoteok\.(?:com|io)"), "curated:remoteok"),
    (re.compile(r"himalayas\.app/jobs/"), "curated:himalayas"),
    (re.compile(r"remotive\.(?:com|io)/(?:jobs|remote-jobs)/"), "curated:remotive"),
    (re.compile(r"jobicy\.com"), "curated:jobicy"),
    (re.compile(r"wellfound\.com/jobs/"), "curated:wellfound"),
    (re.compile(r"arbeitnow\.com/jobs/"), "curated:arbeitnow"),
    (re.compile(r"ai-jobs\.net/job/"), "curated:aijobs"),
    (
        re.compile(r"builtin(?:nyc|sf|la|chicago|seattle|boston|austin)?\.com/job/"),
        "curated:builtin",
    ),
)

_TIER_3_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"linkedin\.com"), "aggregator:linkedin"),
    (re.compile(r"indeed\.com"), "aggregator:indeed"),
    (re.compile(r"glassdoor\.com"), "aggregator:glassdoor"),
    (re.compile(r"ziprecruiter\.com"), "aggregator:ziprecruiter"),
    (re.compile(r"dice\.com"), "aggregator:dice"),
    (re.compile(r"monster\.com"), "aggregator:monster"),
    (re.compile(r"simplyhired\.com"), "aggregator:simplyhired"),
    (re.compile(r"careerbuilder\.com"), "aggregator:careerbuilder"),
    (re.compile(r"snagajob\.com"), "aggregator:snagajob"),
    (re.compile(r"theladders\.com"), "aggregator:theladders"),
    (re.compile(r"jora\.com"), "aggregator:jora"),
    (re.compile(r"talent\.com"), "aggregator:talent"),
    (re.compile(r"jobs2careers\.com"), "aggregator:jobs2careers"),
)

_TIER_2_5_SUBDOMAIN_RX = re.compile(
    r"^https?://(?:careers|jobs|apply|hiring|talent|work|join)\.[\w-]+\.[\w.]+"
)
_TIER_2_5_PATH_RX = re.compile(
    r"/(?:careers|jobs|join-us|join_us|apply|positions|openings"
    r"|opportunities|vacancies|hiring|work-with-us)/"
)


def _classify_url_tier(url: str) -> tuple[float, str]:
    if not url:
        return 4.0, "invalid"
    u = url.lower()
    for rx, label in _TIER_1_PATTERNS:
        if rx.search(u):
            return 1.0, label
    for rx, label in _TIER_2_PATTERNS:
        if rx.search(u):
            return 2.0, label
    for rx, label in _TIER_3_PATTERNS:
        if rx.search(u):
            return 3.0, label
    if _TIER_2_5_SUBDOMAIN_RX.search(u) or _TIER_2_5_PATH_RX.search(u):
        return 2.5, "career:page"
    return 4.0, "unknown"


_COMPANY_FROM_URL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([^/?&#]+)"),
    re.compile(r"jobs\.lever\.co/([^/?#]+)"),
    re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)"),
    re.compile(r"([^./]+)\.wd\d+\.myworkdayjobs\.com"),
    re.compile(r"apply\.workable\.com/([^/?#]+)"),
    re.compile(r"jobs\.smartrecruiters\.com/([^/?#]+)"),
    re.compile(r"workatastartup\.com/jobs/\d+/[^?#]*?-at-([^/?#]+)"),
    re.compile(r"wellfound\.com/jobs/[^/]*?-at-([^/?#]+)"),
    re.compile(r"wellfound\.com/company/([^/?#]+)"),
)
_COMPANY_FROM_URL_HOST_RX = re.compile(r"^https?://(?:www\.)?([^.]+)\.")


def _extract_company_from_url(url: str) -> str | None:
    for rx in _COMPANY_FROM_URL_PATTERNS:
        m = rx.search(url)
        if m:
            return m.group(1)
    m = _COMPANY_FROM_URL_HOST_RX.match(url)
    if m:
        return m.group(1)
    return None


def _deslugify_company(slug: str | None) -> str | None:
    """Mirrors n8n's own real Build Telegraph Body transform (its s156
    fix note): a URL slug ("wisdom-ai") de-hyphenated and title-cased for
    display ("Wisdom Ai"), applied once at normalize time so every
    downstream consumer agrees, not re-derived inconsistently per
    caller."""
    if not slug:
        return None
    return re.sub(r"\b\w", lambda m: m.group(0).upper(), slug.replace("-", " ")).strip() or None


def _run_extraction(result_kwargs: dict[str, Any], *, text: str) -> None:
    """Fills salary/sponsorship fields from free text ONLY when the
    provider gave no structured salary of its own -- mirrors
    job_registry_poller.py's own _extract_if_eligible gate (a result too
    short to extract from stays honestly unextracted, sponsorship stays
    "unknown", never guessed)."""
    if len(text) < 50:
        result_kwargs["sponsorship_signal"] = extract_sponsorship(text)
        return
    extracted = extract_all(text)
    result_kwargs["salary_min"] = extracted.salary_min
    result_kwargs["salary_max"] = extracted.salary_max
    result_kwargs["salary_currency"] = extracted.salary_currency
    result_kwargs["sponsorship_signal"] = extracted.sponsorship_signal


# ── Deterministic query construction (ported verbatim from n8n's real
# Build Serper/You.com/FC Queries nodes, minus the LLM-suggested-queries
# merge -- see module docstring) ──────────────────────────────────────────


def _location_phrase(location: str | None) -> str:
    loc = (location or "").split(",")[0].strip()
    return f' "{loc}"' if loc else ""


def _company_queries(role: str, location: str | None, companies: list[str] | None) -> list[str]:
    loc_part = _location_phrase(location)
    return [f'"{role}" "{co}" jobs{loc_part}' for co in (companies or [])[:3]]


def _dedup_cap(queries: list[str], cap: int) -> list[str]:
    seen: set[str] = set()
    deduped = []
    for q in queries:
        k = q.strip().lower()
        if k and k not in seen:
            seen.add(k)
            deduped.append(q)
    return deduped[:cap]


def build_serper_queries(
    role: str, *, location: str | None = None, companies: list[str] | None = None
) -> list[str]:
    """Freshness (n8n's `after:YYYY-MM-DD`) is left to the caller via
    Serper's own `tbs` request param instead of baked into the query text
    -- between-jobs calls Serper's raw HTTP API directly (unlike n8n's
    node), so `tbs` is a real, cheap request param, not something that
    needs faking in query text."""
    loc_part = _location_phrase(location)
    deterministic = (
        [
            f'(site:boards.greenhouse.io OR site:jobs.lever.co) "{role}"{loc_part}',
            f'(site:jobs.ashbyhq.com OR site:myworkdayjobs.com) "{role}"{loc_part}',
            f'(site:apply.workable.com OR site:jobs.smartrecruiters.com) "{role}"{loc_part}',
        ]
        if role
        else []
    )
    company_qs = _company_queries(role, location, companies) if role else []
    cap = 8 if companies else 4
    return _dedup_cap([*deterministic, *company_qs], cap)


def build_you_com_queries(
    role: str, *, location: str | None = None, companies: list[str] | None = None
) -> list[str]:
    loc_part = _location_phrase(location)
    deterministic = (
        [
            f'site:boards.greenhouse.io "{role}"{loc_part}',
            f'site:jobs.lever.co "{role}"{loc_part}',
            f'(site:jobs.ashbyhq.com OR site:apply.workable.com) "{role}"{loc_part}',
            f'(site:myworkdayjobs.com OR site:jobs.smartrecruiters.com) "{role}"{loc_part}',
        ]
        if role
        else []
    )
    company_qs = _company_queries(role, location, companies) if role else []
    cap = 8 if companies else 6
    return _dedup_cap([*deterministic, *company_qs], cap)


def build_firecrawl_queries(
    role: str,
    *,
    location: str | None = None,
    companies: list[str] | None = None,
    remote_only: bool = False,
) -> list[str]:
    loc_part = _location_phrase(location)
    deterministic = (
        [
            f'site:boards.greenhouse.io "{role}"{loc_part}',
            f'site:jobs.lever.co "{role}"{loc_part}',
            f'(site:jobs.ashbyhq.com OR site:apply.workable.com) "{role}"{loc_part}',
            f'(site:myworkdayjobs.com OR site:jobs.smartrecruiters.com) "{role}"{loc_part}',
        ]
        if role
        else []
    )
    remote_extra = (
        [f'(site:weworkremotely.com OR site:remoteok.com OR site:wellfound.com) "{role}"']
        if remote_only and role
        else []
    )
    # n8n's own reference caps deterministic+remote_extra together at a flat
    # 4 -- since deterministic alone already fills 4 slots, remote_extra
    # never actually survives there (an unflagged quirk, not a documented
    # fix like the You.com onError gap). Caught by this port's own tests,
    # not by anything in the reference itself -- fixed here by widening the
    # cap by exactly remote_extra's own size, rather than reproducing a
    # query that would otherwise always be silently starved.
    ats_cap = 4 + len(remote_extra)
    ats_queries = _dedup_cap([*deterministic, *remote_extra], ats_cap)
    company_qs = _company_queries(role, location, companies) if role else []
    return [*ats_queries, *company_qs][:6]


# ── RemoteOK (no auth) ─────────────────────────────────────────────────────

_REMOTEOK_URL = "https://remoteok.com/api"


def _remoteok_relevant(haystack: str, role_words: list[str]) -> bool:
    """Shared by RemoteOK and Arbeitnow (the name is a historical
    misnomer -- see `fetch_arbeitnow`'s own call site). Mirrors
    `search_aggregation.filter_by_role`'s own already-proven pattern
    (`len(w) > 1` so 2-char acronyms like "AI"/"QA"/"PM" still count,
    word-boundary regex instead of a naive substring) rather than the
    `len(w) > 2` threshold this used to have, which dropped every word of
    an all-short-word query and made this vacuously `True` for any
    result."""
    terms = [w for w in role_words if len(w) > 1]
    if not terms:
        return True
    return all(re.search(r"\b" + re.escape(w) + r"\b", haystack) for w in terms)


async def fetch_remoteok(http: httpx.AsyncClient, *, query: str) -> list[SearchResult]:
    try:
        response = await http.get(
            _REMOTEOK_URL,
            headers={"User-Agent": "between-jobs/1.0 (job search)"},
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "Couldn't reach RemoteOK. Try again in a moment.",
            retryable=True,
        ) from e
    if response.status_code >= 400:
        raise ApiError(
            "PROVIDER_UNAVAILABLE", "RemoteOK couldn't complete that search.", retryable=True
        )

    body = response.json()
    entries = [
        e
        for e in body
        if isinstance(e, dict) and e.get("position") and (e.get("id") or e.get("slug"))
    ]
    role_words = query.lower().split()

    results = []
    for j in entries:
        title = str(j.get("position") or "")
        description = str(j.get("description") or "")
        haystack = f"{title} {' '.join(j.get('tags') or [])} {description}".lower()
        if not _remoteok_relevant(haystack, role_words):
            continue
        location = str(j.get("location") or "").strip() or "Remote"
        snippet = _strip_html(description, max_length=2000)
        salary_min = j.get("salary_min")
        salary_max = j.get("salary_max")
        kwargs: dict[str, Any] = {
            "provider": "remoteok",
            "title": title,
            "company": j.get("company") or None,
            "location": location,
            "remote": True,
            "apply_url": j.get("url") or j.get("apply_url") or "",
            "snippet": snippet,
            "posted_at": j.get("date") or None,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": "USD"
            if (salary_min is not None or salary_max is not None)
            else None,
            "source_tier": 2.0,
        }
        kwargs["sponsorship_signal"] = extract_sponsorship(f"{title} {snippet}")
        if kwargs["apply_url"]:
            results.append(SearchResult(**kwargs))
    return results


# ── Arbeitnow (no auth) ────────────────────────────────────────────────────

_ARBEITNOW_URL = "https://www.arbeitnow.com/api/job-board-api"
_ARBEITNOW_MAX_PAGES = 3
"""Confirmed live (2026-08-31): `search`/`tags`/`remote` query params are
all decorative -- every page returns the same undifferentiated feed
regardless of those params, so filtering is entirely client-side (same
shape as RemoteOK). `visa_sponsorship` IS a real, confirmed-working
server-side filter, but it's a request param, not a response field
(never present in a plain fetch), so sponsorship_signal here comes from
extract_sponsorship on the description text, never guessed from the
absence of a filter param."""


async def fetch_arbeitnow(http: httpx.AsyncClient, *, query: str) -> list[SearchResult]:
    role_words = query.lower().split()
    results: list[SearchResult] = []
    for page in range(1, _ARBEITNOW_MAX_PAGES + 1):
        try:
            response = await http.get(
                _ARBEITNOW_URL,
                params={"page": page},
                headers={"User-Agent": "between-jobs/1.0 (job search)"},
                timeout=_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as e:
            if page == 1:
                raise ApiError(
                    "PROVIDER_UNAVAILABLE",
                    "Couldn't reach Arbeitnow. Try again in a moment.",
                    retryable=True,
                ) from e
            break
        if response.status_code >= 400:
            if page == 1:
                raise ApiError(
                    "PROVIDER_UNAVAILABLE",
                    "Arbeitnow couldn't complete that search.",
                    retryable=True,
                )
            break

        body = response.json()
        entries = body.get("data") or []
        if not entries:
            break
        for j in entries:
            title = str(j.get("title") or "")
            company = j.get("company_name") or None
            description = str(j.get("description") or "")
            haystack = f"{title} {' '.join(j.get('tags') or [])} {description}".lower()
            if not _remoteok_relevant(haystack, role_words):
                continue
            location = str(j.get("location") or "").strip() or None
            remote = bool(j.get("remote")) or _is_remote(location, title)
            apply_url = j.get("url") or ""
            if not apply_url:
                continue
            snippet = _strip_html(description, max_length=2000)
            kwargs: dict[str, Any] = {
                "provider": "arbeitnow",
                "title": title,
                "company": company,
                "location": location,
                "remote": remote,
                "apply_url": apply_url,
                "snippet": snippet,
                "posted_at": None,
                "source_tier": 2.0,
            }
            created_at = j.get("created_at")
            if isinstance(created_at, int | float):
                from datetime import UTC, datetime

                kwargs["posted_at"] = datetime.fromtimestamp(created_at, tz=UTC).isoformat()
            _run_extraction(kwargs, text=f"{title} {snippet}")
            results.append(SearchResult(**kwargs))
        if len(entries) < 175:  # short page -> this is the last one
            break
    return results


# ── USAJobs (BYOK: Authorization-Key + a registered email as User-Agent)
# No n8n precedent (its own `usajobs_key` was loaded but never wired to
# anything -- "Parked, lanes not wired" per the reference's own
# SETUP.md). Request/response shape verified directly against the live
# API before writing this (a real 401 confirms the endpoint/header names;
# the exact per-job field names come from USAJobs' own stable, long-lived
# public schema -- reconfirm on first real live tick with a real key,
# which wasn't available during this research pass). ──────────────────────

_USAJOBS_URL = "https://data.usajobs.gov/api/search"


async def fetch_usajobs(
    http: httpx.AsyncClient, *, query: str, api_key: str, email: str, location: str | None = None
) -> list[SearchResult]:
    params: dict[str, str] = {"Keyword": query, "ResultsPerPage": "25"}
    if location:
        params["LocationName"] = location
    try:
        response = await http.get(
            _USAJOBS_URL,
            params=params,
            headers={
                "Host": "data.usajobs.gov",
                "User-Agent": email,
                "Authorization-Key": api_key,
            },
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE", "Couldn't reach USAJobs. Try again in a moment.", retryable=True
        ) from e
    if response.status_code in (401, 403):
        raise ApiError("PROVIDER_UNAVAILABLE", "USAJobs rejected that key.", retryable=False)
    if response.status_code >= 400:
        raise ApiError(
            "PROVIDER_UNAVAILABLE", "USAJobs couldn't complete that search.", retryable=True
        )

    body = response.json()
    items = ((body.get("SearchResult") or {}).get("SearchResultItems")) or []

    results = []
    for item in items:
        j = item.get("MatchedObjectDescriptor") or {}
        title = j.get("PositionTitle") or ""
        apply_urls = j.get("ApplyURI") or []
        apply_url = apply_urls[0] if apply_urls else (j.get("PositionURI") or "")
        if not apply_url:
            continue
        locations = j.get("PositionLocation") or []
        location_names = [loc.get("LocationName") for loc in locations if loc.get("LocationName")]
        location_text = "; ".join(location_names) or None
        summary = ((j.get("UserArea") or {}).get("Details") or {}).get("JobSummary") or ""
        snippet = _strip_html(summary, max_length=2000)

        salary_min = salary_max = None
        remuneration = j.get("PositionRemuneration") or []
        if remuneration:
            r0 = remuneration[0]
            try:
                min_range = r0.get("MinimumRange")
                max_range = r0.get("MaximumRange")
                salary_min = float(min_range) if min_range is not None else None
                salary_max = float(max_range) if max_range is not None else None
            except (TypeError, ValueError):
                salary_min = salary_max = None

        kwargs: dict[str, Any] = {
            "provider": "usajobs",
            "title": title,
            "company": j.get("OrganizationName") or j.get("DepartmentName") or None,
            "location": location_text,
            "remote": _is_remote(location_text, title),
            "apply_url": apply_url,
            "snippet": snippet,
            "posted_at": j.get("PublicationStartDate") or None,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": "USD"
            if (salary_min is not None or salary_max is not None)
            else None,
            "sponsorship_signal": "explicit_no",
            "source_tier": 1.5,
        }
        # Federal positions require US work authorization essentially
        # without exception -- this is the one provider where "explicit_no"
        # is a structural fact about the JOB CATEGORY, not text-mined from
        # a single posting's own wording (unlike every other provider's
        # sponsorship_signal here).
        results.append(SearchResult(**kwargs))
    return results


# ── Adzuna (BYOK, new 2-secret credential: service="search",
# provider="adzuna" -- app_id in `secret`, app_key in `secret_2`). Faithful
# port of n8n's real Normalize Adzuna node (currency lookup table and all).
# ────────────────────────────────────────────────────────────────────────

_ADZUNA_CURRENCY_BY_COUNTRY = {
    "in": "INR",
    "us": "USD",
    "gb": "GBP",
    "au": "AUD",
    "ca": "CAD",
    "de": "EUR",
    "fr": "EUR",
    "nl": "EUR",
    "sg": "SGD",
    "za": "ZAR",
    "br": "BRL",
    "mx": "MXN",
    "it": "EUR",
    "es": "EUR",
    "pl": "PLN",
    "at": "EUR",
    "ch": "CHF",
    "nz": "NZD",
    "be": "EUR",
}


async def fetch_adzuna(
    http: httpx.AsyncClient,
    *,
    query: str,
    app_id: str,
    app_key: str,
    location: str | None = None,
    country: str = "us",
) -> list[SearchResult]:
    """`country` picks both the URL path segment (Adzuna is country-
    specific, one of 19 supported codes) and the currency lookup for
    `salary_currency` -- mirrors n8n's own real behavior of deriving
    currency from country rather than trusting an unlabeled number
    (s87's own fix: an unknown country means unknown currency, never an
    assumed USD)."""
    try:
        response = await http.get(
            f"https://api.adzuna.com/v1/api/jobs/{country}/search/1",
            params={
                "app_id": app_id,
                "app_key": app_key,
                "what": query,
                "where": (location or "").split(",")[0].strip(),
                "results_per_page": "20",
                "content-type": "application/json",
            },
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE", "Couldn't reach Adzuna. Try again in a moment.", retryable=True
        ) from e
    if response.status_code == 410:
        raise ApiError("PROVIDER_UNAVAILABLE", "Adzuna rejected that key.", retryable=False)
    if response.status_code >= 400:
        raise ApiError(
            "PROVIDER_UNAVAILABLE", "Adzuna couldn't complete that search.", retryable=True
        )

    body = response.json()
    currency = _ADZUNA_CURRENCY_BY_COUNTRY.get(country.lower())

    results = []
    for r in body.get("results") or []:
        title = r.get("title") or ""
        url = r.get("redirect_url") or ""
        if not url:
            continue
        loc = r.get("location") or {}
        location_text = loc.get("display_name") or ", ".join(loc.get("area") or []) or None
        description = str(r.get("description") or "")
        snippet = _strip_html(description, max_length=500)
        salary_min = r.get("salary_min")
        salary_max = r.get("salary_max")
        kwargs: dict[str, Any] = {
            "provider": "adzuna",
            "title": title,
            "company": ((r.get("company") or {}).get("display_name")) or None,
            "location": location_text,
            "remote": _is_remote(location_text, title),
            "apply_url": url,
            "snippet": snippet,
            "posted_at": r.get("created") or None,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": currency
            if (salary_min is not None or salary_max is not None)
            else None,
            "source_tier": 1.5,
        }
        kwargs["sponsorship_signal"] = extract_sponsorship(f"{title} {snippet}")
        results.append(SearchResult(**kwargs))
    return results


# ── You.com (BYOK, existing credential slot: service="search",
# provider="you_com") ──────────────────────────────────────────────────────

_YOU_COM_URL = "https://ydc-index.io/v1/search"


async def fetch_you_com(
    http: httpx.AsyncClient,
    *,
    query: str,
    api_key: str,
    location: str | None = None,
    companies: list[str] | None = None,
    freshness: str = "week",
) -> list[SearchResult]:
    queries = build_you_com_queries(query, location=location, companies=companies)
    if not queries:
        return []

    results: list[SearchResult] = []
    for q in queries:
        try:
            response = await http.post(
                _YOU_COM_URL,
                headers={"X-API-Key": api_key},
                json={"query": q, "count": 10, "freshness": freshness},
                timeout=_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as e:
            raise ApiError(
                "PROVIDER_UNAVAILABLE",
                "Couldn't reach You.com. Try again in a moment.",
                retryable=True,
            ) from e
        if response.status_code >= 400:
            raise ApiError(
                "PROVIDER_UNAVAILABLE", "You.com couldn't complete that search.", retryable=True
            )

        body = response.json()
        for r in (body.get("results") or {}).get("web") or []:
            url = r.get("url") or ""
            if not url:
                continue
            title = r.get("title") or ""
            snippet = (" ".join(r.get("snippets") or []) or r.get("description") or "")[:600]
            tier, _tier_label = _classify_url_tier(url)
            kwargs: dict[str, Any] = {
                "provider": "you_com",
                "title": title,
                "company": _deslugify_company(_extract_company_from_url(url)),
                "location": None,
                "remote": _is_remote(title, snippet) or None,
                "apply_url": url,
                "snippet": snippet,
                "posted_at": r.get("page_age"),
                "source_tier": tier,
            }
            _run_extraction(kwargs, text=f"{title} {snippet}")
            results.append(SearchResult(**kwargs))
    return results


# ── Firecrawl (BYOK, existing credential slot: service="search",
# provider="firecrawl") ────────────────────────────────────────────────────

_FIRECRAWL_URL = "https://api.firecrawl.dev/v2/search"


async def fetch_firecrawl(
    http: httpx.AsyncClient,
    *,
    query: str,
    api_key: str,
    location: str | None = None,
    companies: list[str] | None = None,
    remote_only: bool = False,
    freshness: str = "qdr:w",
) -> list[SearchResult]:
    """Wires `limit`/`tbs` into the real request body -- a genuine, small
    improvement over n8n's own reference, not just a note: live research
    (live-search-track.md) confirmed n8n's own Firecrawl node computes
    this exact `bodyObj` shape and then never sends it (a real, still-live
    gap in the reference, `useCustomBody: true` with no body wired). Since
    between-jobs calls Firecrawl's raw API directly rather than through
    that node, there's no reason to reproduce the omission -- `limit`/
    `tbs` are real, current Firecrawl v2 params, confirmed live. Domain
    restriction still rides on the `site:` operators baked into the query
    TEXT (matching the existing pattern), not `includeDomains` -- using
    both would double-restrict for no benefit."""
    queries = build_firecrawl_queries(
        query, location=location, companies=companies, remote_only=remote_only
    )
    if not queries:
        return []

    limit = 25 if remote_only else 10
    results: list[SearchResult] = []
    for q in queries:
        try:
            response = await http.post(
                _FIRECRAWL_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "query": q,
                    "limit": limit,
                    "sources": [{"type": "web"}],
                    "tbs": freshness,
                },
                timeout=_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as e:
            raise ApiError(
                "PROVIDER_UNAVAILABLE",
                "Couldn't reach Firecrawl. Try again in a moment.",
                retryable=True,
            ) from e
        if response.status_code >= 400:
            raise ApiError(
                "PROVIDER_UNAVAILABLE", "Firecrawl couldn't complete that search.", retryable=True
            )

        body = response.json()
        for r in (body.get("data") or {}).get("web") or []:
            url = r.get("url") or (r.get("metadata") or {}).get("sourceURL") or ""
            if not url:
                continue
            title = r.get("title") or (r.get("metadata") or {}).get("title") or ""
            snippet = (
                r.get("description")
                or r.get("markdown")
                or (r.get("metadata") or {}).get("description")
                or ""
            )[:600]
            tier, _tier_label = _classify_url_tier(url)
            kwargs: dict[str, Any] = {
                "provider": "firecrawl",
                "title": title,
                "company": _deslugify_company(_extract_company_from_url(url)),
                "location": (r.get("metadata") or {}).get("location") or None,
                "remote": _is_remote(title, snippet) or None,
                "apply_url": url,
                "snippet": snippet,
                "posted_at": (r.get("metadata") or {}).get("publishedTime"),
                "source_tier": tier,
            }
            _run_extraction(kwargs, text=f"{title} {snippet}")
            results.append(SearchResult(**kwargs))
    return results


# ── Serper (BYOK, new credential: service="search", provider="serper")
# Google SERP proxy -- confirmed live (live-search-track.md's own research)
# it has NO dedicated Google-Jobs endpoint (that's a different company,
# SerpApi) -- reuses the same deterministic site:-scoped query pattern as
# You.com/Firecrawl. ────────────────────────────────────────────────────────

_SERPER_URL = "https://google.serper.dev/search"


async def fetch_serper(
    http: httpx.AsyncClient,
    *,
    query: str,
    api_key: str,
    location: str | None = None,
    companies: list[str] | None = None,
    freshness: str = "qdr:w",
    country: str = "us",
) -> list[SearchResult]:
    queries = build_serper_queries(query, location=location, companies=companies)
    if not queries:
        return []

    results: list[SearchResult] = []
    for q in queries:
        try:
            response = await http.post(
                _SERPER_URL,
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json={"q": q, "num": 10, "gl": country, "tbs": freshness},
                timeout=_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as e:
            raise ApiError(
                "PROVIDER_UNAVAILABLE",
                "Couldn't reach Serper. Try again in a moment.",
                retryable=True,
            ) from e
        if response.status_code >= 400:
            raise ApiError(
                "PROVIDER_UNAVAILABLE", "Serper couldn't complete that search.", retryable=True
            )

        body = response.json()
        for r in body.get("organic") or []:
            url = r.get("link") or ""
            if not url:
                continue
            title = r.get("title") or ""
            snippet = (r.get("snippet") or "")[:500]
            tier, _tier_label = _classify_url_tier(url)
            kwargs: dict[str, Any] = {
                "provider": "serper",
                "title": title,
                "company": _deslugify_company(_extract_company_from_url(url)),
                "location": None,
                "remote": _is_remote(title, snippet) or None,
                "apply_url": url,
                "snippet": snippet,
                "posted_at": r.get("date"),
                "source_tier": tier,
            }
            _run_extraction(kwargs, text=f"{title} {snippet}")
            results.append(SearchResult(**kwargs))
    return results


# ── Brave Search (BYOK, new credential: service="search", provider="brave")
# No n8n precedent (Brave wasn't one of n8n's 6 -- it's this session's own
# addition, decided 2026-08-31 for index independence from Serper/You.com's
# effectively-Google-backed results). Same general-SERP shape as Serper, so
# reuses build_serper_queries rather than a near-duplicate builder. ────────

_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"


async def fetch_brave(
    http: httpx.AsyncClient,
    *,
    query: str,
    api_key: str,
    location: str | None = None,
    companies: list[str] | None = None,
    freshness: str = "pw",
    country: str = "us",
) -> list[SearchResult]:
    queries = build_serper_queries(query, location=location, companies=companies)
    if not queries:
        return []

    results: list[SearchResult] = []
    for q in queries:
        try:
            response = await http.get(
                _BRAVE_URL,
                headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
                params={"q": q, "count": 20, "freshness": freshness, "country": country},
                timeout=_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as e:
            raise ApiError(
                "PROVIDER_UNAVAILABLE",
                "Couldn't reach Brave. Try again in a moment.",
                retryable=True,
            ) from e
        if response.status_code >= 400:
            raise ApiError(
                "PROVIDER_UNAVAILABLE", "Brave couldn't complete that search.", retryable=True
            )

        body = response.json()
        for r in (body.get("web") or {}).get("results") or []:
            url = r.get("url") or ""
            if not url:
                continue
            title = r.get("title") or ""
            snippet = (r.get("description") or "")[:500]
            tier, _tier_label = _classify_url_tier(url)
            kwargs: dict[str, Any] = {
                "provider": "brave",
                "title": title,
                "company": _deslugify_company(_extract_company_from_url(url)),
                "location": None,
                "remote": _is_remote(title, snippet) or None,
                "apply_url": url,
                "snippet": snippet,
                "posted_at": r.get("page_age") or r.get("age"),
                "source_tier": tier,
            }
            _run_extraction(kwargs, text=f"{title} {snippet}")
            results.append(SearchResult(**kwargs))
    return results


# ── JSearch / RapidAPI (BYOK, new credential: service="search",
# provider="jsearch") -- confirmed live (live-search-track.md) the
# remote-filter param may have been renamed remote_jobs_only ->
# work_from_home since n8n's own port; rather than risk sending a wrong/
# deprecated param name, this deliberately sends NO remote-filter param at
# all and relies on the response's own job_is_remote field instead --
# verify the request-side param name directly before adding it later. ─────

_JSEARCH_URL = "https://jsearch.p.rapidapi.com/search-v2"


async def fetch_jsearch(
    http: httpx.AsyncClient,
    *,
    query: str,
    api_key: str,
    location: str | None = None,
    country: str = "us",
) -> list[SearchResult]:
    search_text = f"{query} in {location}" if location else query
    try:
        response = await http.get(
            _JSEARCH_URL,
            headers={"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"},
            params={
                "query": search_text,
                "num_pages": "1",
                "country": country,
                "date_posted": "week",
            },
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE", "Couldn't reach JSearch. Try again in a moment.", retryable=True
        ) from e
    if response.status_code >= 400:
        raise ApiError(
            "PROVIDER_UNAVAILABLE", "JSearch couldn't complete that search.", retryable=True
        )

    body = response.json()
    jobs = (body.get("data") or {}).get("jobs") or []

    results = []
    for j in jobs:
        title = j.get("job_title") or ""
        if not title:
            continue
        url = j.get("job_apply_link") or ""
        for option in j.get("apply_options") or []:
            if option.get("is_direct") and option.get("apply_link"):
                url = option["apply_link"]
                break
        if not url:
            continue
        location_text = ", ".join(
            p for p in (j.get("job_city"), j.get("job_state"), j.get("job_country")) if p
        )
        description = str(j.get("job_description") or "")
        snippet = _strip_html(description, max_length=2000)
        kwargs: dict[str, Any] = {
            "provider": "jsearch",
            "title": title,
            "company": j.get("employer_name") or None,
            "location": location_text or None,
            "remote": j.get("job_is_remote"),
            "apply_url": url,
            "snippet": snippet,
            "posted_at": j.get("job_posted_at_datetime_utc"),
            "salary_min": j.get("job_min_salary"),
            "salary_max": j.get("job_max_salary"),
            "salary_currency": j.get("job_salary_currency"),
            "source_tier": 2.0,
        }
        kwargs["sponsorship_signal"] = extract_sponsorship(f"{title} {snippet}")
        results.append(SearchResult(**kwargs))
    return results


# ── Fan-out orchestration ──────────────────────────────────────────────────


@dataclass(frozen=True)
class ProviderCredentials:
    """`None` for a BYOK provider means "skip it, the user hasn't
    configured a key," matching try_get_secret's own degrade-not-fail
    contract -- this module never resolves credentials itself, that's
    the caller's job (mirroring research_clients.py's own
    `api_key: str` parameter shape). `adzuna_app_id`/`adzuna_app_key`
    both come from `try_get_secret_pair` (Job Finder P4c's 2-value
    credential) -- either both are set or neither is, never partial."""

    you_com_key: str | None = None
    firecrawl_key: str | None = None
    usajobs_key: str | None = None
    usajobs_email: str | None = None
    serper_key: str | None = None
    brave_key: str | None = None
    jsearch_key: str | None = None
    adzuna_app_id: str | None = None
    adzuna_app_key: str | None = None


_NO_CREDENTIALS = ProviderCredentials()


async def search_jobs(
    http: httpx.AsyncClient,
    *,
    query: str,
    location: str | None = None,
    companies: list[str] | None = None,
    remote_only: bool = False,
    credentials: ProviderCredentials = _NO_CREDENTIALS,
) -> tuple[list[SearchResult], list[str]]:
    """Fans out across every implemented provider concurrently. RemoteOK/
    Arbeitnow always run (no credential needed); every BYOK provider runs
    only when its key is present. One provider's failure becomes a
    warning string, never aborts the batch -- mirrors company_intel_
    pipeline.run_research's own fail-open shape, applied uniformly (see
    module docstring re: the You.com bug this fixes by construction
    rather than porting)."""
    import asyncio

    calls: list[tuple[str, Any]] = [
        ("remoteok", fetch_remoteok(http, query=query)),
        ("arbeitnow", fetch_arbeitnow(http, query=query)),
    ]
    if credentials.you_com_key:
        calls.append(
            (
                "you_com",
                fetch_you_com(
                    http,
                    query=query,
                    api_key=credentials.you_com_key,
                    location=location,
                    companies=companies,
                ),
            )
        )
    if credentials.firecrawl_key:
        calls.append(
            (
                "firecrawl",
                fetch_firecrawl(
                    http,
                    query=query,
                    api_key=credentials.firecrawl_key,
                    location=location,
                    companies=companies,
                    remote_only=remote_only,
                ),
            )
        )
    if credentials.usajobs_key and credentials.usajobs_email:
        calls.append(
            (
                "usajobs",
                fetch_usajobs(
                    http,
                    query=query,
                    api_key=credentials.usajobs_key,
                    email=credentials.usajobs_email,
                    location=location,
                ),
            )
        )
    if credentials.serper_key:
        calls.append(
            (
                "serper",
                fetch_serper(
                    http,
                    query=query,
                    api_key=credentials.serper_key,
                    location=location,
                    companies=companies,
                ),
            )
        )
    if credentials.brave_key:
        calls.append(
            (
                "brave",
                fetch_brave(
                    http,
                    query=query,
                    api_key=credentials.brave_key,
                    location=location,
                    companies=companies,
                ),
            )
        )
    if credentials.jsearch_key:
        calls.append(
            (
                "jsearch",
                fetch_jsearch(
                    http, query=query, api_key=credentials.jsearch_key, location=location
                ),
            )
        )
    if credentials.adzuna_app_id and credentials.adzuna_app_key:
        calls.append(
            (
                "adzuna",
                fetch_adzuna(
                    http,
                    query=query,
                    app_id=credentials.adzuna_app_id,
                    app_key=credentials.adzuna_app_key,
                    location=location,
                ),
            )
        )

    outcomes = await asyncio.gather(*(c for _, c in calls), return_exceptions=True)

    results: list[SearchResult] = []
    warnings: list[str] = []
    for (provider, _), outcome in zip(calls, outcomes, strict=True):
        if isinstance(outcome, ApiError):
            warnings.append(f"{provider}: {outcome.message}")
        elif isinstance(outcome, BaseException):
            warnings.append(f"{provider}: unexpected failure")
        else:
            results.extend(outcome)
    return results, warnings
