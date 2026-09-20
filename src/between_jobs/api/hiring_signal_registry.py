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

**Two callers, one matching rule.** The per-application search knows its
company up front and reads the registry once for it (`fetch_registry_lookup`).
The standalone tab has NO company: an echo's company is whatever page posted it,
so a single search can hold echoes from a dozen different employers.
`fetch_registry_lookup_for_pages` reads for all of them in ONE bounded batch --
one query for the registry companies (their names OR'd together) and one for
those companies' postings, however many echoes there are, never one query per
echo -- and `echo_registry_state` is the one place that decides what a lookup
says about one echo, for both callers.

**What the real registry taught the batch** (15,964 companies, 88,883 open
postings when it was measured): a batch shares its caps between every page in
it, and two things in real data break a plain "companies, then their newest
postings" read. (1) One employer can hold the whole postings cap: Amazon alone
has 12,537 open postings, and a batch that included it returned 1,000 postings,
all Amazon's, for every other page. The postings read is therefore narrowed by
the echoes' own ROLES (an `ilike` on each role's words, a superset of the title
equality the matcher then applies), so a company's newest thousand postings are
not what competes for the cap -- its postings for THE roles being asked about
are. (2) A very short name matches half the registry (`%at%t%` for `AT&T` matched
374 companies, `%ey%` 174, `%hp%` 31) and would fill the shared companies cap
with strangers; a page whose longest word is under `MIN_NAME_WORD_CHARS`
characters is not looked up at all, and its echoes stay unknown -- shown, never
hidden. The per-application read has the same looseness for such a name, but it
has one company to cap and not twenty, and it is unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import cache, cached_property
from typing import Any, cast

from supabase import AsyncClient

from .geo_gazetteer import build_gazetteer
from .hiring_signal_company import CompanyNames, identity_keys_of, tokens_of
from .hiring_signals import HiringSignal, RegistryCandidate, match_ats_echo_to_registry

MAX_COMPANIES = 20
MAX_POSTINGS = 1000
MIN_NAME_WORD_CHARS = 3
"""A page name whose longest registry word is shorter than this is not looked up
in a batch (see the module docstring)."""
MAX_ROLE_PATTERN_WORDS = 6
MAX_BATCH_REGISTRY_COMPANIES = 100
"""The most registry companies one BATCH read returns. A batch covers up to
`MAX_COMPANIES` different pages and each page's name can match several registry
companies (`Meta` matches `Meta Platforms`, `Metabase`, ...), so the batch is
allowed a wider read than the one-company lookup -- but still a fixed one. A
company cut off by it is simply one the batch cannot tie a page to, which is an
unknown match (shown), never a wrong hide.

It is also what bounds the SIZE of the postings request: the companies go into the
request's url as `company_id=in.(<uuid>,...)`, a uuid is 39 characters once encoded,
and a url longer than about 8 KB is refused by the usual reverse proxy. Measured on
the real request builder: 200 companies and 20 roles was 9.0-9.6 KB, 100 is 5.1-6.4
KB (`tests/test_hiring_signal_registry.py` builds the request and checks it). A
request that is refused anyway -- a role with absurdly long words -- is an ordinary
best-effort failure: every echo stays unknown and shown."""

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

    @cached_property
    def identity_keys_by_company(self) -> dict[str, frozenset[str]]:
        """Each registry company's run-together identity forms (computed once
        per lookup, not once per echo)."""
        return {name: identity_keys_of(name) for name in self.companies}


def _registry_words(names: CompanyNames) -> list[str]:
    return [w for token in names.registry_words for w in _TOKEN_RX.findall(token)]


def _registry_pattern(names: CompanyNames) -> str | None:
    """The `ilike` pattern for a company's words, or `None` when it has none."""
    words = _registry_words(names)
    return "%" + "%".join(words) + "%" if words else None


def _batch_registry_pattern(names: CompanyNames) -> str | None:
    """`_registry_pattern`, or `None` for a name too short to look up in a batch
    (see the module docstring)."""
    words = _registry_words(names)
    if not words or max(len(w) for w in words) < MIN_NAME_WORD_CHARS:
        return None
    return "%" + "%".join(words) + "%"


def _role_pattern(role: str) -> str | None:
    """The `ilike` pattern for a role title's words, in order (`Software
    Engineer II` -> `%Software%Engineer%II%`), or `None` when it has none. A
    SUPERSET of every title the matcher would call equal to `role` -- it only
    ever removes punctuation, as the matcher's own title key does -- so it
    narrows the read without ever hiding a title the matcher would have wanted.
    (One case is lost, in the safe direction: the matcher reads `C#` as `C sharp`,
    so a role spelled `C Sharp Developer` does not find a listing spelled `C#
    Developer`. The echo is shown as unmatched instead of hidden.)"""
    words = _TOKEN_RX.findall(role)[:MAX_ROLE_PATTERN_WORDS]
    return "%" + "%".join(words) + "%" if words else None


async def _lookup_for_companies(
    supabase: AsyncClient,
    name_by_id: dict[Any, Any],
    *,
    title_patterns: Sequence[str] = (),
) -> RegistryLookup:
    """The active postings of already-chosen registry companies, as matcher
    candidates (the second half of both reads). With `title_patterns`, only
    postings whose title matches one of them (see `_role_pattern`)."""
    if not name_by_id:
        return RegistryLookup(companies=(), candidates=[])
    query = (
        supabase.table("job_registry_postings")
        .select("id, company_id, title, location")
        .in_("company_id", list(name_by_id))
        .eq("status", "active")
    )
    if title_patterns:
        query = query.or_(",".join(f"title.ilike.{pattern}" for pattern in title_patterns))
    postings = await query.order("first_seen", desc=True).limit(MAX_POSTINGS).execute()
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


async def fetch_registry_lookup(supabase: AsyncClient, names: CompanyNames) -> RegistryLookup:
    """The registry companies whose name contains the words of `names` (its
    shortest spelling) and their active postings. Empty when there are no
    words or no such company."""
    pattern = _registry_pattern(names)
    if pattern is None:
        return RegistryLookup(companies=(), candidates=[])
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
    return await _lookup_for_companies(supabase, name_by_id)


async def fetch_registry_lookup_for_pages(
    supabase: AsyncClient, names: Sequence[CompanyNames], roles: Sequence[str]
) -> RegistryLookup:
    """The registry companies matching ANY of these page names, and their active
    postings for ANY of these roles, in two queries however many names and roles
    there are (see the module docstring). At most `MAX_COMPANIES` distinct names
    and `MAX_COMPANIES` distinct roles are used and at most
    `MAX_BATCH_REGISTRY_COMPANIES` companies and `MAX_POSTINGS` postings read --
    the bound holds whatever the caller passes. Empty when no name is long enough
    to look up, no company matches, or there is no role to look for."""
    patterns = list(
        dict.fromkeys(p for n in names if (p := _batch_registry_pattern(n)) is not None)
    )
    title_patterns = list(dict.fromkeys(p for r in roles if (p := _role_pattern(r)) is not None))
    if not patterns or not title_patterns:
        return RegistryLookup(companies=(), candidates=[])
    # The patterns are word characters and `%` only (see "Untrusted input"), so
    # they hold nothing that would end an `or` clause: no comma, dot, colon or
    # parenthesis.
    clauses = ",".join(f"name.ilike.{pattern}" for pattern in patterns[:MAX_COMPANIES])
    companies = (
        await supabase.table("job_registry_companies")
        .select("id, name")
        .or_(clauses)
        .limit(MAX_BATCH_REGISTRY_COMPANIES)
        .execute()
    )
    name_by_id = {
        cast(dict[str, Any], c)["id"]: cast(dict[str, Any], c)["name"] for c in companies.data
    }
    return await _lookup_for_companies(
        supabase, name_by_id, title_patterns=title_patterns[:MAX_COMPANIES]
    )


def echo_registry_state(echo: HiringSignal, lookup: RegistryLookup) -> str | None:
    """What `lookup` says about one `ats_echo` signal: `matched`, `possible`,
    `unmatched`, or `None` (unknown).

    `None` for an echo that carries no company (page) or no role to match on,
    and for an echo whose page cannot be tied to any registry company in the
    lookup -- the registry may well track its listings under a name this cannot
    connect to it (`AMD` for `Advanced Micro Devices`), and "we cannot tell"
    must not be reported as "we do not track it". `unmatched` therefore means
    something definite: the registry HAS this company and none of its open
    listings is this one.

    Which registry company is the page's company is decided here, by run-
    together identity (`Scale AI` is the registry's `scaleai`, `Meta` is `Meta
    Platforms, Inc.`); `hiring_signals.match_ats_echo_to_registry` is then
    asked only the question it is built for, title and place. A location that
    names only a country (or `Remote`) cannot say WHICH opening it is, so it is
    passed as unknown: an echo for `Austin, Texas` is not hidden by a registry
    listing in `United States`."""
    page = echo.author_name
    if page is None or echo.echo_role is None:
        return None
    page_keys = identity_keys_of(page)
    keys_by_company = lookup.identity_keys_by_company
    if not any(keys & page_keys for keys in keys_by_company.values()):
        return None  # the registry has no company we can tie this page to: unknown
    aligned = [
        replace(
            c,
            company=page,
            location=None if is_country_level(c.location) else c.location,
        )
        for c in lookup.candidates
        if keys_by_company.get(c.company, frozenset()) & page_keys
    ]
    probe = replace(echo, echo_location=None) if is_country_level(echo.echo_location) else echo
    state = match_ats_echo_to_registry(probe, aligned).state
    return None if state == "undeterminable" else state
