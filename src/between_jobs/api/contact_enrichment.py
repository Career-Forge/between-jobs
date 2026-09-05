"""Contact enrichment (outreach-contactfinder.md Phase C; widened
outreach-v2-search-first.md Phase K) -- opt-in and human-gated, layered
on top of `contact_research.py`'s own discovery output. Never part of
discovery itself -- matches both reference repos' own precedent (n8n's
real `Apollo Match` node, spend-capped; careerforge-command-center's
`enrichCandidate()`, gated behind an explicit `window.confirm()`).
`reveal_personal_emails`/`reveal_phone_number` stay forced false always --
Apollo's own confirmed work email only, never a personal or guessed one,
matching both reference repos exactly.

Phase K adds Hunter.io as a second email-enrichment provider (Proposal
§28/§30 name Apollo/Hunter as a paired option, gated the same way -- "an
authorized enrichment provider," never a guess) and Exa as a distinct
LinkedIn-URL-discovery provider (never an email source -- Exa's own
response schema has no email field at all). Both are plain sibling
functions with no shared interface/protocol, same as Apollo -- there's
no abstraction to implement here, just a matching (person_name, company)
-> result shape per provider, so the caller (contact_research_routes.py)
can try one after another without any provider-specific branching beyond
"is this key configured."
"""

from __future__ import annotations

from typing import Any, TypedDict
from urllib.parse import urlparse

import httpx

from .errors import ApiError
from .research_clients import search_exa_people

_APOLLO_MATCH_URL = "https://api.apollo.io/api/v1/people/match"
_HUNTER_EMAIL_FINDER_URL = "https://api.hunter.io/v2/email-finder"
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


async def enrich_hunter(
    http: httpx.AsyncClient, *, api_key: str, person_name: str, company: str
) -> EnrichmentResult:
    """Hunter's Email Finder -- one lookup for one already-selected
    candidate, matching `enrich_candidate`'s own (person_name, company) ->
    EnrichmentResult shape exactly. Hunter's `full_name` param accepts a
    bare full name directly (confirmed against its current API spec), so
    this needs no first/last-name splitting of its own -- a real, if
    less common, wrong split on a multi-word surname would otherwise be
    a silent, self-inflicted bug. Hunter also accepts a bare company NAME
    for its `company` param (not just a domain), so this takes the exact
    same input Apollo already does."""
    try:
        response = await http.get(
            _HUNTER_EMAIL_FINDER_URL,
            params={"company": company, "full_name": person_name, "api_key": api_key},
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "Couldn't reach Hunter. Try again in a moment.",
            retryable=True,
        ) from e

    if response.status_code in (401, 403):
        raise ApiError("PROVIDER_REJECTED", "Hunter rejected that key.")
    if response.status_code >= 400:
        raise ApiError(
            "PROVIDER_UNAVAILABLE", "Hunter couldn't complete that lookup.", retryable=True
        )

    body: dict[str, Any] = response.json()
    data: dict[str, Any] = body.get("data") or {}
    email = data.get("email")
    verification: dict[str, Any] = data.get("verification") or {}

    return EnrichmentResult(
        email=email if isinstance(email, str) else None,
        email_status=verification.get("status")
        if isinstance(verification.get("status"), str)
        else None,
    )


class LinkedInDiscoveryResult(TypedDict):
    linkedin_url: str | None
    match_confidence: str
    """"strong" when Exa's own structured entity data names this exact
    candidate; "inferred" when a plausible linkedin.com/in/ URL came back
    but Exa attached no confirming person entity to it (Exa's people
    search is a fuzzy match, not an exact lookup -- a common name can
    surface someone else's profile as the top hit); "unsupported" when
    nothing came back at all. Never "verified" -- that value is reserved
    elsewhere (contact_research.Confidence) for a source that explicitly
    names the candidate in its own visible text, which a bare URL match
    here never establishes on its own."""


def _normalize_person_name(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _is_linkedin_profile_url(url: str) -> bool:
    """A real host check, not a substring test -- an adversarial review
    caught that `"linkedin.com/in/" in url.lower()` accepts ANY url
    containing that substring anywhere (path, query string, fragment),
    e.g. a phishing/tracker URL like `https://phish.example/next=
    linkedin.com/in/jane-doe`. Exa is a live web-search API over
    attacker-influenceable content, so this is a real, reachable risk,
    not a hypothetical one -- the same class of bug `scrape_denylist.py`
    already guards against for the deny side of this exact problem."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in ("linkedin.com", "www.linkedin.com") and urlparse(url).path.lower().startswith(
        "/in/"
    )


async def find_linkedin_exa(
    http: httpx.AsyncClient,
    *,
    api_key: str,
    person_name: str,
    company: str,
    claimed_title: str | None,
) -> LinkedInDiscoveryResult:
    """Exa People Search -- one lookup for one already-selected candidate
    who doesn't already have a LinkedIn URL from ordinary search evidence.
    Never resolves an email (Exa's schema has none); this only ever finds
    a profile URL, and only trusts it as "strong" when Exa's own entity
    data names this person EXACTLY (after normalization) -- matching this
    codebase's broader anti-fabrication discipline (`contact_research.
    _source_explicitly_names_candidate`'s same "don't trust a bare
    association" stance, applied here to a structured-data match instead
    of snippet text). An adversarial review caught that a bare substring
    check here (`target in entity_name`) would falsely mark "Jane Doe" as
    a "strong" match against an unrelated "Mary Jane Doerty" -- exact
    equality is the safe, conservative choice; anything less exact falls
    through to "inferred", never silently upgraded."""
    query = f"{person_name}"
    if claimed_title:
        query += f", {claimed_title}"
    query += f" at {company}, LinkedIn profile"

    hits = await search_exa_people(http, api_key=api_key, query=query)
    linkedin_hits = [h for h in hits if _is_linkedin_profile_url(h["url"])]
    if not linkedin_hits:
        return LinkedInDiscoveryResult(linkedin_url=None, match_confidence="unsupported")

    normalized_target = _normalize_person_name(person_name)
    for hit in linkedin_hits:
        entity_name = hit["entity_name"]
        if entity_name and _normalize_person_name(entity_name) == normalized_target:
            return LinkedInDiscoveryResult(linkedin_url=hit["url"], match_confidence="strong")

    return LinkedInDiscoveryResult(
        linkedin_url=linkedin_hits[0]["url"], match_confidence="inferred"
    )
