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
search call, so no scrape/search credit is spent validating). You.com
has no such endpoint at all (confirmed against its current API reference
before writing this, not assumed) -- validating a You.com key costs a
real, tiny charge (~$0.005, one `count=1` search) every time a user saves
or rotates one. Documented here rather than silently eaten, since it's a
real cost this project's own "no shared/free-riding usage" BYOK stance
would want surfaced, not hidden in a comment nobody reads.

Saving an LLM credential also upserts the "default" capability_preference
to point at it -- the only capability distinction this platform draws for
LLM execution mode is "configured or not." Search-provider credentials
(You.com, Firecrawl) don't touch capability_preferences at all: Horizon
Sprint 5.0's company_intel pipeline looks them up directly by (service,
provider) via `credential_resolver.try_get_secret` (they're either present
or degrade the pipeline, never "the chosen provider for a capability" the
way an LLM model is).
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
}


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


_VALIDATORS = {
    ("llm", "openrouter"): _validate_openrouter_key,
    ("search", "you_com"): _validate_you_com_key,
    ("search", "firecrawl"): _validate_firecrawl_key,
}


def _redact(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k != "secret_encrypted"}


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

    await _VALIDATORS[key](http, body.secret)

    saved = await save_credential(
        supabase,
        user_id,
        service=body.service,
        provider=body.provider,
        secret=body.secret,
        model=body.model,
        base_url=body.base_url,
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
