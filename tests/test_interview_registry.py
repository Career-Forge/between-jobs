"""Tests for the interview-process registry synthesis (InterviewForge R1,
interviewforge-v1.md)."""

from __future__ import annotations

import json
from typing import Any

from between_jobs.api.company_intel_pipeline import Claim
from between_jobs.api.interview_registry import synthesize_interview_process_model
from between_jobs.api.llm_client import LLMResponse


def _claim(**overrides: Any) -> Claim:
    base: Claim = {
        "category": "interview_process",
        "claim_text": "Candidates go through a recruiter screen then a technical round.",
        "source_url": "https://example.com/a",
        "source_title": "Glassdoor",
        "confidence": "high",
    }
    return {**base, **overrides}  # type: ignore[typeddict-item]


async def test_returns_none_when_there_are_no_interview_process_claims() -> None:
    claims = [_claim(category="culture_and_values")]

    result = await synthesize_interview_process_model(
        "Acme", claims, llm_api_key="key", llm_model="model", llm_base_url=None
    )

    assert result is None


async def test_returns_none_for_an_empty_claims_list() -> None:
    result = await synthesize_interview_process_model(
        "Acme", [], llm_api_key="key", llm_model="model", llm_base_url=None
    )
    assert result is None


async def test_only_interview_process_claims_reach_the_prompt() -> None:
    claims = [
        _claim(claim_text="Real interview fact."),
        _claim(category="funding_and_financial_health", claim_text="Raised a Series B."),
    ]
    seen_prompts: list[str] = []

    async def fake_generate(**kwargs: Any) -> LLMResponse:
        seen_prompts.append(kwargs["user_prompt"])
        return LLMResponse(content=json.dumps({"rounds": [], "typical_topics": []}))

    await synthesize_interview_process_model(
        "Acme",
        claims,
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert "Real interview fact." in seen_prompts[0]
    assert "Series B" not in seen_prompts[0]


async def test_parses_a_full_process_model() -> None:
    claims = [_claim()]

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                {
                    "rounds": [
                        {
                            "name": "Recruiter screen",
                            "format": "30 min call",
                            "focus": "background",
                        },
                        {"name": "Technical interview", "format": "", "focus": "coding"},
                    ],
                    "typical_topics": ["system design", "Python"],
                    "difficulty_signal": "medium",
                    "values_signals": ["ownership"],
                }
            )
        )

    result = await synthesize_interview_process_model(
        "Acme",
        claims,
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result is not None
    assert result["company_name"] == "Acme"
    assert len(result["rounds"]) == 2
    assert result["rounds"][0] == {
        "name": "Recruiter screen",
        "format": "30 min call",
        "focus": "background",
    }
    # An empty format/focus string is omitted, not stored as a blank value.
    assert "format" not in result["rounds"][1]
    assert result["typical_topics"] == ["system design", "Python"]
    assert result["difficulty_signal"] == "medium"
    assert result["values_signals"] == ["ownership"]


async def test_confidence_is_the_lowest_among_the_source_claims_not_llm_reported() -> None:
    claims = [_claim(confidence="high"), _claim(confidence="low")]

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps({"rounds": [], "typical_topics": []}))

    result = await synthesize_interview_process_model(
        "Acme",
        claims,
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result is not None
    assert result["confidence"] == "low"


async def test_an_unrecognized_difficulty_value_defaults_to_unknown() -> None:
    claims = [_claim()]

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                {"rounds": [], "typical_topics": [], "difficulty_signal": "extremely brutal"}
            )
        )

    result = await synthesize_interview_process_model(
        "Acme",
        claims,
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result is not None
    assert result["difficulty_signal"] == "unknown"


async def test_a_round_with_no_name_is_dropped() -> None:
    claims = [_claim()]

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps({"rounds": [{"format": "call"}], "typical_topics": []})
        )

    result = await synthesize_interview_process_model(
        "Acme",
        claims,
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result is not None
    assert result["rounds"] == []


async def test_malformed_json_returns_none_not_a_guessed_model() -> None:
    claims = [_claim()]

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content="not json at all")

    result = await synthesize_interview_process_model(
        "Acme",
        claims,
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result is None


async def test_strips_markdown_code_fences() -> None:
    claims = [_claim()]

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        payload = json.dumps({"rounds": [], "typical_topics": ["Python"]})
        return LLMResponse(content=f"```json\n{payload}\n```")

    result = await synthesize_interview_process_model(
        "Acme",
        claims,
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result is not None
    assert result["typical_topics"] == ["Python"]
