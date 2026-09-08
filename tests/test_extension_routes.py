"""Tests for the browser-extension HTTP endpoints (browser-extension.md
E1, plus E3b's /draft-answer). Exercises the real FastAPI routes via
TestClient, same convention as test_saved_searches_routes.py.

/draft-answer's LLM calls are faked by monkeypatching
`between_jobs.api.extension_routes.llm_generate` -- the route imports its
OWN `generate` reference and passes it explicitly to
`generate_answer`/`verify_answer_claims` (the same R3-bug-informed seam
test_interview_practice_routes.py's own docstring already explains), so
patching application_answer_generator.py's internal default would have
no effect here."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from between_jobs.api.app import app
from between_jobs.api.app_state import get_supabase
from between_jobs.api.auth import require_user_id
from between_jobs.api.llm_client import LLMResponse

_USER_ID = "00000000-0000-0000-0000-000000000001"
_OTHER_USER_ID = "00000000-0000-0000-0000-000000000002"

_PREFERENCE_ROW = {
    "user_id": _USER_ID,
    "capability": "default",
    "execution_mode": "byok_first_party",
    "provider": "openrouter",
    "model": "anthropic/claude-sonnet-4-6",
}
_CREDENTIAL_ROW = {
    "user_id": _USER_ID,
    "service": "llm",
    "provider": "openrouter",
    "model": "anthropic/claude-sonnet-4-6",
    "base_url": None,
    "scope": None,
    "secret_encrypted": "ciphertext-abc",
    "secret_2_encrypted": None,
}
_PROFILE_ROW = {
    "user_id": _USER_ID,
    "activated_at": "2026-08-01T00:00:00Z",
    "canonical_json": {"personal": {"name": "Jane Doe"}},
}


def _apply_or(rows: list[dict[str, Any]], expr: str) -> list[dict[str, Any]]:
    clauses = expr.split(",")

    def matches(row: dict[str, Any]) -> bool:
        for clause in clauses:
            column, op, value = clause.split(".", 2)
            if op == "is" and value == "null" and row.get(column) is None:
                return True
            if op == "gt":
                current = row.get(column)
                if current is not None and current > value:
                    return True
        return False

    return [r for r in rows if matches(r)]


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, column: str, value: Any) -> _ChainBuilder:
        return _ChainBuilder([r for r in self._rows if r.get(column) == value])

    def in_(self, column: str, values: list[Any]) -> _ChainBuilder:
        return _ChainBuilder([r for r in self._rows if r.get(column) in values])

    def or_(self, expr: str) -> _ChainBuilder:
        return _ChainBuilder(_apply_or(self._rows, expr))

    @property
    def not_(self) -> _ChainBuilder:
        return self

    def is_(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def order(self, *_: Any, **__: Any) -> _ChainBuilder:
        rows = sorted(self._rows, key=lambda r: r.get("updated_at", ""), reverse=True)
        return _ChainBuilder(rows)

    def limit(self, _n: int) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTable:
    def __init__(self, *, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows if rows is not None else []
        self._next_id = 1

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.rows)

    def upsert(self, data: dict[str, Any], *, on_conflict: str) -> _ChainBuilder:
        conflict_cols = on_conflict.split(",")
        existing = next(
            (r for r in self.rows if all(r.get(c) == data.get(c) for c in conflict_cols)),
            None,
        )
        if existing is not None:
            existing.update(data)
            return _ChainBuilder([existing])
        row = {"id": f"row-{self._next_id}", "times_used": 0, **data}
        self._next_id += 1
        self.rows.append(row)
        return _ChainBuilder([row])


class _FakeSupabase:
    def __init__(
        self,
        *,
        applications: list[dict[str, Any]] | None = None,
        jobs: list[dict[str, Any]] | None = None,
        job_snapshots: list[dict[str, Any]] | None = None,
        approved_answers: list[dict[str, Any]] | None = None,
        profile_versions: list[dict[str, Any]] | None = None,
        capability_preferences: list[dict[str, Any]] | None = None,
        provider_credentials: list[dict[str, Any]] | None = None,
    ) -> None:
        self._tables = {
            "applications": _FakeTable(rows=applications),
            "jobs": _FakeTable(rows=jobs),
            "job_snapshots": _FakeTable(rows=job_snapshots),
            "approved_answers": _FakeTable(rows=approved_answers),
            "profile_versions": _FakeTable(rows=profile_versions),
            "capability_preferences": _FakeTable(rows=capability_preferences),
            "provider_credentials": _FakeTable(rows=provider_credentials),
        }

    def table(self, name: str) -> _FakeTable:
        return self._tables[name]

    async def _rpc_execute(self, fn: str, params: dict[str, Any]) -> SimpleNamespace:
        if fn == "decrypt_secret":
            return SimpleNamespace(data="sk-real-secret-not-real")
        raise AssertionError(f"unexpected rpc: {fn}")

    def rpc(self, fn: str, params: dict[str, Any]) -> Any:
        return SimpleNamespace(execute=lambda: self._rpc_execute(fn, params))


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


def _client(supabase: _FakeSupabase) -> TestClient:
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    return TestClient(app)


def test_lookup_finds_a_tracked_application_by_url() -> None:
    supabase = _FakeSupabase(
        applications=[
            {
                "id": "app-1",
                "user_id": _USER_ID,
                "job_id": "job-1",
                "active_job_snapshot_id": "snap-1",
            }
        ],
        jobs=[{"id": "job-1", "canonical_url": None}],
        job_snapshots=[{"id": "snap-1", "source_url": "https://jobs.lever.co/acme/1"}],
    )
    client = _client(supabase)

    response = client.get("/extension/lookup", params={"url": "https://jobs.lever.co/acme/1"})

    assert response.status_code == 200
    assert response.json() == {"application_id": "app-1"}


def test_lookup_returns_null_when_no_application_matches() -> None:
    supabase = _FakeSupabase(applications=[])
    client = _client(supabase)

    response = client.get("/extension/lookup", params={"url": "https://jobs.lever.co/acme/1"})

    assert response.status_code == 200
    assert response.json() == {"application_id": None}


def test_lookup_422s_on_an_empty_url() -> None:
    """Defense-in-depth companion to the store-level empty-url guard --
    rejected at the request boundary before it ever reaches matching
    logic."""
    supabase = _FakeSupabase()
    client = _client(supabase)

    response = client.get("/extension/lookup", params={"url": ""})

    assert response.status_code == 422


def test_lookup_never_matches_another_user_s_application() -> None:
    supabase = _FakeSupabase(
        applications=[
            {
                "id": "app-1",
                "user_id": _OTHER_USER_ID,
                "job_id": "job-1",
                "active_job_snapshot_id": "snap-1",
            }
        ],
        jobs=[{"id": "job-1", "canonical_url": None}],
        job_snapshots=[{"id": "snap-1", "source_url": "https://jobs.lever.co/acme/1"}],
    )
    client = _client(supabase)

    response = client.get("/extension/lookup", params={"url": "https://jobs.lever.co/acme/1"})

    assert response.json() == {"application_id": None}


def test_match_answer_returns_an_exact_match() -> None:
    supabase = _FakeSupabase(
        approved_answers=[
            {
                "id": "ans-1",
                "user_id": _USER_ID,
                "normalized_question": "are you willing to relocate",
                "canonical_intent": "willing_to_relocate",
                "answer_text": "Yes",
                "expires_at": None,
                "updated_at": "2026-09-01T00:00:00Z",
            }
        ]
    )
    client = _client(supabase)

    response = client.post(
        "/extension/match-answer", json={"normalized_question": "are you willing to relocate"}
    )

    assert response.status_code == 200
    assert response.json()["answer"]["answer_text"] == "Yes"


def test_match_answer_returns_null_when_nothing_matches() -> None:
    supabase = _FakeSupabase(approved_answers=[])
    client = _client(supabase)

    response = client.post(
        "/extension/match-answer", json={"normalized_question": "are you willing to relocate"}
    )

    assert response.status_code == 200
    assert response.json()["answer"] is None


def test_save_answer_creates_a_new_row() -> None:
    supabase = _FakeSupabase(approved_answers=[])
    client = _client(supabase)

    response = client.post(
        "/extension/approved-answers",
        json={
            "normalized_question": "are you willing to relocate",
            "answer_text": "Yes",
            "canonical_intent": "willing_to_relocate",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["answer_text"] == "Yes"
    assert body["user_id"] == _USER_ID


def test_save_answer_upserts_on_repeat_question() -> None:
    supabase = _FakeSupabase(
        approved_answers=[
            {
                "id": "ans-1",
                "user_id": _USER_ID,
                "normalized_question": "are you willing to relocate",
                "answer_text": "No",
            }
        ]
    )
    client = _client(supabase)

    response = client.post(
        "/extension/approved-answers",
        json={"normalized_question": "are you willing to relocate", "answer_text": "Yes"},
    )

    assert response.status_code == 201
    assert response.json()["answer_text"] == "Yes"
    assert len(supabase.table("approved_answers").rows) == 1


def _draft_answer_supabase(**overrides: Any) -> _FakeSupabase:
    defaults: dict[str, Any] = {
        "applications": [
            {
                "id": "app-1",
                "user_id": _USER_ID,
                "job_id": "job-1",
                "active_job_snapshot_id": "snap-1",
            }
        ],
        "job_snapshots": [{"id": "snap-1", "description_text": "We build ML systems in Python."}],
        "profile_versions": [_PROFILE_ROW],
        "capability_preferences": [_PREFERENCE_ROW],
        "provider_credentials": [_CREDENTIAL_ROW],
    }
    defaults.update(overrides)
    return _FakeSupabase(**defaults)


def _fake_generate_and_verify(
    *,
    generated_answer: str | None,
    declined_reason: str | None = None,
    claims: list[dict[str, Any]] | None = None,
) -> Any:
    async def fake_generate(*, system_prompt: str, **_kwargs: Any) -> LLMResponse:
        if "claim-verification judge" in system_prompt:
            return LLMResponse(content=json.dumps({"claims": claims or []}))
        return LLMResponse(
            content=json.dumps(
                {"answer_text": generated_answer, "declined_reason": declined_reason}
            )
        )

    return fake_generate


def test_draft_answer_skips_the_llm_entirely_for_a_disclaimer_length_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_if_called(**_kwargs: Any) -> LLMResponse:
        raise AssertionError("the LLM must never be called for an ineligible question")

    monkeypatch.setattr("between_jobs.api.extension_routes.llm_generate", fail_if_called)
    supabase = _draft_answer_supabase()
    client = _client(supabase)

    response = client.post(
        "/extension/draft-answer",
        json={"application_id": "app-1", "question_text": "x" * 500},
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {"eligible": False, "answer_text": None, "declined_reason": None, "warnings": []}


def test_draft_answer_generates_and_flags_only_non_grounded_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "between_jobs.api.extension_routes.llm_generate",
        _fake_generate_and_verify(
            generated_answer="I led a team of 5 and invented a new framework.",
            claims=[
                {"claim": "led a team of 5", "verdict": "grounded", "reason": "matches"},
                {
                    "claim": "invented a new framework",
                    "verdict": "contradicted",
                    "reason": "not in facts",
                },
            ],
        ),
    )
    supabase = _draft_answer_supabase()
    client = _client(supabase)

    response = client.post(
        "/extension/draft-answer",
        json={"application_id": "app-1", "question_text": "Tell me about your experience."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["eligible"] is True
    assert body["answer_text"] == "I led a team of 5 and invented a new framework."
    assert body["declined_reason"] is None
    assert len(body["warnings"]) == 1
    assert "invented a new framework" in body["warnings"][0]


def test_draft_answer_returns_a_decline_without_a_verify_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_generate(*, system_prompt: str, **_kwargs: Any) -> LLMResponse:
        assert "claim-verification judge" not in system_prompt, "must not verify a declined answer"
        return LLMResponse(
            content=json.dumps(
                {
                    "answer_text": None,
                    "declined_reason": "This is a consent statement, not a question.",
                }
            )
        )

    monkeypatch.setattr("between_jobs.api.extension_routes.llm_generate", fake_generate)
    supabase = _draft_answer_supabase()
    client = _client(supabase)

    response = client.post(
        "/extension/draft-answer",
        json={"application_id": "app-1", "question_text": "Please acknowledge the policy above."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["eligible"] is True
    assert body["answer_text"] is None
    assert body["declined_reason"] == "This is a consent statement, not a question."


def test_draft_answer_404s_for_a_missing_application(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_if_called(**_kwargs: Any) -> LLMResponse:
        raise AssertionError("the LLM must never be called when the application isn't found")

    monkeypatch.setattr("between_jobs.api.extension_routes.llm_generate", fail_if_called)
    supabase = _draft_answer_supabase(applications=[])
    client = _client(supabase)

    response = client.post(
        "/extension/draft-answer",
        json={"application_id": "app-1", "question_text": "Why do you want to work here?"},
    )

    assert response.status_code == 404


def test_draft_answer_requires_an_active_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_if_called(**_kwargs: Any) -> LLMResponse:
        raise AssertionError(
            "the LLM must never be called with no active profile to ground against"
        )

    monkeypatch.setattr("between_jobs.api.extension_routes.llm_generate", fail_if_called)
    supabase = _draft_answer_supabase(profile_versions=[])
    client = _client(supabase)

    response = client.post(
        "/extension/draft-answer",
        json={"application_id": "app-1", "question_text": "Why do you want to work here?"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SETUP_REQUIRED"


def test_draft_answer_errors_cleanly_when_the_job_snapshot_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_if_called(**_kwargs: Any) -> LLMResponse:
        raise AssertionError("the LLM must never be called when the job snapshot can't be read")

    monkeypatch.setattr("between_jobs.api.extension_routes.llm_generate", fail_if_called)
    supabase = _draft_answer_supabase(job_snapshots=[])
    client = _client(supabase)

    response = client.post(
        "/extension/draft-answer",
        json={"application_id": "app-1", "question_text": "Why do you want to work here?"},
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"


def test_draft_answer_fails_open_when_the_verify_call_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_generate(*, system_prompt: str, **_kwargs: Any) -> LLMResponse:
        if "claim-verification judge" in system_prompt:
            raise RuntimeError("provider outage")
        return LLMResponse(
            content=json.dumps({"answer_text": "I led a team of 5.", "declined_reason": None})
        )

    monkeypatch.setattr("between_jobs.api.extension_routes.llm_generate", fake_generate)
    supabase = _draft_answer_supabase()
    client = _client(supabase)

    response = client.post(
        "/extension/draft-answer",
        json={"application_id": "app-1", "question_text": "Tell me about your experience."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["eligible"] is True
    assert body["answer_text"] == "I led a team of 5."
    assert body["warnings"] == []
