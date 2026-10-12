"""Tests for the Gmail OAuth HTTP endpoints (outreach-contactfinder.md
Phase F). `connect_gmail` is a normal authenticated route; `gmail_callback`
is deliberately tested WITHOUT overriding `require_user_id` -- in real
use Google's browser redirect carries no JWT at all, and this route
doesn't depend on that dependency either, so a test that relied on the
override would be testing a code path production traffic never takes.
"""

from __future__ import annotations

import logging
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


_ALL_THREE = ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REDIRECT_URI")
_DISABLED = "Gmail isn't set up on this server."


def _gmail_off(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ALL_THREE:
        monkeypatch.delenv(name, raising=False)


def test_connect_gmail_on_a_server_without_gmail_is_a_clean_404(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _gmail_off(monkeypatch)
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()

    with caplog.at_level(logging.DEBUG), _client(supabase, http) as client:
        response = client.get("/profile/integrations/gmail/connect")

    assert response.status_code == 404
    error = response.json()["error"]
    assert (error["code"], error["message"]) == ("FEATURE_DISABLED", _DISABLED)
    assert error["retryable"] is False and error["settings_path"] is None
    # no state was minted for a redirect that can never come back
    assert supabase.oauth_states.insert_calls == []
    # "the API cannot start" is for a failing boot, not for a person's click
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
    assert "cannot start" not in caplog.text


def test_connect_gmail_asks_who_you_are_before_it_says_anything_about_gmail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _gmail_off(monkeypatch)
    with _client(_FakeSupabaseClient(), _FakeHttpClient(), authenticated=False) as client:
        response = client.get("/profile/integrations/gmail/connect")
    assert response.status_code == 401


@pytest.mark.parametrize("keep", [None, "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_REDIRECT_URI"])
def test_a_server_that_lost_part_of_its_gmail_settings_after_boot_is_off_not_crashing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, keep: str | None
) -> None:
    """The boot refuses a half-set Gmail; this is what a request answers if the environment
    changes under a running process (a test, a hot reload): the same 404, never a 500."""
    supabase, http = _FakeSupabaseClient(), _FakeHttpClient()
    with caplog.at_level(logging.DEBUG), _client(supabase, http) as client:
        for name in _ALL_THREE:
            if name != keep:
                monkeypatch.delenv(name)
        response = client.get("/profile/integrations/gmail/connect")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FEATURE_DISABLED"
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


@pytest.mark.parametrize(
    "params",
    [
        {"code": "auth-code", "state": "state-abc"},  # what Google sends after consent
        {"error": "access_denied"},  # what Google sends when the person says no
        {},  # nothing at all
    ],
)
def test_the_gmail_callback_on_a_server_without_gmail_is_a_clean_404_not_an_html_500(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, params: dict[str, str]
) -> None:
    _gmail_off(monkeypatch)
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()

    with caplog.at_level(logging.DEBUG), _client(supabase, http, authenticated=False) as client:
        response = client.get("/oauth/gmail/callback", params=params)

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    error = response.json()["error"]
    assert (error["code"], error["message"]) == ("FEATURE_DISABLED", _DISABLED)
    assert "<html" not in response.text
    # nothing was looked up, consumed, exchanged or stored for a redirect nobody asked for
    assert supabase.oauth_states.delete_calls == []
    assert http.post_calls == []
    assert supabase.provider_credentials.upsert_calls == []
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
    assert "cannot start" not in caplog.text


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


@pytest.mark.parametrize(
    "params",
    [
        {"error": "access_denied", "code": "auth-code", "state": "state-abc"},
        {"code": "auth-code"},  # no state to recover the person from
        {"state": "state-abc"},  # no code to exchange
        {"error": "access_denied", "state": "state-abc"},
        {"error": "access_denied", "code": "auth-code"},
    ],
)
def test_gmail_callback_without_everything_it_needs_is_a_clean_failure_page_and_does_nothing(
    params: dict[str, str],
) -> None:
    state_row = {
        "state": "state-abc",
        "user_id": _USER_ID,
        "provider": "gmail",
        "created_at": datetime.now(UTC).isoformat(),
    }
    supabase = _FakeSupabaseClient(oauth_states=_OauthStatesTable(select_rows=[state_row]))
    http = _FakeHttpClient()

    with _client(supabase, http, authenticated=False) as client:
        response = client.get("/oauth/gmail/callback", params=params)

    assert response.status_code == 400
    assert "didn't authorize" in response.text
    assert supabase.oauth_states.delete_calls == []  # the state was not spent
    assert http.post_calls == []  # no code was exchanged
    assert supabase.provider_credentials.upsert_calls == []


@pytest.mark.parametrize("google_status", [400, 401, 503])
def test_gmail_callback_when_google_refuses_the_code_shows_a_clean_failure_page(
    google_status: int,
) -> None:
    state_row = {
        "state": "state-abc",
        "user_id": _USER_ID,
        "provider": "gmail",
        "created_at": datetime.now(UTC).isoformat(),
    }
    supabase = _FakeSupabaseClient(oauth_states=_OauthStatesTable(select_rows=[state_row]))
    http = _FakeHttpClient(status_code=google_status, body={"error": "invalid_grant"})

    with _client(supabase, http, authenticated=False) as client:
        response = client.get(
            "/oauth/gmail/callback", params={"code": "auth-code", "state": "state-abc"}
        )

    assert response.status_code == 400
    assert "text/html" in response.headers["content-type"]
    assert "Couldn't connect Gmail" in response.text
    assert "Google rejected that authorization code." in response.text
    assert supabase.provider_credentials.upsert_calls == []


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
