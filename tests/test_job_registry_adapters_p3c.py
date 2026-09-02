"""Tests for the Eightfold adapter (Job Finder P3c, job-finder-port.md).
Fixtures are shaped from REAL live-fetched responses against 6 real
Eightfold tenants (Bayer, NetApp, Netflix -- "normal" smartapply
tenants; PayPal, Starbucks, Boston Scientific -- the 3 real tenants
n8n's own incident history named as needing the pcsx fallback), plus
the separate detail endpoint used by the JD-backfill lane."""

from __future__ import annotations

from typing import Any

import httpx

from between_jobs.api.job_registry_adapters import (
    DueCompany,
    extract_eightfold_position_id,
    fetch_eightfold,
    fetch_eightfold_detail,
)


def _http(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _company(**overrides: Any) -> DueCompany:
    base: dict[str, Any] = {
        "company_id": "company-uuid-1",
        "name": "Bayer",
        "ats_type": "eightfold",
        "slug": "bayer.com",
        "api_base": "bayer.eightfold.ai",
        "board": "eightfold:bayer.com:bayer.eightfold.ai",
        "etag": "",
    }
    base.update(overrides)
    return DueCompany(**base)


def _smartapply_position(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 562949978450437,
        "name": "Senior Software Engineer",
        "location": "Berlin, Germany",
        "ats_job_id": "REQ-12345",
        "display_job_id": "REQ-12345",
        "job_description": "",
        "work_location_option": "hybrid",
        "canonicalPositionUrl": "https://talent.bayer.com/careers/job/562949978450437",
        "t_create": 1787788800,
    }
    base.update(overrides)
    return base


def _pcsx_position(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 274922097063,
        "name": "Data Engineer",
        "locations": ["San Jose, CA"],
        "atsJobId": "274922097063",
        "displayJobId": "274922097063",
        "workLocationOption": "onsite",
        "positionUrl": "/careers/job/274922097063",
        "creationTs": 1787788800,
    }
    base.update(overrides)
    return base


# ── smartapply (normal) tenants ─────────────────────────────────────────


async def test_fetch_eightfold_maps_smartapply_shaped_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/api/apply/v2/jobs" in str(request.url)
        assert "domain=bayer.com" in str(request.url)
        return httpx.Response(200, json={"positions": [_smartapply_position()]})

    result = await fetch_eightfold(_http(handler), _company())

    assert result.status == "ok"
    p = result.postings[0]
    assert p.external_id == "REQ-12345"
    assert p.title == "Senior Software Engineer"
    assert p.jd_text == ""  # confirmed live: always empty on the list endpoint
    assert p.location == "Berlin, Germany"
    assert p.remote is False
    assert p.apply_url == "https://talent.bayer.com/careers/job/562949978450437"
    assert p.posted_at is not None
    assert p.posted_at.startswith("2026-08")


async def test_fetch_eightfold_custom_domain_needs_no_special_casing() -> None:
    """Confirmed live: Netflix's api_base is a custom domain
    (explore.jobs.netflix.net), not *.eightfold.ai -- the same path and
    params work unchanged."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://explore.jobs.netflix.net/api/apply/v2/jobs")
        return httpx.Response(200, json={"positions": [_smartapply_position(name="SRE")]})

    company = _company(
        slug="netflix.com", api_base="explore.jobs.netflix.net", board="eightfold:netflix.com:x"
    )
    result = await fetch_eightfold(_http(handler), company)

    assert result.status == "ok"
    assert result.postings[0].title == "SRE"


async def test_fetch_eightfold_paginates_and_stops_on_short_page() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        query = dict(pair.split("=") for pair in str(request.url).split("?")[1].split("&"))
        start = int(query["start"])
        if start == 0:
            positions = [_smartapply_position(id=i) for i in range(10)]
        else:
            positions = [_smartapply_position(id=100 + i) for i in range(4)]
        return httpx.Response(200, json={"positions": positions})

    result = await fetch_eightfold(_http(handler), _company())

    assert result.status == "ok"
    assert len(result.postings) == 14
    assert call_count == 2


async def test_fetch_eightfold_page1_failure_returns_partial_not_ok() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            positions = [_smartapply_position(id=i) for i in range(10)]
            return httpx.Response(200, json={"positions": positions})
        return httpx.Response(500)

    result = await fetch_eightfold(_http(handler), _company())

    assert result.status == "partial"
    assert len(result.postings) == 10


# ── pcsx fallback (the s112 tier-flip) ──────────────────────────────────


async def test_fetch_eightfold_falls_back_to_pcsx_on_403() -> None:
    """Confirmed still live today (P3c research) against PayPal/
    Starbucks/Boston Scientific: smartapply 403s, pcsx has the real
    data."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/apply/v2/jobs" in str(request.url):
            return httpx.Response(403, json={"message": "Not authorized for PCSX"})
        assert "/api/pcsx/search" in str(request.url)
        return httpx.Response(200, json={"data": {"positions": [_pcsx_position()]}})

    result = await fetch_eightfold(
        _http(handler), _company(name="PayPal", slug="paypal.com", api_base="paypal.eightfold.ai")
    )

    assert result.status == "ok"
    p = result.postings[0]
    assert p.external_id == "274922097063"
    assert p.location == "San Jose, CA"
    assert p.remote is False
    # positionUrl is site-relative on pcsx -- must be absolutized against api_base
    assert p.apply_url == "https://paypal.eightfold.ai/careers/job/274922097063"
    assert p.posted_at is not None  # sourced from creationTs, not t_create


async def test_fetch_eightfold_falls_back_to_pcsx_on_empty_positions() -> None:
    """The OTHER s112 trigger condition -- a 200 with a genuinely empty
    positions array on smartapply, not just a 403."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/apply/v2/jobs" in str(request.url):
            return httpx.Response(200, json={"positions": []})
        return httpx.Response(200, json={"data": {"positions": [_pcsx_position()]}})

    result = await fetch_eightfold(_http(handler), _company())

    assert result.status == "ok"
    assert len(result.postings) == 1


async def test_fetch_eightfold_never_refallbacks_on_a_later_page() -> None:
    """A later page legitimately running dry on an already-working tier
    must not re-trigger the pcsx fallback -- confirmed this is page-0-only
    in n8n's own real incident history."""
    call_log: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        call_log.append(url)
        query = dict(pair.split("=") for pair in url.split("?")[1].split("&"))
        start = int(query["start"])
        if "/api/pcsx/search" in url:
            # Should never be hit -- this test asserts smartapply stays
            # smartapply once page 0 succeeds normally.
            raise AssertionError("pcsx should never be called once smartapply succeeded on page 0")
        if start == 0:
            return httpx.Response(200, json={"positions": [_smartapply_position(id=1)]})
        return httpx.Response(200, json={"positions": []})

    result = await fetch_eightfold(_http(handler), _company())

    assert result.status == "ok"
    assert len(result.postings) == 1


async def test_fetch_eightfold_both_tiers_failing_is_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Not authorized for PCSX"})

    result = await fetch_eightfold(_http(handler), _company())
    assert result.status == "failed"


async def test_fetch_eightfold_genuinely_empty_board_is_ok_not_failed() -> None:
    """Both tiers return a clean, valid 200 with zero postings -- a
    genuinely empty board, not a failure (matches fetch_apple/
    fetch_workday's own success-vs-failure distinction)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/apply/v2/jobs" in str(request.url):
            return httpx.Response(200, json={"positions": []})
        return httpx.Response(200, json={"data": {"positions": []}})

    result = await fetch_eightfold(_http(handler), _company())

    assert result.status == "ok"
    assert result.postings == []


# ── extract_eightfold_position_id / fetch_eightfold_detail ─────────────


def test_extract_eightfold_position_id_from_real_shaped_url() -> None:
    assert (
        extract_eightfold_position_id("https://talent.bayer.com/careers/job/562949978450437")
        == "562949978450437"
    )


def test_extract_eightfold_position_id_returns_none_when_no_match() -> None:
    assert extract_eightfold_position_id("https://example.com/no-job-path") is None
    assert extract_eightfold_position_id("") is None


async def test_fetch_eightfold_detail_returns_full_description_and_canonical_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == (
            "https://paypal.eightfold.ai/api/apply/v2/jobs/274922097063?domain=paypal.com"
        )
        return httpx.Response(
            200,
            json={
                "job_description": "<p>" + ("Real detailed description. " * 20) + "</p>",
                "canonicalPositionUrl": "https://paypal.eightfold.ai/careers/job/274922097063",
                "work_location_option": None,  # confirmed live: unreliable, caller must ignore
            },
        )

    detail = await fetch_eightfold_detail(
        _http(handler), "paypal.eightfold.ai", "paypal.com", "274922097063"
    )

    assert detail is not None
    assert "Real detailed description." in detail.jd_text
    assert detail.apply_url == "https://paypal.eightfold.ai/careers/job/274922097063"


async def test_fetch_eightfold_detail_smartapply_shaped_regardless_of_source_tier() -> None:
    """Confirmed live: the detail endpoint works unconditionally even for
    a position whose LIST data only ever came through pcsx -- no
    tier-awareness needed in the detail fetch itself."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"job_description": "Detail text.", "canonicalPositionUrl": "https://x/job/1"}
        )

    detail = await fetch_eightfold_detail(_http(handler), "paypal.eightfold.ai", "paypal.com", "1")
    assert detail is not None
    assert detail.jd_text == "Detail text."


async def test_fetch_eightfold_detail_404_returns_none() -> None:
    detail = await fetch_eightfold_detail(
        _http(lambda r: httpx.Response(404)), "bayer.eightfold.ai", "bayer.com", "999"
    )
    assert detail is None
