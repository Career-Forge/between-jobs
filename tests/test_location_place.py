"""The location reader: one place, or nothing.

`read_place` accepts a closed grammar (see the module docstring), so these tests are mostly tables
of what is read and, as many, of what is refused; plus properties that hold for any text, because
the reader sits in front of third-party input.
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

import pytest
from place_fixtures import places_gazetteer

import between_jobs
from between_jobs.api.geo_gazetteer import Gazetteer
from between_jobs.api.location_place import (
    _CA_PROVINCES,
    _US_STATES,
    Place,
    read_place,
)

_GAZ = places_gazetteer()


_READS = [
    # a country alone, with or without how the job is worked
    ("Germany", Place("DE")),
    ("Remote - US", Place("US")),
    ("US Remote", Place("US")),
    ("Remote (US)", Place("US")),
    ("United States (Remote)", Place("US")),
    ("UK", Place("GB")),
    ("U.S.A.", Place("US")),
    ("Remote | Canada", Place("CA")),
    ("Remote: Canada", Place("CA")),
    ("Hybrid - Canada -", Place("CA")),
    ("Remote / Canada", Place("CA")),
    ("Remote; Canada", Place("CA")),
    ("Remote \u2013 Canada", Place("CA")),
    ("Remote \u2014 Canada", Place("CA")),
    ("Onsite - Canada", Place("CA")),
    ("On-Site Canada", Place("CA")),
    ("On-site, Canada", Place("CA")),
    ("Canada (in office)", Place("CA")),
    ("Canada (In-Office)", Place("CA")),
    ("WFH Canada", Place("CA")),
    ("Remote (US), USA", Place("US")),
    ("UAE, United Arab Emirates", Place("AE")),
    # a city, bare, with a state as a code or a name, or with a country
    ("New York", Place("US", "new york")),
    ("New York, NY", Place("US", "new york", "ny")),
    ("Austin, TX", Place("US", "austin", "tx")),
    ("Austin, tx", Place("US", "austin", "tx")),
    ("Austin, Texas", Place("US", "austin", "tx")),
    ("Austin, New  Hampshire", Place("US", "austin", "nh")),
    ("Austin, \uff34\uff25\uff38\uff21\uff33", Place("US", "austin", "tx")),
    ("Atlanta, Georgia", Place("US", "atlanta", "ga")),
    ("Atlanta, Georgia, United States", Place("US", "atlanta", "ga")),
    ("London, Wales", Place("GB", "london")),
    ("Amsterdam, Holland", Place("NL", "amsterdam")),
    ("Toronto, Québec", Place("CA", "toronto", "qc")),
    ("Toronto, CA", Place("CA", "toronto", "ca")),
    ("Cambridge, United Kingdom", Place("GB", "cambridge")),
    ("London, England", Place("GB", "london")),
    ("Dubai, United Arab Emirates", Place("AE", "dubai")),
    ("Austin (TX)", Place("US", "austin", "tx")),
    ("Austin (Texas)", Place("US", "austin", "tx")),
    ("Austin ,TX", Place("US", "austin", "tx")),
    ("Remote - Austin, TX", Place("US", "austin", "tx")),
    ("Austin, TX - Remote", Place("US", "austin", "tx")),
    ("Hybrid (Austin, TX)", Place("US", "austin", "tx")),
    ("Austin, Remote, TX", Place("US", "austin", "tx")),
    # Oregon's code is a part like any other
    ("Dallas, OR", Place("US", "dallas", "or")),
    ("Dallas, OR (Remote)", Place("US", "dallas", "or")),
    ("Dallas, OR - Remote", Place("US", "dallas", "or")),
    ("Portland, OR | Hybrid", Place("US", "portland", "or")),
    # a city, a state and a country; an unknown town counts beside a state
    ("Austin, TX, United States", Place("US", "austin", "tx")),
    ("Austin, Texas, USA", Place("US", "austin", "tx")),
    ("Smalltown, TX, United States", Place("US", "smalltown", "tx")),
    ("Cambridge, MA, United States", Place("US", "cambridge", "ma")),
    # a work-mode word is a word: it is taken out of a name only as a whole word
    ("Remotely, TX, United States", Place("US", "remotely", "tx")),
    ("Premote, TX, United States", Place("US", "premote", "tx")),
    # a country that is written twice at the end adds nothing: the Ashby and Workday probes
    # append the board's country to a location that may already end in it
    ("London, UK, United Kingdom", Place("GB", "london")),
    ("London, England, United Kingdom", Place("GB", "london")),
    ("London, United Kingdom, United Kingdom", Place("GB", "london")),
    ("Austin, TX, United States, United States", Place("US", "austin", "tx")),
    ("Austin, Texas, USA, United States of America", Place("US", "austin", "tx")),
    ("Toronto, ON, Canada, Canada", Place("CA", "toronto", "on")),
    ("Remote - Austin, TX, United States, United States", Place("US", "austin", "tx")),
    ("Portland, Downtown, United States, USA", Place("US", "portland", None, qualified=True)),
    ("Cambridge, Cambridgeshire, England, United Kingdom", Place("GB", "cambridge", None, True)),
    ("US, US, USA", Place("US")),
    ("London, England, United Kingdom, United Kingdom", Place("GB", "london")),
    ("Dubai, Dubai, United Arab Emirates, United Arab Emirates", Place("AE", "dubai")),
    ("Austin, TX, USA, USA, USA", Place("US", "austin", "tx")),
    # a part in between that is not read: the country is established, the place is not
    ("Portland, Downtown, United States", Place("US", "portland", None, qualified=True)),
    ("Singapore, South West, Singapore", Place("SG", "singapore", None, qualified=True)),
    ("Recife, PE, Brazil", Place("BR", "recife", None, qualified=True)),
    ("S\u00e3o Paulo, SP, Brazil", Place("BR", "sao paulo", None, qualified=True)),
    ("Chennai, TN, India", Place("IN", "chennai", None, qualified=True)),
    ("Austin, PK, United States", Place("US", "austin", None, qualified=True)),
    # accents: an unaccented spelling is the accent-free form of the city's name, an accented one
    # is the name exactly
    ("Sao Paulo", Place("BR", "sao paulo")),
    ("S\u00e3o Paulo", Place("BR", "sao paulo")),
    ("Malaga", Place("ES", "malaga")),
    ("M\u00e1laga", Place("ES", "malaga")),
    ("Bogota", Place("CO", "bogota")),
    # a decomposed accent (an "e" and a combining acute) is the same text as a composed one
    ("Toronto, Que\u0301bec", Place("CA", "toronto", "qc")),
    ("Toronto, Qu\u00e9bec", Place("CA", "toronto", "qc")),
    # a city's own name, and a multi-word name without its trailing "City"
    ("Benin City", Place("NG", "benin city")),
    ("Dubai", Place("AE", "dubai")),
]


@pytest.mark.parametrize(("text", "expected"), _READS)
def test_read_place_reads(text: str, expected: Place) -> None:
    assert read_place(_GAZ, text) == expected


_REFUSES = [
    # nothing, or nothing but how the job is worked, or worldwide
    None,
    "",
    "   ",
    "Remote",
    "Hybrid / Remote",
    "Anywhere in Europe",
    "Worldwide",
    "Global HQ, Austin, TX, United States",
    "Austin, TX, United States (anywhere)",
    # a name that is not the place's own, a code, a placeholder, a region
    "Peking",
    "Bombay",
    "BOM",
    "NYC",
    "APAC",
    "TBD",
    "EMEA",
    "Remote - Ontario",
    "Remote - EMEA, United States",
    "Europe, United States",
    "North America, United States",
    "Remote - DACH, Germany",
    "Texas",
    "England",
    "Scotland",
    "Wales",
    "Holland",
    "us",
    "Remote - us",
    "On site Canada",
    # a name more than one country has, or a small town, or one the table lacks
    "Cambridge, MA",
    "Boston",
    "Boston, MA",
    "London",
    "Valencia",
    "Greenwich, CT",
    "Greenwich",
    "Smallville",
    "Smalltown, United States",
    "Nearville",
    "Vancouver, WA",
    # a state that is not the city's country's, or a code that is not a state
    "Toronto, OH",
    "Toronto, OH, Canada",
    "Toronto, Texas",
    "Austin, ON",
    "Austin, TX, Canada",
    "Cairo, IL",
    "Hyderabad, PK",
    "Bogota, VE",
    "Austin, PK",
    "Sydney, NSW",
    "Tbilisi, Georgia",
    "Austin, Ireland",
    "Cambridge, Canada, United Kingdom",
    "Toronto, Canada, USA",
    "Austin, Canada, United States",
    # a country that starts the text is a city, not the country
    "US, Texas",
    "US, Texas, USA",
    "United States, Austin, TX, United States",
    "UK, England",
    "UK (Scotland)",
    "United Kingdom, England",
    "Jamaica, NY",
    "Jamaica (NY)",
    "US, Berlin",
    "UK Remote, London",
    "Remote - US (East)",
    "US Remote (EST)",
    # one case for each rule that refuses a text, where one rule can be told from another: a digit
    # of any script (at the end of the first part, and in the middle of the text), a fourth part
    # that is not a repeated country, an empty name, an underscore, a part that is neither a name
    # nor a state, a conjunction, a province before the United States, a country as an unknown
    # town, an accent that is not the city's own, a letter that only upper-cases to a state code
    "Smalltown 2, TX, United States",
    "Smalltown \u2077\u2078\u2077\u2070\u00b9, TX, United States",
    "Smalltown\u00b2, TX, United States",
    "Portland, Downtown\u00b2, United States",
    "Smalltown \u2163x, TX, United States",
    "Smalltown \u2163, TX, United States",
    "Austin, TX, Foo, United States",
    "Austin, ., United States",
    "Smalltown_TX, TX, United States",
    "EMEA, North, United States",
    "Smalltown OR, TX, United States",
    "Dallas, Or",
    "Dallas, or",
    "Austin, ON, United States",
    "Evans, NL, United States",
    "England, TX, United States",
    "Mal\u00e1ga, Spain",
    "Austin, \ufb02",
    "Austin, \u0131d",
    # a state that is not the country's: the NAME "California" is the state, never "CA" the
    # code of Canada
    "Toronto, California",
    "Toronto, California, Canada",
    # a sub-national name in the middle is a second country, not a region
    "Austin, England, United States",
    "Austin, Dubai, US",
    # a separator character is stripped from the ends of a part, an apostrophe is not
    "Austin', TX",
    # two countries are a list, in either order, whatever a table also calls a place
    "Jamaica, United States",
    "Ireland, United States of America",
    "India, Serbia",
    "Laos, Vietnam",
    "Chile, Turkey",
    # a work mode between two words of a name does not join them
    "New Remote York",
    "Austin Remote TX",
    "United Remote States",
    "San Remote Francisco, CA, United States",
    "Sao Remote Paulo",
    "Aus Remote tin, TX, United States",
    # a list, in any of its separators, or a conjunction
    "Austin, TX / Dallas, TX",
    "Austin, TX | Dallas, TX",
    "Austin, TX; Dallas, TX",
    "Austin, TX & Dallas, TX",
    "Austin, TX + Dallas, TX",
    "Austin, TX \\ Dallas, TX",
    "Austin, TX \u2022 Dallas, TX",
    "Austin, TX \u00b7 Dallas, TX",
    "Austin, TX : Dallas, TX",
    "Austin, TX - Dallas, TX",
    "Austin, TX \u2013 Dallas, TX",
    "Austin, TX \u2014 Dallas, TX",
    "Austin, TX or Dallas, TX",
    "Austin, TX OR Dallas, TX",
    "Austin, TX And Dallas, TX",
    "Austin, TX, or Dallas, TX",
    "Austin or Dallas",
    "Austin, TX or Remote",
    "Seattle OR Remote",
    "SEATTLE OR REMOTE",
    "Dallas OR (Remote)",
    "Lebanon OR (Remote)",
    "Toronto, Canada (OR United States)",
    "Canada (or United States)",
    "Germany (or Austria)",
    "Canada - OR, US",
    "Remote - UK or Ireland",
    "USA, Canada",
    "US + Canada",
    "US-Canada",
    "US & Canada",
    "Trinidad and Tobago",
    # a country code beside other words, in any script
    "London UK Remote",
    "UK Remote USA",
    "US Virgin Islands",
    "US 東京",
    "Москва US",
    # a digit, a ZIP code, a street, other punctuation, a fifth part
    "Austin, TX 78701",
    "Austin, TX, 78701, United States",
    "1 Market St, San Francisco, CA 94105, United States",
    "Dublin 2, Ireland",
    "Austin TX",
    "Austin, TX, USA, Earth",
    "Austin, TX, Foo, United States, Bar",
    "Austin, TX, Foo, Bar",
    "Austin! TX",
    "Austin, TX?",
    "Austin, TX #1",
    "Austin, [TX]",
    "Austin; TX",
    "Austin, \u00a4TX",
    "Austin,\u0000 TX",
    "Austin\u202e, TX",
    "Austin, T\ud800X",
    "Austin - TX",
    "Austin -TX- Texas",
]


@pytest.mark.parametrize("text", _REFUSES)
def test_read_place_refuses(text: str | None) -> None:
    assert read_place(_GAZ, text) is None


def test_read_place_reads_a_plain_name_with_punctuation_only_between_letters() -> None:
    gaz = places_gazetteer()
    assert read_place(gaz, "Smalltown-on-Sea, TX, United States") == Place(
        "US", "smalltown-on-sea", "tx"
    )
    assert read_place(gaz, "St. Smalltown, TX, United States") == Place("US", "st. smalltown", "tx")
    assert read_place(gaz, "O'Fallon, TX, United States") == Place("US", "o'fallon", "tx")
    assert read_place(gaz, "Smalltown -, TX, United States") == Place("US", "smalltown", "tx")
    assert read_place(gaz, "O\u2019Fallon, TX, United States") == Place("US", "o\u2019fallon", "tx")
    assert read_place(gaz, "Small - town, TX, United States") is None
    assert read_place(gaz, "Small -town, TX, United States") is None
    assert read_place(gaz, "Small- town, TX, United States") is None
    assert read_place(gaz, "-Smalltown-, TX, United States") == Place("US", "smalltown", "tx")


_US_STATE_NAMES = {
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
_CA_PROVINCE_NAMES = {
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


def test_the_region_tables_are_exactly_the_states_dc_and_the_provinces() -> None:
    """Spelled out here, not read back from the module: a typo in a code or a name must fail."""
    assert _US_STATES == _US_STATE_NAMES
    assert _CA_PROVINCES == _CA_PROVINCE_NAMES


@pytest.mark.parametrize(("code", "name"), sorted(_US_STATE_NAMES.items()))
def test_a_us_state_is_the_region_of_a_us_city(code: str, name: str) -> None:
    assert read_place(_GAZ, f"Austin, {code}") == Place("US", "austin", code.lower())
    assert read_place(_GAZ, f"Austin, {name}") == Place("US", "austin", code.lower())
    expected_in_canada = Place("CA", "toronto", "ca") if code == "CA" else None
    assert read_place(_GAZ, f"Toronto, {code}") == expected_in_canada


@pytest.mark.parametrize(("code", "name"), sorted(_CA_PROVINCE_NAMES.items()))
def test_a_canadian_province_is_the_region_of_a_canadian_city(code: str, name: str) -> None:
    assert read_place(_GAZ, f"Toronto, {code}") == Place("CA", "toronto", code.lower())
    if " and " not in name:  # a part with the word "and" in it is refused
        assert read_place(_GAZ, f"Toronto, {name}") == Place("CA", "toronto", code.lower())
    assert read_place(_GAZ, f"Austin, {code}") is None


@pytest.mark.parametrize("code", ["PK", "VE", "XX", "GB", "JP", "TW"])
def test_no_other_two_letter_code_is_a_region(code: str) -> None:
    assert read_place(_GAZ, f"Austin, {code}") is None
    assert read_place(_GAZ, f"Toronto, {code}") is None


# -- properties: they hold for any text, and the text is third-party input

_PIECES = [
    "US", "USA", "UK", "UK Remote", "Remote UK", "United States", "United Kingdom", "Canada",
    "Germany", "Austria", "England", "Scotland", "Dubai", "Abu Dhabi", "Austin", "New York",
    "Toronto", "Cambridge", "Lebanon", "Jamaica", "Georgia", "Czech Republic", "Seattle", "WA",
    "TX", "OH", "ON", "OR", "CA", "Texas", "Oregon", "Ontario", "EMEA", "Europe", "Remote",
    "Hybrid", "Anywhere", "or", "and", "OR", "AND", "-", "\u2013", "+", "&", "/", ";", "|", ":",
    "\\", "\u2022", "(", ")", ",", ", ", " ", "  ", "", "東京", "München", "1",
    "\u00a0", "\ud800", "\u202e", "\x00", "́", "_", ".", "'", "Or", "oR", "\u00b2", "\u2163",
    "Smalltown", "Portland", "Downtown", "North",
]  # fmt: skip


def _random_texts(count: int, seed: int = 20260607) -> list[str]:
    rng = random.Random(seed)
    joiners = ["", " ", ", ", "  "]
    return [
        "".join(rng.choice(_PIECES) + rng.choice(joiners) for _ in range(rng.randint(1, 9)))
        for _ in range(count)
    ]


def test_read_place_never_raises() -> None:
    gazetteers = [_GAZ, Gazetteer(city_index={}, country_alias_to_code={}, country_names={})]
    for text in _random_texts(60000):
        for gaz in gazetteers:
            read_place(gaz, text)


def test_what_is_read_is_made_of_plain_names_only() -> None:
    """A place is read from a text only if the text, with the work modes and the parentheses and
    commas taken out, is letters, spaces, hyphens, apostrophes and periods: this is checked with
    a character class, not with the reader's own regular expression."""
    plain = re.compile(r"^[^\W\d_ '\u2019.\-]*$")  # what a name is made of, spaces and joins out
    read = 0
    for text in _random_texts(60000):
        if read_place(_GAZ, text) is None:
            continue
        read += 1
        worked = re.sub(
            r"(?i)\b(remote|hybrid|on-?site|onsite|in[- ]office|wfh)\b",
            " ",
            unicodedata.normalize("NFC", text),
        )
        residue = re.sub(r"[\s,()\-\u2013\u2014|/;:]", "", worked)
        residue = re.sub(r"['\u2019.]", "", residue)
        assert plain.match(residue), text
        assert not any(unicodedata.category(ch).startswith("N") for ch in residue), text
        # the words that join places are gone from every part, except Oregon's code standing alone
        for part in re.split(r"[,()]", worked):
            alone = re.sub(r"^[\s;|/:\-\u2013\u2014]+|[\s;|/:\-\u2013\u2014]+$", "", part)
            assert alone == "OR" or not re.search(r"(?i)\b(and|or)\b", alone), text
    assert read > 2000  # the generator reaches places (about 2,650 of 60,000 are read)


_VALID = [
    "Austin, TX",
    "Austin, Texas, United States",
    "New York",
    "Remote - US",
    "London, England",
    "Portland, Downtown, United States",
    "Dubai",
]


@pytest.mark.parametrize("text", _VALID)
def test_how_the_job_is_worked_changes_nothing(text: str) -> None:
    place = read_place(_GAZ, text)
    assert place is not None
    for dressed in [
        f"Remote - {text}",
        f"{text} - Remote",
        f"{text} (Remote)",
        f"Hybrid: {text}",
        f"{text} | On-site",
        f"({text})",
    ]:
        assert read_place(_GAZ, dressed) == place, dressed


@pytest.mark.parametrize("text", _VALID)
def test_anything_that_is_not_a_plain_name_makes_a_place_unreadable(text: str) -> None:
    for spoiled in [
        f"{text} 1",
        f"{text} or Dallas, TX",
        f"{text} & Dallas, TX",
        f"{text} / Dallas, TX",
        f"{text} + Dallas, TX",
        f"{text}; Dallas, TX",
        f"{text}, Foo, Bar, Baz",
        f"{text} (anywhere)",
        f"{text} !",
        f"{text}\u0000",
        f"{text} 東京",
    ]:
        assert read_place(_GAZ, spoiled) is None, spoiled


_DIGEST_SCRIPT = """
import hashlib, json, sys
from place_fixtures import places_gazetteer
from between_jobs.api.location_place import read_place

gazetteer = places_gazetteer()
digest = hashlib.sha256()
read = 0
for text in json.load(sys.stdin):
    place = read_place(gazetteer, text)
    read += place is not None
    digest.update(repr(place).encode())
print(digest.hexdigest(), read)
"""


def test_a_text_is_read_the_same_whatever_hash_seed_the_process_has() -> None:
    """Nothing is picked from a set. One process has one hash seed, so the same texts are read in
    fresh processes with different seeds and what each one read is compared."""
    texts = [t for t, _ in _READS if t] + [t for t in _REFUSES if t] + _random_texts(3000, seed=1)
    package_root = Path(between_jobs.__file__).resolve().parent.parent
    path = os.pathsep.join([str(package_root), str(Path(__file__).resolve().parent)])
    results = set()
    for seed in ("0", "1", "2", "3"):
        done = subprocess.run(
            [sys.executable, "-c", _DIGEST_SCRIPT],
            input=json.dumps(texts),
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": path},
        )
        results.add(done.stdout.strip())
    assert len(results) == 1, results
    assert int(next(iter(results)).split()[1]) > 100  # the texts do reach places


@pytest.mark.parametrize(
    "text",
    [
        "a" + "-" * 50_000 + "a",
        "a" + ";" * 50_000 + "b",
        "a" + " -" * 25_000 + "b",
        "a" + "/" * 50_000 + "a",
        "a. " * 20_000 + "b",
        "," * 50_000,
        "-".join(["ab"] * 20_000),
    ],
)
def test_read_place_is_linear_in_the_length_of_a_text(text: str) -> None:
    """The callers cap a text at 200 characters, but the reader does not rely on it: a run of
    separators once took seconds at this length (a regular expression that strips the ends of a
    part backtracked over it) and now takes milliseconds. The bound is a hundred times what it
    takes, and several times less than what the slow version took."""
    started = time.perf_counter()
    assert read_place(_GAZ, text) is None
    assert time.perf_counter() - started < 1.0
