"""Tests for the interview-practice HTTP endpoints (InterviewForge R3,
interviewforge-v1.md).

Exercises the real FastAPI routes via TestClient, same shape as
test_resume_documents_routes.py -- outbound calls to forge-engines
(/ingest, /personal) are faked at the httpx client boundary; the LLM calls
(question generation, answer scoring) are faked by monkeypatching
`between_jobs.api.interview_practice_routes.llm_generate` -- the route
imports its OWN `generate` reference and passes it explicitly to
`generate_practice_questions`/`score_answer` (same pattern
test_company_intel_routes.py's own `_patch_llm` already uses), so patching
`interview_practice.py`'s internal default would have no effect here.
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
_SESSION_ID = "80000000-0000-0000-0000-000000000001"
_QUESTION_ID = "90000000-0000-0000-0000-000000000001"
_REGISTRY_ID = "a0000000-0000-0000-0000-000000000001"

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
    "description_text": "Build things.",
}
_PROFILE_VERSION_ROW = {
    "id": _PROFILE_VERSION_ID,
    "user_id": _USER_ID,
    "activated_at": "2026-08-01T00:00:00Z",
    "canonical_json": {"personal": {"name": "Jordan Rivera"}},
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
_REGISTRY_ROW = {
    "id": _REGISTRY_ID,
    "company_name": "Acme",
    "rounds": [{"name": "Technical interview"}],
    "typical_topics": ["system design"],
    "difficulty_signal": "medium",
    "values_signals": ["ownership"],
    "confidence": "high",
}

_QUESTIONS_LLM_RESPONSE = json.dumps(
    {
        "questions": [
            {
                "question": "Tell me about a challenging project.",
                "type": "behavioral",
                "target_skill": "ownership",
                "grounded_in": None,
            }
        ]
    }
)

_SCORE_LLM_RESPONSE = json.dumps(
    {
        "score": 7,
        "structure_feedback": "Well organized.",
        "specificity_feedback": "Could be more specific.",
        "star_coverage": {"situation": True, "task": True, "action": True, "result": False},
        "improved_answer": "A tighter version.",
    }
)


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def is_(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def order(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def limit(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    @property
    def not_(self) -> _ChainBuilder:
        # Real postgrest-py exposes `.not_` as a property returning a
        # filter-builder proxy (`.eq(...).not_.is_(...)`), not a method --
        # `profile_store.get_active_version`'s real query chain uses this.
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
        self.update_calls: list[dict[str, Any]] = []
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

    def update(self, data: dict[str, Any]) -> _ChainBuilder:
        self.update_calls.append(data)
        rows = [{**self.select_rows[0], **data}] if self.select_rows else []
        return _ChainBuilder(rows)


class _QuestionsSelectChain:
    """Unlike `_ChainBuilder`, this actually applies its filters against a
    MUTABLE row list -- the route calls `get_next_unanswered_question`
    TWICE per answer (before and after recording one), and a real answer
    genuinely has to stop showing up as "unanswered" after `.update()`
    writes it -- a fake that ignores its own filters can't tell "session
    still has questions left" apart from "session just completed"."""

    def __init__(self, table: _QuestionsTable) -> None:
        self._table = table
        self._session_id: str | None = None
        self._id: str | None = None
        self._answered_at_is_null = False

    def eq(self, column: str, value: Any) -> _QuestionsSelectChain:
        if column == "session_id":
            self._session_id = value
        if column == "id":
            self._id = value
        return self

    def is_(self, column: str, value: Any) -> _QuestionsSelectChain:
        if column == "answered_at" and value == "null":
            self._answered_at_is_null = True
        return self

    def order(self, *_: Any, **__: Any) -> _QuestionsSelectChain:
        return self

    def limit(self, *_: Any, **__: Any) -> _QuestionsSelectChain:
        return self

    async def execute(self) -> SimpleNamespace:
        rows = self._table.rows
        if self._session_id is not None:
            rows = [r for r in rows if r["session_id"] == self._session_id]
        if self._id is not None:
            rows = [r for r in rows if r["id"] == self._id]
        if self._answered_at_is_null:
            rows = [r for r in rows if r.get("answered_at") is None]
        return SimpleNamespace(data=sorted(rows, key=lambda r: r.get("ordinal", 0)))


class _QuestionsUpdateChain:
    def __init__(self, table: _QuestionsTable, data: dict[str, Any]) -> None:
        self._table = table
        self._data = data
        self._id: str | None = None

    def eq(self, column: str, value: Any) -> _QuestionsUpdateChain:
        if column == "id":
            self._id = value
        return self

    async def execute(self) -> SimpleNamespace:
        for row in self._table.rows:
            if row["id"] == self._id:
                row.update(self._data)
                return SimpleNamespace(data=[row])
        return SimpleNamespace(data=[])


class _QuestionsTable:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.insert_calls: list[Any] = []
        self.update_calls: list[dict[str, Any]] = []
        self._next_id = 1

    def select(self, *_: Any, **__: Any) -> _QuestionsSelectChain:
        return _QuestionsSelectChain(self)

    def insert(self, data: Any) -> _ChainBuilder:
        self.insert_calls.append(data)
        rows = []
        for row in data if isinstance(data, list) else [data]:
            rows.append({"id": f"question-{self._next_id}", "answered_at": None, **row})
            self._next_id += 1
        self.rows.extend(rows)
        return _ChainBuilder(rows)

    def update(self, data: dict[str, Any]) -> _QuestionsUpdateChain:
        self.update_calls.append(data)
        return _QuestionsUpdateChain(self, data)


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
        profile_versions: _FakeTable | None = None,
        interview_process_registry: _FakeTable | None = None,
        interview_sessions: _FakeTable | None = None,
        interview_session_questions: _QuestionsTable | None = None,
    ) -> None:
        self.applications = applications or _FakeTable(select_rows=[_APPLICATION_ROW])
        self.job_snapshots = _FakeTable(select_rows=[_SNAPSHOT_ROW])
        self.profile_versions = profile_versions or _FakeTable(select_rows=[_PROFILE_VERSION_ROW])
        self.capability_preferences = _FakeTable(select_rows=[_PREFERENCE_ROW])
        self.provider_credentials = _FakeTable(select_rows=[_CREDENTIAL_ROW])
        self.interview_process_registry = interview_process_registry or _FakeTable(select_rows=[])
        # No `insert_row` override -- unlike some other fakes in this
        # codebase, this route's own tests assert on fields the real
        # insert call passes (company_name, resume_evidence, ...), so the
        # fake needs to echo back what was actually inserted, not a fixed
        # minimal literal that would silently drop them.
        self.interview_sessions = interview_sessions or _FakeTable(select_rows=[])
        self.interview_session_questions = interview_session_questions or _QuestionsTable([])

    def table(self, name: str) -> Any:
        return {
            "applications": self.applications,
            "job_snapshots": self.job_snapshots,
            "profile_versions": self.profile_versions,
            "capability_preferences": self.capability_preferences,
            "provider_credentials": self.provider_credentials,
            "interview_process_registry": self.interview_process_registry,
            "interview_sessions": self.interview_sessions,
            "interview_session_questions": self.interview_session_questions,
        }[name]

    def rpc(self, fn: str, _params: dict[str, Any]) -> _FakeRpcBuilder:
        if fn == "decrypt_secret":
            return _FakeRpcBuilder("sk-or-v1-real-secret")
        raise AssertionError(f"unexpected rpc: {fn}")


class _FakeHttpClient:
    def __init__(self) -> None:
        self.post_calls: list[tuple[str, dict[str, Any]]] = []

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.post_calls.append((url, kwargs))
        body: dict[str, Any]
        if url.endswith("/ingest"):
            body = {"resume_doc": {"personal": {"name": "Jordan Rivera"}}, "warnings": []}
        elif url.endswith("/personal"):
            body = {
                "resume_text": "Led a team of 5. Built a Python service.",
                "personal": {"name": "Jordan Rivera"},
                "resume_source": "resume_doc",
            }
        else:
            raise AssertionError(f"unexpected forge-engines call: {url}")
        return httpx.Response(status_code=200, json=body, request=httpx.Request("POST", url))


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


def _patch_llm(monkeypatch: pytest.MonkeyPatch, content: str) -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=content)

    monkeypatch.setattr("between_jobs.api.interview_practice_routes.llm_generate", fake_generate)


def _client(supabase: _FakeSupabaseClient, http: _FakeHttpClient) -> TestClient:
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    app.dependency_overrides[get_http_client] = lambda: http
    return TestClient(app)


# ── POST /sessions ────────────────────────────────────────────────────────


def test_start_session_creates_a_session_with_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()
    _patch_llm(monkeypatch, _QUESTIONS_LLM_RESPONSE)

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/interview-practice/sessions")

    assert response.status_code == 201
    body = response.json()
    assert body["session"]["company_name"] == "Acme"
    assert len(body["questions"]) == 1
    assert body["questions"][0]["grounded_in"] is None
    assert supabase.interview_sessions.insert_calls[0]["registry_entry_id"] is None


def test_start_session_uses_the_real_registry_entry_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supabase = _FakeSupabaseClient(
        interview_process_registry=_FakeTable(select_rows=[_REGISTRY_ROW])
    )
    http = _FakeHttpClient()
    seen_prompts: list[str] = []

    async def fake_generate(**kwargs: Any) -> LLMResponse:
        seen_prompts.append(kwargs["user_prompt"])
        return LLMResponse(content=_QUESTIONS_LLM_RESPONSE)

    monkeypatch.setattr("between_jobs.api.interview_practice_routes.llm_generate", fake_generate)

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/interview-practice/sessions")

    assert response.status_code == 201
    payload = json.loads(seen_prompts[0])
    assert payload["registry_entry"]["rounds"][0]["name"] == "Technical interview"
    assert supabase.interview_sessions.insert_calls[0]["registry_entry_id"] == _REGISTRY_ID


def test_start_session_returns_run_failed_when_no_questions_generated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()
    _patch_llm(monkeypatch, "not json at all")

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/interview-practice/sessions")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "RUN_FAILED"
    assert supabase.interview_sessions.insert_calls == []


def test_start_session_requires_an_active_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    supabase = _FakeSupabaseClient(profile_versions=_FakeTable(select_rows=[]))
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/interview-practice/sessions")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SETUP_REQUIRED"


def test_start_session_not_found_for_unknown_application() -> None:
    supabase = _FakeSupabaseClient(applications=_FakeTable(select_rows=[]))
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/interview-practice/sessions")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# ── POST /sessions/{id}/answers ──────────────────────────────────────────


def _session_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": _SESSION_ID,
        "user_id": _USER_ID,
        "application_id": _APPLICATION_ID,
        "company_name": "Acme",
        "resume_evidence": "Led a team of 5.",
        "status": "in_progress",
    }
    return {**row, **overrides}


def _question_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": _QUESTION_ID,
        "session_id": _SESSION_ID,
        "ordinal": 0,
        "question_text": "Tell me about a challenging project.",
        "question_type": "behavioral",
        "target_skill": "ownership",
        "grounded_in": None,
        "answer_text": None,
        "score": None,
        "feedback": None,
        "answered_at": None,
    }
    return {**row, **overrides}


def test_submit_answer_scores_and_records_the_next_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supabase = _FakeSupabaseClient(
        interview_sessions=_FakeTable(select_rows=[_session_row()]),
        interview_session_questions=_QuestionsTable([_question_row()]),
    )
    http = _FakeHttpClient()
    _patch_llm(monkeypatch, _SCORE_LLM_RESPONSE)

    with _client(supabase, http) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/interview-practice/sessions/{_SESSION_ID}/answers",
            json={"answer_text": "My answer."},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["feedback"]["score"] == 7
    assert body["session_report"] is not None
    assert body["session_report"]["average_score"] == 7.0
    update_calls = supabase.interview_session_questions.update_calls
    assert update_calls[0]["answer_text"] == "My answer."
    assert update_calls[0]["score"] == 7


def test_submit_answer_leaves_session_in_progress_when_more_questions_remain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    q1 = _question_row(id=_QUESTION_ID, ordinal=0)
    q2 = _question_row(id="90000000-0000-0000-0000-000000000002", ordinal=1)
    supabase = _FakeSupabaseClient(
        interview_sessions=_FakeTable(select_rows=[_session_row()]),
        interview_session_questions=_QuestionsTable([q1, q2]),
    )
    http = _FakeHttpClient()
    _patch_llm(monkeypatch, _SCORE_LLM_RESPONSE)

    with _client(supabase, http) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/interview-practice/sessions/{_SESSION_ID}/answers",
            json={"answer_text": "My answer."},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["session_report"] is None
    assert body["next_question"] is not None
    assert supabase.interview_sessions.update_calls == []


def test_submit_answer_run_failed_when_no_unanswered_questions() -> None:
    # A real, already-answered question -- confirms the `answered_at is
    # null` filter genuinely excludes it, not just that an empty seed list
    # trivially returns nothing.
    answered = _question_row(answered_at="2026-08-29T00:00:00Z")
    supabase = _FakeSupabaseClient(
        interview_sessions=_FakeTable(select_rows=[_session_row()]),
        interview_session_questions=_QuestionsTable([answered]),
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/interview-practice/sessions/{_SESSION_ID}/answers",
            json={"answer_text": "My answer."},
        )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "RUN_FAILED"


def test_submit_answer_run_failed_when_scoring_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    supabase = _FakeSupabaseClient(
        interview_sessions=_FakeTable(select_rows=[_session_row()]),
        interview_session_questions=_QuestionsTable([_question_row()]),
    )
    http = _FakeHttpClient()
    _patch_llm(monkeypatch, "not json at all")

    with _client(supabase, http) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/interview-practice/sessions/{_SESSION_ID}/answers",
            json={"answer_text": "My answer."},
        )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "RUN_FAILED"
    assert supabase.interview_session_questions.update_calls == []


def test_submit_answer_not_found_for_a_session_on_a_different_application() -> None:
    supabase = _FakeSupabaseClient(
        interview_sessions=_FakeTable(
            select_rows=[_session_row(application_id="different-app-id")]
        ),
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/interview-practice/sessions/{_SESSION_ID}/answers",
            json={"answer_text": "My answer."},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_submit_answer_not_found_for_unknown_session() -> None:
    supabase = _FakeSupabaseClient(interview_sessions=_FakeTable(select_rows=[]))
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/interview-practice/sessions/{_SESSION_ID}/answers",
            json={"answer_text": "My answer."},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# ── GET routes ────────────────────────────────────────────────────────────


def test_get_practice_session_returns_session_and_questions() -> None:
    supabase = _FakeSupabaseClient(
        interview_sessions=_FakeTable(select_rows=[_session_row()]),
        interview_session_questions=_QuestionsTable([_question_row()]),
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.get(
            f"/applications/{_APPLICATION_ID}/interview-practice/sessions/{_SESSION_ID}"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["session"]["id"] == _SESSION_ID
    assert len(body["questions"]) == 1


def test_list_practice_sessions_returns_the_rows() -> None:
    supabase = _FakeSupabaseClient(
        interview_sessions=_FakeTable(select_rows=[_session_row()]),
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}/interview-practice/sessions")

    assert response.status_code == 200
    assert len(response.json()["sessions"]) == 1
