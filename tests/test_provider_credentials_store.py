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
    list_unvalidated_user_ids,
    mark_credential_unvalidated,
    save_credential,
)

_USER_ID = "00000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]], columns: str = "*") -> None:
        # Like PostgREST, an answer carries only the columns the query asked for: a column the
        # code reads but never selected is missing from the row, not quietly present.
        if columns.strip() == "*":
            self._rows = rows
        else:
            wanted = {column.strip() for column in columns.split(",")}
            self._rows = [{k: v for k, v in row.items() if k in wanted} for row in rows]

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
        self.selected: list[str] = []

    def select(self, columns: str = "*", **__: Any) -> _ChainBuilder:
        self.selected.append(columns)
        return _ChainBuilder(self.select_rows, columns)

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
        decrypt_output: str | dict[str, str] | None = None,
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
            if isinstance(self.decrypt_output, dict):
                return _FakeRpcBuilder(self.decrypt_output[params["p_ciphertext"]])
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
                "scope": None,
                "is_validated": True,
                "secret_encrypted": "ciphertext-abc",
                "secret_2_encrypted": None,
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
        "scope": None,
        "is_validated": True,
        "secret": "sk-or-v1-plaintext",
        "secret_2": None,
    }
    assert client.rpc_calls == [("decrypt_secret", {"p_ciphertext": "ciphertext-abc"})]


async def test_get_decrypted_credential_returns_the_real_granted_scope() -> None:
    """Regression test for a real, adversarially-confirmed gap: `scope`
    was written on save but never selected back here, silently making
    it impossible for the reply-checker poller (or any caller) to ever
    learn what Gmail scope was actually granted."""
    table = _FakeTable(
        select_rows=[
            {
                "provider": "gmail",
                "model": None,
                "base_url": None,
                "scope": "https://www.googleapis.com/auth/gmail.compose "
                "https://www.googleapis.com/auth/gmail.readonly",
                "secret_encrypted": "ciphertext-abc",
                "secret_2_encrypted": None,
            }
        ]
    )
    client = _FakeSupabaseClient(table, decrypt_output="refresh-token-value")

    result = await get_decrypted_credential(
        client,  # type: ignore[arg-type]
        _USER_ID,
        service="oauth",
        provider="gmail",
    )

    assert result["scope"] == (
        "https://www.googleapis.com/auth/gmail.compose "
        "https://www.googleapis.com/auth/gmail.readonly"
    )


async def test_save_credential_encrypts_secret_2_when_given() -> None:
    saved_row = {"id": "cred-1", "service": "search", "provider": "adzuna"}
    table = _FakeTable(select_rows=[], upsert_row=saved_row)
    client = _FakeSupabaseClient(
        table, encrypt_output="ciphertext-1"
    )  # same output for both calls -- wiring test, not distinctness

    await save_credential(
        client,  # type: ignore[arg-type]
        _USER_ID,
        service="search",
        provider="adzuna",
        secret="app-id-value",
        secret_2="app-key-value",
    )

    encrypt_calls = [c for c in client.rpc_calls if c[0] == "encrypt_secret"]
    assert len(encrypt_calls) == 2
    assert {c[1]["p_plaintext"] for c in encrypt_calls} == {"app-id-value", "app-key-value"}
    data, _ = table.upsert_calls[0]
    assert data["secret_2_encrypted"] == "ciphertext-1"


async def test_save_credential_leaves_secret_2_null_when_not_given() -> None:
    table = _FakeTable(select_rows=[], upsert_row={"id": "cred-1"})
    client = _FakeSupabaseClient(table, encrypt_output="ciphertext-abc")

    await save_credential(
        client,  # type: ignore[arg-type]
        _USER_ID,
        service="llm",
        provider="openrouter",
        secret="sk-or-v1-plaintext",
    )

    encrypt_calls = [c for c in client.rpc_calls if c[0] == "encrypt_secret"]
    assert len(encrypt_calls) == 1  # secret_2 never touches encrypt_secret when absent
    data, _ = table.upsert_calls[0]
    assert data["secret_2_encrypted"] is None


async def test_save_credential_persists_scope_when_given() -> None:
    """Gmail reply/status parsing (outreach-v2-search-first.md) -- the
    real granted scope string, captured at OAuth-callback time, needs
    to survive the upsert as plaintext (never encrypted -- it isn't a
    secret)."""
    table = _FakeTable(select_rows=[], upsert_row={"id": "cred-1"})
    client = _FakeSupabaseClient(table, encrypt_output="ciphertext-abc")

    await save_credential(
        client,  # type: ignore[arg-type]
        _USER_ID,
        service="oauth",
        provider="gmail",
        secret="refresh-token-value",
        scope="https://www.googleapis.com/auth/gmail.compose "
        "https://www.googleapis.com/auth/gmail.readonly",
    )

    data, _ = table.upsert_calls[0]
    assert data["scope"] == (
        "https://www.googleapis.com/auth/gmail.compose "
        "https://www.googleapis.com/auth/gmail.readonly"
    )
    # scope must never be run through encrypt_secret -- a review caught
    # that only checking the stored value (and not this) would still
    # pass under a regression that encrypted scope and discarded the
    # ciphertext, still writing the raw value alongside a real, unwanted
    # encrypt_secret RPC call carrying it as plaintext input.
    assert client.rpc_calls == [("encrypt_secret", {"p_plaintext": "refresh-token-value"})]


async def test_save_credential_leaves_scope_null_when_not_given() -> None:
    table = _FakeTable(select_rows=[], upsert_row={"id": "cred-1"})
    client = _FakeSupabaseClient(table, encrypt_output="ciphertext-abc")

    await save_credential(
        client,  # type: ignore[arg-type]
        _USER_ID,
        service="llm",
        provider="openrouter",
        secret="sk-or-v1-plaintext",
    )

    data, _ = table.upsert_calls[0]
    assert data["scope"] is None


async def test_get_decrypted_credential_decrypts_secret_2_when_present() -> None:
    table = _FakeTable(
        select_rows=[
            {
                "provider": "adzuna",
                "model": None,
                "base_url": None,
                "secret_encrypted": "cipher-id",
                "secret_2_encrypted": "cipher-key",
            }
        ]
    )
    client = _FakeSupabaseClient(
        table, decrypt_output={"cipher-id": "app-id-value", "cipher-key": "app-key-value"}
    )

    result = await get_decrypted_credential(
        client,  # type: ignore[arg-type]
        _USER_ID,
        service="search",
        provider="adzuna",
    )

    assert result["secret"] == "app-id-value"
    assert result["secret_2"] == "app-key-value"


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


# -- a connection Google stopped honouring: is_validated is the record ---------------------------


class _RecordingQuery:
    """Remembers the column filters a query was built with, and what it was asked to select or
    update, so a test can check that every query is scoped to the person it was made for."""

    def __init__(self, table: _RecordingTable, rows: list[dict[str, Any]]) -> None:
        self._table = table
        self._rows = rows

    def eq(self, column: str, value: Any) -> _RecordingQuery:
        self._table.filters.append((column, value))
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _RecordingTable:
    def __init__(self, *, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.filters: list[tuple[str, Any]] = []
        self.selected: str | None = None
        self.updated: dict[str, Any] | None = None

    def select(self, columns: str) -> _RecordingQuery:
        self.selected = columns
        return _RecordingQuery(self, self.rows)

    def update(self, data: dict[str, Any]) -> _RecordingQuery:
        self.updated = data
        return _RecordingQuery(self, self.rows)


class _TableOnlyClient:
    def __init__(self, table: _RecordingTable) -> None:
        self._table = table

    def table(self, name: str) -> _RecordingTable:
        assert name == "provider_credentials"
        return self._table


async def test_get_decrypted_credential_reports_a_connection_that_was_flagged() -> None:
    table = _FakeTable(
        select_rows=[
            {
                "provider": "gmail",
                "model": None,
                "base_url": None,
                "scope": "https://www.googleapis.com/auth/gmail.readonly",
                "is_validated": False,
                "secret_encrypted": "ciphertext-abc",
                "secret_2_encrypted": None,
            }
        ]
    )
    client = _FakeSupabaseClient(table, decrypt_output="refresh-token-value")

    result = await get_decrypted_credential(
        client,  # type: ignore[arg-type]
        _USER_ID,
        service="oauth",
        provider="gmail",
    )

    assert result["is_validated"] is False
    assert result["secret"] == "refresh-token-value"  # flagging never discards the token


async def test_get_decrypted_credential_asks_the_database_for_is_validated() -> None:
    """The fake above answers with only the selected columns, as PostgREST does, so dropping
    `is_validated` from the query would already turn a flagged connection back into a validated
    one there. This pins the query itself, so the failure names what went missing."""
    table = _FakeTable(
        select_rows=[
            {
                "provider": "gmail",
                "model": None,
                "base_url": None,
                "scope": "https://www.googleapis.com/auth/gmail.readonly",
                "is_validated": False,
                "secret_encrypted": "ciphertext-abc",
                "secret_2_encrypted": None,
            }
        ]
    )
    client = _FakeSupabaseClient(table, decrypt_output="refresh-token-value")

    await get_decrypted_credential(
        client,  # type: ignore[arg-type]
        _USER_ID,
        service="oauth",
        provider="gmail",
    )

    [selected] = table.selected
    assert "is_validated" in [column.strip() for column in selected.split(",")]


async def test_marking_a_credential_unvalidated_is_one_conditional_update_for_that_person() -> None:
    table = _RecordingTable(rows=[{"id": "cred-1"}])

    changed = await mark_credential_unvalidated(
        _TableOnlyClient(table),  # type: ignore[arg-type]
        _USER_ID,
        service="oauth",
        provider="gmail",
    )

    assert changed is True
    # Only is_validated is written (the secret and the scope are not touched), and only for this
    # person's row that is still validated: that condition is what makes it answer "was it me".
    assert table.updated == {"is_validated": False}
    assert sorted(table.filters, key=str) == sorted(
        [
            ("user_id", _USER_ID),
            ("service", "oauth"),
            ("provider", "gmail"),
            ("is_validated", True),
        ],
        key=str,
    )


async def test_marking_a_credential_that_is_already_flagged_or_missing_changes_nothing() -> None:
    table = _RecordingTable(rows=[])  # the conditional update matched no row

    changed = await mark_credential_unvalidated(
        _TableOnlyClient(table),  # type: ignore[arg-type]
        _USER_ID,
        service="oauth",
        provider="gmail",
    )

    assert changed is False


async def test_listing_the_unvalidated_returns_lower_case_ids_and_asks_only_for_them() -> None:
    table = _RecordingTable(
        rows=[
            {"user_id": "AAAAAAAA-0000-0000-0000-000000000001"},
            {"user_id": "bbbbbbbb-0000-0000-0000-000000000002"},
        ]
    )

    ids = await list_unvalidated_user_ids(
        _TableOnlyClient(table),  # type: ignore[arg-type]
        service="oauth",
        provider="gmail",
    )

    assert ids == frozenset(
        {"aaaaaaaa-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000002"}
    )
    assert table.selected == "user_id"  # never a secret
    assert sorted(table.filters, key=str) == sorted(
        [("service", "oauth"), ("provider", "gmail"), ("is_validated", False)], key=str
    )


async def test_listing_the_unvalidated_with_nobody_flagged_is_empty() -> None:
    ids = await list_unvalidated_user_ids(
        _TableOnlyClient(_RecordingTable(rows=[])),  # type: ignore[arg-type]
        service="oauth",
        provider="gmail",
    )
    assert ids == frozenset()
