"""Tests for the ContactFinder evidence-graph core (outreach-
contactfinder.md Phase A) -- query planning, the GitHub L1 fetcher,
provider fan-out, and above all the deterministic re-grounding/ranking
that survives an LLM's raw extraction output.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from between_jobs.api.contact_research import (
    QueryResults,
    build_contact_query_plan,
    extract_and_rank_candidates,
    fetch_github_org_members,
    find_contacts,
    guess_github_org_slug,
    run_contact_research,
)
from between_jobs.api.errors import ApiError
from between_jobs.api.llm_client import LLMResponse
from between_jobs.api.research_clients import SearchHit

_HIT = SearchHit(
    title="Jane Doe -- Technical Recruiter at Acme",
    url="https://blog.acme.example/team/jane-doe",
    snippet="Jane Doe is hiring for the Acme platform team. Reach out if you're interested.",
    published_at="2026-08-01T00:00:00Z",
)


def test_build_contact_query_plan_covers_the_five_personas() -> None:
    queries = build_contact_query_plan("Acme", "Staff AI Engineer")
    assert len(queries) == 5
    personas = {q["persona"] for q in queries}
    assert personas == {"hiring_lead", "recruiter", "manager", "senior_leader", "senior_ic"}
    assert all("Acme" in q["query"] for q in queries)


def test_guess_github_org_slug_strips_to_alphanumeric() -> None:
    assert guess_github_org_slug("Sarvam AI") == "sarvamai"
    assert guess_github_org_slug("OpenAI") == "openai"


def test_guess_github_org_slug_returns_none_for_nothing_usable() -> None:
    assert guess_github_org_slug("   --  ") is None


class _FakeGithubHttp:
    def __init__(
        self, org_status: int, members: list[dict[str, Any]], users: dict[str, dict[str, Any]]
    ) -> None:
        self._org_status = org_status
        self._members = members
        self._users = users

    async def get(self, url: str, **_kwargs: Any) -> httpx.Response:
        if url.endswith("/members"):
            return httpx.Response(self._org_status, json=self._members)
        login = url.rsplit("/", 1)[-1]
        if login in self._users:
            return httpx.Response(200, json=self._users[login])
        return httpx.Response(404, json={})


async def test_fetch_github_org_members_returns_empty_on_404() -> None:
    http = _FakeGithubHttp(404, [], {})
    hits = await fetch_github_org_members(http, org_slug="doesnotexist")  # type: ignore[arg-type]
    assert hits == []


async def test_fetch_github_org_members_skips_members_with_no_public_name() -> None:
    http = _FakeGithubHttp(
        200,
        [{"login": "ghosthandle"}],
        {"ghosthandle": {"login": "ghosthandle", "name": None}},
    )
    hits = await fetch_github_org_members(http, org_slug="acme")  # type: ignore[arg-type]
    assert hits == []


async def test_fetch_github_org_members_returns_real_named_members() -> None:
    http = _FakeGithubHttp(
        200,
        [{"login": "janedoe"}],
        {
            "janedoe": {
                "login": "janedoe",
                "name": "Jane Doe",
                "html_url": "https://github.com/janedoe",
                "bio": "Platform engineer",
            }
        },
    )
    hits = await fetch_github_org_members(http, org_slug="acme")  # type: ignore[arg-type]
    assert len(hits) == 1
    assert "Jane Doe" in hits[0]["title"]
    assert hits[0]["url"] == "https://github.com/janedoe"


class _FakeHttp:
    async def get(self, url: str, **_kwargs: Any) -> httpx.Response:  # pragma: no cover
        raise AssertionError("real http.get should never be reached in these tests")


async def test_run_contact_research_raises_setup_required_when_no_provider_configured() -> None:
    with pytest.raises(ApiError) as exc_info:
        await run_contact_research(
            _FakeHttp(),  # type: ignore[arg-type]
            build_contact_query_plan("Acme", "Engineer"),
            you_com_key=None,
            firecrawl_key=None,
            github_org_slug=None,
        )
    assert exc_info.value.code == "SETUP_REQUIRED"


async def test_run_contact_research_records_a_warning_but_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def flaky_you_com(
        _http: Any, *, api_key: str, query: str, count: int = 5
    ) -> list[SearchHit]:
        if "recruiter" in query:
            raise ApiError("PROVIDER_UNAVAILABLE", "You.com timed out.", retryable=True)
        return [_HIT]

    monkeypatch.setattr("between_jobs.api.contact_research.search_you_com", flaky_you_com)

    _results, providers_used, warnings = await run_contact_research(
        _FakeHttp(),  # type: ignore[arg-type]
        build_contact_query_plan("Acme", "Engineer"),
        you_com_key="yc-key",
        firecrawl_key=None,
        github_org_slug=None,
    )

    assert providers_used == ["you_com"]
    assert len(warnings) == 1
    assert "recruiter" in warnings[0]


async def test_run_contact_research_includes_github_hits_when_org_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_you_com(
        _http: Any, *, api_key: str, query: str, count: int = 5
    ) -> list[SearchHit]:
        return []

    async def fake_github(_http: Any, *, org_slug: str, limit: int = 10) -> list[SearchHit]:
        return [_HIT]

    monkeypatch.setattr("between_jobs.api.contact_research.search_you_com", fake_you_com)
    monkeypatch.setattr("between_jobs.api.contact_research.fetch_github_org_members", fake_github)

    results, providers_used, _warnings = await run_contact_research(
        _FakeHttp(),  # type: ignore[arg-type]
        build_contact_query_plan("Acme", "Engineer"),
        you_com_key="yc-key",
        firecrawl_key=None,
        github_org_slug="acme",
    )

    assert "github" in providers_used
    assert any(hits == [_HIT] for _q, hits in results)


def _results(hits: list[SearchHit]) -> QueryResults:
    query = build_contact_query_plan("Acme", "Engineer")[1]  # recruiter persona
    return [(query, hits)]


def _candidate_payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "person_name": "Jane Doe",
        "claimed_title": "Technical Recruiter",
        "claimed_team": "Platform",
        "source_url": _HIT["url"],
        "evidence_kind": "search_snippet",
        "confidence": "verified",
    }
    base.update(overrides)
    return base


def test_extract_and_rank_candidates_keeps_a_grounded_candidate() -> None:
    raw = json.dumps([_candidate_payload()])
    candidates = extract_and_rank_candidates(raw, _results([_HIT]), company="Acme")

    assert len(candidates) == 1
    assert candidates[0]["person_name"] == "Jane Doe"
    assert candidates[0]["company"] == "Acme"
    assert candidates[0]["persona"] == "recruiter"
    assert candidates[0]["evidence"][0]["source_url"] == _HIT["url"]


def test_extract_and_rank_candidates_drops_a_name_not_in_the_source_text() -> None:
    """The command-center-ported grounding check: citing a real URL isn't
    enough if the source's own title/snippet doesn't literally contain
    the claimed person's name."""
    raw = json.dumps([_candidate_payload(person_name="Someone Else Entirely")])
    candidates = extract_and_rank_candidates(raw, _results([_HIT]), company="Acme")
    assert candidates == []


def test_extract_and_rank_candidates_drops_a_claim_citing_an_unretrieved_url() -> None:
    raw = json.dumps([_candidate_payload(source_url="https://not-in-evidence.example/made-up")])
    candidates = extract_and_rank_candidates(raw, _results([_HIT]), company="Acme")
    assert candidates == []


def test_extract_and_rank_candidates_returns_empty_when_none_are_named() -> None:
    """n8n's own Rule 1, enforced deterministically: no named people in
    the sources means an empty array, never an invented one."""
    raw = json.dumps([])
    candidates = extract_and_rank_candidates(raw, _results([_HIT]), company="Acme")
    assert candidates == []


def test_extract_and_rank_candidates_handles_malformed_json() -> None:
    candidates = extract_and_rank_candidates("not json", _results([_HIT]), company="Acme")
    assert candidates == []


def test_extract_and_rank_candidates_strips_markdown_code_fences() -> None:
    payload = json.dumps([_candidate_payload()])
    raw = f"```json\n{payload}\n```"
    candidates = extract_and_rank_candidates(raw, _results([_HIT]), company="Acme")
    assert len(candidates) == 1


def test_extract_and_rank_candidates_merges_the_same_person_across_sources() -> None:
    second_hit = SearchHit(
        title="Jane Doe joins Acme as Technical Recruiter",
        url="https://press.example/jane-doe-acme",
        snippet="Jane Doe has joined Acme's talent team, hiring for the platform org.",
        published_at="2026-08-05T00:00:00Z",
    )
    query = build_contact_query_plan("Acme", "Engineer")[1]
    results: QueryResults = [(query, [_HIT, second_hit])]

    raw = json.dumps(
        [
            _candidate_payload(source_url=_HIT["url"]),
            _candidate_payload(source_url=second_hit["url"]),
        ]
    )
    candidates = extract_and_rank_candidates(raw, results, company="Acme")

    assert len(candidates) == 1
    assert len(candidates[0]["evidence"]) == 2


def test_extract_and_rank_candidates_ranks_hiring_lead_above_senior_ic() -> None:
    hiring_lead_query = build_contact_query_plan("Acme", "Engineer")[0]
    ic_query = build_contact_query_plan("Acme", "Engineer")[4]
    ic_hit = SearchHit(
        title="John Smith -- Staff Engineer at Acme",
        url="https://blog.acme.example/team/john-smith",
        snippet="John Smith is a staff engineer on the platform team.",
        published_at=None,
    )
    results: QueryResults = [(hiring_lead_query, [_HIT]), (ic_query, [ic_hit])]

    raw = json.dumps(
        [
            _candidate_payload(person_name="Jane Doe", source_url=_HIT["url"]),
            {
                "person_name": "John Smith",
                "claimed_title": "Staff Engineer",
                "claimed_team": None,
                "source_url": ic_hit["url"],
                "evidence_kind": "search_snippet",
                "confidence": "verified",
            },
        ]
    )
    candidates = extract_and_rank_candidates(raw, results, company="Acme")

    assert len(candidates) == 2
    assert candidates[0]["person_name"] == "Jane Doe"
    assert candidates[0]["priority_score"] > candidates[1]["priority_score"]


async def test_find_contacts_returns_empty_when_no_evidence_gathered() -> None:
    candidates = await find_contacts(
        "Acme", _results([]), llm_api_key="key", llm_model="model", llm_base_url=None
    )
    assert candidates == []


async def test_find_contacts_wires_the_llm_call_through_to_grounded_output() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps([_candidate_payload()]))

    candidates = await find_contacts(
        "Acme",
        _results([_HIT]),
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert len(candidates) == 1
    assert candidates[0]["company"] == "Acme"
