"""Tests for the practice-session engine (InterviewForge R2,
interviewforge-v1.md)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from between_jobs.api.interview_practice import (
    AnswerFeedback,
    PracticeQuestion,
    QuestionType,
    ScoredAnswer,
    StarCoverage,
    build_practice_context,
    build_session_report,
    generate_practice_questions,
    score_answer,
    star_applies,
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


@pytest.mark.parametrize("flag", ["situation", "task", "action", "result"])
async def test_score_answer_keeps_each_star_flag_the_model_marks_true(flag: str) -> None:
    """Every one of the four flags is read from the model's reply, in both states: one set
    true with the other three false must come back exactly so (a parser that hard-coded a flag
    would fail for that flag)."""
    reply = {"situation": False, "task": False, "action": False, "result": False, flag: True}

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps({"score": 6, "star_coverage": reply}))

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
    assert feedback["star_coverage"] == reply


async def test_score_answer_keeps_all_four_star_flags_when_the_model_marks_them_true() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                {
                    "score": 9,
                    "star_coverage": {
                        "situation": True,
                        "task": True,
                        "action": True,
                        "result": True,
                    },
                }
            )
        )

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
        "task": True,
        "action": True,
        "result": True,
    }


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


# ── STAR scope: behavioral and situational questions only ─────────────────


def _typed(question_type: QuestionType, score: int, coverage: StarCoverage | None) -> ScoredAnswer:
    feedback: AnswerFeedback = {
        "score": score,
        "structure_feedback": "",
        "specificity_feedback": "",
        "star_coverage": coverage,
    }
    return ScoredAnswer(
        question=_question(type=question_type), answer_text="answer", feedback=feedback
    )


_FULL: StarCoverage = {"situation": True, "task": True, "action": True, "result": True}
_NONE: StarCoverage = {"situation": False, "task": False, "action": False, "result": False}


def test_star_applies_to_behavioral_and_situational_questions_only() -> None:
    assert star_applies("behavioral")
    assert star_applies("situational")
    assert not star_applies("technical")


def test_technical_answers_are_left_out_of_the_star_rate_numerator_and_denominator() -> None:
    answers = [
        _typed("behavioral", 8, _FULL),
        _typed("situational", 6, _NONE),
        # a technical answer with every flag set must not lift the rate, and one with none set
        # must not lower it
        _typed("technical", 9, _FULL),
        _typed("technical", 3, _NONE),
    ]

    report = build_session_report(5, answers)

    assert report["star_coverage_rate"] == 0.5  # 1 of the 2 story answers, not 2 of 4
    assert report["answered_count"] == 4  # the score still counts every answer
    assert report["average_score"] == 6.5


def _all_but(missing: str) -> StarCoverage:
    """Full STAR coverage with exactly one of the four parts absent."""
    return StarCoverage(
        situation=missing != "situation",
        task=missing != "task",
        action=missing != "action",
        result=missing != "result",
    )


@pytest.mark.parametrize("missing", ["situation", "task", "action", "result"])
def test_a_story_answer_missing_any_one_star_part_is_not_full_coverage(missing: str) -> None:
    """The rate counts only answers with ALL FOUR parts: dropping a single one, whichever it is,
    must take an otherwise complete answer out of the numerator."""
    report = build_session_report(5, [_typed("behavioral", 5, _all_but(missing))])

    assert report["star_coverage_rate"] == 0.0


@pytest.mark.parametrize("missing", ["situation", "task", "action", "result"])
def test_one_full_and_one_three_of_four_story_answer_make_half_coverage(missing: str) -> None:
    report = build_session_report(
        5, [_typed("behavioral", 5, _FULL), _typed("situational", 5, _all_but(missing))]
    )

    assert report["star_coverage_rate"] == 0.5


def test_a_session_with_only_technical_answers_has_no_star_rate() -> None:
    report = build_session_report(5, [_typed("technical", 7, _FULL), _typed("technical", 8, None)])

    assert report["star_coverage_rate"] is None
    assert report["average_score"] == 7.5


def test_a_story_answer_with_no_star_data_is_not_counted_as_a_miss() -> None:
    report = build_session_report(
        5, [_typed("behavioral", 5, _FULL), _typed("behavioral", 5, None)]
    )

    assert report["star_coverage_rate"] == 1.0


def test_the_star_rate_rounds_to_two_decimals_over_the_story_answers() -> None:
    answers = [
        _typed("behavioral", 5, _FULL),
        _typed("situational", 5, _NONE),
        _typed("behavioral", 5, _NONE),
        _typed("technical", 5, _FULL),
    ]

    assert build_session_report(5, answers)["star_coverage_rate"] == 0.33


async def test_a_technical_answer_gets_no_star_coverage_even_if_the_model_returns_flags() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                {
                    "score": 8,
                    "structure_feedback": "Clear.",
                    "specificity_feedback": "Precise.",
                    "star_coverage": {
                        "situation": True,
                        "task": True,
                        "action": True,
                        "result": True,
                    },
                }
            )
        )

    feedback = await score_answer(
        _question(type="technical", question="How does a B-tree stay balanced?"),
        "Splits and merges keep every leaf at one depth.",
        "Resume evidence.",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert feedback is not None
    assert feedback["score"] == 8
    assert feedback["star_coverage"] is None


async def test_a_situational_answer_is_still_scored_for_star() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps({"score": 6, "star_coverage": {"situation": True, "task": True}})
        )

    feedback = await score_answer(
        _question(type="situational"),
        "I would first ask what changed.",
        "Resume evidence.",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert feedback is not None
    assert feedback["star_coverage"] == {
        "situation": True,
        "task": True,
        "action": False,
        "result": False,
    }


async def test_the_scoring_prompt_tells_the_model_star_does_not_apply_to_technical_questions() -> (
    None
):
    seen: list[str] = []

    async def fake_generate(**kwargs: Any) -> LLMResponse:
        seen.append(kwargs["system_prompt"])
        seen.append(kwargs["user_prompt"])
        return LLMResponse(content=json.dumps({"score": 5}))

    await score_answer(
        _question(type="technical"),
        "An answer.",
        "Resume evidence.",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert '"technical"' in seen[0] and "star_coverage is not used" in seen[0]
    assert json.loads(seen[1])["question_type"] == "technical"
