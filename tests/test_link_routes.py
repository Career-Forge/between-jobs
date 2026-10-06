"""Tests for the /link/code HTTP endpoint (Sprint 2.8c)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from between_jobs.api.app import app
from between_jobs.api.app_state import get_supabase
from between_jobs.api.auth import require_user_id

_USER_ID = "00000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def is_(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTable:
    def __init__(self) -> None:
        self.delete_calls = 0
        self.insert_calls: list[dict[str, Any]] = []

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder([])

    def insert(self, data: dict[str, Any]) -> _ChainBuilder:
        self.insert_calls.append(data)
        return _ChainBuilder([{**data, "id": "link-code-1"}])

    def delete(self) -> _ChainBuilder:
        self.delete_calls += 1
        return _ChainBuilder([])


class _FakeSupabaseClient:
    def __init__(self) -> None:
        self.link_codes = _FakeTable()

    def table(self, name: str) -> Any:
        assert name == "link_codes"
        return self.link_codes


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")


def _client(supabase: _FakeSupabaseClient) -> TestClient:
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Any:
    yield
    app.dependency_overrides.clear()


def test_mint_link_code_success() -> None:
    supabase = _FakeSupabaseClient()
    with _client(supabase) as client:
        response = client.post("/link/code", json={"channel": "telegram"})

    assert response.status_code == 201
    body = response.json()
    assert body["channel"] == "telegram"
    assert len(body["code"]) == 8
    assert "expires_at" in body


def test_mint_link_code_rejects_unsupported_channel() -> None:
    supabase = _FakeSupabaseClient()
    with _client(supabase) as client:
        response = client.post("/link/code", json={"channel": "slack"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"
    assert supabase.link_codes.insert_calls == []


def test_discord_is_in_the_vocabulary_but_no_code_is_minted_while_nothing_serves_it() -> None:
    """Discord is a channel the link route knows (so it is not INVALID_INPUT), but there is no
    Discord adapter yet, so a code for it could not be redeemed: nothing is minted and the
    answer says Discord is not set up -- never a code that looks as if it worked."""
    supabase = _FakeSupabaseClient()
    with _client(supabase) as client:
        response = client.post("/link/code", json={"channel": "discord"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FEATURE_DISABLED"
    assert "Discord" in response.json()["error"]["message"]
    assert supabase.link_codes.insert_calls == []
    assert supabase.link_codes.delete_calls == 0


def test_a_discord_code_is_minted_for_discord_once_a_server_serves_it() -> None:
    """The route's plumbing is channel-generic: when something turns Discord on, a code is
    minted for that channel and stored under it (what the adapter's own task will rely on)."""
    supabase = _FakeSupabaseClient()
    with _client(supabase) as client:
        client.app.state.discord_enabled = True  # type: ignore[attr-defined]
        try:
            response = client.post("/link/code", json={"channel": "discord"})
        finally:
            client.app.state.discord_enabled = False  # type: ignore[attr-defined]

    assert response.status_code == 201
    assert response.json()["channel"] == "discord"
    (inserted,) = supabase.link_codes.insert_calls
    assert inserted["channel"] == "discord"
    assert inserted["user_id"] == _USER_ID


def test_telegram_is_still_refused_on_a_server_without_a_bot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET")
    supabase = _FakeSupabaseClient()
    with _client(supabase) as client:
        response = client.post("/link/code", json={"channel": "telegram"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FEATURE_DISABLED"
    assert "Telegram" in response.json()["error"]["message"]
    assert supabase.link_codes.insert_calls == []


def test_mint_link_code_requires_auth() -> None:
    supabase = _FakeSupabaseClient()
    app.dependency_overrides[get_supabase] = lambda: supabase
    try:
        with TestClient(app) as client:
            response = client.post("/link/code", json={"channel": "telegram"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
