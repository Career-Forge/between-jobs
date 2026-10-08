"""What the request path reports (app.py), with a fake SDK: an unhandled exception and an
`ApiError` with a 5xx status are; an `ApiError` with a 4xx status is not; and a reporter
that fails changes nothing about the answer the caller gets."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fake_sentry import FakeSentry
from fastapi.testclient import TestClient

from between_jobs.api import channel_core, error_reporting
from between_jobs.api.app import app
from between_jobs.api.errors import ApiError, ErrorCode

DSN = "https://0123456789abcdef0123456789abcdef@o123456.ingest.sentry.io/4501234567"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> FakeSentry:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(error_reporting, "_sdk", None)
    monkeypatch.setattr(error_reporting, "_failure_warned", False)
    monkeypatch.setenv("SENTRY_DSN", DSN)
    return FakeSentry().install(monkeypatch)


@pytest.fixture
def sdk(_env: FakeSentry) -> FakeSentry:
    return _env


@contextmanager
def _route(path: str, endpoint: Any, *, methods: list[str] | None = None) -> Iterator[None]:
    app.add_api_route(path, endpoint, methods=methods or ["GET"])
    try:
        yield
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) != path]


def _raising(error: BaseException) -> Any:
    async def endpoint(thing_id: str) -> None:
        raise error

    return endpoint


def test_an_unhandled_exception_is_reported_once_with_route_method_and_request_id(
    sdk: FakeSentry,
) -> None:
    error = RuntimeError("something nobody handled")
    with (
        _route("/__test_things/{thing_id}", _raising(error)),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        response = client.get(
            "/__test_things/abc?token=sensitive&email=a@b.com",
            headers={"X-Request-ID": "req-sentry-unhandled-1"},
        )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.headers["x-request-id"] == "req-sentry-unhandled-1"
    [(reported, scope)] = sdk.captured
    assert reported is error
    assert scope == {
        "tags": {
            "route": "/__test_things/{thing_id}",
            "method": "GET",
            "request_id": "req-sentry-unhandled-1",
        }
    }
    assert "sensitive" not in repr(scope) and "a@b.com" not in repr(scope)


@pytest.mark.parametrize(
    "code",
    ["INTERNAL_ERROR", "RUN_FAILED", "PROVIDER_REJECTED", "PROVIDER_UNAVAILABLE"],
)
def test_an_api_error_with_a_server_status_is_reported(sdk: FakeSentry, code: ErrorCode) -> None:
    error = ApiError(code, "Something went wrong.")
    with _route("/__test_things/{thing_id}", _raising(error)), TestClient(app) as client:
        response = client.get("/__test_things/abc", headers={"X-Request-ID": "req-sentry-api-1"})

    assert response.status_code >= 500
    [(reported, scope)] = sdk.captured
    assert reported is error
    assert scope == {
        "tags": {
            "route": "/__test_things/{thing_id}",
            "method": "GET",
            "request_id": "req-sentry-api-1",
            "error_code": code,
        }
    }


@pytest.mark.parametrize(
    "code",
    [
        "AUTH_REQUIRED",
        "FORBIDDEN",
        "SETUP_REQUIRED",
        "INVALID_INPUT",
        "NOT_FOUND",
        "CONFLICT",
        "RATE_LIMITED",
        "PROVIDER_RATE_LIMITED",
        "PAYLOAD_TOO_LARGE",
        "ENROLLMENT_REQUIRED",
        "FEATURE_DISABLED",
    ],
)
def test_an_api_error_with_a_client_status_is_never_reported(
    sdk: FakeSentry, code: ErrorCode
) -> None:
    error = ApiError(code, "The caller did something the API refuses.")
    with _route("/__test_things/{thing_id}", _raising(error)), TestClient(app) as client:
        response = client.get("/__test_things/abc")

    assert response.status_code < 500
    assert sdk.captured == []


def test_a_validation_error_is_not_reported(sdk: FakeSentry) -> None:
    async def endpoint(count: int) -> None: ...

    with _route("/__test_validated", endpoint), TestClient(app) as client:
        response = client.get("/__test_validated?count=not-a-number")

    assert response.status_code == 422
    assert sdk.captured == []


@pytest.mark.parametrize(
    "error", [RuntimeError("x"), ApiError("INTERNAL_ERROR", "Something broke.")]
)
def test_a_reporter_that_fails_changes_nothing_about_the_answer(
    sdk: FakeSentry, caplog: pytest.LogCaptureFixture, error: BaseException
) -> None:
    sdk.capture_error = ConnectionError("sentry is down")
    with (
        caplog.at_level(logging.WARNING),
        _route("/__test_things/{thing_id}", _raising(error)),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        response = client.get("/__test_things/abc")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.headers["x-request-id"]


def test_with_reporting_off_the_request_path_never_touches_an_sdk(
    monkeypatch: pytest.MonkeyPatch, sdk: FakeSentry
) -> None:
    monkeypatch.delenv("SENTRY_DSN")
    with (
        _route("/__test_things/{thing_id}", _raising(RuntimeError("x"))),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        assert client.get("/__test_things/abc").status_code == 500
    assert sdk.init_calls == [] and sdk.captured == []


# --- the chat boundary answers in a message, so it reports for itself ------------------------


def _turn() -> Any:
    return SimpleNamespace(message=SimpleNamespace(update_id="u-1", channel="telegram"))


@pytest.mark.parametrize("code", ["INTERNAL_ERROR", "RUN_FAILED", "PROVIDER_UNAVAILABLE"])
def test_a_chat_flow_that_ends_in_a_server_side_api_error_reports_it(
    sdk: FakeSentry, code: ErrorCode
) -> None:
    error_reporting.init_error_reporting()
    error = ApiError(code, "Something went wrong.")

    channel_core._log_known_failure(_turn(), error)

    assert sdk.captured == [
        (error, {"tags": {"task": "prepare", "channel": "telegram", "error_code": code}})
    ]


@pytest.mark.parametrize(
    "code", ["SETUP_REQUIRED", "INVALID_INPUT", "PROVIDER_RATE_LIMITED", "NOT_FOUND"]
)
def test_a_chat_flow_that_ends_in_a_client_side_api_error_does_not(
    sdk: FakeSentry, code: ErrorCode
) -> None:
    error_reporting.init_error_reporting()
    channel_core._log_known_failure(_turn(), ApiError(code, "Set this up first."))
    assert sdk.captured == []
