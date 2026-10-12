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

from between_jobs.api.errors import (
    _STATUS_BY_CODE,
    PROVIDER_FAILURE_CODES,
    ApiError,
    ErrorCode,
    is_provider_failure,
    log_api_error,
    log_contained_failure,
)


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
        # a provider failing is not this service, whatever its status (429 included)
        ("PROVIDER_RATE_LIMITED", logging.WARNING),
        ("PROVIDER_UNAVAILABLE", logging.WARNING),
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


def test_a_feature_the_built_in_engine_does_not_do_is_a_409_that_retrying_cannot_fix() -> None:
    err = ApiError(
        "NOT_AVAILABLE_IN_GENERIC_ENGINE",
        "The gap interview isn't available on this server's built-in engine.",
        details={"operation": "gap_interview", "engine": "generic"},
    )

    # A deployment capability: not a mistake in the request (422), not a failure to retry (503).
    assert err.status_code == 409
    assert err.retryable is False
    body = err.to_body()["error"]
    assert body["code"] == "NOT_AVAILABLE_IN_GENERIC_ENGINE"
    assert body["retryable"] is False
    assert body["details"] == {"operation": "gap_interview", "engine": "generic"}
    # not the answer for a missing setup the person can do themselves
    assert err.code != "SETUP_REQUIRED"
    assert body["capability"] is None and body["missing"] is None and body["settings_path"] is None


def test_it_is_logged_as_a_client_error_not_as_a_failure(caplog: pytest.LogCaptureFixture) -> None:
    """A 5xx would be logged at ERROR and sent to the error tracker every time somebody clicked a
    button the server cannot serve (the tracker side is test_sentry_scrub.py)."""
    with caplog.at_level(logging.DEBUG, logger=_LOGGER.name):
        log_api_error(
            _LOGGER, ApiError("NOT_AVAILABLE_IN_GENERIC_ENGINE", "x"), ctx={"route": "/x"}
        )

    (record,) = caplog.records
    assert record.levelno == logging.INFO


def test_the_web_apps_list_of_error_codes_is_the_servers() -> None:
    """web/src/lib/apiErrorCodes.ts mirrors `ErrorCode`; adding a code means editing both."""
    source = (Path(__file__).parent.parent / "web" / "src" / "lib" / "apiErrorCodes.ts").read_text()
    body = source.split("KNOWN_API_ERROR_CODES = [", 1)[1].split("] as const", 1)[0]
    in_web = set(re.findall(r'"([A-Z_]+)"', body))

    assert in_web == set(ErrorCode.__args__)  # type: ignore[attr-defined]


# -- provider failures: what they are, and the reason in their log line --------------------


def test_the_provider_failure_codes_are_exactly_the_three_provider_ones() -> None:
    assert {
        "PROVIDER_REJECTED",
        "PROVIDER_RATE_LIMITED",
        "PROVIDER_UNAVAILABLE",
    } == PROVIDER_FAILURE_CODES
    # the prefix rule the log level used to rely on names no code outside the set
    assert {c for c in ErrorCode.__args__ if c.startswith("PROVIDER_")} == PROVIDER_FAILURE_CODES  # type: ignore[attr-defined]


@pytest.mark.parametrize("code", sorted(PROVIDER_FAILURE_CODES))
def test_a_provider_failure_is_recognised(code: ErrorCode) -> None:
    assert is_provider_failure(ApiError(code, "x"))


@pytest.mark.parametrize("code", ["RUN_FAILED", "INTERNAL_ERROR", "RATE_LIMITED", "SETUP_REQUIRED"])
def test_other_api_errors_are_not_provider_failures(code: ErrorCode) -> None:
    assert not is_provider_failure(ApiError(code, "x"))


@pytest.mark.parametrize("not_an_api_error", [RuntimeError("PROVIDER_REJECTED"), ValueError()])
def test_only_an_api_error_can_be_a_provider_failure(not_an_api_error: BaseException) -> None:
    assert not is_provider_failure(not_an_api_error)


def test_the_log_line_carries_the_reason_a_provider_failure_names(
    caplog: pytest.LogCaptureFixture,
) -> None:
    error = ApiError("PROVIDER_REJECTED", "x", details={"reason": "out_of_credit"})
    with caplog.at_level(logging.DEBUG, logger=_LOGGER.name):
        log_api_error(_LOGGER, error, ctx={"route": "/x"})

    (record,) = caplog.records
    assert record.ctx == {  # type: ignore[attr-defined]
        "code": "PROVIDER_REJECTED",
        "status": 502,
        "route": "/x",
        "reason": "out_of_credit",
    }


@pytest.mark.parametrize(
    "reason",
    [
        "Your key sk-or-v1-abcdef0123456789 was rejected",  # free text
        "Key_Rejected",  # not a lower-case token
        "9lives",  # does not start with a letter
        "a" * 33,  # longer than a token
        "",
        42,
        None,
        ["key_rejected"],
        # Each of these is short enough, and a letter first, wherever it is not: it pins one
        # character the pattern must refuse, so loosening any one part of it fails here. (The
        # capitalised cases above are refused by their first character alone and say nothing
        # about the rest.)
        "Rejected",  # a capital as the first character, and nothing else wrong with it
        "_key",  # an underscore may not start a token
        "key was rejected",  # a lower-case sentence: spaces
        "sk-or-v1-0123456789abcdef",  # a lower-case key shape: hyphens
        "key.rejected",  # a dot
        "keyRejected",  # a capital after the first character
        "key\n",  # a trailing newline (`$` would let it through, a full match does not)
    ],
)
def test_the_log_line_never_carries_a_reason_that_is_not_a_short_token(
    reason: object, caplog: pytest.LogCaptureFixture
) -> None:
    error = ApiError("PROVIDER_REJECTED", "x", details={"reason": reason})
    with caplog.at_level(logging.DEBUG, logger=_LOGGER.name):
        log_api_error(_LOGGER, error)

    (record,) = caplog.records
    assert "reason" not in record.ctx  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "reason",
    [
        "a" * 32,  # the longest allowed
        "a",  # the shortest
        "out_of_credit",  # a real one: lower-case words and underscores
        "request_rejected2",  # digits after the first character
        "a_1",
        "in_flight_budget",
    ],
)
def test_a_short_lower_case_token_is_kept(reason: str, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger=_LOGGER.name):
        log_api_error(_LOGGER, ApiError("RATE_LIMITED", "x", details={"reason": reason}))
    (record,) = caplog.records
    assert record.ctx["reason"] == reason  # type: ignore[attr-defined]


def test_a_contained_provider_failure_is_one_warning_with_its_reason_and_no_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    error = ApiError("PROVIDER_REJECTED", "x", details={"reason": "key_rejected"})
    with caplog.at_level(logging.DEBUG, logger=_LOGGER.name):
        log_contained_failure(
            _LOGGER, "the item failed", error, task="the_task", ctx={"saved_search_id": "s-1"}
        )

    (record,) = caplog.records
    assert record.levelno == logging.WARNING
    assert record.getMessage() == "api error PROVIDER_REJECTED"
    assert record.exc_info is None
    assert record.ctx == {  # type: ignore[attr-defined]
        "code": "PROVIDER_REJECTED",
        "status": 502,
        "saved_search_id": "s-1",
        "task": "the_task",
        "reason": "key_rejected",
    }


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("boom"), ApiError("INTERNAL_ERROR", "x"), ApiError("RUN_FAILED", "x")],
)
def test_any_other_contained_failure_keeps_its_message_and_traceback(
    failure: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger=_LOGGER.name):
        log_contained_failure(
            _LOGGER, "the item failed", failure, task="the_task", ctx={"draft_id": "d-1"}
        )

    (record,) = caplog.records
    assert record.levelno == logging.ERROR
    assert record.getMessage() == "the item failed"
    assert record.exc_info is not None and record.exc_info[1] is failure
    assert record.ctx == {"draft_id": "d-1"}  # type: ignore[attr-defined]


class _CauseWithCode(Exception):
    def __init__(self, code: object) -> None:
        super().__init__("a message that must not be logged")
        self.code = code


@pytest.mark.parametrize(
    ("cause_code", "logged"),
    [
        ("invalid_api_key", True),
        ("a" * 16, True),  # the longest a code may be
        ("a" * 17, False),  # anything longer could be a sentence, or a key
        ("", True),
        (402, False),  # a status number, not a code
        (None, False),
        (["x"], False),
    ],
)
def test_the_causes_code_is_logged_only_when_it_is_a_short_string(
    cause_code: object, logged: bool, caplog: pytest.LogCaptureFixture
) -> None:
    try:
        try:
            raise _CauseWithCode(cause_code)
        except _CauseWithCode as cause:
            raise ApiError("PROVIDER_REJECTED", "x") from cause
    except ApiError as error:
        with caplog.at_level(logging.DEBUG, logger=_LOGGER.name):
            log_api_error(_LOGGER, error)

    (record,) = caplog.records
    assert ("cause_code" in record.ctx) is logged  # type: ignore[attr-defined]
    if logged:
        assert record.ctx["cause_code"] == cause_code  # type: ignore[attr-defined]
    assert record.ctx["cause_type"].endswith("._CauseWithCode")  # type: ignore[attr-defined]
    assert "must not be logged" not in repr(record.__dict__)
