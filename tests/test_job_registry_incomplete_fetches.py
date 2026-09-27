"""An adapter may report "ok" only for a board's complete listing.

"ok" tells run_poll_tick the fetch is everything the board has, so it
closes every posting the fetch didn't return. On 2026-09-26, the first
ticks to complete after 2026-08-31 did exactly that off incomplete
fetches: Amazon's 2,000-posting page budget closed 14,367 open postings,
and every other capped board lost everything past its own budget.

So a fetch that stops for any reason other than a positive end-of-board
signal -- its page budget ran out, a later page failed, a page came back
in a shape the adapter can't read -- reports "partial" (upsert, close
nothing) or "failed". Each adapter's own end signal (a short page, the
API's own total, Avature's "No jobs found" card, a repeated page) still
reports "ok", including when the board ends on the last budgeted request."""

from __future__ import annotations

import json
from typing import Any

import httpx

from between_jobs.api.job_registry_adapters import (
    DueCompany,
    fetch_amazon,
    fetch_apple,
    fetch_avature,
    fetch_deshaw,
    fetch_eightfold,
    fetch_oracle,
    fetch_smartrecruiters,
    fetch_successfactors,
    fetch_workday,
)


def _http(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _company(ats_type: str, slug: str, api_base: str = "") -> DueCompany:
    return DueCompany(
        company_id="company-uuid-1",
        name="Acme",
        ats_type=ats_type,
        slug=slug,
        api_base=api_base,
        board=f"{ats_type}:{slug}:{api_base}",
        etag="",
    )


def _query(request: httpx.Request) -> dict[str, str]:
    return dict(pair.split("=", 1) for pair in str(request.url).split("?")[1].split("&"))


class _Counter:
    def __init__(self) -> None:
        self.calls = 0


# ── Workday: 5 pages x 20, ends on its own `total` ──────────────────────

_WORKDAY = _company("workday", "acmecareers", "acme.wd5")


def _workday_handler(total: int | None, counter: _Counter, pages_served: int = 10_000) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        counter.calls += 1
        offset = json.loads(request.content)["offset"]
        size = 20 if offset // 20 < pages_served else 0
        if total is not None:
            size = max(0, min(size, total - offset))
        postings = [{"title": "Role", "externalPath": f"/job/r{offset + i}"} for i in range(size)]
        body: dict[str, Any] = {"jobPostings": postings}
        if total is not None:
            body["total"] = total
        return httpx.Response(200, json=body)

    return handler


async def test_workday_budget_exhausted_is_partial() -> None:
    counter = _Counter()
    result = await fetch_workday(_http(_workday_handler(100_000, counter)), _WORKDAY)

    assert result.status == "partial"
    assert len(result.postings) == 100
    assert counter.calls == 5


async def test_workday_board_ending_exactly_at_the_budget_is_ok() -> None:
    result = await fetch_workday(_http(_workday_handler(100, _Counter())), _WORKDAY)

    assert result.status == "ok"
    assert len(result.postings) == 100


async def test_workday_empty_later_page_short_of_total_is_partial() -> None:
    result = await fetch_workday(_http(_workday_handler(100, _Counter(), pages_served=1)), _WORKDAY)

    assert result.status == "partial"
    assert len(result.postings) == 20


async def test_workday_missing_total_never_ends_the_listing_after_page_0() -> None:
    result = await fetch_workday(
        _http(_workday_handler(None, _Counter(), pages_served=3)), _WORKDAY
    )

    assert result.status == "partial"
    assert len(result.postings) == 60


async def test_workday_empty_board_is_ok() -> None:
    result = await fetch_workday(_http(_workday_handler(0, _Counter())), _WORKDAY)

    assert result.status == "ok"
    assert result.postings == []


async def test_workday_empty_first_page_with_a_nonzero_total_is_partial() -> None:
    result = await fetch_workday(_http(_workday_handler(40, _Counter(), pages_served=0)), _WORKDAY)

    assert result.status == "partial"
    assert result.postings == []


# ── SmartRecruiters: 20 pages x 100, ends on `totalFound` ───────────────

_SMARTRECRUITERS = _company("smartrecruiters", "acme")


def _smartrecruiters_handler(total_found: int, counter: _Counter) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        counter.calls += 1
        offset = int(_query(request)["offset"])
        size = max(0, min(100, total_found - offset))
        content = [{"id": str(offset + i), "name": "Role", "location": {}} for i in range(size)]
        return httpx.Response(200, json={"totalFound": total_found, "content": content})

    return handler


async def test_smartrecruiters_budget_exhausted_is_partial() -> None:
    counter = _Counter()
    result = await fetch_smartrecruiters(
        _http(_smartrecruiters_handler(5_000, counter)), _SMARTRECRUITERS
    )

    assert result.status == "partial"
    assert len(result.postings) == 2_000
    assert counter.calls == 20


async def test_smartrecruiters_board_ending_exactly_at_the_budget_is_ok() -> None:
    result = await fetch_smartrecruiters(
        _http(_smartrecruiters_handler(2_000, _Counter())), _SMARTRECRUITERS
    )

    assert result.status == "ok"
    assert len(result.postings) == 2_000


async def test_smartrecruiters_first_page_that_is_not_a_postings_page_is_failed() -> None:
    bodies: list[Any] = [[], {"totalFound": 3}]
    for body in bodies:
        result = await fetch_smartrecruiters(
            _http(lambda r, body=body: httpx.Response(200, json=body)), _SMARTRECRUITERS
        )
        assert result.status == "failed"


# ── Amazon: 20 pages x 100, ends on a short page ────────────────────────

_AMAZON = _company("amazon", "amazon")


def _amazon_handler(total: int, counter: _Counter) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        counter.calls += 1
        offset = int(_query(request)["offset"])
        size = max(0, min(100, total - offset))
        return httpx.Response(
            200, json={"jobs": [{"id": str(offset + i), "title": "Role"} for i in range(size)]}
        )

    return handler


async def test_amazon_budget_exhausted_is_partial() -> None:
    counter = _Counter()
    result = await fetch_amazon(_http(_amazon_handler(10_000, counter)), _AMAZON)

    assert result.status == "partial"
    assert len(result.postings) == 2_000
    assert counter.calls == 20


async def test_amazon_board_ending_on_the_last_budgeted_page_is_ok() -> None:
    counter = _Counter()
    result = await fetch_amazon(_http(_amazon_handler(1_950, counter)), _AMAZON)

    assert result.status == "ok"
    assert len(result.postings) == 1_950
    assert counter.calls == 20


async def test_amazon_first_page_that_is_not_a_search_page_is_failed() -> None:
    bodies: list[Any] = [[], {"error": "unavailable"}, {"hits": 3}]
    for body in bodies:
        result = await fetch_amazon(
            _http(lambda r, body=body: httpx.Response(200, json=body)), _AMAZON
        )
        assert result.status == "failed"


# ── Apple: 10 pages x 20, ends on a short page ──────────────────────────

_APPLE = _company("apple", "apple")


def _apple_handler(total: int, counter: _Counter) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        counter.calls += 1
        page = json.loads(request.content)["page"]
        size = max(0, min(20, total - (page - 1) * 20))
        results = [{"positionId": f"{page}-{i}", "postingTitle": "Role"} for i in range(size)]
        return httpx.Response(200, json={"res": {"searchResults": results}})

    return handler


async def test_apple_budget_exhausted_is_partial() -> None:
    counter = _Counter()
    result = await fetch_apple(_http(_apple_handler(6_229, counter)), _APPLE)

    assert result.status == "partial"
    assert len(result.postings) == 200
    assert counter.calls == 10


async def test_apple_board_ending_on_the_last_budgeted_page_is_ok() -> None:
    counter = _Counter()
    result = await fetch_apple(_http(_apple_handler(190, counter)), _APPLE)

    assert result.status == "ok"
    assert len(result.postings) == 190
    assert counter.calls == 10


# ── Oracle: 30 pages x 20, ends on `TotalJobsCount` or a short page ─────

_ORACLE = _company("oracle", "CX_1001", "jpmc.fa.oraclecloud.com")


def _oracle_handler(
    total: int, counter: _Counter, *, report_total: bool = True, pages_served: int = 10_000
) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        counter.calls += 1
        finder = str(request.url).split("finder=findReqs;")[1]
        offset = int(dict(p.split("=") for p in finder.split(","))["offset"])
        size = max(0, min(20, total - offset)) if offset // 20 < pages_served else 0
        item: dict[str, Any] = {
            "requisitionList": [{"Id": offset + i, "Title": "Role"} for i in range(size)]
        }
        if report_total:
            item["TotalJobsCount"] = total
        return httpx.Response(200, json={"items": [item]})

    return handler


async def test_oracle_budget_exhausted_is_partial() -> None:
    counter = _Counter()
    result = await fetch_oracle(_http(_oracle_handler(7_181, counter)), _ORACLE)

    assert result.status == "partial"
    assert len(result.postings) == 600
    assert counter.calls == 30


async def test_oracle_board_ending_exactly_at_the_budget_is_ok() -> None:
    result = await fetch_oracle(_http(_oracle_handler(600, _Counter())), _ORACLE)

    assert result.status == "ok"
    assert len(result.postings) == 600


async def test_oracle_empty_later_page_short_of_total_is_partial() -> None:
    result = await fetch_oracle(_http(_oracle_handler(300, _Counter(), pages_served=2)), _ORACLE)

    assert result.status == "partial"
    assert len(result.postings) == 40


async def test_oracle_missing_total_jobs_count_never_ends_the_listing_after_page_0() -> None:
    result = await fetch_oracle(_http(_oracle_handler(45, _Counter(), report_total=False)), _ORACLE)

    assert result.status == "ok"  # ended on its short third page, not after the first
    assert len(result.postings) == 45


# ── Eightfold: 30 pages x 10, ends on a short page ──────────────────────

_EIGHTFOLD = _company("eightfold", "acme.com", "acme.eightfold.ai")


def _eightfold_handler(total: int, counter: _Counter) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        counter.calls += 1
        start = int(_query(request)["start"])
        positions = [
            {
                "id": start + i,
                "name": "Role",
                "canonicalPositionUrl": f"https://acme.eightfold.ai/careers/job/{start + i}",
            }
            for i in range(max(0, min(10, total - start)))
        ]
        return httpx.Response(200, json={"positions": positions})

    return handler


async def test_eightfold_budget_exhausted_is_partial() -> None:
    counter = _Counter()
    result = await fetch_eightfold(_http(_eightfold_handler(21_993, counter)), _EIGHTFOLD)

    assert result.status == "partial"
    assert len(result.postings) == 300
    assert counter.calls == 30


async def test_eightfold_board_ending_on_the_last_budgeted_page_is_ok() -> None:
    counter = _Counter()
    result = await fetch_eightfold(_http(_eightfold_handler(295, counter)), _EIGHTFOLD)

    assert result.status == "ok"
    assert len(result.postings) == 295
    assert counter.calls == 30


# ── Avature: 10 pages; ends on its "No jobs found" card ─────────────────

_AVATURE = _company("avature", "acme", "careers")

# Past the end of a listing, and on an empty board (confirmed live
# 2026-09-27, Bloomberg and usijobs.deloitte.com).
_AVATURE_NO_JOBS = """
<article class="article article--result">
  <h3 class="article__header__text__title title title--04">
    No jobs found - There are currently no open roles matching your search.
  </h3>
</article>"""

# The generic error page Avature serves with a 200 (confirmed live
# 2026-09-27 on both Deloitte tenants and Bloomberg): no cards at all.
_AVATURE_ERROR_PAGE = "<html><body><h1>Oops... Something went wrong</h1></body></html>"


def _avature_cards(first_id: int, count: int) -> str:
    return "".join(
        f"""
<article class="article article--card article--non-toggle">
  <h3 class="article__header__text__title">
    <a href="https://acme.avature.net/en_US/careers/JobDetail?jobId={first_id + i}">Role</a>
  </h3>
  <span class="card-item-location">United States</span>
</article>"""
        for i in range(count)
    )


def _avature_handler(pages: int, after: str | int, counter: _Counter) -> Any:
    """Serves `pages` pages of 9 cards, then `after`: a body, or a status."""

    def handler(request: httpx.Request) -> httpx.Response:
        counter.calls += 1
        offset = int(_query(request)["jobOffset"])
        if offset < pages * 9:
            return httpx.Response(200, text=_avature_cards(offset, 9))
        if isinstance(after, int):
            return httpx.Response(after)
        return httpx.Response(200, text=after)

    return handler


async def test_avature_budget_exhausted_is_partial() -> None:
    counter = _Counter()
    result = await fetch_avature(_http(_avature_handler(100, "", counter)), _AVATURE)

    assert result.status == "partial"
    assert len(result.postings) == 90
    assert counter.calls == 10


async def test_avature_board_ending_on_the_last_budgeted_request_is_ok() -> None:
    counter = _Counter()
    result = await fetch_avature(_http(_avature_handler(9, _AVATURE_NO_JOBS, counter)), _AVATURE)

    assert result.status == "ok"
    assert len(result.postings) == 81
    assert counter.calls == 10


async def test_avature_error_page_served_with_a_200_on_a_later_page_is_partial() -> None:
    result = await fetch_avature(
        _http(_avature_handler(3, _AVATURE_ERROR_PAGE, _Counter())), _AVATURE
    )

    assert result.status == "partial"
    assert len(result.postings) == 27


async def test_avature_later_page_failure_is_partial() -> None:
    for after in (503, "timeout"):

        def handler(request: httpx.Request, after: str | int = after) -> httpx.Response:
            if int(_query(request)["jobOffset"]) == 0:
                return httpx.Response(200, text=_avature_cards(0, 9))
            if after == "timeout":
                raise httpx.ConnectTimeout("timed out", request=request)
            return httpx.Response(int(after))

        result = await fetch_avature(_http(handler), _AVATURE)

        assert result.status == "partial"
        assert len(result.postings) == 9


async def test_avature_failed_bare_path_retry_on_a_later_page_is_partial() -> None:
    """Page 0 answers under /en_US/, so the locale path stays in use; a
    later /en_US/ page redirects and its bare-path retry fails."""
    for retry in (503, "timeout"):

        def handler(request: httpx.Request, retry: str | int = retry) -> httpx.Response:
            offset = int(_query(request)["jobOffset"])
            if offset == 0:
                return httpx.Response(200, text=_avature_cards(0, 9))
            if "/en_US/" in str(request.url):
                return httpx.Response(
                    200, text="LanguageManager::redirectToUrl::x::UrlWithoutLocale"
                )
            if retry == "timeout":
                raise httpx.ConnectTimeout("timed out", request=request)
            return httpx.Response(int(retry))

        result = await fetch_avature(_http(handler), _AVATURE)

        assert result.status == "partial"
        assert len(result.postings) == 9


async def test_avature_first_page_whose_cards_no_longer_parse_is_failed() -> None:
    """Bloomberg's anchors now put class before href (live, 2026-09-27),
    which the title pattern doesn't match: blocks, but no jobs and no
    "No jobs found" card. That's a broken template, not an empty board."""
    drifted = """
<article class="article article--result">
  <h3 class="article__header__text__title title title--04">
    <a class="link" href="https://acme.avature.net/careers/JobDetail/Engineer/123">Engineer</a>
  </h3>
</article>"""

    result = await fetch_avature(_http(lambda r: httpx.Response(200, text=drifted)), _AVATURE)

    assert result.status == "failed"


async def test_avature_empty_board_is_ok() -> None:
    result = await fetch_avature(
        _http(lambda r: httpx.Response(200, text=_AVATURE_NO_JOBS)), _AVATURE
    )

    assert result.status == "ok"
    assert result.postings == []


# ── SuccessFactors: 8 pages; ends on an empty or a repeated page ────────

_SUCCESSFACTORS = _company("successfactors", "ey", "careers.ey.com")


def _successfactors_tiles(first_id: int, count: int) -> str:
    return "".join(
        f"""
<li class="job-tile job-id-{first_id + i} job-row-index-{i}" data-url="/job/x/{first_id + i}/">
  <a class="jobTitle-link" href="/job/x/{first_id + i}/">Role</a>
</li>"""
        for i in range(count)
    )


def _successfactors_handler(pages: int, after: str, counter: _Counter) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        counter.calls += 1
        offset = int(_query(request)["startrow"])
        if offset < pages * 25:
            return httpx.Response(200, text=_successfactors_tiles(1_000 + offset, 25))
        return httpx.Response(200, text=after)

    return handler


async def test_successfactors_budget_exhausted_is_partial() -> None:
    counter = _Counter()
    result = await fetch_successfactors(
        _http(_successfactors_handler(100, "", counter)), _SUCCESSFACTORS
    )

    assert result.status == "partial"
    assert len(result.postings) == 200
    assert counter.calls == 8


async def test_successfactors_board_ending_on_the_last_budgeted_request_is_ok() -> None:
    counter = _Counter()
    result = await fetch_successfactors(
        _http(_successfactors_handler(7, "<!DOCTYPE html>", counter)), _SUCCESSFACTORS
    )

    assert result.status == "ok"
    assert len(result.postings) == 175
    assert counter.calls == 8


async def test_successfactors_tenant_repeating_page_0_past_the_end_is_ok() -> None:
    """adidas, ExxonMobil and Cargill never serve an empty page: past
    their last tile they serve page 0's again (live, 2026-09-27)."""
    counter = _Counter()
    result = await fetch_successfactors(
        _http(_successfactors_handler(2, _successfactors_tiles(1_000, 25), counter)),
        _SUCCESSFACTORS,
    )

    assert result.status == "ok"
    assert len({p.external_id for p in result.postings}) == 50
    assert counter.calls == 3


async def test_successfactors_later_page_failure_is_partial() -> None:
    for after in (503, "timeout"):

        def handler(request: httpx.Request, after: str | int = after) -> httpx.Response:
            if int(_query(request)["startrow"]) == 0:
                return httpx.Response(200, text=_successfactors_tiles(1_000, 25))
            if after == "timeout":
                raise httpx.ConnectTimeout("timed out", request=request)
            return httpx.Response(int(after))

        result = await fetch_successfactors(_http(handler), _SUCCESSFACTORS)

        assert result.status == "partial"
        assert len(result.postings) == 25


async def test_successfactors_first_page_whose_tiles_no_longer_parse_is_failed() -> None:
    drifted = '<li class="job-tile job-id-new-format"><a class="jobTitle-link">Role</a></li>'

    result = await fetch_successfactors(
        _http(lambda r: httpx.Response(200, text=drifted)), _SUCCESSFACTORS
    )

    assert result.status == "failed"


# ── D.E. Shaw: one page; its own fetch-error flag is a failure ──────────


def _deshaw_page(page_props: dict[str, Any]) -> str:
    data = json.dumps({"props": {"pageProps": page_props}})
    return f'<html><script id="__NEXT_DATA__" type="application/json">{data}</script></html>'


async def test_deshaw_page_flagging_its_own_job_fetch_error_is_failed() -> None:
    """The careers page still renders (200) in maintenance mode, with
    jobsFetchingError set and no job lists; reading that as an empty
    board would close every D.E. Shaw posting."""
    for page_props in (
        {"jobsFetchingError": True, "regularJobs": [], "internships": []},
        {"jobsFetchingError": True},
        {"jobsFetchingError": False},
    ):
        result = await fetch_deshaw(
            _http(lambda r, p=page_props: httpx.Response(200, text=_deshaw_page(p))),
            _company("deshaw", "deshaw"),
        )
        assert result.status == "failed"


async def test_deshaw_genuinely_empty_lists_are_ok() -> None:
    page = _deshaw_page({"jobsFetchingError": False, "regularJobs": [], "internships": []})

    result = await fetch_deshaw(
        _http(lambda r: httpx.Response(200, text=page)), _company("deshaw", "deshaw")
    )

    assert result.status == "ok"
    assert result.postings == []
