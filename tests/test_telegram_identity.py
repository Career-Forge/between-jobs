"""Tests for Telegram identity resolution/auto-provisioning."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from postgrest.exceptions import APIError

from between_jobs.api.telegram_identity import get_chat_id, resolve_or_create_user_id

_TELEGRAM_USER_ID = 987654321
_EXISTING_USER_ID = "00000000-0000-0000-0000-000000000001"
_NEW_USER_ID = "00000000-0000-0000-0000-000000000002"


class _FakeSelectQuery:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def select(self, columns: str) -> _FakeSelectQuery:
        return self

    def eq(self, column: str, value: Any) -> _FakeSelectQuery:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeInsertQuery:
    def __init__(self, error: APIError | None) -> None:
        self._error = error

    def insert(self, data: dict[str, Any]) -> _FakeInsertQuery:
        return self

    async def execute(self) -> SimpleNamespace:
        if self._error:
            raise self._error
        return SimpleNamespace(data=[{}])


class _FakeChannelIdentitiesTable:
    """Same object serves both .select(...) and .insert(...) call sites,
    matching how postgrest's real query builder works (one table handle,
    different verb methods) -- separate per-scenario row/error state."""

    def __init__(
        self, select_rows: list[dict[str, Any]], insert_error: APIError | None = None
    ) -> None:
        self._select = _FakeSelectQuery(select_rows)
        self._insert = _FakeInsertQuery(insert_error)

    def select(self, columns: str) -> _FakeSelectQuery:
        return self._select

    def insert(self, data: dict[str, Any]) -> _FakeInsertQuery:
        return self._insert


class _FakeAdmin:
    def __init__(self, new_user_id: str) -> None:
        self._new_user_id = new_user_id
        self.create_user_calls: list[dict[str, Any]] = []

    async def create_user(self, attributes: dict[str, Any]) -> SimpleNamespace:
        self.create_user_calls.append(attributes)
        return SimpleNamespace(user=SimpleNamespace(id=self._new_user_id))


class _FakeAuth:
    def __init__(self, new_user_id: str) -> None:
        self.admin = _FakeAdmin(new_user_id)


class _FakeSupabaseClient:
    def __init__(
        self,
        *,
        select_rows: list[dict[str, Any]],
        new_user_id: str = _NEW_USER_ID,
        insert_error: APIError | None = None,
        select_rows_after_race: list[dict[str, Any]] | None = None,
    ) -> None:
        self.auth = _FakeAuth(new_user_id)
        self._table = _FakeChannelIdentitiesTable(select_rows, insert_error)
        self._select_rows_after_race = select_rows_after_race
        self._select_calls = 0

    def table(self, name: str) -> Any:
        assert name == "channel_identities"
        self._select_calls += 1
        # Calls, in order: (1) the initial select, (2) the insert that
        # raises the unique violation -- both need self._table, which
        # carries the configured insert_error. Only the (3) fallback
        # re-select after the caught exception should see the "other
        # request won" rows.
        if self._select_calls > 2 and self._select_rows_after_race is not None:
            return _FakeChannelIdentitiesTable(self._select_rows_after_race)
        return self._table


async def test_existing_link_returns_its_user_id() -> None:
    client = _FakeSupabaseClient(select_rows=[{"user_id": _EXISTING_USER_ID}])
    result = await resolve_or_create_user_id(client, _TELEGRAM_USER_ID)  # type: ignore[arg-type]
    assert result == _EXISTING_USER_ID
    assert client.auth.admin.create_user_calls == []


async def test_new_telegram_user_gets_provisioned() -> None:
    client = _FakeSupabaseClient(select_rows=[], new_user_id=_NEW_USER_ID)
    result = await resolve_or_create_user_id(client, _TELEGRAM_USER_ID)  # type: ignore[arg-type]
    assert result == _NEW_USER_ID
    assert len(client.auth.admin.create_user_calls) == 1
    call = client.auth.admin.create_user_calls[0]
    assert call["app_metadata"]["provider"] == "telegram"
    # Regression guard for the live bug found in Sprint 2.5: the real Auth
    # server rejects create_user({}) with neither email nor phone, even
    # though the SDK's type stub marks both NotRequired. Every field the
    # fix depends on must actually be sent.
    assert call["email"] == f"telegram-{_TELEGRAM_USER_ID}@users.between-jobs.tech"
    assert call["email_confirm"] is True


async def test_concurrent_provisioning_race_falls_back_to_winner() -> None:
    unique_violation = APIError(
        {
            "message": "duplicate key value violates unique constraint "
            '"channel_identities_channel_external_tenant_external_subject_key"',
            "code": "23505",
            "hint": None,
            "details": None,
        }
    )
    client = _FakeSupabaseClient(
        select_rows=[],
        new_user_id=_NEW_USER_ID,
        insert_error=unique_violation,
        select_rows_after_race=[{"user_id": _EXISTING_USER_ID}],
    )
    result = await resolve_or_create_user_id(client, _TELEGRAM_USER_ID)  # type: ignore[arg-type]
    # The OTHER request's row wins -- not the user we just (redundantly) created.
    assert result == _EXISTING_USER_ID


async def test_non_unique_db_error_propagates() -> None:
    other_error = APIError(
        {"message": "something else broke", "code": "42P01", "hint": None, "details": None}
    )
    client = _FakeSupabaseClient(select_rows=[], insert_error=other_error)
    with pytest.raises(APIError):
        await resolve_or_create_user_id(client, _TELEGRAM_USER_ID)  # type: ignore[arg-type]


async def test_get_chat_id_returns_the_linked_telegram_user_id() -> None:
    client = _FakeSupabaseClient(select_rows=[{"external_subject": str(_TELEGRAM_USER_ID)}])
    result = await get_chat_id(client, _EXISTING_USER_ID)  # type: ignore[arg-type]
    assert result == _TELEGRAM_USER_ID


async def test_get_chat_id_returns_none_when_no_telegram_identity_is_linked() -> None:
    client = _FakeSupabaseClient(select_rows=[])
    result = await get_chat_id(client, _EXISTING_USER_ID)  # type: ignore[arg-type]
    assert result is None
