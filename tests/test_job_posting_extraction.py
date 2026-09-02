"""Parity tests for job_posting_extraction.py against n8n's own real-sample
self-test fixtures (scripts/lib/salary_sponsorship_extraction.js) -- the
same sentences that file's own `require.main === module` block checks,
kept here as a real diff-clean parity check rather than invented fixtures."""

from __future__ import annotations

from between_jobs.api.job_posting_extraction import (
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
