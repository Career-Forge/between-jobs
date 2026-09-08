"""Tests for the LLM-drafted-answer engine (browser-extension.md E3b)."""

from __future__ import annotations

import json
from typing import Any

from between_jobs.api.application_answer_generator import (
    AnswerVerification,
    flagged_answer_warnings,
    generate_answer,
    is_generation_eligible,
    verify_answer_claims,
)
from between_jobs.api.llm_client import LLMResponse

_LONG_DISCLAIMER = (
    "Upon successful completion of the hiring process, Company may extend an offer of "
    "employment. Where permitted by applicable law, Company reserves the right to conduct "
    "background check investigations and/or reference checks. Company also performs a "
    "one-time visual verification to confirm that the individual accepting employment or a "
    "contractor role reasonably matches a valid government-issued photo ID (e.g., passport, "
    "national ID card, or driver's license). Any offer is contingent upon satisfactory "
    "completion of any background investigation, reference check, and/or identity "
    "verification. Subject to applicable law, Company may rescind the offer based on the "
    "results of such checks, an applicant's refusal to participate, or any attempts to "
    "interfere with the process."
)


def test_is_generation_eligible_accepts_a_real_short_question() -> None:
    assert is_generation_eligible("Why do you want to work here?") is True


def test_is_generation_eligible_rejects_a_real_disclaimer_paragraph() -> None:
    """Regression guard against the exact real Sysdig content that
    prompted this filter's existence."""
    assert is_generation_eligible(_LONG_DISCLAIMER) is False


def test_is_generation_eligible_rejects_empty_or_whitespace() -> None:
    assert is_generation_eligible("") is False
    assert is_generation_eligible("   ") is False


def test_is_generation_eligible_boundary_is_inclusive() -> None:
    exactly_400 = "x" * 400
    assert is_generation_eligible(exactly_400) is True
    assert is_generation_eligible(exactly_400 + "x") is False


async def test_generate_answer_returns_a_parsed_answer() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                {
                    "answer_text": "I'm drawn to this role given my Python and ML background.",
                    "declined_reason": None,
                }
            )
        )

    result = await generate_answer(
        question_text="Why do you want to work here?",
        profile_summary="CANDIDATE: Jane Doe\nEXPERIENCE:\nML Engineer @ Acme",
        job_description="We build ML systems in Python.",
        llm_api_key="sk-test",
        llm_model="test-model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["answer_text"] is not None
    assert "Python" in result["answer_text"]
    assert result["declined_reason"] is None


async def test_generate_answer_honors_a_model_decline() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                {
                    "answer_text": None,
                    "declined_reason": "This is a consent statement, not a question.",
                }
            )
        )

    result = await generate_answer(
        question_text=_LONG_DISCLAIMER[:400],
        profile_summary="",
        job_description="",
        llm_api_key="sk-test",
        llm_model="test-model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["answer_text"] is None
    assert result["declined_reason"] == "This is a consent statement, not a question."


async def test_generate_answer_handles_a_code_fenced_response() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content="```json\n"
            + json.dumps({"answer_text": "A short answer.", "declined_reason": None})
            + "\n```"
        )

    result = await generate_answer(
        question_text="Describe yourself.",
        profile_summary="",
        job_description="",
        llm_api_key="sk-test",
        llm_model="test-model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["answer_text"] == "A short answer."


async def test_generate_answer_treats_a_garbled_response_as_an_unknown_decline() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content="not json at all")

    result = await generate_answer(
        question_text="Why do you want to work here?",
        profile_summary="",
        job_description="",
        llm_api_key="sk-test",
        llm_model="test-model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["answer_text"] is None
    assert result["declined_reason"] is None


async def test_verify_answer_claims_parses_a_full_verification() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                {
                    "claims": [
                        {
                            "claim": "led a team of 5",
                            "verdict": "grounded",
                            "reason": "matches facts",
                        },
                        {
                            "claim": "invented a new algorithm",
                            "verdict": "contradicted",
                            "reason": "not in facts",
                        },
                    ]
                }
            )
        )

    verification = await verify_answer_claims(
        answer_text="I led a team of 5 and invented a new algorithm.",
        profile_summary="Led a team of 5.",
        job_description="",
        llm_api_key="sk-test",
        llm_model="test-model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert len(verification["claims"]) == 2
    assert verification["claims"][0]["verdict"] == "grounded"
    assert verification["claims"][1]["verdict"] == "contradicted"


async def test_verify_answer_claims_downgrades_an_invalid_verdict_to_unverifiable() -> None:
    """Same "unknown labeled, never guessed" precedent C4's own parser
    already established -- an unparseable verdict is kept and downgraded,
    not silently dropped."""

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                {"claims": [{"claim": "something", "verdict": "not_a_real_verdict"}]}
            )
        )

    verification = await verify_answer_claims(
        answer_text="something",
        profile_summary="",
        job_description="",
        llm_api_key="sk-test",
        llm_model="test-model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert verification["claims"][0]["verdict"] == "unverifiable"


async def test_verify_answer_claims_fails_open_on_a_garbled_response() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content="garbage")

    verification = await verify_answer_claims(
        answer_text="anything",
        profile_summary="",
        job_description="",
        llm_api_key="sk-test",
        llm_model="test-model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert verification["claims"] == []


def test_flagged_answer_warnings_only_surfaces_non_grounded_claims() -> None:
    verification: AnswerVerification = {
        "claims": [
            {"claim": "led a team", "verdict": "grounded", "reason": "matches"},
            {"claim": "invented X", "verdict": "contradicted", "reason": "not in facts"},
            {"claim": "worked with Y", "verdict": "unverifiable", "reason": "not found either way"},
        ]
    }

    warnings = flagged_answer_warnings(verification)

    assert len(warnings) == 2
    assert all("led a team" not in w for w in warnings)
    assert any("invented X" in w and "contradicted" in w for w in warnings)
    assert any("worked with Y" in w and "unverifiable" in w for w in warnings)
