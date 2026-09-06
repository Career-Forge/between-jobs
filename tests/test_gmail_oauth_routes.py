"""Tests for the Gmail OAuth HTTP endpoints (outreach-contactfinder.md
Phase F). `connect_gmail` is a normal authenticated route; `gmail_callback`
is deliberately tested WITHOUT overriding `require_user_id` -- in real
use Google's browser redirect carries no JWT at all, and this route
doesn't depend on that dependency either, so a test that relied on the
override would be testing a code path production traffic never takes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from between_jobs.api.app import app
from between_jobs.api.app_state import get_http_client, get_supabase
from between_jobs.api.auth import require_user_id

_USER_ID = "00000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _OauthStatesTable:
    def __init__(self, *, select_rows: list[dict[str, Any]] | None = None) -> None:
        self.select_rows = select_rows if select_rows is not None else []
        self.insert_calls: list[Any] = []
        self.delete_calls: list[str] = []

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(list(self.select_rows))

    def insert(self, data: Any) -> _ChainBuilder:
        self.insert_calls.append(data)
        self.select_rows.append(dict(data, created_at=datetime.now(UTC).isoformat()))
        return _ChainBuilder([data])

    def delete(self) -> _DeleteBuilder:
        return _DeleteBuilder(self)


class _DeleteBuilder:
    def __init__(self, table: _OauthStatesTable) -> None:
        self._table = table

    def eq(self, _column: str, value: Any) -> _DeleteBuilder:
        self._table.delete_calls.append(value)
        self._table.select_rows = [r for r in self._table.select_rows if r.get("state") != value]
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=[])


class _CredentialsTable:
    def __init__(self) -> None:
        self.upsert_calls: list[dict[str, Any]] = []

    def upsert(self, data: dict[str, Any], on_conflict: str = "") -> _ChainBuilder:
        self.upsert_calls.append(data)
        return _ChainBuilder([{"id": "cred-1", **data}])


class _FakeRpcBuilder:
    def __init__(self, data: Any) -> None:
        self._data = data

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._data)


class _FakeSupabaseClient:
    def __init__(
        self,
        *,
        oauth_states: _OauthStatesTable | None = None,
        provider_credentials: _CredentialsTable | None = None,
    ) -> None:
        self.oauth_states = oauth_states or _OauthStatesTable()
        self.provider_credentials = provider_credentials or _CredentialsTable()

    def table(self, name: str) -> Any:
        return {
            "oauth_states": self.oauth_states,
            "provider_credentials": self.provider_credentials,
        }[name]

    def rpc(self, fn: str, _params: dict[str, Any]) -> _FakeRpcBuilder:
        if fn == "encrypt_secret":
            return _FakeRpcBuilder("ciphertext-abc")
        raise AssertionError(f"unexpected rpc: {fn}")


class _FakeHttpClient:
    def __init__(self, *, status_code: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self.body = body if body is not None else {"access_token": "at-1", "refresh_token": "rt-1"}
        self.post_calls: list[str] = []

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.post_calls.append(url)
        return httpx.Response(
            status_code=self.status_code, json=self.body, request=httpx.Request("POST", url)
        )


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-123")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret-456")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8012/oauth/gmail/callback")


@pytest.fixture(autouse=True)
def _clear_overrides() -> Any:
    yield
    app.dependency_overrides.clear()


def _client(
    supabase: _FakeSupabaseClient, http: _FakeHttpClient, *, authenticated: bool = True
) -> TestClient:
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[get_http_client] = lambda: http
    if authenticated:
        app.dependency_overrides[require_user_id] = lambda: _USER_ID
    return TestClient(app)


def test_connect_gmail_returns_a_real_google_authorize_url() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.get("/profile/integrations/gmail/connect")

    assert response.status_code == 200
    url = response.json()["authorize_url"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=client-123" in url
    assert len(supabase.oauth_states.insert_calls) == 1
    assert supabase.oauth_states.insert_calls[0]["user_id"] == _USER_ID


def test_connect_gmail_without_config_returns_setup_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.get("/profile/integrations/gmail/connect")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SETUP_REQUIRED"


def test_gmail_callback_success_saves_the_refresh_token_and_shows_html() -> None:
    state_row = {
        "state": "state-abc",
        "user_id": _USER_ID,
        "provider": "gmail",
        "created_at": datetime.now(UTC).isoformat(),
    }
    supabase = _FakeSupabaseClient(oauth_states=_OauthStatesTable(select_rows=[state_row]))
    http = _FakeHttpClient(
        body={
            "access_token": "at-1",
            "refresh_token": "rt-1",
            "scope": "https://www.googleapis.com/auth/gmail.compose "
            "https://www.googleapis.com/auth/gmail.readonly",
        }
    )

    with _client(supabase, http, authenticated=False) as client:
        response = client.get(
            "/oauth/gmail/callback", params={"code": "auth-code", "state": "state-abc"}
        )

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Gmail connected" in response.text
    assert len(supabase.provider_credentials.upsert_calls) == 1
    saved = supabase.provider_credentials.upsert_calls[0]
    assert saved["user_id"] == _USER_ID
    assert saved["service"] == "oauth"
    assert saved["provider"] == "gmail"
    assert saved["is_validated"] is True
    # the raw refresh token itself is never stored -- only ciphertext
    assert saved["secret_encrypted"] == "ciphertext-abc"
    # the real granted scope -- captured from Google's own token response,
    # not assumed to match whatever was requested
    assert saved["scope"] == (
        "https://www.googleapis.com/auth/gmail.compose "
        "https://www.googleapis.com/auth/gmail.readonly"
    )


def test_gmail_callback_persists_a_null_scope_when_google_omits_it() -> None:
    state_row = {
        "state": "state-abc",
        "user_id": _USER_ID,
        "provider": "gmail",
        "created_at": datetime.now(UTC).isoformat(),
    }
    supabase = _FakeSupabaseClient(oauth_states=_OauthStatesTable(select_rows=[state_row]))
    http = _FakeHttpClient(body={"access_token": "at-1", "refresh_token": "rt-1"})

    with _client(supabase, http, authenticated=False) as client:
        response = client.get(
            "/oauth/gmail/callback", params={"code": "auth-code", "state": "state-abc"}
        )

    assert response.status_code == 200
    assert supabase.provider_credentials.upsert_calls[0]["scope"] is None


def test_gmail_callback_with_expired_state_shows_a_clean_failure_page() -> None:
    supabase = _FakeSupabaseClient(oauth_states=_OauthStatesTable(select_rows=[]))
    http = _FakeHttpClient()

    with _client(supabase, http, authenticated=False) as client:
        response = client.get(
            "/oauth/gmail/callback", params={"code": "auth-code", "state": "nonexistent"}
        )

    assert response.status_code == 400
    assert "expired" in response.text
    assert supabase.provider_credentials.upsert_calls == []


def test_gmail_callback_when_google_denies_access_shows_a_clean_failure_page() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()

    with _client(supabase, http, authenticated=False) as client:
        response = client.get("/oauth/gmail/callback", params={"error": "access_denied"})

    assert response.status_code == 400
    assert "didn't authorize" in response.text


def test_gmail_callback_when_google_omits_refresh_token_shows_a_clean_failure_page() -> None:
    state_row = {
        "state": "state-abc",
        "user_id": _USER_ID,
        "provider": "gmail",
        "created_at": datetime.now(UTC).isoformat(),
    }
    supabase = _FakeSupabaseClient(oauth_states=_OauthStatesTable(select_rows=[state_row]))
    http = _FakeHttpClient(body={"access_token": "at-1"})  # no refresh_token

    with _client(supabase, http, authenticated=False) as client:
        response = client.get(
            "/oauth/gmail/callback", params={"code": "auth-code", "state": "state-abc"}
        )

    assert response.status_code == 400
    assert "offline access" in response.text
    assert supabase.provider_credentials.upsert_calls == []
