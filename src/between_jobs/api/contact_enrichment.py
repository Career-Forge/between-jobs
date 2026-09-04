"""Contact enrichment (outreach-contactfinder.md Phase C) -- Apollo.io
only for v1, opt-in and human-gated, layered on top of
`contact_research.py`'s own discovery output. Never part of discovery
itself -- matches both reference repos' own precedent (n8n's real `Apollo
Match` node, spend-capped; careerforge-command-center's `enrichCandidate()`,
gated behind an explicit `window.confirm()`). `reveal_personal_emails`/
`reveal_phone_number` stay forced false always -- Apollo's own confirmed
work email only, never a personal or guessed one, matching both reference
repos exactly.
"""

from __future__ import annotations

from typing import Any, TypedDict

import httpx

from .errors import ApiError

_APOLLO_MATCH_URL = "https://api.apollo.io/api/v1/people/match"
_TIMEOUT_SECONDS = 15.0


class EnrichmentResult(TypedDict):
    email: str | None
    email_status: str | None


async def enrich_candidate(
    http: httpx.AsyncClient, *, api_key: str, person_name: str, company: str
) -> EnrichmentResult:
    """One Apollo lookup for one already-selected candidate -- never a
    bulk/batch call. `reveal_personal_emails`/`reveal_phone_number` are
    hardcoded false, not caller-configurable, on purpose: this platform
    only ever surfaces a person's own confirmed WORK email, the same line
    both reference repos already draw."""
    try:
        response = await http.post(
            _APOLLO_MATCH_URL,
            headers={"X-Api-Key": api_key},
            params={
                "name": person_name,
                "organization_name": company,
                "reveal_personal_emails": "false",
                "reveal_phone_number": "false",
            },
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "Couldn't reach Apollo. Try again in a moment.",
            retryable=True,
        ) from e

    if response.status_code in (401, 403):
        raise ApiError("PROVIDER_REJECTED", "Apollo rejected that key.")
    if response.status_code >= 400:
        raise ApiError(
            "PROVIDER_UNAVAILABLE", "Apollo couldn't complete that lookup.", retryable=True
        )

    body: dict[str, Any] = response.json()
    person: dict[str, Any] = body.get("person") or {}
    email = person.get("email")

    # A real, documented Apollo quirk (confirmed against current developer
    # community reports, not assumed): with reveal_personal_emails=false
    # and no confirmed work email on file, Apollo returns the literal
    # placeholder string "email_not_unlocked@domain.com" rather than null
    # -- treating that as a real address would be a real, live bug, not a
    # hypothetical one.
    if isinstance(email, str) and "email_not_unlocked" in email:
        email = None

    return EnrichmentResult(
        email=email if isinstance(email, str) else None,
        email_status=person.get("email_status")
        if isinstance(person.get("email_status"), str)
        else None,
    )
