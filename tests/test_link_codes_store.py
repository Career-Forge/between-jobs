"""Tests for the /link one-time-code persistence (Sprint 2.8c/2.8d)."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

from between_jobs.api.link_codes_store import _CODE_ALPHABET, consume_link_code, mint_code

_USER_ID = "00000000-0000-0000-0000-000000000001"
_LINK_CODE_ID = "60000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def is_(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTable:
    def __init__(
        self, *, select_rows: list[dict[str, Any]], insert_row: dict[str, Any] | None = None
    ) -> None:
        self.select_rows = select_rows
        self.insert_row = insert_row
        self.insert_calls: list[dict[str, Any]] = []
        self.delete_calls = 0

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def insert(self, data: dict[str, Any]) -> _ChainBuilder:
        self.insert_calls.append(data)
        rows = [self.insert_row] if self.insert_row is not None else []
        return _ChainBuilder(rows)

    def delete(self) -> _ChainBuilder:
        self.delete_calls += 1
        return _ChainBuilder([])


class _FakeRpcBuilder:
    def __init__(self, data: Any) -> None:
        self._data = data

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._data)


class _FakeSupabaseClient:
    def __init__(self, table: _FakeTable, *, rpc_data: Any = None) -> None:
        self.link_codes = table
        self.rpc_data = rpc_data
        self.rpc_calls: list[tuple[str, dict[str, Any]]] = []

    def table(self, name: str) -> Any:
        assert name == "link_codes"
        return self.link_codes

    def rpc(self, fn: str, params: dict[str, Any]) -> _FakeRpcBuilder:
        self.rpc_calls.append((fn, params))
        return _FakeRpcBuilder(self.rpc_data)


async def test_mint_code_deletes_prior_pending_then_inserts() -> None:
    table = _FakeTable(select_rows=[], insert_row={"id": _LINK_CODE_ID})
    client = _FakeSupabaseClient(table)

    code, expires_at = await mint_code(client, _USER_ID, "telegram")  # type: ignore[arg-type]

    assert table.delete_calls == 1
    assert len(table.insert_calls) == 1
    assert len(code) == 8
    assert all(c in _CODE_ALPHABET for c in code)
    assert expires_at  # non-empty ISO string
    # Only the hash is ever persisted, never the raw code.
    assert table.insert_calls[0]["code_hash"] == hashlib.sha256(code.encode("utf-8")).hexdigest()
    assert code not in str(table.insert_calls[0])


async def test_consume_link_code_calls_the_rpc_with_expected_params() -> None:
    client = _FakeSupabaseClient(
        _FakeTable(select_rows=[]), rpc_data={"ok": True, "target_user_id": "target-1"}
    )

    result = await consume_link_code(
        client,  # type: ignore[arg-type]
        channel="telegram",
        external_subject="12345",
        code="ABCD2345",
        source_user_id=_USER_ID,
    )

    assert result == {"ok": True, "target_user_id": "target-1"}
    assert client.rpc_calls == [
        (
            "consume_link_code",
            {
                "p_channel": "telegram",
                "p_external_subject": "12345",
                "p_code": "ABCD2345",
                "p_source_user_id": _USER_ID,
            },
        )
    ]


async def test_consume_link_code_returns_soft_failure_shape() -> None:
    client = _FakeSupabaseClient(
        _FakeTable(select_rows=[]), rpc_data={"ok": False, "reason": "invalid_code"}
    )

    result = await consume_link_code(
        client,  # type: ignore[arg-type]
        channel="telegram",
        external_subject="12345",
        code="WRONGONE",
        source_user_id=_USER_ID,
    )

    assert result == {"ok": False, "reason": "invalid_code"}
