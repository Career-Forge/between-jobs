"""Tests for the positioning-brief HTTP endpoints (outreach-v2-search-
first.md Phase I). Exercises the real FastAPI routes via TestClient, same
shape as test_resume_documents_routes.py -- the outbound forge-engines
`/step0` call is faked at the httpx boundary; the brief's own LLM call is
faked by monkeypatching `positioning_brief_routes.llm_generate` directly,
matching every other multi-stage route in this codebase (the R3-bug-
informed seam: the route passes `generate=llm_generate` explicitly).
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
_PROFILE_VERSION_ID = "40000000-0000-0000-0000-000000000001"
_DOCUMENT_ID = "50000000-0000-0000-0000-000000000001"

_APPLICATION_ROW = {
    "id": _APPLICATION_ID,
    "user_id": _USER_ID,
    "active_job_snapshot_id": _SNAPSHOT_ID,
}
_SNAPSHOT_ROW = {
    "id": _SNAPSHOT_ID,
    "title": "Staff Engineer",
    "company_name": "Acme",
    "location_text": "Remote",
    "description_text": "Build a real-time platform.",
}
_PROFILE_VERSION_ROW = {
    "id": _PROFILE_VERSION_ID,
    "user_id": _USER_ID,
    "activated_at": "2026-08-01T00:00:00Z",
    "canonical_json": {
        "personal": {"name": "Jordan Rivera"},
        "skills": {"programming": ["Python"]},
    },
}
_DOCUMENT_ROW = {
    "id": _DOCUMENT_ID,
    "user_id": _USER_ID,
    "application_id": _APPLICATION_ID,
    "profile_version_id": _PROFILE_VERSION_ID,
    "job_snapshot_id": _SNAPSHOT_ID,
}
_PREFERENCE_ROW = {
    "capability": "default",
    "execution_mode": "byok_first_party",
    "provider": "openrouter",
    "model": "anthropic/claude-sonnet-4-6",
}
_CREDENTIAL_ROW = {
    "provider": "openrouter",
    "model": "anthropic/claude-sonnet-4-6",
    "base_url": None,
    "secret_encrypted": "ciphertext-abc",
}
_CAREER_FACTS_ROWS = [
    {"id": "fact-1", "value_json": {"title": "Staff Engineer", "bullets": ["Built with Python."]}}
]
# "kafka" deliberately appears nowhere in career_facts/skills above -- a
# real zero-match must-have cluster AND an "unsupported" key term, giving
# generate_positioning_brief a real gap to cite. "Python" is declared, a
# real "verified" strength to cite.
_STEP0_RESPONSE_BODY = {
    "clusters": [{"name": "Kafka Streaming", "priority": "must_have", "keywords": ["kafka"]}],
    "dealbreakers": [],
    "keyTerms": ["Python", "Kafka"],
}

_BRIEF_LLM_RESPONSE = json.dumps(
    {
        "lead_with": "You've verifiably built with Python.",
        "lead_with_citation": "Python",
        "gap_that_matters": "Kafka streaming experience is missing.",
        "gap_citation": "Kafka",
        "recommended_project": "Build a small Kafka consumer service to close that gap.",
    }
)


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def order(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def limit(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    @property
    def not_(self) -> _ChainBuilder:
        return self

    def is_(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTable:
    def __init__(
        self, *, select_rows: list[dict[str, Any]], insert_row: dict[str, Any] | None = None
    ) -> None:
        self.select_rows = select_rows
        self.insert_row = insert_row
        self.insert_calls: list[dict[str, Any]] = []

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def insert(self, data: dict[str, Any]) -> _ChainBuilder:
        self.insert_calls.append(data)
        rows = [self.insert_row] if self.insert_row is not None else [{**data, "id": "row-1"}]
        return _ChainBuilder(rows)


class _RealFilterQuery:
    """Unlike _ChainBuilder above (whose .eq() is a no-op, matching every
    other table in this file), this one genuinely filters -- used only
    for the one test proving GET /positioning-brief's user_id scoping is
    a real filter, not just a passed-but-ignored argument (a real IDOR an
    adversarial review caught, see positioning_brief_store.get_latest_
    brief)."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, column: str, value: Any) -> _RealFilterQuery:
        return _RealFilterQuery([row for row in self._rows if row.get(column) == value])

    def order(self, *_: Any, **__: Any) -> _RealFilterQuery:
        return self

    def limit(self, *_: Any, **__: Any) -> _RealFilterQuery:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _RealFilterTable:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def select(self, *_: Any, **__: Any) -> _RealFilterQuery:
        return _RealFilterQuery(self._rows)


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
        job_snapshots: _FakeTable | None = None,
        profile_versions: _FakeTable | None = None,
        resume_documents: _FakeTable | None = None,
        capability_preferences: _FakeTable | None = None,
        provider_credentials: _FakeTable | None = None,
        career_facts: _FakeTable | None = None,
        company_intel_runs: _FakeTable | None = None,
        company_intel_claims: _FakeTable | None = None,
        application_events: _FakeTable | None = None,
        positioning_briefs: _FakeTable | None = None,
    ) -> None:
        self.applications = applications or _FakeTable(select_rows=[_APPLICATION_ROW])
        self.job_snapshots = job_snapshots or _FakeTable(select_rows=[_SNAPSHOT_ROW])
        self.profile_versions = profile_versions or _FakeTable(select_rows=[_PROFILE_VERSION_ROW])
        self.resume_documents = resume_documents or _FakeTable(
            select_rows=[], insert_row=_DOCUMENT_ROW
        )
        self.capability_preferences = capability_preferences or _FakeTable(
            select_rows=[_PREFERENCE_ROW]
        )
        self.provider_credentials = provider_credentials or _FakeTable(
            select_rows=[_CREDENTIAL_ROW]
        )
        self.career_facts = career_facts or _FakeTable(select_rows=_CAREER_FACTS_ROWS)
        self.company_intel_runs = company_intel_runs or _FakeTable(select_rows=[])
        self.company_intel_claims = company_intel_claims or _FakeTable(select_rows=[])
        self.application_events = application_events or _FakeTable(select_rows=[])
        self.positioning_briefs = positioning_briefs or _FakeTable(select_rows=[])

    def table(self, name: str) -> Any:
        return {
            "applications": self.applications,
            "job_snapshots": self.job_snapshots,
            "profile_versions": self.profile_versions,
            "resume_documents": self.resume_documents,
            "capability_preferences": self.capability_preferences,
            "provider_credentials": self.provider_credentials,
            "career_facts": self.career_facts,
            "company_intel_runs": self.company_intel_runs,
            "company_intel_claims": self.company_intel_claims,
            "application_events": self.application_events,
            "positioning_briefs": self.positioning_briefs,
        }[name]

    def rpc(self, fn: str, _params: dict[str, Any]) -> _FakeRpcBuilder:
        if fn == "decrypt_secret":
            return _FakeRpcBuilder("sk-or-v1-real-secret")
        raise AssertionError(f"unexpected rpc: {fn}")


class _FakeHttpClient:
    def __init__(self, *, step0_body: dict[str, Any] | None = None) -> None:
        self.post_calls: list[tuple[str, dict[str, Any]]] = []
        self._step0_body = step0_body or _STEP0_RESPONSE_BODY

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.post_calls.append((url, kwargs))
        if url.endswith("/step0"):
            return httpx.Response(
                status_code=200, json=self._step0_body, request=httpx.Request("POST", url)
            )
        raise AssertionError(f"unexpected forge-engines call: {url}")


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")


def _patch_llm(monkeypatch: pytest.MonkeyPatch, content: str = _BRIEF_LLM_RESPONSE) -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=content)

    monkeypatch.setattr("between_jobs.api.positioning_brief_routes.llm_generate", fake_generate)


def _client(supabase: _FakeSupabaseClient, http: _FakeHttpClient) -> TestClient:
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    app.dependency_overrides[get_http_client] = lambda: http
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Any:
    yield
    app.dependency_overrides.clear()


def test_get_brief_returns_none_when_nothing_generated_yet() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}/positioning-brief")

    assert response.status_code == 200
    assert response.json() == {"brief": None}


def test_get_brief_never_returns_another_users_brief() -> None:
    """Regression test for a real IDOR an adversarial review caught: this
    backend queries with the service-role key, so RLS never gates this
    read -- GET must filter by user_id itself, not just application_id."""
    other_user_row = {
        "id": "brief-1",
        "user_id": "99999999-0000-0000-0000-000000000099",
        "application_id": _APPLICATION_ID,
        "lead_with_citation": "Kafka",
    }
    supabase = _FakeSupabaseClient(
        positioning_briefs=_RealFilterTable([other_user_row])  # type: ignore[arg-type]
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}/positioning-brief")

    assert response.status_code == 200
    assert response.json() == {"brief": None}


def test_generate_brief_success_stores_and_returns_the_brief(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()
    _patch_llm(monkeypatch)

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/positioning-brief")

    assert response.status_code == 201
    body = response.json()
    assert body["brief"]["lead_with_citation"] == "Python"
    assert body["brief"]["gap_citation"] == "Kafka"
    inserted = supabase.positioning_briefs.insert_calls[0]
    assert inserted["lead_with_citation"] == "Python"
    assert inserted["application_id"] == _APPLICATION_ID


def test_generate_brief_auto_creates_the_resume_document_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supabase = _FakeSupabaseClient(resume_documents=_FakeTable(select_rows=[]))
    http = _FakeHttpClient()
    _patch_llm(monkeypatch)

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/positioning-brief")

    assert response.status_code == 201
    inserted = supabase.resume_documents.insert_calls[0]
    assert inserted["application_id"] == _APPLICATION_ID
    assert inserted["job_snapshot_id"] == _SNAPSHOT_ID


def test_generate_brief_not_found_returns_404() -> None:
    supabase = _FakeSupabaseClient(applications=_FakeTable(select_rows=[]))
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/positioning-brief")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_generate_brief_no_active_profile_returns_setup_required() -> None:
    supabase = _FakeSupabaseClient(profile_versions=_FakeTable(select_rows=[]))
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/positioning-brief")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SETUP_REQUIRED"
    assert http.post_calls == []


def test_generate_brief_insufficient_evidence_when_no_real_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A JD whose only must-have cluster IS covered, and whose only key
    term is already verified, leaves zero real gap candidates --
    generate_positioning_brief refuses (no LLM call spent) rather than
    let the model invent one."""
    step0_body = {
        "clusters": [{"name": "Python Backend", "priority": "must_have", "keywords": ["python"]}],
        "dealbreakers": [],
        "keyTerms": ["Python"],
    }
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(step0_body=step0_body)

    async def fail_if_called(**_kwargs: Any) -> LLMResponse:
        raise AssertionError("the brief LLM must not be called with no real gap to cite")

    monkeypatch.setattr("between_jobs.api.positioning_brief_routes.llm_generate", fail_if_called)

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/positioning-brief")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INSUFFICIENT_EVIDENCE"


def test_generate_brief_never_persists_a_fabricated_citation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end proof the deterministic re-grounding survives the full
    HTTP round trip, not just the pipeline's own unit tests."""
    fabricated = json.dumps(
        {
            "lead_with": "You know a technology not on your real list.",
            "lead_with_citation": "Rust",
            "gap_that_matters": "Kafka streaming experience is missing.",
            "gap_citation": "Kafka",
            "recommended_project": "Build a small Kafka consumer service.",
        }
    )
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()
    _patch_llm(monkeypatch, content=fabricated)

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/positioning-brief")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INSUFFICIENT_EVIDENCE"
    assert supabase.positioning_briefs.insert_calls == []


def test_generate_brief_includes_company_intel_claims_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supabase = _FakeSupabaseClient(
        company_intel_runs=_FakeTable(select_rows=[{"id": "ci-run-1"}]),
        company_intel_claims=_FakeTable(
            select_rows=[{"claim_text": "Acme builds its platform on WidgetCore."}]
        ),
    )
    http = _FakeHttpClient()

    captured: dict[str, Any] = {}

    async def capturing_generate(**kwargs: Any) -> LLMResponse:
        captured.update(kwargs)
        return LLMResponse(content=_BRIEF_LLM_RESPONSE)

    monkeypatch.setattr(
        "between_jobs.api.positioning_brief_routes.llm_generate", capturing_generate
    )

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/positioning-brief")

    assert response.status_code == 201
    assert "WidgetCore" in captured["user_prompt"]
