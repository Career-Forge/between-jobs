"""Tests for the company-intel HTTP endpoints (Horizon Sprint 5.0).

Exercises the real FastAPI routes via TestClient. Outbound calls to
You.com/Firecrawl/OpenRouter are faked at the httpx client boundary
(get_http_client's override) and the pipeline's own `generate` injection
point respectively -- never a real network call or real spend in a unit
test.
"""

from __future__ import annotations

import json
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
        self._next_id = 1

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def insert(self, data: Any) -> _ChainBuilder:
        # Mirrors real Postgrest insert-with-representation: every row
        # gets a DB-assigned id and becomes visible to a later select(),
        # same as `id uuid primary key default gen_random_uuid()` would.
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


class _CredentialTable:
    """Keyed by (service, provider), unlike `_FakeTable` -- this route
    resolves THREE distinct credentials in one call (LLM, You.com,
    Firecrawl) and needs to answer each query differently, the same
    reasoning test_credential_resolver.py's own `_CredentialTable`
    already established."""

    def __init__(self, rows: dict[tuple[str, str], dict[str, Any]]) -> None:
        self._rows = rows
        self._service = ""
        self._provider = ""

    def select(self, *_: Any, **__: Any) -> _CredentialTable:
        return self

    def eq(self, column: str, value: Any) -> _CredentialTable:
        if column == "service":
            self._service = value
        if column == "provider":
            self._provider = value
        return self

    async def execute(self) -> SimpleNamespace:
        row = self._rows.get((self._service, self._provider))
        return SimpleNamespace(data=[row] if row else [])


class _FakeRpcBuilder:
    def __init__(self, data: Any) -> None:
        self._data = data

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._data)


class _FakeSupabaseClient:
    def __init__(
        self,
        *,
        applications: _FakeTable | None = None,
        capability_preferences: _FakeTable | None = None,
        provider_credentials: dict[tuple[str, str], dict[str, Any]] | None = None,
        company_intel_runs: _FakeTable | None = None,
        company_intel_claims: _FakeTable | None = None,
        interview_process_registry: _FakeTable | None = None,
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
        self.company_intel_runs = company_intel_runs or _FakeTable(
            select_rows=[], insert_row={"id": "run-1", "application_id": _APPLICATION_ID}
        )
        self.company_intel_claims = company_intel_claims or _FakeTable(select_rows=[])
        self.interview_process_registry = interview_process_registry or _FakeTable(select_rows=[])

    def table(self, name: str) -> Any:
        return {
            "applications": self.applications,
            "job_snapshots": self.job_snapshots,
            "capability_preferences": self.capability_preferences,
            "provider_credentials": self._credential_table,
            "company_intel_runs": self.company_intel_runs,
            "company_intel_claims": self.company_intel_claims,
            "interview_process_registry": self.interview_process_registry,
        }[name]

    def rpc(self, fn: str, _params: dict[str, Any]) -> _FakeRpcBuilder:
        if fn == "decrypt_secret":
            return _FakeRpcBuilder("decrypted-secret")
        raise AssertionError(f"unexpected rpc: {fn}")


_HIT_JSON = {
    "results": {
        "web": [
            {
                "title": "Acme raises Series B",
                "url": "https://news.example/acme",
                "snippets": ["Acme raised $50M."],
                "page_age": "2026-08-01",
            }
        ]
    }
}

_CLAIM_LLM_RESPONSE = json.dumps(
    [
        {
            "category": "funding_and_financial_health",
            "claim_text": "Acme raised a Series B.",
            "source_url": "https://news.example/acme",
            "confidence": "high",
        }
    ]
)

_MULTI_CLAIM_LLM_RESPONSE = json.dumps(
    [
        {
            "category": "funding_and_financial_health",
            "claim_text": "Acme raised a Series B.",
            "source_url": "https://news.example/acme",
            "confidence": "high",
        },
        {
            "category": "hiring_activity",
            "claim_text": "Acme is hiring engineers.",
            "source_url": "https://news.example/acme",
            "confidence": "medium",
        },
    ]
)


class _FakeHttpClient:
    """Fakes the You.com/Firecrawl HTTP boundary. The LLM call bypasses
    httpx entirely (llm_client.generate uses the openai SDK), so the
    route itself is monkeypatched to inject a fake `generate` -- see
    `_patch_llm`."""

    def __init__(self, *, status_code: int = 200) -> None:
        self.status_code = status_code
        self.post_calls: list[str] = []

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.post_calls.append(url)
        return httpx.Response(
            status_code=self.status_code, json=_HIT_JSON, request=httpx.Request("POST", url)
        )


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")


@pytest.fixture(autouse=True)
def _clear_overrides() -> Any:
    yield
    app.dependency_overrides.clear()


def _patch_llm(monkeypatch: pytest.MonkeyPatch, content: str = _CLAIM_LLM_RESPONSE) -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=content)

    # The route passes `generate=llm_generate` explicitly to
    # synthesize_dossier (rather than relying on the pipeline module's
    # own default parameter, which binds at function-definition time and
    # so can't be patched after the fact) -- patch the name where the
    # route itself looks it up.
    monkeypatch.setattr("between_jobs.api.company_intel_routes.llm_generate", fake_generate)


# InterviewForge R1: the SAME patched `llm_generate` name serves BOTH the
# claims-synthesis call and the new registry-synthesis call (the route
# passes it explicitly to both) -- dispatch on which system prompt is being
# asked for, the one thing that actually differs between the two calls.
_REGISTRY_SYNTHESIS_PROMPT_MARKER = "You structure ALREADY-VERIFIED"


def _patch_llm_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    claims_content: str,
    registry_content: str | Exception,
) -> None:
    async def fake_generate(**kwargs: Any) -> LLMResponse:
        if _REGISTRY_SYNTHESIS_PROMPT_MARKER in kwargs.get("system_prompt", ""):
            if isinstance(registry_content, Exception):
                raise registry_content
            return LLMResponse(content=registry_content)
        return LLMResponse(content=claims_content)

    monkeypatch.setattr("between_jobs.api.company_intel_routes.llm_generate", fake_generate)


def _client(supabase: _FakeSupabaseClient, http: _FakeHttpClient) -> TestClient:
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    app.dependency_overrides[get_http_client] = lambda: http
    return TestClient(app)


def test_get_company_intel_returns_none_when_nothing_generated_yet() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}/company-intel")

    assert response.status_code == 200
    assert response.json() == {"run": None, "claims": []}


def test_generate_company_intel_with_no_search_provider_returns_setup_required() -> None:
    supabase = _FakeSupabaseClient(
        provider_credentials={("llm", "openrouter"): _LLM_CREDENTIAL_ROW}
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/company-intel")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SETUP_REQUIRED"
    assert http.post_calls == []


def test_generate_company_intel_with_no_llm_returns_setup_required() -> None:
    supabase = _FakeSupabaseClient(
        capability_preferences=_FakeTable(select_rows=[]),
        provider_credentials={("search", "you_com"): _YOU_COM_CREDENTIAL_ROW},
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/company-intel")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SETUP_REQUIRED"


def test_generate_company_intel_success_stores_run_and_claims(
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
        response = client.post(f"/applications/{_APPLICATION_ID}/company-intel")

    assert response.status_code == 201
    body = response.json()
    assert len(body["claims"]) == 1
    assert body["claims"][0]["category"] == "funding_and_financial_health"
    assert len(supabase.company_intel_runs.insert_calls) == 1
    assert supabase.company_intel_runs.insert_calls[0]["company_name"] == "Acme"
    # 7 bounded queries, all against You.com since it's the only search
    # credential configured
    assert len(http.post_calls) == 7


def test_generate_company_intel_response_claims_have_unique_non_empty_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for a React "duplicate key" bug in
    CompanyIntelPanel: the POST response used to echo the pre-insert
    claim dicts straight from the pipeline, which never carry an `id`
    (ids are DB-assigned on insert) -- every claim in the response
    shared the same missing id, which is exactly what the frontend's
    `key={claim.id}` collided on."""
    supabase = _FakeSupabaseClient(
        provider_credentials={
            ("llm", "openrouter"): _LLM_CREDENTIAL_ROW,
            ("search", "you_com"): _YOU_COM_CREDENTIAL_ROW,
        }
    )
    http = _FakeHttpClient()
    _patch_llm(monkeypatch, content=_MULTI_CLAIM_LLM_RESPONSE)

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/company-intel")

    assert response.status_code == 201
    claims = response.json()["claims"]
    assert len(claims) == 2
    ids = [c.get("id") for c in claims]
    assert all(ids)
    assert len(set(ids)) == len(ids)


def test_generate_company_intel_not_found_returns_404() -> None:
    supabase = _FakeSupabaseClient(applications=_FakeTable(select_rows=[]))
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/company-intel")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# ── InterviewForge R1: registry population (interviewforge-v1.md) ───────────

_INTERVIEW_CLAIM_LLM_RESPONSE = json.dumps(
    [
        {
            "category": "interview_process",
            "claim_text": "Candidates go through a recruiter screen then a technical interview.",
            "source_url": "https://news.example/acme",
            "confidence": "high",
        }
    ]
)

_REGISTRY_LLM_RESPONSE = json.dumps(
    {
        "rounds": [{"name": "Recruiter screen"}, {"name": "Technical interview"}],
        "typical_topics": ["algorithms"],
        "difficulty_signal": "medium",
        "values_signals": [],
    }
)


def test_generate_company_intel_creates_a_registry_entry_when_interview_process_claims_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supabase = _FakeSupabaseClient(
        provider_credentials={
            ("llm", "openrouter"): _LLM_CREDENTIAL_ROW,
            ("search", "you_com"): _YOU_COM_CREDENTIAL_ROW,
        }
    )
    http = _FakeHttpClient()
    _patch_llm_dispatch(
        monkeypatch,
        claims_content=_INTERVIEW_CLAIM_LLM_RESPONSE,
        registry_content=_REGISTRY_LLM_RESPONSE,
    )

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/company-intel")

    assert response.status_code == 201
    assert len(supabase.interview_process_registry.insert_calls) == 1
    inserted = supabase.interview_process_registry.insert_calls[0]
    assert inserted["company_name"] == "Acme"
    assert inserted["rounds"] == [{"name": "Recruiter screen"}, {"name": "Technical interview"}]
    assert inserted["source_run_id"] == "run-1"


def test_generate_company_intel_creates_no_registry_entry_without_interview_process_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supabase = _FakeSupabaseClient(
        provider_credentials={
            ("llm", "openrouter"): _LLM_CREDENTIAL_ROW,
            ("search", "you_com"): _YOU_COM_CREDENTIAL_ROW,
        }
    )
    http = _FakeHttpClient()
    # The default claim fixture is category "funding_and_financial_health" --
    # no interview_process claims, so there's nothing to structure and the
    # registry-synthesis call never even needs to run.
    _patch_llm(monkeypatch)

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/company-intel")

    assert response.status_code == 201
    assert supabase.interview_process_registry.insert_calls == []


def test_generate_company_intel_registry_synthesis_failure_does_not_fail_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The claims themselves are already persisted by the time registry
    synthesis runs -- a provider outage on this bonus side effect must
    never turn an otherwise-successful company-intel request into a 5xx."""
    supabase = _FakeSupabaseClient(
        provider_credentials={
            ("llm", "openrouter"): _LLM_CREDENTIAL_ROW,
            ("search", "you_com"): _YOU_COM_CREDENTIAL_ROW,
        }
    )
    http = _FakeHttpClient()
    _patch_llm_dispatch(
        monkeypatch,
        claims_content=_INTERVIEW_CLAIM_LLM_RESPONSE,
        registry_content=RuntimeError("provider outage"),
    )

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/company-intel")

    assert response.status_code == 201
    assert len(response.json()["claims"]) == 1
    assert supabase.interview_process_registry.insert_calls == []
