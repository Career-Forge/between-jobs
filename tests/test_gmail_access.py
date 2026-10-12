"""Who may use Gmail on a server, and how a connection Google stopped honouring is recorded
(`gmail_access`).

What is pinned here is the part no single route test can show:

- the two settings, `GMAIL_ALLOWED_USER_IDS` and `GMAIL_TESTING_MODE`: how they read, and that
  a malformed one stops the boot naming the setting and never the value;
- `GET /capabilities` per caller: `gmail_allowed` is the caller's own answer, there is nothing in
  it about anybody else, and a server without Gmail says exactly what it always said;
- listing and disconnecting are never gated, so a person who is not (or no longer) listed can
  see their connection and delete it, and nothing is deleted for them;
- that every module that talks to Google for a person goes through the list, so a new entry
  point cannot forget it (the routes' own behaviour is in `test_gmail_oauth_routes.py` and
  `test_contact_research_routes.py`, the worker's in `test_gmail_reply_checker.py`);
- the flag that records a revoked connection: one WARNING per transition, and no token and no
  word of Google's in it.
"""

from __future__ import annotations

import ast
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from between_jobs.api.app import app
from between_jobs.api.app_state import get_supabase
from between_jobs.api.auth import require_user_id
from between_jobs.api.env import ConfigurationError
from between_jobs.api.errors import ApiError
from between_jobs.api.gmail_access import (
    GMAIL_NOT_AVAILABLE_MESSAGE,
    check_gmail_access_settings,
    flag_reconnect_required,
    gmail_allowlist,
    gmail_testing_mode,
    is_listed,
    require_gmail_user,
    user_may_use_gmail,
)
from between_jobs.api.gmail_client import GOOGLE_OAUTH_VARIABLES

_ALLOWED_ENV = "GMAIL_ALLOWED_USER_IDS"
_TESTING_ENV = "GMAIL_TESTING_MODE"
_USER = "00000000-0000-0000-0000-000000000001"
_OTHER = "00000000-0000-0000-0000-000000000002"
_THIRD = "00000000-0000-0000-0000-000000000003"
_ROOT = Path(__file__).parent.parent
_GOOGLE_VALUES = {
    "GOOGLE_OAUTH_CLIENT_ID": "client-id-value-that-must-not-be-echoed",
    "GOOGLE_OAUTH_CLIENT_SECRET": "client-secret-value-that-must-not-be-echoed",
    "GOOGLE_OAUTH_REDIRECT_URI": "https://api.example.test/oauth/gmail/callback",
}


@pytest.fixture(autouse=True)
def _environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv(_ALLOWED_ENV, raising=False)
    monkeypatch.delenv(_TESTING_ENV, raising=False)
    for name in GOOGLE_OAUTH_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    yield
    app.dependency_overrides.clear()


def _gmail_on(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _GOOGLE_VALUES.items():
        monkeypatch.setenv(name, value)


@contextmanager
def _client(user_id: str = _USER, *, supabase: Any = None) -> Iterator[TestClient]:
    app.dependency_overrides[require_user_id] = lambda: user_id
    if supabase is not None:
        app.dependency_overrides[get_supabase] = lambda: supabase
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


# -- GMAIL_ALLOWED_USER_IDS ----------------------------------------------------------------------


def test_with_no_list_everyone_may_use_gmail_and_it_reads_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert gmail_allowlist() is None
    assert user_may_use_gmail(_USER) and user_may_use_gmail(_OTHER)
    for blank in ("", "   "):
        monkeypatch.setenv(_ALLOWED_ENV, blank)  # a blank `KEY=` line is "not configured"
        assert gmail_allowlist() is None
        assert user_may_use_gmail(_OTHER)


def test_with_a_list_only_those_on_it_may_and_spaces_around_an_entry_do_not_matter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ALLOWED_ENV, f" {_USER} , {_THIRD}")
    assert gmail_allowlist() == frozenset({_USER, _THIRD})
    assert user_may_use_gmail(_USER) and user_may_use_gmail(_THIRD)
    assert not user_may_use_gmail(_OTHER)


def test_ids_are_matched_in_lower_case_whichever_side_writes_capitals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upper = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
    monkeypatch.setenv(_ALLOWED_ENV, upper)
    assert user_may_use_gmail(upper.lower())
    assert user_may_use_gmail(upper)
    assert not user_may_use_gmail(_USER)


def test_one_membership_test_is_used_everywhere() -> None:
    assert is_listed(None, _USER)
    assert is_listed(frozenset({_USER}), _USER.upper())
    assert not is_listed(frozenset({_USER}), _OTHER)
    assert not is_listed(frozenset(), _USER)  # unreachable from the setting, but an empty list is
    # "nobody", never "everybody"


@pytest.mark.parametrize("bad", [f"{_USER},not-an-id", f"{_USER},,{_OTHER}", ",", f"{_USER},"])
def test_a_malformed_list_stops_the_boot_naming_the_setting_and_never_the_value(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, bad: str
) -> None:
    monkeypatch.setenv(_ALLOWED_ENV, bad)

    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError) as raised, _client():
        pass

    message = str(raised.value)
    assert message.startswith(f"{_ALLOWED_ENV} must be a comma-separated list of user ids")
    assert "not-an-id" not in message and _USER not in message
    [critical] = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert critical.getMessage() == f"the API cannot start: {message}"
    assert "not-an-id" not in caplog.text


def test_a_malformed_list_stops_the_boot_even_on_a_server_that_has_no_gmail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo in a list that decides who gets something must not wait for Gmail to be turned on
    to be found."""
    monkeypatch.setenv(_ALLOWED_ENV, "nonsense")
    with pytest.raises(ConfigurationError), _client():
        pass


def test_a_good_list_boots(monkeypatch: pytest.MonkeyPatch) -> None:
    _gmail_on(monkeypatch)
    monkeypatch.setenv(_ALLOWED_ENV, f"{_USER},{_OTHER}")
    with _client() as client:
        assert client.get("/health").status_code == 200


# -- GMAIL_TESTING_MODE --------------------------------------------------------------------------


def test_testing_mode_is_off_unless_the_operator_says_on(monkeypatch: pytest.MonkeyPatch) -> None:
    assert gmail_testing_mode() is False
    for blank in ("", "  "):
        monkeypatch.setenv(_TESTING_ENV, blank)
        assert gmail_testing_mode() is False
    for on in ("on", "ON", " On "):
        monkeypatch.setenv(_TESTING_ENV, on)
        assert gmail_testing_mode() is True
    for off in ("off", "OFF", " off "):
        monkeypatch.setenv(_TESTING_ENV, off)
        assert gmail_testing_mode() is False


@pytest.mark.parametrize("bad", ["true", "1", "yes", "enabled", "false"])
def test_any_other_spelling_of_testing_mode_stops_the_boot_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, bad: str
) -> None:
    """Strict on purpose, like the tester-programme switch: `=false` must not mean "on"."""
    monkeypatch.setenv(_TESTING_ENV, bad)

    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError) as raised, _client():
        pass

    assert str(raised.value) == f"{_TESTING_ENV} must be 'on' or 'off'"
    assert f"={bad}" not in caplog.text


def test_the_boot_check_reads_both_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    check_gmail_access_settings()  # nothing set: fine
    monkeypatch.setenv(_TESTING_ENV, "maybe")
    with pytest.raises(ConfigurationError):
        check_gmail_access_settings()
    monkeypatch.setenv(_TESTING_ENV, "on")
    monkeypatch.setenv(_ALLOWED_ENV, "nonsense")
    with pytest.raises(ConfigurationError):
        check_gmail_access_settings()


# -- the one answer for "not for you" -------------------------------------------------------------


def test_a_server_without_gmail_answers_with_its_own_sentence_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ALLOWED_ENV, _OTHER)
    with pytest.raises(ApiError) as raised:
        require_gmail_user(_USER)
    assert raised.value.message == "Gmail isn't set up on this server."


def test_a_person_who_is_not_listed_gets_a_404_feature_disabled_that_says_nothing_of_the_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _gmail_on(monkeypatch)
    monkeypatch.setenv(_ALLOWED_ENV, f"{_OTHER},{_THIRD}")

    with pytest.raises(ApiError) as raised:
        require_gmail_user(_USER)

    error = raised.value
    assert (error.code, error.status_code) == ("FEATURE_DISABLED", 404)
    assert (
        error.message
        == GMAIL_NOT_AVAILABLE_MESSAGE
        == "Gmail isn't available for your account yet."
    )
    assert error.retryable is False and error.settings_path is None and error.details == {}
    for listed in (_OTHER, _THIRD):
        assert listed not in error.message


def test_a_listed_person_gets_the_servers_settings_back(monkeypatch: pytest.MonkeyPatch) -> None:
    _gmail_on(monkeypatch)
    monkeypatch.setenv(_ALLOWED_ENV, _USER)
    assert require_gmail_user(_USER)["client_id"] == _GOOGLE_VALUES["GOOGLE_OAUTH_CLIENT_ID"]


def test_with_no_list_any_signed_in_person_gets_the_servers_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _gmail_on(monkeypatch)
    assert require_gmail_user(_USER)["redirect_uri"] == _GOOGLE_VALUES["GOOGLE_OAUTH_REDIRECT_URI"]


# -- GET /capabilities, per caller ---------------------------------------------------------------


def test_a_server_without_gmail_says_nothing_about_gmail_whatever_else_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ALLOWED_ENV, _OTHER)
    monkeypatch.setenv(_TESTING_ENV, "on")

    with _client() as client:
        response = client.get("/capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "telegram": False,
        "telegram_bot_username": None,
        "tester_program_required": False,
        "engine": "remote",
    }


def test_with_gmail_on_and_no_list_everyone_is_allowed_and_testing_mode_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _gmail_on(monkeypatch)
    for caller in (_USER, _OTHER):
        with _client(caller) as client:
            body = client.get("/capabilities").json()
        assert body["gmail"] is True
        assert body["gmail_allowed"] is True
        assert body["gmail_testing_mode"] is False


def test_the_list_decides_per_caller_and_the_answer_never_names_anyone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _gmail_on(monkeypatch)
    monkeypatch.setenv(_ALLOWED_ENV, f"{_USER},{_THIRD}")

    with _client(_USER) as client:
        listed = client.get("/capabilities")
    with _client(_OTHER) as client:
        unlisted = client.get("/capabilities")

    assert listed.json()["gmail_allowed"] is True
    assert unlisted.json()["gmail_allowed"] is False
    assert unlisted.json()["gmail"] is True  # Gmail exists here; it is just not for them yet
    assert {k: v for k, v in unlisted.json().items() if k != "gmail_allowed"} == {
        k: v for k, v in listed.json().items() if k != "gmail_allowed"
    }
    for response in (listed, unlisted):
        for secret in (*_GOOGLE_VALUES.values(), _USER, _OTHER, _THIRD):
            assert secret not in response.text


def test_testing_mode_is_the_operators_switch_for_everyone(monkeypatch: pytest.MonkeyPatch) -> None:
    _gmail_on(monkeypatch)
    monkeypatch.setenv(_TESTING_ENV, "on")
    monkeypatch.setenv(_ALLOWED_ENV, _USER)
    for caller in (_USER, _OTHER):
        with _client(caller) as client:
            assert client.get("/capabilities").json()["gmail_testing_mode"] is True


def test_capabilities_is_still_for_signed_in_people_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _gmail_on(monkeypatch)
    with TestClient(app) as client:
        response = client.get("/capabilities")
    assert response.status_code == 401


# -- listing and disconnecting are never gated ---------------------------------------------------


class _Query:
    def __init__(self, table: _CredentialRows, action: str) -> None:
        self._table = table
        self._action = action
        self._filters: dict[str, Any] = {}

    def select(self, *_: Any, **__: Any) -> _Query:
        return self

    def eq(self, column: str, value: Any) -> _Query:
        self._filters[column] = value
        return self

    async def execute(self) -> SimpleNamespace:
        if self._action == "delete":
            self._table.deleted.append(dict(self._filters))
            self._table.rows = [
                r
                for r in self._table.rows
                if not all(r.get(k) == v for k, v in self._filters.items())
            ]
            return SimpleNamespace(data=[])
        return SimpleNamespace(
            data=[
                r for r in self._table.rows if all(r.get(k) == v for k, v in self._filters.items())
            ]
        )


class _CredentialRows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.deleted: list[dict[str, Any]] = []

    def select(self, *_: Any, **__: Any) -> _Query:
        return _Query(self, "select")

    def delete(self) -> _Query:
        return _Query(self, "delete")


class _Database:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.provider_credentials = _CredentialRows(rows)

    def table(self, name: str) -> _CredentialRows:
        assert name == "provider_credentials"
        return self.provider_credentials


_GMAIL_ROW = {
    "id": "cred-1",
    "user_id": _USER,
    "service": "oauth",
    "provider": "gmail",
    "is_validated": False,
    "scope": "https://www.googleapis.com/auth/gmail.compose",
    "secret_encrypted": "ciphertext",
}


def test_a_person_who_is_not_listed_can_see_their_connection_and_delete_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _gmail_on(monkeypatch)
    monkeypatch.setenv(_ALLOWED_ENV, _OTHER)  # they were removed (or never added)
    database = _Database([dict(_GMAIL_ROW), {**_GMAIL_ROW, "id": "cred-2", "user_id": _OTHER}])

    with _client(_USER, supabase=database) as client:
        listing = client.get("/credentials")
        assert listing.status_code == 200
        [row] = listing.json()
        assert (row["service"], row["provider"], row["is_validated"]) == ("oauth", "gmail", False)
        assert "secret_encrypted" not in row  # the listing never carries the token
        deleted = client.delete("/credentials/oauth/gmail")
        assert deleted.status_code == 204
        assert client.get("/credentials").json() == []

    # exactly their own row went; the listed person's is untouched
    assert database.provider_credentials.deleted == [
        {"user_id": _USER, "service": "oauth", "provider": "gmail"}
    ]
    assert [r["id"] for r in database.provider_credentials.rows] == ["cred-2"]


def test_a_person_who_is_not_listed_keeps_the_stored_connection_until_they_remove_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Being removed from the list deletes nothing: reading Gmail's state is not gated, and no
    route of the list's own touches a credential."""
    _gmail_on(monkeypatch)
    monkeypatch.setenv(_ALLOWED_ENV, _OTHER)
    database = _Database([dict(_GMAIL_ROW)])

    with _client(_USER, supabase=database) as client:
        assert len(client.get("/credentials").json()) == 1
        assert len(client.get("/capabilities").json()) > 0

    assert database.provider_credentials.deleted == []
    assert len(database.provider_credentials.rows) == 1


# -- no entry point can forget the list ----------------------------------------------------------

_SRC = _ROOT / "src" / "between_jobs"
_THE_CALLS_TO_GOOGLE_FOR_A_PERSON = {
    "build_authorize_url",
    "exchange_code_for_tokens",
    "refresh_access_token",
    "create_draft",
    "get_thread",
}
_THE_MODULES_THAT_MAY_MAKE_THEM = {
    "gmail_oauth_routes.py",
    "contact_research_routes.py",
    "gmail_reply_checker.py",
}


def _importers_of_the_google_calls() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in sorted(_SRC.rglob("*.py")):
        if path.name == "gmail_client.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("gmail_client"):
                names = {alias.name for alias in node.names} & _THE_CALLS_TO_GOOGLE_FOR_A_PERSON
                if names:
                    found.setdefault(path.name, set()).update(names)
    return found


def test_only_the_three_known_modules_call_google_for_a_person_and_each_one_checks_the_list() -> (
    None
):
    """A fourth caller (a new route, a new worker, a chat command) fails here until it has been
    thought about: either it goes through `require_gmail_user` / `user_may_use_gmail`, or it
    is added to this list on purpose."""
    importers = _importers_of_the_google_calls()
    assert set(importers) == _THE_MODULES_THAT_MAY_MAKE_THEM, importers
    for name in importers:
        source = (next(_SRC.rglob(name))).read_text()
        assert re.search(r"\b(require_gmail_user|user_may_use_gmail)\b", source), (
            f"{name} calls Google for a person without consulting the Gmail list"
        )


def test_only_the_access_module_reads_the_two_settings() -> None:
    for variable in (_ALLOWED_ENV, _TESTING_ENV):
        readers = sorted(
            path.name for path in _SRC.rglob("*.py") if f'"{variable}"' in path.read_text()
        )
        assert readers == ["gmail_access.py"], (variable, readers)


def test_the_credential_routes_the_list_leaves_alone_have_no_gmail_check() -> None:
    """Disconnecting must never be refused: nothing in them consults the list."""
    source = (_SRC / "api" / "credentials_routes.py").read_text()
    assert "gmail_access" not in source and "user_may_use_gmail" not in source


# -- flagging a connection -----------------------------------------------------------------------


class _FlagTable:
    def __init__(self, *, matches: list[bool]) -> None:
        self._matches = list(matches)
        self.updates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self._filters: dict[str, Any] = {}
        self._data: dict[str, Any] = {}

    def update(self, data: dict[str, Any]) -> _FlagTable:
        self._data, self._filters = data, {}
        return self

    def eq(self, column: str, value: Any) -> _FlagTable:
        self._filters[column] = value
        return self

    async def execute(self) -> SimpleNamespace:
        self.updates.append((self._data, dict(self._filters)))
        return SimpleNamespace(data=[{"id": "cred-1"}] if self._matches.pop(0) else [])


class _FlagDatabase:
    def __init__(self, table: _FlagTable) -> None:
        self._table = table

    def table(self, name: str) -> _FlagTable:
        assert name == "provider_credentials"
        return self._table


async def test_flagging_logs_one_warning_for_the_transition_and_nothing_for_a_repeat(
    caplog: pytest.LogCaptureFixture,
) -> None:
    table = _FlagTable(matches=[True, False, False])
    database = _FlagDatabase(table)

    with caplog.at_level(logging.DEBUG):
        for _ in range(3):
            await flag_reconnect_required(database, _USER)  # type: ignore[arg-type]

    [record] = caplog.records
    assert record.levelno == logging.WARNING
    assert record.name == "between_jobs.api.gmail_access"
    assert record.getMessage() == "a Gmail connection needs to be reconnected"
    assert record.ctx == {"user_id": _USER, "reason": "gmail_reconnect_required"}  # type: ignore[attr-defined]
    assert record.exc_info is None
    # three attempts, each the same conditional update on that person's Gmail credential
    assert len(table.updates) == 3
    for data, filters in table.updates:
        assert data == {"is_validated": False}
        assert filters == {
            "user_id": _USER,
            "service": "oauth",
            "provider": "gmail",
            "is_validated": True,
        }
