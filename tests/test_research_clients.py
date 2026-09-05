"""Tests for the You.com/Firecrawl search clients (Horizon Sprint 5.0).

Uses httpx's own MockTransport (matching test_telegram_client.py's own
convention) -- no real network call, no real spend in a unit test.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from between_jobs.api.errors import ApiError
from between_jobs.api.research_clients import (
    scrape_firecrawl,
    search_exa_people,
    search_firecrawl,
    search_you_com,
)


def _client(handler: Any) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


async def test_search_you_com_sends_the_expected_request() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"results": {"web": []}}, request=request)

    http = _client(handler)
    await search_you_com(http, api_key="yc-key", query="Acme funding", count=3)

    assert captured["url"] == "https://ydc-index.io/v1/search"
    assert captured["headers"]["x-api-key"] == "yc-key"
    assert captured["body"] == {"query": "Acme funding", "count": 3}


async def test_search_you_com_parses_web_results() -> None:
    body = {
        "results": {
            "web": [
                {
                    "title": "Acme raises Series B",
                    "url": "https://news.example/acme-series-b",
                    "snippets": ["Acme raised $50M."],
                    "page_age": "2026-08-01",
                },
                {"title": "No URL here", "snippets": ["skip me"]},
            ]
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body, request=request)

    http = _client(handler)
    hits = await search_you_com(http, api_key="yc-key", query="Acme")

    assert len(hits) == 1
    assert hits[0]["title"] == "Acme raises Series B"
    assert hits[0]["url"] == "https://news.example/acme-series-b"
    assert hits[0]["snippet"] == "Acme raised $50M."
    assert hits[0]["published_at"] == "2026-08-01"


async def test_search_you_com_raises_provider_unavailable_on_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"}, request=request)

    http = _client(handler)
    with pytest.raises(ApiError) as exc_info:
        await search_you_com(http, api_key="yc-key", query="Acme")

    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"


async def test_search_firecrawl_sends_the_expected_request() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {"web": []}}, request=request)

    http = _client(handler)
    await search_firecrawl(http, api_key="fc-key", query="Acme layoffs", limit=5)

    assert captured["url"] == "https://api.firecrawl.dev/v2/search"
    assert captured["headers"]["authorization"] == "Bearer fc-key"
    assert captured["body"]["query"] == "Acme layoffs"
    assert captured["body"]["limit"] == 5
    assert captured["body"]["sources"] == [{"type": "web"}]


async def test_search_firecrawl_parses_web_results() -> None:
    body = {
        "data": {
            "web": [
                {
                    "title": "Acme culture",
                    "url": "https://acme.example/careers",
                    "description": "We value autonomy.",
                }
            ]
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body, request=request)

    http = _client(handler)
    hits = await search_firecrawl(http, api_key="fc-key", query="Acme culture")

    assert len(hits) == 1
    assert hits[0]["url"] == "https://acme.example/careers"
    assert hits[0]["snippet"] == "We value autonomy."
    assert hits[0]["published_at"] is None


async def test_search_firecrawl_raises_provider_unavailable_on_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"}, request=request)

    http = _client(handler)
    with pytest.raises(ApiError) as exc_info:
        await search_firecrawl(http, api_key="bad-key", query="Acme")

    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"


async def test_scrape_firecrawl_sends_the_expected_request() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {"markdown": "", "metadata": {}}}, request=request)

    http = _client(handler)
    await scrape_firecrawl(http, api_key="fc-key", url="https://acme.example/jobs/1")

    assert captured["url"] == "https://api.firecrawl.dev/v2/scrape"
    assert captured["headers"]["authorization"] == "Bearer fc-key"
    assert captured["body"] == {
        "url": "https://acme.example/jobs/1",
        "formats": ["markdown"],
        "onlyMainContent": True,
    }


async def test_scrape_firecrawl_parses_a_real_response_shape() -> None:
    """Shape verified live against a real Firecrawl /v2/scrape call
    against a real Coinbase Greenhouse posting before writing this."""
    body = {
        "success": True,
        "data": {
            "markdown": "# Software Engineer\n\nBuild things.",
            "metadata": {
                "title": "Software Engineer, Blockchain Platform Nodes - Coinbase",
                "og:title": "Software Engineer, Blockchain Platform Nodes - Coinbase",
            },
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body, request=request)

    http = _client(handler)
    page = await scrape_firecrawl(http, api_key="fc-key", url="https://acme.example/jobs/1")

    assert page["markdown"] == "# Software Engineer\n\nBuild things."
    assert page["title"] == "Software Engineer, Blockchain Platform Nodes - Coinbase"


async def test_scrape_firecrawl_falls_back_to_og_title() -> None:
    body = {"data": {"markdown": "content", "metadata": {"og:title": "Fallback Title"}}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body, request=request)

    http = _client(handler)
    page = await scrape_firecrawl(http, api_key="fc-key", url="https://acme.example/jobs/1")

    assert page["title"] == "Fallback Title"


async def test_scrape_firecrawl_handles_missing_title() -> None:
    body = {"data": {"markdown": "content", "metadata": {}}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body, request=request)

    http = _client(handler)
    page = await scrape_firecrawl(http, api_key="fc-key", url="https://acme.example/jobs/1")

    assert page["title"] is None


async def test_scrape_firecrawl_raises_provider_unavailable_on_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"}, request=request)

    http = _client(handler)
    with pytest.raises(ApiError) as exc_info:
        await scrape_firecrawl(http, api_key="fc-key", url="https://acme.example/jobs/1")

    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"


async def test_search_exa_people_sends_the_expected_request() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"results": []}, request=request)

    http = _client(handler)
    await search_exa_people(http, api_key="exa-key", query="Jane Doe at Acme", num_results=3)

    assert captured["url"] == "https://api.exa.ai/search"
    assert captured["headers"]["x-api-key"] == "exa-key"
    assert captured["body"] == {
        "query": "Jane Doe at Acme",
        "category": "people",
        "type": "auto",
        "numResults": 3,
    }


async def test_search_exa_people_extracts_the_person_entity_name() -> None:
    body = {
        "results": [
            {
                "url": "https://www.linkedin.com/in/janedoe",
                "entities": [
                    {"type": "person", "properties": {"name": "Jane Doe"}},
                    {"type": "organization", "properties": {"name": "Acme"}},
                ],
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body, request=request)

    http = _client(handler)
    hits = await search_exa_people(http, api_key="exa-key", query="Jane Doe at Acme")

    assert hits == [{"url": "https://www.linkedin.com/in/janedoe", "entity_name": "Jane Doe"}]


async def test_search_exa_people_handles_a_result_with_no_person_entity() -> None:
    body = {"results": [{"url": "https://www.linkedin.com/in/janedoe", "entities": []}]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body, request=request)

    http = _client(handler)
    hits = await search_exa_people(http, api_key="exa-key", query="Jane Doe at Acme")

    assert hits == [{"url": "https://www.linkedin.com/in/janedoe", "entity_name": None}]


async def test_search_exa_people_raises_provider_unavailable_on_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"}, request=request)

    http = _client(handler)
    with pytest.raises(ApiError) as exc_info:
        await search_exa_people(http, api_key="bad-key", query="Jane Doe at Acme")

    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"
