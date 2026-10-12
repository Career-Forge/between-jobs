"""Gmail is optional, like Telegram: a server started with none of the three GOOGLE_OAUTH_*
settings runs without it.

These boot the real app, lifespan included, the way every route test does (conftest.py switches
the background workers off). What is pinned:

- the boot itself: none, all three, blank values, and every half-set combination that has a
  client id or a client secret in it (which stops the boot, naming the variables that are not set
  and never a value). A redirect URI on its own does not: it is what a .env copied from an
  earlier .env.example holds, and Gmail is simply off;
- that "off" is off all the way down -- no CRITICAL "the API cannot start" line from a request
  or a worker tick, no HTML 500, the routes answer 404 FEATURE_DISABLED, and
  `GET /capabilities` leaves `gmail` out (and says `gmail: true` when it is on);
- that "on" is unchanged.

The routes' own behaviour is in test_gmail_oauth_routes.py, the worker's in
test_gmail_reply_checker.py, pushing a draft in test_contact_research_routes.py.
"""

from __future__ import annotations

import io
import itertools
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from between_jobs.api.app import app
from between_jobs.api.auth import require_user_id
from between_jobs.api.errors import ApiError
from between_jobs.api.gmail_client import (
    GMAIL_DISABLED_MESSAGE,
    GOOGLE_OAUTH_VARIABLES,
    check_gmail_configuration,
    gmail_enabled,
    gmail_oauth_config,
    require_gmail_oauth_config,
)

_USER_ID = "00000000-0000-0000-0000-000000000001"
_ID = "client-id-value-that-must-not-be-echoed"
_SECRET = "client-secret-value-that-must-not-be-echoed"
_REDIRECT = "https://api.example.test/oauth/gmail/callback"
_VALUES = {
    "GOOGLE_OAUTH_CLIENT_ID": _ID,
    "GOOGLE_OAUTH_CLIENT_SECRET": _SECRET,
    "GOOGLE_OAUTH_REDIRECT_URI": _REDIRECT,
}


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    for name in GOOGLE_OAUTH_VARIABLES:
        monkeypatch.delenv(name, raising=False)


@contextmanager
def _client() -> Iterator[TestClient]:
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _set(monkeypatch: pytest.MonkeyPatch, names: tuple[str, ...]) -> None:
    for name in names:
        monkeypatch.setenv(name, _VALUES[name])


def test_the_three_settings_are_the_three_the_env_example_documents() -> None:
    assert tuple(_VALUES) == GOOGLE_OAUTH_VARIABLES
    example = (Path(__file__).parent.parent / ".env.example").read_text()
    documented = set(re.findall(r"(?m)^#?\s*([A-Z][A-Z0-9_]{2,})=", example))
    assert set(GOOGLE_OAUTH_VARIABLES) <= documented


def _apply_dotenv(monkeypatch: pytest.MonkeyPatch, text: str) -> dict[str, str]:
    """What `load_dotenv` would put in the environment for `text` (a blank value is a blank
    string, as it is there), restricted to the Google settings."""
    values = {
        name: value or ""
        for name, value in dotenv_values(stream=io.StringIO(text)).items()
        if name in GOOGLE_OAUTH_VARIABLES
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


def test_copying_the_env_example_as_it_is_leaves_gmail_off_and_the_boot_working(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An uncommented line in the example would set one of the three."""
    example = (Path(__file__).parent.parent / ".env.example").read_text()
    uncommented = {
        name: value
        for name, value in re.findall(r"(?m)^([A-Z][A-Z0-9_]{2,})=(.*)$", example)
        if name in GOOGLE_OAUTH_VARIABLES
    }
    assert not any(value.strip() for value in uncommented.values())

    _apply_dotenv(monkeypatch, example)
    assert check_gmail_configuration() is False
    with _client() as client:
        assert client.get("/health").status_code == 200


# What the Google block of .env.example held before Gmail became optional: the two credentials
# blank, and the redirect address filled in. The README says to `cp .env.example .env` and fill
# in only the Supabase values, so installs made that way still have exactly this.
_EARLIER_EXAMPLE_GOOGLE_BLOCK = """\
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=

# Must exactly match one of the "Authorized redirect URIs" registered on that same OAuth
# client.
GOOGLE_OAUTH_REDIRECT_URI=http://localhost:8012/oauth/gmail/callback
"""


def test_a_dot_env_copied_from_the_earlier_example_still_boots_with_gmail_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blank id, blank secret, redirect set: one of the three, and it must not stop the boot of
    someone who never wanted Gmail."""
    applied = _apply_dotenv(monkeypatch, _EARLIER_EXAMPLE_GOOGLE_BLOCK)
    assert applied == {
        "GOOGLE_OAUTH_CLIENT_ID": "",
        "GOOGLE_OAUTH_CLIENT_SECRET": "",
        "GOOGLE_OAUTH_REDIRECT_URI": "http://localhost:8012/oauth/gmail/callback",
    }

    assert check_gmail_configuration() is False
    with _client() as client:
        assert client.get("/health").status_code == 200
        assert "gmail" not in client.get("/capabilities").json()


# -- the settings ---------------------------------------------------------------------------


def test_with_none_set_gmail_is_off() -> None:
    assert gmail_oauth_config() is None
    assert gmail_enabled() is False
    assert check_gmail_configuration() is False


def test_with_all_three_set_gmail_is_on_and_the_values_come_back_as_they_are(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set(monkeypatch, GOOGLE_OAUTH_VARIABLES)

    assert gmail_oauth_config() == {
        "client_id": _ID,
        "client_secret": _SECRET,
        "redirect_uri": _REDIRECT,
    }
    assert gmail_enabled() is True
    assert check_gmail_configuration() is True
    assert require_gmail_oauth_config() == gmail_oauth_config()


@pytest.mark.parametrize("name", GOOGLE_OAUTH_VARIABLES)
def test_a_blank_value_reads_as_not_set(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """`GOOGLE_OAUTH_CLIENT_ID=` in a .env file is how "not configured" usually looks."""
    _set(monkeypatch, GOOGLE_OAUTH_VARIABLES)
    monkeypatch.setenv(name, "")
    assert gmail_oauth_config() is None


def test_all_blank_is_off_and_boots(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in GOOGLE_OAUTH_VARIABLES:
        monkeypatch.setenv(name, "")
    assert check_gmail_configuration() is False


def test_requiring_gmail_on_a_server_without_it_is_a_404_in_the_style_of_the_telegram_routes() -> (
    None
):
    with pytest.raises(ApiError) as raised:
        require_gmail_oauth_config()

    error = raised.value
    assert (error.code, error.status_code) == ("FEATURE_DISABLED", 404)
    assert error.message == "Gmail isn't set up on this server." == GMAIL_DISABLED_MESSAGE
    assert error.retryable is False and error.settings_path is None and error.details == {}


def test_requiring_it_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """Every call, not just the first: a worker asks on every tick."""
    with caplog.at_level(logging.DEBUG):
        for _ in range(3):
            with pytest.raises(ApiError):
                require_gmail_oauth_config()
    assert caplog.records == []


# -- booting -------------------------------------------------------------------------------


def test_the_app_boots_with_no_gmail_configuration(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG), _client() as client:
        assert client.get("/health").status_code == 200
    assert [r for r in caplog.records if r.levelno >= logging.CRITICAL] == []


def test_the_app_boots_with_all_three(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, GOOGLE_OAUTH_VARIABLES)
    with _client() as client:
        assert client.get("/health").status_code == 200


_REDIRECT_ONLY = ("GOOGLE_OAUTH_REDIRECT_URI",)
_HALF_SET = [
    subset
    for size in (1, 2)
    for subset in itertools.combinations(GOOGLE_OAUTH_VARIABLES, size)
    if subset != _REDIRECT_ONLY
]


@pytest.mark.parametrize("subset", _HALF_SET, ids=lambda subset: "+".join(n[13:] for n in subset))
def test_some_but_not_all_stops_the_boot_naming_the_variables_never_a_value(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, subset: tuple[str, ...]
) -> None:
    """A connect button that can never work, or a client secret with nothing to use it, is a
    mistake and not "off"."""
    _set(monkeypatch, subset)

    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError) as raised, _client():
        pass

    message = str(raised.value)
    for name in GOOGLE_OAUTH_VARIABLES:
        assert name in message  # all three are named, so the operator sees what is needed
    missing = [name for name in GOOGLE_OAUTH_VARIABLES if name not in subset]
    assert message.endswith(f"(not set: {', '.join(missing)})")
    for value in (_ID, _SECRET, _REDIRECT):
        assert value not in message and value not in caplog.text
    # one line of its own, so a platform that shows only the logs shows the reason
    [critical] = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert critical.getMessage() == f"the API cannot start: {message}"


def test_a_half_set_gmail_is_refused_by_the_check_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, ("GOOGLE_OAUTH_CLIENT_SECRET",))
    with pytest.raises(RuntimeError, match="must be set together"):
        check_gmail_configuration()


def test_a_redirect_address_on_its_own_is_gmail_off_and_boots(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Nothing secret is set and nothing could be connected, so it is not a mistake worth
    stopping for; it is what the earlier .env.example left in every copy of it."""
    _set(monkeypatch, _REDIRECT_ONLY)

    assert gmail_oauth_config() is None
    assert gmail_enabled() is False
    assert check_gmail_configuration() is False
    with pytest.raises(ApiError) as raised:
        require_gmail_oauth_config()
    assert raised.value.code == "FEATURE_DISABLED"

    with caplog.at_level(logging.DEBUG), _client() as client:
        assert client.get("/health").status_code == 200
        capabilities = client.get("/capabilities").json()
    assert "gmail" not in capabilities
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


@pytest.mark.parametrize("blank", GOOGLE_OAUTH_VARIABLES)
def test_two_of_the_three_is_a_refusal_whichever_one_is_blank(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    """A blank value (`NAME=` in a .env file) reads as not set, so this is the half-set mistake
    too. Only a redirect address ALONE is let through."""
    _set(monkeypatch, GOOGLE_OAUTH_VARIABLES)
    monkeypatch.setenv(blank, "")

    with pytest.raises(RuntimeError, match="must be set together") as raised:
        check_gmail_configuration()

    assert str(raised.value).endswith(f"(not set: {blank})")


# -- off means off --------------------------------------------------------------------------


def test_capabilities_leaves_gmail_out_when_it_is_off() -> None:
    with _client() as client:
        response = client.get("/capabilities")
    assert response.status_code == 200
    assert "gmail" not in response.json()
    assert response.json() == {
        "telegram": False,
        "telegram_bot_username": None,
        "tester_program_required": False,
        "engine": "remote",
    }


def test_capabilities_says_gmail_is_on_and_never_shows_a_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set(monkeypatch, GOOGLE_OAUTH_VARIABLES)
    with _client() as client:
        response = client.get("/capabilities")
    assert response.json() == {
        "telegram": False,
        "telegram_bot_username": None,
        "tester_program_required": False,
        "engine": "remote",
        "gmail": True,
    }
    for value in (_ID, _SECRET, _REDIRECT):
        assert value not in response.text
