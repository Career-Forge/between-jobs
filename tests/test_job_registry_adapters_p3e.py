"""Tests for the Google adapter (Job Finder P3e, job-finder-port.md).
Fixture markup is shaped from a real live fetch against Google's own
careers site (`about/careers/applications/jobs/results`) -- confirmed
live: `<li class="lLd3Je" ssk='...'>` cards, `<h3 class="QJPWVe">`
titles, `class="r0wTof ">` location spans, relative `href="jobs/
results/..."` hrefs. `fetch_google` itself is deliberately unaware of
"sweep" as a multi-tick concept -- it only reports `hit_end` for its own
tick's own page range; the sweep bookkeeping lives in
job_registry_poller.py and is tested there."""

from __future__ import annotations

from typing import Any

import httpx

from between_jobs.api.job_registry_adapters import DueCompany, fetch_google

_GOOGLE_BASE_URL = "https://www.google.com/about/careers/applications/"


def _http(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _company(**overrides: Any) -> DueCompany:
    base: dict[str, Any] = {
        "company_id": "company-uuid-1",
        "name": "Google",
        "ats_type": "google",
        "slug": "google",
        "api_base": _GOOGLE_BASE_URL,
        "board": f"google:google:{_GOOGLE_BASE_URL}",
        "etag": "",
    }
    base.update(overrides)
    return DueCompany(**base)


def _card(
    ssk: str = "100397577702122182",
    title: str = "Software Engineer III, Infrastructure",
    location: str = "New York, NY, USA",
    href: str = "jobs/results/100397577702122182-software-engineer-iii",
) -> str:
    return (
        f" ssk='18:{ssk}' jsdata=\"foo\">"
        f'<div><h3 class="QJPWVe">{title}</h3>'
        f'<span class="r0wTof ">{location}<span/>'
        f'<a href="{href}?q=Software%20Engineer">Learn more</a></div></li>'
    )


def _page(cards: list[str]) -> str:
    body = "".join(f'<li class="lLd3Je"{c}' for c in cards)
    return f"<html><body><ul>{body}</ul></body></html>"


async def test_fetch_google_parses_a_real_shaped_card() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "page=1" in str(request.url)
        return httpx.Response(200, text=_page([_card()]))  # < page size -> hit_end, one call

    result = await fetch_google(_http(handler), _company(etag=""))

    assert result.status == "ok"
    posting = result.postings[0]
    assert posting.external_id == "100397577702122182"
    assert posting.title == "Software Engineer III, Infrastructure"
    assert posting.location == "New York, NY, USA"
    assert posting.apply_url == (
        f"{_GOOGLE_BASE_URL}jobs/results/100397577702122182-software-engineer-iii"
    )
    assert posting.jd_text == ""


async def test_fetch_google_ssk_without_bucket_prefix() -> None:
    card = " ssk='99887766'><h3 class=\"QJPWVe\">Role</h3></li>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_page([card]))

    result = await fetch_google(_http(handler), _company())

    assert result.postings[0].external_id == "99887766"


async def test_fetch_google_resumes_from_company_etag_cursor() -> None:
    seen_pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(str(request.url).rsplit("page=", 1)[1])
        seen_pages.append(page)
        return httpx.Response(200, text=_page([]))  # empty -> hit_end immediately

    result = await fetch_google(_http(handler), _company(etag="25"))

    assert seen_pages == [25]
    assert result.hit_end is True
    assert result.new_etag == "1"  # sweep completed -> next sweep restarts at page 1


async def test_fetch_google_pagination_is_disjoint_across_ticks() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(str(request.url).rsplit("page=", 1)[1])
        return httpx.Response(200, text=_page([_card(ssk=f"{page}00{i}") for i in range(20)]))

    tick_one = await fetch_google(_http(handler), _company(etag=""))
    assert tick_one.hit_end is False  # ran out of per-tick page budget, board isn't done
    assert tick_one.new_etag is not None

    tick_two = await fetch_google(_http(handler), _company(etag=tick_one.new_etag))

    ids_one = {p.external_id for p in tick_one.postings}
    ids_two = {p.external_id for p in tick_two.postings}
    assert ids_one.isdisjoint(ids_two)


async def test_fetch_google_short_page_sets_hit_end() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_page([_card()] * 7))  # fewer than a full page

    result = await fetch_google(_http(handler), _company())

    assert result.status == "ok"
    assert result.hit_end is True
    assert result.new_etag == "1"
    assert len(result.postings) == 7


async def test_fetch_google_genuinely_empty_page_is_ok_not_failed() -> None:
    """Unlike Avature/SuccessFactors' own "silent-empty trap" fix (P3d),
    Google's safety net against a broken scraper lives at the SWEEP
    level (job_registry_poller.py's own zero-sweep-guard), not the
    per-tick adapter level -- matching n8n's own real s151 fix. A single
    genuinely empty page here is a normal, valid signal that the sweep
    has reached the end of the board."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_page([]))

    result = await fetch_google(_http(handler), _company())

    assert result.status == "ok"
    assert result.hit_end is True
    assert result.postings == []


async def test_fetch_google_http_error_on_first_page_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    result = await fetch_google(_http(handler), _company())

    assert result.status == "failed"


async def test_fetch_google_non_200_on_first_page_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="")

    result = await fetch_google(_http(handler), _company())

    assert result.status == "failed"


async def test_fetch_google_error_mid_sweep_still_returns_partial_postings() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, text=_page([_card()] * 20))
        return httpx.Response(500, text="")

    result = await fetch_google(_http(handler), _company())

    assert result.status == "ok"
    assert len(result.postings) == 20
    assert result.hit_end is False


async def test_fetch_google_card_missing_ssk_is_skipped() -> None:
    malformed_card = '><h3 class="QJPWVe">No ssk here</h3></li>'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_page([malformed_card]))

    result = await fetch_google(_http(handler), _company())

    assert result.postings == []
