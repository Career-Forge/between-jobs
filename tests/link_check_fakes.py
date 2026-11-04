"""Test doubles for the link check (api/link_check.py and api/link_fetch.py): a stand-in for the
employers' job boards, built on httpx.MockTransport, and a clock that sleeps without waiting.

`FakeWeb` answers by (method, exact url). It answers every robots.txt with a 404 (no restrictions)
unless a test says otherwise, and it fails loudly on any other request nobody set up: a check that
asks for something the test did not expect is a bug in the check, not a 404. It records every
request, in order, which is how the tests assert what a check did and, as importantly, did not do.

The answers are built from the captured, trimmed responses in tests/fixtures/link_check/ (see its
README for how they were captured), so a decision is tested against the shape the real endpoint
returned."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from between_jobs.api.link_check import LinkChecker
from between_jobs.api.link_fetch import PoliteFetcher, build_link_check_client

FIXTURES = Path(__file__).parent / "fixtures" / "link_check"


def fixture(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return loaded


def fixture_response(name: str) -> httpx.Response:
    """A fresh response with the captured status and body. The Ashby hosted page's fixture keeps
    only a marker of the page, so that is what its body is."""
    captured = fixture(name)
    if "marker" in captured:
        return httpx.Response(
            captured["status"],
            text=captured["marker"],
            headers={"content-type": captured["content_type"]},
        )
    return httpx.Response(captured["status"], json=captured["body"])


class FakeClock:
    """A monotonic clock that only moves when something sleeps or a test moves it, so pacing and
    cool-downs are tested without waiting for them."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        await asyncio.sleep(0)


Responder = Callable[[httpx.Request], httpx.Response | Any]


class FakeWeb:
    def __init__(self, clock: FakeClock | None = None) -> None:
        self.clock = clock
        self.requests: list[httpx.Request] = []
        self.times: list[float] = []
        self._routes: dict[tuple[str, str], Responder] = {}

    def on(
        self,
        method: str,
        url: str,
        answer: str | Responder | None = None,
        *,
        status: int = 200,
        json_body: Any = None,
        text: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Answers `method url`. `answer` is a fixture name, or a function from the request to a
        response; without it the answer is built from `status`, `json_body`/`text` and `headers`."""
        if isinstance(answer, str):
            name = answer

            def responder(_: httpx.Request) -> httpx.Response:
                return fixture_response(name)

        elif answer is not None:
            responder = answer
        else:

            def responder(_: httpx.Request) -> httpx.Response:
                if json_body is not None:
                    return httpx.Response(status, json=json_body, headers=headers)
                return httpx.Response(status, text=text or "", headers=headers)

        self._routes[(method, url)] = responder

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.clock is not None:
            self.times.append(self.clock())
        responder = self._routes.get((request.method, str(request.url)))
        if responder is None:
            if request.url.path == "/robots.txt":
                return httpx.Response(404, text="")
            raise AssertionError(f"unexpected request: {request.method} {request.url}")
        result = responder(request)
        if asyncio.iscoroutine(result):
            result = await result
        assert isinstance(result, httpx.Response)
        return result

    def urls(self, *, robots: bool = False) -> list[str]:
        """The urls requested, in order, without robots.txt fetches unless asked."""
        return [
            f"{r.method} {r.url}" for r in self.requests if robots or r.url.path != "/robots.txt"
        ]

    def robots_requests(self) -> list[str]:
        return [str(r.url) for r in self.requests if r.url.path == "/robots.txt"]


NOW = datetime(2026, 11, 4, 18, 30, 0, tzinfo=UTC)


def make_fetcher(
    web: FakeWeb, clock: FakeClock | None = None, **options: Any
) -> tuple[PoliteFetcher, FakeClock]:
    clock = clock or FakeClock()
    web.clock = clock
    http = build_link_check_client(httpx.MockTransport(web.handler))
    return PoliteFetcher(http, clock=clock, sleep=clock.sleep, **options), clock


def make_checker(web: FakeWeb, clock: FakeClock | None = None, **options: Any) -> LinkChecker:
    fetcher, clock = make_fetcher(web, clock, **options)
    return LinkChecker(fetcher, clock=clock, now=lambda: NOW)


GH = "https://boards-api.greenhouse.io/v1/boards/stripe/jobs"
LEVER = "https://api.lever.co/v0/postings/leverdemo?mode=json"
LEVER_EU = "https://api.eu.lever.co/v0/postings/leverdemo?mode=json"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/ashby"
ZERO_UUID = "00000000-0000-0000-0000-000000000000"
NVIDIA_BASE = "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite"
NTT_BASE = "https://nttlimited.wd3.myworkdayjobs.com/wday/cxs/nttlimited/NTT_Careers"
