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
from between_jobs.api.env import (
    ConfigurationError,
    https_url_or_refuse,
    optional_uuid_set,
    refuse,
    require_env,
    web_app_url,
)

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


# -- WEB_APP_URL: the web app's public address ---------------------------------------------------


@pytest.mark.parametrize("unset", [None, "", "   "])
def test_an_unset_or_blank_web_app_url_is_none(
    monkeypatch: pytest.MonkeyPatch, unset: str | None
) -> None:
    if unset is None:
        monkeypatch.delenv("WEB_APP_URL", raising=False)
    else:
        monkeypatch.setenv("WEB_APP_URL", unset)

    assert web_app_url() is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://between-jobs.tech", "https://between-jobs.tech"),
        ("https://between-jobs.tech/", "https://between-jobs.tech"),
        ("  https://between-jobs.tech//  ", "https://between-jobs.tech"),
        ("HTTPS://Between-Jobs.tech", "HTTPS://Between-Jobs.tech"),
        ("https://app.example.com:8443", "https://app.example.com:8443"),
        ("https://example.com/app", "https://example.com/app"),
        ("https://example.com/app/", "https://example.com/app"),
        ("https://[::1]:8443", "https://[::1]:8443"),
        # an internationalised host and a non-ASCII path are not "invisible" characters
        ("https://b\u00fccher.test", "https://b\u00fccher.test"),
        ("https://between-jobs.tech/caf\u00e9", "https://between-jobs.tech/caf\u00e9"),
    ],
)
def test_a_good_web_app_url_is_used_without_its_trailing_slash(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: str
) -> None:
    monkeypatch.setenv("WEB_APP_URL", raw)

    assert web_app_url() == expected


@pytest.mark.parametrize(
    "bad",
    [
        "http://between-jobs.tech",  # not https
        "ftp://between-jobs.tech",
        "between-jobs.tech",  # no scheme
        "//between-jobs.tech",
        "https:between-jobs.tech",  # urlsplit reads this as a path
        "https:///profile",  # no host
        "https://",
        "https://user:pass@between-jobs.tech",  # credentials
        "https://user@between-jobs.tech",
        "https://:pass@between-jobs.tech",
        "https://between-jobs.tech?x=1",  # a query
        "https://between-jobs.tech/?",
        "https://between-jobs.tech#top",  # a fragment
        "https://between-jobs.tech/a b",  # whitespace inside
        "https://between-jobs.tech/a\\b",  # a backslash
        "https://between-jobs.tech/a\x01b",  # a control character that is not whitespace
        "https://between-jobs.tech/a\x1bb",  # ESC
        "https://betw\x01een-jobs.tech",  # ...in the host as well
        "https://between-jobs.tech/\x7f",  # DEL
        "https://between-jobs.tech/\u200babc",  # a zero-width space, as pasted from a chat
        "https://between-jobs.tech/\u202eabc",  # a right-to-left override
        "https://exa\u00admple.test",  # a soft hyphen inside the host
        "https://between-jobs.tech/\ufeffabc",  # a byte order mark
        "https://between-jobs.tech/\u200eabc",  # a left-to-right mark
        "https://between-jobs.tech/\u00a0abc",  # a no-break space
        "https://between-jobs.tech/\u2028abc",  # a line separator
        "https://between-jobs.tech/\ue000abc",  # a private-use character
        "https://between-jobs.tech/\u0085abc",  # a next-line control
        "https://between-jobs.tech:notaport",
        "https://between-jobs.tech:99999",
        "javascript:alert(1)",
    ],
)
def test_a_bad_web_app_url_stops_the_boot_and_names_the_setting_not_the_value(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, bad: str
) -> None:
    monkeypatch.setenv("WEB_APP_URL", bad)
    with caplog.at_level(logging.CRITICAL, logger=_LOGGER), pytest.raises(ConfigurationError):
        web_app_url()

    [message] = _critical(caplog)
    assert "WEB_APP_URL" in message
    assert bad.strip() not in message  # a value can carry credentials: never logged


def test_a_null_character_is_refused_too(caplog: pytest.LogCaptureFixture) -> None:
    """The environment cannot carry one (the OS refuses), so it is checked on the helper."""
    with caplog.at_level(logging.CRITICAL, logger=_LOGGER), pytest.raises(ConfigurationError):
        https_url_or_refuse("SOME_ADDRESS", "https://between-jobs.tech/a\x00b")

    assert "SOME_ADDRESS" in _critical(caplog)[0]


def test_the_check_is_the_same_one_whatever_reads_the_setting(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.CRITICAL, logger=_LOGGER), pytest.raises(ConfigurationError):
        https_url_or_refuse("SOME_ADDRESS", "http://example.com")

    assert "SOME_ADDRESS" in _critical(caplog)[0]


def test_booting_with_a_bad_web_app_url_names_it_and_never_its_value(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("WEB_APP_URL", "https://user:hunter2@between-jobs.tech")
    with (
        caplog.at_level(logging.CRITICAL, logger=_LOGGER),
        pytest.raises(ConfigurationError),
        TestClient(app),
    ):
        pass

    [message] = _critical(caplog)
    assert "WEB_APP_URL" in message
    assert "hunter2" not in message and "between-jobs.tech" not in message


def test_booting_with_a_good_web_app_url_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_APP_URL", "https://between-jobs.tech")
    with TestClient(app):
        pass
