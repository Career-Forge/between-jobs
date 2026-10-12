"""Tests for `POST /account/delete` (launch plan P4.5). The deletion itself is covered in
`test_account_deletion.py` and, against a real stack, in
`tests/integration/test_local_account_deletion.py`; this is the HTTP edge: who may call it, the
typed confirmation, the token it hands on, and how a blocked deletion is answered."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fake_sentry import FakeSentry
from fastapi.testclient import TestClient

from between_jobs.api import account_routes, error_reporting
from between_jobs.api.account_deletion import AccountDeletionBlocked, DeletionReport
from between_jobs.api.app import app
from between_jobs.api.app_state import get_http_client, get_supabase
from between_jobs.api.auth import require_user_id
from between_jobs.api.errors import ApiError

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


class _BrokenStorage:
    """A bucket whose listing fails the way an unreachable Storage does."""

    def from_(self, _bucket: str) -> _BrokenStorage:
        return self

    async def list(self, _prefix: str, _options: dict[str, int]) -> list[dict[str, Any]]:
        raise ConnectionError("storage is unreachable")


@pytest.fixture
def tracker(monkeypatch: pytest.MonkeyPatch) -> FakeSentry:
    """The error tracker switched on, over a fake SDK (the app's lifespan starts it)."""
    monkeypatch.setattr(error_reporting, "_sdk", None)
    monkeypatch.setattr(error_reporting, "_failure_warned", False)
    monkeypatch.setenv(
        "SENTRY_DSN", "https://0123456789abcdef0123456789abcdef@o123456.ingest.sentry.io/4501234567"
    )
    return FakeSentry().install(monkeypatch)


def test_our_own_storage_failing_is_reported_once_though_it_is_answered_as_unavailable(
    tracker: FakeSentry, caplog: pytest.LogCaptureFixture
) -> None:
    """The 503 is coded PROVIDER_UNAVAILABLE, which the tracker is told not to hear about (an
    upstream provider having an outage is not a bug here). This is our own bucket, so the failure
    behind it is reported by itself, and the log line says what it was caused by."""
    app.dependency_overrides[require_user_id] = lambda: _USER
    app.dependency_overrides[get_supabase] = lambda: SimpleNamespace(storage=_BrokenStorage())
    app.dependency_overrides[get_http_client] = lambda: object()
    try:
        with caplog.at_level(logging.DEBUG):
            response = _post({"confirm": "delete my account"})
    finally:
        app.dependency_overrides.clear()

    # the answer is unchanged
    assert response.status_code == 503
    error = response.json()["error"]
    assert (error["code"], error["retryable"]) == ("PROVIDER_UNAVAILABLE", True)
    assert "nothing was deleted" in error["message"]

    # exactly one report: the blocked deletion itself, with the real cause chained behind it
    [(reported, scope)] = tracker.captured
    assert isinstance(reported, AccountDeletionBlocked)
    assert not isinstance(reported, ApiError)
    assert isinstance(reported.__cause__, ConnectionError)
    assert scope == {
        "tags": {"task": "account_deletion"},
        "fingerprint": ["account_deletion_blocked"],
    }

    # and the log shows the failure, as an ERROR with its traceback
    [line] = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert line.exc_info is not None and isinstance(line.exc_info[1], AccountDeletionBlocked)
    assert line.ctx == {"task": "account_deletion"}  # type: ignore[attr-defined]


def test_a_provider_failure_is_still_not_reported_next_to_it(tracker: FakeSentry) -> None:
    """The rule that hides an upstream provider's failure is unchanged; only these two sites of
    ours stopped being hidden by it."""
    with TestClient(app):  # starts the tracker
        error_reporting.report_exception(ApiError("PROVIDER_UNAVAILABLE", "x", retryable=True))
        error_reporting.report_exception(ApiError("PROVIDER_REJECTED", "x"))
    assert tracker.captured == []


def test_a_malformed_authorization_header_hands_on_no_token(calls: list[dict[str, Any]]) -> None:
    _post({"confirm": "delete my account"}, Authorization="Basic abc")

    assert calls == [{"user_id": _USER, "access_token": None}]
