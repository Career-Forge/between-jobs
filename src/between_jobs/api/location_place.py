"""Reading a location text as ONE place, or refusing to.

A job board writes a location as free text, and a person's search is free text too. The second
location pass confirms a board's location against a search only when both can be read, and this
module reads only what it can be sure of: the text must be one to three comma-separated plain names
and nothing else, and every other shape is "unknown" -- a list, a qualifier, a code beside other
words, a parenthesis whose content is not a plain name, digits, a conjunction. That keeps the
readable language small enough to enumerate, instead of trying to make sense of whatever a board
writes. Callers bound the length of the text (the route and the probes cap it at 200 characters).

What is accepted, after the words that say how the job is worked ("Remote", "Hybrid", ...) and any
parentheses are taken out (a parenthesis reads like a comma: "Seattle (WA)" is "Seattle, WA"):

- one part: a country ("Remote - US", "Germany"), or a city ("New York");
- two parts: a city and a US state or Canadian province, as a code or a name ("Austin, TX",
  "Austin, Texas"), or a city and a country ("London, UK");
- three parts: a city, a state or province and a country ("Austin, TX, United States"), or a city
  that the gazetteer has in that country with a part in between that is not read ("Bengaluru,
  Karnataka, India": the result is `qualified`, which confirms a search for the country and never
  a search for a city). A country written twice at the end ("London, UK, United Kingdom", or a
  board's "Austin, TX, United States" with its country appended again) counts once.

A country is established in two ways and no others. The text names it, as its last part or as the
whole text, and the city is a place the gazetteer has in that country (or an unknown town beside a
state: "Smalltown, TX, United States"); a text that merely starts with a country is a city, not
the country ("Jamaica, NY" is a borough), and two countries in a row are a list ("Ireland, United
States"). Or the text is a bare city, or a city and a state, and
the city's own name (not an alternate spelling or a code; a multi-word name may drop a trailing
"City", so "New York" is the city the table calls "New York City") is a city of at least
`LARGE_CITY_POPULATION` people that no place in another country shares: the gazetteer is
incomplete, and a town missing from it makes a foreign namesake look unique. A name for part of a
country ("England", "Scotland", "Wales", "Holland") is a country only as the last of several parts
("London, England"); alone it is nothing. ("Dubai" and "Abu Dhabi" alone are the cities.) A state
or province must belong to the city's country ("Toronto, OH" and "Toronto, California" are
nothing).
"""

from __future__ import annotations

import re
import unicodedata
from typing import NamedTuple

from .geo_gazetteer import Gazetteer

LARGE_CITY_POPULATION = 500_000


class Place(NamedTuple):
    """The place a location text names, as far as the text itself establishes it."""

    country: str
    city: str | None = None
    region: str | None = None
    qualified: bool = False
    """True when a part between the city and the country is not read: the country is established,
    the place inside it is not."""


_US_STATES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}

_CA_PROVINCES = {
    "AB": "Alberta",
    "BC": "British Columbia",
    "MB": "Manitoba",
    "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador",
    "NS": "Nova Scotia",
    "NT": "Northwest Territories",
    "NU": "Nunavut",
    "ON": "Ontario",
    "PE": "Prince Edward Island",
    "QC": "Quebec",
    "SK": "Saskatchewan",
    "YT": "Yukon",
}
"""US states (and DC) and Canadian provinces and territories, by their two-letter code. A code or a
name after a city is a region only if it is in here ("Newfoundland and Labrador" can be written by
its code only: a part with the word "and" in it is refused)."""

# The words that say how a job is worked, not where.
_WORK_MODE_RX = re.compile(r"\b(?:remote|hybrid|on-?site|in[- ]office|wfh)\b", re.IGNORECASE)
# What is stripped from the ends of a part: the separators a work mode leaves behind ("Remote - X").
_EDGE_CHARS = " ;|/:-\u2013\u2014"
# A name: letters, joined by one space, hyphen, apostrophe or period; "St. Louis", "Winston-Salem",
# "O'Fallon", "D.C.". No digit (of any script: see `_has_number`), no other punctuation, no space
# beside a hyphen.
_NAME_RX = re.compile(r"^[^\W\d_]+(?:(?: |-|['\u2019]|\.|\. )[^\W\d_]+)*\.?$")
_CONJUNCTION_RX = re.compile(r"\b(?:or|and)\b", re.IGNORECASE)

# Names for part of a country that the alias table lists as countries. As the last of several
# parts ("London, England") one establishes the country; alone it does not.
_SUBNATIONAL_ALIASES = frozenset({"england", "scotland", "wales", "holland", "dubai", "abu dhabi"})
# A country name that is also a US state's: "Atlanta, Georgia" is the state.
_AMBIGUOUS_COUNTRY_NAMES = frozenset({"georgia"})


def _folded(name: str) -> str:
    """A name lower-cased and without accents ("São Paulo" -> "sao paulo")."""
    decomposed = unicodedata.normalize("NFKD", name.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


_REGION_CODE_BY_NAME = {
    name.lower(): code.lower() for code, name in {**_US_STATES, **_CA_PROVINCES}.items()
}


class _Region(NamedTuple):
    code: str
    countries: frozenset[str]
    """The countries the part can be a region of."""


def _region_of(part: str) -> _Region | None:
    """The US state or Canadian province a part is, written as its two-letter code ("TX") or as
    its name ("Texas"), or None. "CA" is California, or Canada written as its ISO code; the name
    "California" is only the state."""
    if len(part) == 2 and part.isascii():
        upper = part.upper()
        if upper == "CA":
            return _Region("ca", frozenset({"US", "CA"}))
        if upper in _US_STATES:
            return _Region(upper.lower(), frozenset({"US"}))
        if upper in _CA_PROVINCES:
            return _Region(upper.lower(), frozenset({"CA"}))
        return None
    code = _REGION_CODE_BY_NAME.get(_folded(part))
    if code is None:
        return None
    return _Region(code, frozenset({"US"}) if code.upper() in _US_STATES else frozenset({"CA"}))


def _has_number(part: str) -> bool:
    """Any character Unicode classes as a number. `\\d` is only the decimal digits, and a
    superscript ZIP code or a roman numeral would pass as letters. (An ideograph that merely carries
    a numeric value is a letter to Unicode, and stays one.)"""
    return any(unicodedata.category(ch).startswith("N") for ch in part)


def _parts(text: str | None) -> list[str] | None:
    """The comma-separated parts of `text` once the work modes are out and every parenthesis reads
    as a comma, or None when anything in it is not a plain name: that is the whole closed
    grammar. Whitespace of every kind is one space, accents are composed."""
    if not text:
        return None
    text = " ".join(unicodedata.normalize("NFC", text).split())
    text = _WORK_MODE_RX.sub(" ", text.replace("(", ",").replace(")", ","))
    parts: list[str] = []
    for raw in text.split(","):
        part = raw.strip(_EDGE_CHARS)
        if not part:
            continue
        # "OR" alone is Oregon's code; the word, in any other part, joins places: refuse
        if (
            not _NAME_RX.match(part)
            or _has_number(part)
            or (part != "OR" and _CONJUNCTION_RX.search(part))
        ):
            return None
        parts.append(part)
    return parts


def _country_of(gazetteer: Gazetteer, part: str, *, last_of_several: bool) -> str | None:
    """The country a part is the name of, or None. The bare upper-case US is the country (the
    lower-case word is not); a name for part of a country counts only as the last of several."""
    key = part.lower()
    if key in _AMBIGUOUS_COUNTRY_NAMES:
        return None
    if key in _SUBNATIONAL_ALIASES and not last_of_several:
        return None
    return gazetteer.country_alias_to_code.get(key) or ("US" if part == "US" else None)


def _bare_city(gazetteer: Gazetteer, key: str, region: _Region | None) -> Place | None:
    """A city, or a city and a state, with no country written: the country is the one the city's
    own name belongs to, if that is a large city and no place in another country has the name."""
    candidates = gazetteer.city_index.get(key, ())
    countries = {c.country_code for c in candidates}
    # A multi-word name also counts without its trailing "City" ("New York" is an alternate name
    # of the city the table calls "New York City"). A single word does not: that is where a
    # country's name in another language collides with a city ("Bénin" is not Benin City). An
    # accented spelling must be the city's own name exactly: only an unaccented one is read as
    # the accent-free form of it ("Cordoba" for "Córdoba", never "Còrdoba").
    names = (key, f"{key} city") if " " in key else (key,)
    own = [
        c for c in candidates if (c.name.lower() if not key.isascii() else _folded(c.name)) in names
    ]
    # a name the gazetteer lacks has no own name, so it fails the floor and never reaches the unpack
    if len(countries) > 1 or max((c.population for c in own), default=0) < LARGE_CITY_POPULATION:
        return None
    (country,) = countries
    if region is not None and country not in region.countries:
        return None
    return Place(country, _folded(key), region.code if region else None)


def _city_in_country(
    gazetteer: Gazetteer, part: str, country: str, region: _Region | None
) -> Place | None:
    """A city in a country the text names: a place the gazetteer has in that country, or an
    unknown town beside a state (the caller has checked that it belongs to the country). An unknown
    town whose name is itself a country's is not a place ("EMEA" or "US" beside a state)."""
    key = part.lower()
    candidates = gazetteer.city_index.get(key, ())
    if candidates:
        if country not in {c.country_code for c in candidates}:
            return None  # the city cannot be in the country the text names
    elif region is None or _country_of(gazetteer, part, last_of_several=True):
        return None  # nothing says that the first part is a place at all ("EMEA, United States")
    return Place(country, _folded(key), region.code if region else None)


def read_place(gazetteer: Gazetteer, text: str | None) -> Place | None:
    """The one place `text` names, with the country it establishes, or None. See the module
    docstring for what is read; everything else is None."""
    parts = _parts(text)
    if not parts:
        return None
    while len(parts) > 2 and _same_country(gazetteer, parts[-2], parts[-1]):
        # a country written again at the end counts once: the probes append a board's country to a
        # location that may already end in it ("Austin, TX, United States" + "United States")
        parts = parts[:-1]
    if len(parts) > 3:
        return None
    first = parts[0].lower()
    if len(parts) == 1:
        country = _country_of(gazetteer, parts[0], last_of_several=False)
        return Place(country) if country else _bare_city(gazetteer, first, None)
    country = _country_of(gazetteer, parts[-1], last_of_several=True)
    if country and all(
        _country_of(gazetteer, part, last_of_several=False) == country for part in parts
    ):
        # the same country written twice ("Remote (US), USA"); a name for part of a country does
        # not count ("UK, England" is not the whole of the United Kingdom)
        return Place(country)
    region = _region_of(parts[1])
    if len(parts) == 2:
        if region:
            return _bare_city(gazetteer, first, region)
        if country is None or _country_of(gazetteer, parts[0], last_of_several=False):
            # a country first is a list of countries ("Ireland, United States"), not a place; a
            # missing country would be refused below anyway, and is named here for the type checker
            return None
        return _city_in_country(gazetteer, parts[0], country, None)
    if country is None:
        return None  # nothing below reads a text with no country; named here for the type checker
    if _country_of(gazetteer, parts[1], last_of_several=True) is not None:
        return None  # two countries ("Austin, Canada, United States"): a list (the same one twice
        # was dropped above)
    if region is not None and country in region.countries:
        return _city_in_country(gazetteer, parts[0], country, region)
    if region is not None and country in {"US", "CA"}:
        return None  # "Toronto, OH, Canada": a state of the other one
    # the middle part is not read ("Bengaluru, Karnataka, India"): a place the gazetteer has in
    # that country, and the country, and nothing about which place inside it
    known = {c.country_code for c in gazetteer.city_index.get(first, ())}
    return Place(country, _folded(first), None, qualified=True) if country in known else None


def _same_country(gazetteer: Gazetteer, one: str, other: str) -> bool:
    """Whether two parts name the same country (a name for part of a country counts)."""
    country = _country_of(gazetteer, one, last_of_several=True)
    return country is not None and country == _country_of(gazetteer, other, last_of_several=True)
