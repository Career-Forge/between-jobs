"""Parity tests for job_posting_extraction.py against n8n's own real-sample
self-test fixtures (scripts/lib/salary_sponsorship_extraction.js) -- the
same sentences that file's own `require.main === module` block checks,
kept here as a real diff-clean parity check rather than invented fixtures."""

from __future__ import annotations

import random
import re
import signal
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from between_jobs.api.job_posting_extraction import (
    _LAKH_CRORE_RX,
    _MAX_TEXT_CHARS,
    EXTRACTION_VERSION,
    extract_all,
    extract_salary,
    extract_sponsorship,
)


def test_real_bosa_properties_sample_range_with_html_entity_dash_and_currency_code() -> None:
    r = extract_salary(
        "Base salary is determined by a combination of factors. "
        "Salary $105,673 &mdash; $142,970 CAD Who You Are..."
    )
    assert r is not None
    assert r.salary_min == 105673
    assert r.salary_max == 142970
    assert r.salary_currency == "CAD"


def test_real_anthropic_sample_range_with_html_entity_dash_and_usd_suffix() -> None:
    r = extract_salary(
        "The annual compensation range for this role is listed below. "
        "Annual Salary: $350,000 &mdash; $850,000 USD Logistics"
    )
    assert r is not None
    assert r.salary_min == 350000
    assert r.salary_max == 850000
    assert r.salary_currency == "USD"


def test_real_microsoft_shaped_sample_plain_hyphen_per_year_no_code_implies_usd() -> None:
    r = extract_salary(
        "based in the US will be between $210,200 - $261,000 per year. "
        "Certain roles may be eligible"
    )
    assert r is not None
    assert r.salary_min == 210200
    assert r.salary_max == 261000
    assert r.salary_currency == "USD"
    assert r.salary_period == "year"


def test_real_sample_plain_hyphen_and_period_terminator_ignores_trailing_policy_text() -> None:
    r = extract_salary(
        "The salary range for this role is $208,000 - $286,000. "
        "Compensation for the role will depend on a number of factors"
    )
    assert r is not None
    assert r.salary_min == 208000
    assert r.salary_max == 286000


def test_real_sample_single_value_with_label_and_html_entity_noise_nearby() -> None:
    r = extract_salary(
        "DevOps Engineer Location: Salt Lake City, UT Salary: $56,000 &nbsp; Want to start"
    )
    assert r is not None
    assert r.salary_min == 56000
    assert r.salary_max is None


def test_real_sample_single_value_with_trailing_code_no_label() -> None:
    r = extract_salary("some preceding text $228,800 USD")
    assert r is not None
    assert r.salary_min == 228800
    assert r.salary_currency == "USD"


def test_trap_bare_compensation_word_with_no_adjacent_digits_extracts_nothing() -> None:
    assert (
        extract_salary(
            "Compensation for the role will depend on a number of factors, "
            "including a candidate's qualifications"
        )
        is None
    )


def test_trap_equity_options_figure_extracts_nothing() -> None:
    assert extract_salary("You will also receive $100k in stock options over 4 years") is None


def test_trap_signing_bonus_figure_extracts_nothing() -> None:
    assert extract_salary("a $50,000 signing bonus in addition to base pay") is None


def test_trap_401k_with_no_dollar_prefix_extracts_nothing() -> None:
    assert extract_salary("We offer a generous 401k match program") is None


def test_trap_bare_small_dollar_amount_below_floor_extracts_nothing() -> None:
    assert extract_salary("you will receive a $50 gift card as a thank you") is None


def test_lakh_lpa_figure_converts_to_inr_year() -> None:
    r = extract_salary("Compensation: 12 LPA fixed, negotiable for the right candidate")
    assert r is not None
    assert r.salary_min == 1_200_000
    assert r.salary_currency == "INR"
    assert r.salary_period == "year"


def test_lakh_range_converts_to_inr() -> None:
    r = extract_salary("Budget for this role is ₹15-20 lakh per annum depending on experience")
    assert r is not None
    assert r.salary_min == 1_500_000
    assert r.salary_max == 2_000_000


def test_crore_figure_converts_to_inr() -> None:
    r = extract_salary("offering 1.2 Cr for the right senior candidate")
    assert r is not None
    assert r.salary_min == 12_000_000


def test_per_hour_period_detected() -> None:
    r = extract_salary("This role pays $45 - $60 per hour depending on experience")
    assert r is not None
    assert r.salary_period == "hour"


def test_per_month_period_detected() -> None:
    r = extract_salary("Stipend of ₹30,000 - 40,000 per month for interns")
    assert r is not None
    assert r.salary_period == "month"


def test_real_anthropic_sponsorship_positive_sample() -> None:
    assert (
        extract_sponsorship(
            "Visa sponsorship: We do sponsor visas! However, we aren't able to "
            "successfully sponsor visas for every role"
        )
        == "explicit_yes"
    )


def test_no_sponsorship_available_is_negative() -> None:
    assert (
        extract_sponsorship("Unfortunately we are not able to offer visa sponsorship for this role")
        == "explicit_no"
    )


def test_unable_to_sponsor_is_negative() -> None:
    assert extract_sponsorship("We are unable to sponsor work visas at this time") == "explicit_no"


def test_no_mention_is_unknown() -> None:
    assert (
        extract_sponsorship("We are looking for a Senior Software Engineer to join our team")
        == "unknown"
    )


def test_negation_wins_when_both_patterns_loosely_present_nearby() -> None:
    assert (
        extract_sponsorship(
            "We previously could sponsor visas but currently do not sponsor visas for this role"
        )
        == "explicit_no"
    )


def test_extract_all_returns_full_shape() -> None:
    r = extract_all("Salary $100,000 - $150,000 USD. We do sponsor visas!")
    assert r.salary_min == 100000
    assert r.salary_max == 150000
    assert r.sponsorship_signal == "explicit_yes"
    assert r.extraction_version == EXTRACTION_VERSION


def test_extract_all_on_empty_text_returns_null_and_unknown_without_raising() -> None:
    r = extract_all("")
    assert r.salary_min is None
    assert r.sponsorship_signal == "unknown"


# -- the lakh/crore pattern is linear (it used to be cubic) -------------------------------

# The pattern as ported from n8n, verbatim. Kept here to show what the fix changed, and
# what it did not.
_OLD_LAKH_CRORE_RX = re.compile(
    r"(?:\u20b9\s*)?(\d+(?:\.\d+)?)\s*(?:-|\u2013|\u2014|&ndash;|&mdash;|to)?\s*"
    r"(\d+(?:\.\d+)?)?\s*(lakh|lac|lpa|l\b|cr|crore)\b",
    re.IGNORECASE,
)


def _hits(rx: re.Pattern[str], text: str) -> list[tuple[str, str | None, str]]:
    return [(m.group(1), m.group(2), m.group(3)) for m in rx.finditer(text)]


@contextmanager
def _within(seconds: int) -> Iterator[None]:
    """Fails the test, instead of hanging the suite, if the block runs long. (SIGALRM
    does interrupt the regex engine; a pattern that is cubic again would otherwise
    take hours at the sizes used below.)"""
    if not hasattr(signal, "SIGALRM"):
        pytest.skip("needs SIGALRM")

    def too_slow(*_: object) -> None:
        raise AssertionError(f"still running after {seconds}s: a regex is superlinear again")

    previous = signal.signal(signal.SIGALRM, too_slow)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


_HOSTILE = {
    "a long run of digits": "1" * 50_000 + " engineer",
    "a number then a long run of spaces": "1" + " " * 50_000 + "x",
    "a decimal then a long run of spaces": "1.5" + " " * 50_000 + "to",
    "a rupee sign then a long run of digits": "\u20b9" + "1" * 50_000,
    "many numbers separated by one space": "1 " * 25_000,
    "numbers each followed by a long run of spaces": ("1" + " " * 40) * 1_000,
    "digits and commas": "$" + "1," * 25_000,
}


@pytest.mark.parametrize("shape", sorted(_HOSTILE))
def test_extraction_stays_fast_on_hostile_text(shape: str) -> None:
    """The old pattern took 67 s at 2,000 characters of the first shape; these are
    25x larger. Employer-controlled text, and HTML-stripped descriptions carry long
    whitespace runs as a matter of course -- all of it runs on the event loop."""
    started = time.perf_counter()
    with _within(10):
        extract_all(_HOSTILE[shape])
    assert time.perf_counter() - started < 2.0


def test_only_the_first_200k_characters_are_searched() -> None:
    padding = "x " * (_MAX_TEXT_CHARS // 2)
    assert len(padding) == _MAX_TEXT_CHARS

    assert extract_salary("budget 15 lakh " + padding) is not None  # inside the window
    assert extract_salary(padding + "budget 15 lakh") is None  # past it
    assert extract_sponsorship(padding + "we do not sponsor visas") == "unknown"
    assert extract_sponsorship("we do not sponsor visas " + padding) == "explicit_no"


# What a real description says, as the old pattern read it and as the new one does.
_REALISTIC = [
    "₹10-15 lakh",
    "₹ 8 - 12 LPA",
    "12 LPA",
    "1.5 cr",
    "1.2 crore per annum",
    "10 to 20 lakh",
    "10 20 lakh",
    "10\u201315 LPA",
    "10&ndash;15 lakh",
    "15-20L",
    "5 L",
    "2 lac",
    "CTC: 18 lpa plus bonus",
    "Compensation \u20b915\u201320 lakh depending on experience",
    "salary 100 cr",
    "a 3 lakh 4 lakh 5 lakh mess",
    "no figure here",
    "total lakhs and crores",
    "1234 lakh",
    "x1,234 lakh",
    "10 total lakh",
    "10 tolakh",
]


@pytest.mark.parametrize("text", _REALISTIC)
def test_the_fix_reads_every_realistic_phrase_exactly_as_the_old_pattern_did(text: str) -> None:
    assert _hits(_LAKH_CRORE_RX, text) == _hits(_OLD_LAKH_CRORE_RX, text)


def test_the_fix_agrees_with_the_old_pattern_on_random_text_except_one_pathological_case() -> None:
    """30,000 short strings built from the pieces these patterns care about. The old
    pattern backtracked into odd readings of a number with two decimal points ("1.30.1l"
    as 1.3 and 0.1); the fix reads the tail of it instead. Everything else must be
    identical."""
    pieces = [
        "0",
        "1",
        "5",
        "12",
        "9.5",
        ".",
        " ",
        "  ",
        "-",
        "\u2013",
        "to",
        "lakh",
        "lpa",
        "l",
        "cr",
        "crore",
        "\u20b9",
        "x",
        "&ndash;",
    ]
    two_decimal_points = re.compile(r"\d\.\d+\.\d")
    rng = random.Random(20261002)
    differing: list[str] = []
    for _ in range(30_000):
        text = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 9)))
        if _hits(_LAKH_CRORE_RX, text) != _hits(_OLD_LAKH_CRORE_RX, text):
            differing.append(text)

    assert [t for t in differing if not two_decimal_points.search(t)] == []


def test_the_one_difference_is_a_number_with_two_decimal_points() -> None:
    # Neither reading of "1.30.1l" means anything -- it is not a number. The old pattern
    # backtracked into splitting it as 1.3 and 0.1; the fix reads the tail, 30.1.
    assert _hits(_OLD_LAKH_CRORE_RX, "1.30.1l") == [("1.3", "0.1", "l")]
    assert _hits(_LAKH_CRORE_RX, "1.30.1l") == [("30.1", None, "l")]
