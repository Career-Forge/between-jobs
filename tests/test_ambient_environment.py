"""The suite does not inherit the developer's Google settings.

The lifespan stops the API when a Google client id or secret is set without the rest of Gmail's
settings, and `app.py` calls `load_dotenv()` on import, so a checkout's `.env` (or a shell) that
holds one of them would stop nearly every route test at startup. `conftest.py` clears the three
before each test; this fails if it stops doing so.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from between_jobs.api.app import app
from between_jobs.api.gmail_client import GOOGLE_OAUTH_VARIABLES, gmail_enabled


def test_the_google_settings_of_the_machine_are_not_in_the_environment_of_a_test() -> None:
    assert [name for name in GOOGLE_OAUTH_VARIABLES if name in os.environ] == []
    assert gmail_enabled() is False


def test_a_route_test_boots_whatever_the_machine_has_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
