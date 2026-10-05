"""Tests for `POST /account/delete` (launch plan P4.5). The deletion itself is covered in
`test_account_deletion.py` and, against a real stack, in
`tests/integration/test_local_account_deletion.py`; this is the HTTP edge: who may call it, the
typed confirmation, the token it hands on, and how a blocked deletion is answered."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from between_jobs.api import account_routes
from between_jobs.api.account_deletion import AccountDeletionBlocked, DeletionReport
from between_jobs.api.app import app
from between_jobs.api.app_state import get_http_client, get_supabase
from between_jobs.api.auth import require_user_id

_USER = "00000000-0000-0000-0000-0000000000a1"
_REPORT = DeletionReport(None, 0, 0, 0, True, 0, {})


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    recorded: list[dict[str, Any]] = []

    async def fake_delete(supabase: Any, http: Any, user_id: str, **kwargs: Any) -> DeletionReport:
        recorded.append({"user_id": user_id, **kwargs})
        return _REPORT

    monkeypatch.setattr(account_routes, "delete_account", fake_delete)
    app.dependency_overrides[require_user_id] = lambda: _USER
    app.dependency_overrides[get_supabase] = lambda: object()
    app.dependency_overrides[get_http_client] = lambda: object()
    try:
        yield recorded
    finally:
        app.dependency_overrides.clear()


def _post(body: dict[str, Any] | None, **headers: str) -> Any:
    with TestClient(app) as client:
        return client.post("/account/delete", json=body, headers=headers)


def test_the_typed_phrase_deletes_the_account_and_hands_on_the_bearer_token(
    calls: list[dict[str, Any]],
) -> None:
    response = _post({"confirm": "delete my account"}, Authorization="Bearer the-jwt")

    assert response.status_code == 204
    assert calls == [{"user_id": _USER, "access_token": "the-jwt"}]


def test_the_phrase_is_forgiving_about_case_and_spaces(calls: list[dict[str, Any]]) -> None:
    assert _post({"confirm": "  Delete   MY account "}).status_code == 204
    assert len(calls) == 1


@pytest.mark.parametrize("typed", ["", "delete", "delete my account please", "yes", "DELETE"])
def test_anything_else_is_refused_and_nothing_is_deleted(
    calls: list[dict[str, Any]], typed: str
) -> None:
    response = _post({"confirm": typed})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"
    assert calls == []


@pytest.mark.parametrize("body", [None, {}, {"confirm": None}, {"confirm": "x" * 101}])
def test_a_missing_or_malformed_body_is_refused(
    calls: list[dict[str, Any]], body: dict[str, Any] | None
) -> None:
    assert _post(body).status_code == 422
    assert calls == []


def test_a_request_without_a_signed_in_user_is_refused_and_nothing_is_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[bool] = []

    async def fake_delete(*_: Any, **__: Any) -> DeletionReport:
        called.append(True)
        return _REPORT

    monkeypatch.setattr(account_routes, "delete_account", fake_delete)
    app.dependency_overrides.pop(require_user_id, None)

    response = _post({"confirm": "delete my account"})

    assert response.status_code == 401
    assert called == []


def test_a_blocked_deletion_is_a_retryable_503_that_says_nothing_was_deleted(
    monkeypatch: pytest.MonkeyPatch, calls: list[dict[str, Any]]
) -> None:
    async def blocked(*_: Any, **__: Any) -> DeletionReport:
        raise AccountDeletionBlocked("a stored file could not be removed")

    monkeypatch.setattr(account_routes, "delete_account", blocked)

    response = _post({"confirm": "delete my account"})

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "PROVIDER_UNAVAILABLE"
    assert error["retryable"] is True
    assert "nothing was deleted" in error["message"]


def test_a_malformed_authorization_header_hands_on_no_token(calls: list[dict[str, Any]]) -> None:
    _post({"confirm": "delete my account"}, Authorization="Basic abc")

    assert calls == [{"user_id": _USER, "access_token": None}]
