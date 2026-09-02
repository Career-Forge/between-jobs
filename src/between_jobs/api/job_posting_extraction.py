"""Salary/sponsorship extraction at ingest (Job Finder P2, job-finder-port.md).

A faithful port of n8n's own `scripts/lib/salary_sponsorship_extraction.js`
-- read directly, not paraphrased. Pure regex, no LLM call, no I/O. Every
regex, threshold, and trap word below is copied verbatim from that file;
the self-test fixtures in this module's own test file are the same real
sample sentences n8n's file embeds as its own self-test, kept as a real
parity check rather than invented fixtures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

EXTRACTION_VERSION = 1

_SPONSOR_NEG_RX = re.compile(
    r"\b(?:no|not?|never|without|don'?t|doesn'?t|does\s*not|cannot|can'?t|"
    r"unable\s*to|not?\s*(?:able|willing)\s*to)\b[^.!?\n]{0,30}\bsponsor",
    re.IGNORECASE,
)
_SPONSOR_POS_RX = re.compile(
    r"\b(?:will|do|does|can|able\s*to|open\s*to|happy\s*to)\b[^.!?\n]{0,20}\bsponsor|"
    r"visa\s*sponsorship\s*(?:available|provided|offered|possible)|"
    r"h-?1b\s*(?:sponsorship|transfer)|sponsor\s*(?:visas?|work\s*permits?)",
    re.IGNORECASE,
)

_CURRENCY_SYMBOL_TO_CODE = {"$": "USD", "₹": "INR", "£": "GBP", "€": "EUR"}
_CURRENCY_CODE_RX = r"(?:USD|INR|GBP|EUR|CAD|AUD|SGD|CHF|NZD)"
_DASH_RX = "(?:-|\u2013|\u2014|&ndash;|&mdash;)"
_NUM_RX = r"\d[\d,]*(?:\.\d+)?\s*[kK]?"
_TRAP_WINDOW = 30
_TRAP_RX = re.compile(
    r"\b(?:option|options|equity|stock|rsu|401\s*\(?k\)?|bonus|signing|sign-on)\b",
    re.IGNORECASE,
)

_SALARY_SYMBOL_RX = re.compile(
    r"([$₹£€])("
    + _NUM_RX
    + r")(?:\s*"
    + _DASH_RX
    + r"\s*[$₹£€]?("
    + _NUM_RX
    + r"))?\s*("
    + _CURRENCY_CODE_RX
    + r")?",
    re.IGNORECASE,
)
_LAKH_CRORE_RX = re.compile(
    r"(?:\u20b9\s*)?(\d+(?:\.\d+)?)\s*(?:-|\u2013|\u2014|&ndash;|&mdash;|to)?\s*"
    r"(\d+(?:\.\d+)?)?\s*(lakh|lac|lpa|l\b|cr|crore)\b",
    re.IGNORECASE,
)
_HAS_K_SUFFIX_RX = re.compile(r"[kK]\s*$")
_PERIOD_HOUR_RX = re.compile(r"\b(?:per\s*hour|/\s*hr|hourly)\b", re.IGNORECASE)
_PERIOD_MONTH_RX = re.compile(r"\b(?:per\s*month|/\s*mo|monthly)\b", re.IGNORECASE)


@dataclass(frozen=True)
class SalaryHit:
    salary_min: float
    salary_max: float | None
    salary_currency: str | None
    salary_period: str


@dataclass(frozen=True)
class ExtractionResult:
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    salary_period: str | None
    sponsorship_signal: str
    extraction_version: int


def extract_sponsorship(text: str | None) -> str:
    s = text or ""
    if not s:
        return "unknown"
    if _SPONSOR_NEG_RX.search(s):
        return "explicit_no"
    if _SPONSOR_POS_RX.search(s):
        return "explicit_yes"
    return "unknown"


def _parse_num(raw: str) -> float | None:
    s = raw.strip()
    has_k = bool(_HAS_K_SUFFIX_RX.search(s))
    cleaned = re.sub(r"[,\s kK]", "", s)
    try:
        n = float(cleaned)
    except ValueError:
        return None
    return n * 1000 if has_k else n


def _detect_period(tail: str) -> str:
    if _PERIOD_HOUR_RX.search(tail):
        return "hour"
    if _PERIOD_MONTH_RX.search(tail):
        return "month"
    return "year"


def _extract_salary_currency_symbol(text: str) -> list[SalaryHit]:
    hits: list[SalaryHit] = []
    for m in _SALARY_SYMBOL_RX.finditer(text):
        tail = text[m.start() : m.end() + _TRAP_WINDOW]
        if _TRAP_RX.search(tail):
            continue
        lo = _parse_num(m.group(2))
        hi = _parse_num(m.group(3)) if m.group(3) else None
        if lo is None:
            continue
        period = _detect_period(tail)
        floor = 5 if period == "hour" else 300 if period == "month" else 10000
        has_k = bool(_HAS_K_SUFFIX_RX.search(m.group(2)))
        if lo < floor and not has_k:
            continue
        currency = (m.group(4) or "").upper() or _CURRENCY_SYMBOL_TO_CODE.get(m.group(1))
        hits.append(
            SalaryHit(
                salary_min=min(lo, hi) if hi is not None else lo,
                salary_max=max(lo, hi) if hi is not None else None,
                salary_currency=currency,
                salary_period=period,
            )
        )
    return hits


def _extract_salary_lakh_crore(text: str) -> list[SalaryHit]:
    hits: list[SalaryHit] = []
    for m in _LAKH_CRORE_RX.finditer(text):
        tail = text[m.start() : m.end() + _TRAP_WINDOW]
        if _TRAP_RX.search(tail):
            continue
        unit = 10_000_000 if m.group(3).lower().startswith("cr") else 100_000
        lo = float(m.group(1)) * unit
        hi = float(m.group(2)) * unit if m.group(2) else None
        hits.append(
            SalaryHit(
                salary_min=min(lo, hi) if hi is not None else lo,
                salary_max=max(lo, hi) if hi is not None else None,
                salary_currency="INR",
                salary_period="year",
            )
        )
    return hits


def extract_salary(text: str | None) -> SalaryHit | None:
    s = text or ""
    if not s:
        return None
    hits = [*_extract_salary_lakh_crore(s), *_extract_salary_currency_symbol(s)]
    if not hits:
        return None
    # Prefer the widest span among multiple candidate matches -- the same
    # tie-break n8n's own extractSalary uses.
    hits.sort(key=lambda h: (h.salary_max or h.salary_min) - h.salary_min, reverse=True)
    return hits[0]


def extract_all(text: str | None) -> ExtractionResult:
    salary = extract_salary(text)
    return ExtractionResult(
        salary_min=salary.salary_min if salary else None,
        salary_max=salary.salary_max if salary else None,
        salary_currency=salary.salary_currency if salary else None,
        salary_period=salary.salary_period if salary else None,
        sponsorship_signal=extract_sponsorship(text),
        extraction_version=EXTRACTION_VERSION,
    )
