"""Tests for Proposal §24.2's public engine contract types (Sprint 3.0d)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError
from test_resume_content import paper as sample_paper

from between_jobs.api.engine_contract import (
    ArtifactRef,
    AtsAttempt,
    PrepareApplicationInput,
    PrepareApplicationResult,
    ResumeContent,
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


# -- the stored result and its resume preview ----------------------------------------------------

_RESULT_FIELDS = {
    "run_id",
    "profile_version_id",
    "job_snapshot_id",
    "resume",
    "cover_letter",
    "application_answers_id",
    "ats_attempts",
    "final_score",
    "score_scale",
    "fit",
    "gate_outcome",
    "gate_reason",
    "gate_cautions",
    "warnings",
    "evidence_fact_ids",
    "resume_content",
}


def _result(**overrides: Any) -> PrepareApplicationResult:
    fields: dict[str, Any] = {
        "run_id": "run-1",
        "profile_version_id": "pv-1",
        "job_snapshot_id": "js-1",
        "resume": ArtifactRef(
            artifact_id="art-1", version_id="ver-1", media_type="application/x-tex", sha256="abc"
        ),
        "cover_letter": None,
        "application_answers_id": None,
        "ats_attempts": [],
        "final_score": None,
        "warnings": [],
        "evidence_fact_ids": [],
    }
    fields.update(overrides)
    return PrepareApplicationResult(**fields)


def test_a_result_has_no_preview_unless_one_is_given() -> None:
    assert _result().resume_content is None
    assert _result().model_dump(mode="json")["resume_content"] is None


def test_the_stored_result_has_exactly_the_keys_the_web_app_expects() -> None:
    preview = ResumeContent.model_validate(sample_paper())

    assert set(_result().model_dump(mode="json")) == _RESULT_FIELDS
    assert set(_result(resume_content=preview).model_dump(mode="json")) == _RESULT_FIELDS
    assert set(PrepareApplicationResult.model_fields) == _RESULT_FIELDS


def test_a_result_stored_before_the_preview_existed_still_validates() -> None:
    stored = _result().model_dump(mode="json")
    del stored["resume_content"]  # what an older row has: the key is not there at all

    result = PrepareApplicationResult.model_validate(stored)

    assert result.resume_content is None
    assert result.resume is not None and result.resume.version_id == "ver-1"


def test_a_result_carries_its_preview_through_a_stored_json_round_trip() -> None:
    preview = ResumeContent.model_validate(sample_paper())
    result = _result(resume_content=preview)

    stored = json.loads(json.dumps(result.model_dump(mode="json"), allow_nan=False))
    again = PrepareApplicationResult.model_validate(stored)

    assert stored["resume_content"] == sample_paper()
    assert stored["resume_content"]["schema"] == 1
    assert again == result
    assert again.resume_content is not None and again.resume_content.schema_ == 1


def test_the_preview_can_be_left_out_of_a_dump_by_name() -> None:
    # how the prepare flow builds the payload the outbox gets
    preview = ResumeContent.model_validate(sample_paper())

    kept_out = _result(resume_content=preview).model_dump(mode="json", exclude={"resume_content"})

    assert set(kept_out) == _RESULT_FIELDS - {"resume_content"}
    assert "section_order" not in json.dumps(kept_out)


def test_a_stored_preview_is_checked_again_when_the_result_is_read_back() -> None:
    stored = _result().model_dump(mode="json")
    stored["resume_content"] = {**sample_paper(), "schema": 2}

    with pytest.raises(ValidationError):
        PrepareApplicationResult.model_validate(stored)


# -- exporting the contract's JSON Schema --------------------------------------------------------


@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_the_paper_contract_exports_a_json_schema_in_both_modes(mode: str) -> None:
    schema: dict[str, Any] = ResumeContent.model_json_schema(mode=mode)  # type: ignore[arg-type]

    assert set(schema["properties"]) == {
        "schema",
        "source",
        "header",
        "summary",
        "section_order",
        "not_shown",
        "experience",
        "internships",
        "projects",
        "education",
        "skills",
    }
    assert {"schema", "source", "section_order", "skills"} <= set(schema["required"])


@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_the_result_exports_a_json_schema_that_names_the_paper_by_its_wire_key(mode: str) -> None:
    schema = PrepareApplicationResult.model_json_schema(mode=mode)  # type: ignore[arg-type]

    assert "resume_content" in schema["properties"]
    assert "schema" in schema["$defs"]["ResumeContent"]["properties"]
    assert "schema_" not in schema["$defs"]["ResumeContent"]["properties"]
