"""Tests for the practice-session engine (InterviewForge R2,
interviewforge-v1.md)."""

from __future__ import annotations

import json
from typing import Any

from between_jobs.api.interview_practice import (
    AnswerFeedback,
    PracticeQuestion,
    ScoredAnswer,
    StarCoverage,
    build_practice_context,
    build_session_report,
    generate_practice_questions,
    score_answer,
)
from between_jobs.api.interview_registry import InterviewProcessModel
from between_jobs.api.llm_client import LLMResponse

_REGISTRY_ENTRY: InterviewProcessModel = {
    "company_name": "Acme",
    "rounds": [{"name": "Technical interview", "format": "live coding", "focus": "algorithms"}],
    "typical_topics": ["system design"],
    "difficulty_signal": "medium",
    "values_signals": ["ownership"],
    "confidence": "high",
}


def _question(**overrides: Any) -> PracticeQuestion:
    base: PracticeQuestion = {
        "question": "Tell me about a time you led a project.",
        "type": "behavioral",
        "target_skill": "leadership",
        "grounded_in": None,
    }
    return {**base, **overrides}  # type: ignore[typeddict-item]


def test_build_practice_context_carries_every_field_through() -> None:
    context = build_practice_context(
        company_name="Acme",
        job_title="Staff Engineer",
        job_description="Build things.",
        resume_evidence="Led a team of 5.",
        registry_entry=_REGISTRY_ENTRY,
    )
    assert context["company_name"] == "Acme"
    assert context["registry_entry"] == _REGISTRY_ENTRY


# ── generate_practice_questions ──────────────────────────────────────────


def _context(registry_entry: InterviewProcessModel | None = None) -> Any:
    return build_practice_context(
        company_name="Acme",
        job_title="Staff Engineer",
        job_description="Build things.",
        resume_evidence="Led a team of 5. Built a Python service.",
        registry_entry=registry_entry,
    )


async def test_generate_practice_questions_parses_a_full_response() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                {
                    "questions": [
                        {
                            "question": "Describe a time you owned a project end to end.",
                            "type": "behavioral",
                            "target_skill": "ownership",
                            "grounded_in": "Technical interview",
                        }
                    ]
                }
            )
        )

    questions = await generate_practice_questions(
        _context(_REGISTRY_ENTRY),
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert len(questions) == 1
    assert questions[0]["type"] == "behavioral"
    assert questions[0]["grounded_in"] == "Technical interview"


async def test_generate_practice_questions_defaults_an_invalid_type_to_behavioral() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps({"questions": [{"question": "Q1", "type": "not-a-real-type"}]})
        )

    questions = await generate_practice_questions(
        _context(), llm_api_key="key", llm_model="model", llm_base_url=None, generate=fake_generate
    )

    assert questions[0]["type"] == "behavioral"


async def test_generate_practice_questions_drops_a_question_with_no_text() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps({"questions": [{"type": "behavioral"}]}))

    questions = await generate_practice_questions(
        _context(), llm_api_key="key", llm_model="model", llm_base_url=None, generate=fake_generate
    )

    assert questions == []


async def test_generate_practice_questions_defaults_grounded_in_to_none() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps({"questions": [{"question": "Q1", "type": "technical"}]})
        )

    questions = await generate_practice_questions(
        _context(), llm_api_key="key", llm_model="model", llm_base_url=None, generate=fake_generate
    )

    assert questions[0]["grounded_in"] is None


async def test_generate_practice_questions_returns_empty_list_on_malformed_json() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content="not json at all")

    questions = await generate_practice_questions(
        _context(), llm_api_key="key", llm_model="model", llm_base_url=None, generate=fake_generate
    )

    assert questions == []


async def test_generate_practice_questions_strips_markdown_code_fences() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        payload = json.dumps({"questions": [{"question": "Q1", "type": "situational"}]})
        return LLMResponse(content=f"```json\n{payload}\n```")

    questions = await generate_practice_questions(
        _context(), llm_api_key="key", llm_model="model", llm_base_url=None, generate=fake_generate
    )

    assert len(questions) == 1


async def test_generate_practice_questions_sends_null_registry_when_company_blind() -> None:
    seen_prompts: list[str] = []

    async def fake_generate(**kwargs: Any) -> LLMResponse:
        seen_prompts.append(kwargs["user_prompt"])
        return LLMResponse(content=json.dumps({"questions": []}))

    await generate_practice_questions(
        _context(None),
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    payload = json.loads(seen_prompts[0])
    assert payload["registry_entry"] is None


async def test_generate_practice_questions_sends_the_real_registry_entry_when_present() -> None:
    seen_prompts: list[str] = []

    async def fake_generate(**kwargs: Any) -> LLMResponse:
        seen_prompts.append(kwargs["user_prompt"])
        return LLMResponse(content=json.dumps({"questions": []}))

    await generate_practice_questions(
        _context(_REGISTRY_ENTRY),
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    payload = json.loads(seen_prompts[0])
    assert payload["registry_entry"]["company_name"] == "Acme"
    assert payload["registry_entry"]["rounds"][0]["name"] == "Technical interview"


# ── score_answer ──────────────────────────────────────────────────────────


async def test_score_answer_parses_a_full_response() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                {
                    "score": 7,
                    "structure_feedback": "Well organized.",
                    "specificity_feedback": "Could use more numbers.",
                    "star_coverage": {
                        "situation": True,
                        "task": True,
                        "action": True,
                        "result": False,
                    },
                    "improved_answer": "A tighter version of the same answer.",
                }
            )
        )

    feedback = await score_answer(
        _question(),
        "My answer.",
        "Resume evidence.",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert feedback is not None
    assert feedback["score"] == 7
    assert feedback["star_coverage"] == {
        "situation": True,
        "task": True,
        "action": True,
        "result": False,
    }
    assert feedback["improved_answer"] == "A tighter version of the same answer."


async def test_score_answer_clamps_an_out_of_range_score() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps({"score": 15, "star_coverage": {}}))

    feedback = await score_answer(
        _question(),
        "My answer.",
        "",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert feedback is not None
    assert feedback["score"] == 10


async def test_score_answer_clamps_a_negative_score() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps({"score": -3, "star_coverage": {}}))

    feedback = await score_answer(
        _question(),
        "My answer.",
        "",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert feedback is not None
    assert feedback["score"] == 0


async def test_score_answer_returns_none_when_score_is_missing_not_a_guessed_default() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps({"star_coverage": {}}))

    feedback = await score_answer(
        _question(),
        "My answer.",
        "",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert feedback is None


async def test_score_answer_returns_none_when_score_is_not_a_number() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps({"score": "high", "star_coverage": {}}))

    feedback = await score_answer(
        _question(),
        "My answer.",
        "",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert feedback is None


async def test_score_answer_defaults_missing_star_fields_to_false() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps({"score": 5, "star_coverage": {"situation": True}}))

    feedback = await score_answer(
        _question(),
        "My answer.",
        "",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert feedback is not None
    assert feedback["star_coverage"] == {
        "situation": True,
        "task": False,
        "action": False,
        "result": False,
    }


async def test_score_answer_omits_improved_answer_when_absent() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps({"score": 5, "star_coverage": {}}))

    feedback = await score_answer(
        _question(),
        "My answer.",
        "",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert feedback is not None
    assert "improved_answer" not in feedback


async def test_score_answer_returns_none_on_malformed_json() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content="not json at all")

    feedback = await score_answer(
        _question(),
        "My answer.",
        "",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert feedback is None


async def test_score_answer_strips_markdown_code_fences() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        payload = json.dumps({"score": 8, "star_coverage": {}})
        return LLMResponse(content=f"```json\n{payload}\n```")

    feedback = await score_answer(
        _question(),
        "My answer.",
        "",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert feedback is not None
    assert feedback["score"] == 8


# ── build_session_report ──────────────────────────────────────────────────


def _star(**overrides: Any) -> StarCoverage:
    base: StarCoverage = {"situation": False, "task": False, "action": False, "result": False}
    return {**base, **overrides}  # type: ignore[typeddict-item]


def _scored(score: int, **star_overrides: Any) -> ScoredAnswer:
    feedback: AnswerFeedback = {
        "score": score,
        "structure_feedback": "",
        "specificity_feedback": "",
        "star_coverage": _star(**star_overrides),
    }
    return ScoredAnswer(question=_question(), answer_text="answer", feedback=feedback)


def test_build_session_report_with_no_scored_answers() -> None:
    report = build_session_report(5, [])
    assert report == {
        "question_count": 5,
        "answered_count": 0,
        "average_score": None,
        "star_coverage_rate": None,
    }


def test_build_session_report_averages_scores_and_star_coverage_rate() -> None:
    scored = [
        _scored(8, situation=True, task=True, action=True, result=True),
        _scored(4),
    ]
    report = build_session_report(5, scored)

    assert report["question_count"] == 5
    assert report["answered_count"] == 2
    assert report["average_score"] == 6.0
    assert report["star_coverage_rate"] == 0.5


def test_build_session_report_rounds_to_two_decimal_places() -> None:
    scored = [_scored(7), _scored(8), _scored(9)]
    report = build_session_report(3, scored)

    assert report["average_score"] == 8.0
    assert report["answered_count"] == 3
