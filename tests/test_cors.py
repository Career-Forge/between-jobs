"""CORS, as the deployed web app needs it (launch plan P2.3): the bundle is
served from one origin and calls the API on another, so every response a
browser is meant to read -- including the error envelope for a failure nothing
handled -- must carry the CORS headers.

Locking the allowed origins down is a separate, later task (P5.6); this only
pins that the headers reach every kind of response."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from between_jobs.api.app import app

_ORIGIN = "https://app.example.com"
_BOOM = "/__test_unhandled_error"


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)


@contextmanager
def _client_with_a_route_that_raises() -> Iterator[TestClient]:
    async def boom() -> None:
        raise RuntimeError("nothing handles this")

    app.add_api_route(_BOOM, boom)
    try:
        # The real server turns an unhandled error into a 500 response; the test
        # client would otherwise re-raise it.
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) != _BOOM]


def test_an_unhandled_error_still_carries_the_cors_headers() -> None:
    """request_id_middleware answers an unhandled error with its own 500. If
    CORS sat inside it, that response would lack the headers and a browser
    would hand the page a network error instead of the envelope."""
    with _client_with_a_route_that_raises() as client:
        response = client.get(_BOOM, headers={"Origin": _ORIGIN})

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.headers["access-control-allow-origin"] == "*"
    assert "x-request-id" in response.headers
    # ...and the page is allowed to read that id, to quote in a bug report.
    assert "x-request-id" in response.headers["access-control-expose-headers"].lower()


def test_a_handled_error_carries_the_cors_headers() -> None:
    with TestClient(app) as client:
        response = client.get("/no-such-route", headers={"Origin": _ORIGIN})

    assert response.status_code == 404
    assert response.headers["access-control-allow-origin"] == "*"


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE", "PUT"])
def test_the_preflight_the_web_app_sends_is_allowed(method: str) -> None:
    """apiFetch sends Authorization and Content-Type on every call, which makes
    the browser preflight it; the answer has to name both headers and the
    method (Access-Control-Allow-Headers: * does not cover Authorization, so
    it matters that the middleware echoes the requested list)."""
    with TestClient(app) as client:
        response = client.options(
            "/link/code",
            headers={
                "Origin": _ORIGIN,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed_headers and "content-type" in allowed_headers
    assert method in response.headers["access-control-allow-methods"]
