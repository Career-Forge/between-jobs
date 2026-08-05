"""Tests for the Telegram webhook route.

The secret-token check runs for real here (real header comparison against
the real app.state value set from the stubbed env var) rather than being
overridden away -- it's the whole point of this endpoint's security, same
reasoning as leaving require_user_id's missing-header path real in
test_api.py.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from between_jobs.api.app import app
from between_jobs.api.app_state import get_supabase, get_telegram_client

_WEBHOOK_SECRET = "test-secret-not-real"
_TELEGRAM_USER_ID = 987654321
_CHAT_ID = 987654321
_EXISTING_USER_ID = "00000000-0000-0000-0000-000000000001"
_NEW_USER_ID = "00000000-0000-0000-0000-000000000002"


class _FakeTelegramLinksQuery:
    def __init__(self, select_rows: list[dict[str, Any]]) -> None:
        self._select_rows = select_rows

    def select(self, columns: str) -> _FakeTelegramLinksQuery:
        return self

    def eq(self, column: str, value: Any) -> _FakeTelegramLinksQuery:
        return self

    def insert(self, data: dict[str, Any]) -> _FakeTelegramLinksQuery:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._select_rows)


class _FakeAdmin:
    def __init__(self) -> None:
        self.create_user_calls: list[dict[str, Any]] = []

    async def create_user(self, attributes: dict[str, Any]) -> SimpleNamespace:
        self.create_user_calls.append(attributes)
        return SimpleNamespace(user=SimpleNamespace(id=_NEW_USER_ID))


class _FakeSupabaseClient:
    def __init__(self, select_rows: list[dict[str, Any]]) -> None:
        self.auth = SimpleNamespace(admin=_FakeAdmin())
        self._select_rows = select_rows

    def table(self, name: str) -> _FakeTelegramLinksQuery:
        assert name == "telegram_links"
        return _FakeTelegramLinksQuery(self._select_rows)


class _FakeTelegramClient:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


def _message_update(text: str = "hello") -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "from": {"id": _TELEGRAM_USER_ID, "is_bot": False, "first_name": "Test"},
            "chat": {"id": _CHAT_ID, "type": "private"},
            "date": 1785000000,
            "text": text,
        },
    }


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", _WEBHOOK_SECRET)


def test_missing_secret_header_rejected() -> None:
    with TestClient(app) as client:
        response = client.post("/telegram/webhook", json=_message_update())
    assert response.status_code == 401


def test_wrong_secret_header_rejected() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook",
            json=_message_update(),
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
        )
    assert response.status_code == 401


def test_message_from_new_user_provisions_and_replies() -> None:
    fake_supabase = _FakeSupabaseClient(select_rows=[])
    fake_telegram = _FakeTelegramClient()
    app.dependency_overrides[get_supabase] = lambda: fake_supabase
    app.dependency_overrides[get_telegram_client] = lambda: fake_telegram
    try:
        with TestClient(app) as client:
            response = client.post(
                "/telegram/webhook",
                json=_message_update("hi there"),
                headers={"X-Telegram-Bot-Api-Secret-Token": _WEBHOOK_SECRET},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert len(fake_supabase.auth.admin.create_user_calls) == 1
    assert fake_telegram.sent == [(_CHAT_ID, fake_telegram.sent[0][1])]
    assert "linked" in fake_telegram.sent[0][1].lower()


def test_message_from_linked_user_skips_provisioning() -> None:
    fake_supabase = _FakeSupabaseClient(select_rows=[{"user_id": _EXISTING_USER_ID}])
    fake_telegram = _FakeTelegramClient()
    app.dependency_overrides[get_supabase] = lambda: fake_supabase
    app.dependency_overrides[get_telegram_client] = lambda: fake_telegram
    try:
        with TestClient(app) as client:
            response = client.post(
                "/telegram/webhook",
                json=_message_update("hi again"),
                headers={"X-Telegram-Bot-Api-Secret-Token": _WEBHOOK_SECRET},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert fake_supabase.auth.admin.create_user_calls == []
    assert len(fake_telegram.sent) == 1


def test_non_message_update_ignored() -> None:
    fake_supabase = _FakeSupabaseClient(select_rows=[])
    fake_telegram = _FakeTelegramClient()
    app.dependency_overrides[get_supabase] = lambda: fake_supabase
    app.dependency_overrides[get_telegram_client] = lambda: fake_telegram
    try:
        with TestClient(app) as client:
            response = client.post(
                "/telegram/webhook",
                json={"update_id": 2, "edited_message": {"text": "oops"}},
                headers={"X-Telegram-Bot-Api-Secret-Token": _WEBHOOK_SECRET},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
    assert fake_supabase.auth.admin.create_user_calls == []
    assert fake_telegram.sent == []
