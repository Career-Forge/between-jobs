"""HTTP surface for BYOK credentials (Sprint 2.7e; widened Horizon Sprint
5.0).

Saving any (service, provider) pair not in `_SUPPORTED_CREDENTIALS` is
rejected with INVALID_INPUT rather than silently accepted and left
unvalidated -- an unvalidated, unsupported credential sitting in the
table would be exactly the "silent platform fallback" Proposal §11 rules
out.

Validation lives here, not in provider_credentials_store.py -- it's an
outbound network call to a third party, a routing/orchestration concern,
not a persistence one. A key is only ever persisted with
`is_validated=True` after the provider itself confirms it. OpenRouter has
a free, non-billed key-info endpoint; Firecrawl's credit-usage endpoint
is the closest equivalent (a billing/account-status read, not a scrape/
search call, so no scrape/search credit is spent validating). You.com,
Serper, Brave, JSearch, Adzuna, and USAJobs have no such endpoint at all
(confirmed against each one's current API reference before writing
this, not assumed) -- validating any of these six costs a real, tiny
charge (one `count=1`/`num=1`/`num_pages=1`/`results_per_page=1` search)
every time a user saves or rotates a key. Documented here rather than
silently eaten, since it's a real cost this project's own "no shared/
free-riding usage" BYOK stance would want surfaced, not hidden in a
comment nobody reads. Adzuna and USAJobs are also the two credentials
needing `body.secret_2` (Job Finder P4c) -- `_TWO_SECRET_PROVIDERS`
gates that requirement and routes to `_TWO_SECRET_VALIDATORS` instead of
the plain single-secret `_VALIDATORS`; every other provider here never
sets `secret_2` at all.

Saving an LLM credential also upserts the "default" capability_preference
to point at it -- the only capability distinction this platform draws for
LLM execution mode is "configured or not." Search-provider credentials
(You.com, Firecrawl, Serper, Brave, JSearch, Adzuna, USAJobs) don't touch
capability_preferences at all: Horizon Sprint 5.0's company_intel
pipeline (You.com/Firecrawl) and Job Finder P4's search_providers.py
(all seven) look them up directly by (service, provider) via
`credential_resolver.try_get_secret`/`try_get_secret_pair` (they're
either present or degrade the relevant pipeline, never "the chosen
provider for a capability" the way an LLM model is).
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends

from supabase import AsyncClient

from .app_state import get_http_client, get_supabase
from .auth import require_user_id
from .capability_preferences_store import DEFAULT_CAPABILITY, set_preference
from .errors import ApiError
from .models import SaveCredentialRequest
from .provider_credentials_store import delete_credential, list_credentials, save_credential

router = APIRouter(prefix="/credentials")

_SUPPORTED_CREDENTIALS = {
    ("llm", "openrouter"),
    ("search", "you_com"),
    ("search", "firecrawl"),
    ("search", "serper"),
    ("search", "brave"),
    ("search", "jsearch"),
    ("search", "adzuna"),
    ("search", "usajobs"),
}

_TWO_SECRET_PROVIDERS = {("search", "adzuna"), ("search", "usajobs")}
"""Job Finder P4c: Adzuna (app_id + app_key) and USAJobs (an
Authorization-Key AND a registered email) are the only two credentials
that need `body.secret_2` -- every other (service, provider) in
`_SUPPORTED_CREDENTIALS` is a plain single-secret credential, validated
via `_VALIDATORS` below. Kept as a separate dict/set pair rather than
widening every existing validator's signature with an unused parameter."""


async def _validate_openrouter_key(http: httpx.AsyncClient, secret: str) -> None:
    try:
        response = await http.get(
            "https://openrouter.ai/api/v1/key",
            headers={"Authorization": f"Bearer {secret}"},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "Couldn't reach OpenRouter to validate that key. Try again in a moment.",
            retryable=True,
        ) from e

    if response.status_code in (401, 403):
        raise ApiError("PROVIDER_REJECTED", "OpenRouter rejected that key.")
    if response.status_code >= 400:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "OpenRouter couldn't validate that key right now. Try again in a moment.",
            retryable=True,
        )


async def _validate_you_com_key(http: httpx.AsyncClient, secret: str) -> None:
    """No free validation endpoint exists for You.com -- this is a real
    (tiny) paid search call, `count=1` to keep the cost minimal."""
    try:
        response = await http.post(
            "https://ydc-index.io/v1/search",
            headers={"X-API-Key": secret},
            json={"query": "between-jobs key validation", "count": 1},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "Couldn't reach You.com to validate that key. Try again in a moment.",
            retryable=True,
        ) from e

    if response.status_code in (401, 403):
        raise ApiError("PROVIDER_REJECTED", "You.com rejected that key.")
    if response.status_code >= 400:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "You.com couldn't validate that key right now. Try again in a moment.",
            retryable=True,
        )


async def _validate_firecrawl_key(http: httpx.AsyncClient, secret: str) -> None:
    try:
        response = await http.get(
            "https://api.firecrawl.dev/v2/team/credit-usage",
            headers={"Authorization": f"Bearer {secret}"},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "Couldn't reach Firecrawl to validate that key. Try again in a moment.",
            retryable=True,
        ) from e

    if response.status_code in (401, 403):
        raise ApiError("PROVIDER_REJECTED", "Firecrawl rejected that key.")
    if response.status_code >= 400:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "Firecrawl couldn't validate that key right now. Try again in a moment.",
            retryable=True,
        )


async def _validate_serper_key(http: httpx.AsyncClient, secret: str) -> None:
    """No free key-info endpoint exists for Serper either (confirmed
    against its live docs, Job Finder P4 research) -- same tradeoff as
    You.com's own validator: a real, tiny (1-credit) search call."""
    try:
        response = await http.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": secret, "Content-Type": "application/json"},
            json={"q": "between-jobs key validation", "num": 1},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "Couldn't reach Serper to validate that key. Try again in a moment.",
            retryable=True,
        ) from e

    if response.status_code in (401, 403):
        raise ApiError("PROVIDER_REJECTED", "Serper rejected that key.")
    if response.status_code >= 400:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "Serper couldn't validate that key right now. Try again in a moment.",
            retryable=True,
        )


async def _validate_brave_key(http: httpx.AsyncClient, secret: str) -> None:
    """No free key-info endpoint exists for Brave either -- same tradeoff
    as You.com/Serper's own validators: a real, tiny search call."""
    try:
        response = await http.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"X-Subscription-Token": secret, "Accept": "application/json"},
            params={"q": "between-jobs key validation", "count": 1},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "Couldn't reach Brave to validate that key. Try again in a moment.",
            retryable=True,
        ) from e

    if response.status_code in (401, 403):
        raise ApiError("PROVIDER_REJECTED", "Brave rejected that key.")
    if response.status_code >= 400:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "Brave couldn't validate that key right now. Try again in a moment.",
            retryable=True,
        )


async def _validate_jsearch_key(http: httpx.AsyncClient, secret: str) -> None:
    """No free key-info endpoint exists for JSearch on RapidAPI either --
    same tradeoff as the other search validators: a real, tiny (1-page,
    1-request-credit per JSearch's own billing model) search call."""
    try:
        response = await http.get(
            "https://jsearch.p.rapidapi.com/search-v2",
            headers={"X-RapidAPI-Key": secret, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"},
            params={"query": "between-jobs key validation", "num_pages": "1"},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "Couldn't reach JSearch to validate that key. Try again in a moment.",
            retryable=True,
        ) from e

    if response.status_code in (401, 403):
        raise ApiError("PROVIDER_REJECTED", "JSearch rejected that key.")
    if response.status_code >= 400:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "JSearch couldn't validate that key right now. Try again in a moment.",
            retryable=True,
        )


_VALIDATORS = {
    ("llm", "openrouter"): _validate_openrouter_key,
    ("search", "you_com"): _validate_you_com_key,
    ("search", "firecrawl"): _validate_firecrawl_key,
    ("search", "serper"): _validate_serper_key,
    ("search", "brave"): _validate_brave_key,
    ("search", "jsearch"): _validate_jsearch_key,
}


async def _validate_adzuna_key(http: httpx.AsyncClient, app_id: str, app_key: str) -> None:
    """Adzuna signals an auth failure with HTTP 410 ("Authorisation
    failed"), confirmed directly against its own live OpenAPI spec before
    writing this -- NOT 401/403 the way every other provider here does.
    Getting this wrong would have meant a bad Adzuna key silently passing
    validation. Country is fixed to "us" for this check only -- any of
    Adzuna's 19 supported countries works equally well to prove the
    credential itself is valid, since country is a per-search param, not
    part of the credential."""
    try:
        response = await http.get(
            "https://api.adzuna.com/v1/api/jobs/us/search/1",
            params={"app_id": app_id, "app_key": app_key, "results_per_page": "1", "what": "test"},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "Couldn't reach Adzuna to validate that key. Try again in a moment.",
            retryable=True,
        ) from e

    if response.status_code == 410:
        raise ApiError("PROVIDER_REJECTED", "Adzuna rejected that app_id/app_key.")
    if response.status_code >= 400:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "Adzuna couldn't validate that key right now. Try again in a moment.",
            retryable=True,
        )


async def _validate_usajobs_key(http: httpx.AsyncClient, api_key: str, email: str) -> None:
    """No free key-info endpoint exists for USAJobs either -- same
    tradeoff as the single-secret search validators: a real, tiny search
    call. `email` here is USAJobs' own registered-email requirement, sent
    as `User-Agent` per its real API contract (confirmed live, Job Finder
    P4 research) -- not a secret, but required on every call including
    this one."""
    try:
        response = await http.get(
            "https://data.usajobs.gov/api/search",
            params={"Keyword": "between-jobs key validation", "ResultsPerPage": "1"},
            headers={
                "Host": "data.usajobs.gov",
                "User-Agent": email,
                "Authorization-Key": api_key,
            },
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "Couldn't reach USAJobs to validate that key. Try again in a moment.",
            retryable=True,
        ) from e

    if response.status_code in (401, 403):
        raise ApiError("PROVIDER_REJECTED", "USAJobs rejected that key.")
    if response.status_code >= 400:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "USAJobs couldn't validate that key right now. Try again in a moment.",
            retryable=True,
        )


_TWO_SECRET_VALIDATORS = {
    ("search", "adzuna"): _validate_adzuna_key,
    ("search", "usajobs"): _validate_usajobs_key,
}


def _redact(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in ("secret_encrypted", "secret_2_encrypted")}


@router.post("", status_code=201)
async def save_credential_route(
    body: SaveCredentialRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> dict[str, Any]:
    key = (body.service, body.provider)
    if key not in _SUPPORTED_CREDENTIALS:
        raise ApiError(
            "INVALID_INPUT", f"{body.service}/{body.provider} isn't a supported credential yet."
        )

    if key in _TWO_SECRET_PROVIDERS:
        if not body.secret_2:
            raise ApiError(
                "INVALID_INPUT", f"{body.service}/{body.provider} needs a second value (secret_2)."
            )
        await _TWO_SECRET_VALIDATORS[key](http, body.secret, body.secret_2)
    else:
        await _VALIDATORS[key](http, body.secret)

    saved = await save_credential(
        supabase,
        user_id,
        service=body.service,
        provider=body.provider,
        secret=body.secret,
        model=body.model,
        base_url=body.base_url,
        secret_2=body.secret_2 if key in _TWO_SECRET_PROVIDERS else None,
        is_validated=True,
    )
    if body.service == "llm":
        await set_preference(
            supabase,
            user_id,
            capability=DEFAULT_CAPABILITY,
            execution_mode="byok_first_party",
            provider=body.provider,
            model=body.model,
        )
    return _redact(saved)


@router.get("")
async def list_credentials_route(
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> list[dict[str, Any]]:
    rows = await list_credentials(supabase, user_id)
    return [_redact(row) for row in rows]


@router.delete("/{service}/{provider}", status_code=204)
async def delete_credential_route(
    service: str,
    provider: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> None:
    await delete_credential(supabase, user_id, service=service, provider=provider)
