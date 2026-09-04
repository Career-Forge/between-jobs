"""Tests for the ContactFinder evidence-graph core (outreach-
contactfinder.md Phase A) -- query planning, the GitHub L1 fetcher,
provider fan-out, and above all the deterministic re-grounding/ranking
that survives an LLM's raw extraction output.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from between_jobs.api.contact_research import (
    ContactQuery,
    QueryResults,
    apply_l2_search_filters,
    build_contact_query_plan,
    extract_and_rank_candidates,
    extract_product_term_candidates,
    fetch_github_org_members,
    find_contacts,
    guess_github_org_slug,
    pick_product_terms,
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


def test_build_contact_query_plan_without_product_terms_returns_the_core_four() -> None:
    """Phase G rewrite (outreach-v2-search-first.md): without Phase H's
    product terms, the two product-anchored manager queries are omitted,
    leaving the four that the trial showed work regardless."""
    queries = build_contact_query_plan("Acme", "Staff AI Engineer")
    assert len(queries) == 4
    personas = [q["persona"] for q in queries]
    assert personas == ["recruiter", "recruiter", "hiring_lead", "senior_leader"]
    assert all("Acme" in q["query"] for q in queries)


def test_build_contact_query_plan_with_product_terms_adds_two_manager_queries() -> None:
    queries = build_contact_query_plan(
        "Acme", "Staff AI Engineer", product_terms=["Widget", "core"]
    )
    assert len(queries) == 6
    manager_queries = [q for q in queries if q["persona"] == "manager"]
    assert len(manager_queries) == 2
    assert all("Widget" in q["query"] for q in manager_queries)
    assert any("engineering manager LinkedIn" in q["query"] for q in manager_queries)
    assert any("team lead" in q["query"] for q in manager_queries)


def test_build_contact_query_plan_never_quotes_the_exact_role_title() -> None:
    """Regression guard for the exact bug the 2026-09-04 trial found: a
    quoted exact title returns zero Firecrawl results on any real
    listing. No query may contain the role title wrapped in quotes."""
    role_title = "AI Engineer - Model Optimization & Acceleration"
    queries = build_contact_query_plan("Acme", role_title)
    assert all(f'"{role_title}"' not in q["query"] for q in queries)
    assert all(role_title not in q["query"] for q in queries)


def test_build_contact_query_plan_marks_the_hiring_post_query_firecrawl_only() -> None:
    queries = build_contact_query_plan("Acme", "Engineer")
    hiring_lead_queries = [q for q in queries if q["persona"] == "hiring_lead"]
    assert len(hiring_lead_queries) == 1
    assert hiring_lead_queries[0]["provider"] == "firecrawl"
    assert "site:linkedin.com/posts" in hiring_lead_queries[0]["query"]


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
    # 2 warnings, not 1: the recruiter query's real failure, plus the
    # hiring-post query being skipped since no Firecrawl key is
    # configured here (it's provider="firecrawl"-only, never run on
    # You.com -- see build_contact_query_plan).
    assert len(warnings) == 2
    assert any("recruiter" in w and "timed out" in w for w in warnings)
    assert any("hiring_lead" in w and "Firecrawl" in w for w in warnings)


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
    """Decoupled from `build_contact_query_plan`'s own exact query shape
    on purpose -- this test is about the ranking rule (Proposal §26.1
    stage 6: a hiring lead can outrank a senior IC), not the plan."""
    hiring_lead_query = ContactQuery(
        persona="hiring_lead", query="site:linkedin.com/posts Acme hiring", provider="firecrawl"
    )
    ic_query = ContactQuery(persona="senior_ic", query="Acme staff engineer")
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


def test_extract_and_rank_candidates_classifies_evidence_kind_from_the_url_not_the_llm() -> None:
    """Deterministic code owns structure and shape: evidence_kind is
    derived from the URL, overriding whatever the LLM's raw JSON claims."""
    profile_hit = SearchHit(
        title="Jane Doe -- Recruiter",
        url="https://www.linkedin.com/in/jane-doe-123/",
        snippet="Jane Doe is a technical recruiter at Acme.",
        published_at=None,
    )
    query = ContactQuery(persona="recruiter", query="site:linkedin.com/in Acme recruiter")
    raw = json.dumps(
        [_candidate_payload(source_url=profile_hit["url"], evidence_kind="github_membership")]
    )
    candidates = extract_and_rank_candidates(raw, [(query, [profile_hit])], company="Acme")
    assert len(candidates) == 1
    assert candidates[0]["evidence"][0]["evidence_kind"] == "linkedin_profile"


def test_extract_and_rank_candidates_drops_a_candidate_with_anchored_former_employer_language() -> (
    None
):
    hit = SearchHit(
        title="Jane Doe -- Former Acme Engineer",
        url="https://blog.example/jane-doe",
        snippet="Jane Doe, former Acme engineer, now leads platform at Widget Inc.",
        published_at=None,
    )
    query = ContactQuery(persona="senior_ic", query="Acme engineer")
    raw = json.dumps([_candidate_payload(source_url=hit["url"])])
    candidates = extract_and_rank_candidates(raw, [(query, [hit])], company="Acme")
    assert candidates == []


def test_extract_and_rank_candidates_keeps_a_candidate_with_an_unrelated_ex_employer() -> None:
    """The employer guard is anchored to the target company specifically
    -- "ex-Google" says nothing about whether this person is currently
    at Acme, so it must not be treated the same as "ex-Acme"."""
    hit = SearchHit(
        title="Jane Doe -- Staff Engineer at Acme",
        url="https://blog.example/jane-doe",
        snippet="Ex-Google engineer Jane Doe now leads the platform team at Acme.",
        published_at=None,
    )
    query = ContactQuery(persona="senior_ic", query="Acme engineer")
    raw = json.dumps([_candidate_payload(source_url=hit["url"])])
    candidates = extract_and_rank_candidates(raw, [(query, [hit])], company="Acme")
    assert len(candidates) == 1


def _snowflake_post_url(slug: str, *, days_ago: float) -> str:
    """Mirrors contact_research.py's own LinkedIn Snowflake epoch
    constant (1288834974657 ms, 2010-11-04) to build a real, decodable
    activity id for a post posted `days_ago` days before now."""
    epoch_ms = 1288834974657
    posted_ms = int(datetime.now(UTC).timestamp() * 1000) - int(days_ago * 86400 * 1000)
    activity_id = (posted_ms - epoch_ms) << 22
    return f"https://www.linkedin.com/posts/{slug}_hiring-update-activity-{activity_id}-abcd/"


def _post_hit(*, slug: str, days_ago: float, snippet: str) -> SearchHit:
    return SearchHit(
        title=f"{slug} post",
        url=_snowflake_post_url(slug, days_ago=days_ago),
        snippet=snippet,
        published_at=None,
    )


def test_apply_l2_search_filters_keeps_a_fresh_person_authored_hiring_post() -> None:
    hit = _post_hit(
        slug="jane-doe-987654",
        days_ago=5,
        snippet="My team is hiring for the platform org at Acme -- reach out!",
    )
    query = ContactQuery(persona="hiring_lead", query="site:linkedin.com/posts Acme hiring")
    filtered, dropped = apply_l2_search_filters([(query, [hit])], company="Acme")
    assert filtered[0][1] == [hit]
    assert dropped == []


def test_apply_l2_search_filters_drops_a_brand_account_post() -> None:
    hit = _post_hit(
        slug="acme",
        days_ago=5,
        snippet="Acme is hiring across the platform org -- see our open roles.",
    )
    query = ContactQuery(persona="hiring_lead", query="site:linkedin.com/posts Acme hiring")
    filtered, dropped = apply_l2_search_filters([(query, [hit])], company="Acme")
    assert filtered[0][1] == []
    assert any("brand-account" in reason for reason in dropped)


def test_apply_l2_search_filters_drops_a_post_with_no_hiring_intent_language() -> None:
    hit = _post_hit(
        slug="jane-doe-987654", days_ago=5, snippet="Jane Doe shares a photo from the Acme offsite."
    )
    query = ContactQuery(persona="hiring_lead", query="site:linkedin.com/posts Acme hiring")
    filtered, dropped = apply_l2_search_filters([(query, [hit])], company="Acme")
    assert filtered[0][1] == []
    assert any("hiring-intent" in reason for reason in dropped)


def test_apply_l2_search_filters_drops_a_stale_post() -> None:
    hit = _post_hit(
        slug="jane-doe-987654",
        days_ago=800,
        snippet="My team is hiring for the platform org at Acme -- reach out!",
    )
    query = ContactQuery(persona="hiring_lead", query="site:linkedin.com/posts Acme hiring")
    filtered, dropped = apply_l2_search_filters([(query, [hit])], company="Acme")
    assert filtered[0][1] == []
    assert any("stale post" in reason for reason in dropped)


def test_apply_l2_search_filters_drops_a_login_wall_placeholder() -> None:
    hit = SearchHit(
        title="Sign in to LinkedIn",
        url="https://www.linkedin.com/in/some-profile/",
        snippet="Sign in to LinkedIn to see this profile.",
        published_at=None,
    )
    query = ContactQuery(persona="recruiter", query="site:linkedin.com/in Acme recruiter")
    filtered, dropped = apply_l2_search_filters([(query, [hit])], company="Acme")
    assert filtered[0][1] == []
    assert any("login-wall" in reason for reason in dropped)


def test_apply_l2_search_filters_dedupes_the_same_url_across_two_queries() -> None:
    q1 = ContactQuery(persona="recruiter", query="Acme Talent Acquisition LinkedIn profile")
    q2 = ContactQuery(persona="recruiter", query="site:linkedin.com/in Acme recruiter")
    filtered, _dropped = apply_l2_search_filters([(q1, [_HIT]), (q2, [_HIT])], company="Acme")
    total_kept = sum(len(hits) for _q, hits in filtered)
    assert total_kept == 1


# --- Phase H: per-company product vocabulary (outreach-v2-search-first.md) ---

_AMD_CLAIM_TEXT = (
    "AMD describes ROCm.AI as bringing its AI ecosystem into developers' "
    "existing tools to help create optimized, GPU-accelerated applications."
)


def test_extract_product_term_candidates_finds_a_real_dotted_product_name() -> None:
    """Real text from the live AMD Company Intel dossier -- confirms the
    regex treats "ROCm.AI" as one token, not two."""
    candidates = extract_product_term_candidates("AMD", _AMD_CLAIM_TEXT)
    assert "ROCm.AI" in candidates


def test_extract_product_term_candidates_excludes_the_company_name() -> None:
    candidates = extract_product_term_candidates(
        "AMD", "AMD is hiring engineers for the ROCm.AI platform team."
    )
    assert "AMD" not in candidates
    assert "ROCm.AI" in candidates


def test_extract_product_term_candidates_excludes_the_companys_first_word() -> None:
    """A multi-word legal name should still exclude its own short-form --
    e.g. "Advanced Micro Devices" shouldn't let "Advanced" through just
    because it's capitalized."""
    candidates = extract_product_term_candidates(
        "Advanced Micro Devices", "Advanced engineering roles on the ROCm.AI team."
    )
    assert "Advanced" not in candidates
    assert "ROCm.AI" in candidates


def test_extract_product_term_candidates_drops_generic_stopwords() -> None:
    candidates = extract_product_term_candidates("Acme", "The Team is hiring for this Role.")
    assert candidates == []


def test_extract_product_term_candidates_dedupes_across_texts() -> None:
    candidates = extract_product_term_candidates(
        "Acme", "Widget powers everything.", "Widget is our platform.", ""
    )
    assert candidates.count("Widget") == 1


def test_extract_product_term_candidates_scans_every_text_argument() -> None:
    """A term appearing ONLY in a later argument -- not the first, and
    with an empty/None argument in between -- must still be found,
    proving multi-text scanning genuinely happens rather than a bug that
    stops after the first non-empty source (which a test asserting only
    on a term placed in the FIRST argument couldn't distinguish)."""
    candidates = extract_product_term_candidates(
        "Acme", "nothing capitalized here", None, "", "Gizmo powers our platform."
    )
    assert "Gizmo" in candidates


def test_extract_product_term_candidates_caps_at_the_bound() -> None:
    many_terms = " ".join(f"Product{i}" for i in range(50))
    candidates = extract_product_term_candidates("Acme", many_terms)
    assert len(candidates) == 30
    assert candidates == [f"Product{i}" for i in range(30)]


def test_extract_product_term_candidates_round_robins_so_a_later_source_survives_the_cap() -> None:
    """Regression test for a real starvation bug an adversarial review
    caught: the original implementation drained the first source (a long
    JD, always passed first at the real call site in contact_research_
    routes.py) before ever considering a later one (a Company Intel
    claim) -- silently excluding the exact term Phase H exists to
    surface whenever the JD alone crossed the 30-candidate bound, which
    real JDs with a tools/requirements section routinely do."""
    long_jd_text = " ".join(f"Tool{i}" for i in range(40))
    candidates = extract_product_term_candidates("AMD", long_jd_text, _AMD_CLAIM_TEXT)
    assert len(candidates) == 30
    assert "ROCm.AI" in candidates


async def test_pick_product_terms_returns_empty_without_calling_the_llm_when_empty() -> None:
    async def fail_if_called(**_kwargs: Any) -> LLMResponse:
        raise AssertionError("the LLM must not be called when there's nothing to pick from")

    picked = await pick_product_terms(
        [], llm_api_key="key", llm_model="model", llm_base_url=None, generate=fail_if_called
    )
    assert picked == []


async def test_pick_product_terms_only_returns_candidate_list_members() -> None:
    """The re-validation this function exists for: an invented term the
    LLM returns that isn't in the candidate list must be dropped, never
    trusted on the model's own say-so."""

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps(["ROCm.AI", "InventedProduct"]))

    picked = await pick_product_terms(
        ["ROCm.AI", "Instinct"],
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )
    assert picked == ["ROCm.AI"]


async def test_pick_product_terms_caps_at_two() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps(["A", "B", "C"]))

    picked = await pick_product_terms(
        ["A", "B", "C"],
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )
    assert len(picked) == 2


async def test_pick_product_terms_handles_malformed_json() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content="not json")

    picked = await pick_product_terms(
        ["ROCm.AI"], llm_api_key="key", llm_model="model", llm_base_url=None, generate=fake_generate
    )
    assert picked == []


async def test_pick_product_terms_returns_empty_when_nothing_qualifies() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps([]))

    picked = await pick_product_terms(
        ["ROCm.AI"], llm_api_key="key", llm_model="model", llm_base_url=None, generate=fake_generate
    )
    assert picked == []


async def test_pick_product_terms_strips_markdown_code_fences() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content='```json\n["ROCm.AI"]\n```')

    picked = await pick_product_terms(
        ["ROCm.AI"], llm_api_key="key", llm_model="model", llm_base_url=None, generate=fake_generate
    )
    assert picked == ["ROCm.AI"]


async def test_pick_product_terms_dedupes_a_repeated_term() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps(["ROCm.AI", "ROCm.AI"]))

    picked = await pick_product_terms(
        ["ROCm.AI"], llm_api_key="key", llm_model="model", llm_base_url=None, generate=fake_generate
    )
    assert picked == ["ROCm.AI"]


async def test_pick_product_terms_skips_non_string_items_without_crashing() -> None:
    """A dict item is unhashable -- proves the isinstance guard short-
    circuits before the set-membership check, never raising TypeError."""

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps([123, {"term": "ROCm.AI"}, "ROCm.AI"]))

    picked = await pick_product_terms(
        ["ROCm.AI"], llm_api_key="key", llm_model="model", llm_base_url=None, generate=fake_generate
    )
    assert picked == ["ROCm.AI"]


async def test_pick_product_terms_trims_incidental_whitespace_in_a_picked_term() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps(["ROCm.AI "]))

    picked = await pick_product_terms(
        ["ROCm.AI"], llm_api_key="key", llm_model="model", llm_base_url=None, generate=fake_generate
    )
    assert picked == ["ROCm.AI"]
