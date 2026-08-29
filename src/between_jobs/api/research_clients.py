"""HTTP clients for the two research providers Proposal §26.1 names for
broad discovery -- You.com Search and Firecrawl Search (Horizon Sprint
5.0). Shapes verified against each provider's current API reference
before writing this, not assumed from training data or the Proposal's
own "illustrative, re-verify current request shapes" example payloads.

Both return a common `SearchHit` shape so `company_intel_pipeline.py`
doesn't need to know which provider actually answered a given query --
matching §26.1's own "Provider failure behavior" table, where either one
(or neither) can answer without the caller needing a different code path
per provider.
"""

from __future__ import annotations

from typing import TypedDict

import httpx

from .errors import ApiError

_YOU_COM_URL = "https://ydc-index.io/v1/search"
_FIRECRAWL_URL = "https://api.firecrawl.dev/v2/search"

_TIMEOUT_SECONDS = 20.0


class SearchHit(TypedDict):
    title: str
    url: str
    snippet: str
    published_at: str | None


async def search_you_com(
    http: httpx.AsyncClient, *, api_key: str, query: str, count: int = 5
) -> list[SearchHit]:
    try:
        response = await http.post(
            _YOU_COM_URL,
            headers={"X-API-Key": api_key},
            json={"query": query, "count": count},
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "Couldn't reach You.com. Try again in a moment.",
            retryable=True,
        ) from e

    if response.status_code >= 400:
        raise ApiError(
            "PROVIDER_UNAVAILABLE", "You.com couldn't complete that search.", retryable=True
        )

    body = response.json()
    web_results = body.get("results", {}).get("web", [])
    return [
        SearchHit(
            title=r.get("title", ""),
            url=r["url"],
            snippet=" ".join(r.get("snippets", [])) or r.get("description", ""),
            published_at=r.get("page_age"),
        )
        for r in web_results
        if r.get("url")
    ]


async def search_firecrawl(
    http: httpx.AsyncClient, *, api_key: str, query: str, limit: int = 5
) -> list[SearchHit]:
    try:
        response = await http.post(
            _FIRECRAWL_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"query": query, "limit": limit, "sources": [{"type": "web"}]},
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "Couldn't reach Firecrawl. Try again in a moment.",
            retryable=True,
        ) from e

    if response.status_code >= 400:
        raise ApiError(
            "PROVIDER_UNAVAILABLE", "Firecrawl couldn't complete that search.", retryable=True
        )

    body = response.json()
    web_results = body.get("data", {}).get("web", [])
    return [
        SearchHit(
            title=r.get("title", ""),
            url=r["url"],
            snippet=r.get("description", "") or "",
            published_at=None,
        )
        for r in web_results
        if r.get("url")
    ]
