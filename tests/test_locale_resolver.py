"""Tests for R4's locale resolution precedence and country-detection
heuristic (resumeforge-shape-and-fit.md)."""

from __future__ import annotations

from between_jobs.api.locale_resolver import (
    detect_country_from_text,
    resolve_locale_for_prepare,
)


def test_detects_common_country_names() -> None:
    assert detect_country_from_text("United Kingdom") == "UK"
    assert detect_country_from_text("Remote, Germany") == "DE"
    assert detect_country_from_text("Mexico City, Mexico") == "MX"


def test_detects_by_city_when_country_name_is_absent() -> None:
    assert detect_country_from_text("London, UK") == "UK"
    assert detect_country_from_text("Toronto, ON") == "CA"
    assert detect_country_from_text("Bengaluru, India") == "IN"
    assert detect_country_from_text("Dubai, UAE") == "GULF"


def test_ireland_is_detected_distinctly_from_the_uk() -> None:
    assert detect_country_from_text("Dublin, Ireland") == "IE"


def test_case_insensitive_and_whitespace_tolerant() -> None:
    assert detect_country_from_text("  NEW YORK,   NY  ") == "US"


def test_none_or_empty_text_returns_none() -> None:
    assert detect_country_from_text(None) is None
    assert detect_country_from_text("") is None
    assert detect_country_from_text("   ") is None


def test_unrecognized_location_returns_none_rather_than_guessing() -> None:
    assert detect_country_from_text("Remote") is None
    assert detect_country_from_text("Some Made Up Place") is None


def test_deferred_countries_are_still_detectable_here() -> None:
    """Detection itself doesn't know about R4's JP/CN deferral (decision
    #8) -- that's forge-engines' `resolve_locale`'s job, which returns
    DEFAULT + a note for these two specifically. This layer's only
    responsibility is "what country does this text look like."""
    assert detect_country_from_text("Tokyo, Japan") == "JP"
    assert detect_country_from_text("Shanghai") == "CN"


# ── resolve_locale_for_prepare precedence ───────────────────────────────────


def test_document_override_wins_over_everything() -> None:
    result = resolve_locale_for_prepare(
        document_override="UK", user_default="US", job_location_text="Berlin, Germany"
    )
    assert result == "UK"


def test_user_default_wins_when_no_document_override() -> None:
    result = resolve_locale_for_prepare(
        document_override=None, user_default="CA", job_location_text="Berlin, Germany"
    )
    assert result == "CA"


def test_job_detection_is_the_final_fallback() -> None:
    result = resolve_locale_for_prepare(
        document_override=None, user_default=None, job_location_text="Berlin, Germany"
    )
    assert result == "DE"


def test_nothing_resolves_to_none() -> None:
    result = resolve_locale_for_prepare(
        document_override=None, user_default=None, job_location_text="Remote"
    )
    assert result is None


def test_empty_string_overrides_are_treated_as_absent() -> None:
    """An empty string is falsy, same as None -- guards against a caller
    passing "" instead of leaving the field unset."""
    result = resolve_locale_for_prepare(
        document_override="", user_default="", job_location_text="Toronto, Canada"
    )
    assert result == "CA"
