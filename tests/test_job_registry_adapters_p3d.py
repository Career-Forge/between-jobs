"""Tests for the Avature and SuccessFactors adapters (Job Finder P3d,
job-finder-port.md). Fixtures are trimmed real HTML fetched directly
against real tenants (IBM/Bloomberg/Two Sigma/Deloitte x2 for Avature;
Cargill/Vodafone/ExxonMobil/adidas/EY for SuccessFactors), not invented
markup -- each adapter's regex set was sanity-checked against the full
raw pages before these tests were written."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from between_jobs.api.job_registry_adapters import DueCompany, fetch_avature, fetch_successfactors


def _http(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _avature_company(**overrides: Any) -> DueCompany:
    base: dict[str, Any] = {
        "company_id": "company-uuid-1",
        "name": "IBM",
        "ats_type": "avature",
        "slug": "ibmglobal",
        "api_base": "careers",
        "board": "avature:ibmglobal:careers",
        "etag": "",
    }
    base.update(overrides)
    return DueCompany(**base)


def _sf_company(**overrides: Any) -> DueCompany:
    base: dict[str, Any] = {
        "company_id": "company-uuid-1",
        "name": "Cargill",
        "ats_type": "successfactors",
        "slug": "cargill",
        "api_base": "jobs.cargill.com",
        "board": "successfactors:cargill:jobs.cargill.com",
        "etag": "",
    }
    base.update(overrides)
    return DueCompany(**base)


# ── Avature: template 1 -- article--card (IBM) ──────────────────────────

_IBM_CARD_HTML = """
<div class="results">
<article class="article article--card article--non-toggle" id="article--1">
  <div class="article__content">
    <div class="article__header__text">
      <h3 class="article__header__text__title article__header__text__title--2">
        <a href="https://careers.ibm.com/en_US/careers/JobDetail?jobId=129448" class="link">
          .NET Senior System Developer TS/SCI
        </a>
      </h3>
    </div>
    <span class="card-item-location">United States</span>
  </div>
</article>
</div>
"""


def _avature_handler_single_page(html: str) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        query = dict(pair.split("=") for pair in str(request.url).split("?")[1].split("&"))
        if int(query["jobOffset"]) == 0:
            return httpx.Response(200, text=html)
        return httpx.Response(200, text="<div>no more cards</div>")

    return handler


async def test_fetch_avature_parses_card_template_ibm() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/en_US/careers/SearchJobs" in str(request.url)
        return _avature_handler_single_page(_IBM_CARD_HTML)(request)

    result = await fetch_avature(_http(handler), _avature_company())

    assert result.status == "ok"
    assert len(result.postings) == 1
    p = result.postings[0]
    assert p.external_id == "129448"
    assert p.title == ".NET Senior System Developer TS/SCI"
    assert p.location == "United States"
    assert p.apply_url == "https://ibmglobal.avature.net/en_US/careers/JobDetail?jobId=129448"


# ── Avature: template 2 -- article article--result (Bloomberg) ─────────

_BLOOMBERG_JOB_URL = "https://bloomberg.avature.net/careers/JobDetail/Senior-Recruiter/21654"
_BLOOMBERG_RESULT_HTML = f"""
<article class="article article--result" id="article--1">
  <div class="article__content--result">
    <div class="article__header__text">
      <h3 class="article__header__text__title article__header__text__title--2">
        <a href="{_BLOOMBERG_JOB_URL}" class="link">
          Senior Recruiter, Corporate Functions
        </a>
      </h3>
      <span class="list-item-location">London, United Kingdom</span>
    </div>
  </div>
</article>
"""


async def test_fetch_avature_needs_bare_path_retry_and_parses_result_template() -> None:
    """Confirmed live (P3d research): Bloomberg's /en_US/ path returns a
    LanguageManager::redirectToUrl marker with no cards; the bare path
    then has the real data."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if "/en_US/" in url:
            return httpx.Response(
                200,
                text="LanguageManager::redirectToUrl::https://bloomberg.avature.net/careers/SearchJobs?...::UrlWithoutLocale",
            )
        return httpx.Response(200, text=_BLOOMBERG_RESULT_HTML)

    company = _avature_company(
        name="Bloomberg", slug="bloomberg", board="avature:bloomberg:careers"
    )
    result = await fetch_avature(_http(handler), company)

    assert result.status == "ok"
    assert any("/en_US/" in c for c in calls)
    assert any("/en_US/" not in c for c in calls)
    p = result.postings[0]
    assert p.external_id == "21654"
    assert p.location == "London, United Kingdom"
    # article--result's apply_url is the card's own href, used as-is --
    # not reconstructed.
    assert p.apply_url == _BLOOMBERG_JOB_URL


async def test_fetch_avature_custom_domain_and_nondefault_listing_page() -> None:
    """Two Sigma: a dot in slug means slug IS the host; api_base doubles
    as portalPath/listingPage."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/en_US/" in url:
            assert url.startswith("https://careers.twosigma.com/en_US/careers/OpenRoles")
            return httpx.Response(200, text="LanguageManager::redirectToUrl::x::UrlWithoutLocale")
        assert url.startswith("https://careers.twosigma.com/careers/OpenRoles")
        return _avature_handler_single_page(_BLOOMBERG_RESULT_HTML)(request)

    company = _avature_company(
        name="Two Sigma",
        slug="careers.twosigma.com",
        api_base="careers/OpenRoles",
        board="avature:careers.twosigma.com:careers/OpenRoles",
    )
    result = await fetch_avature(_http(handler), company)

    assert result.status == "ok"
    assert len(result.postings) == 1


# ── Avature: template 3 -- bare article--result (Deloitte) ─────────────

_DELOITTE_JOB_URL = (
    "https://apply.deloitte.com/en_US/careers/JobDetail/"
    "O-M-Lead-Technical-Transformation-Manager/364936"
)
_DELOITTE_BARE_RESULT_HTML = f"""
<article class="article--result " data-total="999+">
    <div class="article__content--result">
        <div class="article__header__text">
                            <h3 class="article__header__text__title">
                    <a href="{_DELOITTE_JOB_URL}" class="link">
                        O&amp;M Lead  Technical Transformation Manager
                    </a>
                </h3>
                            <div class="article__header__text__subtitle">
                                            <span>
                            Deloitte US
                        </span>
                                                                        |
                                                <span>
                            Deloitte Consulting LLP
                        </span>
                                                                        |
                                                <span>Multiple Locations</span>
                </div>
        </div>
</article>
"""


async def test_fetch_avature_parses_deloitte_bare_result_template_positionally() -> None:
    """Regression guard: Deloitte's fallback template has no dedicated
    location CSS class at all (confirmed live, real markup) -- location
    is the LAST of several plain <span> tags, not a selector match."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_DELOITTE_BARE_RESULT_HTML)

    company = _avature_company(
        name="Deloitte",
        slug="apply.deloitte.com",
        api_base="careers",
        board="avature:apply.deloitte.com:careers",
    )
    result = await fetch_avature(_http(handler), company)

    assert result.status == "ok"
    p = result.postings[0]
    assert p.external_id == "364936"
    assert p.title == "O&M Lead Technical Transformation Manager"
    assert p.location == "Multiple Locations"
    assert p.apply_url == _DELOITTE_JOB_URL


# ── Avature: pagination + failure modes ─────────────────────────────────


async def test_fetch_avature_advances_offset_by_actual_blocks_not_page_size() -> None:
    """Confirmed live (P3d research): every real tenant renders fewer
    cards than the requested 50 -- advancing by the requested page size
    would skip most of the board."""
    offsets_seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = dict(pair.split("=") for pair in str(request.url).split("?")[1].split("&"))
        offset = int(query["jobOffset"])
        offsets_seen.append(offset)
        if offset >= 18:
            return httpx.Response(200, text="<div>no more cards</div>")
        # Real IBM behavior: 9 cards per page regardless of the requested 50.
        cards = _IBM_CARD_HTML.replace("129448", str(129448 + offset)) * 1
        return httpx.Response(200, text=cards * 9 if offset == 0 else cards)

    result = await fetch_avature(_http(handler), _avature_company())

    assert result.status == "ok"
    # First call at offset 0, second call at whatever offset the actual
    # block count from page 0 produced (9, not 50).
    assert offsets_seen[0] == 0
    assert offsets_seen[1] == 9


async def test_fetch_avature_no_template_matches_is_failed() -> None:
    result = await fetch_avature(
        _http(lambda r: httpx.Response(200, text="<div>nothing here</div>")), _avature_company()
    )
    assert result.status == "failed"


async def test_fetch_avature_server_error_is_failed() -> None:
    result = await fetch_avature(_http(lambda r: httpx.Response(500)), _avature_company())
    assert result.status == "failed"


# ── SuccessFactors ───────────────────────────────────────────────────────

_CARGILL_JOB_PATH = "/job/Izegem-Packaging-Material-Supply-Planner-West-8870/1365626157/"
_CARGILL_LOCATION_BLOCK = """
            <div id="job-1365626157-desktop-section-location" class="section-field location">
                <span id="job-1365626157-desktop-section-location-label">Location</span>
                <div id="job-1365626157-desktop-section-location-value">
                    Izegem, West Flanders, Belgium
                </div>
            </div>"""
_CARGILL_TILE_HTML = f"""
<ul>
<li class="job-tile job-id-1365626157 job-row-index-1" data-url="{_CARGILL_JOB_PATH}">
    <div class="job-tile-cell">
        <div class="oneline">
            <div class="tiletitle">
                <a class="jobTitle-link fontcolorx" href="/job/x/1365626157/">
                    Packaging Material Supply Planner
                </a>
            </div>
        </div>
        <div class="oneline">{_CARGILL_LOCATION_BLOCK}
            <div id="job-1365626157-desktop-section-department" class="section-field department">
                <span id="job-1365626157-desktop-section-department-label">Department</span>
                <div id="job-1365626157-desktop-section-department-value">Operations
                </div>
            </div>
        </div>
    </div>
</li>
</ul>
"""


def _sf_handler_single_page(html: str) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        query = dict(pair.split("=") for pair in str(request.url).split("?")[1].split("&"))
        if int(query["startrow"]) == 0:
            return httpx.Response(200, text=html)
        return httpx.Response(200, text="<div>no more tiles</div>")

    return handler


async def test_fetch_successfactors_parses_real_tile_and_anchors_on_location_value() -> None:
    """Regression guard: the location div's id ends in "-location-value",
    not a generic "-value" -- the same tile also carries a sibling
    "-department-value" div with an identical suffix that must not be
    mismatched."""
    result = await fetch_successfactors(
        _http(_sf_handler_single_page(_CARGILL_TILE_HTML)), _sf_company()
    )

    assert result.status == "ok"
    p = result.postings[0]
    assert p.external_id == "1365626157"
    assert p.title == "Packaging Material Supply Planner"
    assert p.location == "Izegem, West Flanders, Belgium"
    assert p.apply_url == f"https://jobs.cargill.com{_CARGILL_JOB_PATH}"


async def test_fetch_successfactors_missing_location_field_is_empty_not_error() -> None:
    """Confirmed live: 3 of 5 real tenants (ExxonMobil, adidas, EY) render
    no location field on the tile at all -- a real per-tenant config
    choice, not a markup break."""
    html = _CARGILL_TILE_HTML.replace(_CARGILL_LOCATION_BLOCK, "")

    result = await fetch_successfactors(_http(_sf_handler_single_page(html)), _sf_company())

    assert result.status == "ok"
    assert result.postings[0].location == ""


async def test_fetch_successfactors_advances_by_actual_tile_count_not_hardcoded_25() -> None:
    """Confirmed live: adidas's real per-tenant page size is 50, not the
    25 n8n's own source assumed as a platform constant -- advancing by a
    hardcoded 25 would re-fetch half of every page."""
    offsets_seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = dict(pair.split("=") for pair in str(request.url).split("?")[1].split("&"))
        offset = int(query["startrow"])
        offsets_seen.append(offset)
        if offset >= 50:
            return httpx.Response(200, text="<div>no more tiles</div>")
        return httpx.Response(200, text=_CARGILL_TILE_HTML * 50)

    result = await fetch_successfactors(_http(handler), _sf_company())

    assert result.status == "ok"
    assert offsets_seen[0] == 0
    assert offsets_seen[1] == 50  # advanced by the ACTUAL 50 tiles returned, not a hardcoded 25


async def test_fetch_successfactors_stops_on_genuinely_empty_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = dict(pair.split("=") for pair in str(request.url).split("?")[1].split("&"))
        if int(query["startrow"]) == 0:
            return httpx.Response(200, text=_CARGILL_TILE_HTML)
        return httpx.Response(200, text="<div>done</div>")

    result = await fetch_successfactors(_http(handler), _sf_company())

    assert result.status == "ok"
    assert len(result.postings) == 1


async def test_fetch_successfactors_no_tiles_first_page_is_failed() -> None:
    result = await fetch_successfactors(
        _http(lambda r: httpx.Response(200, text="<div>empty account</div>")), _sf_company()
    )
    assert result.status == "failed"


async def test_fetch_successfactors_server_error_is_failed() -> None:
    result = await fetch_successfactors(_http(lambda r: httpx.Response(500)), _sf_company())
    assert result.status == "failed"
