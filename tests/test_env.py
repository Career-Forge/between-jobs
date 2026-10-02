"""How the API refuses to start on a bad configuration (env.py).

uvicorn reports a lifespan that fails as ONE log record holding the whole traceback, and
the log formatter caps a record at 4,000 characters. The reason is the last line of
that traceback, so on a platform that shows only the logs it is the part that gets cut:
the operator sees "Traceback ..." and no hint which setting is wrong. `refuse()` logs
the reason as a short record of its own first. These pin that, at the helper and at
each place the app actually refuses to boot.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from between_jobs.api.app import app
from between_jobs.api.env import ConfigurationError, refuse, require_env

_LOGGER = "between_jobs.api.env"


def _critical(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL]


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)


def test_refuse_logs_the_reason_as_its_own_short_record_and_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.CRITICAL, logger=_LOGGER), pytest.raises(ConfigurationError):
        refuse("SOMETHING must be set")

    assert _critical(caplog) == ["the API cannot start: SOMETHING must be set"]


def test_a_configuration_error_is_still_a_runtime_error() -> None:
    """Callers and tests that catch RuntimeError keep working."""
    assert issubclass(ConfigurationError, RuntimeError)


def test_a_missing_required_variable_is_named_and_its_value_never_is(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("SOME_REQUIRED_SETTING", raising=False)
    with caplog.at_level(logging.CRITICAL, logger=_LOGGER), pytest.raises(ConfigurationError):
        require_env("SOME_REQUIRED_SETTING")

    assert _critical(caplog) == [
        "the API cannot start: missing required env var SOME_REQUIRED_SETTING"
    ]


def test_a_blank_required_variable_counts_as_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SOME_REQUIRED_SETTING", "")
    with caplog.at_level(logging.CRITICAL, logger=_LOGGER), pytest.raises(ConfigurationError):
        require_env("SOME_REQUIRED_SETTING")


def test_a_set_variable_is_returned_and_nothing_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SOME_REQUIRED_SETTING", "a-value")
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        assert require_env("SOME_REQUIRED_SETTING") == "a-value"
    assert caplog.records == []


# -- each place the app really refuses to boot --------------------------------------


@pytest.mark.parametrize("missing", ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"])
def test_booting_without_a_database_setting_names_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, missing: str
) -> None:
    monkeypatch.delenv(missing)
    with (
        caplog.at_level(logging.CRITICAL, logger=_LOGGER),
        pytest.raises(ConfigurationError, match=missing),
        TestClient(app),
    ):
        pass

    assert _critical(caplog) == [f"the API cannot start: missing required env var {missing}"]


def test_booting_with_a_bad_lease_switch_names_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("WORKER_LEASES", "false")
    with (
        caplog.at_level(logging.CRITICAL, logger=_LOGGER),
        pytest.raises(ConfigurationError),
        TestClient(app),
    ):
        pass

    assert _critical(caplog) == ["the API cannot start: WORKER_LEASES must be 'on' or 'off'"]


@pytest.mark.parametrize("only", ["TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET"])
def test_booting_half_configured_for_telegram_names_both_settings_and_neither_value(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, only: str
) -> None:
    monkeypatch.setenv(only, "a-value-that-must-not-be-logged")
    with (
        caplog.at_level(logging.CRITICAL, logger=_LOGGER),
        pytest.raises(ConfigurationError),
        TestClient(app),
    ):
        pass

    [message] = _critical(caplog)
    assert "TELEGRAM_BOT_TOKEN" in message and "TELEGRAM_WEBHOOK_SECRET" in message
    assert "a-value-that-must-not-be-logged" not in message
