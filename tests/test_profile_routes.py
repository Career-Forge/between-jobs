"""Tests for the profile HTTP endpoints (Sprint 2.5c).

Exercises the real FastAPI routes via TestClient (like test_api.py does for
/sessions) rather than calling profile_store.py directly -- this is what
proves request parsing, dependency wiring, and error-code mapping actually
work, not just the underlying functions in isolation (those are covered in
test_profile.py/test_profile_store.py).
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
_VERSION_ID = "10000000-0000-0000-0000-000000000001"
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

_VALID_RAW_TEXT = """{
  "personal": {"name": "Jane Doe", "emails": [], "phones": [], "links": {}, "location": {}},
  "summary_bullets": [], "experience": [{"title": "Engineer", "company": "Acme",
  "start_date": "2022-01", "end_date": "present", "bullets": []}],
  "projects": [], "education": [], "skills": {}, "achievements": []
}"""


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]], count: int | None = None) -> None:
        self._rows = rows
        self._count = count

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
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows, count=self._count)


class _FakeProfileVersionsTable:
    def __init__(
        self,
        *,
        select_rows: list[dict[str, Any]],
        insert_row: dict[str, Any] | None = None,
        update_row: dict[str, Any] | None = None,
        select_count: int | None = None,
        select_rows_after_first: list[dict[str, Any]] | None = None,
    ) -> None:
        self.select_rows = select_rows
        self.insert_row = insert_row
        self.update_row = update_row
        self.select_count = select_count
        self.delete_calls = 0
        self._select_rows_after_first = select_rows_after_first
        self._select_call_count = 0

    def select(self, columns: str, **_: Any) -> _ChainBuilder:
        self._select_call_count += 1
        rows = (
            self._select_rows_after_first
            if self._select_call_count > 1 and self._select_rows_after_first is not None
            else self.select_rows
        )
        return _ChainBuilder(rows, count=self.select_count)

    def insert(self, data: dict[str, Any]) -> _ChainBuilder:
        rows = [self.insert_row] if self.insert_row is not None else []
        return _ChainBuilder(rows)

    def update(self, data: dict[str, Any]) -> _ChainBuilder:
        rows = [self.update_row] if self.update_row is not None else []
        return _ChainBuilder(rows)

    def delete(self) -> _ChainBuilder:
        self.delete_calls += 1
        return _ChainBuilder([])


class _FakeCareerFactsTable:
    def __init__(self, *, select_rows: list[dict[str, Any]] | None = None) -> None:
        self.select_rows = select_rows or []

    def insert(self, data: list[dict[str, Any]]) -> _ChainBuilder:
        return _ChainBuilder([])

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)


class _FakeTable:
    """A plain select-only fake -- matches test_resume_documents_routes.py's
    own `_FakeTable`, reused here for the two tables `credential_resolver
    .resolve()` reads that this file's other fakes never needed before."""

    def __init__(self, *, select_rows: list[dict[str, Any]]) -> None:
        self.select_rows = select_rows

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)


class _FakeRpcBuilder:
    def __init__(self, data: Any) -> None:
        self._data = data

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._data)


class _FakeSupabaseClient:
    def __init__(
        self,
        profile_versions: _FakeProfileVersionsTable,
        career_facts: _FakeCareerFactsTable | None = None,
        *,
        capability_preferences: _FakeTable | None = None,
        provider_credentials: _FakeTable | None = None,
    ) -> None:
        self.profile_versions = profile_versions
        self.career_facts = career_facts or _FakeCareerFactsTable()
        self.capability_preferences = capability_preferences or _FakeTable(
            select_rows=[_PREFERENCE_ROW]
        )
        self.provider_credentials = provider_credentials or _FakeTable(
            select_rows=[_CREDENTIAL_ROW]
        )

    def table(self, name: str) -> Any:
        if name == "profile_versions":
            return self.profile_versions
        if name == "career_facts":
            return self.career_facts
        if name == "capability_preferences":
            return self.capability_preferences
        if name == "provider_credentials":
            return self.provider_credentials
        raise AssertionError(f"unexpected table: {name}")

    def rpc(self, fn: str, params: dict[str, Any]) -> _FakeRpcBuilder:
        if fn == "decrypt_secret":
            return _FakeRpcBuilder("sk-or-v1-real-secret")
        raise AssertionError(f"unexpected rpc: {fn}")


class _FakeHttpClient:
    def __init__(self, *, gap_draft_body: dict[str, Any] | None = None) -> None:
        self.post_calls: list[tuple[str, dict[str, Any]]] = []
        self._gap_draft_body = gap_draft_body or {
            "bullet": "Preprocessed images with OpenCV.",
            "entity_pointer": "/experience/0",
        }

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.post_calls.append((url, kwargs))
        if url.endswith("/gap-interview/draft"):
            return httpx.Response(
                status_code=200, json=self._gap_draft_body, request=httpx.Request("POST", url)
            )
        raise AssertionError(f"unexpected forge-engines call: {url}")


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # app.py's lifespan runs for every TestClient(app) regardless of
    # dependency_overrides -- these just need to be non-empty so it
    # doesn't raise; no test here actually exercises the real clients
    # built from them.
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


def test_import_profile_version_success() -> None:
    inserted_row = {"id": _VERSION_ID, "user_id": _USER_ID, "activated_at": None}
    table = _FakeProfileVersionsTable(select_rows=[], insert_row=inserted_row)
    supabase = _FakeSupabaseClient(table)
    with _client(supabase) as client:
        response = client.post("/profile/versions", json={"raw_text": _VALID_RAW_TEXT})

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == _VERSION_ID
    assert body["stats"]["experience"] == 1
    assert body["warnings"]


def test_import_profile_version_invalid_json_returns_422() -> None:
    supabase = _FakeSupabaseClient(_FakeProfileVersionsTable(select_rows=[]))
    with _client(supabase) as client:
        response = client.post("/profile/versions", json={"raw_text": "not json at all"})

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "INVALID_INPUT"
    assert "valid JSON" in body["message"]


def test_import_profile_version_missing_field_returns_structured_422() -> None:
    # Regression test for the gap Sprint 2.6f closed: a missing required
    # field never reached ProfileImportError at all -- FastAPI's own
    # automatic body validation rejected it first, before this route's
    # code ran. Confirms that failure mode also carries the Appendix B
    # envelope now, not FastAPI's default {"detail": [...]} shape.
    supabase = _FakeSupabaseClient(_FakeProfileVersionsTable(select_rows=[]))
    with _client(supabase) as client:
        response = client.post("/profile/versions", json={})

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "INVALID_INPUT"


def test_get_profile_version_found() -> None:
    row = {"id": _VERSION_ID, "user_id": _USER_ID}
    supabase = _FakeSupabaseClient(_FakeProfileVersionsTable(select_rows=[row]))
    with _client(supabase) as client:
        response = client.get(f"/profile/versions/{_VERSION_ID}")

    assert response.status_code == 200
    assert response.json() == row


def test_get_career_facts_success() -> None:
    version_row = {"id": _VERSION_ID, "user_id": _USER_ID}
    facts = [{"id": "fact-1", "fact_type": "experience", "value_json": {"title": "Eng"}}]
    supabase = _FakeSupabaseClient(
        _FakeProfileVersionsTable(select_rows=[version_row]),
        _FakeCareerFactsTable(select_rows=facts),
    )
    with _client(supabase) as client:
        response = client.get(f"/profile/versions/{_VERSION_ID}/career-facts")

    assert response.status_code == 200
    assert response.json() == facts


def test_get_career_facts_version_not_found_returns_404() -> None:
    supabase = _FakeSupabaseClient(_FakeProfileVersionsTable(select_rows=[]))
    with _client(supabase) as client:
        response = client.get(f"/profile/versions/{_VERSION_ID}/career-facts")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_get_profile_version_not_found() -> None:
    supabase = _FakeSupabaseClient(_FakeProfileVersionsTable(select_rows=[]))
    with _client(supabase) as client:
        response = client.get(f"/profile/versions/{_VERSION_ID}")

    assert response.status_code == 404


def test_activate_profile_version_success() -> None:
    updated_row = {"id": _VERSION_ID, "activated_at": "2026-08-06T00:00:00Z"}
    supabase = _FakeSupabaseClient(
        _FakeProfileVersionsTable(select_rows=[], update_row=updated_row)
    )
    with _client(supabase) as client:
        response = client.post(f"/profile/versions/{_VERSION_ID}/activate")

    assert response.status_code == 200
    assert response.json() == updated_row


def test_activate_profile_version_not_found() -> None:
    supabase = _FakeSupabaseClient(_FakeProfileVersionsTable(select_rows=[], update_row=None))
    with _client(supabase) as client:
        response = client.post(f"/profile/versions/{_VERSION_ID}/activate")

    assert response.status_code == 404


def test_cancel_profile_version_success() -> None:
    row = {"id": _VERSION_ID, "user_id": _USER_ID, "activated_at": None}
    table = _FakeProfileVersionsTable(select_rows=[row])
    supabase = _FakeSupabaseClient(table)
    with _client(supabase) as client:
        response = client.delete(f"/profile/versions/{_VERSION_ID}")

    assert response.status_code == 204
    assert table.delete_calls == 1


def test_cancel_profile_version_not_found() -> None:
    supabase = _FakeSupabaseClient(_FakeProfileVersionsTable(select_rows=[]))
    with _client(supabase) as client:
        response = client.delete(f"/profile/versions/{_VERSION_ID}")

    assert response.status_code == 404


def test_cancel_profile_version_already_activated_returns_409() -> None:
    row = {"id": _VERSION_ID, "user_id": _USER_ID, "activated_at": "2026-08-06T00:00:00Z"}
    supabase = _FakeSupabaseClient(_FakeProfileVersionsTable(select_rows=[row]))
    with _client(supabase) as client:
        response = client.delete(f"/profile/versions/{_VERSION_ID}")

    assert response.status_code == 409


def test_get_current_profile_found() -> None:
    row = {"id": _VERSION_ID, "activated_at": "2026-08-06T00:00:00Z"}
    supabase = _FakeSupabaseClient(_FakeProfileVersionsTable(select_rows=[row], select_count=3))
    with _client(supabase) as client:
        response = client.get("/profile/current")

    assert response.status_code == 200
    assert response.json() == {**row, "version_count": 3}


def test_get_current_profile_none_yet() -> None:
    supabase = _FakeSupabaseClient(_FakeProfileVersionsTable(select_rows=[]))
    with _client(supabase) as client:
        response = client.get("/profile/current")

    assert response.status_code == 404


def test_get_current_profile_without_auth_header_returns_401() -> None:
    # No dependency override for require_user_id -- proves the route is
    # actually protected, same reasoning as test_api.py's equivalent for
    # /sessions.
    with TestClient(app) as client:
        response = client.get("/profile/current")
    assert response.status_code == 401


# ── S4b: /gap-interview/draft and /gap-interview/approve ────────────────────

_ACTIVE_VERSION_ROW = {
    "id": _VERSION_ID,
    "user_id": _USER_ID,
    "activated_at": "2026-08-06T00:00:00Z",
    "canonical_json": {
        "personal": {"name": "Jane Doe"},
        "experience": [
            {
                "title": "AI Engineer",
                "company": "Capgemini",
                "start_date": "2024-01",
                "end_date": "present",
                "bullets": ["Built agentic pipelines."],
            }
        ],
        "projects": [],
        "education": [],
        "skills": {"programming": ["Python"]},
        "achievements": [],
    },
}


def test_draft_gap_interview_fact_success() -> None:
    supabase = _FakeSupabaseClient(_FakeProfileVersionsTable(select_rows=[_ACTIVE_VERSION_ROW]))
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(
            "/profile/gap-interview/draft",
            json={"question": "Have you used OpenCV?", "answer": "Yes, a bit."},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["bullet"] == "Preprocessed images with OpenCV."
    assert body["entity_pointer"] == "/experience/0"
    assert body["candidates"] == [{"pointer": "/experience/0", "label": "AI Engineer at Capgemini"}]
    draft_call = next(
        kwargs for url, kwargs in http.post_calls if url.endswith("/gap-interview/draft")
    )
    assert draft_call["json"]["question"] == "Have you used OpenCV?"
    assert draft_call["json"]["answer"] == "Yes, a bit."
    assert draft_call["json"]["candidates"] == [
        {"pointer": "/experience/0", "label": "AI Engineer at Capgemini"}
    ]


def test_draft_gap_interview_fact_no_active_profile_returns_404() -> None:
    supabase = _FakeSupabaseClient(_FakeProfileVersionsTable(select_rows=[]))
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(
            "/profile/gap-interview/draft",
            json={"question": "Have you used OpenCV?", "answer": "Yes."},
        )

    assert response.status_code == 404
    assert http.post_calls == []


def test_draft_gap_interview_fact_no_entries_returns_invalid_input() -> None:
    empty_version = {**_ACTIVE_VERSION_ROW, "canonical_json": {"experience": [], "projects": []}}
    supabase = _FakeSupabaseClient(_FakeProfileVersionsTable(select_rows=[empty_version]))
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(
            "/profile/gap-interview/draft",
            json={"question": "Have you used OpenCV?", "answer": "Yes."},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"
    assert http.post_calls == []


def test_approve_gap_interview_fact_success() -> None:
    inserted_row = {"id": "20000000-0000-0000-0000-000000000002", "activated_at": None}
    supabase = _FakeSupabaseClient(
        _FakeProfileVersionsTable(
            select_rows=[_ACTIVE_VERSION_ROW],
            insert_row=inserted_row,
            # First select() is the route's own get_active_version lookup
            # (finds the row); the second is create_pending_version's own
            # dedup-by-content-hash check, which the real filtered query
            # would find nothing for -- appending a bullet changes the
            # content_hash, so no existing row would ever match it.
            select_rows_after_first=[],
        )
    )

    with _client(supabase) as client:
        response = client.post(
            "/profile/gap-interview/approve",
            json={"bullet": "Preprocessed images with OpenCV.", "entity_pointer": "/experience/0"},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == inserted_row["id"]
    assert body["stats"]["experience"] == 1


def test_approve_gap_interview_fact_no_active_profile_returns_404() -> None:
    supabase = _FakeSupabaseClient(_FakeProfileVersionsTable(select_rows=[]))

    with _client(supabase) as client:
        response = client.post(
            "/profile/gap-interview/approve",
            json={"bullet": "Something.", "entity_pointer": "/experience/0"},
        )

    assert response.status_code == 404


def test_approve_gap_interview_fact_invalid_pointer_returns_422() -> None:
    supabase = _FakeSupabaseClient(_FakeProfileVersionsTable(select_rows=[_ACTIVE_VERSION_ROW]))

    with _client(supabase) as client:
        response = client.post(
            "/profile/gap-interview/approve",
            json={"bullet": "Something.", "entity_pointer": "/experience/99"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_approve_gap_interview_fact_empty_bullet_returns_422() -> None:
    supabase = _FakeSupabaseClient(_FakeProfileVersionsTable(select_rows=[_ACTIVE_VERSION_ROW]))

    with _client(supabase) as client:
        response = client.post(
            "/profile/gap-interview/approve",
            json={"bullet": "   ", "entity_pointer": "/experience/0"},
        )

    assert response.status_code == 422
