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
_FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"
_EXA_SEARCH_URL = "https://api.exa.ai/search"

_TIMEOUT_SECONDS = 20.0
_SCRAPE_TIMEOUT_SECONDS = 30.0
"""A single-page scrape (outreach-v2-search-first.md Phase J) is a
heavier real fetch than a search call -- verified live against a real
job posting before writing this: a real Firecrawl /v2/scrape response
for one Coinbase Greenhouse posting took several seconds and returned
~6KB of markdown."""


class SearchHit(TypedDict):
    title: str
    url: str
    snippet: str
    published_at: str | None


class ScrapedPage(TypedDict):
    markdown: str
    title: str | None


class ExaPersonHit(TypedDict):
    url: str
    entity_name: str | None
    """From the result's own `entities[]` where `type == "person"` --
    Exa's structured name for whoever it thinks this result is about,
    distinct from the page `title` (which is often the whole post/profile
    headline, not a bare name). `None` when Exa returned no person entity
    for this result at all -- the caller must not trust the URL alone as
    a confirmed match in that case."""


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


async def scrape_firecrawl(http: httpx.AsyncClient, *, api_key: str, url: str) -> ScrapedPage:
    """outreach-v2-search-first.md Phase J -- the SCRAPE endpoint (as
    distinct from `search_firecrawl`'s SEARCH endpoint above), used only
    for a URL the caller already has (a job posting the user pasted
    themselves), never for open-ended discovery. Callers must apply the
    host deny-list (`scrape_denylist.py`) BEFORE calling this -- this
    function itself does not check, since it has no way to distinguish
    "the caller already checked" from "the caller forgot to."""
    try:
        response = await http.post(
            _FIRECRAWL_SCRAPE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
            timeout=_SCRAPE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "Couldn't reach Firecrawl. Try again in a moment.",
            retryable=True,
        ) from e

    if response.status_code >= 400:
        raise ApiError(
            "PROVIDER_UNAVAILABLE", "Firecrawl couldn't fetch that page.", retryable=True
        )

    body = response.json()
    data = body.get("data", {})
    metadata = data.get("metadata", {})
    return ScrapedPage(
        markdown=data.get("markdown", "") or "",
        title=metadata.get("title") or metadata.get("og:title"),
    )


async def search_exa_people(
    http: httpx.AsyncClient, *, api_key: str, query: str, num_results: int = 3
) -> list[ExaPersonHit]:
    """outreach-v2-search-first.md Phase K -- Exa's `category="people"`
    search (there is no separate "People Search" endpoint; confirmed
    against Exa's own current docs before writing this). Auth is the
    `x-api-key` header, not `Authorization: Bearer` -- Exa's docs list
    both as valid, this one matches every one of its own examples."""
    try:
        response = await http.post(
            _EXA_SEARCH_URL,
            headers={"x-api-key": api_key},
            json={"query": query, "category": "people", "type": "auto", "numResults": num_results},
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE", "Couldn't reach Exa. Try again in a moment.", retryable=True
        ) from e

    if response.status_code >= 400:
        raise ApiError("PROVIDER_UNAVAILABLE", "Exa couldn't complete that search.", retryable=True)

    body = response.json()
    hits: list[ExaPersonHit] = []
    for result in body.get("results") or []:
        if not isinstance(result, dict):
            continue
        url = result.get("url")
        if not isinstance(url, str):
            continue
        entity_name: str | None = None
        for entity in result.get("entities") or []:
            if isinstance(entity, dict) and entity.get("type") == "person":
                name = (entity.get("properties") or {}).get("name")
                if isinstance(name, str) and name:
                    entity_name = name
                    break
        hits.append(ExaPersonHit(url=url, entity_name=entity_name))
    return hits
