"""Tests for the Telegram "Mark as applied" callback (Sprint 3.4d).

`_handle_callback`'s `_STAGE_PREFIX` branch calls the exact same
`applications_store.change_stage` the web's `POST /{id}/stage` route
calls (test_applications_routes.py covers that RPC's own behavior).
What's new here is the Telegram-side glue: does the callback data parse
into the right (application_id, new_status) pair, and does it turn a
success/ApplicationNotFound result into the right chat message.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from between_jobs.api.app import app
from between_jobs.api.app_state import get_supabase, get_telegram_client

_TELEGRAM_USER_ID = 987654321
_CHAT_ID = 987654321
_USER_ID = "00000000-0000-0000-0000-000000000001"
_APPLICATION_ID = "30000000-0000-0000-0000-000000000001"
_WEBHOOK_SECRET = "test-secret-not-real"
_RAISED_EXCEPTION_SQLSTATE = "P0001"
_INVALID_STATUS_SQLSTATE = "22023"


class _FakeChannelIdentitiesTable:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def select(self, *_: Any, **__: Any) -> _FakeChannelIdentitiesTable:
        return self

    def eq(self, *_: Any, **__: Any) -> _FakeChannelIdentitiesTable:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)

    def insert(self, _data: dict[str, Any]) -> _InsertBuilder:
        return _InsertBuilder([{}])


class _InsertBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeRpcBuilder:
    def __init__(self, data: Any, error: Exception | None) -> None:
        self._data = data
        self._error = error

    async def execute(self) -> SimpleNamespace:
        if self._error:
            raise self._error
        return SimpleNamespace(data=self._data)


class _FakeSupabaseClient:
    def __init__(
        self,
        *,
        channel_identities_rows: list[dict[str, Any]],
        rpc_data: Any = None,
        rpc_error: Exception | None = None,
    ) -> None:
        self.auth = SimpleNamespace(admin=SimpleNamespace())
        self.channel_identities = _FakeChannelIdentitiesTable(channel_identities_rows)
        self.rpc_data = rpc_data
        self.rpc_error = rpc_error
        self.rpc_calls: list[tuple[str, dict[str, Any]]] = []

    def table(self, name: str) -> Any:
        if name == "channel_identities":
            return self.channel_identities
        raise AssertionError(f"unexpected table: {name}")

    def rpc(self, fn: str, params: dict[str, Any]) -> _FakeRpcBuilder:
        self.rpc_calls.append((fn, params))
        if fn != "change_application_stage":
            raise AssertionError(f"unexpected rpc: {fn}")
        return _FakeRpcBuilder(self.rpc_data, self.rpc_error)


class _FakeTelegramClient:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, dict[str, Any] | None]] = []
        self.answered_callback_ids: list[str] = []

    async def send_message(
        self, chat_id: int, text: str, *, reply_markup: dict[str, Any] | None = None
    ) -> None:
        self.sent.append((chat_id, text, reply_markup))

    async def answer_callback_query(self, callback_query_id: str) -> None:
        self.answered_callback_ids.append(callback_query_id)


def _callback_update(data: str) -> dict[str, Any]:
    return {
        "update_id": 2,
        "callback_query": {
            "id": "cbq-1",
            "from": {"id": _TELEGRAM_USER_ID, "is_bot": False, "first_name": "Test"},
            "message": {"message_id": 2, "chat": {"id": _CHAT_ID, "type": "private"}},
            "data": data,
        },
    }


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", _WEBHOOK_SECRET)


def _post(
    supabase: _FakeSupabaseClient, telegram: _FakeTelegramClient, update: dict[str, Any]
) -> Any:
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[get_telegram_client] = lambda: telegram
    try:
        with TestClient(app) as client:
            return client.post(
                "/telegram/webhook",
                json=update,
                headers={"X-Telegram-Bot-Api-Secret-Token": _WEBHOOK_SECRET},
            )
    finally:
        app.dependency_overrides.clear()


def test_mark_applied_callback_changes_stage_and_confirms() -> None:
    supabase = _FakeSupabaseClient(
        channel_identities_rows=[{"user_id": _USER_ID}],
        rpc_data={"id": _APPLICATION_ID, "status": "applied"},
    )
    telegram = _FakeTelegramClient()

    response = _post(supabase, telegram, _callback_update(f"app:stage:{_APPLICATION_ID}:applied"))

    assert response.status_code == 200
    assert telegram.answered_callback_ids == ["cbq-1"]
    assert len(supabase.rpc_calls) == 1
    fn, params = supabase.rpc_calls[0]
    assert fn == "change_application_stage"
    assert params["p_application_id"] == _APPLICATION_ID
    assert params["p_new_status"] == "applied"
    assert params["p_user_id"] == _USER_ID
    assert "Marked as *applied*." in telegram.sent[0][1]


def test_mark_applied_callback_application_not_found_sends_honest_error() -> None:
    error = APIError(
        {
            "message": "application not found",
            "code": _RAISED_EXCEPTION_SQLSTATE,
            "details": None,
            "hint": None,
        }
    )
    supabase = _FakeSupabaseClient(channel_identities_rows=[{"user_id": _USER_ID}], rpc_error=error)
    telegram = _FakeTelegramClient()

    response = _post(supabase, telegram, _callback_update(f"app:stage:{_APPLICATION_ID}:applied"))

    assert response.status_code == 200
    assert telegram.answered_callback_ids == ["cbq-1"]
    assert "❌" in telegram.sent[0][1]


def test_stage_callback_with_forged_status_sends_honest_error() -> None:
    """K1 (applications-kanban.md D2) -- this is the ONE real, reachable
    caller of InvalidApplicationStatus: callback_data is parsed straight
    into `new_status` with no Pydantic Literal anywhere on this path, so a
    forged `app:stage:{id}:bogus` callback is the first real check it
    meets, at the change_application_stage RPC itself."""
    error = APIError(
        {
            "message": "invalid application status: bogus",
            "code": _INVALID_STATUS_SQLSTATE,
            "details": None,
            "hint": None,
        }
    )
    supabase = _FakeSupabaseClient(channel_identities_rows=[{"user_id": _USER_ID}], rpc_error=error)
    telegram = _FakeTelegramClient()

    response = _post(supabase, telegram, _callback_update(f"app:stage:{_APPLICATION_ID}:bogus"))

    assert response.status_code == 200
    assert telegram.answered_callback_ids == ["cbq-1"]
    assert "❌" in telegram.sent[0][1]
    assert "bogus" in telegram.sent[0][1]
