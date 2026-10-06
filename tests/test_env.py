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
from between_jobs.api.env import ConfigurationError, optional_uuid_set, refuse, require_env

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


# -- a list of user ids ---------------------------------------------------------------------

_ID_A = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
_ID_B = "00000000-0000-0000-0000-000000000002"


@pytest.mark.parametrize("value", [None, "", "   ", "\t"])
def test_an_unset_or_blank_id_list_is_no_list(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("SOME_ID_LIST", raising=False)
    else:
        monkeypatch.setenv("SOME_ID_LIST", value)
    assert optional_uuid_set("SOME_ID_LIST") is None


def test_an_id_list_is_split_on_commas_trimmed_and_lower_cased(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOME_ID_LIST", f"  {_ID_A.upper()} ,{_ID_B}  ")
    assert optional_uuid_set("SOME_ID_LIST") == frozenset({_ID_A, _ID_B})
    monkeypatch.setenv("SOME_ID_LIST", _ID_A)
    assert optional_uuid_set("SOME_ID_LIST") == frozenset({_ID_A})


@pytest.mark.parametrize(
    "value",
    [
        "not-a-uuid",
        f"{_ID_A},oops",
        f"{_ID_A},,{_ID_B}",  # an empty entry
        f"{_ID_A},",  # a trailing comma
        ",",  # nothing at all, but not blank
        f"urn:uuid:{_ID_A}",  # a spelling Postgres rejects
        f"{{{_ID_A}}}",
        _ID_A.replace("-", ""),
        f"{_ID_A} {_ID_B}",  # two ids with no comma
        "\uff10" * 8 + "-0000-0000-0000-000000000000",  # full-width digits
        # A canonical UUID is 8-4-4-4-12 hex digits, and a typo that keeps it nearly right (a
        # truncated id is the likeliest) would match nobody and look like a working list.
        _ID_A[:-1],  # last group one short: 35 characters
        _ID_A + "0",  # last group one long: 37 characters
        _ID_A[1:],  # first group of 7
        "0" + _ID_A,  # first group of 9
        _ID_A.replace("-4e5f-", "-4e5-"),  # second group of 3
        _ID_A.replace("-4a6b-", "-4a6-"),  # third group of 3
        _ID_A.replace("-4a6b-", "-4a6bc-"),  # third group of 5
        _ID_A.replace("-8c7d-", "-8c7-"),  # fourth group of 3
        _ID_A.replace("0a1b2c3d", "0a1b2c3g"),  # a letter that is not a hex digit
        "0a1b2c3d4-e5f-4a6b-8c7d-9e0f1a2b3c4d",  # a hyphen one place late
        # full-width digits are not hex digits anywhere in the id, not just in the first group
        _ID_A.replace("4e5f", "\uff14\uff10\uff10\uff10"),
        _ID_A.replace("4a6b", "\uff14\uff10\uff10\uff10"),
        _ID_A.replace("8c7d", "\uff18\uff10\uff10\uff10"),
        _ID_A[:24] + "\uff10" * 12,
    ],
)
def test_an_id_list_with_a_bad_entry_stops_the_boot_naming_the_setting_never_the_value(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, value: str
) -> None:
    monkeypatch.setenv("SOME_ID_LIST", value)
    with caplog.at_level(logging.CRITICAL, logger=_LOGGER), pytest.raises(ConfigurationError):
        optional_uuid_set("SOME_ID_LIST")

    [message] = _critical(caplog)
    assert message.startswith("the API cannot start: SOME_ID_LIST must be")
    assert len(value) < 4 or value.strip() not in message  # a lone "," is in the wording


def test_booting_with_a_bad_hiring_signals_list_names_the_setting_and_not_the_value(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("HIRING_SIGNALS_ALLOWED_USER_IDS", f"{_ID_A},a-person-not-an-id")
    with (
        caplog.at_level(logging.CRITICAL, logger=_LOGGER),
        pytest.raises(ConfigurationError),
        TestClient(app),
    ):
        pass

    [message] = _critical(caplog)
    assert "HIRING_SIGNALS_ALLOWED_USER_IDS" in message
    assert "a-person-not-an-id" not in message and _ID_A not in message
