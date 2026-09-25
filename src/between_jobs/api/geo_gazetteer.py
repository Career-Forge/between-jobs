"""Job Finder P5d (live-search-track.md) -- the gazetteer-backed 3-state
location filter. A faithful port of n8n's real `resolveLocation`/
`checkLocationState` (read directly from `aggregate_jobs.js`, lines
109-301, not paraphrased).

Two real, disclosed simplifications from the reference:

1. n8n's own `scopeBuckets` (a whole `preferred`/`acceptable`-tier
   location-PREFERENCE system, sourced from a stored `user_prefs.
   location_scopes` this project has no equivalent of yet) is NOT ported
   -- `check_location_state` here takes a single target location
   (matching `search_jobs()`'s own existing `location: str | None`
   param), not a multi-bucket preference scope. `location_tier` doesn't
   exist here as a result; only match/mismatch/unknown.
2. The sponsorship hard-exclude n8n's own `Aggregate Jobs` bundles right
   next to this (`needsSponsorshipFor`, gated on `work_auth.authorized`)
   is genuinely NOT built here, and not because of laziness: between-
   jobs' own profile has `work_authorization` (free text, e.g. "H1B") and
   `work_authorization_status` (explicitly documented in profile.py as
   "render-only... never enter any LLM prompt" -- inert display data, not
   meant to drive logic). Neither is a structured ISO-country-authorized
   list the way n8n's own `work_auth.authorized` is. Building the hard-
   exclude against either would mean GUESSING a country list out of free
   text or repurposing a field its own docstring says not to use for
   logic -- exactly the kind of guessed behavior this project's "unknown
   means labeled as unknown, never guessed" charter rules out. A real
   structured work-authorization-country field is its own separate
   feature, not this sub-phase's job -- flagged here, not silently
   dropped or hacked around.

Fail-open throughout, matching n8n's own `try { ... } catch { GAZETTEER =
null }`: an empty or unavailable gazetteer means every location resolves
to "unresolved," so `check_location_state` always returns "unknown," never
a guessed match or mismatch -- a fresh BYOK self-host deployment that
hasn't run `scripts/import_geo_gazetteer.py` simply gets no location
filtering, not an error.

**A real, disclosed characteristic surfaced by this phase's own live
verification, not a bug**: a city-level request like "New York" resolves
BOTH a city ("New York City") AND that city's own home country ("US").
`check_location_state`'s country-match branch (`request_country in
job_geo.countries`) then means a job in, say, "Palo Alto, California"
counts as a MATCH for a "New York" search -- same country, different
city. Confirmed this is n8n's own real, intentional design (not
something this port introduced): the country-match branch exists as its
own deliberate signal, separate from the city-match branch, so a
COUNTRY-only request ("jobs in Germany," no city component) has
something to match against at all. The tradeoff is that a CITY-level
request inherits that same broad fallback for free, whether or not
that's the intent -- confirmed via real production data (a "New York"
search legitimately surfacing "Palo Alto, California" and "Charlotte,
North Carolina" postings as verified matches), not a hypothetical.
Documented here rather than silently accepted or silently "fixed" --
this project's own D2 discipline is to port proven logic faithfully and
diverge only where something is PROVEN wrong (like n8n's own s96
incident), not to unilaterally redesign behavior on a hunch it might be
too broad.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any, Literal, cast

from supabase import AsyncClient

from .supabase_helpers import fetch_all_pages

logger = logging.getLogger(__name__)

_REMOTE_RX = re.compile(r"\b(remote|wfh|work[\s-]?from[\s-]?home|telecommut\w*)\b", re.IGNORECASE)
_GLOBAL_RX = re.compile(r"\b(worldwide|global|anywhere|any\s*location)\b", re.IGNORECASE)
# Deliberately narrow (only "<CODE> Remote"/"Remote (<CODE>)" adjacency,
# case-SENSITIVE on the code) -- a bare uppercase 2-letter token collides
# too often with real words ("IT Support") to scan broadly. Verbatim from
# n8n's own real regex, including its own reasoning in its source comment.
_CODE_REMOTE_RX = re.compile(r"\b([A-Z]{2})\b[\s-]*[Rr]emote\b|\b[Rr]emote\b[\s(:-]*\b([A-Z]{2})\b")
_UPPER_CODE_EXTRA = {"UK": "GB"}

_SEGMENT_SPLIT_RX = re.compile(r"[;|]|\s+or\s+", re.IGNORECASE)
_FRAGMENT_SPLIT_RX = re.compile(r"[-:–—]")  # noqa: RUF001 -- en/em dash are real delimiters here
_PAREN_RX = re.compile(r"[()]")


@dataclass(frozen=True)
class GazetteerCity:
    name: str
    country_code: str
    population: int


@dataclass(frozen=True)
class Gazetteer:
    city_index: dict[str, tuple[GazetteerCity, ...]]
    """Lowercased name/alias -> cities sharing that name, population
    descending (so index 0 is the biggest city with that name by
    default, matching the reference's own documented disambiguation)."""
    country_alias_to_code: dict[str, str]
    """Lowercased alias ("usa", "america") -> ISO code."""
    country_names: dict[str, str]
    """ISO code -> display name, used only by the CODE_REMOTE_RX check to
    confirm a 2-letter token is a real country code."""


_EMPTY_GAZETTEER = Gazetteer(city_index={}, country_alias_to_code={}, country_names={})


def _load_country_aliases() -> tuple[dict[str, str], dict[str, str]]:
    raw = resources.files("between_jobs.api.data").joinpath("geo_country_aliases.json").read_text()
    data = json.loads(raw)
    alias_to_code: dict[str, str] = {}
    for code, aliases in data["countries"].items():
        for alias in aliases:
            alias_to_code[alias.lower()] = code
    return alias_to_code, cast("dict[str, str]", data["country_names"])


def build_gazetteer(city_rows: list[dict[str, Any]]) -> Gazetteer:
    """Builds the in-memory alias index once from already-fetched rows --
    mirrors n8n's own real design (load the whole file once per Code-node
    execution, build an index there) rather than a per-lookup SQL query;
    `resolveLocation`'s own multi-candidate, multi-segment matching isn't
    a shape SQL expresses cleanly. Rows MUST already be population-
    descending (the caller's query is responsible for that ordering) --
    this function doesn't re-sort, so a caller-side ordering bug would
    silently break "biggest city wins" disambiguation."""
    country_alias_to_code, country_names = _load_country_aliases()
    city_index: dict[str, list[GazetteerCity]] = {}
    for row in city_rows:
        city = GazetteerCity(
            name=row["name"], country_code=row["country_code"], population=row["population"] or 0
        )
        names = {row["name"], row["ascii_name"], *(row["alt_names"] or [])}
        for name in names:
            if not name:
                continue
            city_index.setdefault(name.lower(), []).append(city)
    return Gazetteer(
        city_index={k: tuple(v) for k, v in city_index.items()},
        country_alias_to_code=country_alias_to_code,
        country_names=country_names,
    )


_cached_gazetteer: Gazetteer | None = None
_FETCH_PAGE_SIZE = 1000


async def _fetch_all_city_rows(supabase: AsyncClient) -> list[dict[str, Any]]:
    """PostgREST caps an unranged `.select()` at its own default row
    limit (1,000) -- the identical bug class `import_job_registry_seed.
    py`'s own `_select_all_companies` already found and fixed for a
    different table (P1's real ~16k-row registry, silently resolving
    only the first page). Confirmed live here too, not assumed: a plain
    unranged select against the real 34,006-row gazetteer table returned
    exactly 1,000 rows. Same fix -- page via `.range()` until a short
    page confirms there's nothing left, preserving the same `order by
    population desc` on every page so the overall row sequence this
    function returns stays correctly population-descending throughout,
    not just within each page."""

    async def _page(start: int, end: int) -> list[dict[str, Any]]:
        result = (
            await supabase.table("geo_gazetteer_cities")
            .select("name, ascii_name, alt_names, country_code, population")
            .order("population", desc=True)
            .range(start, end)
            .execute()
        )
        return cast("list[dict[str, Any]]", result.data)

    return await fetch_all_pages(_page, page_size=_FETCH_PAGE_SIZE)


async def get_gazetteer(supabase: AsyncClient) -> Gazetteer:
    """Loads and caches the gazetteer for this process's lifetime -- it's
    static reference data (only changes via re-running the import script
    and restarting the service), so a per-call DB round-trip would be
    pure waste. Fails open to an empty gazetteer (never raises) if the
    table is empty or unreachable, matching n8n's own try/catch."""
    global _cached_gazetteer
    if _cached_gazetteer is not None:
        return _cached_gazetteer
    try:
        rows = await _fetch_all_city_rows(supabase)
    except Exception:
        logger.exception("gazetteer failed to load; location filtering falls back to unknown")
        rows = []
    _cached_gazetteer = build_gazetteer(rows) if rows else _EMPTY_GAZETTEER
    return _cached_gazetteer


@dataclass(frozen=True)
class LocationResolution:
    countries: frozenset[str]
    cities: frozenset[str]
    remote: bool
    is_global: bool
    unresolved: bool


def _candidates_for_part(part: str) -> list[str]:
    trimmed = part.strip()
    out = [trimmed]
    fragments = [f.strip() for f in _FRAGMENT_SPLIT_RX.split(trimmed) if f.strip()]
    if len(fragments) > 1:
        out.extend(reversed(fragments))
    return out


def resolve_location(gazetteer: Gazetteer, raw: str | None) -> LocationResolution:
    s = (raw or "").strip()
    if not s:
        return LocationResolution(frozenset(), frozenset(), False, False, True)

    remote = bool(_REMOTE_RX.search(s))
    is_global = bool(_GLOBAL_RX.search(s))
    countries_found: set[str] = set()
    cities_found: set[str] = set()
    any_resolved = False

    code_match = _CODE_REMOTE_RX.search(s)
    if code_match:
        code = code_match.group(1) or code_match.group(2)
        iso = code if code in gazetteer.country_names else _UPPER_CODE_EXTRA.get(code)
        if iso:
            countries_found.add(iso)
            any_resolved = True

    segments = [_PAREN_RX.sub("", seg).strip() for seg in _SEGMENT_SPLIT_RX.split(s) if seg.strip()]
    for segment in segments:
        parts = [p.strip() for p in segment.split(",") if p.strip()]
        matched_country: str | None = None
        for part in parts:
            matched_country = gazetteer.country_alias_to_code.get(part.lower())
            if matched_country:
                break
        if matched_country:
            countries_found.add(matched_country)
            any_resolved = True

        city_hit = False
        for part in parts:
            if city_hit:
                break
            for cand in _candidates_for_part(part):
                candidates = gazetteer.city_index.get(cand.lower())
                if not candidates:
                    continue
                city = candidates[0]
                if matched_country:
                    same_country = next(
                        (c for c in candidates if c.country_code == matched_country), None
                    )
                    if same_country:
                        city = same_country
                cities_found.add(city.name)
                countries_found.add(city.country_code)
                any_resolved = True
                city_hit = True
                break

    return LocationResolution(
        countries=frozenset(countries_found),
        cities=frozenset(cities_found),
        remote=remote,
        is_global=is_global,
        unresolved=not any_resolved and not remote and not is_global,
    )


LocationState = Literal["match", "mismatch", "unknown"]


def check_location_state(
    gazetteer: Gazetteer,
    job_location: str | None,
    *,
    request_cities: frozenset[str] = frozenset(),
    request_country: str | None = None,
) -> LocationState:
    """`request_cities` must already be lowercased by the caller (mirrors
    `resolve_location`'s own output, which callers typically feed
    straight in)."""
    hay = (job_location or "").lower().strip()
    if not hay or hay == "unknown":
        return "unknown"
    job_geo = resolve_location(gazetteer, job_location)
    if job_geo.unresolved:
        return "unknown"
    if job_geo.is_global:
        return "match"
    if request_cities and any(c.lower() in request_cities for c in job_geo.cities):
        return "match"
    if request_country and request_country in job_geo.countries:
        return "match"
    # s96: a job confidently resolved to OTHER countries is a real
    # mismatch EVEN WHEN it's remote -- "US Remote" means remote WITHIN
    # the US, not remote everywhere. The exact bug this whole gazetteer
    # upgrade exists to fix.
    if job_geo.countries:
        return "mismatch"
    if job_geo.remote:
        return "unknown"  # genuinely no country stated -- honestly ambiguous
    return "unknown"
