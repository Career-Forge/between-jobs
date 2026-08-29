"""Tests for BYOK provider credential persistence (Sprint 2.7c)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from between_jobs.api.provider_credentials_store import (
    CredentialNotFound,
    delete_credential,
    get_decrypted_credential,
    list_credentials,
    save_credential,
)

_USER_ID = "00000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTable:
    def __init__(
        self, *, select_rows: list[dict[str, Any]], upsert_row: dict[str, Any] | None = None
    ) -> None:
        self.select_rows = select_rows
        self.upsert_row = upsert_row
        self.upsert_calls: list[tuple[dict[str, Any], str]] = []
        self.delete_calls = 0

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def upsert(self, data: dict[str, Any], on_conflict: str = "") -> _ChainBuilder:
        self.upsert_calls.append((data, on_conflict))
        rows = [self.upsert_row] if self.upsert_row is not None else []
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
    def __init__(
        self,
        table: _FakeTable,
        *,
        encrypt_output: str | None = None,
        decrypt_output: str | None = None,
    ) -> None:
        self.provider_credentials = table
        self.encrypt_output = encrypt_output
        self.decrypt_output = decrypt_output
        self.rpc_calls: list[tuple[str, dict[str, Any]]] = []

    def table(self, name: str) -> Any:
        assert name == "provider_credentials"
        return self.provider_credentials

    def rpc(self, fn: str, params: dict[str, Any]) -> _FakeRpcBuilder:
        self.rpc_calls.append((fn, params))
        if fn == "encrypt_secret":
            return _FakeRpcBuilder(self.encrypt_output)
        if fn == "decrypt_secret":
            return _FakeRpcBuilder(self.decrypt_output)
        raise AssertionError(f"unexpected rpc: {fn}")


async def test_save_credential_encrypts_then_upserts() -> None:
    saved_row = {"id": "cred-1", "service": "llm", "provider": "openrouter"}
    table = _FakeTable(select_rows=[], upsert_row=saved_row)
    client = _FakeSupabaseClient(table, encrypt_output="ciphertext-abc")

    result = await save_credential(
        client,  # type: ignore[arg-type]
        _USER_ID,
        service="llm",
        provider="openrouter",
        secret="sk-or-v1-plaintext",
    )

    assert result == saved_row
    assert client.rpc_calls == [("encrypt_secret", {"p_plaintext": "sk-or-v1-plaintext"})]
    assert len(table.upsert_calls) == 1
    data, on_conflict = table.upsert_calls[0]
    assert data["secret_encrypted"] == "ciphertext-abc"
    assert data["user_id"] == _USER_ID
    assert "sk-or-v1-plaintext" not in str(data)
    assert on_conflict == "user_id,service,provider"


async def test_list_credentials_filters_by_service_when_given() -> None:
    rows = [{"id": "cred-1", "service": "llm"}]
    table = _FakeTable(select_rows=rows)
    client = _FakeSupabaseClient(table)

    result = await list_credentials(client, _USER_ID, service="llm")  # type: ignore[arg-type]

    assert result == rows


async def test_list_credentials_without_service_filter() -> None:
    rows = [{"id": "cred-1"}, {"id": "cred-2"}]
    table = _FakeTable(select_rows=rows)
    client = _FakeSupabaseClient(table)

    result = await list_credentials(client, _USER_ID)  # type: ignore[arg-type]

    assert result == rows


async def test_get_decrypted_credential_found() -> None:
    table = _FakeTable(
        select_rows=[
            {
                "provider": "openrouter",
                "model": "anthropic/claude-sonnet-4-6",
                "base_url": None,
                "secret_encrypted": "ciphertext-abc",
            }
        ]
    )
    client = _FakeSupabaseClient(table, decrypt_output="sk-or-v1-plaintext")

    result = await get_decrypted_credential(
        client,  # type: ignore[arg-type]
        _USER_ID,
        service="llm",
        provider="openrouter",
    )

    assert result == {
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4-6",
        "base_url": None,
        "secret": "sk-or-v1-plaintext",
    }
    assert client.rpc_calls == [("decrypt_secret", {"p_ciphertext": "ciphertext-abc"})]


async def test_get_decrypted_credential_not_found_raises() -> None:
    table = _FakeTable(select_rows=[])
    client = _FakeSupabaseClient(table)

    with pytest.raises(CredentialNotFound):
        await get_decrypted_credential(
            client,  # type: ignore[arg-type]
            _USER_ID,
            service="llm",
            provider="openrouter",
        )


async def test_delete_credential_calls_delete() -> None:
    table = _FakeTable(select_rows=[])
    client = _FakeSupabaseClient(table)

    await delete_credential(client, _USER_ID, service="llm", provider="openrouter")  # type: ignore[arg-type]

    assert table.delete_calls == 1
