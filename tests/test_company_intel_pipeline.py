"""Tests for the company-intel pipeline (Horizon Sprint 5.0) -- Stage 1
(fingerprint), Stage 2 (query plan), Stage 3 (retrieval + graceful
degradation), and Stage 7 (synthesis + deterministic claim validation).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from between_jobs.api.company_intel_pipeline import (
    QueryResults,
    ResearchQuery,
    build_query_plan,
    build_role_fingerprint,
    run_research,
    synthesize_dossier,
)
from between_jobs.api.errors import ApiError
from between_jobs.api.llm_client import LLMResponse
from between_jobs.api.research_clients import SearchHit

_SNAPSHOT = {
    "title": "Staff AI Engineer",
    "company_name": "Acme",
    "location_text": "Remote",
}


def test_build_role_fingerprint_is_deterministic_from_the_snapshot() -> None:
    fingerprint = build_role_fingerprint(_SNAPSHOT)
    assert fingerprint == {"company": "Acme", "title": "Staff AI Engineer", "location": "Remote"}


def test_build_role_fingerprint_handles_missing_location() -> None:
    fingerprint = build_role_fingerprint({"title": "Engineer", "company_name": "Acme"})
    assert fingerprint["location"] is None


def test_build_query_plan_is_bounded_and_company_scoped() -> None:
    fingerprint = build_role_fingerprint(_SNAPSHOT)
    queries = build_query_plan(fingerprint)

    assert len(queries) == 7
    assert all("Acme" in q["query"] for q in queries)
    purposes = {q["purpose"] for q in queries}
    assert purposes == {
        "product_and_mission",
        "funding_and_financial_health",
        "hiring_activity",
        "layoffs_and_risk",
        "culture_and_values",
        "interview_process",
        "relevant_news",
    }
    # None of the person-finding query families from §27's target
    # portfolio -- this pipeline never looks for a named individual.
    assert not any("recruiter" in q["query"] or "hiring manager" in q["query"] for q in queries)


class _FakeHttp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def post(self, url: str, **kwargs: Any) -> None:  # pragma: no cover - unused
        raise AssertionError("real http.post should never be reached in these tests")


_HIT = SearchHit(
    title="Acme raises Series B", url="https://news.example/a", snippet="...", published_at=None
)


async def test_run_research_raises_setup_required_when_no_provider_configured() -> None:
    with pytest.raises(ApiError) as exc_info:
        await run_research(
            _FakeHttp(),  # type: ignore[arg-type]
            build_query_plan(build_role_fingerprint(_SNAPSHOT)),
            you_com_key=None,
            firecrawl_key=None,
        )

    assert exc_info.value.code == "SETUP_REQUIRED"


async def test_run_research_prefers_you_com_when_both_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_you_com(
        _http: Any, *, api_key: str, query: str, count: int = 5
    ) -> list[SearchHit]:
        calls.append("you_com")
        return [_HIT]

    async def fake_firecrawl(
        _http: Any, *, api_key: str, query: str, limit: int = 5
    ) -> list[SearchHit]:
        calls.append("firecrawl")
        return [_HIT]

    monkeypatch.setattr("between_jobs.api.company_intel_pipeline.search_you_com", fake_you_com)
    monkeypatch.setattr("between_jobs.api.company_intel_pipeline.search_firecrawl", fake_firecrawl)

    queries = build_query_plan(build_role_fingerprint(_SNAPSHOT))
    results, providers_used, warnings = await run_research(
        _FakeHttp(),  # type: ignore[arg-type]
        queries,
        you_com_key="yc-key",
        firecrawl_key="fc-key",
    )

    assert providers_used == ["you_com"]
    assert calls == ["you_com"] * len(queries)
    assert warnings == []
    assert len(results) == len(queries)


async def test_run_research_falls_back_to_firecrawl_when_you_com_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_firecrawl(
        _http: Any, *, api_key: str, query: str, limit: int = 5
    ) -> list[SearchHit]:
        return [_HIT]

    monkeypatch.setattr("between_jobs.api.company_intel_pipeline.search_firecrawl", fake_firecrawl)

    results, providers_used, warnings = await run_research(
        _FakeHttp(),  # type: ignore[arg-type]
        build_query_plan(build_role_fingerprint(_SNAPSHOT)),
        you_com_key=None,
        firecrawl_key="fc-key",
    )

    assert providers_used == ["firecrawl"]
    assert warnings == []
    assert all(hits == [_HIT] for _q, hits in results)


async def test_run_research_records_a_warning_on_a_failed_query_but_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def flaky_you_com(
        _http: Any, *, api_key: str, query: str, count: int = 5
    ) -> list[SearchHit]:
        if "layoffs" in query:
            raise ApiError("PROVIDER_UNAVAILABLE", "You.com timed out.", retryable=True)
        return [_HIT]

    monkeypatch.setattr("between_jobs.api.company_intel_pipeline.search_you_com", flaky_you_com)

    results, _providers, warnings = await run_research(
        _FakeHttp(),  # type: ignore[arg-type]
        build_query_plan(build_role_fingerprint(_SNAPSHOT)),
        you_com_key="yc-key",
        firecrawl_key=None,
    )

    assert len(warnings) == 1
    assert "layoffs_and_risk" in warnings[0]
    # every other query still returned real hits despite the one failure
    assert sum(len(hits) for _q, hits in results) == len(results) - 1


def _query_results(hits: list[SearchHit]) -> QueryResults:
    query = ResearchQuery(purpose="product_and_mission", query="Acme overview")
    return [(query, hits)]


async def test_synthesize_dossier_returns_empty_when_no_evidence_gathered() -> None:
    fingerprint = build_role_fingerprint(_SNAPSHOT)
    claims = await synthesize_dossier(
        fingerprint,
        _query_results([]),
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
    )
    assert claims == []


async def test_synthesize_dossier_parses_and_keeps_valid_claims() -> None:
    fingerprint = build_role_fingerprint(_SNAPSHOT)
    results = _query_results([_HIT])

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                [
                    {
                        "category": "product_and_mission",
                        "claim_text": "Acme raised a Series B.",
                        "source_url": _HIT["url"],
                        "confidence": "high",
                    }
                ]
            )
        )

    claims = await synthesize_dossier(
        fingerprint,
        results,
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert len(claims) == 1
    assert claims[0]["source_url"] == _HIT["url"]
    assert claims[0]["source_title"] == _HIT["title"]
    assert claims[0]["confidence"] == "high"


async def test_synthesize_dossier_drops_a_claim_citing_an_unretrieved_url() -> None:
    fingerprint = build_role_fingerprint(_SNAPSHOT)
    results = _query_results([_HIT])

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                [
                    {
                        "category": "product_and_mission",
                        "claim_text": "Acme did something.",
                        "source_url": "https://not-in-evidence.example/made-up",
                        "confidence": "high",
                    }
                ]
            )
        )

    claims = await synthesize_dossier(
        fingerprint,
        results,
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert claims == []


async def test_synthesize_dossier_drops_a_claim_with_an_unknown_category() -> None:
    fingerprint = build_role_fingerprint(_SNAPSHOT)
    results = _query_results([_HIT])

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                [
                    {
                        "category": "not_a_real_category",
                        "claim_text": "Acme did something.",
                        "source_url": _HIT["url"],
                        "confidence": "high",
                    }
                ]
            )
        )

    claims = await synthesize_dossier(
        fingerprint,
        results,
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert claims == []


async def test_synthesize_dossier_defaults_bad_confidence_to_medium() -> None:
    fingerprint = build_role_fingerprint(_SNAPSHOT)
    results = _query_results([_HIT])

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                [
                    {
                        "category": "product_and_mission",
                        "claim_text": "Acme did something.",
                        "source_url": _HIT["url"],
                        "confidence": "extremely sure",
                    }
                ]
            )
        )

    claims = await synthesize_dossier(
        fingerprint,
        results,
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert claims[0]["confidence"] == "medium"


async def test_synthesize_dossier_handles_malformed_json_gracefully() -> None:
    fingerprint = build_role_fingerprint(_SNAPSHOT)
    results = _query_results([_HIT])

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content="not json at all")

    claims = await synthesize_dossier(
        fingerprint,
        results,
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert claims == []


async def test_synthesize_dossier_strips_markdown_code_fences() -> None:
    fingerprint = build_role_fingerprint(_SNAPSHOT)
    results = _query_results([_HIT])

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        payload = json.dumps(
            [
                {
                    "category": "product_and_mission",
                    "claim_text": "Acme did something.",
                    "source_url": _HIT["url"],
                    "confidence": "low",
                }
            ]
        )
        return LLMResponse(content=f"```json\n{payload}\n```")

    claims = await synthesize_dossier(
        fingerprint,
        results,
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert len(claims) == 1
