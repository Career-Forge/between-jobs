"""Dates as a resume prints them. Plain text out; `LatexText` escapes it at render time."""

from __future__ import annotations

import re

_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
_YEAR_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def month_label(value: str) -> str:
    """`2021-03` as `Mar 2021`, `present` as `Present`; anything else (a year, a date the
    person wrote their own way) exactly as written."""
    text = value.strip()
    if text.lower() == "present":
        return "Present"
    match = _YEAR_MONTH.match(text)
    if match:
        return f"{_MONTHS[int(match.group(2)) - 1]} {match.group(1)}"
    return text


def date_range(start: str, end: str, *, current: bool = False) -> str:
    """`Mar 2021 -- Present`; one side alone when the other is empty; empty when both are."""
    first = month_label(start)
    last = "Present" if current else month_label(end)
    if first and last:
        return f"{first} -- {last}"
    return first or last
