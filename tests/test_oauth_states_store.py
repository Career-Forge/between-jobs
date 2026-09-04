"""Tests for OAuth-state correlation tokens (outreach-contactfinder.md
Phase F) -- single-use, provider-scoped, expiring.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from between_jobs.api.oauth_states_store import consume_state, mint_state

_USER_ID = "00000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTable:
    def __init__(self, *, select_rows: list[dict[str, Any]]) -> None:
        self.select_rows = select_rows
        self.insert_calls: list[Any] = []
        self.delete_calls: list[str] = []

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(list(self.select_rows))

    def insert(self, data: Any) -> _ChainBuilder:
        self.insert_calls.append(data)
        self.select_rows.append(dict(data, created_at=datetime.now(UTC).isoformat()))
        return _ChainBuilder([data])

    def delete(self) -> _DeleteBuilder:
        return _DeleteBuilder(self)


class _DeleteBuilder:
    def __init__(self, table: _FakeTable) -> None:
        self._table = table

    def eq(self, _column: str, value: Any) -> _DeleteBuilder:
        self._table.delete_calls.append(value)
        self._table.select_rows = [r for r in self._table.select_rows if r.get("state") != value]
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=[])


class _FakeSupabaseClient:
    def __init__(self, *, oauth_states: _FakeTable | None = None) -> None:
        self.oauth_states = oauth_states or _FakeTable(select_rows=[])

    def table(self, name: str) -> Any:
        return {"oauth_states": self.oauth_states}[name]


async def test_mint_state_inserts_a_row_for_the_user() -> None:
    supabase = _FakeSupabaseClient()
    state = await mint_state(supabase, _USER_ID, "gmail")  # type: ignore[arg-type]

    assert len(state) > 20  # a real random token, not a short guessable one
    inserted = supabase.oauth_states.insert_calls[0]
    assert inserted["user_id"] == _USER_ID
    assert inserted["provider"] == "gmail"


async def test_consume_state_returns_the_user_id_for_a_valid_state() -> None:
    row = {
        "state": "state-abc",
        "user_id": _USER_ID,
        "provider": "gmail",
        "created_at": datetime.now(UTC).isoformat(),
    }
    supabase = _FakeSupabaseClient(oauth_states=_FakeTable(select_rows=[row]))

    result = await consume_state(supabase, "state-abc", "gmail")  # type: ignore[arg-type]
    assert result == _USER_ID


async def test_consume_state_deletes_the_row_so_it_cannot_be_replayed() -> None:
    row = {
        "state": "state-abc",
        "user_id": _USER_ID,
        "provider": "gmail",
        "created_at": datetime.now(UTC).isoformat(),
    }
    table = _FakeTable(select_rows=[row])
    supabase = _FakeSupabaseClient(oauth_states=table)

    await consume_state(supabase, "state-abc", "gmail")  # type: ignore[arg-type]

    assert table.select_rows == []
    assert "state-abc" in table.delete_calls


async def test_consume_state_returns_none_for_an_unknown_state() -> None:
    supabase = _FakeSupabaseClient(oauth_states=_FakeTable(select_rows=[]))
    result = await consume_state(supabase, "nonexistent", "gmail")  # type: ignore[arg-type]
    assert result is None


async def test_consume_state_returns_none_for_a_provider_mismatch() -> None:
    row = {
        "state": "state-abc",
        "user_id": _USER_ID,
        "provider": "some_other_provider",
        "created_at": datetime.now(UTC).isoformat(),
    }
    supabase = _FakeSupabaseClient(oauth_states=_FakeTable(select_rows=[row]))

    result = await consume_state(supabase, "state-abc", "gmail")  # type: ignore[arg-type]
    assert result is None


async def test_consume_state_returns_none_for_an_expired_state() -> None:
    stale = (datetime.now(UTC) - timedelta(minutes=15)).isoformat()
    row = {"state": "state-abc", "user_id": _USER_ID, "provider": "gmail", "created_at": stale}
    supabase = _FakeSupabaseClient(oauth_states=_FakeTable(select_rows=[row]))

    result = await consume_state(supabase, "state-abc", "gmail")  # type: ignore[arg-type]
    assert result is None
