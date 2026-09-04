"""Tests for the positioning brief (outreach-v2-search-first.md Phase I)
-- the strength/gap candidate builders, the deterministic re-grounding of
LLM citations, the rubric check, and the capped retry loop.
"""

from __future__ import annotations

import json
from typing import Any

from between_jobs.api.llm_client import LLMResponse
from between_jobs.api.positioning_brief import (
    PositioningBrief,
    check_rubric,
    fit_context_line,
    gap_candidates,
    generate_positioning_brief,
    strength_candidates,
)
from between_jobs.api.tailor import ClusterCoverage

_SKILLS = [
    {"skill": "LLM Orchestration", "requested_as": "LangChain", "state": "verified"},
    {"skill": "Kubernetes", "requested_as": "Kubernetes", "state": "supported"},
    {"skill": "Rust", "requested_as": "Rust", "state": "unsupported"},
    {"skill": "GraphQL", "requested_as": "GraphQL", "state": "adjacent"},
]

_COVERAGE: list[ClusterCoverage] = [
    ClusterCoverage(
        name="Distributed Systems",
        priority="must_have",
        keywords=["distributed"],
        coverage_count=2,
        matched_fact_ids=["fact-1"],
    ),
    ClusterCoverage(
        name="Kafka",
        priority="must_have",
        keywords=["kafka"],
        coverage_count=0,
        matched_fact_ids=[],
    ),
    ClusterCoverage(
        name="Nice To Have Cloud",
        priority="nice_to_have",
        keywords=["cloud"],
        coverage_count=0,
        matched_fact_ids=[],
    ),
]


def test_strength_candidates_includes_verified_supported_and_covered_must_haves() -> None:
    candidates = strength_candidates(_SKILLS, _COVERAGE)
    assert "LLM Orchestration" in candidates
    assert "Kubernetes" in candidates
    assert "Distributed Systems" in candidates


def test_strength_candidates_excludes_unsupported_and_adjacent_and_uncovered() -> None:
    candidates = strength_candidates(_SKILLS, _COVERAGE)
    assert "Rust" not in candidates
    assert "GraphQL" not in candidates
    assert "Kafka" not in candidates


def test_gap_candidates_includes_unsupported_skills_and_zero_match_must_haves() -> None:
    candidates = gap_candidates(_SKILLS, _COVERAGE)
    assert "Rust" in candidates
    assert "Kafka" in candidates


def test_gap_candidates_excludes_adjacent_and_verified_supported_and_nice_to_have() -> None:
    """ "adjacent" is deliberately excluded from both buckets (neither a
    clean strength nor a clean gap, per skills.py's own docstring), and a
    nice_to_have's zero-match doesn't count as a gap -- only must_have
    clusters do, matching tailor.pick_gap_interview_questions' own rule."""
    candidates = gap_candidates(_SKILLS, _COVERAGE)
    assert "GraphQL" not in candidates
    assert "LLM Orchestration" not in candidates
    assert "Nice To Have Cloud" not in candidates


def test_fit_context_line_handles_missing_fit() -> None:
    assert "no Honest Floor read" in fit_context_line(None)


def test_fit_context_line_summarizes_a_real_fit() -> None:
    line = fit_context_line({"recommendation": "Caution", "overall_score": 6.2})
    assert "Caution" in line
    assert "6.2" in line


def _brief(**overrides: Any) -> PositioningBrief:
    base = PositioningBrief(
        lead_with="You've verifiably built with LLM Orchestration tooling.",
        lead_with_citation="LLM Orchestration",
        gap_that_matters="Kafka experience isn't demonstrated anywhere in your history.",
        gap_citation="Kafka",
        recommended_project="Build a small event-driven service using Kafka to close that gap.",
    )
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def test_check_rubric_passes_a_clean_brief() -> None:
    assert check_rubric(_brief()) == []


def test_check_rubric_flags_an_outcome_promise() -> None:
    warnings = check_rubric(_brief(recommended_project="Do this and you'll get the offer."))
    assert any("outcome-promise" in w for w in warnings)


def test_check_rubric_flags_other_real_promise_phrasings() -> None:
    """Regression coverage for phrasings an adversarial review found
    slipped past the original, narrower list."""
    assert check_rubric(_brief(gap_that_matters="This will land you the role.")) != []
    assert check_rubric(_brief(lead_with="This project will get you the offer.")) != []


async def test_generate_positioning_brief_refuses_with_no_strengths() -> None:
    brief, warnings = await generate_positioning_brief(
        company="Acme",
        role_title="Staff Engineer",
        coverage=[],
        skills=[],
        company_intel_claims=[],
        fit=None,
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
    )
    assert brief is None
    assert "lead with" in warnings[0]


async def test_generate_positioning_brief_refuses_with_no_gaps() -> None:
    all_verified = [
        {"skill": "LLM Orchestration", "requested_as": "LangChain", "state": "verified"}
    ]
    brief, warnings = await generate_positioning_brief(
        company="Acme",
        role_title="Staff Engineer",
        coverage=[],
        skills=all_verified,
        company_intel_claims=[],
        fit=None,
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
    )
    assert brief is None
    assert "no honest gap" in warnings[0]


async def test_generate_positioning_brief_succeeds_on_first_attempt() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps(dict(_brief())))

    brief, warnings = await generate_positioning_brief(
        company="Acme",
        role_title="Staff Engineer",
        coverage=_COVERAGE,
        skills=_SKILLS,
        company_intel_claims=[],
        fit=None,
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )
    assert brief is not None
    assert warnings == []
    assert brief["lead_with_citation"] == "LLM Orchestration"
    assert brief["gap_citation"] == "Kafka"


async def test_generate_positioning_brief_drops_a_fabricated_lead_with_citation() -> None:
    """The deterministic re-grounding this module exists for: a citation
    that isn't a real member of the candidate list must be rejected, even
    though the JSON itself parses cleanly."""

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps(dict(_brief(lead_with_citation="Made Up Skill"))))

    brief, warnings = await generate_positioning_brief(
        company="Acme",
        role_title="Staff Engineer",
        coverage=_COVERAGE,
        skills=_SKILLS,
        company_intel_claims=[],
        fit=None,
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
        max_attempts=1,
    )
    assert brief is None
    assert warnings != []


async def test_generate_positioning_brief_drops_a_fabricated_gap_citation() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps(dict(_brief(gap_citation="Invented Requirement"))))

    brief, warnings = await generate_positioning_brief(
        company="Acme",
        role_title="Staff Engineer",
        coverage=_COVERAGE,
        skills=_SKILLS,
        company_intel_claims=[],
        fit=None,
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
        max_attempts=1,
    )
    assert brief is None
    assert warnings != []


async def test_generate_positioning_brief_retries_once_on_a_banned_promise_then_succeeds() -> None:
    calls = 0

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return LLMResponse(
                content=json.dumps(dict(_brief(recommended_project="This is a guaranteed way in.")))
            )
        return LLMResponse(content=json.dumps(dict(_brief())))

    brief, warnings = await generate_positioning_brief(
        company="Acme",
        role_title="Staff Engineer",
        coverage=_COVERAGE,
        skills=_SKILLS,
        company_intel_claims=[],
        fit=None,
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )
    assert calls == 2
    assert brief is not None
    assert warnings == []


async def test_generate_positioning_brief_caps_retries_at_two_attempts() -> None:
    calls = 0

    async def always_bad_generate(**_kwargs: Any) -> LLMResponse:
        nonlocal calls
        calls += 1
        return LLMResponse(content="not json")

    brief, warnings = await generate_positioning_brief(
        company="Acme",
        role_title="Staff Engineer",
        coverage=_COVERAGE,
        skills=_SKILLS,
        company_intel_claims=[],
        fit=None,
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=always_bad_generate,
    )
    assert calls == 2
    assert brief is None
    assert warnings != []


async def test_generate_positioning_brief_caps_retries_when_always_rubric_failing() -> None:
    """Mirrors test_generate_outreach_draft_caps_retries_at_two_attempts:
    a parseable, correctly-grounded brief that never passes the rubric
    still returns the best-effort brief (not None) with the rubric
    warnings intact -- the exact path an adversarial review found had no
    test coverage, unlike its sibling module."""
    calls = 0

    async def always_promising_generate(**_kwargs: Any) -> LLMResponse:
        nonlocal calls
        calls += 1
        return LLMResponse(
            content=json.dumps(dict(_brief(recommended_project="This is a guaranteed win.")))
        )

    brief, warnings = await generate_positioning_brief(
        company="Acme",
        role_title="Staff Engineer",
        coverage=_COVERAGE,
        skills=_SKILLS,
        company_intel_claims=[],
        fit=None,
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=always_promising_generate,
    )
    assert calls == 2
    assert brief is not None
    assert warnings != []


async def test_generate_positioning_brief_includes_company_intel_and_fit_context() -> None:
    """Not a citation source (see check below), but real context the
    prompt should carry -- proven by inspecting the actual user_prompt
    sent, not just the returned brief."""
    captured: dict[str, Any] = {}

    async def capturing_generate(**kwargs: Any) -> LLMResponse:
        captured.update(kwargs)
        return LLMResponse(content=json.dumps(dict(_brief())))

    await generate_positioning_brief(
        company="Acme",
        role_title="Staff Engineer",
        coverage=_COVERAGE,
        skills=_SKILLS,
        company_intel_claims=[{"claim_text": "Acme builds its platform on WidgetCore."}],
        fit={"recommendation": "Apply", "overall_score": 8.1},
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=capturing_generate,
    )
    assert "WidgetCore" in captured["user_prompt"]
    assert "Apply" in captured["user_prompt"]
