"""Tests for the Oracle Fusion HCM adapter (Job Finder P3b,
job-finder-port.md). Fixtures are shaped from REAL live-fetched
responses against 6 real Oracle Fusion customers (Akamai, Ford, Goldman
Sachs, JPMorgan Chase, Honeywell, Texas Instruments) -- including the
real, live-confirmed finding that WorkplaceType/ShortDescriptionStr are
legitimately empty for some customers, not broken, and that the
apply_url construction (once believed Oracle-Corp-specific per n8n's own
code comment) genuinely generalizes across unrelated customers."""

from __future__ import annotations

from typing import Any

import httpx

from between_jobs.api.job_registry_adapters import DueCompany, fetch_oracle


def _http(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _company(**overrides: Any) -> DueCompany:
    base: dict[str, Any] = {
        "company_id": "company-uuid-1",
        "name": "Ford",
        "ats_type": "oracle",
        "slug": "CX_1",
        "api_base": "efds.fa.em5.oraclecloud.com",
        "board": "oracle:CX_1:efds.fa.em5.oraclecloud.com",
        "etag": "",
    }
    base.update(overrides)
    return DueCompany(**base)


def _requisition(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "Id": 65071,
        "Title": "Jira Administration Engineer",
        "ShortDescriptionStr": "<p>Administer Jira for engineering teams.</p>",
        "PrimaryLocation": "Dearborn, MI",
        "PrimaryLocationCountry": "United States",
        "WorkplaceType": "On-site",
        "WorkplaceTypeCode": "ON_SITE",
        "PostedDate": "2026-08-29T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def _page(requisitions: list[dict[str, Any]], total: int) -> dict[str, Any]:
    return {"items": [{"requisitionList": requisitions, "TotalJobsCount": total}]}


async def test_fetch_oracle_maps_real_shaped_fields_and_builds_apply_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert url.startswith("https://efds.fa.em5.oraclecloud.com/hcmRestApi/resources/latest/")
        assert "siteNumber=CX_1" in url
        assert "limit=20,offset=0" in url
        return httpx.Response(200, json=_page([_requisition()], total=1))

    result = await fetch_oracle(_http(handler), _company())

    assert result.status == "ok"
    assert len(result.postings) == 1
    p = result.postings[0]
    assert p.external_id == "65071"
    assert p.title == "Jira Administration Engineer"
    assert p.jd_text == "Administer Jira for engineering teams."
    assert p.location == "Dearborn, MI"
    assert p.remote is False
    assert p.apply_url == (
        "https://efds.fa.em5.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/65071"
    )
    assert p.posted_at is not None


async def test_fetch_oracle_treats_empty_workplace_type_and_description_as_legitimate() -> None:
    """Real, live-confirmed finding: Goldman Sachs/JPMorgan Chase/Texas
    Instruments leave WorkplaceType and/or ShortDescriptionStr as empty
    strings on real postings -- this is a per-customer data choice, not
    a broken field, and must not be guessed at."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_page(
                [
                    _requisition(
                        WorkplaceType="",
                        WorkplaceTypeCode="",
                        ShortDescriptionStr="",
                    )
                ],
                total=1,
            ),
        )

    result = await fetch_oracle(_http(handler), _company())

    assert result.status == "ok"
    p = result.postings[0]
    assert p.jd_text == ""
    assert p.remote is False


async def test_fetch_oracle_detects_remote_workplace_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_page([_requisition(WorkplaceType="Remote")], total=1))

    result = await fetch_oracle(_http(handler), _company())
    assert result.postings[0].remote is True


async def test_fetch_oracle_falls_back_to_country_when_primary_location_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_page(
                [_requisition(PrimaryLocation="", PrimaryLocationCountry="United States")], total=1
            ),
        )

    result = await fetch_oracle(_http(handler), _company())
    assert result.postings[0].location == "United States"


async def test_fetch_oracle_paginates_using_real_offset_and_total_jobs_count() -> None:
    """Real, live-confirmed: offset/limit pagination genuinely works and
    returns disjoint pages, unlike n8n's own single-hardcoded-page fetch
    -- real companies range up to 7,181 postings (JPMorgan Chase)."""
    total = 45

    def handler(request: httpx.Request) -> httpx.Response:
        query = str(request.url).split("finder=findReqs;")[1]
        params = dict(p.split("=") for p in query.split(","))
        offset = int(params["offset"])
        page_size = min(20, total - offset)
        reqs = [_requisition(Id=offset + i, Title=f"Role {offset + i}") for i in range(page_size)]
        return httpx.Response(200, json=_page(reqs, total=total))

    result = await fetch_oracle(_http(handler), _company())

    assert result.status == "ok"
    assert len(result.postings) == total  # 3 pages: 20 + 20 + 5
    assert len({p.external_id for p in result.postings}) == total  # all distinct


async def test_fetch_oracle_stops_when_requisition_list_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = str(request.url).split("finder=findReqs;")[1]
        params = dict(p.split("=") for p in query.split(","))
        offset = int(params["offset"])
        if offset == 0:
            return httpx.Response(200, json=_page([_requisition()], total=1000))
        return httpx.Response(200, json=_page([], total=1000))

    result = await fetch_oracle(_http(handler), _company())

    assert result.status == "ok"
    assert len(result.postings) == 1


async def test_fetch_oracle_first_page_failure_is_failed() -> None:
    result = await fetch_oracle(_http(lambda r: httpx.Response(500)), _company())
    assert result.status == "failed"
    assert result.postings == []


async def test_fetch_oracle_empty_items_is_failed() -> None:
    result = await fetch_oracle(
        _http(lambda r: httpx.Response(200, json={"items": []})), _company()
    )
    assert result.status == "failed"


async def test_fetch_oracle_page1_failure_returns_partial_not_ok() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            reqs = [_requisition(Id=i, Title=f"Role {i}") for i in range(20)]
            return httpx.Response(200, json=_page(reqs, total=50))
        return httpx.Response(500)

    result = await fetch_oracle(_http(handler), _company())

    assert result.status == "partial"
    assert len(result.postings) == 20


async def test_fetch_oracle_skips_requisition_with_no_id() -> None:
    reqs = [_requisition(Id=65071), _requisition(Id=None, Title="No Id Role")]

    result = await fetch_oracle(
        _http(lambda r: httpx.Response(200, json=_page(reqs, total=2))), _company()
    )

    assert len(result.postings) == 1
    assert result.postings[0].external_id == "65071"
    assert all("None" not in p.apply_url for p in result.postings)
