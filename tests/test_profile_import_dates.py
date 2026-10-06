"""Date equivalence for the resume importer (api/profile_import_dates.py): is a date the model
proposes really in the document, in whatever format the document wrote it?

The table below is the contract. The rule behind every "None" is the same: a date the text does
not contain is never accepted.
"""

from __future__ import annotations

import re

import pytest

from between_jobs.api.profile_import_dates import (
    DateIndex,
    DateMention,
    extract_mentions,
    ground_date,
    parse_single_date,
)


def _ground(
    text: str,
    value: str,
    role: str = "start",
    *,
    require_month: bool = True,
    near: int | None = None,
) -> tuple[str, bool] | None:
    result = ground_date(
        value,
        DateIndex.from_text(text),
        role=role,  # type: ignore[arg-type]
        require_month=require_month,
        near=near,
    )
    return None if result is None else (result.value, result.year_only)


# -- every format the document may use -------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "value", "role", "expected"),
    [
        # month name and year
        ("Widget Analyst   Mar 2024 - Sep 2024", "2024-03", "start", ("2024-03", False)),
        ("Widget Analyst   Mar 2024 - Sep 2024", "2024-09", "end", ("2024-09", False)),
        ("May 2018 - January 2021", "2018-05", "start", ("2018-05", False)),
        ("May 2018 - January 2021", "2021-01", "end", ("2021-01", False)),
        ("Sept. 2019 - May 2020", "2019-09", "start", ("2019-09", False)),
        ("Sep 2019 - May 2020", "2019-09", "start", ("2019-09", False)),
        ("SEPTEMBER 2019", "2019-09", "start", ("2019-09", False)),
        ("Dec, 2019", "2019-12", "start", ("2019-12", False)),
        ("May 2019", "2019-05", "start", ("2019-05", False)),
        ("Mar 12, 2024", "2024-03", "start", ("2024-03", False)),
        ("March 3rd 2024", "2024-03", "start", ("2024-03", False)),
        ("Mar '24", "2024-03", "start", ("2024-03", False)),
        ("Mar \N{RIGHT SINGLE QUOTATION MARK}24", "2024-03", "start", ("2024-03", False)),
        # numeric
        ("03/2019 - 11/2022", "2019-03", "start", ("2019-03", False)),
        ("03/2019 - 11/2022", "2022-11", "end", ("2022-11", False)),
        ("3/2024", "2024-03", "start", ("2024-03", False)),
        ("03-2024", "2024-03", "start", ("2024-03", False)),
        ("03.2024", "2024-03", "start", ("2024-03", False)),
        ("2024-02 - Present", "2024-02", "start", ("2024-02", False)),
        ("2024/02", "2024-02", "start", ("2024-02", False)),
        ("started 2024-02-15 in the office", "2024-02", "start", ("2024-02", False)),
        # two-digit years
        ("01/22 - Present", "2022-01", "start", ("2022-01", False)),
        ("04/19 - 08/21", "2021-08", "end", ("2021-08", False)),
        ("01/49", "2049-01", "start", ("2049-01", False)),
        ("01/50", "1950-01", "start", ("1950-01", False)),
        # a year with no month: only the convention month, and flagged
        ("2021 - 2022", "2021-01", "start", ("2021-01", True)),
        ("2021 - 2022", "2022-12", "end", ("2022-12", True)),
        ("2021-2022", "2021-01", "start", ("2021-01", True)),
        ("2021 - 2022", "2021-06", "start", None),
        ("2021 - 2022", "2022-06", "end", None),
        ("2021 - 2022", "2021-12", "start", None),
        ("2021 - 2022", "2022-01", "end", None),
        # the present
        ("Apr. 2022 to present", "present", "end", ("present", False)),
        ("Apr. 2022 to Present", "Present", "end", ("present", False)),
        ("Feb 2023 - Current", "present", "end", ("present", False)),
        ("2024-02 - Present | Remote", "present", "end", ("present", False)),
        ("2019 until now", "present", "end", ("present", False)),
        ("2019 - to date", "current", "end", ("present", False)),
        ("Jun 2022 - ongoing", "ongoing", "end", ("present", False)),
        ("Present", "present", "end", ("present", False)),
        # never the present when the text does not say so
        ("Jun 2022 - Aug 2023", "present", "end", None),
        ("The current role now drives the campaign list", "present", "end", None),
        ("- Current project plans were written", "present", "end", None),
        ("a now famous author, to present the results", "present", "end", None),
        ("Jun 2022 - Present", "present", "start", None),
        ("Jun 2022 - Present", "present", "single", None),
        # a date the text does not contain
        ("Mar 2024 - Sep 2024", "2024-04", "start", None),
        ("Mar 2024 - Sep 2024", "2023-06", "start", None),
        ("Mar 2024 - Sep 2024", "1987-03", "start", None),
        ("no dates here at all", "2020-01", "start", None),
        ("", "2020-01", "start", None),
        # not a date at all
        ("Mar 2024", "Summer 2024", "start", None),
        ("Mar 2024", "someday", "start", None),
        ("Mar 2024", "", "start", None),
        ("Mar 2024", "   ", "start", None),
        ("Mar 2024", "x" * 100, "start", None),
        ("Mar 2024", "2024-13", "start", None),
        ("Mar 2024", "2024-00", "start", None),
        # the model wrote the document's own format: it is stored as YYYY-MM
        ("Mar 2024 - Sep 2024", "Mar 2024", "start", ("2024-03", False)),
        ("03/2019", "03/2019", "start", ("2019-03", False)),
        ("March 2024", "March 2024.", "start", ("2024-03", False)),
        # more than a date, or more than one
        ("Mar 2024 - Sep 2024", "Mar 2024 (expected)", "start", None),
        ("2021 - 2022", "2021 - 2022", "start", None),
        ("Mar 2024 - Sep 2024", "Mar 2024 - Sep 2024", "start", None),
    ],
)
def test_date_equivalence_table(
    text: str, value: str, role: str, expected: tuple[str, bool] | None
) -> None:
    assert _ground(text, value, role) == expected


@pytest.mark.parametrize(
    ("text", "value", "role", "expected"),
    [
        # fields that may be a bare year (education, publications ...)
        ("Larkfield Fictional College | 2015", "2015", "end", ("2015", False)),
        ("Larkfield Fictional College | 2015", "2015-06", "end", None),
        ("Larkfield Fictional College | 2015", "2015-12", "end", ("2015-12", True)),
        ("Sep 2022 - May 2026", "2026-05", "end", ("2026-05", False)),
        ("Sep 2022 - May 2026", "2026", "end", ("2026", False)),  # less precise, still its date
        ("Sep 2022 - May 2026", "2027", "end", None),
        ("Published June 2024", "2024-06", "single", ("2024-06", False)),
        ("Published June 2024", "2024", "single", ("2024", False)),
        ("Published 2024", "2024", "single", ("2024", False)),
        ("Published 2024", "2024-01", "single", None),
        ("Published 2024", "2024-12", "single", None),
        ("Published 2024", "2023", "single", None),
        ("Class of 2015 to 2019", "2015-01", "start", ("2015-01", True)),
    ],
)
def test_fields_that_may_hold_just_a_year(
    text: str, value: str, role: str, expected: tuple[str, bool] | None
) -> None:
    assert _ground(text, value, role, require_month=False) == expected


def test_a_bare_year_for_a_field_that_needs_a_month_takes_the_convention_only_if_shown_alone() -> (
    None
):
    assert _ground("2021 - 2022", "2021", "start") == ("2021-01", True)
    assert _ground("2021 - 2022", "2022", "end") == ("2022-12", True)
    # the year is only ever written with a month in the text: no convention applies
    assert _ground("Jun 2021 - Aug 2022", "2021", "start") is None
    # a lone date has no convention at all
    assert _ground("2021", "2021", "single") is None


def test_a_year_that_is_part_of_a_month_date_is_not_also_a_bare_year() -> None:
    mentions = extract_mentions("Mar 2024 - Sep 2024")
    assert [(m.year, m.month) for m in mentions] == [(2024, 3), (2024, 9)]


def test_mentions_carry_their_position() -> None:
    text = "From Mar 2024 until 2027."
    assert extract_mentions(text) == [
        DateMention(2024, 3, 5, 13),
        DateMention(2027, None, 20, 24),
    ]
    assert text[5:13] == "Mar 2024"


@pytest.mark.parametrize(
    "text",
    [
        "Call (555) 010-0123 today",
        "ZIP 20190 and 12345",
        "Saved $2025 in costs",
        "13/2020",
        "version 3.14.2",
        "40 stores, 1,950 SKUs",
        "ratio 24/7",
        "page 1 of 2",
    ],
)
def test_numbers_that_are_not_dates_are_not_read_as_dates(text: str) -> None:
    assert extract_mentions(text) == []


def test_a_range_of_years_is_two_bare_years() -> None:
    assert [(m.year, m.month) for m in extract_mentions("2021 - 2022")] == [
        (2021, None),
        (2022, None),
    ]
    assert [(m.year, m.month) for m in extract_mentions("2015-2018")] == [
        (2015, None),
        (2018, None),
    ]


def test_the_nearest_of_several_matches_is_chosen() -> None:
    text = "Mar 2024 at the head of the page ... " + "x " * 50 + "Mar 2024 down here"
    index = DateIndex.from_text(text)
    first, second = (m for m in index.mentions)
    near_first = ground_date("2024-03", index, role="start", require_month=True, near=0)
    near_second = ground_date("2024-03", index, role="start", require_month=True, near=len(text))
    assert near_first is not None and near_second is not None
    assert near_first.span == (first.start, first.end)
    assert near_second.span == (second.start, second.end)


def test_a_grounding_says_where_the_date_is() -> None:
    text = "Widget Analyst Mar 2024 - Present"
    index = DateIndex.from_text(text)
    start = ground_date("2024-03", index, role="start", require_month=True)
    end = ground_date("present", index, role="end", require_month=True)
    assert start is not None and end is not None
    assert text[start.span[0] : start.span[1]] == "Mar 2024"
    assert text[end.span[0] : end.span[1]].endswith("Present")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Mar 2024", (2024, 3)),
        ("2024", (2024, None)),
        ("03/2024", (2024, 3)),
        (" 2024-03 ", (2024, 3)),
        ("Mar 2024 - Sep 2024", None),
        ("Mar 2024 and more", None),
        ("nothing", None),
        ("", None),
    ],
)
def test_parse_single_date(value: str, expected: tuple[int, int | None] | None) -> None:
    assert parse_single_date(value) == expected


# -- a range that shares its year ------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "value", "role", "expected"),
    [
        ("Software Intern   Jun - Aug 2023", "2023-06", "start", ("2023-06", False)),
        ("Software Intern   Jun - Aug 2023", "2023-08", "end", ("2023-08", False)),
        ("Jun-Aug 2023", "2023-06", "start", ("2023-06", False)),
        ("June to August 2023", "2023-06", "start", ("2023-06", False)),
        ("June to August 2023", "2023-08", "end", ("2023-08", False)),
        ("May \N{EN DASH} Aug 2022", "2022-05", "start", ("2022-05", False)),
        ("Sept. - Dec. 2021", "2021-09", "start", ("2021-09", False)),
        ("Jan until Mar 2020", "2020-01", "start", ("2020-01", False)),
        # a month is only ever the one the text shows, in the year the text shows
        ("Jun - Aug 2023", "2022-06", "start", None),
        ("Jun - Aug 2023", "2023-07", "start", None),
        ("Jun - Aug 2023", "2023-05", "start", None),
        # `Nov - Feb 2023` does not say whether November is in 2023 or the year before
        ("Nov - Feb 2023", "2023-11", "start", None),
        ("Nov - Feb 2023", "2022-11", "start", None),
        ("Nov - Feb 2023", "2023-02", "end", ("2023-02", False)),
        # two dates that each carry their own year are unchanged
        ("Jun 2022 - Aug 2023", "2022-06", "start", ("2022-06", False)),
        ("Jun 2022 - Aug 2023", "2023-06", "start", None),
    ],
)
def test_the_first_month_of_a_range_shares_the_year_of_the_second(
    text: str, value: str, role: str, expected: tuple[str, bool] | None
) -> None:
    assert _ground(text, value, role) == expected


def test_the_shared_year_month_is_a_mention_at_the_month_word() -> None:
    text = "Jun - Aug 2023"
    assert extract_mentions(text) == [DateMention(2023, 6, 0, 3), DateMention(2023, 8, 6, 14)]
    assert [(m.year, m.month) for m in extract_mentions("Nov - Feb 2023")] == [(2023, 2)]


def test_a_month_word_that_is_not_the_start_of_a_range_is_not_a_date() -> None:
    assert [(m.year, m.month) for m in extract_mentions("Worked in March. Left Aug 2023")] == [
        (2023, 8)
    ]


@pytest.mark.parametrize(
    ("text", "value", "expected"),
    [
        ("Mar-2019 - Jun-2021", "2019-03", ("2019-03", False)),
        ("Mar-2019 - Jun-2021", "2021-06", ("2021-06", False)),
        ("Mar/2019", "2019-03", ("2019-03", False)),
        ("Mar - 2019", "2019-03", None),  # spaces on both sides of a dash: not one date
    ],
)
def test_a_month_name_and_year_may_be_joined_by_a_hyphen_or_a_slash(
    text: str, value: str, expected: tuple[str, bool] | None
) -> None:
    assert _ground(text, value, "start") == expected


# -- the present closes a range --------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Mar 2018 - Jun 2020\nPrepared slides to present Q3 results to the board.",
        "Mar 2018 - Jun 2020\nWe had to present 20 results to the board.",
        "Mar 2018 - Jun 2020\nasked to Present Results every week",
        "Mar 2018 - Jun 2020\nto present.",
        "Mar 2018 - Jun 2020\nto present, and led the team",
        "Acme - Current Projects",
        "Jun 2022 - Aug 2023\n- Current Projects included the migration",
        "Led the team to ongoing success",
    ],
)
def test_prose_that_says_present_is_not_a_date_that_is_still_going(text: str) -> None:
    assert DateIndex.from_text(text).present == ()
    assert _ground(text, "present", "end") is None


@pytest.mark.parametrize(
    "text",
    [
        "Jun 2022 - Present",
        "2019 \N{EN DASH} Current",
        "Jun 2022 \N{EM DASH} Present | Remote",
        "Jun 2022 to present",
        "Jun 2022 -\nPresent",  # the range wrapped after the dash
        "Mar 2018 - Currently",
        "Jun 2022 - Pres.",
        "Jun 2022 til present",
        "2019 \N{MINUS SIGN} Present",
        "Jun 2022 \N{RIGHTWARDS ARROW} Present",
        "Jun 2022 | Present",
        "Springfield\nPresent",  # a column of end dates, on a line of its own
    ],
)
def test_a_present_marker_that_closes_a_range_is_found(text: str) -> None:
    assert _ground(text, "present", "end") == ("present", False)


def test_a_present_marker_far_from_any_date_is_not_a_range() -> None:
    text = "Jun 2022\n\nSomething else entirely, then - Present"
    assert DateIndex.from_text(text).present == ()


# -- a month that is not a month -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "value", "role"),
    [
        ("Paper ref 2023-13, Journal of Widgets", "2023-13", "single"),
        ("Course 2023-00 listing", "2023-00", "single"),
        ("Class of 2015-13", "2015-13", "end"),
        ("Course 2023/13", "2023-13", "single"),
        ("Course 2023.13", "2023-13", "single"),
    ],
)
def test_an_impossible_month_in_the_text_is_never_a_date(text: str, value: str, role: str) -> None:
    assert _ground(text, value, role, require_month=False) is None


@pytest.mark.parametrize(
    "text",
    [
        "ref 2023-13",
        "ref 2023/13",
        "ref 2023.00",
        "Paper 2023-99",
        "ref 2023-00 and 2024-14",
        "Jun 2023 - 13/2024 and 0/2020 and 13/22",
    ],
)
def test_no_mention_has_a_month_outside_one_to_twelve(text: str) -> None:
    for mention in extract_mentions(text):
        assert mention.month is None or 1 <= mention.month <= 12, text
    assert parse_single_date("2023-13") is None


def test_ground_date_refuses_a_month_the_patterns_let_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The profile model checks months only for experience dates, so this is the second line of
    defence for the other entries: even if a pattern were loosened to read `2023-13` as a month,
    a month outside 1-12 is not grounded."""
    from between_jobs.api import profile_import_dates as dates

    loosened = re.compile(r"(?<![\d/.\-])((?:19|20)\d{2})[-/.](0[1-9]|1[0-3])(?!\d)")
    monkeypatch.setattr(dates, "_PATTERNS", ((loosened, "year-month"), *dates._PATTERNS[1:]))
    assert extract_mentions("ref 2023-13")[0].month == 13  # the loosened pattern does read it
    assert _ground("Paper ref 2023-13, Journal of Widgets", "2023-13", "single") is None
