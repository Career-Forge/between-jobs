"""Tests for the structured API error contract (Sprint 2.6a).

Route-level exercise of this (401/404/409/422/500 responses actually
carrying this envelope) lives in test_api.py, test_auth.py, and
test_profile_routes.py -- this file only proves the contract type itself:
the body shape matches Appendix B exactly and every code maps to a status.
"""

from __future__ import annotations

from between_jobs.api.errors import _STATUS_BY_CODE, ApiError


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
