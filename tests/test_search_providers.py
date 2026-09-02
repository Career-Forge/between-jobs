"""Tests for the live-search providers (Job Finder P4a/P4b,
live-search-track.md). Fixtures for RemoteOK/Arbeitnow/You.com/Firecrawl/
Serper/Brave are shaped from real live-fetched responses (see
live-search-track.md's own provider reference); USAJobs' and JSearch's
fixtures follow their stable, well-documented public schemas (no real
key was available during this research pass for either -- flagged there
as a re-verify-on-first-real-tick item)."""

from __future__ import annotations

import dataclasses
import json as jsonlib
from typing import Any

import httpx
import pytest

from between_jobs.api.errors import ApiError
from between_jobs.api.search_providers import (
    ProviderCredentials,
    SearchResult,
    _classify_url_tier,
    _deslugify_company,
    _extract_company_from_url,
    build_firecrawl_queries,
    build_serper_queries,
    build_you_com_queries,
    fetch_adzuna,
    fetch_arbeitnow,
    fetch_brave,
    fetch_firecrawl,
    fetch_jsearch,
    fetch_remoteok,
    fetch_serper,
    fetch_usajobs,
    fetch_you_com,
    search_jobs,
)


def _http(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ── URL-tier classifier ──────────────────────────────────────────────────


def test_classify_url_tier_direct_ats_is_tier_1() -> None:
    assert _classify_url_tier("https://boards.greenhouse.io/acme/jobs/1") == (1.0, "ats:greenhouse")
    assert _classify_url_tier("https://acme.wd5.myworkdayjobs.com/careers/job/1") == (
        1.0,
        "ats:workday",
    )


def test_classify_url_tier_curated_board_is_tier_2() -> None:
    assert _classify_url_tier("https://remoteok.com/remote-jobs/123") == (2.0, "curated:remoteok")


def test_classify_url_tier_aggregator_is_tier_3() -> None:
    assert _classify_url_tier("https://www.linkedin.com/jobs/view/123") == (
        3.0,
        "aggregator:linkedin",
    )


def test_classify_url_tier_generic_career_path_is_tier_2_5() -> None:
    tier, _ = _classify_url_tier("https://acme.com/careers/openings/123")
    assert tier == 2.5


def test_classify_url_tier_unknown_domain_is_tier_4() -> None:
    assert _classify_url_tier("https://acme-blog.example.com/post/1") == (4.0, "unknown")


def test_classify_url_tier_empty_url_is_tier_4() -> None:
    assert _classify_url_tier("") == (4.0, "invalid")


# ── company-from-URL extraction ──────────────────────────────────────────


def test_extract_company_from_url_greenhouse() -> None:
    assert _extract_company_from_url("https://boards.greenhouse.io/acme/jobs/1") == "acme"


def test_extract_company_from_url_workday() -> None:
    assert _extract_company_from_url("https://acme.wd5.myworkdayjobs.com/careers/job/1") == "acme"


def test_extract_company_from_url_falls_back_to_host() -> None:
    assert _extract_company_from_url("https://www.example-corp.com/careers") == "example-corp"


def test_deslugify_company_title_cases_and_despaces() -> None:
    assert _deslugify_company("wisdom-ai") == "Wisdom Ai"
    assert _deslugify_company(None) is None
    assert _deslugify_company("") is None


# ── query builders ────────────────────────────────────────────────────────


def test_build_serper_queries_deterministic_and_capped() -> None:
    queries = build_serper_queries("data scientist", location="Berlin, DE")
    assert len(queries) == 3
    assert all('"data scientist"' in q for q in queries)
    assert all('"Berlin"' in q for q in queries)


def test_build_serper_queries_company_scoped_raises_cap() -> None:
    queries = build_serper_queries("swe", companies=["Meta", "Notion"])
    assert len(queries) <= 8
    assert any("Meta" in q for q in queries)
    assert any("Notion" in q for q in queries)


def test_build_serper_queries_empty_role_and_no_companies_returns_empty() -> None:
    assert build_serper_queries("") == []


def test_build_you_com_queries_deduped() -> None:
    queries = build_you_com_queries("swe")
    assert len(queries) == len(set(q.lower() for q in queries))
    assert len(queries) <= 6


def test_build_firecrawl_queries_remote_only_adds_extra_query() -> None:
    without_remote = build_firecrawl_queries("swe")
    with_remote = build_firecrawl_queries("swe", remote_only=True)
    assert len(with_remote) >= len(without_remote)
    assert any("weworkremotely" in q for q in with_remote)


# ── RemoteOK (no auth) ───────────────────────────────────────────────────


def _remoteok_job(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "123",
        "position": "Senior Backend Engineer",
        "company": "Acme",
        "tags": ["python", "backend"],
        "description": "<p>Great role, no sponsorship provided.</p>",
        "location": "",
        "salary_min": 120000,
        "salary_max": 160000,
        "date": "2026-08-30T00:00:00+00:00",
        "url": "https://remoteok.com/remote-jobs/123",
    }
    base.update(overrides)
    return base


async def test_fetch_remoteok_maps_real_shaped_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = [{"legal": "..."}, _remoteok_job()]
        return httpx.Response(200, json=body)

    results = await fetch_remoteok(_http(handler), query="backend engineer")

    assert len(results) == 1
    r = results[0]
    assert r.provider == "remoteok"
    assert r.title == "Senior Backend Engineer"
    assert r.location == "Remote"  # falls back when empty
    assert r.remote is True
    assert r.salary_min == 120000
    assert r.salary_currency == "USD"
    assert r.source_tier == 2.0


async def test_fetch_remoteok_filters_by_query_keywords() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = [
            {"legal": "..."},
            _remoteok_job(position="Data Scientist", tags=["ml"]),
            _remoteok_job(position="Frontend Engineer", tags=["react"]),
        ]
        return httpx.Response(200, json=body)

    results = await fetch_remoteok(_http(handler), query="data scientist")

    assert len(results) == 1
    assert results[0].title == "Data Scientist"


async def test_fetch_remoteok_skips_entries_without_apply_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"legal": "..."}, _remoteok_job(url="", apply_url="")])

    results = await fetch_remoteok(_http(handler), query="")

    assert results == []


async def test_fetch_remoteok_http_error_raises_provider_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(ApiError) as exc_info:
        await fetch_remoteok(_http(handler), query="engineer")
    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"


# ── Arbeitnow (no auth) ──────────────────────────────────────────────────


def _arbeitnow_job(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "slug": "backend-engineer-acme-berlin-1",
        "company_name": "Acme",
        "title": "Backend Engineer",
        "remote": False,
        "url": "https://www.arbeitnow.com/jobs/companies/acme/backend-engineer-1",
        "tags": ["python"],
        "job_types": ["Full-time"],
        "location": "Berlin, Germany",
        "created_at": 1788144930,
    }
    base.update(overrides)
    return base


async def test_fetch_arbeitnow_maps_real_shaped_fields_and_converts_timestamp() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page")
        if page == "1":
            return httpx.Response(200, json={"data": [_arbeitnow_job()], "meta": {}})
        return httpx.Response(200, json={"data": [], "meta": {}})

    results = await fetch_arbeitnow(_http(handler), query="backend engineer")

    assert len(results) == 1
    r = results[0]
    assert r.provider == "arbeitnow"
    assert r.location == "Berlin, Germany"
    assert r.posted_at == "2026-08-31T02:55:30+00:00"


async def test_fetch_arbeitnow_remote_true_from_field_even_without_remote_keyword() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page")
        if page == "1":
            return httpx.Response(
                200, json={"data": [_arbeitnow_job(remote=True, location="Anywhere")], "meta": {}}
            )
        return httpx.Response(200, json={"data": [], "meta": {}})

    results = await fetch_arbeitnow(_http(handler), query="")

    assert results[0].remote is True


async def test_fetch_arbeitnow_stops_pagination_on_short_page() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        page = request.url.params.get("page")
        if page == "1":
            return httpx.Response(
                200, json={"data": [_arbeitnow_job(slug=str(i)) for i in range(5)], "meta": {}}
            )
        raise AssertionError("should not fetch page 2 after a short page 1")

    results = await fetch_arbeitnow(_http(handler), query="")

    assert calls["n"] == 1
    assert len(results) == 5


async def test_fetch_arbeitnow_client_side_filters_by_query() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page")
        if page == "1":
            return httpx.Response(
                200,
                json={
                    "data": [
                        _arbeitnow_job(title="Backend Engineer"),
                        _arbeitnow_job(title="Marketing Manager", slug="mm-1"),
                    ],
                    "meta": {},
                },
            )
        return httpx.Response(200, json={"data": [], "meta": {}})

    results = await fetch_arbeitnow(_http(handler), query="backend engineer")

    assert len(results) == 1
    assert results[0].title == "Backend Engineer"


# ── USAJobs (BYOK) ────────────────────────────────────────────────────────


def _usajobs_item(**overrides: Any) -> dict[str, Any]:
    descriptor: dict[str, Any] = {
        "PositionTitle": "Data Scientist",
        "OrganizationName": "Department of Commerce",
        "ApplyURI": ["https://www.usajobs.gov/job/123456"],
        "PositionLocation": [{"LocationName": "Washington, DC"}],
        "UserArea": {"Details": {"JobSummary": "Federal data science role."}},
        "PositionRemuneration": [{"MinimumRange": "90000", "MaximumRange": "120000"}],
        "PublicationStartDate": "2026-08-20",
    }
    descriptor.update(overrides)
    return {"MatchedObjectDescriptor": descriptor}


async def test_fetch_usajobs_sends_correct_auth_headers() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200, json={"SearchResult": {"SearchResultItems": []}})

    await fetch_usajobs(
        _http(handler), query="data scientist", api_key="k", email="dev@example.com"
    )

    assert captured["authorization-key"] == "k"
    assert captured["user-agent"] == "dev@example.com"


async def test_fetch_usajobs_maps_real_shaped_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"SearchResult": {"SearchResultItems": [_usajobs_item()]}})

    results = await fetch_usajobs(
        _http(handler), query="data scientist", api_key="k", email="dev@example.com"
    )

    assert len(results) == 1
    r = results[0]
    assert r.title == "Data Scientist"
    assert r.company == "Department of Commerce"
    assert r.location == "Washington, DC"
    assert r.apply_url == "https://www.usajobs.gov/job/123456"
    assert r.salary_min == 90000
    assert r.salary_max == 120000
    assert r.salary_currency == "USD"
    assert r.sponsorship_signal == "explicit_no"
    assert r.source_tier == 1.5


async def test_fetch_usajobs_skips_items_without_apply_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "SearchResult": {"SearchResultItems": [_usajobs_item(ApplyURI=[], PositionURI="")]}
            },
        )

    results = await fetch_usajobs(_http(handler), query="x", api_key="k", email="dev@example.com")

    assert results == []


async def test_fetch_usajobs_rejected_key_raises_provider_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    with pytest.raises(ApiError) as exc_info:
        await fetch_usajobs(_http(handler), query="x", api_key="bad", email="dev@example.com")
    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"


# ── Adzuna (BYOK, 2-secret) ───────────────────────────────────────────────


def _adzuna_result(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "129698749",
        "title": "Data Engineer",
        "description": "Great data role, no sponsorship offered.",
        "created": "2026-08-20T18:07:39Z",
        "redirect_url": "https://www.adzuna.com/jobs/land/ad/129698749",
        "location": {"display_name": "New York, NY", "area": ["US", "New York"]},
        "category": {"tag": "it-jobs", "label": "IT Jobs"},
        "company": {"display_name": "Acme Corp"},
        "salary_min": 120000,
        "salary_max": 160000,
        "salary_is_predicted": "0",
    }
    base.update(overrides)
    return base


async def test_fetch_adzuna_maps_real_shaped_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/jobs/us/search/1" in str(request.url)
        return httpx.Response(200, json={"count": 1, "results": [_adzuna_result()]})

    results = await fetch_adzuna(_http(handler), query="engineer", app_id="id", app_key="key")

    assert len(results) == 1
    r = results[0]
    assert r.provider == "adzuna"
    assert r.company == "Acme Corp"
    assert r.location == "New York, NY"
    assert r.apply_url == "https://www.adzuna.com/jobs/land/ad/129698749"
    assert r.salary_min == 120000
    assert r.salary_currency == "USD"  # derived from country="us"
    assert r.source_tier == 1.5


async def test_fetch_adzuna_derives_currency_from_country() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": 1, "results": [_adzuna_result()]})

    results = await fetch_adzuna(
        _http(handler), query="engineer", app_id="id", app_key="key", country="de"
    )

    assert results[0].salary_currency == "EUR"


async def test_fetch_adzuna_sends_app_id_and_app_key_as_query_params() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.url.params)
        return httpx.Response(200, json={"count": 0, "results": []})

    await fetch_adzuna(_http(handler), query="engineer", app_id="my-id", app_key="my-key")

    assert captured["app_id"] == "my-id"
    assert captured["app_key"] == "my-key"


async def test_fetch_adzuna_skips_results_without_redirect_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": 1, "results": [_adzuna_result(redirect_url="")]})

    results = await fetch_adzuna(_http(handler), query="engineer", app_id="id", app_key="key")

    assert results == []


async def test_fetch_adzuna_rejected_key_raises_provider_unavailable() -> None:
    """Adzuna signals auth failure with 410, not 401/403 -- confirmed
    against its own live OpenAPI spec."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(410, json={})

    with pytest.raises(ApiError) as exc_info:
        await fetch_adzuna(_http(handler), query="x", app_id="bad", app_key="bad")
    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"


async def test_fetch_adzuna_http_error_raises_provider_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(ApiError) as exc_info:
        await fetch_adzuna(_http(handler), query="engineer", app_id="id", app_key="key")
    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"


# ── You.com (BYOK) ────────────────────────────────────────────────────────


async def test_fetch_you_com_maps_and_classifies_results() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] > 1:  # build_you_com_queries fires several sub-queries
            return httpx.Response(200, json={"results": {"web": []}})
        return httpx.Response(
            200,
            json={
                "results": {
                    "web": [
                        {
                            "url": "https://boards.greenhouse.io/wisdom-ai/jobs/1",
                            "title": "Senior Engineer",
                            "snippets": ["Great role in Berlin."],
                            "page_age": "2026-08-20",
                        }
                    ]
                }
            },
        )

    results = await fetch_you_com(_http(handler), query="engineer", api_key="k")

    assert len(results) == 1
    r = results[0]
    assert r.provider == "you_com"
    assert r.company == "Wisdom Ai"
    assert r.source_tier == 1.0
    assert r.location is None  # no gazetteer extraction, deliberately


async def test_fetch_you_com_no_query_returns_empty() -> None:
    async def unreachable(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not be called")

    results = await fetch_you_com(_http(unreachable), query="", api_key="k")

    assert results == []


async def test_fetch_you_com_error_raises_provider_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    with pytest.raises(ApiError) as exc_info:
        await fetch_you_com(_http(handler), query="engineer", api_key="k")
    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"


# ── Firecrawl (BYOK) ──────────────────────────────────────────────────────


async def test_fetch_firecrawl_sends_limit_and_tbs_in_body() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(jsonlib.loads(request.content))
        return httpx.Response(200, json={"data": {"web": []}})

    await fetch_firecrawl(_http(handler), query="engineer", api_key="k")

    assert captured["limit"] == 10
    assert "tbs" in captured


async def test_fetch_firecrawl_remote_only_raises_limit_to_25() -> None:
    captured: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(jsonlib.loads(request.content)["limit"])
        return httpx.Response(200, json={"data": {"web": []}})

    await fetch_firecrawl(_http(handler), query="engineer", api_key="k", remote_only=True)

    assert all(limit == 25 for limit in captured)


async def test_fetch_firecrawl_maps_real_shaped_fields() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] > 1:  # build_firecrawl_queries fires several sub-queries
            return httpx.Response(200, json={"data": {"web": []}})
        return httpx.Response(
            200,
            json={
                "data": {
                    "web": [
                        {
                            "url": "https://jobs.lever.co/acme/1",
                            "title": "Platform Engineer",
                            "description": "Remote friendly role.",
                            "metadata": {"publishedTime": "2026-08-25"},
                        }
                    ]
                }
            },
        )

    results = await fetch_firecrawl(_http(handler), query="engineer", api_key="k")

    assert len(results) == 1
    r = results[0]
    assert r.company == "Acme"
    assert r.source_tier == 1.0
    assert r.remote is True  # "remote friendly" text-detected


# ── Serper (BYOK) ──────────────────────────────────────────────────────────


async def test_fetch_serper_maps_real_shaped_fields() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] > 1:  # build_serper_queries fires several sub-queries
            return httpx.Response(200, json={"organic": []})
        return httpx.Response(
            200,
            json={
                "organic": [
                    {
                        "title": "Data Engineer",
                        "link": "https://boards.greenhouse.io/acme/jobs/1",
                        "snippet": "Great data role.",
                        "date": "Mar 10, 2026",
                    }
                ]
            },
        )

    results = await fetch_serper(_http(handler), query="engineer", api_key="k")

    assert len(results) == 1
    r = results[0]
    assert r.provider == "serper"
    assert r.company == "Acme"
    assert r.source_tier == 1.0
    assert r.posted_at == "Mar 10, 2026"
    assert r.location is None  # no gazetteer extraction, deliberately


async def test_fetch_serper_sends_correct_auth_header() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200, json={"organic": []})

    await fetch_serper(_http(handler), query="engineer", api_key="serper-key")

    assert captured["x-api-key"] == "serper-key"


async def test_fetch_serper_no_query_returns_empty() -> None:
    async def unreachable(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not be called")

    results = await fetch_serper(_http(unreachable), query="", api_key="k")

    assert results == []


async def test_fetch_serper_error_raises_provider_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    with pytest.raises(ApiError) as exc_info:
        await fetch_serper(_http(handler), query="engineer", api_key="k")
    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"


# ── Brave (BYOK) ───────────────────────────────────────────────────────────


async def test_fetch_brave_maps_real_shaped_fields() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] > 1:
            return httpx.Response(200, json={"web": {"results": []}})
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "Backend Engineer",
                            "url": "https://jobs.lever.co/acme/1",
                            "description": "Great backend role.",
                            "age": "2026-08-20T00:00:00.000Z",
                        }
                    ]
                }
            },
        )

    results = await fetch_brave(_http(handler), query="engineer", api_key="k")

    assert len(results) == 1
    r = results[0]
    assert r.provider == "brave"
    assert r.company == "Acme"
    assert r.source_tier == 1.0
    assert r.posted_at == "2026-08-20T00:00:00.000Z"


async def test_fetch_brave_sends_correct_auth_header() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200, json={"web": {"results": []}})

    await fetch_brave(_http(handler), query="engineer", api_key="brave-key")

    assert captured["x-subscription-token"] == "brave-key"


async def test_fetch_brave_error_raises_provider_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    with pytest.raises(ApiError) as exc_info:
        await fetch_brave(_http(handler), query="engineer", api_key="k")
    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"


# ── JSearch / RapidAPI (BYOK) ────────────────────────────────────────────


def _jsearch_job(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "job_id": "abc123",
        "job_title": "Backend Engineer",
        "employer_name": "Acme",
        "job_city": "New York",
        "job_state": "NY",
        "job_country": "US",
        "job_description": "Great backend role.",
        "job_apply_link": "https://acme.com/apply/1",
        "apply_options": [
            {
                "publisher": "Acme",
                "apply_link": "https://acme.com/apply/direct/1",
                "is_direct": True,
            }
        ],
        "job_is_remote": True,
        "job_min_salary": 120000,
        "job_max_salary": 160000,
        "job_salary_currency": "USD",
        "job_posted_at_datetime_utc": "2026-08-25T00:00:00Z",
    }
    base.update(overrides)
    return base


async def test_fetch_jsearch_maps_real_shaped_fields_and_prefers_direct_link() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"jobs": [_jsearch_job()]}})

    results = await fetch_jsearch(_http(handler), query="backend engineer", api_key="k")

    assert len(results) == 1
    r = results[0]
    assert r.provider == "jsearch"
    assert r.title == "Backend Engineer"
    assert r.company == "Acme"
    assert r.location == "New York, NY, US"
    assert r.apply_url == "https://acme.com/apply/direct/1"  # direct link preferred
    assert r.remote is True
    assert r.salary_min == 120000
    assert r.salary_currency == "USD"


async def test_fetch_jsearch_sends_correct_auth_headers() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200, json={"data": {"jobs": []}})

    await fetch_jsearch(_http(handler), query="engineer", api_key="js-key")

    assert captured["x-rapidapi-key"] == "js-key"
    assert captured["x-rapidapi-host"] == "jsearch.p.rapidapi.com"


async def test_fetch_jsearch_falls_back_to_apply_link_without_direct_option() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"jobs": [_jsearch_job(apply_options=[])]}})

    results = await fetch_jsearch(_http(handler), query="engineer", api_key="k")

    assert results[0].apply_url == "https://acme.com/apply/1"


async def test_fetch_jsearch_skips_jobs_without_any_apply_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": {"jobs": [_jsearch_job(apply_options=[], job_apply_link="")]}}
        )

    results = await fetch_jsearch(_http(handler), query="engineer", api_key="k")

    assert results == []


async def test_fetch_jsearch_error_raises_provider_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    with pytest.raises(ApiError) as exc_info:
        await fetch_jsearch(_http(handler), query="engineer", api_key="k")
    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"


# ── Fan-out orchestration ─────────────────────────────────────────────────


async def test_search_jobs_always_tries_remoteok_and_arbeitnow_with_no_credentials() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "remoteok" in str(request.url):
            return httpx.Response(200, json=[{"legal": "x"}])
        return httpx.Response(200, json={"data": [], "meta": {}})

    results, warnings = await search_jobs(_http(handler), query="engineer")

    assert any("remoteok" in c for c in calls)
    assert any("arbeitnow" in c for c in calls)
    assert results == []
    assert warnings == []


async def test_search_jobs_skips_byok_providers_without_credentials() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "arbeitnow" in str(request.url):
            return httpx.Response(200, json={"data": [], "meta": {}})
        return httpx.Response(200, json=[{"legal": "x"}])

    await search_jobs(_http(handler), query="engineer")

    assert not any("ydc-index" in c or "firecrawl" in c or "usajobs" in c for c in calls)


async def test_search_jobs_includes_byok_providers_when_credentials_present() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "ydc-index" in str(request.url):
            return httpx.Response(200, json={"results": {"web": []}})
        if "firecrawl" in str(request.url):
            return httpx.Response(200, json={"data": {"web": []}})
        if "usajobs" in str(request.url):
            return httpx.Response(200, json={"SearchResult": {"SearchResultItems": []}})
        if "arbeitnow" in str(request.url):
            return httpx.Response(200, json={"data": [], "meta": {}})
        if "google.serper.dev" in str(request.url):
            return httpx.Response(200, json={"organic": []})
        if "brave" in str(request.url):
            return httpx.Response(200, json={"web": {"results": []}})
        if "jsearch" in str(request.url):
            return httpx.Response(200, json={"data": {"jobs": []}})
        if "adzuna" in str(request.url):
            return httpx.Response(200, json={"count": 0, "results": []})
        return httpx.Response(200, json=[{"legal": "x"}])

    await search_jobs(
        _http(handler),
        query="engineer",
        credentials=ProviderCredentials(
            you_com_key="a",
            firecrawl_key="b",
            usajobs_key="c",
            usajobs_email="e@x.com",
            serper_key="d",
            brave_key="e",
            jsearch_key="f",
            adzuna_app_id="g",
            adzuna_app_key="h",
        ),
    )

    assert any("ydc-index" in c for c in calls)
    assert any("firecrawl" in c for c in calls)
    assert any("usajobs" in c for c in calls)
    assert any("google.serper.dev" in c for c in calls)
    assert any("brave" in c for c in calls)
    assert any("jsearch" in c for c in calls)
    assert any("adzuna" in c for c in calls)


async def test_search_jobs_skips_adzuna_when_only_app_id_is_set() -> None:
    """Both adzuna_app_id and adzuna_app_key must be present -- a partial
    2-secret credential never fires (matches try_get_secret_pair's own
    all-or-nothing degrade contract)."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"data": [], "meta": {}})

    await search_jobs(
        _http(handler),
        query="engineer",
        credentials=ProviderCredentials(adzuna_app_id="only-id"),
    )

    assert not any("adzuna" in c for c in calls)


async def test_search_jobs_one_provider_failure_does_not_abort_others() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "remoteok" in str(request.url):
            raise httpx.ConnectError("boom", request=request)
        if "arbeitnow" in str(request.url):
            return httpx.Response(200, json={"data": [_arbeitnow_job()], "meta": {}})
        return httpx.Response(200, json={"data": [], "meta": {}})

    results, warnings = await search_jobs(_http(handler), query="backend engineer")

    assert len(results) == 1
    assert results[0].provider == "arbeitnow"
    assert len(warnings) == 1
    assert warnings[0].startswith("remoteok:")


def test_search_result_is_frozen() -> None:
    r = SearchResult(
        provider="x",
        title="t",
        company=None,
        location=None,
        remote=None,
        apply_url="https://example.com",
        snippet="",
        posted_at=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.title = "y"  # type: ignore[misc]
