"""The link check's manners (api/link_fetch.py): the closed host list and the request hook that
enforces it, robots.txt (RFC 9309), the one-request-a-second pacer, the ten-minute cool-down, the
bounds on time and size, and the identifying User-Agent. No network: httpx.MockTransport, and a
clock that sleeps without waiting."""

from __future__ import annotations

import asyncio
import gzip
import logging
from collections.abc import AsyncIterator
from itertools import pairwise
from typing import Any

import httpx
import pytest
from link_check_fakes import FakeClock, FakeWeb, make_fetcher
from rendered_logs import rendered

from between_jobs.api import link_fetch
from between_jobs.api.link_fetch import (
    HostRefused,
    PoliteFetcher,
    Undecided,
    build_link_check_client,
    is_allowed_host,
    parse_robots,
    refuse_hosts_outside_allowlist,
)

GH_HOST = "boards-api.greenhouse.io"
LIST_URL = f"https://{GH_HOST}/v1/boards/stripe/jobs"
ROBOTS_URL = f"https://{GH_HOST}/robots.txt"


# -- the closed list of hosts ------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "boards-api.greenhouse.io",
        "api.lever.co",
        "api.eu.lever.co",
        "api.ashbyhq.com",
        "jobs.ashbyhq.com",
        "nvidia.wd5.myworkdayjobs.com",
        "nttlimited.wd3.myworkdayjobs.com",
        "a.wd1.myworkdayjobs.com",
        "a-b-1.wd103.myworkdayjobs.com",
    ],
)
def test_the_allowed_hosts(host: str) -> None:
    assert is_allowed_host(host)


@pytest.mark.parametrize(
    "host",
    [
        "",
        "evil.example",
        "localhost",
        "127.0.0.1",
        "169.254.169.254",
        "::1",
        "boards.greenhouse.io",
        "greenhouse.io",
        "api.greenhouse.io",
        "boards-api.greenhouse.io.evil.example",
        "evilboards-api.greenhouse.io",
        "x.boards-api.greenhouse.io",
        "api.lever.co.evil.example",
        "evilapi.lever.co",
        "jobs.lever.co",
        "api.ashbyhq.com.evil.example",
        "evilmyworkdayjobs.com",
        "myworkdayjobs.com",
        "wd5.myworkdayjobs.com",
        "acme.myworkdayjobs.com",
        "acme.wd.myworkdayjobs.com",
        "acme.wd5.myworkdayjobs.com.evil.example",
        "acme.wd5.myworkdayjobs.com.",
        "-acme.wd5.myworkdayjobs.com",
        "acme.evil.wd5.myworkdayjobs.com",
        "ACME.wd5.myworkdayjobs.com",
        "acme.wd" + chr(0x665) + ".myworkdayjobs.com",
    ],
)
def test_every_other_host_is_refused(host: str) -> None:
    assert not is_allowed_host(host)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/",
        "https://evil.example/v1/boards/stripe/jobs",
        "http://boards-api.greenhouse.io/v1/boards/stripe/jobs",
        "https://boards-api.greenhouse.io:8443/v1/boards/stripe/jobs",
        "https://user:pass@boards-api.greenhouse.io/v1/boards/stripe/jobs",
        "https://127.0.0.1/",
        "https://[::1]/",
        "https://169.254.169.254/latest/meta-data/",
        "https://localhost/",
        "https://boards.greenhouse.io/stripe/jobs/1",
        "https://boards-api.greenhouse.io.evil.example/",
        "https://evilmyworkdayjobs.com/",
    ],
)
async def test_the_request_hook_refuses_a_rogue_host_even_if_the_code_that_built_it_is_wrong(
    url: str,
) -> None:
    """The second lock: a client built the way the app builds it never sends anything to a host
    outside the list, whatever address it is given."""
    reached: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reached.append(request)
        return httpx.Response(200)

    async with build_link_check_client(httpx.MockTransport(handler)) as client:
        with pytest.raises(HostRefused):
            await client.get(url)

    assert reached == []


async def test_the_request_hook_lets_an_allowed_host_through() -> None:
    reached: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reached.append(request)
        return httpx.Response(200)

    async with build_link_check_client(httpx.MockTransport(handler)) as client:
        await client.get(LIST_URL)
        await client.get("https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/S/jobs")
        await client.get(f"https://{GH_HOST}:443/x")

    assert len(reached) == 3


async def test_the_hook_is_what_the_client_is_built_with() -> None:
    client = build_link_check_client()

    assert client.event_hooks["request"] == [refuse_hosts_outside_allowlist]
    assert client.follow_redirects is False
    await client.aclose()


async def test_the_fetcher_never_sends_to_a_rogue_host_and_logs_it_without_the_address(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Through `fetch` the first thing asked of a host is its robots.txt, which the hook refuses,
    so the rogue host reads as one whose rules cannot be had; the request itself, asked of the
    sender directly, is refused as a network failure. Neither reaches the transport."""
    web = FakeWeb()
    fetcher, _ = make_fetcher(web)
    rogue = "https://evil.example/secret-path?token=SECRET-QUERY"

    with caplog.at_level(logging.WARNING):
        with pytest.raises(Undecided) as through_fetch:
            await fetcher.fetch("GET", rogue)
        with pytest.raises(Undecided) as direct:
            await fetcher._send("GET", rogue)

    assert through_fetch.value.reason == "robots_unavailable"
    assert direct.value.reason == "network"
    assert web.requests == []
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 2
    text = rendered(errors)
    assert "SECRET-QUERY" not in text
    assert "secret-path" not in text


# -- who it says it is, and what it does not follow --------------------------------------------


async def test_every_request_says_who_is_asking_robots_txt_included() -> None:
    web = FakeWeb()
    web.on("GET", LIST_URL, status=200, json_body={})
    fetcher, _ = make_fetcher(web)

    await fetcher.fetch("GET", LIST_URL)

    assert [r.url.path for r in web.requests] == ["/robots.txt", "/v1/boards/stripe/jobs"]
    for request in web.requests:
        assert request.headers["user-agent"] == (
            "BetweenJobsLinkCheck/1.0 (+https://between-jobs.tech; support@between-jobs.tech)"
        )


async def test_a_redirect_is_an_answer_and_is_never_followed() -> None:
    web = FakeWeb()
    web.on("GET", LIST_URL, status=301, headers={"location": "https://stripe.com/jobs"})
    fetcher, _ = make_fetcher(web)

    answer = await fetcher.fetch("GET", LIST_URL)

    assert answer.status == 301
    assert web.urls() == [f"GET {LIST_URL}"]


# -- robots.txt: the parser --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("robots", "path", "allowed"),
    [
        # nothing says no
        ("", "/anything", True),
        ("User-agent: *\nDisallow:", "/anything", True),
        ("User-agent: *\nAllow: /", "/anything", True),
        # a plain prefix
        ("User-agent: *\nDisallow: /v1/", "/v1/boards/x/jobs", False),
        ("User-agent: *\nDisallow: /v1/", "/v2/boards", True),
        ("User-agent: *\nDisallow: /", "/", False),
        ("User-agent: *\nDisallow: /", "/v1/boards", False),
        # the longest matching rule wins, in either order in the file
        ("User-agent: *\nDisallow: /v1/\nAllow: /v1/boards/", "/v1/boards/x", True),
        ("User-agent: *\nAllow: /v1/boards/\nDisallow: /v1/", "/v1/boards/x", True),
        ("User-agent: *\nAllow: /v1/\nDisallow: /v1/boards/", "/v1/boards/x", False),
        ("User-agent: *\nAllow: /v1/\nDisallow: /v1/boards/", "/v1/other", True),
        # a tie goes to Allow, whichever comes first
        ("User-agent: *\nDisallow: /v1/\nAllow: /v1/", "/v1/x", True),
        ("User-agent: *\nAllow: /v1/\nDisallow: /v1/", "/v1/x", True),
        # wildcards and the end anchor
        ("User-agent: *\nDisallow: /*/jobs", "/v1/boards/x/jobs", False),
        ("User-agent: *\nDisallow: /*/jobs", "/v1/boards/x/job", True),
        ("User-agent: *\nDisallow: /*.json$", "/data/a.json", False),
        ("User-agent: *\nDisallow: /*.json$", "/data/a.json?x=1", True),
        ("User-agent: *\nDisallow: /*.json$", "/data/a.jsonl", True),
        ("User-agent: *\nDisallow: /v1/$", "/v1/", False),
        ("User-agent: *\nDisallow: /v1/$", "/v1/boards", True),
        ("User-agent: *\nDisallow: /a*b*c", "/a-b-c", False),
        ("User-agent: *\nDisallow: /a*b*c", "/a-c-b", True),
        ("User-agent: *\nDisallow: /*?mode=json", "/v0/postings/x?mode=json", False),
        ("User-agent: *\nDisallow: /*?mode=json", "/v0/postings/x", True),
        ("User-agent: *\nDisallow: /v1*", "/v1", False),
        # the group for this crawler beats the group for everyone
        ("User-agent: *\nDisallow: /\n\nUser-agent: BetweenJobsLinkCheck\nAllow: /", "/x", True),
        ("User-agent: *\nAllow: /\n\nUser-agent: betweenjobslinkcheck\nDisallow: /x", "/x", False),
        ("User-agent: *\nDisallow: /\n\nUser-agent: Googlebot\nAllow: /", "/x", False),
        ("User-agent: Googlebot\nDisallow: /", "/x", True),
        # two groups for this crawler are one group; so are two agent lines over one list
        (
            "User-agent: BetweenJobsLinkCheck\nDisallow: /a\n\n"
            "User-agent: BetweenJobsLinkCheck\nDisallow: /b",
            "/b",
            False,
        ),
        ("User-agent: Googlebot\nUser-agent: BetweenJobsLinkCheck\nDisallow: /a", "/a", False),
        ("User-agent: BetweenJobsLinkCheck/1.0\nDisallow: /a", "/a", False),
        ("User-agent: *\nUser-agent: Bingbot\nDisallow: /a", "/a", False),
        # the format: comments, case, spacing, line endings, a BOM, lines it does not know
        ("﻿User-agent: *\r\nDisallow: /a # not this\r\n", "/a", False),
        ("USER-AGENT: *\nDISALLOW: /a", "/a", False),
        ("User-agent:*\nDisallow:/a", "/a", False),
        ("# User-agent: *\n# Disallow: /\n", "/a", True),
        (
            "User-agent: *\nCrawl-delay: 10\nSitemap: https://x.example/s.xml\nDisallow: /a",
            "/a",
            False,
        ),
        ("Disallow: /a\nUser-agent: *\nDisallow: /b", "/a", True),
        ("User-agent: *\nthis is not a rule\nDisallow: /a", "/a", False),
    ],
)
def test_robots_rules_follow_rfc_9309(robots: str, path: str, allowed: bool) -> None:
    assert parse_robots(robots).allows(path) is allowed


def test_a_robots_file_full_of_stars_cannot_make_matching_slow() -> None:
    hostile = "User-agent: *\nDisallow: /" + "*a" * 5000 + "b"

    assert parse_robots(hostile).allows("/" + "a" * 2000) is True


def test_only_the_first_half_megabyte_of_a_robots_file_is_read() -> None:
    padding = "# " + "x" * 100 + "\n"
    body = "User-agent: *\n" + padding * 6000 + "Disallow: /late\n"

    assert len(body) > link_fetch.ROBOTS_MAX_PARSED_BYTES
    assert parse_robots(body).allows("/late") is True


# -- robots.txt: what the fetcher does with it -------------------------------------------------


async def test_a_disallowed_path_is_not_requested() -> None:
    web = FakeWeb()
    web.on("GET", ROBOTS_URL, status=200, text="User-agent: *\nDisallow: /v1/boards/\n")
    fetcher, _ = make_fetcher(web)

    with pytest.raises(Undecided) as refused:
        await fetcher.fetch("GET", LIST_URL)

    assert refused.value.reason == "robots_disallowed"
    assert web.urls() == []
    assert web.robots_requests() == [ROBOTS_URL]


async def test_the_query_is_part_of_the_path_robots_rules_are_matched_against() -> None:
    web = FakeWeb()
    web.on(
        "GET",
        "https://api.lever.co/robots.txt",
        status=200,
        text="User-agent: *\nDisallow: /*?mode=json\n",
    )
    fetcher, _ = make_fetcher(web)

    with pytest.raises(Undecided) as refused:
        await fetcher.fetch("GET", "https://api.lever.co/v0/postings/x?mode=json")

    assert refused.value.reason == "robots_disallowed"


async def test_a_post_is_held_to_robots_like_any_other_request() -> None:
    host = "nvidia.wd5.myworkdayjobs.com"
    web = FakeWeb()
    web.on(
        "GET", f"https://{host}/robots.txt", status=200, text="User-agent: *\nDisallow: /wday/\n"
    )
    fetcher, _ = make_fetcher(web)

    with pytest.raises(Undecided) as refused:
        await fetcher.fetch("POST", f"https://{host}/wday/cxs/nvidia/S/jobs", json_body={})

    assert refused.value.reason == "robots_disallowed"


@pytest.mark.parametrize("status", [404, 410, 401, 403, 400])
async def test_a_4xx_robots_file_means_no_restrictions(status: int) -> None:
    web = FakeWeb()
    web.on("GET", ROBOTS_URL, status=status, text="User-agent: *\nDisallow: /\n")
    web.on("GET", LIST_URL, status=200, json_body={})
    fetcher, _ = make_fetcher(web)

    answer = await fetcher.fetch("GET", LIST_URL)

    assert answer.status == 200


@pytest.mark.parametrize("status", [500, 502, 503, 301, 302])
async def test_a_robots_file_that_cannot_be_had_means_nothing_is_requested(status: int) -> None:
    web = FakeWeb()
    web.on("GET", ROBOTS_URL, status=status, text="User-agent: *\nAllow: /\n")
    fetcher, _ = make_fetcher(web)

    with pytest.raises(Undecided) as refused:
        await fetcher.fetch("GET", LIST_URL)

    assert refused.value.reason == "robots_unavailable"
    assert web.urls() == []


@pytest.mark.parametrize(
    "error", [httpx.ConnectError("down"), httpx.ReadTimeout("slow"), httpx.ReadError("reset")]
)
async def test_a_robots_file_that_cannot_be_reached_means_nothing_is_requested(
    error: Exception,
) -> None:
    web = FakeWeb()

    def fail(_: httpx.Request) -> httpx.Response:
        raise error

    web.on("GET", ROBOTS_URL, fail)
    fetcher, _ = make_fetcher(web)

    with pytest.raises(Undecided) as refused:
        await fetcher.fetch("GET", LIST_URL)

    assert refused.value.reason == "robots_unavailable"
    assert web.urls() == []


async def test_robots_is_fetched_once_per_host_and_remembered_for_six_hours() -> None:
    web = FakeWeb()
    web.on("GET", LIST_URL, status=200, json_body={})
    other = "https://api.lever.co/v0/postings/x?mode=json"
    web.on("GET", other, status=200, json_body=[])
    fetcher, clock = make_fetcher(web)

    await fetcher.fetch("GET", LIST_URL)
    await fetcher.fetch("GET", LIST_URL)
    await fetcher.fetch("GET", other)

    assert sorted(web.robots_requests()) == [
        "https://api.lever.co/robots.txt",
        ROBOTS_URL,
    ]

    clock.now += link_fetch.ROBOTS_TTL_SECONDS - 10
    await fetcher.fetch("GET", LIST_URL)
    assert len(web.robots_requests()) == 2

    clock.now += 20
    await fetcher.fetch("GET", LIST_URL)
    assert web.robots_requests().count(ROBOTS_URL) == 2


async def test_an_unavailable_robots_file_is_asked_for_again_after_five_minutes_not_six_hours() -> (
    None
):
    web = FakeWeb()
    answers = iter([503, 200])

    def robots(_: httpx.Request) -> httpx.Response:
        return httpx.Response(next(answers), text="User-agent: *\nAllow: /\n")

    web.on("GET", ROBOTS_URL, robots)
    web.on("GET", LIST_URL, status=200, json_body={})
    fetcher, clock = make_fetcher(web)

    with pytest.raises(Undecided):
        await fetcher.fetch("GET", LIST_URL)
    with pytest.raises(Undecided):
        await fetcher.fetch("GET", LIST_URL)
    assert len(web.robots_requests()) == 1

    clock.now += link_fetch.ROBOTS_UNAVAILABLE_TTL_SECONDS + 1
    answer = await fetcher.fetch("GET", LIST_URL)

    assert answer.status == 200
    assert len(web.robots_requests()) == 2


async def test_checks_racing_for_one_hosts_robots_file_ask_once() -> None:
    web = FakeWeb()
    web.on("GET", LIST_URL, status=200, json_body={})
    fetcher, _ = make_fetcher(web)

    await asyncio.gather(*(fetcher.fetch("GET", LIST_URL) for _ in range(4)))

    assert web.robots_requests() == [ROBOTS_URL]


# -- one request a second to a host ------------------------------------------------------------


async def test_requests_to_one_host_are_a_second_apart_robots_included() -> None:
    web = FakeWeb()
    web.on("GET", LIST_URL, status=200, json_body={})
    web.on("GET", f"{LIST_URL}/1", status=404, text="")
    fetcher, clock = make_fetcher(web)
    start = clock.now

    await fetcher.fetch("GET", LIST_URL)
    await fetcher.fetch("GET", f"{LIST_URL}/1")

    assert [t - start for t in web.times] == [0.0, 1.0, 2.0]


async def test_a_wait_on_one_host_never_delays_another() -> None:
    web = FakeWeb()
    web.on("GET", LIST_URL, status=200, json_body={})
    web.on("GET", f"{LIST_URL}/1", status=404, text="")
    web.on("GET", "https://api.ashbyhq.com/posting-api/job-board/x", status=200, json_body={})
    fetcher, clock = make_fetcher(web)
    start = clock.now

    await fetcher.fetch("GET", LIST_URL)
    await fetcher.fetch("GET", LIST_URL + "/1")
    before = clock.now
    await fetcher.fetch("GET", "https://api.ashbyhq.com/posting-api/job-board/x")

    # the Ashby host had no recent requests: its robots.txt and its request wait only for each other
    assert clock.now - before == 1.0
    assert before - start == 2.0


async def test_checks_racing_for_one_host_queue_up_a_second_apart() -> None:
    web = FakeWeb()
    for n in range(4):
        web.on("GET", f"{LIST_URL}/{n}", status=404, text="")
    fetcher, clock = make_fetcher(web)
    start = clock.now

    await asyncio.gather(*(fetcher.fetch("GET", f"{LIST_URL}/{n}") for n in range(4)))

    gaps = sorted(t - start for t in web.times)
    assert all(later - earlier >= 1.0 for earlier, later in pairwise(gaps))


# -- a host that asks us to stop ---------------------------------------------------------------


async def test_a_429_stops_every_request_to_that_host_for_ten_minutes() -> None:
    web = FakeWeb()
    web.on("GET", LIST_URL, status=429, text="slow down")
    web.on("GET", f"{LIST_URL}/1", status=404, text="")
    other = "https://api.ashbyhq.com/posting-api/job-board/x"
    web.on("GET", other, status=200, json_body={})
    fetcher, clock = make_fetcher(web)

    with pytest.raises(Undecided) as first:
        await fetcher.fetch("GET", LIST_URL)
    seen = len(web.requests)
    with pytest.raises(Undecided) as second:
        await fetcher.fetch("GET", f"{LIST_URL}/1")

    assert (first.value.reason, second.value.reason) == ("rate_limited", "rate_limited")
    assert len(web.requests) == seen
    assert (await fetcher.fetch("GET", other)).status == 200  # another host is unaffected

    clock.now += link_fetch.HOST_COOLDOWN_SECONDS - 5
    with pytest.raises(Undecided):
        await fetcher.fetch("GET", f"{LIST_URL}/1")
    assert len(web.requests) == seen + 2  # only the other host's two requests

    clock.now += 10
    assert (await fetcher.fetch("GET", f"{LIST_URL}/1")).status == 404


@pytest.mark.parametrize("status", [200, 404, 500, 503])
async def test_any_retry_after_stops_the_host_whatever_the_status(status: int) -> None:
    web = FakeWeb()
    web.on("GET", LIST_URL, status=status, text="", headers={"Retry-After": "120"})
    fetcher, _ = make_fetcher(web)

    with pytest.raises(Undecided) as refused:
        await fetcher.fetch("GET", LIST_URL)
    seen = len(web.requests)
    with pytest.raises(Undecided):
        await fetcher.fetch("GET", LIST_URL)

    assert refused.value.reason == "rate_limited"
    assert len(web.requests) == seen


async def test_a_429_on_robots_txt_is_a_cool_down_not_an_unavailable_robots_file() -> None:
    web = FakeWeb()
    web.on("GET", ROBOTS_URL, status=429, text="")
    fetcher, _ = make_fetcher(web)

    with pytest.raises(Undecided) as refused:
        await fetcher.fetch("GET", LIST_URL)

    assert refused.value.reason == "rate_limited"


async def test_a_429_is_never_retried() -> None:
    web = FakeWeb()
    web.on("GET", LIST_URL, status=429, text="")
    fetcher, _ = make_fetcher(web)

    with pytest.raises(Undecided):
        await fetcher.fetch("GET", LIST_URL)

    assert web.urls() == [f"GET {LIST_URL}"]


class _SlowClock(FakeClock):
    """A clock whose waits take a little real time, in proportion to the virtual wait, so a request
    queued behind another really does start after the earlier one has been answered."""

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds / 200)
        self.now = max(self.now, self.now + seconds)


async def test_a_429_stops_the_requests_already_waiting_for_their_turn_at_that_host() -> None:
    web = FakeWeb()
    for n in range(3):
        web.on("GET", f"{LIST_URL}/{n}", status=429 if n == 0 else 404, text="slow down")
    fetcher, _ = make_fetcher(web, _SlowClock())

    outcomes = await asyncio.gather(
        *(fetcher.fetch("GET", f"{LIST_URL}/{n}") for n in range(3)), return_exceptions=True
    )

    assert [o.reason for o in outcomes if isinstance(o, Undecided)] == ["rate_limited"] * 3
    assert web.urls() == [f"GET {LIST_URL}/0"]


async def test_a_429_on_robots_txt_stops_the_checks_waiting_for_that_robots_file() -> None:
    async def slow_429(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.01)  # in flight long enough for the others to queue behind it
        return httpx.Response(429, headers={"Retry-After": "120"})

    web = FakeWeb()
    web.on("GET", ROBOTS_URL, slow_429)
    fetcher, _ = make_fetcher(web, _SlowClock())

    outcomes = await asyncio.gather(
        *(fetcher.fetch("GET", f"{LIST_URL}/{n}") for n in range(3)), return_exceptions=True
    )

    assert [o.reason for o in outcomes if isinstance(o, Undecided)] == ["rate_limited"] * 3
    assert web.robots_requests() == [ROBOTS_URL]
    assert web.urls() == []


# -- bounds ------------------------------------------------------------------------------------


def test_the_body_cap_is_sixteen_mebibytes_and_the_request_timeout_fifteen_seconds() -> None:
    assert link_fetch.MAX_BODY_BYTES == 16 * 1024 * 1024
    assert link_fetch.REQUEST_TIMEOUT_SECONDS == 15.0
    assert link_fetch.MIN_SECONDS_BETWEEN_REQUESTS == 1.0
    assert link_fetch.HOST_COOLDOWN_SECONDS == 600.0
    assert link_fetch.ROBOTS_TTL_SECONDS == 6 * 3600.0


async def test_a_body_past_the_cap_is_abandoned() -> None:
    web = FakeWeb()
    web.on("GET", LIST_URL, status=200, text="x" * 200)
    fetcher, _ = make_fetcher(web, max_body_bytes=100)

    with pytest.raises(Undecided) as refused:
        await fetcher.fetch("GET", LIST_URL)

    assert refused.value.reason == "network"


async def test_a_body_that_declares_it_is_past_the_cap_is_never_read() -> None:
    read = False

    async def body() -> AsyncIterator[bytes]:
        nonlocal read
        read = True
        yield b"x"

    web = FakeWeb()
    web.on(
        "GET",
        LIST_URL,
        lambda _: httpx.Response(200, headers={"content-length": "5000"}, content=body()),
    )
    fetcher, _ = make_fetcher(web, max_body_bytes=100)

    with pytest.raises(Undecided) as refused:
        await fetcher.fetch("GET", LIST_URL)

    assert refused.value.reason == "network"
    assert read is False


async def test_the_cap_applies_after_decompression() -> None:
    bomb = gzip.compress(b"0" * 100_000)
    assert len(bomb) < 1000
    web = FakeWeb()
    web.on(
        "GET",
        LIST_URL,
        lambda _: httpx.Response(200, headers={"content-encoding": "gzip"}, content=bomb),
    )
    fetcher, _ = make_fetcher(web, max_body_bytes=10_000)

    with pytest.raises(Undecided) as refused:
        await fetcher.fetch("GET", LIST_URL)

    assert refused.value.reason == "network"


async def test_a_body_just_inside_the_cap_is_read_whole() -> None:
    web = FakeWeb()
    web.on("GET", LIST_URL, status=200, text="x" * 100)
    fetcher, _ = make_fetcher(web, max_body_bytes=100)

    answer = await fetcher.fetch("GET", LIST_URL)

    assert answer.body == b"x" * 100


async def test_a_body_nobody_needs_is_not_read() -> None:
    read = False

    async def body() -> AsyncIterator[bytes]:
        nonlocal read
        read = True
        yield b"a whole page"

    web = FakeWeb()
    web.on("GET", LIST_URL, lambda _: httpx.Response(404, content=body()))
    fetcher, _ = make_fetcher(web)

    answer = await fetcher.fetch("GET", LIST_URL, read_body=False)

    assert (answer.status, answer.body) == (404, b"")
    assert read is False


async def test_a_request_is_timed_in_total_not_per_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server that sends a byte now and then keeps every individual read inside httpx's timeout
    forever; the request as a whole is what is bounded."""
    monkeypatch.setattr(link_fetch, "REQUEST_TIMEOUT_SECONDS", 0.05)

    async def drip() -> AsyncIterator[bytes]:
        while True:
            await asyncio.sleep(0.01)
            yield b"x"

    web = FakeWeb()
    web.on("GET", LIST_URL, lambda _: httpx.Response(200, content=drip()))
    fetcher, _ = make_fetcher(web)

    with pytest.raises(Undecided) as refused:
        # the guard turns a missing total timeout into a failure here instead of a hang
        async with asyncio.timeout(5):
            await fetcher.fetch("GET", LIST_URL)

    assert refused.value.reason == "timeout"


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ConnectTimeout("slow"), "timeout"),
        (httpx.PoolTimeout("busy"), "timeout"),
        (httpx.ConnectError("down"), "network"),
        (httpx.RemoteProtocolError("garbled"), "network"),
        (httpx.TooManyRedirects("loop"), "network"),
    ],
)
async def test_a_failure_to_get_an_answer_is_a_timeout_or_a_network_failure(
    error: Exception, reason: str
) -> None:
    web = FakeWeb()

    def fail(_: httpx.Request) -> httpx.Response:
        raise error

    web.on("GET", LIST_URL, fail)
    fetcher, _ = make_fetcher(web)

    with pytest.raises(Undecided) as refused:
        await fetcher.fetch("GET", LIST_URL)

    assert refused.value.reason == reason


async def test_the_fetcher_closes_its_client() -> None:
    http = build_link_check_client(httpx.MockTransport(lambda _: httpx.Response(200)))
    fetcher: Any = PoliteFetcher(http)

    await fetcher.aclose()

    assert http.is_closed


def test_the_pacers_memory_of_hosts_stays_bounded() -> None:
    clock = FakeClock()
    fetcher = PoliteFetcher(
        build_link_check_client(httpx.MockTransport(lambda _: httpx.Response(200))),
        clock=clock,
        sleep=clock.sleep,
    )

    async def many() -> None:
        for n in range(600):
            await fetcher._pace(f"t{n}.wd1.myworkdayjobs.com")
            clock.now += 5

    asyncio.run(many())

    assert len(fetcher._next_slot) < 300


async def test_the_memory_of_robots_files_and_cool_downs_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(link_fetch, "_REMEMBERED_HOSTS_MAX", 3)
    web = FakeWeb()
    hosts = [f"t{n}.wd1.myworkdayjobs.com" for n in range(6)]
    for host in hosts:
        web.on("GET", f"https://{host}/x", status=200, text="")
    fetcher, _ = make_fetcher(web)

    for host in hosts:
        await fetcher.fetch("GET", f"https://{host}/x")
    for host in hosts:
        fetcher._cool_down(host)

    assert list(fetcher._robots) == hosts[-3:]
    assert list(fetcher._cooldowns) == hosts[-3:]
