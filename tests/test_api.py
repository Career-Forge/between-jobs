"""Tests for the FastAPI spine skeleton: /health and that a protected route
refuses an unauthenticated caller.

Real, non-mocked JWT verification (the part that actually matters to get
right) is tested separately in test_auth.py.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from between_jobs.api.app import app


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Lifespan runs under TestClient regardless of dependency_overrides --
    # these just need to be non-empty so lifespan doesn't raise; none of
    # the clients/secrets built from them are actually used in these tests.
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    # tests/conftest.py switches every worker and the dependency probes off.
    assert set(body["workers"]) == {
        "outbox",
        "job_registry_poller",
        "saved_search_matcher",
        "gmail_reply_checker",
        "hiring_signal_cache_purge",
    }
    assert {w["status"] for w in body["workers"].values()} == {"disabled"}
    assert set(body["dependencies"].values()) == {"not_checked"}
    # This test configures a Telegram bot, and conftest switches the cache-purge worker (which
    # hosts the webhook probe) off: the block says the probe is not running, not "not yet".
    assert body["telegram_webhook"] == {
        "status": "unknown",
        "last_checked_at": None,
        "reasons": ["probe_not_running"],
    }


def test_an_authenticated_route_without_an_auth_header_returns_401() -> None:
    # No dependency override for require_user_id here -- this exercises
    # the real header-parsing path, proving the endpoint is actually
    # protected rather than just trusting an override to exist.
    with TestClient(app) as client:
        response = client.get("/capabilities")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"
