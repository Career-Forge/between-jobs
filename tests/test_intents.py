"""Tests for the deterministic message classifier (Sprint 2.5 rewrite --
the old colon-delimited "my resume: <text>" capture is gone; see
intents.py's docstring)."""

from __future__ import annotations

import json

import pytest

from between_jobs.api.intents import (
    Intent,
    classify,
    is_unlink_command,
    looks_like_job_paste,
    looks_like_json_payload,
    parse_apply_reference,
    parse_job_paste,
    parse_link_code,
)


@pytest.mark.parametrize(
    "text",
    [
        "set up my resume",
        "SET UP MY RESUME",
        "setup my resume",
        "set up resume",
        "my resume",
        "resume",
        "resume?",
        "/setup",
    ],
)
def test_setup_help_matches(text: str) -> None:
    assert classify(text) == Intent.SETUP_HELP


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
    assert classify(text) == Intent.CHECK_RESUME


@pytest.mark.parametrize(
    "text",
    [
        "hello",
        "find ML jobs in NYC",
        "what can you do?",
        "",
        "my resume: Jane Doe",  # the old Sprint 2.4 format -- deliberately no longer recognized
    ],
)
def test_unrelated_text_is_unknown(text: str) -> None:
    assert classify(text) == Intent.UNKNOWN


def test_short_text_is_not_a_json_payload() -> None:
    assert looks_like_json_payload("set up my resume") is False


def test_text_starting_with_brace_is_a_json_payload() -> None:
    assert looks_like_json_payload('{"personal": {"name": "Jane"}}') is True


def test_long_text_is_a_json_payload_even_without_a_leading_brace() -> None:
    long_text = "x" * 801
    assert looks_like_json_payload(long_text) is True


def test_realistic_resume_json_is_detected() -> None:
    payload = json.dumps({"personal": {"name": "Jane Doe"}, "experience": []})
    assert looks_like_json_payload(payload) is True


def test_leading_whitespace_before_brace_still_detected() -> None:
    assert looks_like_json_payload('   {"personal": {}}') is True


@pytest.mark.parametrize(
    ("text", "expected_code"),
    [
        ("/link ABCD2345", "ABCD2345"),
        ("/LINK abcd2345", "abcd2345"),
        ("  /link  XYZ98765  ".strip(), "XYZ98765"),
    ],
)
def test_parse_link_code_extracts_the_code(text: str, expected_code: str) -> None:
    assert parse_link_code(text) == expected_code


@pytest.mark.parametrize("text", ["/link", "/link ", "link ABCD2345", "not a link command"])
def test_parse_link_code_returns_none_for_non_matching_text(text: str) -> None:
    assert parse_link_code(text) is None


@pytest.mark.parametrize("text", ["/unlink", "/UNLINK", "  /unlink  ".strip()])
def test_is_unlink_command_matches(text: str) -> None:
    assert is_unlink_command(text) is True


@pytest.mark.parametrize("text", ["/unlink me", "unlink", "not a command"])
def test_is_unlink_command_rejects_non_matching_text(text: str) -> None:
    assert is_unlink_command(text) is False


@pytest.mark.parametrize(
    "text",
    ["track a job", "Track this job", "/track", "add a job", "new application", "apply", "Apply?"],
)
def test_track_job_help_intent_matches(text: str) -> None:
    assert classify(text) == Intent.TRACK_JOB_HELP


@pytest.mark.parametrize("text", ["Title: Staff Engineer", "  title:  Foo  "])
def test_looks_like_job_paste_matches_a_title_leading_message(text: str) -> None:
    assert looks_like_job_paste(text) is True


@pytest.mark.parametrize("text", ["Company: Acme", "not a job paste", "", "track a job"])
def test_looks_like_job_paste_rejects_non_matching_text(text: str) -> None:
    assert looks_like_job_paste(text) is False


def test_parse_job_paste_extracts_all_fields() -> None:
    text = (
        "Title: Staff AI Engineer\n"
        "Company: Acme\n"
        "Location: Remote\n"
        "URL: https://example.com/jobs/1\n"
        "\n"
        "We need a Python engineer with RAG experience.\n"
        "Multiple paragraphs allowed."
    )
    result = parse_job_paste(text)
    assert result == {
        "title": "Staff AI Engineer",
        "company_name": "Acme",
        "location_text": "Remote",
        "canonical_url": "https://example.com/jobs/1",
        "description_text": (
            "We need a Python engineer with RAG experience.\nMultiple paragraphs allowed."
        ),
    }


def test_parse_job_paste_works_without_a_blank_line_before_the_body() -> None:
    text = "Title: Staff Engineer\nCompany: Acme\nBuild things."
    result = parse_job_paste(text)
    assert result == {
        "title": "Staff Engineer",
        "company_name": "Acme",
        "location_text": None,
        "canonical_url": None,
        "description_text": "Build things.",
    }


def test_parse_job_paste_omits_optional_fields_when_absent() -> None:
    text = "Title: Staff Engineer\nCompany: Acme\n\nBuild things."
    result = parse_job_paste(text)
    assert isinstance(result, dict)
    assert result["location_text"] is None
    assert result["canonical_url"] is None


def test_parse_job_paste_reports_missing_company() -> None:
    result = parse_job_paste("Title: Staff Engineer\n\nBuild things.")
    assert result == ["Missing required field: Company"]


def test_parse_job_paste_reports_missing_description() -> None:
    result = parse_job_paste("Title: Staff Engineer\nCompany: Acme")
    assert result == ["Missing the job description text after the header fields"]


def test_parse_job_paste_reports_all_missing_fields_at_once() -> None:
    result = parse_job_paste("Title: Staff Engineer")
    assert result == [
        "Missing required field: Company",
        "Missing the job description text after the header fields",
    ]


@pytest.mark.parametrize("text", ["list", "/list", "List my applications", "my applications"])
def test_list_applications_intent_matches(text: str) -> None:
    assert classify(text) == Intent.LIST_APPLICATIONS


@pytest.mark.parametrize(
    ("text", "expected_index"),
    [
        ("apply to #3", 3),
        ("apply #3", 3),
        ("Apply to #12", 12),
        ("generate #5", 5),
        ("APPLY TO #1", 1),
    ],
)
def test_parse_apply_reference_extracts_the_index(text: str, expected_index: int) -> None:
    assert parse_apply_reference(text) == expected_index


@pytest.mark.parametrize("text", ["apply", "apply to a job", "#3", "generate", "not a reference"])
def test_parse_apply_reference_returns_none_for_non_matching_text(text: str) -> None:
    assert parse_apply_reference(text) is None
