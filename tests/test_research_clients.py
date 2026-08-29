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
from between_jobs.api.research_clients import search_firecrawl, search_you_com


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
