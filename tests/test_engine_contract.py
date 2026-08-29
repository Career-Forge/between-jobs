"""Tests for Proposal §24.2's public engine contract types (Sprint 3.0d)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from between_jobs.api.engine_contract import (
    ArtifactRef,
    AtsAttempt,
    PrepareApplicationInput,
    PrepareApplicationResult,
)

_BREAKDOWN_WIRE = {
    "semanticCoverage": 80,
    "experienceQuality": 70,
    "hardReqScore": 90,
    "quantification": 60,
    "companyAlignment": 50,
    "structure": 100,
}


def test_ats_attempt_accepts_forge_engines_camelcase_breakdown() -> None:
    attempt = AtsAttempt.model_validate(
        {
            "overall_score": 75,
            "breakdown": _BREAKDOWN_WIRE,
            "confidence": "high",
            "rating": "strong",
            "gaps": ["Missing Kubernetes"],
        }
    )

    assert attempt.breakdown.semantic_coverage == 80
    assert attempt.breakdown.hard_req_score == 90


def test_ats_attempt_breakdown_also_accepts_snake_case() -> None:
    # populate_by_name=True -- between-jobs' own code (e.g. round-tripping
    # a stored PrepareApplicationResult) should be able to construct these
    # without going through the wire alias.
    breakdown = {
        "semantic_coverage": 80,
        "experience_quality": 70,
        "hard_req_score": 90,
        "quantification": 60,
        "company_alignment": 50,
        "structure": 100,
    }
    attempt = AtsAttempt.model_validate(
        {
            "overall_score": 75,
            "breakdown": breakdown,
            "confidence": "high",
            "rating": "strong",
            "gaps": [],
        }
    )
    assert attempt.breakdown.company_alignment == 50


def test_artifact_ref_download_url_defaults_to_none() -> None:
    ref = ArtifactRef(
        artifact_id="art-1", version_id="ver-1", media_type="application/pdf", sha256="abc123"
    )
    assert ref.download_url is None


def test_prepare_application_input_requires_a_long_enough_idempotency_key() -> None:
    with pytest.raises(ValidationError):
        PrepareApplicationInput(
            application_id="app-1",
            profile_version_id="pv-1",
            job_snapshot_id="js-1",
            idempotency_key="too-short",
        )


def test_prepare_application_input_defaults_requested_artifacts() -> None:
    command = PrepareApplicationInput(
        application_id="app-1",
        profile_version_id="pv-1",
        job_snapshot_id="js-1",
        idempotency_key="a" * 16,
    )
    assert command.requested_artifacts == ["resume", "cover_letter"]


def test_prepare_application_input_rejects_an_unknown_artifact_kind() -> None:
    with pytest.raises(ValidationError):
        PrepareApplicationInput(
            application_id="app-1",
            profile_version_id="pv-1",
            job_snapshot_id="js-1",
            idempotency_key="a" * 16,
            requested_artifacts=["resume", "video_intro"],  # type: ignore[list-item]
        )


def test_prepare_application_result_round_trips_with_no_artifacts() -> None:
    # A declined gate: resume/cover_letter are None, no attempts were made.
    result = PrepareApplicationResult(
        run_id="run-1",
        profile_version_id="pv-1",
        job_snapshot_id="js-1",
        resume=None,
        cover_letter=None,
        application_answers_id=None,
        ats_attempts=[],
        final_score=None,
        warnings=["Seniority mismatch -- role reads senior, profile reads mid."],
        evidence_fact_ids=[],
    )
    assert result.score_scale == "0-100"
    assert result.resume is None
    assert result.ats_attempts == []
