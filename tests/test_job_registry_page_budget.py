"""A paginated adapter that runs out of its per-tick page budget before the
board runs out of postings must report "partial", never "ok".

"ok" tells run_poll_tick the fetch is the board's complete listing, so it
closes every posting the fetch didn't return. On 2026-09-26, the first
ticks to complete after 2026-08-31 did exactly that: Amazon's 2,000-posting
budget closed 14,367 open postings, and every other capped board lost
everything past its own budget. These tests serve each adapter a board
that never ends and check the fetch stops at its budget as "partial";
where the adapter can tell that the board ended exactly at the budget,
that case stays "ok"."""

from __future__ import annotations

import json
from typing import Any

import httpx

from between_jobs.api.job_registry_adapters import (
    DueCompany,
    fetch_amazon,
    fetch_apple,
    fetch_avature,
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


# ── Workday: 5 pages x 20 ───────────────────────────────────────────────


def _workday_handler(total: int, counter: _Counter) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        counter.calls += 1
        offset = json.loads(request.content)["offset"]
        size = max(0, min(20, total - offset))
        postings = [{"title": "Role", "externalPath": f"/job/r{offset + i}"} for i in range(size)]
        return httpx.Response(200, json={"total": total, "jobPostings": postings})

    return handler


async def test_workday_budget_exhausted_is_partial() -> None:
    counter = _Counter()
    result = await fetch_workday(
        _http(_workday_handler(100_000, counter)),
        _company("workday", "acmecareers", "acme.wd5"),
    )

    assert result.status == "partial"
    assert len(result.postings) == 100
    assert counter.calls == 5


async def test_workday_board_ending_exactly_at_the_budget_is_ok() -> None:
    counter = _Counter()
    result = await fetch_workday(
        _http(_workday_handler(100, counter)), _company("workday", "acmecareers", "acme.wd5")
    )

    assert result.status == "ok"
    assert len(result.postings) == 100


# ── SmartRecruiters: 20 pages x 100 ─────────────────────────────────────


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
        _http(_smartrecruiters_handler(5_000, counter)), _company("smartrecruiters", "acme")
    )

    assert result.status == "partial"
    assert len(result.postings) == 2_000
    assert counter.calls == 20


async def test_smartrecruiters_board_ending_exactly_at_the_budget_is_ok() -> None:
    counter = _Counter()
    result = await fetch_smartrecruiters(
        _http(_smartrecruiters_handler(2_000, counter)), _company("smartrecruiters", "acme")
    )

    assert result.status == "ok"
    assert len(result.postings) == 2_000


# ── Amazon: 20 pages x 100 ──────────────────────────────────────────────


async def test_amazon_budget_exhausted_is_partial() -> None:
    counter = _Counter()

    def handler(request: httpx.Request) -> httpx.Response:
        counter.calls += 1
        offset = int(_query(request)["offset"])
        jobs = [{"id": str(offset + i), "title": "Role"} for i in range(100)]
        return httpx.Response(200, json={"jobs": jobs})

    result = await fetch_amazon(_http(handler), _company("amazon", "amazon"))

    assert result.status == "partial"
    assert len(result.postings) == 2_000
    assert counter.calls == 20


# ── Apple: 10 pages x 20 ────────────────────────────────────────────────


async def test_apple_budget_exhausted_is_partial() -> None:
    counter = _Counter()

    def handler(request: httpx.Request) -> httpx.Response:
        counter.calls += 1
        page = json.loads(request.content)["page"]
        results = [{"positionId": f"{page}-{i}", "postingTitle": "Role"} for i in range(20)]
        return httpx.Response(200, json={"res": {"searchResults": results}})

    result = await fetch_apple(_http(handler), _company("apple", "apple"))

    assert result.status == "partial"
    assert len(result.postings) == 200
    assert counter.calls == 10


# ── Oracle: 30 pages x 20 ───────────────────────────────────────────────


def _oracle_handler(total: int, counter: _Counter) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        counter.calls += 1
        finder = str(request.url).split("finder=findReqs;")[1]
        offset = int(dict(p.split("=") for p in finder.split(","))["offset"])
        size = max(0, min(20, total - offset))
        reqs = [{"Id": offset + i, "Title": "Role"} for i in range(size)]
        return httpx.Response(
            200, json={"items": [{"requisitionList": reqs, "TotalJobsCount": total}]}
        )

    return handler


async def test_oracle_budget_exhausted_is_partial() -> None:
    counter = _Counter()
    result = await fetch_oracle(
        _http(_oracle_handler(7_181, counter)),
        _company("oracle", "CX_1001", "jpmc.fa.oraclecloud.com"),
    )

    assert result.status == "partial"
    assert len(result.postings) == 600
    assert counter.calls == 30


async def test_oracle_board_ending_exactly_at_the_budget_is_ok() -> None:
    counter = _Counter()
    result = await fetch_oracle(
        _http(_oracle_handler(600, counter)),
        _company("oracle", "CX_1001", "jpmc.fa.oraclecloud.com"),
    )

    assert result.status == "ok"
    assert len(result.postings) == 600


# ── Eightfold: 30 pages x 10 ────────────────────────────────────────────


async def test_eightfold_budget_exhausted_is_partial() -> None:
    counter = _Counter()

    def handler(request: httpx.Request) -> httpx.Response:
        counter.calls += 1
        start = int(_query(request)["start"])
        positions = [
            {
                "id": start + i,
                "name": "Role",
                "canonicalPositionUrl": f"https://acme.eightfold.ai/careers/job/{start + i}",
            }
            for i in range(10)
        ]
        return httpx.Response(200, json={"positions": positions})

    result = await fetch_eightfold(
        _http(handler), _company("eightfold", "acme.com", "acme.eightfold.ai")
    )

    assert result.status == "partial"
    assert len(result.postings) == 300
    assert counter.calls == 30


# ── Avature: 10 pages, as many cards as the tenant renders ──────────────


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


async def test_avature_budget_exhausted_is_partial() -> None:
    counter = _Counter()

    def handler(request: httpx.Request) -> httpx.Response:
        counter.calls += 1
        offset = int(_query(request)["jobOffset"])
        return httpx.Response(200, text=_avature_cards(offset, 9))

    result = await fetch_avature(_http(handler), _company("avature", "acme", "careers"))

    assert result.status == "partial"
    assert len(result.postings) == 90
    assert counter.calls == 10


async def test_avature_later_page_failure_is_partial() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if int(_query(request)["jobOffset"]) == 0:
            return httpx.Response(200, text=_avature_cards(0, 9))
        return httpx.Response(503)

    result = await fetch_avature(_http(handler), _company("avature", "acme", "careers"))

    assert result.status == "partial"
    assert len(result.postings) == 9


# ── SuccessFactors: 8 pages, as many tiles as the tenant renders ────────


def _successfactors_tiles(first_id: int, count: int) -> str:
    return "".join(
        f"""
<li class="job-tile job-id-{first_id + i} job-row-index-{i}" data-url="/job/x/{first_id + i}/">
  <a class="jobTitle-link" href="/job/x/{first_id + i}/">Role</a>
</li>"""
        for i in range(count)
    )


async def test_successfactors_budget_exhausted_is_partial() -> None:
    counter = _Counter()

    def handler(request: httpx.Request) -> httpx.Response:
        counter.calls += 1
        offset = int(_query(request)["startrow"])
        return httpx.Response(200, text=_successfactors_tiles(1_000 + offset, 25))

    result = await fetch_successfactors(
        _http(handler), _company("successfactors", "ey", "careers.ey.com")
    )

    assert result.status == "partial"
    assert len(result.postings) == 200
    assert counter.calls == 8


async def test_successfactors_later_page_failure_is_partial() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if int(_query(request)["startrow"]) == 0:
            return httpx.Response(200, text=_successfactors_tiles(1_000, 25))
        raise httpx.ConnectTimeout("timed out", request=request)

    result = await fetch_successfactors(
        _http(handler), _company("successfactors", "ey", "careers.ey.com")
    )

    assert result.status == "partial"
    assert len(result.postings) == 25
