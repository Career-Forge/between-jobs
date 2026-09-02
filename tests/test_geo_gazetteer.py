"""Tests for the gazetteer-backed 3-state location filter (Job Finder
P5d, live-search-track.md). A faithful port of n8n's real
`resolveLocation`/`checkLocationState` -- fixtures use a small synthetic
city set (not the real 34k-city import) except where a test explicitly
exercises the real bundled country-alias data file."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from between_jobs.api.geo_gazetteer import (
    Gazetteer,
    _load_country_aliases,
    build_gazetteer,
    check_location_state,
    get_gazetteer,
    resolve_location,
)

# City rows MUST be fed to build_gazetteer in population-descending order --
# the same contract the real DB query (`order by population desc`) upholds.
_CITY_ROWS: list[dict[str, Any]] = [
    {
        "name": "Paris",
        "ascii_name": "Paris",
        "alt_names": ["City of Light"],
        "country_code": "FR",
        "population": 2100000,
    },
    {
        "name": "New York",
        "ascii_name": "New York",
        "alt_names": ["NYC", "New York City"],
        "country_code": "US",
        "population": 8000000,
    },
    {
        "name": "Bengaluru",
        "ascii_name": "Bengaluru",
        "alt_names": ["Bangalore"],
        "country_code": "IN",
        "population": 8400000,
    },
    {
        "name": "Berlin",
        "ascii_name": "Berlin",
        "alt_names": [],
        "country_code": "DE",
        "population": 3600000,
    },
    {
        "name": "Paris",
        "ascii_name": "Paris",
        "alt_names": [],
        "country_code": "US",  # Paris, Texas -- deliberately lower population than Paris, FR
        "population": 25000,
    },
]


def _gazetteer() -> Gazetteer:
    return build_gazetteer(_CITY_ROWS)


# ── real bundled country-alias data ──────────────────────────────────────


def test_load_country_aliases_resolves_real_data() -> None:
    alias_to_code, country_names = _load_country_aliases()
    assert alias_to_code["usa"] == "US"
    assert alias_to_code["america"] == "US"
    assert alias_to_code["india"] == "IN"
    assert alias_to_code["uk"] == "GB"
    assert country_names["US"] == "United States"


# ── build_gazetteer ───────────────────────────────────────────────────────


def test_build_gazetteer_indexes_name_ascii_and_alt_names() -> None:
    g = _gazetteer()
    assert "new york" in g.city_index
    assert "nyc" in g.city_index
    assert "bangalore" in g.city_index  # alt name resolves too


def test_build_gazetteer_disambiguates_by_population_when_no_country_hint() -> None:
    g = _gazetteer()
    # Two cities named "Paris" (FR pop 2.1M, US/TX pop 25k) -- index 0 must
    # be the bigger one, matching the real gazetteer's own documented
    # "sorted by population descending" disambiguation.
    candidates = g.city_index["paris"]
    assert len(candidates) == 2
    assert candidates[0].country_code == "FR"


# ── resolve_location ──────────────────────────────────────────────────────


def test_resolve_location_empty_string_is_unresolved() -> None:
    result = resolve_location(_gazetteer(), "")
    assert result.unresolved is True
    assert result.countries == frozenset()


def test_resolve_location_none_is_unresolved() -> None:
    result = resolve_location(_gazetteer(), None)
    assert result.unresolved is True


def test_resolve_location_plain_city_resolves_city_and_country() -> None:
    result = resolve_location(_gazetteer(), "New York")
    assert result.cities == frozenset({"New York"})
    assert result.countries == frozenset({"US"})
    assert result.unresolved is False


def test_resolve_location_city_comma_country_disambiguates() -> None:
    # "Paris, USA" must resolve to Paris, Texas -- not the bigger Paris, FR --
    # because the country hint on the SAME segment picks the same-country
    # match over the population default. Real, confirmed behavior caught
    # writing this test: a bare "US" is NOT a recognized alias in the real
    # bundled data (only "usa"/"u.s."/"america"/etc. are) -- avoids the
    # exact false-positive collision (the common word "us") the
    # CODE_REMOTE_RX comment already names for the *remote*-adjacency
    # check, applying the same caution to plain country matching too.
    result = resolve_location(_gazetteer(), "Paris, USA")
    assert result.countries == frozenset({"US"})


def test_resolve_location_country_alias_resolves() -> None:
    result = resolve_location(_gazetteer(), "Germany")
    assert result.countries == frozenset({"DE"})


def test_resolve_location_remote_keyword_detected() -> None:
    result = resolve_location(_gazetteer(), "Remote")
    assert result.remote is True
    # A bare "Remote" with no city/country resolves nothing else, but is
    # NOT unresolved since remote=True short-circuits that.
    assert result.unresolved is False


def test_resolve_location_global_keyword_detected() -> None:
    result = resolve_location(_gazetteer(), "Worldwide")
    assert result.is_global is True


def test_resolve_location_code_remote_pattern() -> None:
    # "US Remote" -- the real s96 bug this whole gazetteer upgrade exists
    # to fix: a bare "US" is never a recognized country alias on its own
    # (dodges false positives), but "<CODE> Remote" adjacency is a real,
    # narrow signal.
    result = resolve_location(_gazetteer(), "US Remote")
    assert "US" in result.countries
    assert result.remote is True


def test_resolve_location_uk_extra_code_maps_to_gb() -> None:
    result = resolve_location(_gazetteer(), "UK Remote")
    assert "GB" in result.countries


def test_resolve_location_gibberish_is_unresolved() -> None:
    result = resolve_location(_gazetteer(), "Zzyzxville Nowhereland")
    assert result.unresolved is True


def test_resolve_location_semicolon_separated_segments() -> None:
    result = resolve_location(_gazetteer(), "New York; Berlin")
    assert result.cities == frozenset({"New York", "Berlin"})
    assert result.countries == frozenset({"US", "DE"})


# ── check_location_state ─────────────────────────────────────────────────


def test_check_location_state_empty_job_location_is_unknown() -> None:
    g = _gazetteer()
    assert check_location_state(g, "", request_country="US") == "unknown"
    assert check_location_state(g, None, request_country="US") == "unknown"
    assert check_location_state(g, "unknown", request_country="US") == "unknown"


def test_check_location_state_matches_request_country() -> None:
    g = _gazetteer()
    assert check_location_state(g, "New York, NY", request_country="US") == "match"


def test_check_location_state_matches_request_city() -> None:
    g = _gazetteer()
    state = check_location_state(g, "Bengaluru, India", request_cities=frozenset({"bengaluru"}))
    assert state == "match"


def test_check_location_state_mismatches_a_different_country() -> None:
    g = _gazetteer()
    assert check_location_state(g, "Berlin, Germany", request_country="US") == "mismatch"


def test_check_location_state_global_always_matches() -> None:
    g = _gazetteer()
    assert check_location_state(g, "Worldwide", request_country="US") == "match"


def test_check_location_state_us_remote_mismatches_non_us_request() -> None:
    """The real s96 bug fix: "US Remote" is remote WITHIN the US, not
    remote everywhere -- must mismatch a request for a different
    country, even though the job is genuinely remote."""
    g = _gazetteer()
    assert check_location_state(g, "US Remote", request_country="DE") == "mismatch"


def test_check_location_state_bare_remote_with_no_country_is_unknown() -> None:
    g = _gazetteer()
    assert check_location_state(g, "Remote", request_country="US") == "unknown"


def test_check_location_state_unresolvable_job_location_is_unknown() -> None:
    g = _gazetteer()
    assert check_location_state(g, "Zzyzxville", request_country="US") == "unknown"


# ── get_gazetteer (fail-open caching) ────────────────────────────────────


class _FakeQueryBuilder:
    """Simulates real PostgREST `.range()` pagination -- not just a
    single unbounded return -- so a test using a fixture bigger than one
    page can actually catch the real silent-truncation bug this module
    already hit once (a plain unranged select against the real 34,006-row
    table returned exactly 1,000 rows, confirmed live)."""

    def __init__(self, rows: list[dict[str, Any]], *, raise_error: bool = False) -> None:
        self._rows = rows
        self._raise_error = raise_error
        self._start = 0
        self._end = len(rows) - 1

    def select(self, *_: Any, **__: Any) -> _FakeQueryBuilder:
        return self

    def order(self, *_: Any, **__: Any) -> _FakeQueryBuilder:
        return self

    def range(self, start: int, end: int) -> _FakeQueryBuilder:
        self._start = start
        self._end = end
        return self

    async def execute(self) -> SimpleNamespace:
        if self._raise_error:
            raise RuntimeError("connection refused")
        return SimpleNamespace(data=self._rows[self._start : self._end + 1])


class _FakeSupabase:
    def __init__(self, rows: list[dict[str, Any]], *, raise_error: bool = False) -> None:
        self._rows = rows
        self._raise_error = raise_error
        self.calls = 0

    def table(self, name: str) -> _FakeQueryBuilder:
        self.calls += 1
        return _FakeQueryBuilder(self._rows, raise_error=self._raise_error)


async def test_get_gazetteer_builds_from_real_rows() -> None:
    import between_jobs.api.geo_gazetteer as geo_module

    geo_module._cached_gazetteer = None
    supabase = _FakeSupabase(_CITY_ROWS)

    g = await get_gazetteer(supabase)  # type: ignore[arg-type]

    assert "new york" in g.city_index
    geo_module._cached_gazetteer = None  # don't leak cache into other tests


async def test_get_gazetteer_fails_open_on_error() -> None:
    import between_jobs.api.geo_gazetteer as geo_module

    geo_module._cached_gazetteer = None
    supabase = _FakeSupabase([], raise_error=True)

    g = await get_gazetteer(supabase)  # type: ignore[arg-type]

    assert g.city_index == {}
    geo_module._cached_gazetteer = None


async def test_get_gazetteer_pages_past_the_first_1000_rows() -> None:
    """Regression test for a real bug this module hit on its own first
    live verification: a plain unranged `.select()` against the real
    34,006-row table returned exactly 1,000 rows (PostgREST's own
    default cap), the identical bug class `import_job_registry_seed.py`
    already found and fixed for a different table. A fixture bigger than
    one page is what actually catches this -- every other test here uses
    a 5-row fixture that couldn't have."""
    import between_jobs.api.geo_gazetteer as geo_module

    geo_module._cached_gazetteer = None
    big_rows = [
        {
            "name": f"City{i}",
            "ascii_name": f"City{i}",
            "alt_names": [],
            "country_code": "US",
            "population": 1_500_000 - i,  # keep it population-descending
        }
        for i in range(1500)
    ]
    supabase = _FakeSupabase(big_rows)

    g = await get_gazetteer(supabase)  # type: ignore[arg-type]

    assert "city0" in g.city_index
    assert "city999" in g.city_index
    assert "city1499" in g.city_index  # past the first page -- the actual regression check
    geo_module._cached_gazetteer = None


async def test_get_gazetteer_caches_after_first_call() -> None:
    import between_jobs.api.geo_gazetteer as geo_module

    geo_module._cached_gazetteer = None
    supabase = _FakeSupabase(_CITY_ROWS)

    await get_gazetteer(supabase)  # type: ignore[arg-type]
    await get_gazetteer(supabase)  # type: ignore[arg-type]

    assert supabase.calls == 1
    geo_module._cached_gazetteer = None
