"""Tests for the structured API error contract (Sprint 2.6a).

Route-level exercise of this (401/404/409/422/500 responses actually
carrying this envelope) lives in test_api.py, test_auth.py, and
test_profile_routes.py -- this file only proves the contract type itself:
the body shape matches Appendix B exactly and every code maps to a status.
"""

from __future__ import annotations

import re
from pathlib import Path

from between_jobs.api.errors import _STATUS_BY_CODE, ApiError, ErrorCode


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


def test_the_web_apps_list_of_error_codes_is_the_servers() -> None:
    """web/src/lib/apiErrorCodes.ts mirrors `ErrorCode`; adding a code means editing both."""
    source = (Path(__file__).parent.parent / "web" / "src" / "lib" / "apiErrorCodes.ts").read_text()
    body = source.split("KNOWN_API_ERROR_CODES = [", 1)[1].split("] as const", 1)[0]
    in_web = set(re.findall(r'"([A-Z_]+)"', body))

    assert in_web == set(ErrorCode.__args__)  # type: ignore[attr-defined]
