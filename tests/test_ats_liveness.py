"""Tests for Job Finder P7 (job-finder-port.md's own build order) --
live-search-lane ATS liveness verification. Uses httpx's own MockTransport
(same convention as test_job_registry_adapters.py) with response shapes
taken from real live-verified payloads, not invented ones."""

from __future__ import annotations

from typing import Any

import httpx

from between_jobs.api.ats_liveness import (
    _as_dict,
    _ashby_location,
    _chase,
    _is_listing_page,
    _probe,
    _probe_ashby,
    _probe_avature,
    _probe_deshaw,
    _probe_generic,
    _probe_google,
    _probe_greenhouse,
    _probe_lever,
    _probe_linkedin,
    _probe_successfactors,
    _probe_workday,
    _successfactors_location,
    _workday_cxs_url,
    verify_liveness,
)
from between_jobs.api.search_providers import SearchResult


def _http(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _result(**overrides: Any) -> SearchResult:
    base: dict[str, Any] = {
        "provider": "serper",
        "title": "Backend Engineer",
        "company": "Acme",
        "location": "New York, NY",
        "remote": None,
        "apply_url": "https://boards.greenhouse.io/acme/jobs/555",
        "snippet": "",
        "posted_at": None,
        "source_tier": 1.0,
    }
    base.update(overrides)
    return SearchResult(**base)


def _url(raw: str) -> httpx.URL:
    return httpx.URL(raw)


# ── _is_listing_page ─────────────────────────────────────────────────────


def test_is_listing_page_matches_aggregator_title() -> None:
    assert _is_listing_page("300+ Machine Learning Engineer Jobs in Austin")


def test_is_listing_page_does_not_match_a_real_title() -> None:
    assert not _is_listing_page("Senior Machine Learning Engineer")


def test_is_listing_page_matches_openings_variant() -> None:
    assert _is_listing_page("42 Openings in Data Science")


# ── _as_dict ──────────────────────────────────────────────────────────────


def test_as_dict_passes_through_a_real_dict() -> None:
    assert _as_dict({"a": 1}) == {"a": 1}


def test_as_dict_defaults_non_dict_to_empty() -> None:
    assert _as_dict(None) == {}
    assert _as_dict("not a dict") == {}


# ── Ashby ────────────────────────────────────────────────────────────────


def _ashby_handler(jobs: list[dict[str, Any]]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.ashbyhq.com/posting-api/job-board/distyl"
        return httpx.Response(200, json={"jobs": jobs, "apiVersion": "1"})

    return handler


_ASHBY_REAL_JOB = {
    "id": "0451eb82-21cd-4d72-8e03-c148c53e745d",
    "location": "Remote (US)",
    "address": {"postalAddress": {"addressCountry": "USA"}},
}


async def test_probe_ashby_alive_job_is_kept_with_location() -> None:
    http = _http(_ashby_handler([_ASHBY_REAL_JOB]))
    url = _url("https://jobs.ashbyhq.com/distyl/0451eb82-21cd-4d72-8e03-c148c53e745d")

    result = await _probe_ashby(http, url, {})

    assert result.verdict == "keep"
    assert result.location == "Remote (US), USA"


async def test_probe_ashby_absent_job_id_is_dead() -> None:
    http = _http(_ashby_handler([_ASHBY_REAL_JOB]))
    url = _url("https://jobs.ashbyhq.com/distyl/00000000-0000-0000-0000-000000000000")

    result = await _probe_ashby(http, url, {})

    assert result.verdict == "dead"


async def test_probe_ashby_org_api_failure_is_inconclusive() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    http = _http(handler)
    url = _url("https://jobs.ashbyhq.com/distyl/0451eb82-21cd-4d72-8e03-c148c53e745d")

    result = await _probe_ashby(http, url, {})

    assert result.verdict == "keep"


async def test_probe_ashby_unrecognized_path_is_kept() -> None:
    http = _http(lambda r: httpx.Response(200))
    url = _url("https://jobs.ashbyhq.com/distyl")

    result = await _probe_ashby(http, url, {})

    assert result.verdict == "keep"


async def test_probe_ashby_shares_one_org_fetch_across_two_jobs() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"jobs": [_ASHBY_REAL_JOB]})

    http = _http(handler)
    cache: dict[str, Any] = {}
    url1 = _url("https://jobs.ashbyhq.com/distyl/0451eb82-21cd-4d72-8e03-c148c53e745d")
    url2 = _url("https://jobs.ashbyhq.com/distyl/00000000-0000-0000-0000-000000000000")

    r1 = await _probe_ashby(http, url1, cache)
    r2 = await _probe_ashby(http, url2, cache)

    assert r1.verdict == "keep"
    assert r2.verdict == "dead"
    assert call_count == 1


def test_ashby_location_handles_missing_address() -> None:
    assert _ashby_location({"location": "Remote"}) == "Remote"


def test_ashby_location_returns_none_when_nothing_present() -> None:
    assert _ashby_location({}) is None


# ── Lever ────────────────────────────────────────────────────────────────

_LEVER_REAL_POSTING = {
    "id": "ddad0cc6-1967-458c-a868-e14116ee139c",
    "categories": {"location": "Massachusetts - Boston"},
}


async def test_probe_lever_alive_job_is_kept_with_location() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.lever.co/v0/postings/veeva?mode=json"
        return httpx.Response(200, json=[_LEVER_REAL_POSTING])

    http = _http(handler)
    url = _url("https://jobs.lever.co/veeva/ddad0cc6-1967-458c-a868-e14116ee139c")

    result = await _probe_lever(http, url, {})

    assert result.verdict == "keep"
    assert result.location == "Massachusetts - Boston"


async def test_probe_lever_absent_job_id_is_dead() -> None:
    http = _http(lambda r: httpx.Response(200, json=[_LEVER_REAL_POSTING]))
    url = _url("https://jobs.lever.co/veeva/00000000-0000-0000-0000-000000000000")

    result = await _probe_lever(http, url, {})

    assert result.verdict == "dead"


async def test_probe_lever_non_array_body_is_inconclusive() -> None:
    http = _http(lambda r: httpx.Response(200, json={"unexpected": "shape"}))
    url = _url("https://jobs.lever.co/veeva/ddad0cc6-1967-458c-a868-e14116ee139c")

    result = await _probe_lever(http, url, {})

    assert result.verdict == "keep"


async def test_probe_lever_shielded_cache_survives_a_sibling_timeout() -> None:
    """Real regression, found on a live run against Veeva's real 12.56MB
    Lever response (884 open postings): two jobs from the same company
    share one org-level fetch task. Without `asyncio.shield` in
    `_probe_lever`, cancelling ONE caller's own `wait_for(...)` while
    it's suspended awaiting the shared task ALSO cancels that shared
    task (asyncio propagates a cancelled task's cancellation to whatever
    it's currently awaiting) -- breaking every OTHER caller still
    legitimately awaiting the identical fetch, which then sees an
    uncaught `CancelledError` instead of its own real result.

    Deliberately does not go through `verify_liveness`'s own uniform
    per-job timeout (both jobs would then race the exact same timer and
    the bug wouldn't reliably reproduce) -- staggers a short-budget
    caller against a long-budget one sharing the same cache entry,
    mirroring the real, uneven latency that let one caller's timeout
    fire while another was still legitimately in flight."""
    import asyncio as _asyncio
    import contextlib

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        await _asyncio.sleep(0.3)
        return httpx.Response(200, json=[_LEVER_REAL_POSTING])

    http = _http(slow_handler)
    cache: dict[str, Any] = {}
    url = _url("https://jobs.lever.co/veeva/ddad0cc6-1967-458c-a868-e14116ee139c")

    long_budget_task = _asyncio.ensure_future(_probe_lever(http, url, cache))
    await _asyncio.sleep(0)  # let it populate the shared cache entry and start awaiting it

    with contextlib.suppress(TimeoutError):
        await _asyncio.wait_for(_probe_lever(http, url, cache), timeout=0.05)

    result = await long_budget_task
    assert result.verdict == "keep"


# ── Greenhouse ───────────────────────────────────────────────────────────


async def test_probe_greenhouse_404_is_dead() -> None:
    http = _http(lambda r: httpx.Response(404, json={"status": 404, "error": "Job not found"}))
    url = _url("https://boards.greenhouse.io/cottinghambutler/jobs/4018409008")

    result = await _probe_greenhouse(http, url)

    assert result.verdict == "dead"


async def test_probe_greenhouse_200_is_kept_with_location() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert (
            str(request.url)
            == "https://boards-api.greenhouse.io/v1/boards/cottinghambutler/jobs/4018409008"
        )
        return httpx.Response(200, json={"location": {"name": "Madison, Wisconsin, United States"}})

    http = _http(handler)
    url = _url("https://boards.greenhouse.io/cottinghambutler/jobs/4018409008")

    result = await _probe_greenhouse(http, url)

    assert result.verdict == "keep"
    assert result.location == "Madison, Wisconsin, United States"


async def test_probe_greenhouse_job_boards_host_variant_is_matched() -> None:
    http = _http(lambda r: httpx.Response(200, json={"location": {"name": "Remote"}}))
    url = _url("https://job-boards.greenhouse.io/acme/jobs/1")

    result = await _probe_greenhouse(http, url)

    assert result.verdict == "keep"


async def test_probe_greenhouse_falls_through_to_chase_on_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "boards-api.greenhouse.io" in str(request.url):
            return httpx.Response(500)
        return httpx.Response(404)

    http = _http(handler)
    url = _url("https://boards.greenhouse.io/acme/jobs/1")

    result = await _probe_greenhouse(http, url)

    assert result.verdict == "dead"


async def test_probe_greenhouse_error_true_redirect_target_is_dead() -> None:
    """A real, disclosed generic-fallback weakness (found live for a
    plain Greenhouse HEAD-chase): a dead posting's redirect target may be
    RELATIVE (not followed, per the https-only rule) leaving the final
    observed status at 302 -- this only matters if this specific
    error=true check doesn't catch it first, exercised here with an
    ABSOLUTE redirect target so the check has something to match."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "boards-api.greenhouse.io" in str(request.url):
            return httpx.Response(500)
        return httpx.Response(
            302, headers={"location": "https://boards.greenhouse.io/acme?error=true"}
        )

    http = _http(handler)
    url = _url("https://boards.greenhouse.io/acme/jobs/1")

    result = await _probe_greenhouse(http, url)

    assert result.verdict == "dead"


async def test_probe_greenhouse_unmatched_path_falls_through_to_chase() -> None:
    http = _http(lambda r: httpx.Response(200))
    url = _url("https://boards.greenhouse.io/acme/some-other-path")

    result = await _probe_greenhouse(http, url)

    assert result.verdict == "keep"


# ── Workday ──────────────────────────────────────────────────────────────


def test_workday_cxs_url_strips_locale_and_apply_suffix() -> None:
    url = _url(
        "https://hp.wd5.myworkdayjobs.com/en-US/ExternalCareerSite/job/"
        "Ness-Ziona/Strategic-Procurement-Account-Lead_3165924-1/apply/useMyLastApplication"
    )
    assert (
        _workday_cxs_url(url)
        == "https://hp.wd5.myworkdayjobs.com/wday/cxs/hp/ExternalCareerSite/job/"
        "Strategic-Procurement-Account-Lead_3165924-1"
    )


def test_workday_cxs_url_accepts_details_marker() -> None:
    url = _url("https://hp.wd5.myworkdayjobs.com/ExternalCareerSite/details/Some-Slug_123")
    assert (
        _workday_cxs_url(url)
        == "https://hp.wd5.myworkdayjobs.com/wday/cxs/hp/ExternalCareerSite/job/Some-Slug_123"
    )


def test_workday_cxs_url_returns_none_when_no_marker_present() -> None:
    url = _url("https://hp.wd5.myworkdayjobs.com/ExternalCareerSite/nonsense")
    assert _workday_cxs_url(url) is None


def test_workday_cxs_url_returns_none_when_marker_is_first_segment() -> None:
    url = _url("https://hp.wd5.myworkdayjobs.com/job/Some-Slug_123")
    assert _workday_cxs_url(url) is None


async def test_probe_workday_404_is_dead() -> None:
    http = _http(lambda r: httpx.Response(404))
    url = _url("https://hp.wd5.myworkdayjobs.com/ExternalCareerSite/job/Some-Slug_999")

    result = await _probe_workday(http, url)

    assert result.verdict == "dead"


async def test_probe_workday_403_s22_error_code_is_dead() -> None:
    http = _http(
        lambda r: httpx.Response(403, json={"errorCode": "S22", "message": "permission denied"})
    )
    url = _url("https://hp.wd5.myworkdayjobs.com/ExternalCareerSite/job/Strategic-Lead_3165924-1")

    result = await _probe_workday(http, url)

    assert result.verdict == "dead"


async def test_probe_workday_403_permission_denied_message_is_dead() -> None:
    http = _http(
        lambda r: httpx.Response(403, json={"errorCode": "S99", "message": "Permission Denied"})
    )
    url = _url("https://hp.wd5.myworkdayjobs.com/ExternalCareerSite/job/Slug_1")

    result = await _probe_workday(http, url)

    assert result.verdict == "dead"


async def test_probe_workday_bare_403_is_inconclusive() -> None:
    """A real, live-confirmed distinction: 403 alone (no S22/permission-
    denied signature) is a tenant firewall/WAF block, not a per-job dead
    signal -- e.g. the real live-confirmed 404/S21 case for a bogus id
    against an otherwise-healthy tenant never reaches this branch at all
    (S21 arrives via a plain 404), so a bare 403 here must stay
    inconclusive rather than being misread as dead."""
    http = _http(lambda r: httpx.Response(403, json={"errorCode": "S99", "message": "blocked"}))
    url = _url("https://hp.wd5.myworkdayjobs.com/ExternalCareerSite/job/Slug_1")

    result = await _probe_workday(http, url)

    assert result.verdict == "keep"


async def test_probe_workday_200_can_apply_false_is_dead() -> None:
    http = _http(lambda r: httpx.Response(200, json={"jobPostingInfo": {"canApply": False}}))
    url = _url("https://hp.wd5.myworkdayjobs.com/ExternalCareerSite/job/Slug_1")

    result = await _probe_workday(http, url)

    assert result.verdict == "dead"


async def test_probe_workday_200_is_kept_with_location() -> None:
    http = _http(
        lambda r: httpx.Response(
            200,
            json={
                "jobPostingInfo": {
                    "canApply": True,
                    "location": "Singapore, South West, Singapore",
                    "country": {"descriptor": "Singapore"},
                }
            },
        )
    )
    url = _url("https://hp.wd5.myworkdayjobs.com/ExternalCareerSite/job/Slug_1")

    result = await _probe_workday(http, url)

    assert result.verdict == "keep"
    assert result.location == "Singapore, South West, Singapore, Singapore"


async def test_probe_workday_unconstructible_url_is_inconclusive() -> None:
    http = _http(lambda r: httpx.Response(200))
    url = _url("https://hp.wd5.myworkdayjobs.com/nonsense")

    result = await _probe_workday(http, url)

    assert result.verdict == "keep"


# ── Avature ──────────────────────────────────────────────────────────────


async def test_probe_avature_path_style_alive_is_kept() -> None:
    http = _http(lambda r: httpx.Response(200))
    url = _url("https://apply.deloitte.com/en_US/careers/JobDetail/Some-Role/364609")

    result = await _probe_avature(http, url)

    assert result.verdict == "keep"


async def test_probe_avature_error_redirect_is_dead() -> None:
    http = _http(
        lambda r: httpx.Response(
            302, headers={"location": "https://apply.deloitte.com/en_US/careers/Error"}
        )
    )
    url = _url("https://apply.deloitte.com/en_US/careers/JobDetail/Some-Role/999999999")

    result = await _probe_avature(http, url)

    assert result.verdict == "dead"


async def test_probe_avature_query_style_job_id_is_recognized() -> None:
    http = _http(
        lambda r: httpx.Response(302, headers={"location": "https://ibm.avature.net/Error"})
    )
    url = _url("https://ibm.avature.net/careers/SearchJobs?jobId=999999")

    result = await _probe_avature(http, url)

    assert result.verdict == "dead"


async def test_probe_avature_twosigma_custom_domain_is_recognized() -> None:
    http = _http(
        lambda r: httpx.Response(302, headers={"location": "https://careers.twosigma.com/Error"})
    )
    url = _url("https://careers.twosigma.com/JobDetail/Some-Role/999999")

    result = await _probe_avature(http, url)

    assert result.verdict == "dead"


async def test_probe_avature_non_job_url_is_kept() -> None:
    http = _http(lambda r: httpx.Response(200))
    url = _url("https://ibm.avature.net/careers/SomeOtherPage")

    result = await _probe_avature(http, url)

    assert result.verdict == "keep"


async def test_probe_avature_redirect_to_non_error_path_is_kept() -> None:
    http = _http(
        lambda r: httpx.Response(
            301, headers={"location": "https://ibm.avature.net/careers/Somewhere"}
        )
    )
    url = _url("https://ibm.avature.net/careers/JobDetail/Role/123")

    result = await _probe_avature(http, url)

    assert result.verdict == "keep"


# ── Google careers ───────────────────────────────────────────────────────


async def test_probe_google_populated_og_title_is_kept() -> None:
    http = _http(
        lambda r: httpx.Response(
            200, text='<meta property="og:title" content="DV360 Account Manager">'
        )
    )
    url = _url("https://www.google.com/about/careers/applications/jobs/results/131529-dv360")

    result = await _probe_google(http, url)

    assert result.verdict == "keep"


async def test_probe_google_empty_og_title_is_dead() -> None:
    http = _http(lambda r: httpx.Response(200, text='<meta property="og:title" content="">'))
    url = _url("https://www.google.com/about/careers/applications/jobs/results/999999-bogus")

    result = await _probe_google(http, url)

    assert result.verdict == "dead"


async def test_probe_google_missing_tag_is_kept() -> None:
    http = _http(lambda r: httpx.Response(200, text="<html><body>no meta here</body></html>"))
    url = _url("https://www.google.com/about/careers/applications/jobs/results/999999-bogus")

    result = await _probe_google(http, url)

    assert result.verdict == "keep"


# ── D.E. Shaw ────────────────────────────────────────────────────────────

_DESHAW_ALIVE_HTML = (
    '<script id="__NEXT_DATA__" type="application/json">'
    '{"props":{"pageProps":{"jobData":{"id":"1"},"routes":{}}}}'
    "</script>"
)
_DESHAW_DEAD_HTML = (
    '<script id="__NEXT_DATA__" type="application/json">'
    '{"props":{"pageProps":{"redirectToCareers":true,"routes":{}}}}'
    "</script>"
)


async def test_probe_deshaw_job_data_present_is_kept() -> None:
    http = _http(lambda r: httpx.Response(200, text=_DESHAW_ALIVE_HTML))
    url = _url("https://www.deshaw.com/careers/receptionist-singapore-6012")

    result = await _probe_deshaw(http, url)

    assert result.verdict == "keep"


async def test_probe_deshaw_redirect_to_careers_no_job_data_is_dead() -> None:
    http = _http(lambda r: httpx.Response(200, text=_DESHAW_DEAD_HTML))
    url = _url("https://www.deshaw.com/careers/definitely-not-a-real-job")

    result = await _probe_deshaw(http, url)

    assert result.verdict == "dead"


async def test_probe_deshaw_missing_next_data_is_kept() -> None:
    http = _http(lambda r: httpx.Response(200, text="<html></html>"))
    url = _url("https://www.deshaw.com/careers/some-role")

    result = await _probe_deshaw(http, url)

    assert result.verdict == "keep"


# ── SuccessFactors ───────────────────────────────────────────────────────

_SF_ALIVE_HTML = (
    '<div class="jobDisplayShell" itemscope itemtype="http://schema.org/JobPosting">'
    '<meta itemprop="addressLocality" content="Camana Bay">'
    '<meta itemprop="addressRegion" content="KY">'
    '<meta itemprop="addressCountry" content="Cayman Islands">'
    "</div>"
)
_SF_DEAD_CLOSED_HTML = (
    "<html><body>"
    "The Job is no longer available"
    '<script>var careerSiteCompanyId = "12345";</script>'
    '<script src="https://rmkcdn.successfactors.com/assets/main.js"></script>'
    "</body></html>"
)
_SF_UNRELATED_PAGE_HTML = "<html><body>404 -- page not found</body></html>"


async def test_probe_successfactors_alive_page_is_kept_with_location() -> None:
    http = _http(lambda r: httpx.Response(200, text=_SF_ALIVE_HTML))
    url = _url("https://careers.ey.com/ey/job/Some-Role-KY11106/1421402433/")

    result = await _probe_successfactors(http, url)

    assert result.verdict == "keep"
    assert result.location == "Camana Bay, KY, Cayman Islands"


async def test_probe_successfactors_closed_job_no_itemtype_is_dead() -> None:
    """The n8n-documented dead signal -- real for a job that DID exist
    and was later closed (confirmed live against 6 real closed EY
    postings, all with this exact body text), corroborated by a real SF
    Career Site Builder body fingerprint (confirmed live on
    careers.ey.com and jobs.exxonmobil.com)."""
    http = _http(lambda r: httpx.Response(200, text=_SF_DEAD_CLOSED_HTML))
    url = _url("https://careers.ey.com/ey/job/Some-Role/1391530333/")

    result = await _probe_successfactors(http, url)

    assert result.verdict == "dead"


async def test_probe_successfactors_no_itemtype_and_no_fingerprint_falls_back_to_keep() -> None:
    """Dispatch here is path-shape-only, since real SF tenants use
    arbitrary vanity domains with no shared host suffix to dispatch on
    instead -- a coincidentally path-matching but genuinely unrelated
    page (no SF Career Site Builder fingerprint anywhere in the body)
    must never be marked dead just because the itemtype string is also
    absent, matching "inconclusive != dead, always"."""
    http = _http(lambda r: httpx.Response(200, text=_SF_UNRELATED_PAGE_HTML))
    url = _url("https://careers.somecompany.example/job/some-role/123456789/")

    result = await _probe_successfactors(http, url)

    assert result.verdict == "keep"


async def test_probe_successfactors_errorpage_redirect_is_dead() -> None:
    """The real drift found live: a genuinely nonexistent id doesn't
    reproduce the 200/no-itemtype signal at all -- it redirects to
    /errorpage/. n8n's own reference doesn't document this path; this
    port adds it."""
    http = _http(
        lambda r: httpx.Response(
            302, headers={"location": "https://careers.ey.com/errorpage/?errortype=Exception"}
        )
    )
    url = _url("https://careers.ey.com/ey/job/Some-Role/9999999999/")

    result = await _probe_successfactors(http, url)

    assert result.verdict == "dead"


async def test_probe_successfactors_redirect_elsewhere_is_kept() -> None:
    http = _http(
        lambda r: httpx.Response(302, headers={"location": "https://careers.ey.com/ey/login"})
    )
    url = _url("https://careers.ey.com/ey/job/Some-Role/123/")

    result = await _probe_successfactors(http, url)

    assert result.verdict == "keep"


def test_successfactors_location_prefers_three_part_decomposition() -> None:
    body = (
        '<meta itemprop="addressLocality" content="Camana Bay">'
        '<meta itemprop="addressRegion" content="KY">'
        '<meta itemprop="streetAddress" content="Camana Bay, KY, KY11106">'
    )
    assert _successfactors_location(body) == "Camana Bay, KY"


def test_successfactors_location_falls_back_to_street_address() -> None:
    """The real, live-confirmed EY drift: only the combined field
    exists on real pages, never the three separately-named itemprops."""
    body = '<meta itemprop="streetAddress" content="Camana Bay, KY, KY11106">'
    assert _successfactors_location(body) == "Camana Bay, KY, KY11106"


def test_successfactors_location_returns_none_when_nothing_present() -> None:
    assert _successfactors_location("<html></html>") is None


# ── LinkedIn ─────────────────────────────────────────────────────────────


async def test_probe_linkedin_guest_api_404_is_dead() -> None:
    http = _http(lambda r: httpx.Response(404))
    url = _url("https://www.linkedin.com/jobs/view/software-engineer-2792064576")

    result = await _probe_linkedin(http, url)

    assert result.verdict == "dead"


async def test_probe_linkedin_closed_job_text_is_dead() -> None:
    http = _http(
        lambda r: httpx.Response(
            200,
            text='<figcaption class="closed-job__flavor--closed">'
            "No longer accepting applications</figcaption>",
        )
    )
    url = _url("https://www.linkedin.com/jobs/view/software-engineer-3799978060")

    result = await _probe_linkedin(http, url)

    assert result.verdict == "dead"


async def test_probe_linkedin_alive_job_falls_through_to_chase_and_is_kept() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "jobs-guest" in str(request.url):
            return httpx.Response(200, text="<html>a real job</html>")
        return httpx.Response(200)

    http = _http(handler)
    url = _url("https://www.linkedin.com/jobs/view/c-developer-4461092789")

    result = await _probe_linkedin(http, url)

    assert result.verdict == "keep"


async def test_probe_linkedin_expired_jd_redirect_is_dead() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "jobs-guest" in str(request.url):
            return httpx.Response(200, text="<html>ok</html>")
        return httpx.Response(
            302, headers={"location": "https://www.linkedin.com/jobs/expired_jd_redirect"}
        )

    http = _http(handler)
    url = _url("https://www.linkedin.com/jobs/view/some-role-123456")

    result = await _probe_linkedin(http, url)

    assert result.verdict == "dead"


async def test_probe_linkedin_no_id_match_still_runs_chase() -> None:
    http = _http(lambda r: httpx.Response(404))
    url = _url("https://www.linkedin.com/jobs/search/?keywords=engineer")

    result = await _probe_linkedin(http, url)

    assert result.verdict == "dead"


# ── generic fallback ─────────────────────────────────────────────────────


async def test_probe_generic_404_is_dead() -> None:
    http = _http(lambda r: httpx.Response(404))
    url = _url("https://example.com/careers/some-job")

    result = await _probe_generic(http, url)

    assert result.verdict == "dead"


async def test_probe_generic_410_is_dead() -> None:
    http = _http(lambda r: httpx.Response(410))
    url = _url("https://example.com/careers/some-job")

    result = await _probe_generic(http, url)

    assert result.verdict == "dead"


async def test_probe_generic_200_is_kept() -> None:
    http = _http(lambda r: httpx.Response(200))
    url = _url("https://example.com/careers/some-job")

    result = await _probe_generic(http, url)

    assert result.verdict == "keep"


async def test_probe_generic_follows_a_same_origin_relative_redirect() -> None:
    """`_chase` follows a path-relative Location header via
    `httpx.URL.join` (matching n8n's own real `resolveLoc`, which also
    resolves a leading-slash path against the current host) -- landing
    on a 404 here proves relative-redirect-following works end to end,
    not just that absolute https:// targets are followed."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://boards.greenhouse.io/ziprecruiter/jobs/5281310":
            return httpx.Response(302, headers={"location": "/ziprecruiter/gone"})
        return httpx.Response(404)

    http = _http(handler)
    url = _url("https://boards.greenhouse.io/ziprecruiter/jobs/5281310")

    result = await _probe_generic(http, url)

    assert result.verdict == "dead"


async def test_probe_generic_relative_redirect_to_a_live_page_is_kept() -> None:
    """A real, disclosed weakness confirmed live: a redirect (even one
    this port correctly follows) that lands on a genuinely-200 fallback/
    board page -- e.g. Greenhouse's own generic "job not found" UX --
    can't be told apart from a real live posting by status code alone.
    Harmless in practice since real Greenhouse URLs always route through
    `_probe_greenhouse` instead, which uses the JSON detail API's own
    unambiguous 404, never reaching this path."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://boards.greenhouse.io/ziprecruiter/jobs/5281310":
            return httpx.Response(302, headers={"location": "/ziprecruiter?error=true"})
        return httpx.Response(200)

    http = _http(handler)
    url = _url("https://boards.greenhouse.io/ziprecruiter/jobs/5281310")

    result = await _probe_generic(http, url)

    assert result.verdict == "keep"


async def test_probe_generic_follows_absolute_https_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://example.com/first":
            return httpx.Response(301, headers={"location": "https://example.com/second"})
        return httpx.Response(404)

    http = _http(handler)
    url = _url("https://example.com/first")

    result = await _probe_generic(http, url)

    assert result.verdict == "dead"


async def test_probe_generic_does_not_follow_http_redirect() -> None:
    http = _http(lambda r: httpx.Response(302, headers={"location": "http://example.com/insecure"}))
    url = _url("https://example.com/first")

    result = await _probe_generic(http, url)

    assert result.verdict == "keep"


async def test_probe_generic_redirect_cap_overrun_is_inconclusive() -> None:
    count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        count["n"] += 1
        return httpx.Response(302, headers={"location": f"https://example.com/hop{count['n']}"})

    http = _http(handler)
    url = _url("https://example.com/first")

    result = await _probe_generic(http, url)

    assert result.verdict == "keep"


# ── _chase ───────────────────────────────────────────────────────────────


async def test_chase_returns_zero_status_on_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    http = _http(handler)
    status, _final_url = await _chase(http, _url("https://example.com/x"), method="HEAD")

    assert status == 0


# ── dispatch (_probe) ────────────────────────────────────────────────────


async def test_probe_dispatches_workday_by_host_suffix() -> None:
    http = _http(lambda r: httpx.Response(404))
    result = await _probe(
        http,
        _result(apply_url="https://acme.wd1.myworkdayjobs.com/site/job/Loc/Role_123"),
        {},
        {},
    )
    assert result.verdict == "dead"


async def test_probe_dispatches_successfactors_by_path_shape_not_host() -> None:
    http = _http(lambda r: httpx.Response(200, text=_SF_DEAD_CLOSED_HTML))
    result = await _probe(
        http,
        _result(apply_url="https://careers.somecompany.example/job/some-role/123456789/"),
        {},
        {},
    )
    assert result.verdict == "dead"


async def test_probe_falls_back_to_generic_for_unrecognized_host() -> None:
    http = _http(lambda r: httpx.Response(404))
    result = await _probe(
        http, _result(apply_url="https://randomcareersite.example/jobs/1"), {}, {}
    )
    assert result.verdict == "dead"


async def test_probe_invalid_url_is_kept() -> None:
    http = _http(lambda r: httpx.Response(200))
    result = await _probe(http, _result(apply_url="not-a-url"), {}, {})
    assert result.verdict == "keep"


async def test_probe_non_https_url_is_kept() -> None:
    http = _http(lambda r: httpx.Response(200))
    result = await _probe(
        http, _result(apply_url="http://boards.greenhouse.io/acme/jobs/1"), {}, {}
    )
    assert result.verdict == "keep"


# ── verify_liveness (top-level orchestration) ───────────────────────────


async def test_verify_liveness_drops_dead_jobs_and_counts_them() -> None:
    http = _http(lambda r: httpx.Response(404))
    results = [
        _result(apply_url="https://example.com/jobs/1"),
        _result(apply_url="https://example.com/jobs/2"),
    ]

    alive, dead_removed = await verify_liveness(http, results)

    assert alive == []
    assert dead_removed == 2


async def test_verify_liveness_keeps_alive_jobs_and_sets_link_checked() -> None:
    http = _http(lambda r: httpx.Response(200))
    results = [_result(apply_url="https://example.com/jobs/1", link_checked=False)]

    alive, dead_removed = await verify_liveness(http, results)

    assert dead_removed == 0
    assert len(alive) == 1
    assert alive[0].link_checked is True


async def test_verify_liveness_filters_out_listing_page_titles_before_probing() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should never probe a listing-page title")

    http = _http(handler)
    results = [_result(title="300+ Software Engineer Jobs in Austin")]

    alive, dead_removed = await verify_liveness(http, results)

    assert alive == []
    assert dead_removed == 0  # filtered out before probing, not counted as "dead"


async def test_verify_liveness_backfills_location_and_resets_location_verified() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"location": {"name": "Madison, Wisconsin"}})

    http = _http(handler)
    results = [
        _result(
            apply_url="https://boards.greenhouse.io/acme/jobs/555",
            location="wrong snippet-derived location",
            location_verified=True,
        )
    ]

    alive, _dead_removed = await verify_liveness(http, results)

    assert alive[0].location == "Madison, Wisconsin"
    assert alive[0].location_verified is None


async def test_verify_liveness_does_not_touch_location_when_no_correction_found() -> None:
    http = _http(lambda r: httpx.Response(200))
    results = [
        _result(
            apply_url="https://example.com/jobs/1",
            location="Original Location",
            location_verified=True,
        )
    ]

    alive, _dead_removed = await verify_liveness(http, results)

    assert alive[0].location == "Original Location"
    assert alive[0].location_verified is True


async def test_verify_liveness_probe_exception_fails_open_to_keep() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("unexpected boom")

    http = _http(handler)
    results = [_result(apply_url="https://example.com/jobs/1")]

    alive, dead_removed = await verify_liveness(http, results)

    assert dead_removed == 0
    assert len(alive) == 1


async def test_verify_liveness_empty_input_returns_empty() -> None:
    http = _http(lambda r: httpx.Response(200))
    alive, dead_removed = await verify_liveness(http, [])
    assert alive == []
    assert dead_removed == 0
