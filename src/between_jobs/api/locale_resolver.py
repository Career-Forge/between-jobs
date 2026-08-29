"""Locale resolution for resume generation (R4, resumeforge-shape-and-fit.md).

Precedence: document override -> user default -> job-country detection ->
None (forge-engines' own `resolve_shape` falls back to its DEFAULT profile
when given `None`). `document_override`/`user_default` are real parameters
here, not dead ones -- they're `None` at every call site TODAY because
`resume_documents.shape_overrides` (R6) doesn't exist in the schema yet.
Once R6 ships that column, `run_prepare_application` passes real values
through unchanged; this function's own precedence logic needs no rework.

`detect_country_from_text` is a deliberately small, keyword-based heuristic
over free-text job-posting locations (`job_snapshots.location_text`) --
not a geocoding service. It only needs to distinguish the ~18 locales
forge-engines actually has profiles for (`locale_profiles_v2.json`); a
location it can't confidently place returns `None` rather than guessing,
which resolves to forge-engines' own honest DEFAULT+optional-note behavior
rather than a wrong-but-confident locale.
"""

from __future__ import annotations

import re

# Ordered longest/most-specific keyword first within each entry isn't
# needed here -- every keyword across all locales is checked, and the
# FIRST locale with any match wins (dict iteration order = definition
# order below), so more distinctive locales are listed before broader
# same-language ones (e.g. Ireland before UK, Canada before US) to avoid a
# generic "London, Ontario" mis-hit -- itself not fully solvable by a
# keyword heuristic, which is exactly why this stays best-effort.
_COUNTRY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "IE": ("ireland", "dublin", "cork", "galway"),
    "UK": ("united kingdom", "england", "scotland", "wales", "london", "manchester", " uk"),
    "CA": ("canada", "toronto", "vancouver", "montreal", "ottawa", "calgary"),
    "US": (
        "united states",
        "usa",
        " u.s.",
        "new york",
        "san francisco",
        "seattle",
        "austin",
        "boston",
        "chicago",
        "los angeles",
    ),
    "NL": ("netherlands", "amsterdam", "rotterdam", "the hague", "eindhoven"),
    "NORDICS": (
        "sweden",
        "stockholm",
        "norway",
        "oslo",
        "denmark",
        "copenhagen",
        "finland",
        "helsinki",
    ),
    "FR": ("france", "paris", "lyon", "toulouse"),
    "DE": ("germany", "berlin", "munich", "hamburg", "frankfurt"),
    "AT": ("austria", "vienna"),
    "CH": ("switzerland", "zurich", "geneva", "basel", "zürich"),
    "MX": ("mexico", "mexico city", "guadalajara", "monterrey"),
    "AU": ("australia", "sydney", "melbourne", "brisbane", "perth"),
    "NZ": ("new zealand", "auckland", "wellington"),
    "IN": (
        "india",
        "bengaluru",
        "bangalore",
        "hyderabad",
        "pune",
        "mumbai",
        "delhi",
        "gurugram",
        "gurgaon",
    ),
    "BR": ("brazil", "sao paulo", "são paulo", "rio de janeiro"),
    "GULF": (
        "united arab emirates",
        "uae",
        "dubai",
        "abu dhabi",
        "saudi arabia",
        "riyadh",
        "qatar",
        "doha",
    ),
    "SEA_HUB": ("singapore", "malaysia", "kuala lumpur"),
    "JP": ("japan", "tokyo", "osaka"),
    "CN": ("china", "shanghai", "beijing", "shenzhen"),
}

_WHITESPACE_RE = re.compile(r"\s+")


def detect_country_from_text(location_text: str | None) -> str | None:
    if not location_text:
        return None
    normalized = " " + _WHITESPACE_RE.sub(" ", location_text.strip().lower()) + " "
    for country_code, keywords in _COUNTRY_KEYWORDS.items():
        if any(kw in normalized for kw in keywords):
            return country_code
    return None


def resolve_locale_for_prepare(
    *,
    document_override: str | None = None,
    user_default: str | None = None,
    job_location_text: str | None = None,
) -> str | None:
    """Returns a country code (or `None` if nothing resolved) -- the caller
    passes this straight through to forge-engines' `/apply` as `locale`;
    forge-engines' own `resolve_shape` handles the DEFAULT+note fallback
    for `None`/unmapped/deferred codes, so this function never needs to
    duplicate that logic."""
    if document_override:
        return document_override
    if user_default:
        return user_default
    return detect_country_from_text(job_location_text)
