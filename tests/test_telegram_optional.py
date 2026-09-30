"""Telegram is optional (launch plan P2.4): a server started with neither
TELEGRAM_BOT_TOKEN nor TELEGRAM_WEBHOOK_SECRET runs web-only.

These boot the real app, lifespan included, the way every route test does
(conftest.py switches the background workers off). What's pinned:

- the boot itself: neither value, both, and the two half-configured cases;
- that "off" is off all the way down -- the webhook and the link-code route
  answer 404 FEATURE_DISABLED (never 401, never a comparison against a missing
  secret), and `GET /capabilities` says `telegram: false`;
- that "on" is unchanged -- same routes, `telegram: true`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from between_jobs.api.app import app
from between_jobs.api.auth import require_user_id

_USER_ID = "00000000-0000-0000-0000-000000000001"
_TOKEN = "test-token-not-real"
_SECRET = "test-webhook-secret-not-real"


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)


@contextmanager
def _client(*, signed_in: bool = True) -> Iterator[TestClient]:
    if signed_in:
        app.dependency_overrides[require_user_id] = lambda: _USER_ID
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", _TOKEN)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", _SECRET)


# -- booting ---------------------------------------------------------------


def test_the_app_boots_with_no_telegram_configuration() -> None:
    with _client() as client:
        assert client.get("/health").status_code == 200


def test_blank_values_count_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """`TELEGRAM_BOT_TOKEN=` in a .env file is how "not configured" usually looks."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "")
    with _client() as client:
        assert client.get("/capabilities").json() == {"telegram": False}


@pytest.mark.parametrize("only", ["TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET"])
def test_one_value_without_the_other_stops_the_boot(
    monkeypatch: pytest.MonkeyPatch, only: str
) -> None:
    """A bot that can't verify its webhook, or a secret with no bot behind it,
    is a mistake -- not "off". Names are in the message, values never."""
    monkeypatch.setenv(only, "a-value-that-must-not-be-echoed")
    with pytest.raises(RuntimeError) as raised, _client():
        pass
    message = str(raised.value)
    assert "TELEGRAM_BOT_TOKEN" in message and "TELEGRAM_WEBHOOK_SECRET" in message
    assert "a-value-that-must-not-be-echoed" not in message


def test_the_app_boots_with_both_values(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    with _client() as client:
        assert client.get("/health").status_code == 200


# -- off means off ---------------------------------------------------------


@pytest.mark.parametrize("sent_secret", [None, "", _SECRET, "anything"])
def test_the_webhook_is_a_404_when_telegram_is_off_whatever_secret_arrives(
    sent_secret: str | None,
) -> None:
    """Not a 401, and never a comparison against a secret that doesn't exist:
    an empty expected secret would accept an empty header."""
    headers = {} if sent_secret is None else {"X-Telegram-Bot-Api-Secret-Token": sent_secret}
    with _client(signed_in=False) as client:
        response = client.post("/telegram/webhook", json={"message": {}}, headers=headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FEATURE_DISABLED"


def test_a_link_code_cannot_be_minted_when_telegram_is_off() -> None:
    with _client() as client:
        response = client.post("/link/code", json={"channel": "telegram"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FEATURE_DISABLED"


def test_capabilities_says_telegram_is_off() -> None:
    with _client() as client:
        response = client.get("/capabilities")
    assert response.status_code == 200
    assert response.json() == {"telegram": False}


# -- on is unchanged --------------------------------------------------------


def test_capabilities_says_telegram_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    with _client() as client:
        assert client.get("/capabilities").json() == {"telegram": True}


def test_the_webhook_still_checks_its_secret_when_telegram_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    with _client(signed_in=False) as client:
        wrong = client.post(
            "/telegram/webhook",
            json={},
            headers={"X-Telegram-Bot-Api-Secret-Token": "not-the-secret"},
        )
        missing = client.post("/telegram/webhook", json={})
    assert wrong.status_code == 401
    assert missing.status_code == 401


# -- the gate on the capabilities route itself -------------------------------


def test_capabilities_requires_a_signed_in_user() -> None:
    with _client(signed_in=False) as client:
        assert client.get("/capabilities").status_code == 401
