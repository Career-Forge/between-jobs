"""ATS adapters for the Job Finder registry poller (P2/P3a, job-finder-port.md).

Greenhouse/Lever/Ashby/Workday (P2), faithfully ported from n8n's own
`CareerForge_ATS_Poller.json` (`Build Requests`/`Parse Jobs` nodes, read
directly) -- these four cover 99.1% of the real imported registry
(15,822 of 15,964 companies). All four are unauthenticated public APIs.

P3a adds the 6 "mechanical" long-tail adapters (SmartRecruiters,
Workable, Recruitee, Amazon, Apple, D.E. Shaw) -- 76 more companies.
Unlike P2, these were NOT ported from n8n's source as-is: each was
independently re-verified live (real HTTP fetches against real seeded
companies, plus each platform's own current public docs where they
exist) before writing a single line, because n8n's reference source
turned out to have drifted from several of these APIs' real current
shape. Real, confirmed deltas the port fixes rather than reproduces:
  - SmartRecruiters: n8n hardcoded a single page (`limit=100&offset=0`,
    never advanced) -- confirmed live that real companies (ServiceNow
    556, AbbVie 1,687 postings) have far more than 100, so this port
    paginates for real via `offset`/`totalFound`.
  - Workable: n8n's claimed remote signal (`workplace`/
    `location.location`) doesn't exist in the live payload at all --
    the real signal is a boolean `telecommuting` field.
  - Recruitee: n8n's claimed `url`/`careers_url` fallback is wrong --
    there is no `url` field; only `careers_url` exists. A literal port
    would KeyError-equivalent to an empty apply_url for every posting.
  - Amazon: n8n's claim of "no date field" is stale -- `posted_date`
    now exists. Also paginates for real (confirmed live: `offset=100`
    returns entirely distinct postings from `offset=0`), where n8n
    fetched only the first 100.
  - Apple: n8n's assumed flat response shape is wrong -- the real body
    is `{"res": {"searchResults": [...], ...}}`, one level deeper. The
    real numeric-ish id is `positionId`, not `id` (a `"PIPE-..."`
    string n8n's own code never distinguished).
  - D.E. Shaw: n8n's assumed `regularJobs[i]`/`internships[i]` shape is
    wrong -- every real entry is nested one level deeper, under
    `regularJobs[i].data`. A literal port silently reads `undefined`
    for every field.

P3b adds Oracle Fusion HCM ("Oracle Recruiting Cloud") -- 39 real
companies (Ford, JPMorgan Chase, Goldman Sachs, Marriott, and 35
others), each an independent Oracle Fusion customer on their own pod
host (`api_base`), not Oracle Corp itself. n8n's own code comment
claimed its `apply_url` construction (`https://{api_base}/hcmUI/
CandidateExperience/en/sites/{slug}/job/{Id}`) only worked because
"Oracle itself is the only seeded oracle-type row" -- live-verified
(job-finder-port.md P3b research) directly against 6 real, unrelated
customers (Akamai, Ford, Goldman Sachs, JPMorgan Chase, Honeywell,
Texas Instruments): all 6 resolved to real, correctly-branded,
job-specific candidate pages. The old comment's caution was unfounded;
the pod-host construction generalizes and needs no per-customer lookup.
Also confirmed live and real, unlike n8n's own single-hardcoded-page
fetch: `limit`/`offset` pagination genuinely works (disjoint pages,
correct `POSTING_DATES_DESC` ordering) -- real companies range from 235
(Akamai) to 7,181 (JPMorgan Chase) postings, so this port paginates for
real rather than reproducing the single-page gap. `WorkplaceType`/
`ShortDescriptionStr` are legitimately empty for some real customers
(Goldman Sachs, JPMorgan Chase, Texas Instruments) -- confirmed via live
sampling across all 6 test companies, not a broken field, just a
per-customer data-entry choice; the adapter treats absence as absence,
never guesses a value.

P3c adds Eightfold -- 19 real companies (Bayer, PayPal, Starbucks,
Microsoft's own real tenant, and 15 others), the most structurally
involved adapter so far: a real, still-live tier-fallback bug (live-
verified directly, P3c research, against the 3 real tenants n8n's own
incident history named -- PayPal/Starbucks/Boston Scientific) and a
genuinely separate JD-backfill feature (`run_eightfold_jd_backfill` in
job_registry_poller.py), because Eightfold's own list endpoint NEVER
returns real description text on either tier (confirmed live: `""` on
100% of sampled positions, both tiers) -- worse than n8n's own "usually
short/absent" characterization.

Tier fallback: the primary ("smartapply") endpoint 403s outright for
some real tenants -- confirmed live today, not just historically --
while the same tenant's "pcsx" endpoint has the real data. Falls back
from smartapply to pcsx only on page 0 (a later page legitimately
running dry on an already-working tier must not re-trigger it), on
EITHER a 403 or a genuinely empty `positions` array (live evidence only
reproduced the 403 case today, but the OR is kept since the error
text -- confirmed live to be the same "Not authorized for PCSX" message
regardless of which tier actually rejected the request -- isn't a
reliable signal of which failure mode occurred).

The two tiers have genuinely different schemas confirmed live, not just
different envelopes: smartapply uses snake_case ids (`ats_job_id`/
`display_job_id`), an absolute `canonicalPositionUrl`, and Unix-seconds
`t_create`; pcsx uses camelCase ids (`atsJobId`/`displayJobId`), a
site-relative `positionUrl` needing absolutizing against `api_base`, and
`creationTs`/`postedTs` instead of `t_create` -- confirmed live that a
mapping which only ever looks for `t_create` would silently produce a
null `posted_at` for every pcsx-tier posting (which is exactly the 3
historically-affected tenants). A custom, non-`*.eightfold.ai` domain
(Netflix, `explore.jobs.netflix.net`) was confirmed live to need zero
special-casing -- the same path/params work unchanged.

The JD-backfill lane's detail endpoint was confirmed live to be
smartapply-shaped UNCONDITIONALLY, regardless of which tier a company's
list data came through (tested directly against PayPal/Starbucks
position ids sourced from their own pcsx listings) -- a real
simplification versus the list-fetch's own tier-awareness. It was also
confirmed live that the detail endpoint's own `work_location_option`
comes back null for every company tested, even when the list endpoint
had a real value for the same job -- the backfill therefore never
touches the `remote` field, only `jd_text`/`apply_url`/salary+
sponsorship extraction, matching n8n's own design.

P3d adds Avature (5 real companies: IBM, Bloomberg, Two Sigma, and
Deloitte under two separate tenants) and SuccessFactors (5: Cargill,
Vodafone, ExxonMobil, adidas, EY) -- both real HTML scraping, no JSON
API for either, and both re-verified live directly against all 10 real
tenants (raw HTML fetched and inspected byte-for-byte, not assumed)
before writing any code.

Avature genuinely needs 3 separate card templates, confirmed still
live and unambiguous across all 5 real tenants: `article--card` (IBM;
external_id/apply_url via a `JobDetail?jobId=` regex + reconstruction),
`article--result` with the base `article` class (Bloomberg, Two Sigma;
apply_url is the card's own href, used as-is), and bare `article--
result` with no base `article` class (both Deloitte tenants). Deloitte's
own real markup, fetched directly, confirmed its title anchor's `href`
is ALSO already a full absolute URL (same as the `article--result`
tier) -- so both non-card templates share identical external_id/
apply_url extraction, differing only in location: `article--result` has
a dedicated `list-item-location` CSS class, but Deloitte's fallback
template has no such class at all -- location is the LAST of several
plain, un-classed `<span>` tags inside the header subtitle (confirmed
directly: `Deloitte US | Deloitte Consulting LLP | Multiple Locations`,
positional, not selector-based).

The locale-prefix auto-retry (try `/en_US/{portal}/{listingPage}`
first; on a body containing the literal marker `LanguageManager::
redirectToUrl`, retry the bare path instead) is confirmed still live
and real: IBM and both Deloitte tenants work directly on the locale
path with no retry; Bloomberg and Two Sigma both genuinely need the
bare-path retry today. Two Sigma's custom domain (`careers.
twosigma.com`, not `*.avature.net`) and its non-default listing page
(`OpenRoles`, not `SearchJobs`) are both handled the same way n8n's own
source did: a dot in `slug` means the slug IS the full host, and
`api_base` doubles as `"portalPath[/listingPage]"` split on `/`.

Pagination for BOTH Avature and SuccessFactors advances by however many
cards/tiles a page ACTUALLY returned, never a fixed requested/assumed
page size -- confirmed live this isn't a single-tenant quirk for
either platform. Avature: every one of the 5 real tenants returned
fewer cards than the requested 50 per page (9, 12, 10, 10, 10) --
n8n's own historical "IBM renders ~9 regardless of what's requested"
framing undersold it; it's a real per-tenant Avature behavior, not an
IBM-specific bug. SuccessFactors: n8n's own source assumed a fixed
`PAGE_SIZE=25` platform-wide constant for its own stop-and-advance
logic -- confirmed live this is actually a PER-TENANT configuration
value (Cargill/Vodafone/ExxonMobil/EY are real at 25; adidas is real at
50), so a literal port advancing by a hardcoded 25 would have silently
re-fetched half of every page for adidas, the same pagination-drift
failure class already found and fixed for SmartRecruiters in P3a.
Advancing by the actual count returned sidesteps needing to know any
tenant's page size in advance, and stopping only on a genuinely empty
page (never on "fewer than N") avoids assuming a size that isn't
actually constant.

SuccessFactors' own field markup was also fetched directly (Cargill) to
confirm the exact real structure: title and apply_url both live in the
same anchor tag (`class="jobTitle-link"`, a real absolute-path `href`,
also duplicated as the enclosing tile's own `data-url` attribute), and
location lives in a value `<div>` whose `id` ends in `-location-value`
specifically -- confirmed important to anchor on, not a generic
`-value` suffix, since the same tile carries sibling `-department-
value` and other section fields with the identical suffix pattern that
must not be mismatched. Confirmed live and disclosed, not silently
dropped: 3 of the 5 real tenants (ExxonMobil, adidas, EY) render no
location field on the tile at all (a real per-tenant Career Site
Builder configuration choice, not a markup break) -- absence there
means an honestly empty location, never a guess. Neither platform ever
returns real JD text on the list page (no detail endpoint exists for
either, unlike Eightfold) -- `jd_text` stays permanently empty for
both, exactly as disclosed for Workday/SmartRecruiters/Oracle's own
list-only fields.

P3e adds Google -- a single real company (1,700+ real active postings,
confirmed live -- page 85 of 20/page still returned a full page),
closing out the Registry track. It's the only P2/P3 adapter that's a
genuine `PAGINATED_TYPE`: its real board is far larger than any single
15-minute tick can fully re-scan (confirmed live: `MAX_PAGES_PER_TICK`
below covers 120 postings/tick against a board multiple ticks deep), so
unlike every other adapter here, `fetch_google` alone cannot tell the
poller when it's safe to close stale postings -- that requires the
cross-tick sweep bookkeeping this project's own P1/P2 research
originally deferred (the mechanism behind n8n's own real "microsoft
lost all 85 postings this way" incident). `job_registry_poller.py`
owns that bookkeeping; this module's own job is narrower: fetch a
page range starting from wherever `company.etag` (repurposed as a page
cursor, exactly like n8n's own real design) says to resume, and report
whether this tick's fetch reached the true end of the board
(`hit_end`) -- it has no notion of "the sweep" as a multi-tick concept
at all, deliberately.

The real markup was fetched and inspected directly (not assumed):
confirmed still live today, byte-for-byte, all of n8n's claimed
selectors (`<li class="lLd3Je" ssk='N:ID'>` cards, `QJPWVe` title,
`r0wTof ` location, a `jobs/results/...` relative href needing joining
against the base careers-applications URL). Confirmed live: pagination
is real (`page=1` vs `page=2` return fully disjoint real postings),
`PAGE_SIZE=20` is exact, and a genuinely exhausted page (`page=200`)
returns zero cards cleanly. A single tick's own `ok` flag is set on
ANY valid parseable response, same as Apple/Workday/Eightfold, NOT
the stricter Avature/SuccessFactors "zero blocks this call = fail"
rule -- Google's own safety net against a broken-scraper-vs-genuinely-
done ambiguity lives one layer up, at the cumulative multi-tick sweep
level (see job_registry_poller.py), matching exactly where n8n's own
real fix (s151) put it, not at the single-page level.

D8 (Pranav, 2026-08-30) changed what gets stored: between-jobs keeps every
posting from a board, not just ones matching n8n's own AI/ML title regex
(that filter existed because n8n's bot served one person's own job
search; between-jobs is a general BYOK platform). Two things n8n did
specifically to bound title-filtered work therefore do NOT port here:
  - The per-board CAP (25/50 for dream tier) -- it existed to bound
    *embedding* cost per tick, and embedding population is already
    deferred (P1's migration). No cap: every fetch stores everything it
    returns.
  - Workday's "search top-up" (re-querying with AI/ML keywords when the
    first unfiltered page came up short) -- its entire purpose was
    finding buried *relevant* roles; without a title filter there's no
    "relevant subset" to hunt for. Disclosed consequence, not a silent
    drop: Workday here only ever fetches its first 5 pages (~100
    postings) per tick per board, so a very large Workday board (a few
    exist, e.g. one real board with 1,300+ postings) stays under-covered
    in this v1. Workday is 224 of 15,964 companies (1.4%) -- a small,
    known gap, revisit if it matters.

Not ported, disclosed: Greenhouse's `company_name` backfill (a data-
quality nicety -- fixing a company's display name from its own job
payload when it was seeded with just a slug) and the two ATS-specific
digest-time liveness re-verifiers (Ashby/Lever org-listing-membership
checks, Workday's canApply/S22 signature check) -- both live in n8n's
Master search-serving workflow, not its poller, and only matter once
between-jobs has a serving-time search path of its own (a later phase).
The registry's own liveness here is the poller's generic absence-based
closure (job_registry_poller.py), which is what P1/P2's schema and
scheduling exist to support.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

_HTTP_TIMEOUT_SECONDS = 20.0

_HTML_TAG_RX = re.compile(r"<[^>]+>")
_WHITESPACE_RX = re.compile(r"\s+")
_REMOTE_RX = re.compile(r"remote", re.IGNORECASE)
_WORKDAY_REMOTE_RX = re.compile(r"flex|remote", re.IGNORECASE)

# Order matters -- mirrors n8n's own chained dec() exactly (amp first, so a
# double-escaped "&amp;lt;" resolves to "&lt;" on the first pass and "<" on
# the second, via _strip_html's dec(dec(s)) call).
_HTML_ENTITIES = (
    ("&amp;", "&"),
    ("&lt;", "<"),
    ("&gt;", ">"),
    ("&quot;", '"'),
    ("&#39;", "'"),
    ("&#x27;", "'"),
)

_WORKDAY_PAGE_LIMIT = 20
_WORKDAY_MAX_PAGES = 5

_SMARTRECRUITERS_PAGE_SIZE = 100
_SMARTRECRUITERS_MAX_PAGES = 20  # 2,000 postings/tick ceiling -- real companies seen up to 1,687

_AMAZON_PAGE_SIZE = 100  # confirmed live: result_limit>100 is rejected outright
_AMAZON_MAX_PAGES = 20  # a bound on tick work, not amazon.jobs' own real ceiling (~10,000 hits)

_APPLE_PAGE_SIZE = 20  # confirmed live: exactly 20 results per page, not the 100 n8n assumed
_APPLE_MAX_PAGES = 10

# n8n's own value; Oracle's REST finder docs don't confirm a higher safe limit
_ORACLE_PAGE_SIZE = 20
# 600 postings/tick ceiling -- real companies confirmed up to 7,181 (JPMorgan
# Chase) -- a large board stays under-covered in one tick, same disclosed-
# limitation class as Workday's own per-tick page budget.
_ORACLE_MAX_PAGES = 30

_EIGHTFOLD_PAGE_SIZE = 10  # confirmed live: a hard API ceiling, num>10 is still capped at 10
# real companies confirmed up to 21,993 (Starbucks) -- disclosed per-tick
# ceiling, same class as Oracle's own.
_EIGHTFOLD_MAX_PAGES = 30
_EIGHTFOLD_POSITION_ID_RX = re.compile(r"/careers/job/(\d+)")

_AVATURE_PAGE_SIZE = 50  # the REQUESTED size; confirmed live every real tenant renders fewer
_AVATURE_MAX_PAGES = 10
_AVATURE_REDIRECT_MARKER = "LanguageManager::redirectToUrl"
_AVATURE_CARD_MARKER = '<article class="article article--card'
_AVATURE_RESULT_MARKER = '<article class="article article--result'
_AVATURE_BARE_RESULT_MARKER = '<article class="article--result'
_AVATURE_TITLE_ANCHOR_RX = re.compile(
    r'article__header__text__title[^>]*>\s*<a\s+href="([^"]*)"[^>]*>(.*?)</a>', re.DOTALL
)
_AVATURE_CARD_JOB_ID_RX = re.compile(r"JobDetail\?jobId=(\d+)")
_AVATURE_CARD_LOCATION_RX = re.compile(r'card-item-location">([^<]*)</span>')
_AVATURE_RESULT_LOCATION_RX = re.compile(r'list-item-location">([^<]*)</span>')
_AVATURE_TRAILING_ID_RX = re.compile(r"/(\d+)(?:[?#]|$)")
_AVATURE_PLAIN_SPAN_RX = re.compile(r"<span>([^<]*)</span>")

_SUCCESSFACTORS_MAX_PAGES = 8
_SUCCESSFACTORS_TILE_MARKER = '<li class="job-tile job-id-'
_SUCCESSFACTORS_EXTERNAL_ID_RX = re.compile(r"^(\d+)")
_SUCCESSFACTORS_TITLE_RX = re.compile(r"jobTitle-link[^>]*>\s*([^<]*?)\s*<", re.DOTALL)
# Anchored on "-location-value" specifically, not a generic "-value"
# suffix -- confirmed live (P3d research) the same tile carries sibling
# "-department-value" and other section fields sharing that suffix.
_SUCCESSFACTORS_LOCATION_RX = re.compile(r'-location-value">\s*([^<]*?)\s*</div>', re.DOTALL)
_SUCCESSFACTORS_DATA_URL_RX = re.compile(r'data-url="([^"]+)"')

# confirmed live: the real board is 85+ pages deep, far too many to
# finish in one tick -- see the P3e sweep-bookkeeping split, job_registry_
# poller.py's own module docstring and the sweep-bookkeeping migration.
_GOOGLE_PAGE_SIZE = 20
_GOOGLE_MAX_PAGES_PER_TICK = 6
_GOOGLE_BASE_URL = "https://www.google.com/about/careers/applications/"
_GOOGLE_CARD_MARKER = '<li class="lLd3Je"'
_GOOGLE_SSK_RX = re.compile(r"ssk='(?:\d+:)?(\d+)'")
_GOOGLE_TITLE_RX = re.compile(r'<h3 class="QJPWVe">([^<]+)</h3>')
_GOOGLE_LOCATION_RX = re.compile(r'class="r0wTof ">([^<]+)<')
_GOOGLE_HREF_RX = re.compile(r'href="(jobs/results/[^"?]+)')

_SLUG_INVALID_RX = re.compile(r"[^a-z0-9]+")
_MONTH_NAME_DATE_FORMAT = "%B %d, %Y"  # Amazon's real posted_date shape, e.g. "August 3, 2026"
_DESHAW_NEXT_DATA_RX = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)

AdapterStatus = Literal["ok", "partial", "not_modified", "gone", "failed"]


@dataclass(frozen=True)
class DueCompany:
    company_id: str
    name: str
    ats_type: str
    slug: str
    api_base: str
    board: str
    etag: str
    # Only meaningful for a PAGINATED_TYPE (currently just Google) --
    # None/0 for every other ats_type, carried on every row regardless so
    # the poller's own due-companies query stays a single SELECT rather
    # than a second query gated on ats_type.
    sweep_started_at: str | None = None
    sweep_posting_count: int = 0


@dataclass(frozen=True)
class ParsedPosting:
    company_id: str
    board: str
    external_id: str
    title: str
    jd_text: str
    location: str
    remote: bool | None
    apply_url: str
    posted_at: str | None


@dataclass(frozen=True)
class AdapterResult:
    status: AdapterStatus
    postings: list[ParsedPosting] = field(default_factory=list)
    new_etag: str | None = None
    # True for every adapter except fetch_google: a single-response (or,
    # for Workday/Oracle/etc, single-tick-bounded-pagination) fetch is
    # inherently "the end" of what this tick will ever see. Only Google's
    # own cross-tick sweep can have more real pages waiting beyond this
    # tick's own page budget -- job_registry_poller.py is what actually
    # acts on this, not this module.
    hit_end: bool = True


@dataclass(frozen=True)
class EightfoldDetail:
    """The JD-backfill lane's own fetch result -- a partial update, not a
    full ParsedPosting, since it only ever refreshes jd_text/apply_url on
    an already-stored row (never remote -- confirmed live unreliable on
    this endpoint, see job_registry_poller.run_eightfold_jd_backfill)."""

    jd_text: str
    apply_url: str


def _decode_entities_once(s: str) -> str:
    for entity, char in _HTML_ENTITIES:
        s = s.replace(entity, char)
    return s


def _strip_html(s: str | None) -> str:
    text = _decode_entities_once(_decode_entities_once(s or ""))
    text = _HTML_TAG_RX.sub(" ", text)
    return _WHITESPACE_RX.sub(" ", text).strip()


def _is_remote(s: str | None) -> bool:
    return bool(_REMOTE_RX.search(s or ""))


def _iso(v: str | int | float | None) -> str | None:
    """Mirrors n8n's own `iso()`, which just wraps `new Date(v)` -- v is a
    string for Greenhouse (updated_at/first_published) but Lever's own
    `createdAt` is a real Unix epoch-milliseconds number, which JS's Date
    constructor accepts natively. Caught by a real test using an actual
    Lever-shaped timestamp, not a string -- the first draft only handled
    strings and raised on a real payload shape."""
    if v is None or v == "":
        return None
    if isinstance(v, int | float):
        try:
            return datetime.fromtimestamp(v / 1000, tz=UTC).isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    normalized = v[:-1] + "+00:00" if v.endswith("Z") else v
    try:
        return datetime.fromisoformat(normalized).isoformat()
    except ValueError:
        pass
    try:
        # Amazon's real posted_date field (confirmed live, P3a research):
        # a human-readable "August 3, 2026" string, not ISO8601.
        return datetime.strptime(v, _MONTH_NAME_DATE_FORMAT).isoformat()
    except ValueError:
        return None


def _iso_from_unix_seconds(v: Any) -> str | None:
    """Eightfold's smartapply tier gives Unix SECONDS (`t_create`,
    confirmed live e.g. 1787788800 -> 2026-08-27), not milliseconds like
    `_iso()`'s own numeric branch assumes for Lever -- a separate helper
    rather than overloading `_iso` with an ambiguous units guess."""
    if v is None or v == "":
        return None
    try:
        seconds = float(v)
    except (TypeError, ValueError):
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _join_nonempty(parts: list[str | None], sep: str = ", ") -> str:
    return sep.join(p for p in parts if p)


def _slugify(title: str) -> str:
    """Best-effort cosmetic slug for Apple's apply_url -- the trailing
    path segment after positionId is for display/SEO, not routing, so
    exact byte-parity with Apple's own generator isn't required."""
    slug = _SLUG_INVALID_RX.sub("-", title.lower()).strip("-")
    return slug or "job"


def _amazon_location_is_remote(locations: list[Any] | None) -> bool:
    """Amazon's `locations` field is a list of STRINGIFIED JSON objects
    (confirmed live), each carrying a `type` field -- `"VIRTUAL"` is the
    real remote signal, not a plain string to regex."""
    for entry in locations or []:
        try:
            parsed = json.loads(entry) if isinstance(entry, str) else entry
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict) and parsed.get("type") == "VIRTUAL":
            return True
    return False


def _failed() -> AdapterResult:
    return AdapterResult(status="failed")


def _partial(postings: list[ParsedPosting], new_etag: str | None = None) -> AdapterResult:
    """A mid-pagination failure (page > 0 network error or bad response)
    that still collected real data from at least one earlier page --
    returned instead of "ok" so job_registry_poller.run_poll_tick knows
    this fetch is NOT a complete listing (it skips closing stale
    postings for this board this tick) while still upserting whatever
    WAS collected, so a later page's failure doesn't throw away good
    data from earlier pages. Never returned when page 0 itself fails --
    that stays a full _failed(), unchanged. Distinct from Google's own
    hit_end/sweep-bookkeeping mechanism (job_registry_poller.py's
    _PAGINATED_ATS_TYPES branch) -- that's for a cross-tick sweep, this
    is for a fetch that was meant to complete within one tick and
    didn't."""
    return AdapterResult(status="partial", postings=postings, new_etag=new_etag)


async def _get_with_conditional_etag(
    http: httpx.AsyncClient, url: str, etag: str
) -> httpx.Response | None:
    headers = {"If-None-Match": etag} if etag else {}
    try:
        return await http.get(url, headers=headers, timeout=_HTTP_TIMEOUT_SECONDS)
    except httpx.HTTPError:
        return None


def _classify_json_response(
    response: httpx.Response,
) -> tuple[AdapterStatus, dict[str, Any] | list[Any] | None]:
    """Classifies the response and, only on "ok", also returns the parsed
    JSON body (dict or list) -- avoids parsing the body twice and avoids a
    caller having to narrow a status-or-body union."""
    if response.status_code == 304:
        return "not_modified", None
    if response.status_code in (404, 410):
        return "gone", None
    if not (200 <= response.status_code < 300):
        return "failed", None
    try:
        body = response.json()
    except ValueError:
        return "failed", None
    if isinstance(body, dict) and body.get("error"):
        return "failed", None
    if not isinstance(body, dict | list):
        return "failed", None
    return "ok", body


async def fetch_greenhouse(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
    url = f"https://boards-api.greenhouse.io/v1/boards/{company.slug}/jobs?content=true"
    response = await _get_with_conditional_etag(http, url, company.etag)
    if response is None:
        return _failed()
    status, body = _classify_json_response(response)
    if status != "ok":
        return AdapterResult(status=status)
    if not isinstance(body, dict):
        return _failed()

    postings = []
    for j in body.get("jobs") or []:
        location = ((j.get("location") or {}).get("name")) or ""
        postings.append(
            ParsedPosting(
                company_id=company.company_id,
                board=company.board,
                external_id=str(j.get("id")),
                title=j.get("title") or "",
                jd_text=_strip_html(j.get("content")),
                location=location,
                remote=_is_remote(location),
                apply_url=j.get("absolute_url") or "",
                posted_at=_iso(j.get("updated_at") or j.get("first_published")),
            )
        )
    return AdapterResult(status="ok", postings=postings, new_etag=response.headers.get("etag"))


async def fetch_lever(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
    url = f"https://api.lever.co/v0/postings/{company.slug}?mode=json"
    response = await _get_with_conditional_etag(http, url, company.etag)
    if response is None:
        return _failed()
    status, body = _classify_json_response(response)
    if status != "ok":
        return AdapterResult(status=status)

    if isinstance(body, list):
        arr = body
    elif isinstance(body, dict):
        arr = body.get("data") or []
    else:
        arr = []
    postings = []
    for j in arr:
        categories = j.get("categories") or {}
        location = categories.get("location") or ""
        workplace_type = j.get("workplaceType") or ""
        postings.append(
            ParsedPosting(
                company_id=company.company_id,
                board=company.board,
                external_id=str(j.get("id")),
                title=j.get("text") or "",
                jd_text=j.get("descriptionPlain") or _strip_html(j.get("description")),
                location=location,
                remote=_is_remote(f"{location} {workplace_type}"),
                apply_url=j.get("hostedUrl") or j.get("applyUrl") or "",
                posted_at=_iso(j.get("createdAt")),
            )
        )
    return AdapterResult(status="ok", postings=postings, new_etag=response.headers.get("etag"))


async def fetch_ashby(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{company.slug}?includeCompensation=true"
    response = await _get_with_conditional_etag(http, url, company.etag)
    if response is None:
        return _failed()
    status, body = _classify_json_response(response)
    if status != "ok":
        return AdapterResult(status=status)
    if not isinstance(body, dict):
        return _failed()

    postings = []
    for j in body.get("jobs") or []:
        if j.get("isListed") is False:
            continue
        postings.append(
            ParsedPosting(
                company_id=company.company_id,
                board=company.board,
                external_id=str(j.get("id")),
                title=j.get("title") or "",
                jd_text=j.get("descriptionPlain") or _strip_html(j.get("descriptionHtml")),
                location=j.get("location") or "",
                remote=bool(j.get("isRemote")),
                apply_url=j.get("jobUrl") or j.get("applyUrl") or "",
                posted_at=_iso(j.get("publishedAt")),
            )
        )
    return AdapterResult(status="ok", postings=postings, new_etag=response.headers.get("etag"))


async def fetch_workday(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
    api_base = company.api_base or ""
    tenant, dot, wd_n = api_base.partition(".")
    if not dot or not tenant or not wd_n or not company.slug:
        return _failed()

    host = f"{tenant}.{wd_n}.myworkdayjobs.com"
    url = f"https://{host}/wday/cxs/{tenant}/{company.slug}/jobs"
    postings: list[ParsedPosting] = []
    ok = False
    partial_failure = False
    cached_total: int | None = None

    for page in range(_WORKDAY_MAX_PAGES):
        offset = page * _WORKDAY_PAGE_LIMIT
        payload = {
            "appliedFacets": {},
            "limit": _WORKDAY_PAGE_LIMIT,
            "offset": offset,
            "searchText": "",
        }
        try:
            response = await http.post(url, json=payload, timeout=_HTTP_TIMEOUT_SECONDS)
        except httpx.HTTPError:
            partial_failure = ok
            break
        if response.status_code != 200:
            partial_failure = ok
            break
        try:
            data = response.json()
        except ValueError:
            partial_failure = ok
            break
        ok = True
        if cached_total is None:
            cached_total = data.get("total") or 0
        job_postings = data.get("jobPostings") or []
        if not job_postings:
            break
        for j in job_postings:
            external_path = j.get("externalPath") or ""
            postings.append(
                ParsedPosting(
                    company_id=company.company_id,
                    board=company.board,
                    external_id=str(external_path),
                    title=j.get("title") or "",
                    jd_text="",
                    location=j.get("locationsText") or "",
                    remote=bool(_WORKDAY_REMOTE_RX.search(j.get("remoteType") or "")),
                    apply_url=f"https://{host}/{company.slug}{external_path}",
                    posted_at=None,
                )
            )
        if offset + _WORKDAY_PAGE_LIMIT >= cached_total:
            break

    if not ok:
        return _failed()
    if partial_failure:
        return _partial(postings)
    return AdapterResult(status="ok", postings=postings, new_etag=None)


async def fetch_smartrecruiters(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
    postings: list[ParsedPosting] = []
    new_etag: str | None = None
    offset = 0
    for page in range(_SMARTRECRUITERS_MAX_PAGES):
        url = (
            f"https://api.smartrecruiters.com/v1/companies/{company.slug}/postings"
            f"?limit={_SMARTRECRUITERS_PAGE_SIZE}&offset={offset}"
        )
        etag = company.etag if page == 0 else ""
        response = await _get_with_conditional_etag(http, url, etag)
        if response is None:
            if page == 0:
                return _failed()
            return _partial(postings, new_etag)
        status, body = _classify_json_response(response)
        if page == 0:
            if status != "ok":
                return AdapterResult(status=status)
            new_etag = response.headers.get("etag")
        elif status != "ok":
            return _partial(postings, new_etag)
        if not isinstance(body, dict):
            if page == 0:
                break
            return _partial(postings, new_etag)

        content = body.get("content") or []
        for j in content:
            posting_id = j.get("id")
            if posting_id is None or posting_id == "":
                # No usable id -- external_id would collapse to "", which
                # would collide with every other id-less posting on this
                # board under the (board, external_id) upsert key. Not a
                # usable candidate; skip rather than emit a .../None URL.
                continue
            loc = j.get("location") or {}
            location = loc.get("fullLocation") or _join_nonempty(
                [loc.get("city"), loc.get("region"), loc.get("country")]
            )
            postings.append(
                ParsedPosting(
                    company_id=company.company_id,
                    board=company.board,
                    external_id=str(posting_id),
                    title=j.get("name") or "",
                    jd_text="",
                    location=location,
                    remote=bool(loc.get("remote")),
                    apply_url=f"https://jobs.smartrecruiters.com/{company.slug}/{posting_id}",
                    posted_at=_iso(j.get("releasedDate")),
                )
            )
        total_found = body.get("totalFound") or 0
        offset += _SMARTRECRUITERS_PAGE_SIZE
        if not content or offset >= total_found:
            break
    return AdapterResult(status="ok", postings=postings, new_etag=new_etag)


async def fetch_workable(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
    url = f"https://apply.workable.com/api/v1/widget/accounts/{company.slug}?details=true"
    response = await _get_with_conditional_etag(http, url, company.etag)
    if response is None:
        return _failed()
    status, body = _classify_json_response(response)
    if status != "ok":
        return AdapterResult(status=status)
    if not isinstance(body, dict):
        return _failed()

    postings = []
    for j in body.get("jobs") or []:
        location = _join_nonempty([j.get("city"), j.get("state"), j.get("country")])
        postings.append(
            ParsedPosting(
                company_id=company.company_id,
                board=company.board,
                external_id=str(j.get("shortcode") or ""),
                title=j.get("title") or "",
                jd_text=_strip_html(j.get("description")),
                location=location,
                remote=bool(j.get("telecommuting")),
                apply_url=j.get("url") or j.get("application_url") or "",
                posted_at=_iso(j.get("published_on")),
            )
        )
    return AdapterResult(status="ok", postings=postings, new_etag=response.headers.get("etag"))


async def fetch_recruitee(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
    url = f"https://{company.slug}.recruitee.com/api/offers"
    response = await _get_with_conditional_etag(http, url, company.etag)
    if response is None:
        return _failed()
    status, body = _classify_json_response(response)
    if status != "ok":
        return AdapterResult(status=status)
    if not isinstance(body, dict):
        return _failed()

    postings = []
    for j in body.get("offers") or []:
        if j.get("status") and j.get("status") != "published":
            continue
        location = j.get("location") or _join_nonempty([j.get("city"), j.get("country")])
        jd_text = _strip_html(f"{j.get('description') or ''} {j.get('requirements') or ''}")
        postings.append(
            ParsedPosting(
                company_id=company.company_id,
                board=company.board,
                external_id=str(j.get("id") or ""),
                title=j.get("title") or "",
                jd_text=jd_text,
                location=location,
                remote=_is_remote(location),
                apply_url=j.get("careers_url") or "",
                posted_at=_iso(j.get("published_at")),
            )
        )
    return AdapterResult(status="ok", postings=postings, new_etag=response.headers.get("etag"))


async def fetch_amazon(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
    postings: list[ParsedPosting] = []
    new_etag: str | None = None
    offset = 0
    for page in range(_AMAZON_MAX_PAGES):
        url = f"https://www.amazon.jobs/en/search.json?offset={offset}&result_limit={_AMAZON_PAGE_SIZE}"
        etag = company.etag if page == 0 else ""
        response = await _get_with_conditional_etag(http, url, etag)
        if response is None:
            if page == 0:
                return _failed()
            return _partial(postings, new_etag)
        status, body = _classify_json_response(response)
        if page == 0:
            if status != "ok":
                return AdapterResult(status=status)
            new_etag = response.headers.get("etag")
        elif status != "ok":
            return _partial(postings, new_etag)
        if not isinstance(body, dict):
            if page == 0:
                break
            return _partial(postings, new_etag)

        jobs = body.get("jobs") or []
        if not jobs:
            break
        for j in jobs:
            job_path = j.get("job_path") or ""
            postings.append(
                ParsedPosting(
                    company_id=company.company_id,
                    board=company.board,
                    external_id=str(j.get("id_icims") or j.get("id") or ""),
                    title=j.get("title") or "",
                    jd_text=_strip_html(j.get("description")),
                    location=j.get("location") or "",
                    remote=_amazon_location_is_remote(j.get("locations")),
                    apply_url=(
                        j.get("url_next_step")
                        or (f"https://www.amazon.jobs{job_path}" if job_path else "")
                    ),
                    posted_at=_iso(j.get("posted_date")),
                )
            )
        offset += _AMAZON_PAGE_SIZE
        if len(jobs) < _AMAZON_PAGE_SIZE:
            break
    return AdapterResult(status="ok", postings=postings, new_etag=new_etag)


async def fetch_apple(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
    postings: list[ParsedPosting] = []
    ok = False
    partial_failure = False
    for page in range(1, _APPLE_MAX_PAGES + 1):
        payload = {
            "query": "",
            "locale": "en-us",
            "sort": "newest",
            "filters": {},
            "page": page,
            "format": "json",
        }
        try:
            response = await http.post(
                "https://jobs.apple.com/api/v1/search", json=payload, timeout=_HTTP_TIMEOUT_SECONDS
            )
        except httpx.HTTPError:
            partial_failure = ok
            break
        if response.status_code != 200:
            partial_failure = ok
            break
        try:
            body = response.json()
        except ValueError:
            partial_failure = ok
            break
        res = body.get("res") if isinstance(body, dict) else None
        if not isinstance(res, dict):
            partial_failure = ok
            break
        ok = True
        results = res.get("searchResults") or []
        if not results:
            break
        for j in results:
            locations = j.get("locations") or []
            location = _join_nonempty(
                [
                    _join_nonempty(
                        [loc.get("city"), loc.get("stateProvince"), loc.get("countryName")]
                    )
                    for loc in locations
                ],
                sep=" | ",
            )
            position_id = j.get("positionId") or j.get("id") or ""
            title = j.get("postingTitle") or ""
            postings.append(
                ParsedPosting(
                    company_id=company.company_id,
                    board=company.board,
                    external_id=str(position_id),
                    title=title,
                    jd_text=_strip_html(j.get("jobSummary")),
                    location=location,
                    remote=bool(j.get("homeOffice")),
                    apply_url=f"https://jobs.apple.com/en-us/details/{position_id}/{_slugify(title)}",
                    posted_at=_iso(j.get("postingDate") or j.get("postDateInGMT")),
                )
            )
        if len(results) < _APPLE_PAGE_SIZE:
            break

    if not ok:
        return _failed()
    if partial_failure:
        return _partial(postings)
    return AdapterResult(status="ok", postings=postings, new_etag=None)


async def fetch_deshaw(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
    try:
        response = await http.get("https://www.deshaw.com/careers", timeout=_HTTP_TIMEOUT_SECONDS)
    except httpx.HTTPError:
        return _failed()
    if response.status_code != 200:
        return _failed()
    match = _DESHAW_NEXT_DATA_RX.search(response.text)
    if not match:
        return _failed()
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return _failed()

    page_props = ((data.get("props") or {}).get("pageProps")) or {}
    entries = [*(page_props.get("regularJobs") or []), *(page_props.get("internships") or [])]
    postings = []
    for entry in entries:
        j = entry.get("data") or {}
        if not j:
            continue
        locations = ((j.get("jobMetadata") or {}).get("jobLocations")) or []
        location = _join_nonempty([loc.get("name") for loc in locations], sep=" | ")
        job_url = (j.get("jobUrl") or "").lower()
        postings.append(
            ParsedPosting(
                company_id=company.company_id,
                board=company.board,
                external_id=str(j.get("id") or ""),
                title=j.get("displayName") or "",
                jd_text=_strip_html((j.get("jobDescription") or {}).get("websiteDescription")),
                location=location,
                remote=_is_remote(location),
                apply_url=f"https://www.deshaw.com/careers/{job_url}",
                posted_at=None,
            )
        )
    return AdapterResult(status="ok", postings=postings, new_etag=None)


async def fetch_oracle(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
    postings: list[ParsedPosting] = []
    ok = False
    partial_failure = False
    offset = 0
    total: int | None = None
    for _page in range(_ORACLE_MAX_PAGES):
        url = (
            f"https://{company.api_base}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
            f"?onlyData=true&expand=requisitionList&finder=findReqs;siteNumber={company.slug},"
            f"limit={_ORACLE_PAGE_SIZE},offset={offset},sortBy=POSTING_DATES_DESC"
        )
        try:
            response = await http.get(url, timeout=_HTTP_TIMEOUT_SECONDS)
        except httpx.HTTPError:
            partial_failure = ok
            break
        if response.status_code != 200:
            partial_failure = ok
            break
        try:
            body = response.json()
        except ValueError:
            partial_failure = ok
            break
        items = body.get("items") if isinstance(body, dict) else None
        if not items:
            # Ambiguous empty vs. malformed, same class already accepted
            # for Avature/SuccessFactors' own documented "silent-empty
            # trap" reasoning -- not treated as a transport/parse failure.
            break
        first_item = items[0] or {}
        req_list = first_item.get("requisitionList") or []
        if not req_list:
            break
        ok = True
        for j in req_list:
            posting_id = j.get("Id")
            if posting_id is None or posting_id == "":
                # No usable id -- external_id would collapse to "", which
                # would collide with every other id-less posting on this
                # board under the (board, external_id) upsert key. Not a
                # usable candidate; skip rather than emit a .../None URL.
                continue
            location = j.get("PrimaryLocation") or j.get("PrimaryLocationCountry") or ""
            postings.append(
                ParsedPosting(
                    company_id=company.company_id,
                    board=company.board,
                    external_id=str(posting_id),
                    title=j.get("Title") or "",
                    jd_text=_strip_html(j.get("ShortDescriptionStr")),
                    location=location,
                    remote=_is_remote(j.get("WorkplaceType") or j.get("WorkplaceTypeCode") or ""),
                    apply_url=(
                        f"https://{company.api_base}/hcmUI/CandidateExperience/en/sites/"
                        f"{company.slug}/job/{posting_id}"
                    ),
                    posted_at=_iso(j.get("PostedDate")),
                )
            )
        if total is None:
            total = first_item.get("TotalJobsCount") or 0
        offset += _ORACLE_PAGE_SIZE
        if len(req_list) < _ORACLE_PAGE_SIZE or offset >= total:
            break

    if not ok:
        return _failed()
    if partial_failure:
        return _partial(postings)
    return AdapterResult(status="ok", postings=postings, new_etag=None)


def _eightfold_external_id(j: dict[str, Any]) -> str:
    return str(
        j.get("ats_job_id")
        or j.get("display_job_id")
        or j.get("atsJobId")
        or j.get("displayJobId")
        or j.get("id")
        or ""
    )


def _eightfold_location(j: dict[str, Any]) -> str:
    """smartapply carries a flat `location` string; pcsx only ever has
    `locations[]` (confirmed live, P3c research) -- prefer the flat
    field when present, never assume both exist."""
    if j.get("location"):
        return str(j["location"])
    return _join_nonempty([str(loc) for loc in (j.get("locations") or [])], sep=" | ")


def _eightfold_apply_url(api_base: str, j: dict[str, Any]) -> str:
    """smartapply's `canonicalPositionUrl` is already absolute; pcsx's
    only field is a site-relative `positionUrl` -- confirmed live,
    never the reverse for either tier."""
    raw = j.get("canonicalPositionUrl") or j.get("positionUrl") or ""
    if raw.startswith("http"):
        return raw
    return f"https://{api_base}{raw}" if raw else ""


async def _fetch_eightfold_positions(
    http: httpx.AsyncClient, company: DueCompany, offset: int, tier: str
) -> tuple[list[dict[str, Any]], int, dict[str, Any] | None]:
    path = "/api/pcsx/search" if tier == "pcsx" else "/api/apply/v2/jobs"
    url = (
        f"https://{company.api_base}{path}?domain={company.slug}"
        f"&start={offset}&num={_EIGHTFOLD_PAGE_SIZE}"
    )
    try:
        response = await http.get(url, timeout=_HTTP_TIMEOUT_SECONDS)
    except httpx.HTTPError:
        return [], 0, None
    try:
        data = response.json()
    except ValueError:
        return [], response.status_code, None
    if not isinstance(data, dict):
        return [], response.status_code, None
    if tier == "pcsx":
        positions = ((data.get("data") or {}).get("positions")) or []
    else:
        positions = data.get("positions") or []
    return positions, response.status_code, data


async def _fetch_eightfold_page(
    http: httpx.AsyncClient, company: DueCompany, offset: int, tier: str, *, allow_fallback: bool
) -> tuple[list[dict[str, Any]] | None, str]:
    """Returns (positions, tier) -- `positions` is None on a hard failure
    (caller stops paginating without marking this a success);
    `tier` is the possibly-updated tier for the caller's next page.
    A genuinely empty-but-valid page returns `([], tier)`, which the
    caller DOES treat as success (this platform legitimately has zero
    postings right now), matching the same distinction fetch_apple/
    fetch_workday already draw between "no valid response" and "a valid
    response with nothing in it".

    s112 (confirmed still live, P3c research, against the 3 real tenants
    n8n's own incident history named): on page 0 only, a 403 OR a
    genuinely empty `positions` array on the smartapply tier means this
    tenant needs pcsx instead -- never re-triggered on a later page,
    where running dry on an already-working tier is normal."""
    positions, status, data = await _fetch_eightfold_positions(http, company, offset, tier)
    valid = status == 200 and data is not None
    if allow_fallback and tier == "smartapply" and (status == 403 or (valid and not positions)):
        tier = "pcsx"
        positions, status, data = await _fetch_eightfold_positions(http, company, offset, tier)
        valid = status == 200 and data is not None
    if not valid:
        return None, tier
    return positions, tier


async def fetch_eightfold(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
    postings: list[ParsedPosting] = []
    ok = False
    partial_failure = False
    offset = 0
    tier = "smartapply"

    for page in range(_EIGHTFOLD_MAX_PAGES):
        positions, tier = await _fetch_eightfold_page(
            http, company, offset, tier, allow_fallback=(page == 0)
        )
        if positions is None:
            partial_failure = ok
            break
        ok = True
        if not positions:
            break
        for j in positions:
            posted_seconds = j.get("t_create") or j.get("creationTs") or j.get("postedTs")
            remote_signal = j.get("work_location_option") or j.get("workLocationOption") or ""
            postings.append(
                ParsedPosting(
                    company_id=company.company_id,
                    board=company.board,
                    external_id=_eightfold_external_id(j),
                    title=j.get("name") or "",
                    # Confirmed live, P3c research: always empty on the
                    # list endpoint, both tiers -- real content only
                    # ever comes from the separate JD-backfill lane
                    # (job_registry_poller.run_eightfold_jd_backfill).
                    jd_text="",
                    location=_eightfold_location(j),
                    remote=_is_remote(remote_signal),
                    apply_url=_eightfold_apply_url(company.api_base, j),
                    posted_at=_iso_from_unix_seconds(posted_seconds),
                )
            )
        offset += _EIGHTFOLD_PAGE_SIZE
        if len(positions) < _EIGHTFOLD_PAGE_SIZE:
            break

    if not ok:
        return _failed()
    if partial_failure:
        return _partial(postings)
    return AdapterResult(status="ok", postings=postings, new_etag=None)


def extract_eightfold_position_id(apply_url: str) -> str | None:
    match = _EIGHTFOLD_POSITION_ID_RX.search(apply_url or "")
    return match.group(1) if match else None


async def fetch_eightfold_detail(
    http: httpx.AsyncClient, api_base: str, slug: str, position_id: str
) -> EightfoldDetail | None:
    """The JD-backfill lane's own fetch. Confirmed live, P3c research:
    this detail endpoint is smartapply-shaped UNCONDITIONALLY, regardless
    of which tier a company's list data came through (tested directly
    against PayPal/Starbucks position ids sourced from their own pcsx
    listings) -- no tier-awareness needed here, unlike the list fetch."""
    url = f"https://{api_base}/api/apply/v2/jobs/{position_id}?domain={slug}"
    try:
        response = await http.get(url, timeout=_HTTP_TIMEOUT_SECONDS)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return EightfoldDetail(
        jd_text=_strip_html(data.get("job_description")),
        apply_url=data.get("canonicalPositionUrl") or "",
    )


def _avature_split_blocks(html: str) -> tuple[list[str], str]:
    """Tries each card template's split marker in order until one yields
    at least one block -- confirmed live (P3d research) all 5 real
    tenants match exactly one of these 3, unambiguously."""
    for marker, template in (
        (_AVATURE_CARD_MARKER, "card"),
        (_AVATURE_RESULT_MARKER, "result"),
        (_AVATURE_BARE_RESULT_MARKER, "bare_result"),
    ):
        blocks = html.split(marker)[1:]
        if blocks:
            return blocks, template
    return [], "none"


def _avature_parse_block(
    block: str, template: str, company: DueCompany, host: str, locale_prefix: str, portal: str
) -> ParsedPosting | None:
    title_match = _AVATURE_TITLE_ANCHOR_RX.search(block)
    if title_match is None:
        return None
    href, title = title_match.group(1), _strip_html(title_match.group(2))

    if template == "card":
        job_id_match = _AVATURE_CARD_JOB_ID_RX.search(block)
        if job_id_match is None:
            return None
        external_id = job_id_match.group(1)
        apply_url = f"https://{host}{locale_prefix}/{portal}/JobDetail?jobId={external_id}"
        location_match = _AVATURE_CARD_LOCATION_RX.search(block)
        location = _strip_html(location_match.group(1)) if location_match else ""
    else:
        id_match = _AVATURE_TRAILING_ID_RX.search(href)
        if id_match is None:
            return None
        external_id = id_match.group(1)
        apply_url = href
        if template == "result":
            location_match = _AVATURE_RESULT_LOCATION_RX.search(block)
            location = _strip_html(location_match.group(1)) if location_match else ""
        else:
            # Deloitte's own real markup, fetched directly: no dedicated
            # location CSS class exists at all -- it's the LAST of
            # several plain, un-classed <span> tags in the header
            # subtitle (e.g. "Deloitte US | Deloitte Consulting LLP |
            # Multiple Locations"). Positional, not selector-based.
            spans = _AVATURE_PLAIN_SPAN_RX.findall(block)
            location = _strip_html(spans[-1]) if spans else ""

    return ParsedPosting(
        company_id=company.company_id,
        board=company.board,
        external_id=external_id,
        title=title,
        jd_text="",
        location=location,
        remote=_is_remote(f"{location} {title}"),
        apply_url=apply_url,
        posted_at=None,
    )


async def fetch_avature(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
    # Two Sigma's own real setup: a dot in `slug` means the slug IS the
    # full custom-domain host (careers.twosigma.com), not a
    # `{slug}.avature.net` tenant. `api_base` doubles as
    # "portalPath[/listingPage]" -- confirmed live for Two Sigma
    # ("careers/OpenRoles") and every other tenant defaulting to
    # "careers"/"SearchJobs".
    host = company.slug if "." in company.slug else f"{company.slug}.avature.net"
    portal, _, listing_page = (company.api_base or "careers").partition("/")
    if not listing_page:
        listing_page = "SearchJobs"

    postings: list[ParsedPosting] = []
    ok = False
    offset = 0
    use_locale = True

    for _page in range(_AVATURE_MAX_PAGES):
        locale_prefix = "/en_US" if use_locale else ""
        url = (
            f"https://{host}{locale_prefix}/{portal}/{listing_page}"
            f"?jobRecordsPerPage={_AVATURE_PAGE_SIZE}&jobOffset={offset}"
        )
        try:
            response = await http.get(url, timeout=_HTTP_TIMEOUT_SECONDS)
        except httpx.HTTPError:
            break
        if response.status_code != 200:
            break
        body = response.text

        # Confirmed live (P3d research): Bloomberg/Two Sigma's /en_US/
        # path returns a bare marker string, no cards -- only ever
        # checked/switched once per fetch, on whichever page happens to
        # be first, not re-evaluated every page.
        if use_locale and _AVATURE_REDIRECT_MARKER in body:
            use_locale = False
            locale_prefix = ""
            url = (
                f"https://{host}/{portal}/{listing_page}"
                f"?jobRecordsPerPage={_AVATURE_PAGE_SIZE}&jobOffset={offset}"
            )
            try:
                response = await http.get(url, timeout=_HTTP_TIMEOUT_SECONDS)
            except httpx.HTTPError:
                break
            if response.status_code != 200:
                break
            body = response.text

        blocks, template = _avature_split_blocks(body)
        if not blocks:
            # Deliberately NOT `ok = True` here -- n8n's own real incident
            # history (s201) documents exactly this as "the silent-empty
            # trap": treating zero matched blocks as a valid empty result
            # let a real board (Deloitte, before its 3rd template existed)
            # decay to permanently-empty without ever erroring or
            # deactivating. Unlike a JSON API's structured `{"positions":
            # []}` (genuinely unambiguous), zero string-split matches on
            # scraped HTML can't be told apart from a stale template
            # assumption -- so it's treated as a failure (real backoff,
            # eventual deactivation if it persists), not a silent success.
            break
        ok = True
        for block in blocks:
            parsed = _avature_parse_block(block, template, company, host, locale_prefix, portal)
            if parsed is not None:
                postings.append(parsed)
        # s72 (confirmed still live, P3d research, on ALL 5 real tenants,
        # not just IBM): advance by the ACTUAL block count returned, never
        # the requested page size -- every tenant renders fewer than the
        # requested 50 (9/12/10/10/10), so `offset += _AVATURE_PAGE_SIZE`
        # would skip most of every board.
        offset += len(blocks)

    if not ok:
        return _failed()
    return AdapterResult(status="ok", postings=postings, new_etag=None)


def _successfactors_parse_tile(tile: str, host: str, company: DueCompany) -> ParsedPosting | None:
    id_match = _SUCCESSFACTORS_EXTERNAL_ID_RX.match(tile)
    if id_match is None:
        return None
    title_match = _SUCCESSFACTORS_TITLE_RX.search(tile)
    location_match = _SUCCESSFACTORS_LOCATION_RX.search(tile)
    url_match = _SUCCESSFACTORS_DATA_URL_RX.search(tile)
    title = _strip_html(title_match.group(1)) if title_match else ""
    # Confirmed live (P3d research): 3 of 5 real tenants render no
    # location field on the tile at all (a real per-tenant Career Site
    # Builder configuration choice) -- absence means an honestly empty
    # location, never guessed.
    location = _strip_html(location_match.group(1)) if location_match else ""
    apply_url = f"https://{host}{url_match.group(1)}" if url_match else ""
    return ParsedPosting(
        company_id=company.company_id,
        board=company.board,
        external_id=id_match.group(1),
        title=title,
        jd_text="",
        location=location,
        remote=_is_remote(f"{location} {title}"),
        apply_url=apply_url,
        posted_at=None,
    )


async def fetch_successfactors(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
    postings: list[ParsedPosting] = []
    ok = False
    offset = 0

    for _page in range(_SUCCESSFACTORS_MAX_PAGES):
        url = (
            f"https://{company.api_base}/search/tile-search-results"
            f"?q=&sortColumn=referencedate&sortDirection=desc&startrow={offset}"
        )
        try:
            response = await http.get(url, timeout=_HTTP_TIMEOUT_SECONDS)
        except httpx.HTTPError:
            break
        if response.status_code != 200:
            break
        tiles = response.text.split(_SUCCESSFACTORS_TILE_MARKER)[1:]
        if not tiles:
            # Same "silent-empty trap" reasoning as fetch_avature -- zero
            # matched tiles on scraped HTML can't be told apart from a
            # stale extraction pattern, so it's a failure, not a silent
            # empty success.
            break
        ok = True
        for tile in tiles:
            parsed = _successfactors_parse_tile(tile, company.api_base, company)
            if parsed is not None:
                postings.append(parsed)
        # Confirmed live (P3d research): the tenant's own per-page tile
        # count is NOT a platform constant -- 4 of 5 real tenants are 25,
        # adidas is real at 50 -- and there is no request-side page-size
        # param to control it at all. Advancing by the actual count
        # returned (not a hardcoded 25) sidesteps needing to know any
        # tenant's real page size in advance, and avoids the exact
        # pagination-drift bug already found and fixed for SmartRecruiters
        # in P3a (a hardcoded 25 would re-fetch half of every adidas page).
        offset += len(tiles)

    if not ok:
        return _failed()
    return AdapterResult(status="ok", postings=postings, new_etag=None)


def _google_parse_card(card: str, company: DueCompany) -> ParsedPosting | None:
    ssk_match = _GOOGLE_SSK_RX.search(card)
    if ssk_match is None:
        return None
    title_match = _GOOGLE_TITLE_RX.search(card)
    location_match = _GOOGLE_LOCATION_RX.search(card)
    href_match = _GOOGLE_HREF_RX.search(card)
    title = _strip_html(title_match.group(1)) if title_match else ""
    location = _strip_html(location_match.group(1)) if location_match else ""
    apply_url = f"{_GOOGLE_BASE_URL}{href_match.group(1)}" if href_match else ""
    return ParsedPosting(
        company_id=company.company_id,
        board=company.board,
        external_id=ssk_match.group(1),
        title=title,
        jd_text="",
        location=location,
        remote=_is_remote(f"{location} {title}"),
        apply_url=apply_url,
        posted_at=None,
    )


async def fetch_google(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
    """The only real PAGINATED_TYPE in this port -- resumes from
    `company.etag` as a page cursor (repurposed exactly like n8n's own
    real design; confirmed live still holding a real historical value,
    "25", carried over from P1's seed import). Reports `hit_end` back to
    the caller but has NO notion of a multi-tick "sweep" itself -- that
    bookkeeping is entirely job_registry_poller.py's job, deliberately,
    since this module's other 14 adapters have no equivalent concept at
    all and shouldn't need to care about one."""
    start_page = int(company.etag) if company.etag.isdigit() else 1
    start_page = max(1, start_page)

    postings: list[ParsedPosting] = []
    ok = False
    hit_end = False
    page = start_page

    for _tick_page in range(_GOOGLE_MAX_PAGES_PER_TICK):
        url = f"{_GOOGLE_BASE_URL}jobs/results?page={page}"
        try:
            response = await http.get(url, timeout=_HTTP_TIMEOUT_SECONDS)
        except httpx.HTTPError:
            break
        if response.status_code != 200:
            break
        ok = True  # a valid response -- even a genuinely empty one is a
        # normal, expected way for a sweep to end here, unlike Avature/
        # SuccessFactors' own single-page ambiguity (see this module's
        # own docstring for why the safety net lives one layer up for
        # this adapter specifically).
        cards = response.text.split(_GOOGLE_CARD_MARKER)[1:]
        if not cards:
            hit_end = True
            break
        for card in cards:
            parsed = _google_parse_card(card, company)
            if parsed is not None:
                postings.append(parsed)
        if len(cards) < _GOOGLE_PAGE_SIZE:
            hit_end = True
            break
        page += 1

    if not ok:
        return _failed()
    next_cursor = "1" if hit_end else str(page)
    return AdapterResult(status="ok", postings=postings, new_etag=next_cursor, hit_end=hit_end)


ADAPTERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "workday": fetch_workday,
    "smartrecruiters": fetch_smartrecruiters,
    "workable": fetch_workable,
    "recruitee": fetch_recruitee,
    "amazon": fetch_amazon,
    "apple": fetch_apple,
    "deshaw": fetch_deshaw,
    "oracle": fetch_oracle,
    "eightfold": fetch_eightfold,
    "avature": fetch_avature,
    "successfactors": fetch_successfactors,
    "google": fetch_google,
}
