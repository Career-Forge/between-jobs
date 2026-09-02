"""Tests for the 6 P3a "mechanical" long-tail adapters (SmartRecruiters,
Workable, Recruitee, Amazon, Apple, D.E. Shaw). Fixtures are shaped from
REAL live-fetched responses (job-finder-port.md P3a web-verification
research against real seeded companies), not the older, partly-stale
n8n reference shapes -- several fields here deliberately differ from
what n8n's own source claims (see job_registry_adapters.py's own module
docstring for the specific deltas each test guards against)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from between_jobs.api.job_registry_adapters import (
    DueCompany,
    fetch_amazon,
    fetch_apple,
    fetch_deshaw,
    fetch_recruitee,
    fetch_smartrecruiters,
    fetch_workable,
)


def _http(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _company(**overrides: Any) -> DueCompany:
    base: dict[str, Any] = {
        "company_id": "company-uuid-1",
        "name": "Acme",
        "ats_type": "smartrecruiters",
        "slug": "acme",
        "api_base": "",
        "board": "smartrecruiters:acme:",
        "etag": "",
    }
    base.update(overrides)
    return DueCompany(**base)


# ── SmartRecruiters ──────────────────────────────────────────────────────


async def test_fetch_smartrecruiters_maps_real_shaped_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "limit=100&offset=0" in str(request.url)
        return httpx.Response(
            200,
            headers={"etag": '"sr-etag"'},
            json={
                "offset": 0,
                "limit": 100,
                "totalFound": 1,
                "content": [
                    {
                        "id": "744000146344019",
                        "name": "Director, Software Engineering Management",
                        "location": {
                            "city": "Santa Clara",
                            "region": "CA",
                            "country": "us",
                            "fullLocation": "Santa Clara, CA, US",
                            "remote": True,
                        },
                        "releasedDate": "2026-08-30T12:04:16.608Z",
                    }
                ],
            },
        )

    result = await fetch_smartrecruiters(_http(handler), _company())

    assert result.status == "ok"
    assert result.new_etag == '"sr-etag"'
    assert len(result.postings) == 1
    p = result.postings[0]
    assert p.external_id == "744000146344019"
    assert p.title == "Director, Software Engineering Management"
    assert p.jd_text == ""  # confirmed live: no description field on the list endpoint
    assert p.location == "Santa Clara, CA, US"
    assert p.remote is True
    assert p.apply_url == "https://jobs.smartrecruiters.com/acme/744000146344019"


async def test_fetch_smartrecruiters_paginates_using_real_offset_totalfound() -> None:
    total_found = 250

    def handler(request: httpx.Request) -> httpx.Response:
        query = dict(pair.split("=") for pair in str(request.url).split("?")[1].split("&"))
        offset = int(query["offset"])
        page_size = min(100, total_found - offset)
        content = [
            {"id": str(offset + i), "name": "Role", "location": {}} for i in range(page_size)
        ]
        return httpx.Response(200, json={"totalFound": total_found, "content": content})

    result = await fetch_smartrecruiters(_http(handler), _company())

    assert result.status == "ok"
    assert len(result.postings) == total_found  # 3 real pages: 100 + 100 + 50


async def test_fetch_smartrecruiters_stops_when_content_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = dict(pair.split("=") for pair in str(request.url).split("?")[1].split("&"))
        offset = int(query["offset"])
        if offset == 0:
            return httpx.Response(
                200,
                json={"totalFound": 1000, "content": [{"id": "1", "name": "Role", "location": {}}]},
            )
        return httpx.Response(200, json={"totalFound": 1000, "content": []})

    result = await fetch_smartrecruiters(_http(handler), _company())

    assert result.status == "ok"
    assert len(result.postings) == 1


async def test_fetch_smartrecruiters_304_is_not_modified() -> None:
    result = await fetch_smartrecruiters(
        _http(lambda r: httpx.Response(304)), _company(etag='"old-etag"')
    )
    assert result.status == "not_modified"


async def test_fetch_smartrecruiters_404_is_gone() -> None:
    result = await fetch_smartrecruiters(_http(lambda r: httpx.Response(404)), _company())
    assert result.status == "gone"


async def test_fetch_smartrecruiters_page1_failure_returns_partial_not_ok() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(
                200,
                headers={"etag": '"sr-etag"'},
                json={
                    "totalFound": 250,
                    "content": [{"id": str(i), "name": "Role", "location": {}} for i in range(100)],
                },
            )
        return httpx.Response(500)

    result = await fetch_smartrecruiters(_http(handler), _company())

    assert result.status == "partial"
    assert len(result.postings) == 100
    assert result.new_etag == '"sr-etag"'


async def test_fetch_smartrecruiters_skips_posting_with_no_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "totalFound": 2,
                "content": [
                    {"id": "744000146344019", "name": "Real Role", "location": {}},
                    {"name": "No Id Role", "location": {}},
                ],
            },
        )

    result = await fetch_smartrecruiters(_http(handler), _company())

    assert len(result.postings) == 1
    assert result.postings[0].external_id == "744000146344019"
    assert all("None" not in p.apply_url for p in result.postings)


# ── Workable ─────────────────────────────────────────────────────────────


async def test_fetch_workable_uses_telecommuting_boolean_not_workplace_string() -> None:
    """Regression guard: the old n8n source claimed the remote signal was
    a `workplace`/`location.location` string -- confirmed live (P3a
    research) that neither field exists; the real signal is a boolean
    `telecommuting` field."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "details=true" in str(request.url)
        return httpx.Response(
            200,
            json={
                "name": "Acme",
                "jobs": [
                    {
                        "shortcode": "ABC123",
                        "title": "ML Engineer",
                        "description": "<p>Do ML things.</p>",
                        "city": "Remote",
                        "state": "",
                        "country": "USA",
                        "telecommuting": True,
                        "url": "https://apply.workable.com/acme/j/ABC123",
                        "published_on": "2026-08-01",
                    }
                ],
            },
        )

    result = await fetch_workable(_http(handler), _company(ats_type="workable"))

    assert result.status == "ok"
    p = result.postings[0]
    assert p.external_id == "ABC123"
    assert p.remote is True
    assert p.jd_text == "Do ML things."
    assert p.location == "Remote, USA"


async def test_fetch_workable_falls_back_to_application_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "shortcode": "X1",
                        "title": "Role",
                        "application_url": "https://apply.workable.com/acme/j/X1/apply",
                    }
                ]
            },
        )

    result = await fetch_workable(_http(handler), _company(ats_type="workable"))
    assert result.postings[0].apply_url == "https://apply.workable.com/acme/j/X1/apply"


async def test_fetch_workable_404_is_gone() -> None:
    result = await fetch_workable(
        _http(lambda r: httpx.Response(404)), _company(ats_type="workable")
    )
    assert result.status == "gone"


# ── Recruitee ────────────────────────────────────────────────────────────


async def test_fetch_recruitee_uses_careers_url_not_url_field() -> None:
    """Regression guard: the old n8n source claimed a `careers_url`/`url`
    fallback -- confirmed live (P3a research) that `url` doesn't exist at
    all on the real API; only `careers_url` does."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://acme.recruitee.com/api/offers"
        return httpx.Response(
            200,
            json={
                "offers": [
                    {
                        "id": 12345,
                        "title": "Backend Engineer",
                        "status": "published",
                        "description": "<p>Build things.</p>",
                        "requirements": "<p>5 years experience.</p>",
                        "location": "Zurich, Switzerland",
                        "careers_url": "https://acme.recruitee.com/o/backend-engineer",
                        "careers_apply_url": "https://acme.recruitee.com/o/backend-engineer/c/new",
                        "published_at": "2026-08-15T00:00:00Z",
                    }
                ]
            },
        )

    result = await fetch_recruitee(_http(handler), _company(ats_type="recruitee"))

    assert result.status == "ok"
    p = result.postings[0]
    assert p.external_id == "12345"
    assert p.apply_url == "https://acme.recruitee.com/o/backend-engineer"
    assert "Build things." in p.jd_text
    assert "5 years experience." in p.jd_text
    assert p.remote is False


async def test_fetch_recruitee_skips_non_published_offers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "offers": [
                    {"id": 1, "title": "Draft role", "status": "draft", "careers_url": "https://x"},
                    {
                        "id": 2,
                        "title": "Live role",
                        "status": "published",
                        "careers_url": "https://x",
                    },
                ]
            },
        )

    result = await fetch_recruitee(_http(handler), _company(ats_type="recruitee"))
    assert [p.external_id for p in result.postings] == ["2"]


async def test_fetch_recruitee_404_is_gone() -> None:
    result = await fetch_recruitee(
        _http(lambda r: httpx.Response(404)), _company(ats_type="recruitee")
    )
    assert result.status == "gone"


# ── Amazon ───────────────────────────────────────────────────────────────


async def test_fetch_amazon_maps_fields_and_parses_month_name_date() -> None:
    """Regression guard: the old n8n source claimed no date field exists
    on this endpoint -- confirmed live (P3a research) that `posted_date`
    now does, in a human-readable "August 3, 2026" shape, not ISO8601."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "result_limit=100" in str(request.url)
        return httpx.Response(
            200,
            json={
                "hits": 1,
                "jobs": [
                    {
                        "id_icims": "2456789",
                        "title": "Software Development Engineer",
                        "description": "<p>Build scalable systems.</p>",
                        "location": "Seattle, WA, USA",
                        "locations": [json.dumps({"type": "ONSITE"})],
                        "url_next_step": "https://www.amazon.jobs/en/jobs/2456789",
                        "posted_date": "August 3, 2026",
                    }
                ],
            },
        )

    result = await fetch_amazon(_http(handler), _company(ats_type="amazon"))

    assert result.status == "ok"
    p = result.postings[0]
    assert p.external_id == "2456789"
    assert p.remote is False
    assert p.posted_at is not None
    assert p.posted_at.startswith("2026-08-03")


async def test_fetch_amazon_detects_virtual_remote_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": "1",
                        "title": "Remote Role",
                        "locations": [json.dumps({"type": "VIRTUAL"})],
                        "job_path": "/en/jobs/1",
                    }
                ]
            },
        )

    result = await fetch_amazon(_http(handler), _company(ats_type="amazon"))
    assert result.postings[0].remote is True
    assert result.postings[0].apply_url == "https://www.amazon.jobs/en/jobs/1"


async def test_fetch_amazon_paginates_until_short_page() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        query = dict(pair.split("=") for pair in str(request.url).split("?")[1].split("&"))
        offset = int(query["offset"])
        if offset == 0:
            jobs = [{"id": str(i), "title": "Role"} for i in range(100)]
        else:
            jobs = [{"id": str(100 + i), "title": "Role"} for i in range(30)]
        return httpx.Response(200, json={"jobs": jobs})

    result = await fetch_amazon(_http(handler), _company(ats_type="amazon"))

    assert result.status == "ok"
    assert len(result.postings) == 130
    assert call_count == 2


async def test_fetch_amazon_404_is_gone() -> None:
    result = await fetch_amazon(_http(lambda r: httpx.Response(404)), _company(ats_type="amazon"))
    assert result.status == "gone"


async def test_fetch_amazon_page1_failure_returns_partial_not_ok() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            jobs = [{"id": str(i), "title": "Role"} for i in range(100)]
            return httpx.Response(200, json={"jobs": jobs})
        return httpx.Response(500)

    result = await fetch_amazon(_http(handler), _company(ats_type="amazon"))

    assert result.status == "partial"
    assert len(result.postings) == 100


# ── Apple ────────────────────────────────────────────────────────────────


async def test_fetch_apple_unwraps_res_envelope_and_uses_position_id() -> None:
    """Regression guard: the old n8n source assumed a flat response body
    -- confirmed live (P3a research) the real shape is {"res": {...}},
    one level deeper, and the real dedupe-worthy id is `positionId`
    (numeric-ish string), not `id` (a "PIPE-..." string)."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["page"] == 1
        assert body["format"] == "json"
        return httpx.Response(
            200,
            json={
                "res": {
                    "searchResults": [
                        {
                            "id": "PIPE-200313970",
                            "positionId": "200313970",
                            "postingTitle": "Software Engineer",
                            "jobSummary": "Build great software.",
                            "locations": [
                                {"city": "Cupertino", "stateProvince": "CA", "countryName": "USA"}
                            ],
                            "homeOffice": False,
                            "postingDate": "2026-08-01",
                        }
                    ],
                    "totalRecords": 1,
                }
            },
        )

    result = await fetch_apple(_http(handler), _company(ats_type="apple", slug="apple"))

    assert result.status == "ok"
    p = result.postings[0]
    assert p.external_id == "200313970"
    assert p.location == "Cupertino, CA, USA"
    assert "apple.com/en-us/details/200313970/software-engineer" in p.apply_url


async def test_fetch_apple_stops_pagination_on_short_page() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        results = [
            {"positionId": str(i), "postingTitle": "Role"} for i in range(5)
        ]  # < _APPLE_PAGE_SIZE (20)
        return httpx.Response(200, json={"res": {"searchResults": results}})

    result = await fetch_apple(_http(handler), _company(ats_type="apple", slug="apple"))

    assert result.status == "ok"
    assert call_count == 1
    assert len(result.postings) == 5


async def test_fetch_apple_empty_first_page_is_ok_not_failed() -> None:
    result = await fetch_apple(
        _http(lambda r: httpx.Response(200, json={"res": {"searchResults": []}})),
        _company(ats_type="apple", slug="apple"),
    )
    assert result.status == "ok"
    assert result.postings == []


async def test_fetch_apple_server_error_is_failed() -> None:
    result = await fetch_apple(
        _http(lambda r: httpx.Response(500)), _company(ats_type="apple", slug="apple")
    )
    assert result.status == "failed"


async def test_fetch_apple_page2_failure_returns_partial_not_ok() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            results = [{"positionId": str(i), "postingTitle": "Role"} for i in range(20)]
            return httpx.Response(200, json={"res": {"searchResults": results}})
        return httpx.Response(500)

    result = await fetch_apple(_http(handler), _company(ats_type="apple", slug="apple"))

    assert result.status == "partial"
    assert len(result.postings) == 20


# ── D.E. Shaw ────────────────────────────────────────────────────────────


def _deshaw_page(job_data: dict[str, Any]) -> str:
    next_data = {
        "props": {
            "pageProps": {
                "regularJobs": [{"data": job_data}],
                "internships": [],
            }
        }
    }
    return f'<html><body><script id="__NEXT_DATA__">{json.dumps(next_data)}</script></body></html>'


async def test_fetch_deshaw_unwraps_data_nesting() -> None:
    """Regression guard: the old n8n source assumed regularJobs[i] IS the
    job object -- confirmed live (P3a research) every real entry is
    nested one level deeper, under regularJobs[i].data."""
    html = _deshaw_page(
        {
            "id": 6012,
            "displayName": "Receptionist",
            "jobDescription": {"websiteDescription": "<p>Front desk role.</p>"},
            "jobMetadata": {"jobLocations": [{"name": "Singapore", "abbreviation": "SG"}]},
            "jobUrl": "Receptionist-Singapore-6012",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    result = await fetch_deshaw(_http(handler), _company(ats_type="deshaw", slug="deshaw"))

    assert result.status == "ok"
    p = result.postings[0]
    assert p.external_id == "6012"
    assert p.title == "Receptionist"
    assert p.jd_text == "Front desk role."
    assert p.location == "Singapore"
    assert p.apply_url == "https://www.deshaw.com/careers/receptionist-singapore-6012"


async def test_fetch_deshaw_missing_next_data_is_failed() -> None:
    result = await fetch_deshaw(
        _http(lambda r: httpx.Response(200, text="<html><body>no data here</body></html>")),
        _company(ats_type="deshaw", slug="deshaw"),
    )
    assert result.status == "failed"


async def test_fetch_deshaw_server_error_is_failed() -> None:
    result = await fetch_deshaw(
        _http(lambda r: httpx.Response(500)), _company(ats_type="deshaw", slug="deshaw")
    )
    assert result.status == "failed"
