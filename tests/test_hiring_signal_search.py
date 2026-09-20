"""Tests for Hiring Signals P3's one piece of network code
(`hiring_signal_search`): the four provider adapters, the freshness mapping,
the caps, and the hard line that nothing here ever requests linkedin.com.

Provider bodies: You.com's empty answer and Firecrawl's result shapes are the
real ones (see `tests/golden/hiring_signals/provider_responses/README.md`);
Brave's and Serper's are synthetic, written from their public documentation --
no Brave or Serper key existed to capture them with, so those two adapters are
covered by mocked-transport tests only.
"""

from __future__ import annotations

import ast
import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from hiring_signal_fakes import RecordingTransport, load_fixture

from between_jobs.api import hiring_signal_search
from between_jobs.api.errors import ApiError
from between_jobs.api.hiring_signal_search import (
    CALL_TIMEOUT_SECONDS,
    MAX_BODY_BYTES,
    MAX_PROVIDER_CALLS_PER_REQUEST,
    MAX_RESULTS_PER_CALL,
    PROVIDER_HOSTS,
    PROVIDER_ORDER,
    WINDOW_DAYS,
    HiringProvider,
    provider_freshness_request,
    refuse_non_provider_hosts,
    search_provider,
)
from between_jobs.api.hiring_signals import Freshness, RawSearchHit, parse_hit
from between_jobs.api.search_providers import fetch_you_com, you_com_hit_fields

KEY = "test-key-123456"
QUERY = 'site:linkedin.com/posts ("we\'re hiring") "acme" ("software engineer" OR "engineer")'
TODAY = date(2026, 9, 19)

_PROVIDER_HOSTS = {
    "you_com": "ydc-index.io",
    "brave": "api.search.brave.com",
    "serper": "google.serper.dev",
    "firecrawl": "api.firecrawl.dev",
}


def _client(
    handler: Any,
) -> tuple[httpx.AsyncClient, RecordingTransport]:
    transport = RecordingTransport(handler)
    return httpx.AsyncClient(transport=transport), transport


def _json_handler(body: Any, status: int = 200) -> Any:
    return lambda request: httpx.Response(status, json=body, request=request)


def _bodies_for(provider: str, entries: list[Any]) -> Any:
    """A body in `provider`'s own shape holding `entries` (already in that
    provider's field names)."""
    bodies: dict[str, Any] = {
        "you_com": {"results": {"web": entries}},
        "brave": {"web": {"results": entries}},
        "serper": {"organic": entries},
        "firecrawl": {"data": {"web": entries}},
    }
    return bodies[provider]


def _entry(provider: str, url: str, title: str, snippet: str) -> dict[str, Any]:
    entries: dict[str, dict[str, Any]] = {
        "you_com": {"url": url, "title": title, "snippets": [snippet]},
        "brave": {"url": url, "title": title, "description": snippet},
        "serper": {"link": url, "title": title, "snippet": snippet},
        "firecrawl": {"url": url, "title": title, "description": snippet},
    }
    return entries[provider]


ALL_PROVIDERS: list[HiringProvider] = ["you_com", "brave", "serper", "firecrawl"]


# ── requests: endpoint, auth, body ───────────────────────────────────────


async def test_you_com_request_shape() -> None:
    client, transport = _client(_json_handler(load_fixture("you_com_empty.json")))
    hits = await search_provider(
        client, "you_com", api_key=KEY, query=QUERY, freshness_param="week"
    )

    assert hits == []
    (request,) = transport.requests
    assert request.method == "POST"
    assert str(request.url) == "https://ydc-index.io/v1/search"
    assert request.headers["X-API-Key"] == KEY
    assert transport.json_bodies()[0] == {"query": QUERY, "count": 20, "freshness": "week"}


async def test_firecrawl_request_shape_uses_the_search_endpoint_only() -> None:
    client, transport = _client(_json_handler(load_fixture("firecrawl_empty.json")))
    await search_provider(client, "firecrawl", api_key=KEY, query=QUERY, freshness_param="qdr:d3")

    (request,) = transport.requests
    assert request.method == "POST"
    assert str(request.url) == "https://api.firecrawl.dev/v2/search"
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert transport.json_bodies()[0] == {
        "query": QUERY,
        "limit": 20,
        "sources": [{"type": "web"}],
        "tbs": "qdr:d3",
    }


async def test_brave_request_shape() -> None:
    client, transport = _client(_json_handler({"web": {"results": []}}))
    await search_provider(client, "brave", api_key=KEY, query=QUERY, freshness_param="pw")

    (request,) = transport.requests
    assert request.method == "GET"
    assert request.url.host == "api.search.brave.com"
    assert request.url.path == "/res/v1/web/search"
    assert request.headers["X-Subscription-Token"] == KEY
    assert dict(request.url.params) == {
        "q": QUERY,
        "count": "20",
        "freshness": "pw",
        "country": "us",
    }


async def test_serper_request_shape() -> None:
    client, transport = _client(_json_handler({"organic": []}))
    await search_provider(client, "serper", api_key=KEY, query=QUERY, freshness_param="qdr:w")

    (request,) = transport.requests
    assert request.method == "POST"
    assert str(request.url) == "https://google.serper.dev/search"
    assert request.headers["X-API-KEY"] == KEY
    assert transport.json_bodies()[0] == {"q": QUERY, "num": 20, "gl": "us", "tbs": "qdr:w"}


async def test_every_call_carries_an_explicit_timeout() -> None:
    for provider in ALL_PROVIDERS:
        client, transport = _client(_json_handler(_bodies_for(provider, [])))
        await search_provider(client, provider, api_key=KEY, query=QUERY, freshness_param="x")
        timeout = transport.requests[0].extensions["timeout"]
        assert set(timeout.values()) == {CALL_TIMEOUT_SECONDS}, provider
    assert CALL_TIMEOUT_SECONDS <= 10.0


# ── responses: real shapes ───────────────────────────────────────────────


async def test_you_com_answers_with_an_empty_results_object_when_linkedin_is_not_in_its_index() -> (
    None
):
    """The real You.com answer to every LinkedIn-directed query tried: a 200
    whose `results` is `{}` -- no `web` key. It is an empty answer, not a
    malformed one."""
    client, _ = _client(_json_handler(load_fixture("you_com_empty.json")))
    assert (
        await search_provider(client, "you_com", api_key=KEY, query=QUERY, freshness_param="week")
        == []
    )


async def test_you_com_maps_snippets_into_one_snippet_and_keeps_title_and_url() -> None:
    client, _ = _client(_json_handler(load_fixture("you_com_unscoped_control.json")))
    hits = await search_provider(client, "you_com", api_key=KEY, query="q", freshness_param="day")

    assert [h.url for h in hits] == [
        "https://jobs.example.com/roles/software-engineer-payments",
        "https://blog.example.org/2026/09/hiring-software-engineers",
    ]
    assert hits[0].title == "Software Engineer, Payments - Example Careers"
    assert hits[0].snippet == (
        "Build and run payments services used by millions of businesses. "
        "Work with a small team on reliability and scale."
    )


async def test_firecrawl_maps_the_composed_company_posts_fixture_in_order() -> None:
    client, _ = _client(_json_handler(load_fixture("firecrawl_company_posts.json")))
    hits = await search_provider(
        client, "firecrawl", api_key=KEY, query=QUERY, freshness_param="qdr:w"
    )

    body = load_fixture("firecrawl_company_posts.json")
    assert len(hits) == len(body["data"]["web"]) == 13
    assert [h.url for h in hits] == [e["url"] for e in body["data"]["web"]]
    assert hits[0].title == "Stripe Careers | Staff Software Engineer, Payments | Jordan Testwell"
    assert hits[0].snippet.startswith("2 days ago · Our payments team is hiring a Staff")
    assert all(isinstance(h, RawSearchHit) for h in hits)
    # these are exactly the hits the parser is built to read
    parsed = [parse_hit(h) for h in hits]
    assert (
        sum(not hasattr(p, "reason") for p in parsed) == 11
    )  # 13 hits, a jobs page and a ugcPost declined


async def test_firecrawl_empty_answer_is_an_empty_list() -> None:
    client, _ = _client(_json_handler(load_fixture("firecrawl_empty.json")))
    assert (
        await search_provider(
            client, "firecrawl", api_key=KEY, query=QUERY, freshness_param="qdr:w"
        )
        == []
    )


@pytest.mark.parametrize("provider", ["brave", "serper"])
async def test_documented_shapes_for_the_providers_without_a_real_capture(
    provider: HiringProvider,
) -> None:
    entries = [_entry(provider, "https://www.linkedin.com/posts/x_y-activity-1-a", "T", "S")]
    client, _ = _client(_json_handler(_bodies_for(provider, entries)))
    hits = await search_provider(client, provider, api_key=KEY, query=QUERY, freshness_param="x")
    assert hits == [RawSearchHit("https://www.linkedin.com/posts/x_y-activity-1-a", "T", "S")]


# ── caps and clipping ────────────────────────────────────────────────────


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_results_are_capped_even_when_a_provider_returns_more(
    provider: HiringProvider,
) -> None:
    entries = [_entry(provider, f"https://example.com/{i}", "t", "s") for i in range(60)]
    client, _ = _client(_json_handler(_bodies_for(provider, entries)))

    default = await search_provider(client, provider, api_key=KEY, query=QUERY, freshness_param="x")
    small = await search_provider(
        client, provider, api_key=KEY, query=QUERY, freshness_param="x", max_results=5
    )

    assert len(default) == MAX_RESULTS_PER_CALL == 20
    assert len(small) == 5


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_the_result_cap_is_also_what_is_asked_of_the_provider(
    provider: HiringProvider,
) -> None:
    client, transport = _client(_json_handler(_bodies_for(provider, [])))
    await search_provider(
        client, provider, api_key=KEY, query=QUERY, freshness_param="x", max_results=7
    )
    (request,) = transport.requests
    asked = (
        request.url.params["count"]
        if provider == "brave"
        else json.loads(request.content)[
            {"you_com": "count", "serper": "num", "firecrawl": "limit"}[provider]
        ]
    )
    assert int(asked) == 7


async def test_oversized_fields_are_clipped_past_the_parsers_own_limits() -> None:
    entry = {"url": "https://e.com/" + "u" * 5000, "title": "t" * 2000, "description": "s" * 5000}
    client, _ = _client(_json_handler(_bodies_for("firecrawl", [entry])))
    (hit,) = await search_provider(
        client, "firecrawl", api_key=KEY, query=QUERY, freshness_param="qdr:w"
    )
    # one character past what hiring_signals treats as too long, so it is still
    # declined there, but never kept at full length
    assert len(hit.url) == 2049
    assert len(hit.title) == 513
    assert len(hit.snippet) == 600


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_malformed_entries_are_skipped_or_blanked_not_fatal(
    provider: HiringProvider,
) -> None:
    good = _entry(provider, "https://example.com/ok", "Title", "Snippet")
    entries: list[Any] = [
        "not an object",
        None,
        7,
        [],
        {},  # no url
        _entry(provider, "", "empty url", "s"),
        {**good, "title": 5},  # non-string title -> blank
        good,
    ]
    client, _ = _client(_json_handler(_bodies_for(provider, entries)))
    hits = await search_provider(client, provider, api_key=KEY, query=QUERY, freshness_param="x")

    assert [h.url for h in hits] == ["https://example.com/ok", "https://example.com/ok"]
    assert hits[0].title == ""
    assert hits[1].title == "Title"


# ── failures ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503])
async def test_http_errors_become_a_retryable_provider_unavailable(
    provider: HiringProvider, status: int
) -> None:
    client, _ = _client(_json_handler({"error": "no"}, status))
    with pytest.raises(ApiError) as caught:
        await search_provider(client, provider, api_key=KEY, query=QUERY, freshness_param="x")
    assert caught.value.code == "PROVIDER_UNAVAILABLE"
    assert caught.value.retryable is True


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
@pytest.mark.parametrize(
    "error",
    [httpx.ReadTimeout("slow"), httpx.ConnectTimeout("slow"), httpx.ConnectError("down")],
)
async def test_transport_failures_and_timeouts_become_provider_unavailable(
    provider: HiringProvider, error: httpx.HTTPError
) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise error

    client, _ = _client(boom)
    with pytest.raises(ApiError) as caught:
        await search_provider(client, provider, api_key=KEY, query=QUERY, freshness_param="x")
    assert caught.value.code == "PROVIDER_UNAVAILABLE"


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"<html>not json</html>"),
        httpx.Response(200, content=b""),
        httpx.Response(200, json=[]),
        httpx.Response(200, json="text"),
        httpx.Response(200, json=None),
        httpx.Response(200, json=3),
    ],
)
async def test_an_unreadable_or_non_object_body_is_a_provider_failure_not_an_empty_answer(
    provider: HiringProvider, response: httpx.Response
) -> None:
    client, _ = _client(lambda request: response)
    with pytest.raises(ApiError) as caught:
        await search_provider(client, provider, api_key=KEY, query=QUERY, freshness_param="x")
    assert caught.value.code == "PROVIDER_UNAVAILABLE"
    assert caught.value.retryable is True


@pytest.mark.parametrize(
    ("provider", "body"),
    [
        ("you_com", {"results": {"web": "nope"}}),
        ("you_com", {"results": {"web": {"a": 1}}}),
        ("brave", {"web": {"results": "nope"}}),
        ("serper", {"organic": {"a": 1}}),
        ("firecrawl", {"data": {"web": "nope"}}),
    ],
)
async def test_a_result_container_that_is_not_a_list_is_a_provider_failure(
    provider: HiringProvider, body: Any
) -> None:
    client, _ = _client(_json_handler(body))
    with pytest.raises(ApiError) as caught:
        await search_provider(client, provider, api_key=KEY, query=QUERY, freshness_param="x")
    assert caught.value.code == "PROVIDER_UNAVAILABLE"


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
@pytest.mark.parametrize("body", [{}, {"unrelated": 1}])
async def test_a_missing_result_container_reads_as_no_results(
    provider: HiringProvider, body: Any
) -> None:
    """The real You.com empty answer has no `web` key at all, so an absent
    container is "nothing found" for every provider (a container of the wrong
    TYPE is the failure, above)."""
    client, _ = _client(_json_handler(body))
    assert (
        await search_provider(client, provider, api_key=KEY, query=QUERY, freshness_param="x") == []
    )


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_an_error_never_carries_the_key_the_query_or_anything_the_provider_sent(
    provider: HiringProvider,
) -> None:
    def leaky(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500, json={"detail": f"bad key {KEY} for query {QUERY}"}, request=request
        )

    client, _ = _client(leaky)
    with pytest.raises(ApiError) as caught:
        await search_provider(client, provider, api_key=KEY, query=QUERY, freshness_param="x")
    rendered = json.dumps(caught.value.to_body())
    assert KEY not in rendered
    assert "acme" not in rendered.lower()
    assert "site:" not in rendered


# ── freshness mapping ────────────────────────────────────────────────────

_EXPECTED_PARAMS: dict[tuple[str, Freshness], tuple[str, int]] = {
    ("you_com", Freshness.DAY): ("day", 1),
    ("you_com", Freshness.THREE_DAYS): ("2026-09-16to2026-09-19", 3),
    ("you_com", Freshness.WEEK): ("week", 7),
    ("brave", Freshness.DAY): ("pd", 1),
    ("brave", Freshness.THREE_DAYS): ("pw", 7),
    ("brave", Freshness.WEEK): ("pw", 7),
    ("serper", Freshness.DAY): ("qdr:d", 1),
    ("serper", Freshness.THREE_DAYS): ("qdr:w", 7),
    ("serper", Freshness.WEEK): ("qdr:w", 7),
    ("firecrawl", Freshness.DAY): ("qdr:d", 1),
    ("firecrawl", Freshness.THREE_DAYS): ("qdr:d3", 3),
    ("firecrawl", Freshness.WEEK): ("qdr:w", 7),
}


@pytest.mark.parametrize(("provider", "freshness"), sorted(_EXPECTED_PARAMS, key=str))
def test_the_freshness_mapping_table(provider: HiringProvider, freshness: Freshness) -> None:
    request = provider_freshness_request(provider, freshness, today=TODAY)
    assert (request.param, request.window_days) == _EXPECTED_PARAMS[(provider, freshness)]


def test_the_table_covers_every_provider_and_window() -> None:
    assert {(p, f) for p in PROVIDER_ORDER for f in Freshness} == set(_EXPECTED_PARAMS)


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
@pytest.mark.parametrize("freshness", list(Freshness))
def test_a_provider_window_is_never_narrower_than_the_window_asked_for(
    provider: HiringProvider, freshness: Freshness
) -> None:
    """The caller trims to the exact window from the decoded post time, so a
    provider may be asked for a WIDER window but must never be asked for a
    narrower one (that would hide posts the user asked to see)."""
    request = provider_freshness_request(provider, freshness, today=TODAY)
    assert request.window_days >= WINDOW_DAYS[freshness]


def test_the_date_range_moves_with_today_and_windows_sharing_a_param_share_it() -> None:
    later = provider_freshness_request("you_com", Freshness.THREE_DAYS, today=date(2026, 10, 2))
    assert later.param == "2026-09-29to2026-10-02"
    # 3 days and a week are the same provider call on Brave and Serper
    for provider in ("brave", "serper"):
        three = provider_freshness_request(provider, Freshness.THREE_DAYS, today=TODAY)
        week = provider_freshness_request(provider, Freshness.WEEK, today=TODAY)
        assert three.param == week.param


# ── ceilings ─────────────────────────────────────────────────────────────


def test_the_ceilings_are_pinned() -> None:
    assert MAX_RESULTS_PER_CALL == 20
    assert MAX_PROVIDER_CALLS_PER_REQUEST == 4 == len(PROVIDER_ORDER)
    assert len(set(PROVIDER_ORDER)) == len(PROVIDER_ORDER)


def test_the_provider_order_encodes_why_you_com_and_serper_come_last() -> None:
    """You.com is behind the two that can see LinkedIn because its index
    returned no LinkedIn results on any query tried. Serper is LAST because
    the design calls it "fallback only, never primary" (it proxies Google
    results, a terms-of-service dependency the others do not have) -- even
    behind You.com's known-empty answer, whose only cost is one cheap call."""
    assert PROVIDER_ORDER == ("brave", "firecrawl", "you_com", "serper")
    assert PROVIDER_ORDER[-1] == "serper"
    assert PROVIDER_ORDER.index("firecrawl") < PROVIDER_ORDER.index("you_com")


# ── the hard line: only provider hosts, never linkedin.com ───────────────


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_a_search_only_ever_contacts_its_own_provider_host(provider: HiringProvider) -> None:
    """Results full of linkedin.com links (post urls, jobs pages, an embed
    address, a company page) are parsed as strings and never requested."""
    entries = [
        _entry(
            provider, "https://www.linkedin.com/posts/a_b-activity-7506381452083381426-x", "t", "s"
        ),
        _entry(provider, "https://www.linkedin.com/jobs/view/123", "t", "s"),
        _entry(provider, "https://www.linkedin.com/company/acme", "t", "s"),
        _entry(
            provider,
            "https://www.linkedin.com/embed/feed/update/urn:li:activity:7506381452083381426",
            "t",
            "s",
        ),
        _entry(provider, "https://lnkd.in/abc", "t", "s"),
    ]
    client, transport = _client(_json_handler(_bodies_for(provider, entries)))

    hits = await search_provider(client, provider, api_key=KEY, query=QUERY, freshness_param="x")

    assert len(hits) == 5
    assert transport.hosts == [_PROVIDER_HOSTS[provider]]
    assert not any(host.endswith("linkedin.com") for host in transport.hosts)


def test_the_provider_hosts_are_the_four_documented_ones_and_none_is_linkedin() -> None:
    assert set(_PROVIDER_HOSTS.values()) == {
        "ydc-index.io",
        "api.search.brave.com",
        "google.serper.dev",
        "api.firecrawl.dev",
    }


def _code_string_constants(path: str) -> list[str]:
    """Every string constant in a module that is CODE -- not a docstring or a
    bare string statement (which is where prose lives)."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    prose = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in prose
    ]


def test_the_network_module_holds_no_url_literals_and_never_names_linkedin() -> None:
    """Endpoints live in `search_providers`; this module is handed a client
    and calls its helpers. It must not grow a URL (least of all a LinkedIn
    one) of its own."""
    strings = _code_string_constants(hiring_signal_search.__file__)
    assert strings  # the scan is looking at real code
    assert not [s for s in strings if "linkedin" in s.lower()]
    assert not [s for s in strings if s.startswith(("http://", "https://"))]


# ── the extraction left Discover's adapters alone ────────────────────────


def test_you_com_hit_fields_reads_nothing_from_an_entry_without_a_url() -> None:
    assert you_com_hit_fields({"url": "", "title": "t", "snippets": [1, 2]}) == ("", "", "")
    assert you_com_hit_fields({"snippets": None}) == ("", "", "")
    assert you_com_hit_fields(
        {"url": "https://a.example/x", "title": "t", "snippets": ["a", "b"]}
    ) == (
        "https://a.example/x",
        "t",
        "a b",
    )


async def test_discover_still_skips_a_url_less_you_com_entry_whatever_its_snippets_hold() -> None:
    """`fetch_you_com` (the live job-search path) used to skip an entry with no
    url BEFORE it read the entry's snippets. Moving the field reading into a
    shared helper briefly made it read them first, so an entry with no url and a
    non-string in `snippets` raised `TypeError` and failed the whole search.
    Pinned here (`tests/test_search_providers.py` is not edited by this
    feature): the entry is skipped, and the valid one beside it still comes
    through."""
    body = {
        "results": {
            "web": [
                {"url": "", "title": "no url", "snippets": [1, 2]},
                {"title": "no url key", "snippets": None},
                {"url": "https://jobs.example.com/a", "title": "A", "snippets": ["one", "two"]},
            ]
        }
    }
    client, _ = _client(_json_handler(body))

    results = await fetch_you_com(client, api_key=KEY, query="python developer")

    assert results
    assert {r.apply_url for r in results} == {"https://jobs.example.com/a"}
    assert {r.snippet for r in results} == {"one two"}


# ── BC-3: the timeout is a TOTAL deadline, not a per-chunk one ───────────


class _Drip(httpx.AsyncByteStream):
    """A response body that arrives one byte at a time. httpx's own timeout
    restarts on every chunk, so a provider (or a middlebox) doing this never
    trips it."""

    def __init__(self, body: bytes, delay: float) -> None:
        self._body = body
        self._delay = delay

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for i in range(len(self._body)):
            await asyncio.sleep(self._delay)
            yield self._body[i : i + 1]


class _DripTransport(httpx.AsyncBaseTransport):
    def __init__(self, body: bytes, delay: float) -> None:
        self._body = body
        self._delay = delay

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_Drip(self._body, self._delay), request=request)


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_a_provider_that_drips_its_answer_is_cut_off_at_the_total_deadline(
    provider: HiringProvider,
) -> None:
    body = json.dumps(_bodies_for(provider, [])).encode()
    assert len(body) * 0.1 > 1.0  # the whole answer would take over a second
    client = httpx.AsyncClient(transport=_DripTransport(body, 0.1))

    started = time.monotonic()
    with pytest.raises(ApiError) as caught:
        await search_provider(
            client, provider, api_key=KEY, query=QUERY, freshness_param="x", timeout=0.3
        )
    elapsed = time.monotonic() - started

    assert caught.value.code == "PROVIDER_UNAVAILABLE"
    assert caught.value.retryable is True
    assert elapsed < 0.9  # the deadline, not the drip, ended it


# ── LP-4 / BC-14: hostile bodies, and exactly what an error says ─────────


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_a_deeply_nested_body_is_a_provider_failure_not_a_crash(
    provider: HiringProvider,
) -> None:
    client, _ = _client(lambda request: httpx.Response(200, content=b"[" * 200_000))
    with pytest.raises(ApiError) as caught:
        await search_provider(client, provider, api_key=KEY, query=QUERY, freshness_param="x")
    assert caught.value.code == "PROVIDER_UNAVAILABLE"
    assert caught.value.retryable is True


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_a_body_over_the_size_cap_is_refused_before_it_is_parsed(
    provider: HiringProvider,
) -> None:
    padding = "x" * (MAX_BODY_BYTES + 10)
    body = {**_bodies_for(provider, []), "padding": padding}
    client, _ = _client(_json_handler(body))
    with pytest.raises(ApiError) as caught:
        await search_provider(client, provider, api_key=KEY, query=QUERY, freshness_param="x")
    assert caught.value.code == "PROVIDER_UNAVAILABLE"
    assert "larger response" in caught.value.message
    assert padding[:50] not in json.dumps(caught.value.to_body())


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_a_body_at_the_size_cap_is_still_read(provider: HiringProvider) -> None:
    empty = json.dumps({**_bodies_for(provider, []), "padding": ""}).encode()
    padded = json.dumps(
        {**_bodies_for(provider, []), "padding": "x" * (MAX_BODY_BYTES - len(empty))}
    ).encode()
    assert len(padded) == MAX_BODY_BYTES
    client, _ = _client(lambda request: httpx.Response(200, content=padded))
    assert (
        await search_provider(client, provider, api_key=KEY, query=QUERY, freshness_param="x") == []
    )


_LABELS = {"you_com": "You.com", "brave": "Brave", "serper": "Serper", "firecrawl": "Firecrawl"}


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_the_failure_messages_are_exactly_these_and_name_only_the_provider(
    provider: HiringProvider,
) -> None:
    label = _LABELS[provider]

    client, _ = _client(lambda request: httpx.Response(200, content=b"SECRET-BODY not json"))
    with pytest.raises(ApiError) as unreadable:
        await search_provider(client, provider, api_key=KEY, query=QUERY, freshness_param="x")
    assert unreadable.value.message == f"{label} returned a response this feature could not read."

    slow = httpx.AsyncClient(transport=_DripTransport(b"{" + b" " * 40 + b"}", 0.1))
    with pytest.raises(ApiError) as timed_out:
        await search_provider(
            slow, provider, api_key=KEY, query=QUERY, freshness_param="x", timeout=0.2
        )
    assert timed_out.value.message == f"{label} did not answer in time."

    big = {**_bodies_for(provider, []), "p": "x" * (MAX_BODY_BYTES + 1)}
    client, _ = _client(_json_handler(big))
    with pytest.raises(ApiError) as oversized:
        await search_provider(client, provider, api_key=KEY, query=QUERY, freshness_param="x")
    assert oversized.value.message == f"{label} sent a larger response than this feature will read."

    for error in (unreadable.value, timed_out.value, oversized.value):
        assert "SECRET-BODY" not in json.dumps(error.to_body())


# ── LP-3: the runtime half of the hard line ──────────────────────────────


@pytest.mark.parametrize("host", sorted(PROVIDER_HOSTS))
async def test_the_guard_lets_a_provider_host_through(host: str) -> None:
    await refuse_non_provider_hosts(httpx.Request("POST", f"https://{host}/v1/search"))


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/feed/update/urn:li:activity:7506381452083381426",
        "https://linkedin.com/posts/x",
        "https://es.linkedin.com/posts/x",
        "https://evil.example/v1/search",
        "https://ydc-index.io.evil.example/v1/search",  # a lookalike suffix
        "https://evil.example/ydc-index.io",  # the host in the path
        "http://api.firecrawl.dev/v2/search",  # right host, not https
    ],
)
async def test_the_guard_refuses_everything_else_before_it_is_sent(url: str) -> None:
    with pytest.raises(httpx.RequestError):
        await refuse_non_provider_hosts(httpx.Request("GET", url))


async def test_a_client_with_the_guard_never_puts_a_linkedin_request_on_the_wire() -> None:
    transport = RecordingTransport(lambda request: httpx.Response(200, json={}, request=request))
    client = httpx.AsyncClient(
        transport=transport, event_hooks={"request": [refuse_non_provider_hosts]}
    )

    with pytest.raises(httpx.RequestError):
        await client.get("https://www.linkedin.com/feed/update/urn:li:activity:7506381452083381426")
    assert transport.requests == []


async def test_a_refused_host_reads_as_a_provider_failure_like_any_transport_error() -> None:
    """The four provider helpers turn `httpx.HTTPError` into `PROVIDER_UNAVAILABLE`;
    the guard raises one, so a refusal cannot surface as anything else."""
    transport = RecordingTransport(lambda request: httpx.Response(200, json={}, request=request))
    client = httpx.AsyncClient(
        transport=transport, event_hooks={"request": [refuse_non_provider_hosts]}
    )
    with pytest.raises(httpx.HTTPError):
        await client.post("https://www.linkedin.com/x")
