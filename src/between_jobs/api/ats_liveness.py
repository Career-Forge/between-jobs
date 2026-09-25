"""Job Finder P7 (job-finder-port.md's own build order; live-search-track.md
names it as the next phase after P6) -- live-search-lane ATS liveness
verification. A faithful port of n8n's real `Verify Job Links` node (v3,
read directly from `CareerForge_Master_local.json`, not paraphrased -- its
own header comment already states the mechanism: "API-verify instead of
status-probe where a definitive, unauthenticated endpoint exists...
generic HEAD-chase remains the fallback for everything else. Inconclusive
!= dead, always.").

This is specifically for LIVE-SEARCH-LANE results (P4's 9 BYOK providers).
Registry-lane results (P1-P3e) already get liveness for free from the
poller's own absence-based mechanism (a job silently drops out of a
board's re-listing -> `close_stale_job_registry_postings` marks it
closed) -- this module is never meant to run on registry-lane
`SearchResult`s, only on freshly-scraped ones that haven't been through
the poller.

Ported platforms, in n8n's own real dispatch order: Ashby, Lever,
Greenhouse, Workday, Avature (+ the `careers.twosigma.com` custom-domain
exception), Google careers, D.E. Shaw careers, SuccessFactors Career Site
Builder (matched by URL PATH shape, not host, since tenants use arbitrary
company-owned domains), LinkedIn, and a generic HEAD-chase fallback for
everything else. n8n's own plan-doc summary names only "Ashby/Greenhouse/
Workday/LinkedIn/generic fallback" -- Lever/Avature/Google/D.E. Shaw/
SuccessFactors are real, present in the actual source and equally
load-bearing, just undersold by that summary; all are ported here.

Every mechanism was independently live-re-verified against real endpoints
before porting (both the ALIVE case and, more importantly, a genuinely
DEAD/bogus case per platform, not just "the happy path still 200s").
Confirmed NO drift for 8 of the 9 platform-specific mechanisms (Ashby,
Lever, Greenhouse, Workday -- including a real, live-triggered 403/S22
"permission denied" dead signal on a job that had genuinely closed and
been reposted under a new requisition id between when the reference was
written and this verification -- Avature, Google, D.E. Shaw, LinkedIn).
SuccessFactors had two real, disclosed drifts, fixed here rather than
silently ported as documented:
- A genuinely nonexistent/malformed job id does NOT reproduce n8n's
  documented "200 + no itemtype" dead signal -- it 302-redirects to a
  path containing `/errorpage/` instead. The 200/no-itemtype signal is
  real but specific to a job that DID exist and was later closed/filled
  (confirmed against 6 real closed EY postings, every one containing the
  literal body text "The Job is no longer available", HTTP 200, and zero
  occurrences of the itemtype attribute). This port treats BOTH signals
  as dead, not just the one the reference documents.
- n8n's own itemprop location extraction (addressLocality/addressRegion/
  addressCountry) doesn't match real EY markup -- the real page's
  PostalAddress block only carries a single combined `streetAddress`
  meta value. Extraction here tries the three-part decomposition first
  (a different tenant might still use it), falling back to the combined
  field.

"Inconclusive is never dead" holds throughout: any network error,
timeout, non-matching status code, or unparseable response resolves to
KEEP, never DEAD -- matching every one of n8n's own already-incident-
hardened decisions (a bare 403 from Workday's CXS API is inconclusive;
only the exact S22-errorCode-or-"permission denied"-message signature is
treated as dead; a redirect target must resolve to an absolute https://
URL to be followed at all -- an http:// target is inconclusive, since
this project also has no plaintext-http client configured for this
path, matching the reference's own sandboxed-https-only constraint,
which happens to be a deliberate choice here too, not just an inherited
limitation).

Two real, deliberate simplifications from n8n's own implementation, not
fidelity losses:
- n8n's own JS sandbox had NO url/fetch globals available (a hand-rolled
  regex `parseUrl` and manual relative-redirect resolution were the only
  option) and a real, hard-won default body-read cap (n8n's own sandboxed
  fetch truncates at 300KB unless raised per-call -- its own header
  comment describes a real incident where a truncated 11.4MB Ashby board
  response silently failed to parse and made every job on it permanently
  "inconclusive"). Python has neither constraint: `httpx.URL.join()`
  handles relative-redirect resolution via real RFC 3986 semantics
  (absolute https://, absolute http://, protocol-relative //, and
  path-relative / all handled uniformly, then checked for a final https
  scheme), and `httpx` has no default truncation to work around, so no
  size-cap machinery is ported here.
- n8n's own node also re-runs a SECOND, smaller location-match re-filter
  (S30) after backfilling a corrected location from ATS ground truth,
  using an `app_settings.geo_reference` table this project never ported.
  between-jobs already has a strictly more capable location filter
  (`search_aggregation.filter_by_location`, backed by the real
  34,006-city GeoNames gazetteer from P5d) -- duplicating a weaker
  version of the same logic here would violate this codebase's own DRY
  discipline. This module therefore does NOT re-filter by location
  itself: it only corrects `location` (and resets `location_verified` to
  `None`, since the prior verified status was computed against the
  pre-correction value and can no longer be trusted) when ATS ground
  truth reveals a different real location than what a live-search
  provider's snippet guessed. A caller that wants n8n's own S30 behavior
  can simply re-invoke `filter_by_location` on this module's own output
  -- the same real gazetteer, applied twice, is still correct and
  idempotent, not a second implementation living here.

Pipeline position, confirmed directly from n8n's own real node graph (not
assumed from the plan doc's prose): `Filter Applied Jobs -> Verify Job
Links -> Experience Filter -> Build Scorer Input -> JobScorer`. Liveness
verification runs AFTER P5's aggregate/filter stages and BEFORE P6's
`score_jobs` -- no point spending an LLM call scoring a job that's
already confirmed dead. "Filter Applied Jobs" (dropping postings the
user already applied to) is a separate concern, out of this phase's own
scope, left for wherever the actual search route gets built.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, replace
from typing import Any, Literal
from urllib.parse import unquote

import httpx

from .search_providers import SearchResult

logger = logging.getLogger(__name__)

_JsonDict = dict[str, Any]

_PROBE_TIMEOUT_SECONDS = 3.5
"""Per-HTTP-request timeout for every single-job-detail probe (Greenhouse/
Workday/Avature/Google/D.E. Shaw/SuccessFactors/LinkedIn/generic) --
deliberately much tighter than the registry poller's own 20s
(`job_registry_adapters._HTTP_TIMEOUT_SECONDS`), since this runs on a
live, interactive search request path, not a background tick, matching
n8n's own real per-request timeout exactly. NOT used for the Ashby/Lever
org-level list fetch -- see `_ORG_LIST_TIMEOUT_SECONDS`."""
_ORG_LIST_TIMEOUT_SECONDS = 15.0
"""A real, live-measured limitation, not a divergence from n8n's own
reference: n8n uses the SAME 3.5s timeout for every request, including
Ashby/Lever's own org-level list fetch -- but a real, currently-active
company's real board (Veeva's Lever postings: 884 open roles, 12.56MB)
took 8.32s to fetch over a real network connection in this project's own
live verification, well past 3.5s. n8n's reference likely has this exact
same limitation; it just was never exercised at this scale before. Ashby/
Lever get a separate, larger per-request budget specifically for their
org-level fetch (never for anything else) so a genuinely large board
doesn't make EVERY job at that company permanently "inconclusive" --
`_JOB_OVERALL_TIMEOUT_SECONDS` is sized to match."""
_JOB_OVERALL_TIMEOUT_SECONDS = 18.0
"""Per-job wall-clock cap across however many requests one probe makes
(e.g. Workday's URL construction + one CXS fetch, or Ashby/Lever's own
org-level fetch). Sized to give the `_ORG_LIST_TIMEOUT_SECONDS` fetch a
real chance to complete with margin, not just the tighter single-job
`_PROBE_TIMEOUT_SECONDS` case -- a job that still blows this budget
resolves to KEEP -- inconclusive, not dead -- same as any other probe
failure, matching n8n's own real per-job race."""
_MAX_REDIRECT_HOPS = 3

_LISTING_PAGE_RX = re.compile(
    r"\b\d+\+?\s+(?:[a-z]+\s+){0,4}(jobs|openings|positions|roles)\b", re.IGNORECASE
)
"""A "300+ Machine Learning Engineer Jobs in Austin" aggregator listing
page mistaken for a single posting -- ported alongside liveness
verification since it's bundled in the exact same n8n node, not because
it's itself a liveness check."""

_LivenessVerdict = Literal["keep", "dead"]


@dataclass(frozen=True)
class _ProbeResult:
    verdict: _LivenessVerdict
    location: str | None = None


def _is_listing_page(title: str) -> bool:
    return bool(_LISTING_PAGE_RX.search(title))


def _host_of(raw: str) -> str:
    """The host alone, for logs -- a job URL's path and query stay out. Never
    raises: it runs inside exception handlers."""
    try:
        url = _parse_url(raw)
    except (httpx.InvalidURL, UnicodeError, ValueError, TypeError):
        return "unparseable"
    return url.host if url is not None else "unparseable"


def _parse_url(raw: str) -> httpx.URL | None:
    # `.host` decodes IDNA, which raises UnicodeError for a malformed xn-- label.
    try:
        url = httpx.URL(raw)
        if url.scheme != "https" or not url.host:
            return None
    except (httpx.InvalidURL, UnicodeError):
        return None
    return url


async def _chase(http: httpx.AsyncClient, url: httpx.URL, *, method: str) -> tuple[int, str]:
    """Manually follows up to `_MAX_REDIRECT_HOPS` redirects, mirroring
    n8n's own real `chase()` -- only an absolute https:// resolution is
    followed (`httpx.URL.join` handles the RFC 3986 resolution itself;
    a redirect target that resolves to http:// or fails to parse is
    treated as unfollowable, matching n8n's own https-only sandbox
    constraint). Returns (final_status, final_url); a redirect-cap
    overrun returns status 0, matching n8n's own inconclusive sentinel."""
    current = url
    final_url = str(url)
    for _hop in range(_MAX_REDIRECT_HOPS + 1):
        try:
            response = await http.request(
                method, str(current), timeout=_PROBE_TIMEOUT_SECONDS, follow_redirects=False
            )
        except httpx.HTTPError:
            return 0, final_url
        if response.status_code not in (301, 302, 303, 307, 308):
            return response.status_code, final_url
        location = response.headers.get("location", "")
        if not location:
            return response.status_code, final_url
        try:
            candidate = current.join(location)
        except httpx.InvalidURL:
            return response.status_code, final_url
        if candidate.scheme != "https":
            return response.status_code, final_url
        current = candidate
        final_url = str(candidate)
    return 0, final_url


# ── Ashby ────────────────────────────────────────────────────────────────

_ASHBY_PATH_RX = re.compile(r"^/([^/]+)/([0-9a-fA-F-]{20,})")


async def _fetch_ashby_org_jobs(http: httpx.AsyncClient, org: str) -> dict[str, _JsonDict] | None:
    try:
        response = await http.get(
            f"https://api.ashbyhq.com/posting-api/job-board/{org}",
            timeout=_ORG_LIST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        return None
    return {j["id"]: j for j in jobs if isinstance(j, dict) and "id" in j}


def _as_dict(value: Any) -> _JsonDict:
    return value if isinstance(value, dict) else {}


def _ashby_location(job: _JsonDict) -> str | None:
    address = _as_dict(job.get("address"))
    postal = _as_dict(address.get("postalAddress"))
    country = postal.get("addressCountry")
    parts = [job.get("location"), country]
    joined = ", ".join(p for p in parts if p)
    return joined or None


async def _probe_ashby(
    http: httpx.AsyncClient,
    url: httpx.URL,
    cache: dict[str, asyncio.Task[dict[str, _JsonDict] | None]],
) -> _ProbeResult:
    m = _ASHBY_PATH_RX.match(url.path)
    if not m:
        return _ProbeResult("keep")
    org, job_id = unquote(m.group(1)), m.group(2)
    if org not in cache:
        cache[org] = asyncio.ensure_future(_fetch_ashby_org_jobs(http, org))
    # `shield` is real, not defensive theater: a genuine live run found
    # that without it, one job's own `_JOB_OVERALL_TIMEOUT_SECONDS`
    # timeout cancelling ITS OWN probe chain also cancels this SHARED
    # org-level task (asyncio propagates a task's cancellation to
    # whatever it's currently awaiting) -- breaking every OTHER job in
    # the same batch still waiting on the identical org fetch, which then
    # sees an uncaught CancelledError instead of a clean "keep" verdict.
    jobs = await asyncio.shield(cache[org])
    if jobs is None:
        return _ProbeResult("keep")
    job = jobs.get(job_id)
    if job is None:
        return _ProbeResult("dead")
    return _ProbeResult("keep", _ashby_location(job))


# ── Lever ────────────────────────────────────────────────────────────────

_LEVER_PATH_RX = re.compile(r"^/([^/]+)/([0-9a-fA-F-]{20,})")


async def _fetch_lever_org_postings(
    http: httpx.AsyncClient, company: str
) -> dict[str, _JsonDict] | None:
    try:
        response = await http.get(
            f"https://api.lever.co/v0/postings/{company}?mode=json",
            timeout=_ORG_LIST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    return {p["id"]: p for p in data if isinstance(p, dict) and "id" in p}


async def _probe_lever(
    http: httpx.AsyncClient,
    url: httpx.URL,
    cache: dict[str, asyncio.Task[dict[str, _JsonDict] | None]],
) -> _ProbeResult:
    m = _LEVER_PATH_RX.match(url.path)
    if not m:
        return _ProbeResult("keep")
    company, job_id = unquote(m.group(1)), m.group(2)
    if company not in cache:
        cache[company] = asyncio.ensure_future(_fetch_lever_org_postings(http, company))
    # See _probe_ashby's own comment: shield this shared org-level fetch
    # from one job's timeout cancelling it out from under every other job
    # in the batch that's also awaiting it.
    postings = await asyncio.shield(cache[company])
    if postings is None:
        return _ProbeResult("keep")
    posting = postings.get(job_id)
    if posting is None:
        return _ProbeResult("dead")
    categories = _as_dict(posting.get("categories"))
    return _ProbeResult("keep", categories.get("location") or None)


# ── Greenhouse ───────────────────────────────────────────────────────────

_GREENHOUSE_PATH_RX = re.compile(r"^/([^/]+)/jobs/(\d+)")


async def _probe_greenhouse(http: httpx.AsyncClient, url: httpx.URL) -> _ProbeResult:
    m = _GREENHOUSE_PATH_RX.match(url.path)
    if m:
        board, job_id = m.group(1), m.group(2)
        try:
            response = await http.get(
                f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}",
                timeout=_PROBE_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError:
            response = None
        if response is not None:
            if response.status_code == 404:
                return _ProbeResult("dead")
            if response.status_code == 200:
                try:
                    data = response.json()
                except ValueError:
                    data = {}
                location = (
                    (data.get("location") or {}).get("name") if isinstance(data, dict) else None
                )
                return _ProbeResult("keep", location)
            # API errored (network/timeout) or another status -> fall through to
            # redirect-chase below, matching n8n's own real fallback exactly.
    status, final_url = await _chase(http, url, method="HEAD")
    if status in (404, 410) or "error=true" in final_url:
        return _ProbeResult("dead")
    return _ProbeResult("keep")


# ── Workday ──────────────────────────────────────────────────────────────

_LOCALE_SEGMENT_RX = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


def _workday_cxs_url(url: httpx.URL) -> str | None:
    tenant = url.host.split(".")[0]
    segs = [s for s in url.path.split("?")[0].split("/") if s]
    if segs and _LOCALE_SEGMENT_RX.match(segs[0]):
        segs = segs[1:]
    if "apply" in segs:
        segs = segs[: segs.index("apply")]
    if "job" in segs:
        marker_idx = segs.index("job")
    elif "details" in segs:
        marker_idx = segs.index("details")
    else:
        return None
    if marker_idx < 1 or marker_idx == len(segs) - 1:
        return None
    site = segs[0]
    last = segs[-1]
    return f"https://{url.host}/wday/cxs/{tenant}/{site}/job/{last}"


async def _probe_workday(http: httpx.AsyncClient, url: httpx.URL) -> _ProbeResult:
    cxs_url = _workday_cxs_url(url)
    if cxs_url is None:
        return _ProbeResult("keep")
    try:
        response = await http.get(cxs_url, timeout=_PROBE_TIMEOUT_SECONDS)
    except httpx.HTTPError:
        return _ProbeResult("keep")
    if response.status_code == 404:
        return _ProbeResult("dead")
    if response.status_code == 403:
        try:
            body = response.json()
        except ValueError:
            body = {}
        error_code = body.get("errorCode") if isinstance(body, dict) else None
        message = str(body.get("message") or "") if isinstance(body, dict) else ""
        if error_code == "S22" or re.search(r"permission denied", message, re.IGNORECASE):
            return _ProbeResult("dead")
        return _ProbeResult("keep")
    if response.status_code == 200:
        try:
            data = response.json()
        except ValueError:
            data = {}
        info = data.get("jobPostingInfo") if isinstance(data, dict) else None
        if isinstance(info, dict):
            if info.get("canApply") is False:
                return _ProbeResult("dead")
            country_obj = _as_dict(info.get("country"))
            parts = [info.get("location"), country_obj.get("descriptor")]
            location = ", ".join(p for p in parts if p) or None
            return _ProbeResult("keep", location)
        return _ProbeResult("keep")
    return _ProbeResult("keep")


# ── Avature (+ careers.twosigma.com) ────────────────────────────────────

_AVATURE_JOBID_RX = re.compile(r"[?&]jobId=(\d+)")
_AVATURE_JOBDETAIL_RX = re.compile(r"/JobDetail/[^/]+/(\d+)")
_ERROR_PATH_RX = re.compile(r"/Error(?:[/?]|$)", re.IGNORECASE)


async def _probe_avature(http: httpx.AsyncClient, url: httpx.URL) -> _ProbeResult:
    if not (_AVATURE_JOBID_RX.search(str(url)) or _AVATURE_JOBDETAIL_RX.search(url.path)):
        return _ProbeResult("keep")
    try:
        response = await http.get(
            str(url),
            headers={"Accept": "text/html"},
            timeout=_PROBE_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
    except httpx.HTTPError:
        return _ProbeResult("keep")
    if response.status_code in (301, 302):
        location = response.headers.get("location", "")
        if _ERROR_PATH_RX.search(location):
            return _ProbeResult("dead")
    return _ProbeResult("keep")


# ── Google careers ───────────────────────────────────────────────────────

_GOOGLE_JOBS_PATH_PREFIX = "/about/careers/applications/jobs/results/"
_OG_TITLE_RX = re.compile(r'<meta property="og:title" content="([^"]*)"')


async def _probe_google(http: httpx.AsyncClient, url: httpx.URL) -> _ProbeResult:
    try:
        response = await http.get(
            str(url), headers={"Accept": "text/html"}, timeout=_PROBE_TIMEOUT_SECONDS
        )
    except httpx.HTTPError:
        return _ProbeResult("keep")
    m = _OG_TITLE_RX.search(response.text)
    if m and not m.group(1).strip():
        return _ProbeResult("dead")
    return _ProbeResult("keep")


# ── D.E. Shaw careers ────────────────────────────────────────────────────

_DESHAW_NEXT_DATA_RX = re.compile(r'<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>')


async def _probe_deshaw(http: httpx.AsyncClient, url: httpx.URL) -> _ProbeResult:
    try:
        response = await http.get(
            str(url), headers={"Accept": "text/html"}, timeout=_PROBE_TIMEOUT_SECONDS
        )
    except httpx.HTTPError:
        return _ProbeResult("keep")
    m = _DESHAW_NEXT_DATA_RX.search(response.text)
    if not m:
        return _ProbeResult("keep")
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return _ProbeResult("keep")
    props = data.get("props") if isinstance(data, dict) else {}
    page_props = props.get("pageProps") if isinstance(props, dict) else {}
    if not isinstance(page_props, dict):
        return _ProbeResult("keep")
    if page_props.get("redirectToCareers") and not page_props.get("jobData"):
        return _ProbeResult("dead")
    return _ProbeResult("keep")


# ── SuccessFactors Career Site Builder ──────────────────────────────────

_SUCCESSFACTORS_PATH_RX = re.compile(r"/job/[^/]+/\d+/?$")
_SF_ITEMTYPE = 'itemtype="http://schema.org/JobPosting"'
_SF_FINGERPRINT_RX = re.compile(r"careerSiteCompanyId|successfactors\.com|sapsf\.(?:com|eu|cn)")
_SF_ERRORPAGE_RX = re.compile(r"/errorpage/", re.IGNORECASE)
_SF_LOCALITY_RX = re.compile(r'addressLocality"\s+content="([^"]*)"')
_SF_REGION_RX = re.compile(r'addressRegion"\s+content="([^"]*)"')
_SF_COUNTRY_RX = re.compile(r'addressCountry"\s+content="([^"]*)"')
_SF_STREET_RX = re.compile(r'streetAddress"\s+content="([^"]*)"')


def _successfactors_location(body: str) -> str | None:
    matches = [
        _SF_LOCALITY_RX.search(body),
        _SF_REGION_RX.search(body),
        _SF_COUNTRY_RX.search(body),
    ]
    parts = [m.group(1) for m in matches if m and m.group(1)]
    if parts:
        return ", ".join(parts)
    # Real live drift (see module docstring): several tenants only carry a
    # single combined streetAddress meta value, never the 3-part
    # decomposition n8n's own reference assumed.
    street = _SF_STREET_RX.search(body)
    return street.group(1) if street and street.group(1) else None


async def _probe_successfactors(http: httpx.AsyncClient, url: httpx.URL) -> _ProbeResult:
    try:
        response = await http.get(
            str(url),
            headers={"Accept": "text/html"},
            timeout=_PROBE_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
    except httpx.HTTPError:
        return _ProbeResult("keep")
    if response.status_code in (301, 302, 303, 307, 308):
        location = response.headers.get("location", "")
        # Real live drift (see module docstring): a genuinely nonexistent
        # id redirects to /errorpage/ rather than reproducing the 200/
        # no-itemtype signal n8n's own reference documents -- that signal
        # is real, but only for a job that existed and was later closed.
        if _SF_ERRORPAGE_RX.search(location):
            return _ProbeResult("dead")
        return _ProbeResult("keep")
    if response.status_code != 200:
        return _ProbeResult("keep")
    body = response.text
    if _SF_ITEMTYPE not in body:
        # Ambiguous: could be a real closed SF job (the documented dead
        # signal) or a non-SF page that coincidentally matched the
        # /job/<slug>/<digits>/ path shape -- dispatch here is path-shape-
        # only, since real SF tenants use arbitrary vanity domains with no
        # shared host suffix to dispatch on instead (confirmed against 5
        # real seeded tenants). Corroborate against the SF Career Site
        # Builder's own body fingerprint (confirmed live on
        # careers.ey.com and jobs.exxonmobil.com) before committing to
        # "dead" -- absent that too, this was never confirmed to be an SF
        # page at all, so fall back to "keep", same as every other
        # genuinely inconclusive case ("inconclusive != dead, always").
        if not _SF_FINGERPRINT_RX.search(body):
            return _ProbeResult("keep")
        return _ProbeResult("dead")
    return _ProbeResult("keep", _successfactors_location(body))


# ── LinkedIn ─────────────────────────────────────────────────────────────

_LINKEDIN_JOB_ID_RX = re.compile(r"/jobs/view/(?:[^/]*-)?(\d+)")
_LINKEDIN_DEAD_TEXT_RX = re.compile(r"no longer accepting applications|closed-job", re.IGNORECASE)


async def _probe_linkedin(http: httpx.AsyncClient, url: httpx.URL) -> _ProbeResult:
    m = _LINKEDIN_JOB_ID_RX.search(url.path)
    if m:
        job_id = m.group(1)
        try:
            response = await http.get(
                f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}",
                headers={"Accept": "text/html"},
                timeout=_PROBE_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError:
            response = None
        if response is not None:
            if response.status_code == 404:
                return _ProbeResult("dead")
            if _LINKEDIN_DEAD_TEXT_RX.search(response.text):
                return _ProbeResult("dead")
    # Always also runs the redirect-chase, even after a clean guest-API
    # result -- matches n8n's own real code exactly, which never early-
    # returns "keep" from the guest-API branch.
    status, final_url = await _chase(http, url, method="HEAD")
    if status in (404, 410):
        return _ProbeResult("dead")
    if "expired_jd_redirect" in final_url:
        return _ProbeResult("dead")
    return _ProbeResult("keep")


# ── generic fallback ─────────────────────────────────────────────────────


async def _probe_generic(http: httpx.AsyncClient, url: httpx.URL) -> _ProbeResult:
    """A real, working, but genuinely weak/conservative check, confirmed
    live: it reliably catches only ATS platforms whose dead-job path
    terminates in a direct 404/410 (confirmed live: Lever). A platform
    whose dead-job path instead redirects (even through a same-origin
    relative Location header, which `_chase` DOES correctly follow, via
    `httpx.URL.join`, matching n8n's own real `resolveLoc` exactly) to a
    still-200 fallback/board page will silently pass through as
    inconclusive/keep -- a status-code-only check has no way to
    distinguish "landed on a real live posting" from "landed on a
    generic board-listing page after the specific job was removed"
    (confirmed live: Greenhouse's own generic "job not found" UX
    redirects to exactly this kind of still-200 board page -- never
    reached here in practice, since Greenhouse always routes through
    `_probe_greenhouse` above, which catches this exact case via its own
    JSON detail API instead). This is the reference's own documented
    tradeoff, not a bug to special-case per-site here."""
    status, _final_url = await _chase(http, url, method="HEAD")
    if status in (404, 410):
        return _ProbeResult("dead")
    return _ProbeResult("keep")


# ── dispatch ─────────────────────────────────────────────────────────────


async def _probe(
    http: httpx.AsyncClient,
    result: SearchResult,
    ashby_cache: dict[str, asyncio.Task[dict[str, _JsonDict] | None]],
    lever_cache: dict[str, asyncio.Task[dict[str, _JsonDict] | None]],
) -> _ProbeResult:
    url = _parse_url(result.apply_url)
    if url is None:
        return _ProbeResult("keep")
    host = url.host.lower()

    if host == "jobs.ashbyhq.com":
        return await _probe_ashby(http, url, ashby_cache)
    if host == "jobs.lever.co":
        return await _probe_lever(http, url, lever_cache)
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io"):
        return await _probe_greenhouse(http, url)
    if host.endswith(".myworkdayjobs.com"):
        return await _probe_workday(http, url)
    if host.endswith(".avature.net") or host == "careers.twosigma.com":
        return await _probe_avature(http, url)
    if host == "www.google.com" and url.path.startswith(_GOOGLE_JOBS_PATH_PREFIX):
        return await _probe_google(http, url)
    if host == "www.deshaw.com" and url.path.startswith("/careers/"):
        return await _probe_deshaw(http, url)
    if _SUCCESSFACTORS_PATH_RX.search(url.path):
        return await _probe_successfactors(http, url)
    if host in ("www.linkedin.com", "linkedin.com"):
        return await _probe_linkedin(http, url)
    return await _probe_generic(http, url)


async def verify_liveness(
    http: httpx.AsyncClient, results: list[SearchResult]
) -> tuple[list[SearchResult], int]:
    """Filters `results` down to those confirmed still alive, or merely
    inconclusive (see the module docstring's "inconclusive is never dead"
    rule), via a live per-platform probe. Returns `(alive, dead_removed)`.

    Every probe races against `_JOB_OVERALL_TIMEOUT_SECONDS`; a job that
    blows the budget, or whose probe raises anything unexpected, resolves
    to keep -- same fail-open contract as every other probe failure, never
    a batch-wide crash over one bad response."""
    candidates = [r for r in results if not _is_listing_page(r.title)]
    ashby_cache: dict[str, asyncio.Task[dict[str, _JsonDict] | None]] = {}
    lever_cache: dict[str, asyncio.Task[dict[str, _JsonDict] | None]] = {}

    timed_out = 0

    async def _probe_with_budget(result: SearchResult) -> _ProbeResult:
        nonlocal timed_out
        try:
            return await asyncio.wait_for(
                _probe(http, result, ashby_cache, lever_cache),
                timeout=_JOB_OVERALL_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            timed_out += 1
            return _ProbeResult("keep")
        except Exception:
            logger.warning(
                "liveness probe failed; keeping the result",
                exc_info=True,
                extra={"ctx": {"host": _host_of(result.apply_url)}},
            )
            return _ProbeResult("keep")

    verdicts = await asyncio.gather(*(_probe_with_budget(r) for r in candidates))
    if timed_out:
        logger.info(
            "liveness probes timed out; those results were kept",
            extra={"ctx": {"timed_out": timed_out, "probed": len(candidates)}},
        )

    alive: list[SearchResult] = []
    for result, verdict in zip(candidates, verdicts, strict=True):
        if verdict.verdict == "dead":
            continue
        updated = replace(result, link_checked=True)
        if verdict.location:
            updated = replace(updated, location=verdict.location, location_verified=None)
        alive.append(updated)
    return alive, len(candidates) - len(alive)
