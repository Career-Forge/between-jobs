"""A small gazetteer shaped like the real table, shared by the tests of the location reader and of
the second location pass."""

from __future__ import annotations

from typing import Any

from between_jobs.api.geo_gazetteer import Gazetteer, build_gazetteer


def places_gazetteer() -> Gazetteer:
    """A small gazetteer built to contain the kinds of rows real data has: names shared by several
    countries, small and large (Cambridge; Valencia, large in both), alternate names that point at
    a place in another country (Augusta is also an alias of the German Augsburg; Ireland of West
    Springfield; USA of Concord), alternate spellings and codes of a large city (Bombay, BJS), a US
    town the table does not have beside its English namesake (Greenwich) and a Canadian city that
    has no US namesake here (Vancouver; the real table also has Vancouver, Washington, so this
    table is deliberately the incomplete kind the reader must survive), small towns whose names are
    region words or placeholders (Wa, Apac), a US borough named like a country (Jamaica), names
    used in one country only, with one or several rows (Austin, Dallas, Portland), and cities at
    the population floor. New York is named the way the real table names it: "New York City", with
    "New York" as an alternate name. Rows are in the population-descending order the real query
    returns."""

    def row(
        name: str, country_code: str, population: int, *alt: str, ascii_name: str | None = None
    ) -> dict[str, Any]:
        return {
            "name": name,
            "ascii_name": ascii_name or name,
            "alt_names": list(alt),
            "country_code": country_code,
            "population": population,
        }

    rows = [
        row("Beijing", "CN", 21893095, "Peking", "BJS"),
        row("Mumbai", "IN", 12691836, "Bombay", "BOM"),
        row("Benin City", "NG", 1782000, "B\u00e9nin", "Benin"),
        row("Berlin", "DE", 3645000),
        row("Dubai", "AE", 3331420),
        row("Singapore", "SG", 5453600),
        row("Abu Dhabi", "AE", 1483000),
        row("M\u00e1laga", "ES", 578460, "M\u00e0laga", ascii_name="Malaga"),
        row("Cebu City", "PH", 965332, "Cebu"),
        row("S\u00e3o Paulo", "BR", 12325232, ascii_name="Sao Paulo"),
        row("Cairo", "EG", 9539673),
        row("London", "GB", 8961989),
        row("London", "CA", 422324),
        row("New York City", "US", 8804190, "New York", "NYC"),
        row("Bogota", "CO", 7181469),
        row("Sydney", "AU", 5312163),
        row("Amsterdam", "NL", 872680),
        row("Chennai", "IN", 7088000),
        row("Recife", "BR", 1653461),
        row("Pune", "IN", 3124458),
        row("Toronto", "CA", 2794356),
        row("Valencia", "VE", 1385000),
        row("Dallas", "US", 1304379),
        row("Tbilisi", "GE", 1118000),
        row("Dublin", "IE", 1024027),
        row("Austin", "US", 974447),
        row("Valencia", "ES", 792000),
        row("Seattle", "US", 780995),
        row("Boston", "US", 653833),
        row("Portland", "US", 652503),
        row("Vancouver", "CA", 631486),
        row("Atlanta", "US", 510823),
        row("Exactville", "US", 500000),
        row("Nearville", "US", 499999),
        row("Augsburg", "DE", 296582, "Augusta"),
        row("Augusta", "US", 195844),
        row("Cambridge", "GB", 145674),
        row("Concord", "US", 129000, "USA"),
        row("Cambridge", "US", 118927),
        row("Wa", "GH", 107000),
        row("Greenwich", "GB", 100000),
        row("Apac", "UG", 70000, "APAC"),
        row("Portland", "US", 68408),
        row("Boston", "GB", 45339),
        row("Austin", "US", 29677),
        row("West Springfield", "US", 28000, "Ireland"),
        row("Smallville", "US", 20000),
        row("Springdale", "US", 30000, "India"),
        row("Jamaica", "US", 216866),
        row("Dallas", "US", 15000),
    ]
    return build_gazetteer(sorted(rows, key=lambda r: -r["population"]))
