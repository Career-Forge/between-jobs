"""Tests for the deterministic intent classifier."""

from __future__ import annotations

import pytest

from between_jobs.api.intents import Intent, classify


@pytest.mark.parametrize(
    "text",
    [
        "my resume: Jane Doe, Software Engineer, 5 years experience",
        "MY RESUME: Jane Doe",
        "set up my resume: Jane Doe",
        "resume: Jane Doe",
        "  my resume:   Jane Doe with leading/trailing space  ",
    ],
)
def test_set_up_resume_matches(text: str) -> None:
    result = classify(text)
    assert result.intent == Intent.SET_UP_RESUME
    assert result.resume_text is not None
    assert "Jane Doe" in result.resume_text


def test_set_up_resume_captures_multiline_text() -> None:
    text = "my resume: Jane Doe\nSoftware Engineer\n5 years experience"
    result = classify(text)
    assert result.intent == Intent.SET_UP_RESUME
    assert result.resume_text == "Jane Doe\nSoftware Engineer\n5 years experience"


def test_set_up_resume_with_no_content_after_colon_is_unknown() -> None:
    result = classify("my resume:")
    assert result.intent == Intent.UNKNOWN
    assert result.resume_text is None


@pytest.mark.parametrize(
    "text",
    [
        "check my resume",
        "Check My Resume",
        "check my resume?",
        "do you have my resume",
        "do you have my resume?",
        "show my resume",
    ],
)
def test_check_resume_matches(text: str) -> None:
    result = classify(text)
    assert result.intent == Intent.CHECK_RESUME
    assert result.resume_text is None


@pytest.mark.parametrize(
    "text",
    [
        "hello",
        "find ML jobs in NYC",
        "what can you do?",
        "",
        "resume",  # bare word, no colon -- not a valid trigger
    ],
)
def test_unrelated_text_is_unknown(text: str) -> None:
    result = classify(text)
    assert result.intent == Intent.UNKNOWN
    assert result.resume_text is None
