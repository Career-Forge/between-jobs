"""Tests for the ContactFinder HTTP endpoints (outreach-contactfinder.md
Phase B/C). Exercises the real FastAPI routes via TestClient, faking the
You.com/Firecrawl/GitHub/Apollo HTTP boundary and the pipeline's own
`generate` injection point -- never a real network call or real spend.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from between_jobs.api.app import app
from between_jobs.api.app_state import get_http_client, get_supabase
from between_jobs.api.auth import require_user_id
from between_jobs.api.llm_client import LLMResponse

_USER_ID = "00000000-0000-0000-0000-000000000001"
_APPLICATION_ID = "30000000-0000-0000-0000-000000000001"
_SNAPSHOT_ID = "20000000-0000-0000-0000-000000000002"

_APPLICATION_ROW = {
    "id": _APPLICATION_ID,
    "user_id": _USER_ID,
    "active_job_snapshot_id": _SNAPSHOT_ID,
}
_SNAPSHOT_ROW = {
    "id": _SNAPSHOT_ID,
    "title": "Staff AI Engineer",
    "company_name": "Acme",
    "location_text": "Remote",
    # Deliberately no capitalized/technical-looking tokens -- keeps
    # extract_product_term_candidates empty so pick_product_terms
    # short-circuits without an LLM call in tests that don't patch one.
    "description_text": "looking for a strong engineer to help build and scale our platform",
}
_PREFERENCE_ROW = {
    "capability": "default",
    "execution_mode": "byok_first_party",
    "provider": "openrouter",
    "model": "anthropic/claude-sonnet-4-6",
}
_LLM_CREDENTIAL_ROW = {
    "provider": "openrouter",
    "model": "anthropic/claude-sonnet-4-6",
    "base_url": None,
    "secret_encrypted": "llm-cipher",
}
_YOU_COM_CREDENTIAL_ROW = {
    "provider": "you_com",
    "model": None,
    "base_url": None,
    "secret_encrypted": "yc-cipher",
}


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def in_(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def order(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def limit(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTable:
    def __init__(
        self, *, select_rows: list[dict[str, Any]], insert_row: dict[str, Any] | None = None
    ) -> None:
        self.select_rows = select_rows
        self.insert_row = insert_row
        self.insert_calls: list[Any] = []
        self.update_calls: list[Any] = []
        self._next_id = 1

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def insert(self, data: Any) -> _ChainBuilder:
        self.insert_calls.append(data)
        if isinstance(data, list):
            rows = []
            for row in data:
                rows.append({"id": f"row-{self._next_id}", **row})
                self._next_id += 1
        else:
            rows = [self.insert_row] if self.insert_row is not None else [{**data, "id": "row-1"}]
        self.select_rows.extend(rows)
        return _ChainBuilder(rows)

    def update(self, data: Any) -> _ChainBuilder:
        self.update_calls.append(data)
        for row in self.select_rows:
            row.update(data)
        return _ChainBuilder(self.select_rows)


class _CredentialTable:
    """Rows keyed on (service, provider) -- this file's tests have one caller. `update` is the
    conditional update `mark_credential_unvalidated` makes: it changes (and returns) a row only
    while the row's `is_validated` still equals the value the query asks for, like Postgres."""

    def __init__(self, rows: dict[tuple[str, str], dict[str, Any]]) -> None:
        self._rows = rows
        self._service = ""
        self._provider = ""
        self._pending_update: dict[str, Any] | None = None
        self._validated_filter: bool | None = None
        self.reads = 0

    def select(self, *_: Any, **__: Any) -> _CredentialTable:
        self._pending_update = None
        self.reads += 1
        return self

    def update(self, data: dict[str, Any]) -> _CredentialTable:
        self._pending_update = data
        self._validated_filter = None
        return self

    def eq(self, column: str, value: Any) -> _CredentialTable:
        if column == "service":
            self._service = value
        if column == "provider":
            self._provider = value
        if column == "is_validated":
            self._validated_filter = value
        return self

    async def execute(self) -> SimpleNamespace:
        row = self._rows.get((self._service, self._provider))
        if self._pending_update is not None:
            update, self._pending_update = self._pending_update, None
            if (
                row is None
                or self._validated_filter is None
                or row.get("is_validated", True) != self._validated_filter
            ):
                return SimpleNamespace(data=[])
            row.update(update)
        return SimpleNamespace(data=[row] if row else [])


class _FakeRpcBuilder:
    def __init__(self, data: Any) -> None:
        self._data = data

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._data)


class _PaginatedTable:
    """`.select().range().execute()` -- matches `company_tiers.get_
    company_tier_index`'s own real query chain (no `.eq()` at all)."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self._start = 0
        self._end = len(rows)

    def select(self, *_: Any, **__: Any) -> _PaginatedTable:
        return self

    def range(self, start: int, end: int) -> _PaginatedTable:
        self._start = start
        self._end = end
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows[self._start : self._end + 1])


class _FakeSupabaseClient:
    def __init__(
        self,
        *,
        applications: _FakeTable | None = None,
        capability_preferences: _FakeTable | None = None,
        provider_credentials: dict[tuple[str, str], dict[str, Any]] | None = None,
        contact_research_runs: _FakeTable | None = None,
        contact_candidates: _FakeTable | None = None,
        contact_candidate_evidence: _FakeTable | None = None,
        outreach_drafts: _FakeTable | None = None,
        company_intel_runs: _FakeTable | None = None,
        company_intel_claims: _FakeTable | None = None,
        company_tiers: list[dict[str, Any]] | None = None,
    ) -> None:
        self.applications = applications or _FakeTable(select_rows=[_APPLICATION_ROW])
        self.job_snapshots = _FakeTable(select_rows=[_SNAPSHOT_ROW])
        self.capability_preferences = capability_preferences or _FakeTable(
            select_rows=[_PREFERENCE_ROW]
        )
        self._credential_table = _CredentialTable(
            provider_credentials
            if provider_credentials is not None
            else {("llm", "openrouter"): _LLM_CREDENTIAL_ROW}
        )
        self.contact_research_runs = contact_research_runs or _FakeTable(
            select_rows=[], insert_row={"id": "run-1", "application_id": _APPLICATION_ID}
        )
        self.contact_candidates = contact_candidates or _FakeTable(select_rows=[])
        self.contact_candidate_evidence = contact_candidate_evidence or _FakeTable(select_rows=[])
        self.outreach_drafts = outreach_drafts or _FakeTable(select_rows=[])
        # Phase H -- a prior Company Intel run is optional; empty by
        # default so get_latest_company_intel_run cleanly returns None.
        self.company_intel_runs = company_intel_runs or _FakeTable(select_rows=[])
        self.company_intel_claims = company_intel_claims or _FakeTable(select_rows=[])
        # Phase K -- empty by default, matching get_company_tier_index's
        # own documented fail-open contract (an empty index means the
        # X/Twitter lane's gate never fires, never the reverse).
        self._company_tiers = _PaginatedTable(company_tiers or [])

    def table(self, name: str) -> Any:
        return {
            "applications": self.applications,
            "job_snapshots": self.job_snapshots,
            "capability_preferences": self.capability_preferences,
            "provider_credentials": self._credential_table,
            "contact_research_runs": self.contact_research_runs,
            "contact_candidates": self.contact_candidates,
            "contact_candidate_evidence": self.contact_candidate_evidence,
            "outreach_drafts": self.outreach_drafts,
            "company_intel_runs": self.company_intel_runs,
            "company_intel_claims": self.company_intel_claims,
            "company_tiers": self._company_tiers,
        }[name]

    def rpc(self, fn: str, _params: dict[str, Any]) -> _FakeRpcBuilder:
        if fn == "decrypt_secret":
            return _FakeRpcBuilder("decrypted-secret")
        raise AssertionError(f"unexpected rpc: {fn}")


_HIT_JSON = {
    "results": {
        "web": [
            {
                "title": "Jane Doe -- Technical Recruiter at Acme",
                "url": "https://blog.acme.example/team/jane-doe",
                "snippets": ["Jane Doe is hiring for the Acme platform team."],
                "page_age": "2026-08-01",
            }
        ]
    }
}

_CANDIDATE_LLM_RESPONSE = json.dumps(
    [
        {
            "person_name": "Jane Doe",
            "claimed_title": "Technical Recruiter",
            "claimed_team": "Platform",
            "source_url": "https://blog.acme.example/team/jane-doe",
            "evidence_kind": "search_snippet",
            "confidence": "verified",
        }
    ]
)


class _FakeHttpClient:
    def __init__(
        self,
        *,
        status_code: int = 200,
        apollo_status_code: int = 200,
        apollo_body: dict[str, Any] | None = None,
        hunter_status_code: int = 200,
        hunter_body: dict[str, Any] | None = None,
        exa_status_code: int = 200,
        exa_body: dict[str, Any] | None = None,
        google_token_body: dict[str, Any] | None = None,
        gmail_draft_status_code: int = 200,
        gmail_draft_body: dict[str, Any] | None = None,
        google_token_status_code: int = 200,
    ) -> None:
        self.status_code = status_code
        self.apollo_status_code = apollo_status_code
        self.apollo_body = apollo_body if apollo_body is not None else {"person": None}
        self.hunter_status_code = hunter_status_code
        self.hunter_body = hunter_body if hunter_body is not None else {"data": {"email": None}}
        self.exa_status_code = exa_status_code
        self.exa_body = exa_body if exa_body is not None else {"results": []}
        self.google_token_body = (
            google_token_body if google_token_body is not None else {"access_token": "at-1"}
        )
        self.google_token_status_code = google_token_status_code
        self.gmail_draft_status_code = gmail_draft_status_code
        self.gmail_draft_body = (
            gmail_draft_body
            if gmail_draft_body is not None
            else {"id": "gmail-draft-1", "message": {"id": "msg-1", "threadId": "gmail-thread-1"}}
        )
        self.post_calls: list[str] = []
        self.get_calls: list[str] = []

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.post_calls.append(url)
        if "apollo.io" in url:
            return httpx.Response(
                status_code=self.apollo_status_code,
                json=self.apollo_body,
                request=httpx.Request("POST", url),
            )
        if "exa.ai" in url:
            return httpx.Response(
                status_code=self.exa_status_code,
                json=self.exa_body,
                request=httpx.Request("POST", url),
            )
        if "oauth2.googleapis.com" in url:
            return httpx.Response(
                status_code=self.google_token_status_code,
                json=self.google_token_body,
                request=httpx.Request("POST", url),
            )
        if "gmail.googleapis.com" in url:
            return httpx.Response(
                status_code=self.gmail_draft_status_code,
                json=self.gmail_draft_body,
                request=httpx.Request("POST", url),
            )
        return httpx.Response(
            status_code=self.status_code, json=_HIT_JSON, request=httpx.Request("POST", url)
        )

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self.get_calls.append(url)
        if "hunter.io" in url:
            return httpx.Response(
                status_code=self.hunter_status_code,
                json=self.hunter_body,
                request=httpx.Request("GET", url),
            )
        # No real GitHub org exists for the fixture company -- a bare 404
        # is exactly how a real "no org at that slug" response behaves,
        # and fetch_github_org_members already treats that as an empty,
        # non-error L1 result.
        return httpx.Response(status_code=404, json={}, request=httpx.Request("GET", url))


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-123")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret-456")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8012/oauth/gmail/callback")
    monkeypatch.delenv("GMAIL_ALLOWED_USER_IDS", raising=False)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Any:
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reset_company_tier_cache() -> Any:
    """Phase K -- generate_contacts now calls get_company_tier_index on
    every request; its module-level cache must not leak between tests,
    same precedent as test_discovery_routes.py's own identical fixture."""
    import between_jobs.api.company_tiers as tiers_module

    tiers_module._cached_index = None
    yield
    tiers_module._cached_index = None


def _patch_llm(monkeypatch: pytest.MonkeyPatch, content: str = _CANDIDATE_LLM_RESPONSE) -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=content)

    # Same R3-bug-informed seam every other multi-stage route in this
    # codebase already uses -- the route passes `generate=llm_generate`
    # explicitly rather than relying on find_contacts' own default.
    monkeypatch.setattr("between_jobs.api.contact_research_routes.llm_generate", fake_generate)


def _client(supabase: _FakeSupabaseClient, http: _FakeHttpClient) -> TestClient:
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    app.dependency_overrides[get_http_client] = lambda: http
    return TestClient(app)


def test_get_contacts_returns_none_when_nothing_generated_yet() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}/contacts")

    assert response.status_code == 200
    assert response.json() == {"run": None, "candidates": []}


def test_generate_contacts_with_no_search_provider_returns_setup_required() -> None:
    supabase = _FakeSupabaseClient(
        provider_credentials={("llm", "openrouter"): _LLM_CREDENTIAL_ROW}
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/contacts")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SETUP_REQUIRED"
    assert http.post_calls == []


def test_generate_contacts_with_no_llm_returns_setup_required() -> None:
    supabase = _FakeSupabaseClient(
        capability_preferences=_FakeTable(select_rows=[]),
        provider_credentials={("search", "you_com"): _YOU_COM_CREDENTIAL_ROW},
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/contacts")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SETUP_REQUIRED"


def test_generate_contacts_success_stores_run_and_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supabase = _FakeSupabaseClient(
        provider_credentials={
            ("llm", "openrouter"): _LLM_CREDENTIAL_ROW,
            ("search", "you_com"): _YOU_COM_CREDENTIAL_ROW,
        }
    )
    http = _FakeHttpClient()
    _patch_llm(monkeypatch)

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/contacts")

    assert response.status_code == 201
    body = response.json()
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["person_name"] == "Jane Doe"
    assert len(body["candidates"][0]["evidence"]) == 1
    assert supabase.contact_research_runs.insert_calls[0]["company_name"] == "Acme"
    # 4 bounded queries without product_terms (Phase G); the hiring-post
    # query is provider="firecrawl"-only and gets skipped with no HTTP
    # call at all since no Firecrawl key is configured here -- so only
    # the 3 remaining queries actually hit You.com.
    assert len(http.post_calls) == 3


def test_generate_contacts_fetches_company_intel_scoped_to_the_right_user_and_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fake company_intel_runs/company_intel_claims tables' own
    .eq() is a no-op (this codebase has hit that exact test-harness gap
    before -- a filter that can't fail regardless of what's passed in
    proves nothing about scoping), so this spies on the real store
    function directly to prove the correct user_id/application_id are
    actually passed through, not just that SOME data comes back."""
    supabase = _FakeSupabaseClient(
        provider_credentials={
            ("llm", "openrouter"): _LLM_CREDENTIAL_ROW,
            ("search", "you_com"): _YOU_COM_CREDENTIAL_ROW,
        }
    )
    http = _FakeHttpClient()
    _patch_llm(monkeypatch)

    calls: list[tuple[Any, str, str]] = []

    async def fake_get_latest_company_intel_run(
        supabase_arg: Any, user_id: str, application_id: str
    ) -> Any:
        calls.append((supabase_arg, user_id, application_id))
        return None

    monkeypatch.setattr(
        "between_jobs.api.contact_research_routes.get_latest_company_intel_run",
        fake_get_latest_company_intel_run,
    )

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/contacts")

    assert response.status_code == 201
    assert len(calls) == 1
    assert calls[0][0] is supabase
    assert calls[0][1] == _USER_ID
    assert calls[0][2] == _APPLICATION_ID


def test_generate_contacts_wires_product_terms_from_company_intel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase H end-to-end: a real Company Intel claim mentioning a
    product name flows into build_contact_query_plan's manager queries
    and gets persisted on the run row -- proving the wiring, not just
    the underlying functions in isolation."""
    supabase = _FakeSupabaseClient(
        provider_credentials={
            ("llm", "openrouter"): _LLM_CREDENTIAL_ROW,
            ("search", "you_com"): _YOU_COM_CREDENTIAL_ROW,
        },
        company_intel_runs=_FakeTable(select_rows=[{"id": "ci-run-1"}]),
        company_intel_claims=_FakeTable(
            select_rows=[
                {
                    "category": "product_and_mission",
                    "claim_text": "Acme builds its platform on WidgetCore for scalable inference.",
                }
            ]
        ),
    )
    http = _FakeHttpClient()

    async def fake_generate(**kwargs: Any) -> LLMResponse:
        if "flagship SOFTWARE product" in kwargs["system_prompt"]:
            return LLMResponse(content=json.dumps(["WidgetCore"]))
        return LLMResponse(content=_CANDIDATE_LLM_RESPONSE)

    monkeypatch.setattr("between_jobs.api.contact_research_routes.llm_generate", fake_generate)

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/contacts")

    assert response.status_code == 201
    inserted_run = supabase.contact_research_runs.insert_calls[0]
    assert inserted_run["product_terms"] == ["WidgetCore"]
    # 6 queries now (the 4-query core plus the 2 product-anchored manager
    # queries); the hiring-post query is still firecrawl-only and skipped
    # (no Firecrawl key configured here), so 5 hit You.com.
    assert len(http.post_calls) == 5


def test_generate_contacts_never_invents_a_person_not_in_the_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end proof the anti-fabrication grounding survives the full
    HTTP round trip, not just the pipeline's own unit tests."""
    supabase = _FakeSupabaseClient(
        provider_credentials={
            ("llm", "openrouter"): _LLM_CREDENTIAL_ROW,
            ("search", "you_com"): _YOU_COM_CREDENTIAL_ROW,
        }
    )
    http = _FakeHttpClient()
    fabricated = json.dumps(
        [
            {
                "person_name": "Someone Fabricated",
                "claimed_title": "CEO",
                "claimed_team": None,
                "source_url": "https://blog.acme.example/team/jane-doe",
                "evidence_kind": "search_snippet",
                "confidence": "verified",
            }
        ]
    )
    _patch_llm(monkeypatch, content=fabricated)

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/contacts")

    assert response.status_code == 201
    assert response.json()["candidates"] == []


def test_generate_contacts_not_found_returns_404() -> None:
    supabase = _FakeSupabaseClient(applications=_FakeTable(select_rows=[]))
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/contacts")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# ── Phase K: the X/Twitter lane's company-tier gate ─────────────────────

_FIRECRAWL_CREDENTIAL_ROW = {
    "provider": "firecrawl",
    "model": None,
    "base_url": None,
    "secret_encrypted": "fc-cipher",
}


def test_generate_contacts_includes_the_x_lane_for_a_non_fortune_500_company(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-empty tier index that simply doesn't list this company is
    the real "presumed startup" signal -- Acme isn't in the seeded rows
    below, but WidgetCo is, proving the index itself loaded correctly."""
    supabase = _FakeSupabaseClient(
        provider_credentials={
            ("llm", "openrouter"): _LLM_CREDENTIAL_ROW,
            ("search", "you_com"): _YOU_COM_CREDENTIAL_ROW,
            ("search", "firecrawl"): _FIRECRAWL_CREDENTIAL_ROW,
        },
        company_tiers=[{"normalized_name": "widgetco", "weight": 1.0}],
    )
    http = _FakeHttpClient()
    _patch_llm(monkeypatch)

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/contacts")

    assert response.status_code == 201
    # 3 untagged queries -> you_com, 1 LinkedIn hiring-post query + 2 new
    # X/Twitter queries (all firecrawl-tagged) -> firecrawl = 6 total.
    assert len(http.post_calls) == 6


def test_generate_contacts_excludes_the_x_lane_for_a_fortune_500_company(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supabase = _FakeSupabaseClient(
        provider_credentials={
            ("llm", "openrouter"): _LLM_CREDENTIAL_ROW,
            ("search", "you_com"): _YOU_COM_CREDENTIAL_ROW,
            ("search", "firecrawl"): _FIRECRAWL_CREDENTIAL_ROW,
        },
        company_tiers=[{"normalized_name": "acme", "weight": 1.0}],
    )
    http = _FakeHttpClient()
    _patch_llm(monkeypatch)

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/contacts")

    assert response.status_code == 201
    # No X lane -- 3 untagged queries -> you_com, 1 hiring-post -> firecrawl.
    assert len(http.post_calls) == 4


def test_generate_contacts_excludes_the_x_lane_when_the_tier_index_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty tier index (the fetch failed, or the table is genuinely
    empty) must never be read as "every company is a startup" -- the
    same fail-open-signal-corruption class already fixed elsewhere in
    this codebase for the location filter."""
    supabase = _FakeSupabaseClient(
        provider_credentials={
            ("llm", "openrouter"): _LLM_CREDENTIAL_ROW,
            ("search", "you_com"): _YOU_COM_CREDENTIAL_ROW,
            ("search", "firecrawl"): _FIRECRAWL_CREDENTIAL_ROW,
        },
        company_tiers=[],
    )
    http = _FakeHttpClient()
    _patch_llm(monkeypatch)

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/contacts")

    assert response.status_code == 201
    assert len(http.post_calls) == 4


# ── Phase C: enrichment ──────────────────────────────────────────────────

_CANDIDATE_ID = "80000000-0000-0000-0000-000000000001"
_RUN_ID = "70000000-0000-0000-0000-000000000001"
_CANDIDATE_ROW = {
    "id": _CANDIDATE_ID,
    "run_id": _RUN_ID,
    "person_name": "Jane Doe",
    "company": "Acme",
    "claimed_title": "Technical Recruiter",
}
_RUN_ROW = {"id": _RUN_ID, "user_id": _USER_ID}
_APOLLO_CREDENTIAL_ROW = {
    "provider": "apollo",
    "model": None,
    "base_url": None,
    "secret_encrypted": "apollo-cipher",
}


def test_enrich_contact_with_no_apollo_key_returns_setup_required() -> None:
    supabase = _FakeSupabaseClient(
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_CANDIDATE_ROW)]),
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/enrich")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SETUP_REQUIRED"


def test_enrich_contact_success_persists_the_real_email() -> None:
    supabase = _FakeSupabaseClient(
        provider_credentials={("search", "apollo"): _APOLLO_CREDENTIAL_ROW},
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_CANDIDATE_ROW)]),
    )
    http = _FakeHttpClient(
        apollo_body={"person": {"email": "jane.doe@acme.example", "email_status": "verified"}}
    )

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/enrich")

    assert response.status_code == 200
    body = response.json()
    assert body["enriched_email"] == "jane.doe@acme.example"
    assert body["enrichment_provider"] == "apollo"


def test_enrich_contact_never_reveals_personal_data() -> None:
    supabase = _FakeSupabaseClient(
        provider_credentials={("search", "apollo"): _APOLLO_CREDENTIAL_ROW},
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_CANDIDATE_ROW)]),
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        client.post(f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/enrich")

    apollo_calls = [c for c in http.post_calls if "apollo.io" in c]
    assert len(apollo_calls) == 1


def test_enrich_contact_for_a_candidate_owned_by_someone_else_returns_404() -> None:
    # `_ChainBuilder.eq()` is a no-op stub (returns every seeded row
    # regardless of the filter), so an empty `contact_research_runs`
    # table is how this fake represents "the real .eq('user_id', ...)
    # filter found nothing" -- the same technique
    # test_get_owned_candidate_raises_when_the_run_belongs_to_someone_else
    # already uses in test_contact_research_store.py.
    supabase = _FakeSupabaseClient(
        provider_credentials={("search", "apollo"): _APOLLO_CREDENTIAL_ROW},
        contact_research_runs=_FakeTable(select_rows=[]),
        contact_candidates=_FakeTable(select_rows=[dict(_CANDIDATE_ROW)]),
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/enrich")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# ── Phase K: Hunter as a second enrichment provider ──────────────────────

_HUNTER_CREDENTIAL_ROW = {
    "provider": "hunter",
    "model": None,
    "base_url": None,
    "secret_encrypted": "hunter-cipher",
}
_EXA_CREDENTIAL_ROW = {
    "provider": "exa",
    "model": None,
    "base_url": None,
    "secret_encrypted": "exa-cipher",
}


def test_enrich_contact_uses_hunter_directly_when_apollo_not_configured() -> None:
    supabase = _FakeSupabaseClient(
        provider_credentials={("search", "hunter"): _HUNTER_CREDENTIAL_ROW},
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_CANDIDATE_ROW)]),
    )
    http = _FakeHttpClient(
        hunter_body={
            "data": {"email": "jane.doe@acme.example", "verification": {"status": "valid"}}
        }
    )

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/enrich")

    assert response.status_code == 200
    body = response.json()
    assert body["enriched_email"] == "jane.doe@acme.example"
    assert body["enrichment_provider"] == "hunter"
    assert not any("apollo.io" in c for c in http.post_calls)


def test_enrich_contact_falls_back_to_hunter_when_apollo_finds_nothing() -> None:
    supabase = _FakeSupabaseClient(
        provider_credentials={
            ("search", "apollo"): _APOLLO_CREDENTIAL_ROW,
            ("search", "hunter"): _HUNTER_CREDENTIAL_ROW,
        },
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_CANDIDATE_ROW)]),
    )
    http = _FakeHttpClient(
        apollo_body={"person": None},
        hunter_body={
            "data": {"email": "jane.doe@acme.example", "verification": {"status": "valid"}}
        },
    )

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/enrich")

    assert response.status_code == 200
    body = response.json()
    assert body["enriched_email"] == "jane.doe@acme.example"
    assert body["enrichment_provider"] == "hunter"
    assert any("apollo.io" in c for c in http.post_calls)
    assert any("hunter.io" in c for c in http.get_calls)


def test_enrich_contact_does_not_call_hunter_when_apollo_already_found_an_email() -> None:
    supabase = _FakeSupabaseClient(
        provider_credentials={
            ("search", "apollo"): _APOLLO_CREDENTIAL_ROW,
            ("search", "hunter"): _HUNTER_CREDENTIAL_ROW,
        },
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_CANDIDATE_ROW)]),
    )
    http = _FakeHttpClient(
        apollo_body={"person": {"email": "jane.doe@acme.example", "email_status": "verified"}}
    )

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/enrich")

    assert response.status_code == 200
    assert response.json()["enrichment_provider"] == "apollo"
    assert not any("hunter.io" in c for c in http.get_calls)


def test_enrich_contact_falls_through_to_hunter_when_apollo_key_is_rejected() -> None:
    supabase = _FakeSupabaseClient(
        provider_credentials={
            ("search", "apollo"): _APOLLO_CREDENTIAL_ROW,
            ("search", "hunter"): _HUNTER_CREDENTIAL_ROW,
        },
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_CANDIDATE_ROW)]),
    )
    http = _FakeHttpClient(
        apollo_status_code=401,
        hunter_body={
            "data": {"email": "jane.doe@acme.example", "verification": {"status": "valid"}}
        },
    )

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/enrich")

    assert response.status_code == 200
    assert response.json()["enrichment_provider"] == "hunter"


def test_enrich_contact_propagates_apollo_error_when_hunter_not_configured() -> None:
    """A user without Hunter configured still sees Apollo's own real
    error, unchanged from before Hunter existed."""
    supabase = _FakeSupabaseClient(
        provider_credentials={("search", "apollo"): _APOLLO_CREDENTIAL_ROW},
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_CANDIDATE_ROW)]),
    )
    http = _FakeHttpClient(apollo_status_code=401)

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/enrich")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "PROVIDER_REJECTED"


def test_enrich_contact_keeps_apollos_empty_result_when_hunter_then_fails() -> None:
    """Regression test for a real, adversarially-confirmed bug: Hunter's
    own failure (after Apollo already produced a valid, if empty, real
    result) used to propagate uncaught and discard Apollo's completed
    lookup. It must now fall back to Apollo's own "no email found"
    result instead of hard-erroring the whole request."""
    supabase = _FakeSupabaseClient(
        provider_credentials={
            ("search", "apollo"): _APOLLO_CREDENTIAL_ROW,
            ("search", "hunter"): _HUNTER_CREDENTIAL_ROW,
        },
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_CANDIDATE_ROW)]),
    )
    http = _FakeHttpClient(apollo_body={"person": None}, hunter_status_code=401)

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/enrich")

    assert response.status_code == 200
    body = response.json()
    assert body["enriched_email"] is None
    assert body["enrichment_provider"] == "apollo"


def test_enrich_contact_raises_when_both_apollo_and_hunter_fail() -> None:
    supabase = _FakeSupabaseClient(
        provider_credentials={
            ("search", "apollo"): _APOLLO_CREDENTIAL_ROW,
            ("search", "hunter"): _HUNTER_CREDENTIAL_ROW,
        },
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_CANDIDATE_ROW)]),
    )
    http = _FakeHttpClient(apollo_status_code=401, hunter_status_code=401)

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/enrich")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "PROVIDER_REJECTED"


def test_enrich_contact_propagates_hunter_error_when_hunter_is_the_only_provider() -> None:
    supabase = _FakeSupabaseClient(
        provider_credentials={("search", "hunter"): _HUNTER_CREDENTIAL_ROW},
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_CANDIDATE_ROW)]),
    )
    http = _FakeHttpClient(hunter_status_code=401)

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/enrich")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "PROVIDER_REJECTED"


def test_enrich_contact_with_neither_provider_mentions_both_in_setup_required() -> None:
    supabase = _FakeSupabaseClient(
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_CANDIDATE_ROW)]),
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/enrich")

    assert response.status_code == 409
    message = response.json()["error"]["message"]
    assert "Apollo" in message and "Hunter" in message


# ── Phase K: Exa LinkedIn discovery ──────────────────────────────────────


def test_find_linkedin_with_no_exa_key_returns_setup_required() -> None:
    supabase = _FakeSupabaseClient(
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_CANDIDATE_ROW)]),
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/find-linkedin"
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SETUP_REQUIRED"


def test_find_linkedin_success_persists_the_discovered_url() -> None:
    supabase = _FakeSupabaseClient(
        provider_credentials={("search", "exa"): _EXA_CREDENTIAL_ROW},
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_CANDIDATE_ROW)]),
    )
    http = _FakeHttpClient(
        exa_body={
            "results": [
                {
                    "url": "https://www.linkedin.com/in/janedoe",
                    "entities": [{"type": "person", "properties": {"name": "Jane Doe"}}],
                }
            ]
        }
    )

    with _client(supabase, http) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/find-linkedin"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["discovered_linkedin_url"] == "https://www.linkedin.com/in/janedoe"
    assert body["linkedin_discovery_provider"] == "exa"
    assert body["linkedin_discovery_confidence"] == "strong"


def test_find_linkedin_for_a_candidate_owned_by_someone_else_returns_404() -> None:
    supabase = _FakeSupabaseClient(
        provider_credentials={("search", "exa"): _EXA_CREDENTIAL_ROW},
        contact_research_runs=_FakeTable(select_rows=[]),
        contact_candidates=_FakeTable(select_rows=[dict(_CANDIDATE_ROW)]),
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/find-linkedin"
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# ── Phase E: outreach drafting ───────────────────────────────────────────

_EVIDENCE_ROW = {
    "id": "ev-1",
    "candidate_id": _CANDIDATE_ID,
    "source_title": "Jane Doe presents at PyData",
    "source_snippet": "Jane Doe gave a talk on scaling ML infra at PyData NYC.",
    "confidence": "verified",
    "observed_at": "2026-08-01T00:00:00Z",
}
_DRAFT_LLM_RESPONSE = json.dumps(
    {
        "subject": "Loved your PyData talk",
        "email_body": "Saw your PyData talk on scaling ML infra -- would love to chat about the "
        "role.",
        "linkedin_message": "Saw your PyData talk on scaling ML infra -- would love to connect.",
        "follow_up_message": "Following up in case my last note got buried!",
    }
)


def test_get_outreach_draft_returns_none_when_nothing_generated_yet() -> None:
    supabase = _FakeSupabaseClient(
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_CANDIDATE_ROW)]),
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.get(
            f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/draft-outreach"
        )

    assert response.status_code == 200
    assert response.json() == {"draft": None}


def test_generate_outreach_success_persists_the_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    supabase = _FakeSupabaseClient(
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_CANDIDATE_ROW)]),
        contact_candidate_evidence=_FakeTable(select_rows=[dict(_EVIDENCE_ROW)]),
    )
    http = _FakeHttpClient()
    _patch_llm(monkeypatch, content=_DRAFT_LLM_RESPONSE)

    with _client(supabase, http) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/draft-outreach"
        )

    assert response.status_code == 201
    body = response.json()
    assert body["draft"]["subject"] == "Loved your PyData talk"
    assert body["draft"]["hook_evidence_id"] == "ev-1"


def test_generate_outreach_with_no_evidence_returns_insufficient_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supabase = _FakeSupabaseClient(
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_CANDIDATE_ROW)]),
        contact_candidate_evidence=_FakeTable(select_rows=[]),
    )
    http = _FakeHttpClient()
    _patch_llm(monkeypatch, content=_DRAFT_LLM_RESPONSE)

    with _client(supabase, http) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/draft-outreach"
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INSUFFICIENT_EVIDENCE"


def test_generate_outreach_for_a_candidate_owned_by_someone_else_returns_404() -> None:
    supabase = _FakeSupabaseClient(
        contact_research_runs=_FakeTable(select_rows=[]),
        contact_candidates=_FakeTable(select_rows=[dict(_CANDIDATE_ROW)]),
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/draft-outreach"
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# ── Phase F: push to Gmail ────────────────────────────────────────────────

_ENRICHED_CANDIDATE_ROW = {**_CANDIDATE_ROW, "enriched_email": "jane.doe@acme.example"}
_DRAFT_ROW = {
    "id": "draft-1",
    "candidate_id": _CANDIDATE_ID,
    "subject": "Loved your PyData talk",
    "email_body": "Saw your PyData talk -- would love to chat about the role.",
    "gmail_draft_id": None,
}
_GMAIL_CREDENTIAL_ROW = {
    "provider": "gmail",
    "model": None,
    "base_url": None,
    "secret_encrypted": "rt-cipher",
}


def test_push_outreach_to_gmail_with_no_enriched_email_returns_invalid_input() -> None:
    supabase = _FakeSupabaseClient(
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_CANDIDATE_ROW)]),
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/push-to-gmail"
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"
    assert "work email" in response.json()["error"]["message"]


def test_push_outreach_to_gmail_with_no_draft_returns_invalid_input() -> None:
    supabase = _FakeSupabaseClient(
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_ENRICHED_CANDIDATE_ROW)]),
        outreach_drafts=_FakeTable(select_rows=[]),
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/push-to-gmail"
        )

    assert response.status_code == 422
    assert "Draft an outreach message" in response.json()["error"]["message"]


def test_push_outreach_to_gmail_with_no_gmail_connection_returns_setup_required() -> None:
    supabase = _FakeSupabaseClient(
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_ENRICHED_CANDIDATE_ROW)]),
        outreach_drafts=_FakeTable(select_rows=[dict(_DRAFT_ROW)]),
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/push-to-gmail"
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SETUP_REQUIRED"


def test_push_outreach_to_gmail_on_a_server_without_gmail_is_feature_disabled(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Not "Connect Gmail first": there is nothing to connect on this server, and the Integrations
    button would only say the same. Answered before the person's connection is looked up."""
    for name in ("CLIENT_ID", "CLIENT_SECRET", "REDIRECT_URI"):
        monkeypatch.delenv(f"GOOGLE_OAUTH_{name}")
    supabase = _FakeSupabaseClient(
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_ENRICHED_CANDIDATE_ROW)]),
        outreach_drafts=_FakeTable(select_rows=[dict(_DRAFT_ROW)]),
    )
    http = _FakeHttpClient()

    with caplog.at_level(logging.DEBUG), _client(supabase, http) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/push-to-gmail"
        )

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "FEATURE_DISABLED"
    assert error["message"] == "Gmail isn't set up on this server."
    assert error["settings_path"] is None
    assert not any(r.levelno >= logging.CRITICAL for r in caplog.records)
    assert http.post_calls == []


def test_an_already_pushed_draft_is_returned_even_on_a_server_that_has_since_lost_gmail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("CLIENT_ID", "CLIENT_SECRET", "REDIRECT_URI"):
        monkeypatch.delenv(f"GOOGLE_OAUTH_{name}")
    already_pushed = {**_DRAFT_ROW, "gmail_draft_id": "d-1", "gmail_thread_id": "t-1"}
    supabase = _FakeSupabaseClient(
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_ENRICHED_CANDIDATE_ROW)]),
        outreach_drafts=_FakeTable(select_rows=[already_pushed]),
    )

    with _client(supabase, _FakeHttpClient()) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/push-to-gmail"
        )

    assert response.status_code == 200
    assert response.json()["gmail_draft_id"] == "d-1"


def test_push_outreach_to_gmail_success_creates_a_real_gmail_draft() -> None:
    supabase = _FakeSupabaseClient(
        provider_credentials={("oauth", "gmail"): _GMAIL_CREDENTIAL_ROW},
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_ENRICHED_CANDIDATE_ROW)]),
        outreach_drafts=_FakeTable(select_rows=[dict(_DRAFT_ROW)]),
    )
    http = _FakeHttpClient(
        gmail_draft_body={
            "id": "gmail-draft-xyz",
            "message": {"id": "msg-xyz", "threadId": "gmail-thread-xyz"},
        }
    )

    with _client(supabase, http) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/push-to-gmail"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["gmail_draft_id"] == "gmail-draft-xyz"
    assert body["gmail_thread_id"] == "gmail-thread-xyz"
    assert any("gmail.googleapis.com" in c for c in http.post_calls)
    assert not any("gmail.googleapis.com" in c and "send" in c for c in http.post_calls)


def test_push_outreach_to_gmail_is_idempotent_on_a_second_call() -> None:
    """A real already-pushed row always has gmail_thread_id set alongside
    gmail_draft_id (mark_pushed_to_gmail writes both together) -- the
    fixture and assertion here mirror that, closing a real, adversarially
    -confirmed gap where this test previously couldn't have caught a
    regression that stopped persisting/returning gmail_thread_id on the
    idempotent short-circuit path."""
    already_pushed = {
        **_DRAFT_ROW,
        "gmail_draft_id": "gmail-draft-existing",
        "gmail_thread_id": "gmail-thread-existing",
    }
    supabase = _FakeSupabaseClient(
        provider_credentials={("oauth", "gmail"): _GMAIL_CREDENTIAL_ROW},
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_ENRICHED_CANDIDATE_ROW)]),
        outreach_drafts=_FakeTable(select_rows=[dict(already_pushed)]),
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/push-to-gmail"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["gmail_draft_id"] == "gmail-draft-existing"
    assert body["gmail_thread_id"] == "gmail-thread-existing"
    # never re-called Gmail for an already-pushed draft
    assert not any("gmail.googleapis.com" in c for c in http.post_calls)


# ── Gmail for invited people only, and a connection Google stopped honouring ─────────────────

_OTHER_USER_ID = "00000000-0000-0000-0000-000000000002"
_PUSH_URL = f"/applications/{_APPLICATION_ID}/contacts/{_CANDIDATE_ID}/push-to-gmail"
_RECONNECT_REASON = "gmail_reconnect_required"
_TOKEN_TEXT = "decrypted-secret"  # what the fake database hands back as the refresh token
_GOOGLE_FREE_TEXT = "Token has been expired or revoked. (free text from Google)"


def _push_world(
    *, credential: dict[str, Any] | None = None, **http_options: Any
) -> tuple[_FakeSupabaseClient, _FakeHttpClient]:
    supabase = _FakeSupabaseClient(
        provider_credentials={("oauth", "gmail"): credential or dict(_GMAIL_CREDENTIAL_ROW)},
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_ENRICHED_CANDIDATE_ROW)]),
        outreach_drafts=_FakeTable(select_rows=[dict(_DRAFT_ROW)]),
    )
    return supabase, _FakeHttpClient(**http_options)


def test_a_person_the_gmail_list_does_not_name_gets_the_not_available_answer_and_no_google_call(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The connection is there (made before they were removed from the list), and it is not
    used: not read, not refreshed, not asked to create anything."""
    monkeypatch.setenv("GMAIL_ALLOWED_USER_IDS", _OTHER_USER_ID)
    supabase, http = _push_world()
    credential = supabase._credential_table

    with caplog.at_level(logging.DEBUG), _client(supabase, http) as client:
        response = client.post(_PUSH_URL)

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "FEATURE_DISABLED"
    assert error["message"] == "Gmail isn't available for your account yet."
    assert error["settings_path"] is None and error["details"] == {}
    assert _OTHER_USER_ID not in response.text  # nothing about who is on the list
    assert http.post_calls == [] and http.get_calls == []
    assert credential.reads == 0
    assert supabase.outreach_drafts.update_calls == []
    assert [r for r in caplog.records if "gmail" in r.getMessage().lower()] == []


def test_a_person_the_gmail_list_names_pushes_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GMAIL_ALLOWED_USER_IDS", f"{_OTHER_USER_ID}, {_USER_ID.upper()}")
    supabase, http = _push_world()

    with _client(supabase, http) as client:
        response = client.post(_PUSH_URL)

    assert response.status_code == 200
    assert response.json()["gmail_draft_id"] == "gmail-draft-1"
    assert any("gmail.googleapis.com" in c for c in http.post_calls)


def test_the_list_does_not_hide_a_draft_that_was_already_pushed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returning the existing state touches neither Google nor the connection."""
    monkeypatch.setenv("GMAIL_ALLOWED_USER_IDS", _OTHER_USER_ID)
    already_pushed = {**_DRAFT_ROW, "gmail_draft_id": "d-1", "gmail_thread_id": "t-1"}
    supabase = _FakeSupabaseClient(
        contact_research_runs=_FakeTable(select_rows=[_RUN_ROW]),
        contact_candidates=_FakeTable(select_rows=[dict(_ENRICHED_CANDIDATE_ROW)]),
        outreach_drafts=_FakeTable(select_rows=[already_pushed]),
    )

    with _client(supabase, _FakeHttpClient()) as client:
        response = client.post(_PUSH_URL)

    assert response.status_code == 200
    assert response.json()["gmail_draft_id"] == "d-1"


def _assert_reconnect_answer(response: Any) -> None:
    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "PROVIDER_REJECTED"
    assert error["details"] == {"reason": _RECONNECT_REASON}
    assert error["settings_path"] == "/profile/integrations"
    assert error["retryable"] is False
    assert "Reconnect Gmail" in error["message"]
    for leaked in (_TOKEN_TEXT, "secret-456", _GOOGLE_FREE_TEXT, "invalid_grant"):
        assert leaked not in response.text


def test_google_refusing_the_refresh_token_flags_the_connection_and_asks_for_a_reconnect(
    caplog: pytest.LogCaptureFixture,
) -> None:
    supabase, http = _push_world(
        google_token_status_code=400,
        google_token_body={"error": "invalid_grant", "error_description": _GOOGLE_FREE_TEXT},
    )

    with caplog.at_level(logging.DEBUG), _client(supabase, http) as client:
        first = client.post(_PUSH_URL)
        calls_after_first = list(http.post_calls)
        second = client.post(_PUSH_URL)

    _assert_reconnect_answer(first)
    _assert_reconnect_answer(second)
    # flagged, not deleted: the row and its secret are still there
    row = supabase._credential_table._rows[("oauth", "gmail")]
    assert row["is_validated"] is False and row["secret_encrypted"] == "rt-cipher"
    # the second click did not go to Google at all
    assert calls_after_first == [c for c in http.post_calls if "oauth2.googleapis.com" in c]
    assert len(calls_after_first) == 1 and http.post_calls == calls_after_first
    # one WARNING for the transition, with no token and nothing Google said
    flagged = [r for r in caplog.records if r.getMessage().startswith("a Gmail connection needs")]
    assert len(flagged) == 1 and flagged[0].levelno == logging.WARNING
    assert flagged[0].ctx == {"user_id": _USER_ID, "reason": _RECONNECT_REASON}  # type: ignore[attr-defined]
    for leaked in (_TOKEN_TEXT, "secret-456", _GOOGLE_FREE_TEXT):
        assert leaked not in caplog.text


def test_a_connection_that_is_already_flagged_is_not_tried_again() -> None:
    supabase, http = _push_world(credential={**_GMAIL_CREDENTIAL_ROW, "is_validated": False})

    with _client(supabase, http) as client:
        response = client.post(_PUSH_URL)

    _assert_reconnect_answer(response)
    assert http.post_calls == []
    assert supabase.outreach_drafts.update_calls == []


def test_gmail_answering_401_after_a_fresh_token_flags_the_connection() -> None:
    supabase, http = _push_world(
        gmail_draft_status_code=401,
        gmail_draft_body={"error": {"code": 401, "message": _GOOGLE_FREE_TEXT}},
    )

    with _client(supabase, http) as client:
        response = client.post(_PUSH_URL)

    _assert_reconnect_answer(response)
    assert supabase._credential_table._rows[("oauth", "gmail")]["is_validated"] is False


def test_a_google_outage_is_retryable_and_never_flags_the_connection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    for token_status, draft_status in ((503, 200), (500, 200), (200, 503), (200, 500)):
        supabase, http = _push_world(
            google_token_status_code=token_status,
            google_token_body={"access_token": "at-1"} if token_status == 200 else {},
            gmail_draft_status_code=draft_status,
            gmail_draft_body={},
        )
        with caplog.at_level(logging.DEBUG), _client(supabase, http) as client:
            response = client.post(_PUSH_URL)

        assert response.status_code == 503, (token_status, draft_status)
        error = response.json()["error"]
        assert (error["code"], error["retryable"]) == ("PROVIDER_UNAVAILABLE", True)
        assert error["details"] == {}
        row = supabase._credential_table._rows[("oauth", "gmail")]
        assert row.get("is_validated", True) is True, (token_status, draft_status)
    assert not any(r.getMessage().startswith("a Gmail connection needs") for r in caplog.records)


def test_a_network_failure_is_retryable_and_never_flags_the_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supabase, http = _push_world()

    async def unreachable(url: str, **_kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError("no route", request=httpx.Request("POST", url))

    monkeypatch.setattr(http, "post", unreachable)

    with _client(supabase, http) as client:
        response = client.post(_PUSH_URL)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "PROVIDER_UNAVAILABLE"
    assert supabase._credential_table._rows[("oauth", "gmail")].get("is_validated", True) is True


def test_a_server_side_client_problem_is_not_a_reason_to_send_the_person_to_reconnect() -> None:
    """invalid_client means the operator's OAuth client settings are wrong. Every user would be
    flagged and told to reconnect, and it could not help."""
    supabase, http = _push_world(
        google_token_status_code=401, google_token_body={"error": "invalid_client"}
    )

    with _client(supabase, http) as client:
        response = client.post(_PUSH_URL)

    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "PROVIDER_REJECTED" and error["details"] == {}
    assert supabase._credential_table._rows[("oauth", "gmail")].get("is_validated", True) is True
