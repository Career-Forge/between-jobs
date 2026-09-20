"""Hiring Signals P3 -- the registry lookup behind `ats_echo` matching.

LinkedIn auto-generates a "We're #hiring a new <role> in <city>" post for
every job an employer's ATS syndicates to it. Such a post is an ATS listing
in a LinkedIn costume, and if the job registry (the ATS poller's
`job_registry_postings`) already tracks that listing, showing the post again
as a "hiring signal" is a duplicate. `hiring_signals.match_ats_echo_to_registry`
is the pure matcher; it takes candidates as input, and this module is the
database read that produces them.

**What it reads.** The registry companies whose name contains the
application's company as words (`ilike`, a superset -- the caller decides
which of them are the same company, see `hiring_signal_company.
identity_keys_of`), then those companies' ACTIVE postings, newest first,
capped. A closed posting is not "a listing we already track", so it is never a
candidate.

**Bounded.** At most `MAX_COMPANIES` companies and `MAX_POSTINGS` postings
are read. A company with more open postings than the cap can have an echo
whose posting falls outside it; that echo comes back `unmatched` and is shown
rather than hidden -- the safe direction, since only a `matched` echo is
hidden.

**Untrusted input.** The company name reaches a `LIKE` pattern, so it is
reduced to its word characters first (`[^\\W_]+` tokens joined by `%`), which
contain no wildcard, escape or quote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from typing import Any, cast

from supabase import AsyncClient

from .geo_gazetteer import build_gazetteer
from .hiring_signal_company import CompanyNames, tokens_of
from .hiring_signals import RegistryCandidate

MAX_COMPANIES = 20
MAX_POSTINGS = 1000

_TOKEN_RX = re.compile(r"[^\W_]+")
_LOCATION_PARTS_RX = re.compile(r"[,;/|]")
_PLACELESS_WORDS = frozenset({"remote", "anywhere", "worldwide", "global", "hybrid", "onsite"})


@cache
def _country_names() -> frozenset[str]:
    """Every country name and alias the gazetteer knows, in comparison form
    (`fold`ed and tokenized, so `U.S.` and `USA` are found the way a location
    text spells them). The bundled alias file only: no city data, no database."""
    aliases = build_gazetteer([]).country_alias_to_code
    return frozenset(" ".join(tokens_of(alias)) for alias in aliases)


def is_country_level(location: str | None) -> bool:
    """Whether `location` names no place narrower than a country: a country
    (`United States`, `USA`, `India`), `Remote`, `Anywhere`, or a list of them.
    Such a location cannot say WHICH opening a listing is -- the same title at
    the same company in two cities is two openings -- so the echo matcher is
    told it is unknown instead. `None` and text with no letters are not
    country-level (there is nothing to say)."""
    if location is None:
        return False
    parts = [part for part in _LOCATION_PARTS_RX.split(location) if part.strip()]
    if not parts:
        return False
    countries = _country_names()
    for part in parts:
        words = [t for t in tokens_of(part) if t not in _PLACELESS_WORDS]
        if words and " ".join(words) not in countries:
            return False
    return True


@dataclass(frozen=True)
class RegistryLookup:
    """What the registry holds for a company name: the names of the registry
    companies the words matched (`companies`, a superset -- including a
    company with no open postings at all) and their ACTIVE postings as matcher
    candidates. The two are separate because "the registry has this company but
    no such listing" and "the registry has no company we can tie this to" are
    different answers."""

    companies: tuple[str, ...]
    candidates: list[RegistryCandidate]


async def fetch_registry_lookup(supabase: AsyncClient, names: CompanyNames) -> RegistryLookup:
    """The registry companies whose name contains the words of `names` (its
    shortest spelling) and their active postings. Empty when there are no
    words or no such company."""
    words = [w for token in names.registry_words for w in _TOKEN_RX.findall(token)]
    if not words:
        return RegistryLookup(companies=(), candidates=[])
    pattern = "%" + "%".join(words) + "%"
    companies = (
        await supabase.table("job_registry_companies")
        .select("id, name")
        .ilike("name", pattern)
        .limit(MAX_COMPANIES)
        .execute()
    )
    name_by_id = {
        cast(dict[str, Any], c)["id"]: cast(dict[str, Any], c)["name"] for c in companies.data
    }
    if not name_by_id:
        return RegistryLookup(companies=(), candidates=[])
    postings = (
        await supabase.table("job_registry_postings")
        .select("id, company_id, title, location")
        .in_("company_id", list(name_by_id))
        .eq("status", "active")
        .order("first_seen", desc=True)
        .limit(MAX_POSTINGS)
        .execute()
    )
    candidates: list[RegistryCandidate] = []
    for row in postings.data:
        posting = cast(dict[str, Any], row)
        company = name_by_id.get(posting["company_id"])
        title = posting.get("title")
        if not company or not isinstance(title, str):
            continue
        location = posting.get("location")
        candidates.append(
            RegistryCandidate(
                company=company,
                title=title,
                ref=str(posting["id"]),
                location=location if isinstance(location, str) else None,
            )
        )
    return RegistryLookup(
        companies=tuple(name for name in name_by_id.values() if isinstance(name, str)),
        candidates=candidates,
    )
