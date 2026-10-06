"""Date equivalence for the resume importer: is this date in the document?

The profile stores dates as `YYYY-MM` (or `present` for a job that has no end). A resume writes
them every way there is: `Jun 2025`, `June 2019`, `06/2025`, `2023-06`, `01/22`, `Apr. 2022 to
present`, `2021 - 2022`. The importer lets a model propose profile dates, then keeps one only if
the document really contains that date. This module is that check, and it errs on the side of
refusing: a date the text does not contain is never accepted.

`DateIndex.from_text` reads every date the text mentions: a full month-and-year (`Jun 2025`,
`06/2025`, `2023-06`, `01/22`, `Jun 12, 2025`), a bare year, the start of a range that shares
its year (`Jun` in `Jun - Aug 2023`), and the words that mean "still going" (`Present`,
`Current`, `to date` ...) when they close a range: right after a date, the way `Jun 2022 -
Present` does, or alone on a line. `to present the results` is a sentence, not a date.
`ground_date` then checks one proposed value against that index.

A year with no month is the one place the profile needs more than the document says: an
experience entry has to carry `YYYY-MM`. The convention, written down here and reported to the
person as an assumption every time it is used, is `YYYY-01` for a start date and `YYYY-12` for
an end date, and it is accepted only when the document really shows that year on its own. A
month the document never shows is never accepted any other way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

DateRole = Literal["start", "end", "single"]

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_MONTH_NAMES = "|".join(sorted(_MONTHS, key=len, reverse=True))
_YEAR = r"(?:19|20)\d{2}"
_APOSTROPHES = "'\u2019"

# Most specific first: a span matched by an earlier pattern is not read again by a later one.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # 2023-06, 2023/06, 2023.06 (also the head of 2023-06-15)
    (re.compile(rf"(?<![\d/.\-])({_YEAR})[-/.](0[1-9]|1[0-2])(?!\d)"), "year-month"),
    # Jun 12, 2025 / June 12th 2025
    (
        re.compile(rf"\b({_MONTH_NAMES})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+({_YEAR})\b", re.I),
        "name-day-year",
    ),
    # Mar 2024 / March, 2024 / Sept. 2019 / Mar-2019 / Mar/2019
    (re.compile(rf"\b({_MONTH_NAMES})\.?,?(?:\s+|[-/])({_YEAR})\b", re.I), "name-year"),
    # Jun '25
    (
        re.compile(rf"\b({_MONTH_NAMES})\.?,?\s+[{_APOSTROPHES}](\d{{2}})\b", re.I),
        "name-short-year",
    ),
    # 06/2025, 6-2025, 06.2025
    (re.compile(rf"(?<![\d/.\-])(0?[1-9]|1[0-2])[-/.]({_YEAR})(?!\d)"), "month-year"),
    # 01/22 (month and two-digit year; only with a slash, and not the head of a longer date)
    (re.compile(r"(?<![\d/.\-])(0?[1-9]|1[0-2])/(\d{2})(?![\d/])"), "month-short-year"),
    # a bare year, 2021
    (re.compile(rf"(?<![\d/$,])({_YEAR})(?!\d)"), "year"),
)

_DASH = "(?:[-\u2010-\u2015\u2212]|->|\u2192)"
_NOT_A_SENTENCE = r"\b(?![ ]+(?-i:[a-z]))"
"""A word that closes a date range is not followed by more lowercase words: `Present`,
`Present | Springfield`, `Present` at the end of a line, but not the start of a bullet like
`- Current work ...`."""
_SHARED_YEAR_LEAD_RE = re.compile(
    rf"\b({_MONTH_NAMES})\.?\s*(?:{_DASH}|\bto\b|\buntil\b|\bthrough\b|\bthru\b)\s*$",
    re.IGNORECASE,
)
"""The words before `Aug 2023` in `Jun - Aug 2023`: a month, then what joins the two ends of a
range."""
_PRESENT_RANGE_RE = re.compile(
    rf"(?:{_DASH}|[|/,:]|\bto\b|\buntil\b|\btill\b|\btil\b|\bthrough\b|\bthru\b)"
    rf"\s*(?:present|pres\b\.?|current(?:ly)?|ongoing){_NOT_A_SENTENCE}"
    rf"|(?:{_DASH}|\bto\b|\buntil\b|\btill\b|\btil\b)\s*(?:now|today|date){_NOT_A_SENTENCE}",
    re.IGNORECASE,
)
_PRESENT_LINE_RE = re.compile(r"^\s*(?:present|current)\s*$", re.IGNORECASE | re.MULTILINE)
_LEAD_REACH = 24
_PRESENT_REACH = 12
"""How far after a date a range-closing `Present` may start: the separator and its spaces."""
_PRESENT_VALUES = frozenset(
    {"present", "current", "currently", "ongoing", "now", "today", "to date", "till date"}
)
"""What a proposed end date may say to mean "no end date yet"."""


@dataclass(frozen=True, slots=True)
class DateMention:
    year: int
    month: int | None
    start: int
    end: int


def _two_digit_year(value: int) -> int:
    return 2000 + value if value < 50 else 1900 + value


def extract_mentions(text: str) -> list[DateMention]:
    """Every date the text mentions, in order of position. A year that is part of a month-and-
    year date is not also reported as a bare year. In `Jun - Aug 2023` the first month shares
    the year of the second, so `Jun` is reported as June 2023 -- when the range runs forward
    within one year: `Nov - Feb 2023` does not say which year November belongs to, and is left
    alone."""
    found: list[DateMention] = []
    taken: list[tuple[int, int]] = []
    shared: list[tuple[re.Match[str], int, int]] = []
    for pattern, kind in _PATTERNS:
        for match in pattern.finditer(text):
            span = (match.start(), match.end())
            if any(span[0] < t_end and t_start < span[1] for t_start, t_end in taken):
                continue
            if kind == "year-month":
                year, month = int(match.group(1)), int(match.group(2))
            elif kind in ("name-day-year", "name-year"):
                year, month = int(match.group(2)), _MONTHS[match.group(1).lower()]
            elif kind == "name-short-year":
                year, month = _two_digit_year(int(match.group(2))), _MONTHS[match.group(1).lower()]
            elif kind == "month-year":
                year, month = int(match.group(2)), int(match.group(1))
            elif kind == "month-short-year":
                year, month = _two_digit_year(int(match.group(2))), int(match.group(1))
            else:
                year, month = int(match.group(1)), 0
            taken.append(span)
            found.append(DateMention(year, month or None, span[0], span[1]))
            if kind == "name-year" and month:
                lead = _SHARED_YEAR_LEAD_RE.search(text, max(0, span[0] - _LEAD_REACH), span[0])
                if lead is not None:
                    shared.append((lead, year, month))
    for lead, year, month in shared:
        first = _MONTHS[lead.group(1).lower()]
        word = (lead.start(1), lead.end(1))
        if first <= month and not any(
            word[0] < t_end and t_start < word[1] for t_start, t_end in taken
        ):
            found.append(DateMention(year, first, word[0], word[1]))
    return sorted(found, key=lambda m: m.start)


def _closes_a_range(text: str, at: int, mentions: list[DateMention]) -> bool:
    """A date ends just before `at` (the start of `- Present`, `to current` ...), on the same
    line and with nothing but the separator's own spaces between: `Jun 2022 - Present` is a
    range; `to present the results`, `Acme - Current Projects` and a bullet that starts with
    `- Current` under a date line are not."""
    return any(
        m.end <= at
        and at - m.end <= _PRESENT_REACH
        and not any(ch.isalnum() or ch == "\n" for ch in text[m.end : at])
        for m in mentions
    )


@dataclass(frozen=True, slots=True)
class DateIndex:
    mentions: tuple[DateMention, ...]
    present: tuple[tuple[int, int], ...]
    """Where the text closes a range with Present, Current, to date ..."""

    @classmethod
    def from_text(cls, text: str) -> DateIndex:
        mentions = extract_mentions(text)
        present = [
            (m.start(), m.end())
            for m in _PRESENT_RANGE_RE.finditer(text)
            if _closes_a_range(text, m.start(), mentions)
        ]
        present.extend((m.start(), m.end()) for m in _PRESENT_LINE_RE.finditer(text))
        return cls(tuple(mentions), tuple(sorted(present)))


@dataclass(frozen=True, slots=True)
class DateGrounding:
    value: str
    """The date as the profile stores it: `YYYY-MM`, `YYYY`, or `present`."""
    span: tuple[int, int]
    """Where in the text the date is."""
    year_only: bool = False
    """The document shows only the year and the month is the start/end convention."""


def _nearest[T](items: list[T], key_start: list[int], near: int | None) -> T:
    if near is None:
        return items[0]
    best = min(range(len(items)), key=lambda i: abs(key_start[i] - near))
    return items[best]


def parse_single_date(value: str) -> tuple[int, int | None] | None:
    """(year, month or None) when `value` is exactly one date and nothing else, else None."""
    mentions = extract_mentions(value)
    if len(mentions) != 1:
        return None
    mention = mentions[0]
    leftover = value[: mention.start] + value[mention.end :]
    if any(ch.isalnum() for ch in leftover):
        return None
    return mention.year, mention.month


def ground_date(
    value: str,
    index: DateIndex,
    *,
    role: DateRole,
    require_month: bool,
    near: int | None = None,
) -> DateGrounding | None:
    """The proposed date `value`, in the form the profile stores, when the document contains it;
    None when it does not.

    `role` says which end of a range the field is (`start`, `end`) or that it is a lone date
    (`single`). `require_month` is for fields that must be `YYYY-MM`: a bare year the document
    shows alone is then written with the convention month, flagged `year_only`. `near` is a
    text offset the field belongs near, used to pick the right one of several matches."""
    raw = value.strip()
    if not raw or len(raw) > 40:
        return None

    if raw.lower() in _PRESENT_VALUES:
        if role != "end" or not index.present:
            return None
        starts = [p[0] for p in index.present]
        return DateGrounding("present", _nearest(list(index.present), starts, near))

    parsed = parse_single_date(raw)
    if parsed is None:
        return None
    year, month = parsed
    if month is not None and not 1 <= month <= 12:
        return None  # the patterns already refuse one; the profile model does not check these
    same_year = [m for m in index.mentions if m.year == year]
    bare = [m for m in same_year if m.month is None]

    if month is not None:
        exact = [m for m in same_year if m.month == month]
        if exact:
            hit = _nearest(exact, [m.start for m in exact], near)
            return DateGrounding(f"{year:04d}-{month:02d}", (hit.start, hit.end))
        convention = {"start": 1, "end": 12}.get(role)
        if bare and month == convention:
            hit = _nearest(bare, [m.start for m in bare], near)
            return DateGrounding(f"{year:04d}-{month:02d}", (hit.start, hit.end), year_only=True)
        return None

    if not same_year:
        return None
    if require_month:
        if not bare or role == "single":
            return None
        hit = _nearest(bare, [m.start for m in bare], near)
        convention_month = 1 if role == "start" else 12
        return DateGrounding(
            f"{year:04d}-{convention_month:02d}", (hit.start, hit.end), year_only=True
        )
    hit = _nearest(same_year, [m.start for m in same_year], near)
    return DateGrounding(f"{year:04d}", (hit.start, hit.end))
