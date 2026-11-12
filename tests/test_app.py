"""App-level wiring tests -- CORS specifically (browser-extension.md E2).

The browser extension calls this API directly from a chrome-extension://
origin, unlike the web frontend (whose requests never trigger a real CORS
check at all, since Vite's dev proxy makes them same-origin). This is the
one thing that would otherwise only surface as a silent, hard-to-diagnose
failure in a real browser -- a plain pytest client doesn't enforce CORS at
all, so this test exists specifically to catch a regression here.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from between_jobs.api.app import app


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")


_EXTENSION = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"


def test_cors_preflight_allows_a_chrome_extension_origin_that_is_listed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", f"https://app.example.com,{_EXTENSION}")
    with TestClient(app) as client:
        response = client.options(
            "/extension/lookup",
            headers={
                "Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == _EXTENSION
    assert "GET" in response.headers["access-control-allow-methods"]


def test_cors_preflight_allows_a_chrome_extension_origin_when_no_list_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset, any origin may read a response, as before the setting existed."""
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    with TestClient(app) as client:
        response = client.options(
            "/extension/lookup",
            headers={
                "Origin": _EXTENSION,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_cors_does_not_allow_credentialed_requests() -> None:
    """This API never uses cookies (auth.py verifies a Bearer JWT) -- CORS
    credential support must stay off, since combining `allow_origins=["*"]`
    with `allow_credentials=True` would be a real cross-origin data-leak
    risk for any route that did rely on a cookie."""
    with TestClient(app) as client:
        response = client.options(
            "/extension/lookup",
            headers={
                "Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert "access-control-allow-credentials" not in response.headers
