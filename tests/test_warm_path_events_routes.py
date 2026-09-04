"""Tests for the events warm-path HTTP endpoints (outreach-contactfinder.md
Phase D). Exercises the real FastAPI routes via TestClient, faking the
You.com/Firecrawl HTTP boundary and the pipeline's own `generate`
injection point -- never a real network call or real spend.
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
_PROFILE_ROW = {
    "user_id": _USER_ID,
    "activated_at": "2026-08-01T00:00:00Z",
    "canonical_json": {"personal": {"name": "Jane Doe", "location": {"city": "Brooklyn"}}},
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
        self.insert_calls: list[Any] = []
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


class _CredentialTable:
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
        profile_versions: _FakeTable | None = None,
        warm_path_runs: _FakeTable | None = None,
        warm_path_events: _FakeTable | None = None,
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
        self.profile_versions = profile_versions or _FakeTable(select_rows=[_PROFILE_ROW])
        self.warm_path_runs = warm_path_runs or _FakeTable(
            select_rows=[], insert_row={"id": "run-1", "application_id": _APPLICATION_ID}
        )
        self.warm_path_events = warm_path_events or _FakeTable(select_rows=[])

    def table(self, name: str) -> Any:
        return {
            "applications": self.applications,
            "job_snapshots": self.job_snapshots,
            "capability_preferences": self.capability_preferences,
            "provider_credentials": self._credential_table,
            "profile_versions": self.profile_versions,
            "warm_path_runs": self.warm_path_runs,
            "warm_path_events": self.warm_path_events,
        }[name]

    def rpc(self, fn: str, _params: dict[str, Any]) -> _FakeRpcBuilder:
        if fn == "decrypt_secret":
            return _FakeRpcBuilder("decrypted-secret")
        raise AssertionError(f"unexpected rpc: {fn}")


_HIT_JSON = {
    "results": {
        "web": [
            {
                "title": "Jane Doe to speak at Data+AI Summit",
                "url": "https://conf.example/data-ai-summit",
                "snippets": ["Jane Doe, Staff Engineer at Acme, will present on scaling ML infra."],
                "page_age": "2026-08-01",
            }
        ]
    }
}

_EVENT_LLM_RESPONSE = json.dumps(
    [
        {
            "event_name": "Data+AI Summit",
            "event_url": "https://conf.example/data-ai-summit",
            "event_date": "2026-10-01",
            "location": "New York",
            "certainty": "confirmed_speaker",
            "speaker_name": "Jane Doe",
            "speaker_title": "Staff Engineer",
            "talk_topic": "Scaling ML infra",
        }
    ]
)


class _FakeHttpClient:
    def __init__(self, *, status_code: int = 200) -> None:
        self.status_code = status_code
        self.post_calls: list[str] = []
        self.post_kwargs: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.post_calls.append(url)
        self.post_kwargs.append(kwargs)
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


def _patch_llm(monkeypatch: pytest.MonkeyPatch, content: str = _EVENT_LLM_RESPONSE) -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=content)

    monkeypatch.setattr("between_jobs.api.warm_path_events_routes.llm_generate", fake_generate)


def _client(supabase: _FakeSupabaseClient, http: _FakeHttpClient) -> TestClient:
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    app.dependency_overrides[get_http_client] = lambda: http
    return TestClient(app)


def test_get_warm_path_events_returns_none_when_nothing_generated_yet() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}/warm-path-events")

    assert response.status_code == 200
    assert response.json() == {"run": None, "events": []}


def test_generate_warm_path_events_with_no_search_provider_returns_setup_required() -> None:
    supabase = _FakeSupabaseClient(
        provider_credentials={("llm", "openrouter"): _LLM_CREDENTIAL_ROW}
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/warm-path-events")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SETUP_REQUIRED"


def test_generate_warm_path_events_success_stores_run_and_events_using_the_users_metro(
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
        response = client.post(f"/applications/{_APPLICATION_ID}/warm-path-events")

    assert response.status_code == 201
    body = response.json()
    assert len(body["events"]) == 1
    assert body["events"][0]["speaker_name"] == "Jane Doe"
    assert len(http.post_calls) == 3  # 3 bounded certainty-tier queries
    # the query plan is built from the user's own profile location
    # (Brooklyn), not the job's ("Remote") -- confirms _user_metro is
    # actually threaded through, not silently ignored
    assert all("Brooklyn" in kwargs["json"]["query"] for kwargs in http.post_kwargs)


def test_generate_warm_path_events_works_with_no_profile_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supabase = _FakeSupabaseClient(
        provider_credentials={
            ("llm", "openrouter"): _LLM_CREDENTIAL_ROW,
            ("search", "you_com"): _YOU_COM_CREDENTIAL_ROW,
        },
        profile_versions=_FakeTable(select_rows=[]),
    )
    http = _FakeHttpClient()
    _patch_llm(monkeypatch)

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/warm-path-events")

    assert response.status_code == 201


def test_generate_warm_path_events_fails_open_on_a_malformed_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A profile row that fails ResumeTemplate validation (missing the
    required `personal.name`) must degrade to "unknown metro," not 500
    the whole request -- the location lookup is a query refinement, not
    a hard requirement."""
    malformed_profile = {
        "user_id": _USER_ID,
        "activated_at": "2026-08-01T00:00:00Z",
        "canonical_json": {"personal": {"location": {"city": "Brooklyn"}}},  # no `name`
    }
    supabase = _FakeSupabaseClient(
        provider_credentials={
            ("llm", "openrouter"): _LLM_CREDENTIAL_ROW,
            ("search", "you_com"): _YOU_COM_CREDENTIAL_ROW,
        },
        profile_versions=_FakeTable(select_rows=[malformed_profile]),
    )
    http = _FakeHttpClient()
    _patch_llm(monkeypatch)

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/warm-path-events")

    assert response.status_code == 201
    assert all("Brooklyn" not in kwargs["json"]["query"] for kwargs in http.post_kwargs)


def test_generate_warm_path_events_not_found_returns_404() -> None:
    supabase = _FakeSupabaseClient(applications=_FakeTable(select_rows=[]))
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/warm-path-events")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
