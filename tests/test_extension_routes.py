"""Tests for the browser-extension HTTP endpoints (browser-extension.md
E1). Exercises the real FastAPI routes via TestClient, same convention as
test_saved_searches_routes.py."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from between_jobs.api.app import app
from between_jobs.api.app_state import get_supabase
from between_jobs.api.auth import require_user_id

_USER_ID = "00000000-0000-0000-0000-000000000001"
_OTHER_USER_ID = "00000000-0000-0000-0000-000000000002"


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
    ) -> None:
        self._tables = {
            "applications": _FakeTable(rows=applications),
            "jobs": _FakeTable(rows=jobs),
            "job_snapshots": _FakeTable(rows=job_snapshots),
            "approved_answers": _FakeTable(rows=approved_answers),
        }

    def table(self, name: str) -> _FakeTable:
        return self._tables[name]


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
