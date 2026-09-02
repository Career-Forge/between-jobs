"""Tests for the Greenhouse/Lever/Ashby/Workday adapters (Job Finder P2,
job-finder-port.md). Uses httpx's own MockTransport (same convention as
test_telegram_client.py) rather than a live network call -- field mapping
is checked against real-shaped response payloads read directly from
n8n's own Parse Jobs node, not invented shapes."""

from __future__ import annotations

import json
from typing import Any

import httpx

from between_jobs.api.job_registry_adapters import (
    DueCompany,
    fetch_ashby,
    fetch_greenhouse,
    fetch_lever,
    fetch_workday,
)


def _http(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _company(**overrides: Any) -> DueCompany:
    base: dict[str, Any] = {
        "company_id": "company-uuid-1",
        "name": "Acme",
        "ats_type": "greenhouse",
        "slug": "acme",
        "api_base": "",
        "board": "greenhouse:acme",
        "etag": "",
    }
    base.update(overrides)
    return DueCompany(**base)


# ── Greenhouse ────────────────────────────────────────────────────────────


async def test_fetch_greenhouse_maps_fields_and_strips_html_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert (
            str(request.url) == "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
        )
        return httpx.Response(
            200,
            headers={"etag": '"abc123"'},
            json={
                "jobs": [
                    {
                        "id": 555,
                        "title": "Staff Engineer",
                        "content": "<p>Build &amp; ship things.</p>",
                        "location": {"name": "Remote - US"},
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/555",
                        "updated_at": "2026-08-01T00:00:00.000Z",
                    }
                ]
            },
        )

    result = await fetch_greenhouse(_http(handler), _company())

    assert result.status == "ok"
    assert result.new_etag == '"abc123"'
    assert len(result.postings) == 1
    p = result.postings[0]
    assert p.external_id == "555"
    assert p.title == "Staff Engineer"
    assert p.jd_text == "Build & ship things."
    assert p.location == "Remote - US"
    assert p.remote is True
    assert p.apply_url == "https://boards.greenhouse.io/acme/jobs/555"
    assert p.posted_at is not None


async def test_fetch_greenhouse_sends_if_none_match_when_etag_present() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["if_none_match"] = request.headers.get("if-none-match")
        return httpx.Response(304)

    result = await fetch_greenhouse(_http(handler), _company(etag='"abc123"'))

    assert captured["if_none_match"] == '"abc123"'
    assert result.status == "not_modified"
    assert result.postings == []


async def test_fetch_greenhouse_omits_conditional_header_when_no_etag_stored() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["if_none_match"] = request.headers.get("if-none-match")
        return httpx.Response(200, json={"jobs": []})

    await fetch_greenhouse(_http(handler), _company(etag=""))

    assert captured["if_none_match"] is None


async def test_fetch_greenhouse_404_is_gone() -> None:
    result = await fetch_greenhouse(_http(lambda r: httpx.Response(404)), _company())
    assert result.status == "gone"


async def test_fetch_greenhouse_410_is_gone() -> None:
    result = await fetch_greenhouse(_http(lambda r: httpx.Response(410)), _company())
    assert result.status == "gone"


async def test_fetch_greenhouse_server_error_is_failed() -> None:
    result = await fetch_greenhouse(_http(lambda r: httpx.Response(500)), _company())
    assert result.status == "failed"


async def test_fetch_greenhouse_malformed_json_is_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    result = await fetch_greenhouse(_http(handler), _company())
    assert result.status == "failed"


async def test_fetch_greenhouse_body_error_field_is_failed() -> None:
    result = await fetch_greenhouse(
        _http(lambda r: httpx.Response(200, json={"error": "invalid board"})), _company()
    )
    assert result.status == "failed"


async def test_fetch_greenhouse_network_error_is_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    result = await fetch_greenhouse(_http(handler), _company())
    assert result.status == "failed"


# ── Lever ────────────────────────────────────────────────────────────────


async def test_fetch_lever_maps_fields_from_bare_array_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.lever.co/v0/postings/acme?mode=json"
        return httpx.Response(
            200,
            json=[
                {
                    "id": "abc-uuid",
                    "text": "Senior Backend Engineer",
                    "descriptionPlain": "Real description text.",
                    "categories": {"location": "New York"},
                    "workplaceType": "remote",
                    "hostedUrl": "https://jobs.lever.co/acme/abc-uuid",
                    "createdAt": 1700000000000,
                }
            ],
        )

    result = await fetch_lever(_http(handler), _company(ats_type="lever", board="lever:acme"))

    assert result.status == "ok"
    p = result.postings[0]
    assert p.external_id == "abc-uuid"
    assert p.title == "Senior Backend Engineer"
    assert p.jd_text == "Real description text."
    assert p.location == "New York"
    assert p.remote is True
    assert p.apply_url == "https://jobs.lever.co/acme/abc-uuid"


async def test_fetch_lever_falls_back_to_stripped_html_description() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": "id-2",
                    "text": "Engineer",
                    "description": "<p>Plain text only.</p>",
                    "categories": {},
                    "hostedUrl": "https://jobs.lever.co/acme/id-2",
                }
            ],
        )

    result = await fetch_lever(_http(handler), _company(ats_type="lever", board="lever:acme"))
    assert result.postings[0].jd_text == "Plain text only."


async def test_fetch_lever_wrapped_data_body_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [{"id": "id-3", "text": "Role", "categories": {}, "hostedUrl": "https://x"}]
            },
        )

    result = await fetch_lever(_http(handler), _company(ats_type="lever", board="lever:acme"))
    assert len(result.postings) == 1
    assert result.postings[0].external_id == "id-3"


async def test_fetch_lever_404_is_gone() -> None:
    result = await fetch_lever(
        _http(lambda r: httpx.Response(404)), _company(ats_type="lever", board="lever:acme")
    )
    assert result.status == "gone"


# ── Ashby ────────────────────────────────────────────────────────────────


async def test_fetch_ashby_maps_fields_and_uses_real_is_remote_boolean() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "includeCompensation=true" in str(request.url)
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": "job-uuid-1",
                        "title": "ML Engineer",
                        "descriptionPlain": "Do ML things.",
                        "location": "San Francisco",
                        "isRemote": True,
                        "jobUrl": "https://jobs.ashbyhq.com/acme/job-uuid-1",
                        "publishedAt": "2026-08-01T00:00:00Z",
                    }
                ]
            },
        )

    result = await fetch_ashby(_http(handler), _company(ats_type="ashby", board="ashby:acme"))

    assert result.status == "ok"
    p = result.postings[0]
    assert p.external_id == "job-uuid-1"
    assert p.remote is True
    assert p.jd_text == "Do ML things."


async def test_fetch_ashby_skips_unlisted_postings() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {"id": "listed", "title": "A", "isListed": True, "jobUrl": "https://x"},
                    {"id": "unlisted", "title": "B", "isListed": False, "jobUrl": "https://x"},
                ]
            },
        )

    result = await fetch_ashby(_http(handler), _company(ats_type="ashby", board="ashby:acme"))
    assert [p.external_id for p in result.postings] == ["listed"]


# ── Workday ──────────────────────────────────────────────────────────────


def _workday_company() -> DueCompany:
    return _company(
        ats_type="workday", slug="acmecareers", api_base="acme.wd5", board="workday:acmecareers"
    )


async def test_fetch_workday_posts_cxs_search_and_maps_fields() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert (
            str(request.url) == "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/acmecareers/jobs"
        )
        body = json.loads(request.content)
        captured.append(body)
        return httpx.Response(
            200,
            json={
                "total": 1,
                "jobPostings": [
                    {
                        "title": "Data Engineer",
                        "externalPath": "/job/Data-Engineer_R-123",
                        "locationsText": "Remote - USA",
                        "remoteType": "Remote",
                    }
                ],
            },
        )

    result = await fetch_workday(_http(handler), _workday_company())

    assert result.status == "ok"
    assert result.new_etag is None
    assert len(result.postings) == 1
    p = result.postings[0]
    assert p.external_id == "/job/Data-Engineer_R-123"
    assert p.remote is True
    assert p.jd_text == ""
    assert p.posted_at is None
    assert p.apply_url == "https://acme.wd5.myworkdayjobs.com/acmecareers/job/Data-Engineer_R-123"
    assert captured[0] == {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}


async def test_fetch_workday_paginates_until_total_reached() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        body = json.loads(request.content)
        offset = body["offset"]
        call_count += 1
        if offset == 0:
            postings = [{"title": f"Role {i}", "externalPath": f"/job/r{i}"} for i in range(20)]
        elif offset == 20:
            postings = [{"title": f"Role {i}", "externalPath": f"/job/r{i}"} for i in range(20, 40)]
        else:
            postings = [{"title": f"Role {i}", "externalPath": f"/job/r{i}"} for i in range(40, 45)]
        return httpx.Response(200, json={"total": 45, "jobPostings": postings})

    result = await fetch_workday(_http(handler), _workday_company())

    assert result.status == "ok"
    assert len(result.postings) == 45
    assert call_count == 3


async def test_fetch_workday_stops_after_max_pages_on_a_huge_board() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        postings = [{"title": "Role", "externalPath": "/job/r"}] * 20
        return httpx.Response(200, json={"total": 100000, "jobPostings": postings})

    result = await fetch_workday(_http(handler), _workday_company())
    # 5 pages * 20/page, regardless of the board's real (much larger) total --
    # the disclosed v1 limitation (module docstring), not a bug.
    assert len(result.postings) == 100


async def test_fetch_workday_first_page_failure_is_failed() -> None:
    result = await fetch_workday(_http(lambda r: httpx.Response(500)), _workday_company())
    assert result.status == "failed"
    assert result.postings == []


async def test_fetch_workday_malformed_api_base_is_failed() -> None:
    company = _company(
        ats_type="workday", slug="acmecareers", api_base="no-dot-here", board="workday:x"
    )
    result = await fetch_workday(_http(lambda r: httpx.Response(200)), company)
    assert result.status == "failed"


async def test_fetch_workday_page1_failure_returns_partial_not_ok() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            postings = [{"title": f"Role {i}", "externalPath": f"/job/r{i}"} for i in range(20)]
            return httpx.Response(200, json={"total": 50, "jobPostings": postings})
        return httpx.Response(500)

    result = await fetch_workday(_http(handler), _workday_company())

    assert result.status == "partial"
    assert len(result.postings) == 20
