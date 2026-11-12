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
    cors_allowed_origins,
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


# -- CORS_ALLOWED_ORIGINS: the websites that may read the API's responses --------------------------

_EXTENSION_ID = "abcdefghijklmnopabcdefghijklmnop"


@pytest.mark.parametrize("unset", [None, "", "   "])
def test_an_unset_or_blank_cors_list_is_none(
    monkeypatch: pytest.MonkeyPatch, unset: str | None
) -> None:
    if unset is None:
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", unset)

    assert cors_allowed_origins() is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://between-jobs.tech", {"https://between-jobs.tech"}),
        # the way a browser sends it: lower case, no default port
        ("HTTPS://Between-Jobs.TECH", {"https://between-jobs.tech"}),
        ("https://between-jobs.tech:443", {"https://between-jobs.tech"}),
        ("https://app.example.com:8443", {"https://app.example.com:8443"}),
        (" https://a.example , https://b.example ", {"https://a.example", "https://b.example"}),
        ("https://a.example,https://a.example", {"https://a.example"}),
        ("https://[2001:db8::1]:8443", {"https://[2001:db8::1]:8443"}),
        # http is for a developer's own machine only
        ("http://localhost:5173", {"http://localhost:5173"}),
        ("http://localhost", {"http://localhost"}),
        ("http://localhost:80", {"http://localhost"}),
        ("http://127.0.0.1:8012", {"http://127.0.0.1:8012"}),
        ("http://[::1]:5173", {"http://[::1]:5173"}),
        (f"chrome-extension://{_EXTENSION_ID}", {f"chrome-extension://{_EXTENSION_ID}"}),
        # what a browser accepts and sends as written: underscores, edge hyphens, one trailing dot
        ("https://a_b.example", {"https://a_b.example"}),
        ("https://-a.example", {"https://-a.example"}),
        ("https://a-.example", {"https://a-.example"}),
        ("https://example.com.", {"https://example.com."}),
        # a number is a host name unless it is the LAST label
        ("https://1.example", {"https://1.example"}),
        ("https://3.4.example", {"https://3.4.example"}),
        # the limits themselves: ports 1 and 65535, a 63-character label, a 253-character host
        ("https://a.example:1", {"https://a.example:1"}),
        ("https://a.example:65535", {"https://a.example:65535"}),
        ("https://" + "a" * 63 + ".example", {"https://" + "a" * 63 + ".example"}),
        (
            "https://" + ".".join(["a" * 63] * 3 + ["a" * 61]),
            {"https://" + ".".join(["a" * 63] * 3 + ["a" * 61])},
        ),
        # the case a browser never sends is written the way it does
        ("HTTP://LOCALHOST:5173", {"http://localhost:5173"}),
        ("https://[2001:DB8::1]:8443", {"https://[2001:db8::1]:8443"}),
        # an IPv4-mapped IPv6 host, as a browser writes it (the same on every Python version)
        ("https://[::ffff:102:304]", {"https://[::ffff:102:304]"}),
        ("https://[::]", {"https://[::]"}),
        # an IPv6 host as a browser writes it: the first longest run of zero groups is "::", a
        # single zero group is written out, and nothing is padded
        ("https://[fe80::1]", {"https://[fe80::1]"}),
        ("https://[1::2:0:0:3:4]", {"https://[1::2:0:0:3:4]"}),
        ("https://[1:0:0:2::34]", {"https://[1:0:0:2::34]"}),
        ("https://[1:0:2:3:4:5:6:7]", {"https://[1:0:2:3:4:5:6:7]"}),
        ("https://[1:2:3:4::5:6]", {"https://[1:2:3:4::5:6]"}),
        (
            "https://[2001:DB8:85A3:8D3:1319:8A2E:370:7344]",
            {"https://[2001:db8:85a3:8d3:1319:8a2e:370:7344]"},
        ),
        # a last label that only starts like a number, a trailing dot, a leading-zero port
        ("https://example.1a", {"https://example.1a"}),
        ("https://example.0xg", {"https://example.0xg"}),
        ("https://a.", {"https://a."}),
        (
            "https://" + ".".join(["a" * 63] * 3 + ["a" * 61]) + ".",
            {"https://" + ".".join(["a" * 63] * 3 + ["a" * 61]) + "."},
        ),
        ("https://a.example:08443", {"https://a.example:8443"}),
        (
            f"https://between-jobs.tech,chrome-extension://{_EXTENSION_ID}",
            {"https://between-jobs.tech", f"chrome-extension://{_EXTENSION_ID}"},
        ),
    ],
)
def test_a_good_cors_list_is_read_as_the_origins_a_browser_sends(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: set[str]
) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", raw)

    assert cors_allowed_origins() == frozenset(expected)


@pytest.mark.parametrize(
    "bad",
    [
        "*",  # a wildcard is the setting being unset, written down: leave it unset
        "https://*.between-jobs.tech",
        "https://between-jobs.tech,*",
        "between-jobs.tech",  # no scheme
        "//between-jobs.tech",
        "https:between-jobs.tech",
        "https://",
        "https://between-jobs.tech/",  # a path, even the empty one: no browser sends it
        "https://between-jobs.tech/app",
        "https://between-jobs.tech?x=1",
        "https://between-jobs.tech#top",
        "https://user@between-jobs.tech",
        "https://user:pass@between-jobs.tech",
        "http://between-jobs.tech",  # http for anything but localhost
        "http://192.168.0.10:5173",
        "http://localhost.evil.example",
        "ftp://between-jobs.tech",
        "javascript:alert(1)",
        "https://between-jobs.tech:notaport",
        "https://between-jobs.tech:99999",
        "https://a.example,,https://b.example",  # an empty entry
        "https://a.example,",
        ",https://a.example",
        "https://a b.example",  # whitespace inside
        "https://between-jobs.tech\\app",
        "https://b\u00fccher.test",  # an internationalised host must be written as xn--...
        "https://between-jobs.tech\u200b",  # a zero-width space pasted from a chat
        "https://between-jobs.tech\x01",
        f"chrome-extension://{_EXTENSION_ID}/",  # a path
        f"chrome-extension://{_EXTENSION_ID.upper()}",  # ids are lower case
        "chrome-extension://abcdefghijklmnopabcdefghijklmno",  # 31 letters
        f"chrome-extension://{_EXTENSION_ID}p",  # 33 letters
        "chrome-extension://abcdefghijklmnopabcdefghijklmnoz",  # a letter outside a-p
        "chrome-extension://*",
        "moz-extension://abcdefgh-1234",  # another browser's extension scheme
        # a host a browser would never send, or would read as something else
        'https://between-jobs.tech"',  # a quote from a copied value
        "https://%41.example",
        "https://a..example",  # an empty label
        "https://.example",
        "https://" + "a" * 64 + ".example",  # a label over 63 characters
        "https://" + ".".join(["abcdefghi"] * 30),  # a host over 253 characters
        "https://127.1",  # a browser reads these as 127.0.0.1
        "https://0x7f.1",
        "https://2130706433",
        "https://1.2.3",
        "https://1.2.3.4.5",
        "https://256.1.1.1",
        "https://01.1.1.1",
        # a letter that only looks like an ASCII one: IGNORECASE alone would read the Kelvin sign
        # as k, the long s as s and the dotless i as i, and the boot would rewrite the entry
        "https://\u212a.example",
        "https://\u017f.example",
        "https://\u0131.example",
        # http for anything but localhost, in any case
        "HTTP://between-jobs.tech",
        "Http://evil.example:8080",
        "HTTP://192.168.0.10",
        # a number as the last label is an IPv4 address to a browser, in any case or base
        "https://a.1",
        "https://example.0x",
        "https://a.0xFF",
        "https://a.0XFF",
        "https://1.0X7F",
        "https://0x",
        "https://1.2.3.4.",  # a browser drops the trailing dot of an address
        "https://" + ".".join(["a" * 63] * 3 + ["a" * 62]),  # 254 characters, one too many
        "https://a.example:" + "9" * 5000,  # a port that is not a port, not a crash
        f"chrome-extension://{_EXTENSION_ID[:-1]}q",  # an id uses the letters a to p
        # an IPv6 address that is not the text a browser writes for it, or not an address at all
        "https://[1:0:0:2::3:4]",  # the FIRST longest run is the one written as ::
        "https://[1::2:3:4:5:6:7]",  # a single zero group is not compressed
        "https://[1:2:3]",
        "https://[12345::1]",
        "https://[:::]",
        # one dot too many; http on a host that only resembles a loopback one
        "https://example.com..",
        "http://127.0.0.2",
        "http://[::]",
        "http://app.localhost",
        "http://notlocalhost",
        # a scheme that only starts like http(s), and entries joined without a comma
        "http\u017f://between-jobs.tech",
        "httpss://a.example",
        "https://a.example;https://b.example",
        "https://a.example:000443",  # a port is at most five characters
        "https://[2001:db8:0:0:0:0:0:1]",  # an IPv6 address must be in its compressed form
        "https://[2001:0db8::1]",
        "https://[fe80::1%25eth0]",  # a zone id
        "https://[v1.x]",
        "https://[::ffff:1.2.3.4]",
        "https://[]",
        "https://a.example:",  # an empty port
        "https://a.example:0",
        "https://a.example:65536",
        "https://a.example:00443x",
        "http://[2001:db8::1]",  # http for loopback only, IPv6 included
    ],
)
def test_a_bad_cors_list_stops_the_boot_and_names_the_setting_not_the_value(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, bad: str
) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", bad)
    with caplog.at_level(logging.CRITICAL, logger=_LOGGER), pytest.raises(ConfigurationError):
        cors_allowed_origins()
    [message] = _critical(caplog)
    caplog.clear()

    # The message is the same fixed text whatever the value is, so no value can be in it (a value
    # can carry credentials). Compared with the text for another bad value, not searched for the
    # value, because a short bad value ("https://") is also part of the fixed explanation.
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "*")
    with caplog.at_level(logging.CRITICAL, logger=_LOGGER), pytest.raises(ConfigurationError):
        cors_allowed_origins()
    [reference] = _critical(caplog)
    assert "CORS_ALLOWED_ORIGINS" in message
    assert message == reference


def test_one_bad_entry_among_good_ones_is_refused_not_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo must not quietly turn a list into a shorter one."""
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://between-jobs.tech,https://typo.example/")
    with pytest.raises(ConfigurationError):
        cors_allowed_origins()


def test_booting_with_a_bad_cors_list_names_it_and_never_its_value(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://user:hunter2@between-jobs.tech")
    with (
        caplog.at_level(logging.CRITICAL, logger=_LOGGER),
        pytest.raises(ConfigurationError),
        TestClient(app),
    ):
        pass

    [message] = _critical(caplog)
    assert "CORS_ALLOWED_ORIGINS" in message
    assert "hunter2" not in message and "between-jobs.tech" not in message
