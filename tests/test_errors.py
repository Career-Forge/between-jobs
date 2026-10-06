"""Tests for the structured API error contract (Sprint 2.6a).

Route-level exercise of this (401/404/409/422/500 responses actually
carrying this envelope) lives in test_api.py, test_auth.py, and
test_profile_routes.py -- this file only proves the contract type itself:
the body shape matches Appendix B exactly and every code maps to a status.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest
from postgrest.exceptions import APIError

from between_jobs.api.errors import _STATUS_BY_CODE, ApiError, ErrorCode, log_api_error


def test_to_body_matches_appendix_b_shape() -> None:
    err = ApiError(
        "SETUP_REQUIRED",
        "Company intelligence needs a search provider and an LLM model.",
        capability="company_intel",
        missing=["search_provider", "llm_model"],
        settings_path="/profile/integrations?capability=company_intel",
    )
    assert err.to_body() == {
        "error": {
            "code": "SETUP_REQUIRED",
            "message": "Company intelligence needs a search provider and an LLM model.",
            "retryable": False,
            "capability": "company_intel",
            "missing": ["search_provider", "llm_model"],
            "settings_path": "/profile/integrations?capability=company_intel",
            "run_id": None,
            "details": {},
        }
    }


def test_defaults_are_appendix_b_defaults() -> None:
    err = ApiError("NOT_FOUND", "no profile version found")
    body = err.to_body()["error"]
    assert body["retryable"] is False
    assert body["capability"] is None
    assert body["missing"] is None
    assert body["settings_path"] is None
    assert body["run_id"] is None
    assert body["details"] == {}


def test_every_error_code_has_a_status() -> None:
    # ErrorCode is a Literal, not a real enum -- nothing enforces every
    # member has a status entry except this test walking the literal's
    # own __args__.
    from between_jobs.api.errors import ErrorCode

    for code in ErrorCode.__args__:  # type: ignore[attr-defined]
        assert code in _STATUS_BY_CODE


def test_status_code_property_reads_the_map() -> None:
    assert ApiError("AUTH_REQUIRED", "x").status_code == 401
    assert ApiError("CONFLICT", "x").status_code == 409
    assert ApiError("INTERNAL_ERROR", "x").status_code == 500


def test_the_platforms_own_limit_codes_have_their_own_statuses() -> None:
    limited = ApiError("RATE_LIMITED", "slow down", retryable=True)
    too_large = ApiError("PAYLOAD_TOO_LARGE", "too big")

    assert limited.status_code == 429
    assert too_large.status_code == 413
    assert too_large.retryable is False  # sending the same body again cannot succeed
    # Both are 429s, but they mean different things and a client must be able to tell.
    provider = ApiError("PROVIDER_RATE_LIMITED", "x")
    assert provider.status_code == 429
    assert provider.code != limited.code


# -- the one log line an ApiError gets, wherever it is answered ----------------------------

_LOGGER = logging.getLogger("between_jobs.test_errors")


@pytest.mark.parametrize(
    ("code", "level"),
    [
        ("NOT_FOUND", logging.INFO),  # a client error
        ("PROVIDER_RATE_LIMITED", logging.INFO),  # still a 4xx
        ("PROVIDER_UNAVAILABLE", logging.WARNING),  # a provider failing is not this service
        ("PROVIDER_REJECTED", logging.WARNING),
        ("INTERNAL_ERROR", logging.ERROR),
        ("RUN_FAILED", logging.ERROR),
    ],
)
def test_an_api_error_is_logged_at_the_level_that_says_whose_problem_it_is(
    code: ErrorCode, level: int, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger=_LOGGER.name):
        log_api_error(_LOGGER, ApiError(code, "a message"), ctx={"route": "/x"})

    (record,) = caplog.records
    assert record.levelno == level
    assert record.getMessage() == f"api error {code}"
    assert record.ctx == {  # type: ignore[attr-defined]
        "code": code,
        "status": _STATUS_BY_CODE[code],
        "route": "/x",
    }


def test_the_log_line_has_the_causes_type_and_code_and_never_a_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    try:
        try:
            raise APIError({"message": "row for jane@example.com exists", "code": "23505"})
        except APIError as cause:
            raise ApiError("INTERNAL_ERROR", "could not save jane@example.com") from cause
    except ApiError as error:
        with caplog.at_level(logging.DEBUG, logger=_LOGGER.name):
            log_api_error(_LOGGER, error)

    (record,) = caplog.records
    assert record.ctx == {  # type: ignore[attr-defined]
        "code": "INTERNAL_ERROR",
        "status": 500,
        "cause_type": "postgrest.exceptions.APIError",
        "cause_code": "23505",
    }
    assert "jane@example.com" not in repr(record.__dict__)


def test_a_file_the_route_does_not_read_is_a_415_that_retrying_cannot_fix() -> None:
    refused = ApiError("UNSUPPORTED_MEDIA_TYPE", "Only PDF or DOCX is supported.")

    assert refused.status_code == 415
    assert refused.retryable is False
    assert refused.to_body()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_the_web_apps_list_of_error_codes_is_the_servers() -> None:
    """web/src/lib/apiErrorCodes.ts mirrors `ErrorCode`; adding a code means editing both."""
    source = (Path(__file__).parent.parent / "web" / "src" / "lib" / "apiErrorCodes.ts").read_text()
    body = source.split("KNOWN_API_ERROR_CODES = [", 1)[1].split("] as const", 1)[0]
    in_web = set(re.findall(r'"([A-Z_]+)"', body))

    assert in_web == set(ErrorCode.__args__)  # type: ignore[attr-defined]
