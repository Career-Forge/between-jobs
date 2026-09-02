"""Tests for Job Finder P6a (live-search-track.md's own P6 scoping) --
the batch fit-scorer's profile summarizer, 4-strategy parse cascade, and
the deterministic weighted-composite/applicability logic. Ported from
n8n's real `JobScorer` -> `Parse Scorer Output` node chain."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from between_jobs.api.job_fit_scoring import (
    _DEFAULT_SYSTEM_PROMPT,
    _SYSTEM_PROMPT_OVERRIDE_ENV_VAR,
    ScoredJob,
    _load_system_prompt,
    score_jobs,
    summarize_profile,
)
from between_jobs.api.llm_client import LLMResponse
from between_jobs.api.profile import (
    Experience,
    Location,
    Personal,
    Project,
    ResumeTemplate,
    Skills,
)
from between_jobs.api.search_providers import SearchResult


def _profile(**overrides: Any) -> ResumeTemplate:
    base: dict[str, Any] = {
        "personal": Personal(
            name="Jane Doe",
            headline="Backend Engineer",
            location=Location(city="New York", region="NY", country="USA"),
        ),
        "summary_bullets": ["Built scalable backend systems."],
        "experience": [
            Experience(
                title="Software Engineer",
                company="Acme",
                start_date="2020-01",
                end_date="2023-01",
                bullets=["Shipped a payments pipeline."],
            )
        ],
        "skills": Skills(programming=["Python", "Go"], ai_ml=["PyTorch"]),
    }
    base.update(overrides)
    return ResumeTemplate(**base)


def _result(**overrides: Any) -> SearchResult:
    base: dict[str, Any] = {
        "provider": "serper",
        "title": "Backend Engineer",
        "company": "Acme",
        "location": "New York, NY",
        "remote": None,
        "apply_url": "https://boards.greenhouse.io/acme/jobs/1",
        "snippet": "Build backend systems in Python.",
        "posted_at": None,
        "source_tier": 1.0,
    }
    base.update(overrides)
    return SearchResult(**base)


def _results(n: int) -> list[SearchResult]:
    return [
        _result(apply_url=f"https://boards.greenhouse.io/acme/jobs/{i}", title=f"Role {i}")
        for i in range(n)
    ]


# ── summarize_profile ──────────────────────────────────────────────────────


def test_summarize_profile_includes_name_headline_and_location() -> None:
    summary = summarize_profile(_profile())
    assert "CANDIDATE: Jane Doe" in summary
    assert "HEADLINE: Backend Engineer" in summary
    assert "LOCATION: New York, NY, USA" in summary


def test_summarize_profile_includes_experience_bullets() -> None:
    summary = summarize_profile(_profile())
    assert "Software Engineer @ Acme" in summary
    assert "Shipped a payments pipeline." in summary


def test_summarize_profile_dedupes_skills_across_categories() -> None:
    profile = _profile(skills=Skills(programming=["Python", "Go"], ai_ml=["Python", "PyTorch"]))
    summary = summarize_profile(profile)
    skills_line = next(line for line in summary.splitlines() if line.startswith("SKILLS:"))
    assert skills_line.count("Python") == 1


def test_summarize_profile_handles_a_bare_minimum_profile() -> None:
    summary = summarize_profile(ResumeTemplate(personal=Personal(name="Jane Doe")))
    assert "CANDIDATE: Jane Doe" in summary


def test_summarize_profile_caps_at_6000_chars() -> None:
    profile = _profile(
        experience=[
            Experience(
                title=f"Role {i}",
                company="Acme",
                start_date="2020-01",
                end_date="2021-01",
                bullets=["A very long bullet point about accomplishments." * 5],
            )
            for i in range(20)
        ]
    )
    assert len(summarize_profile(profile)) <= 6000


def test_summarize_profile_includes_projects() -> None:
    profile = _profile(projects=[Project(name="RAG Pipeline", tech=["Python", "pgvector"])])
    summary = summarize_profile(profile)
    assert "RAG Pipeline [Python, pgvector]" in summary


# ── score_jobs: batching ────────────────────────────────────────────────


async def test_score_jobs_returns_empty_when_no_results() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        raise AssertionError("should never be called with zero results")

    scored, unscored, strategy = await score_jobs(
        profile=_profile(),
        results=[],
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )
    assert scored == []
    assert unscored == []
    assert strategy == "none"


async def test_score_jobs_caps_the_batch_at_30_and_returns_the_rest_unscored() -> None:
    async def fake_generate(**kwargs: Any) -> LLMResponse:
        payload = json.loads(kwargs["user_prompt"])
        assert len(payload["jobs"]) == 30
        scored = [
            {"job_id": j["job_id"], "fit_score": 7, "one_liner": "Good fit"}
            for j in payload["jobs"]
        ]
        return LLMResponse(content=json.dumps({"scored": scored}))

    results = _results(45)
    scored, unscored, _strategy = await score_jobs(
        profile=_profile(),
        results=results,
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )
    assert len(scored) == 30
    assert len(unscored) == 15
    assert unscored == results[30:]


async def test_score_jobs_drops_a_scored_item_whose_job_id_is_unrecognized() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                {"scored": [{"job_id": "https://not-a-real-job.example", "fit_score": 8}]}
            )
        )

    scored, _unscored, _strategy = await score_jobs(
        profile=_profile(),
        results=_results(1),
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )
    assert scored == []


# ── parse cascade ──────────────────────────────────────────────────────────


async def test_score_jobs_direct_json_strategy() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                {
                    "scored": [
                        {
                            "job_id": "https://boards.greenhouse.io/acme/jobs/0",
                            "fit_score": 8,
                            "one_liner": "Strong fit",
                        }
                    ]
                }
            )
        )

    scored, _unscored, strategy = await score_jobs(
        profile=_profile(),
        results=_results(1),
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )
    assert strategy == "direct"
    assert scored[0].fit_score == 8
    assert scored[0].one_liner == "Strong fit"


async def test_score_jobs_markdown_fence_strategy() -> None:
    payload = json.dumps(
        {"scored": [{"job_id": "https://boards.greenhouse.io/acme/jobs/0", "fit_score": 6}]}
    )

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=f"Here you go:\n```json\n{payload}\n```")

    scored, _unscored, strategy = await score_jobs(
        profile=_profile(),
        results=_results(1),
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )
    assert strategy == "markdown_fence"
    assert scored[0].fit_score == 6


async def test_score_jobs_regex_object_strategy_recovers_from_leading_prose() -> None:
    payload = json.dumps(
        {"scored": [{"job_id": "https://boards.greenhouse.io/acme/jobs/0", "fit_score": 5}]}
    )

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=f"Sure! {payload} Hope that helps.")

    scored, _unscored, strategy = await score_jobs(
        profile=_profile(),
        results=_results(1),
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )
    assert strategy == "regex_object"
    assert scored[0].fit_score == 5


async def test_score_jobs_item_regex_strategy_recovers_from_broken_json() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=(
                '{"scored": [{"job_id": "https://boards.greenhouse.io/acme/jobs/0", '
                '"fit_score": 7, "one_liner": "Good fit"}'  # missing outer ]/} -- invalid JSON
            )
        )

    scored, _unscored, strategy = await score_jobs(
        profile=_profile(),
        results=_results(1),
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )
    assert strategy == "item_regex"
    assert scored[0].fit_score == 7
    assert scored[0].one_liner == "Good fit"


async def test_score_jobs_neutral_fallback_never_fails() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content="I cannot help with that request.")

    scored, _unscored, strategy = await score_jobs(
        profile=_profile(),
        results=_results(2),
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )
    assert strategy == "neutral_fallback"
    assert len(scored) == 2
    assert all(s.fit_score == 5 for s in scored)


async def test_score_jobs_fit_score_is_clamped_to_0_10() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                {
                    "scored": [
                        {
                            "job_id": "https://boards.greenhouse.io/acme/jobs/0",
                            "fit_score": 99,
                        }
                    ]
                }
            )
        )

    scored, _unscored, _strategy = await score_jobs(
        profile=_profile(),
        results=_results(1),
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )
    assert scored[0].fit_score == 10


# ── composite / applicability ───────────────────────────────────────────


def _generate_returning(item: dict[str, Any]) -> Any:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps({"scored": [item]}))

    return fake_generate


async def test_composite_uses_fit_score_x10_as_base_when_no_subscores_given() -> None:
    item = {"job_id": "https://boards.greenhouse.io/acme/jobs/0", "fit_score": 7}
    scored, _unscored, _strategy = await score_jobs(
        profile=_profile(),
        results=_results(1),
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=_generate_returning(item),
    )
    sub = scored[0].sub_scores
    assert sub.skills == 70
    assert sub.experience == 70


async def test_composite_prefers_explicit_subscores_over_fit_score_base() -> None:
    item = {
        "job_id": "https://boards.greenhouse.io/acme/jobs/0",
        "fit_score": 5,
        "skills_score": 90,
        "experience_score": 40,
    }
    scored, _unscored, _strategy = await score_jobs(
        profile=_profile(),
        results=_results(1),
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=_generate_returning(item),
    )
    sub = scored[0].sub_scores
    assert sub.skills == 90
    assert sub.experience == 40


async def test_composite_company_health_inapplicable_when_lookup_returns_none() -> None:
    item = {"job_id": "https://boards.greenhouse.io/acme/jobs/0", "fit_score": 8}
    scored, _unscored, _strategy = await score_jobs(
        profile=_profile(),
        results=_results(1),
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=_generate_returning(item),
        company_health_lookup=lambda _company: None,
    )
    assert "company_health" in scored[0].inapplicable_dims


async def test_composite_company_health_applicable_and_scaled_when_lookup_hits() -> None:
    item = {"job_id": "https://boards.greenhouse.io/acme/jobs/0", "fit_score": 8}
    scored, _unscored, _strategy = await score_jobs(
        profile=_profile(),
        results=_results(1),
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=_generate_returning(item),
        company_health_lookup=lambda _company: 1.0,
    )
    assert "company_health" not in scored[0].inapplicable_dims
    assert scored[0].sub_scores.company_health == 100


async def test_composite_compensation_inapplicable_without_a_salary_min() -> None:
    item = {"job_id": "https://boards.greenhouse.io/acme/jobs/0", "fit_score": 8}
    scored, _unscored, _strategy = await score_jobs(
        profile=_profile(),
        results=[_result(salary_min=None, apply_url="https://boards.greenhouse.io/acme/jobs/0")],
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=_generate_returning(item),
    )
    assert "compensation" in scored[0].inapplicable_dims


async def test_composite_compensation_applicable_when_job_states_a_salary_min() -> None:
    item = {"job_id": "https://boards.greenhouse.io/acme/jobs/0", "fit_score": 8}
    scored, _unscored, _strategy = await score_jobs(
        profile=_profile(),
        results=[_result(salary_min=150000, apply_url="https://boards.greenhouse.io/acme/jobs/0")],
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=_generate_returning(item),
    )
    assert "compensation" not in scored[0].inapplicable_dims


async def test_composite_location_uses_verified_signal_when_location_requested() -> None:
    item = {"job_id": "https://boards.greenhouse.io/acme/jobs/0", "fit_score": 8}
    scored, _unscored, _strategy = await score_jobs(
        profile=_profile(),
        results=[
            _result(location_verified=True, apply_url="https://boards.greenhouse.io/acme/jobs/0")
        ],
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=_generate_returning(item),
        location_requested=True,
    )
    assert scored[0].sub_scores.location == 100


async def test_composite_location_unverified_but_requested_scores_35() -> None:
    item = {"job_id": "https://boards.greenhouse.io/acme/jobs/0", "fit_score": 8}
    scored, _unscored, _strategy = await score_jobs(
        profile=_profile(),
        results=[
            _result(location_verified=None, apply_url="https://boards.greenhouse.io/acme/jobs/0")
        ],
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=_generate_returning(item),
        location_requested=True,
    )
    assert scored[0].sub_scores.location == 35


async def test_composite_location_falls_back_to_llm_guess_when_not_requested() -> None:
    item = {
        "job_id": "https://boards.greenhouse.io/acme/jobs/0",
        "fit_score": 8,
        "location_match": "mismatch",
    }
    scored, _unscored, _strategy = await score_jobs(
        profile=_profile(),
        results=[
            _result(location_verified=None, apply_url="https://boards.greenhouse.io/acme/jobs/0")
        ],
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=_generate_returning(item),
        location_requested=False,
    )
    assert scored[0].sub_scores.location == 20


async def test_composite_bottleneck_names_the_lowest_applicable_subscore() -> None:
    item = {
        "job_id": "https://boards.greenhouse.io/acme/jobs/0",
        "fit_score": 8,
        "skills_score": 90,
        "experience_score": 10,
    }
    scored, _unscored, _strategy = await score_jobs(
        profile=_profile(),
        results=_results(1),
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=_generate_returning(item),
    )
    assert scored[0].bottleneck == "experience"


async def test_composite_bins_score100_into_strong_good_mixed_poor() -> None:
    async def scored_for(skills: int, experience: int) -> ScoredJob:
        item = {
            "job_id": "https://boards.greenhouse.io/acme/jobs/0",
            "fit_score": 8,
            "skills_score": skills,
            "experience_score": experience,
        }
        scored, _unscored, _strategy = await score_jobs(
            profile=_profile(),
            results=_results(1),
            llm_api_key="key",
            llm_model="model",
            llm_base_url=None,
            generate=_generate_returning(item),
        )
        return scored[0]

    assert (await scored_for(90, 90)).bin == "Strong"
    assert (await scored_for(60, 60)).bin == "Good"
    assert (await scored_for(45, 45)).bin == "Mixed"
    assert (await scored_for(10, 10)).bin == "Poor"


async def test_score_jobs_sorts_by_score100_descending() -> None:
    async def fake_generate(**kwargs: Any) -> LLMResponse:
        payload = json.loads(kwargs["user_prompt"])
        scored = [
            {"job_id": j["job_id"], "fit_score": 3 if i == 0 else 9}
            for i, j in enumerate(payload["jobs"])
        ]
        return LLMResponse(content=json.dumps({"scored": scored}))

    scored, _unscored, _strategy = await score_jobs(
        profile=_profile(),
        results=_results(2),
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )
    assert scored[0].fit_score == 9
    assert scored[1].fit_score == 3


async def test_score_jobs_tie_breaks_equal_scores_by_location_match() -> None:
    async def fake_generate(**kwargs: Any) -> LLMResponse:
        payload = json.loads(kwargs["user_prompt"])
        matches = ["mismatch", "match"]
        scored = [
            {"job_id": j["job_id"], "fit_score": 5, "location_match": matches[i]}
            for i, j in enumerate(payload["jobs"])
        ]
        return LLMResponse(content=json.dumps({"scored": scored}))

    results = _results(2)
    scored, _unscored, _strategy = await score_jobs(
        profile=_profile(),
        results=results,
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )
    assert scored[0].location_match == "match"
    assert scored[1].location_match == "mismatch"


# ── _load_system_prompt: the hosted-override seam ───────────────────────


def test_load_system_prompt_returns_the_default_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_SYSTEM_PROMPT_OVERRIDE_ENV_VAR, raising=False)
    assert _load_system_prompt() == _DEFAULT_SYSTEM_PROMPT


def test_load_system_prompt_reads_a_real_override_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    override_path = tmp_path / "hosted-prompt.md"
    override_path.write_text("A hosted-only, further-tuned prompt.")
    monkeypatch.setenv(_SYSTEM_PROMPT_OVERRIDE_ENV_VAR, str(override_path))

    assert _load_system_prompt() == "A hosted-only, further-tuned prompt."


def test_load_system_prompt_fails_open_when_the_file_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(_SYSTEM_PROMPT_OVERRIDE_ENV_VAR, str(tmp_path / "does-not-exist.md"))
    assert _load_system_prompt() == _DEFAULT_SYSTEM_PROMPT


def test_load_system_prompt_fails_open_when_the_file_is_blank(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    override_path = tmp_path / "blank.md"
    override_path.write_text("   \n")
    monkeypatch.setenv(_SYSTEM_PROMPT_OVERRIDE_ENV_VAR, str(override_path))

    assert _load_system_prompt() == _DEFAULT_SYSTEM_PROMPT


async def test_score_jobs_passes_the_loaded_system_prompt_to_the_llm_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    override_path = tmp_path / "hosted-prompt.md"
    override_path.write_text("A hosted-only, further-tuned prompt.")
    monkeypatch.setenv(_SYSTEM_PROMPT_OVERRIDE_ENV_VAR, str(override_path))

    captured: dict[str, Any] = {}

    async def fake_generate(**kwargs: Any) -> LLMResponse:
        captured["system_prompt"] = kwargs["system_prompt"]
        return LLMResponse(content=json.dumps({"scored": []}))

    await score_jobs(
        profile=_profile(),
        results=_results(1),
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert captured["system_prompt"] == "A hosted-only, further-tuned prompt."
