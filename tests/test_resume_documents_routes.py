"""Tests for the resume-documents HTTP endpoints (Sprint 3.2c).

Exercises the real FastAPI routes via TestClient, same shape as
test_prepare_application_route.py -- the outbound calls to forge-engines
(/ingest, /personal, /header/resolve) are faked at the httpx client
boundary, never a real network call.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from between_jobs.api.app import app
from between_jobs.api.app_state import get_http_client, get_supabase
from between_jobs.api.auth import require_user_id

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
    "job_id": "job-1",
    "title": "Staff Engineer",
    "company_name": "Acme",
    "location_text": "Remote",
    "description_text": "Build things.",
    "source_url": "https://example.com/jobs/1",
    "source_kind": "manual_paste",
}
_PROFILE_VERSION_ROW = {
    "id": _PROFILE_VERSION_ID,
    "user_id": _USER_ID,
    "activated_at": "2026-08-01T00:00:00Z",
    "canonical_json": {
        "personal": {"name": "Jordan Rivera"},
        "skills": {"programming": ["Python"], "ai_ml": ["PyTorch"]},
    },
}
_DOCUMENT_ROW = {
    "id": _DOCUMENT_ID,
    "user_id": _USER_ID,
    "application_id": _APPLICATION_ID,
    "profile_version_id": _PROFILE_VERSION_ID,
    "job_snapshot_id": _SNAPSHOT_ID,
    "section_order": [],
    "section_visibility": {},
    "header_layout": {"chips": [{"field": "email"}], "separator": "pipe"},
}
_MASTER_DOCUMENT_ROW: dict[str, Any] = {
    "id": _DOCUMENT_ID,
    "user_id": _USER_ID,
    "application_id": None,
    "profile_version_id": _PROFILE_VERSION_ID,
    "job_snapshot_id": None,
    "section_order": [],
    "section_visibility": {},
    "header_layout": {},
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
    {
        "id": "fact-1",
        "value_json": {"title": "Staff Engineer", "bullets": ["Built things with Python."]},
    }
]
_STEP0_RESPONSE_BODY = {
    "clusters": [{"name": "Python", "priority": "must_have", "keywords": ["python"]}],
    "dealbreakers": [],
    "keyTerms": ["Python", "TensorFlow", "AWS"],
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
        self.update_calls: list[dict[str, Any]] = []

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def insert(self, data: dict[str, Any]) -> _ChainBuilder:
        self.insert_calls.append(data)
        rows = [self.insert_row] if self.insert_row is not None else [{**data, "id": _DOCUMENT_ID}]
        return _ChainBuilder(rows)

    def update(self, data: dict[str, Any]) -> _ChainBuilder:
        self.update_calls.append(data)
        rows = [{**self.select_rows[0], **data}] if self.select_rows else []
        return _ChainBuilder(rows)


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
    ) -> None:
        self.applications = applications or _FakeTable(select_rows=[_APPLICATION_ROW])
        self.job_snapshots = job_snapshots or _FakeTable(select_rows=[_SNAPSHOT_ROW])
        self.profile_versions = profile_versions or _FakeTable(select_rows=[_PROFILE_VERSION_ROW])
        self.resume_documents = resume_documents or _FakeTable(select_rows=[_DOCUMENT_ROW])
        self.capability_preferences = capability_preferences or _FakeTable(
            select_rows=[_PREFERENCE_ROW]
        )
        self.provider_credentials = provider_credentials or _FakeTable(
            select_rows=[_CREDENTIAL_ROW]
        )
        self.career_facts = career_facts or _FakeTable(select_rows=_CAREER_FACTS_ROWS)

    def table(self, name: str) -> Any:
        return {
            "applications": self.applications,
            "job_snapshots": self.job_snapshots,
            "profile_versions": self.profile_versions,
            "resume_documents": self.resume_documents,
            "capability_preferences": self.capability_preferences,
            "provider_credentials": self.provider_credentials,
            "career_facts": self.career_facts,
        }[name]

    def rpc(self, fn: str, params: dict[str, Any]) -> _FakeRpcBuilder:
        if fn == "decrypt_secret":
            return _FakeRpcBuilder("sk-or-v1-real-secret")
        raise AssertionError(f"unexpected rpc: {fn}")


class _FakeHttpClient:
    def __init__(
        self,
        *,
        step0_body: dict[str, Any] | None = None,
        gap_interview_body: dict[str, Any] | None = None,
    ) -> None:
        self.post_calls: list[tuple[str, dict[str, Any]]] = []
        self._step0_body = step0_body or _STEP0_RESPONSE_BODY
        self._gap_interview_body = gap_interview_body or {"questions": []}

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.post_calls.append((url, kwargs))
        body: dict[str, Any]
        if url.endswith("/ingest"):
            body = {"resume_doc": {"personal": {"name": "Jordan Rivera"}}, "warnings": []}
        elif url.endswith("/personal"):
            body = {
                "resume_text": "...",
                "personal": {"name": "Jordan Rivera", "email": "jordan@example.com"},
                "resume_source": "resume_doc",
            }
        elif url.endswith("/header/resolve"):
            body = {"chips": [{"field": "email", "text": "jordan@example.com", "href": None}]}
        elif url.endswith("/step0"):
            body = self._step0_body
        elif url.endswith("/gap-interview"):
            body = self._gap_interview_body
        else:
            raise AssertionError(f"unexpected forge-engines call: {url}")
        return httpx.Response(status_code=200, json=body, request=httpx.Request("POST", url))


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")


def _client(supabase: _FakeSupabaseClient, http: _FakeHttpClient | None = None) -> TestClient:
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    if http is not None:
        app.dependency_overrides[get_http_client] = lambda: http
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Any:
    yield
    app.dependency_overrides.clear()


def test_get_my_document_master_get_or_create() -> None:
    supabase = _FakeSupabaseClient(resume_documents=_FakeTable(select_rows=[]))
    with _client(supabase) as client:
        response = client.get("/resume-documents/mine")

    assert response.status_code == 200
    assert supabase.resume_documents.insert_calls[0]["application_id"] is None
    assert supabase.resume_documents.insert_calls[0]["job_snapshot_id"] is None


def test_get_my_document_for_application_resolves_job_snapshot() -> None:
    supabase = _FakeSupabaseClient(resume_documents=_FakeTable(select_rows=[]))
    with _client(supabase) as client:
        response = client.get(f"/resume-documents/mine?application_id={_APPLICATION_ID}")

    assert response.status_code == 200
    inserted = supabase.resume_documents.insert_calls[0]
    assert inserted["application_id"] == _APPLICATION_ID
    assert inserted["job_snapshot_id"] == _SNAPSHOT_ID


def test_get_my_document_no_active_profile_returns_setup_required() -> None:
    supabase = _FakeSupabaseClient(profile_versions=_FakeTable(select_rows=[]))
    with _client(supabase) as client:
        response = client.get("/resume-documents/mine")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SETUP_REQUIRED"


def test_get_my_document_application_not_found_returns_404() -> None:
    supabase = _FakeSupabaseClient(applications=_FakeTable(select_rows=[]))
    with _client(supabase) as client:
        response = client.get(f"/resume-documents/mine?application_id={_APPLICATION_ID}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_update_header_success() -> None:
    supabase = _FakeSupabaseClient()
    layout = {"chips": [{"field": "phone"}]}
    with _client(supabase) as client:
        response = client.patch(
            f"/resume-documents/{_DOCUMENT_ID}/header", json={"header_layout": layout}
        )

    assert response.status_code == 200
    assert supabase.resume_documents.update_calls == [{"header_layout": layout}]


def test_update_header_not_found_returns_404() -> None:
    supabase = _FakeSupabaseClient(resume_documents=_FakeTable(select_rows=[]))
    with _client(supabase) as client:
        response = client.patch(
            f"/resume-documents/{_DOCUMENT_ID}/header", json={"header_layout": {}}
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_update_evidence_success() -> None:
    supabase = _FakeSupabaseClient()
    fact_ids = ["fact-1", "fact-2"]
    with _client(supabase) as client:
        response = client.patch(
            f"/resume-documents/{_DOCUMENT_ID}/evidence", json={"evidence_fact_ids": fact_ids}
        )

    assert response.status_code == 200
    assert supabase.resume_documents.update_calls == [{"selected_evidence_fact_ids": fact_ids}]


def test_update_evidence_not_found_returns_404() -> None:
    supabase = _FakeSupabaseClient(resume_documents=_FakeTable(select_rows=[]))
    with _client(supabase) as client:
        response = client.patch(
            f"/resume-documents/{_DOCUMENT_ID}/evidence", json={"evidence_fact_ids": []}
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_update_assertions_success() -> None:
    supabase = _FakeSupabaseClient()
    assertions = ["On-site role"]
    with _client(supabase) as client:
        response = client.patch(
            f"/resume-documents/{_DOCUMENT_ID}/assertions", json={"assertions": assertions}
        )

    assert response.status_code == 200
    assert supabase.resume_documents.update_calls == [{"assertions": assertions}]


def test_update_assertions_not_found_returns_404() -> None:
    supabase = _FakeSupabaseClient(resume_documents=_FakeTable(select_rows=[]))
    with _client(supabase) as client:
        response = client.patch(
            f"/resume-documents/{_DOCUMENT_ID}/assertions", json={"assertions": []}
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_update_sections_success() -> None:
    supabase = _FakeSupabaseClient()
    order = ["experience", "education", "summary"]
    visibility = {"summary": False}
    with _client(supabase) as client:
        response = client.patch(
            f"/resume-documents/{_DOCUMENT_ID}/sections",
            json={"section_order": order, "section_visibility": visibility},
        )

    assert response.status_code == 200
    assert supabase.resume_documents.update_calls == [
        {"section_order": order, "section_visibility": visibility}
    ]


def test_update_sections_not_found_returns_404() -> None:
    supabase = _FakeSupabaseClient(resume_documents=_FakeTable(select_rows=[]))
    with _client(supabase) as client:
        response = client.patch(
            f"/resume-documents/{_DOCUMENT_ID}/sections",
            json={"section_order": [], "section_visibility": {}},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_preview_header_with_draft_layout_calls_the_full_chain() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()
    draft_layout = {"chips": [{"field": "email", "display_mode": "label"}]}

    with _client(supabase, http) as client:
        response = client.post(
            f"/resume-documents/{_DOCUMENT_ID}/header/preview",
            json={"header_layout": draft_layout},
        )

    assert response.status_code == 200
    assert response.json()["chips"] == [
        {"field": "email", "text": "jordan@example.com", "href": None}
    ]
    urls = [url for url, _ in http.post_calls]
    assert any(u.endswith("/ingest") for u in urls)
    assert any(u.endswith("/personal") for u in urls)
    resolve_call = next(
        kwargs for url, kwargs in http.post_calls if url.endswith("/header/resolve")
    )
    assert resolve_call["json"]["header_layout"] == draft_layout


def test_preview_header_without_draft_uses_saved_layout() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(
            f"/resume-documents/{_DOCUMENT_ID}/header/preview", json={"header_layout": None}
        )

    assert response.status_code == 200
    resolve_call = next(
        kwargs for url, kwargs in http.post_calls if url.endswith("/header/resolve")
    )
    assert resolve_call["json"]["header_layout"] == _DOCUMENT_ROW["header_layout"]


def test_preview_header_document_not_found_returns_404() -> None:
    supabase = _FakeSupabaseClient(resume_documents=_FakeTable(select_rows=[]))
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(
            f"/resume-documents/{_DOCUMENT_ID}/header/preview", json={"header_layout": None}
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_get_coverage_success() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/resume-documents/{_DOCUMENT_ID}/coverage")

    assert response.status_code == 200
    body = response.json()
    assert body["step0"]["clusters"] == _STEP0_RESPONSE_BODY["clusters"]
    assert body["coverage"] == [
        {
            "name": "Python",
            "priority": "must_have",
            "keywords": ["python"],
            "coverage_count": 1,
            "matched_fact_ids": ["fact-1"],
        }
    ]
    assert body["skills"] == [
        {"skill": "Python", "requested_as": "Python", "state": "verified"},
        {"skill": "TensorFlow", "requested_as": "TensorFlow", "state": "adjacent"},
        {"skill": "AWS", "requested_as": "AWS", "state": "unsupported"},
    ]
    step0_call = next(kwargs for url, kwargs in http.post_calls if url.endswith("/step0"))
    assert step0_call["json"]["job_description"] == _SNAPSHOT_ROW["description_text"]
    assert step0_call["json"]["credential"]["secret"] == "sk-or-v1-real-secret"


def test_get_coverage_master_document_returns_invalid_input() -> None:
    supabase = _FakeSupabaseClient(resume_documents=_FakeTable(select_rows=[_MASTER_DOCUMENT_ROW]))
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/resume-documents/{_DOCUMENT_ID}/coverage")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"
    assert http.post_calls == []


def test_get_coverage_document_not_found_returns_404() -> None:
    supabase = _FakeSupabaseClient(resume_documents=_FakeTable(select_rows=[]))
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/resume-documents/{_DOCUMENT_ID}/coverage")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


_CV_STEP0_RESPONSE_BODY = {
    "clusters": [
        {"name": "Computer Vision", "priority": "must_have", "keywords": ["computer vision"]}
    ],
    "dealbreakers": [],
    "keyTerms": ["Computer Vision"],
}
_CV_PROFILE_VERSION_ROW = {
    **_PROFILE_VERSION_ROW,
    "canonical_json": {
        "personal": {"name": "Jordan Rivera"},
        "skills": {"programming": ["Python"], "ai_ml": []},
        "experience": [{"bullets": ["Preprocessed images with OpenCV before training."]}],
    },
}
_CV_CAREER_FACTS_ROWS = [
    {
        "id": "fact-1",
        "value_json": {"bullets": ["Preprocessed images with OpenCV before training."]},
    }
]


def test_get_gap_interview_skips_the_llm_call_when_there_are_no_candidates() -> None:
    # _STEP0_RESPONSE_BODY's only cluster is "Python", which _CAREER_FACTS_ROWS
    # already covers (coverage_count > 0) -- no eligible gap, so gap_interview
    # should never even fire the second real LLM call.
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/resume-documents/{_DOCUMENT_ID}/gap-interview")

    assert response.status_code == 200
    assert response.json() == {"questions": []}
    assert all(not url.endswith("/gap-interview") for url, _ in http.post_calls)


def test_get_gap_interview_success_with_a_real_bridge() -> None:
    supabase = _FakeSupabaseClient(
        profile_versions=_FakeTable(select_rows=[_CV_PROFILE_VERSION_ROW]),
        career_facts=_FakeTable(select_rows=_CV_CAREER_FACTS_ROWS),
    )
    question_text = "Have you used OpenCV for anything beyond basic preprocessing?"
    http = _FakeHttpClient(
        step0_body=_CV_STEP0_RESPONSE_BODY,
        gap_interview_body={
            "questions": [{"cluster_name": "Computer Vision", "question": question_text}]
        },
    )

    with _client(supabase, http) as client:
        response = client.post(f"/resume-documents/{_DOCUMENT_ID}/gap-interview")

    assert response.status_code == 200
    assert response.json() == {
        "questions": [{"cluster_name": "Computer Vision", "question": question_text}]
    }
    gap_interview_call = next(
        kwargs for url, kwargs in http.post_calls if url.endswith("/gap-interview")
    )
    assert gap_interview_call["json"]["items"] == [
        {"cluster_name": "Computer Vision", "bridge_skill": "opencv"}
    ]


def test_get_gap_interview_master_document_returns_invalid_input() -> None:
    supabase = _FakeSupabaseClient(resume_documents=_FakeTable(select_rows=[_MASTER_DOCUMENT_ROW]))
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/resume-documents/{_DOCUMENT_ID}/gap-interview")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"
    assert http.post_calls == []


def test_get_gap_interview_document_not_found_returns_404() -> None:
    supabase = _FakeSupabaseClient(resume_documents=_FakeTable(select_rows=[]))
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/resume-documents/{_DOCUMENT_ID}/gap-interview")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
