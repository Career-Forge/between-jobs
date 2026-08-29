"""Tests for the outbox worker (Sprint 2.6d)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from between_jobs.api.outbox_store import run_worker_once

_ROW = {"id": "40000000-0000-0000-0000-000000000001", "event_type": "application.stage_changed.v1"}


class _FakeRpcBuilder:
    def __init__(self, data: list[dict[str, Any]]) -> None:
        self._data = data

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._data)


class _FakeSupabaseClient:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.rpc_calls: list[tuple[str, dict[str, Any]]] = []

    def rpc(self, fn: str, params: dict[str, Any]) -> _FakeRpcBuilder:
        self.rpc_calls.append((fn, params))
        return _FakeRpcBuilder(self._rows)


async def test_run_worker_once_calls_claim_and_publish_with_batch_size() -> None:
    client = _FakeSupabaseClient([_ROW])

    result = await run_worker_once(client, batch_size=5)  # type: ignore[arg-type]

    assert result == [_ROW]
    assert client.rpc_calls == [("claim_and_publish_outbox_batch", {"p_limit": 5})]


async def test_run_worker_once_returns_empty_when_nothing_to_claim() -> None:
    client = _FakeSupabaseClient([])
    result = await run_worker_once(client)  # type: ignore[arg-type]
    assert result == []


async def test_run_worker_once_dispatches_claimed_rows_to_every_listener() -> None:
    client = _FakeSupabaseClient([_ROW])
    calls_a: list[list[dict[str, Any]]] = []
    calls_b: list[list[dict[str, Any]]] = []

    async def listener_a(_supabase: Any, rows: list[dict[str, Any]]) -> None:
        calls_a.append(rows)

    async def listener_b(_supabase: Any, rows: list[dict[str, Any]]) -> None:
        calls_b.append(rows)

    result = await run_worker_once(client, listeners=[listener_a, listener_b])  # type: ignore[arg-type]

    assert result == [_ROW]
    assert calls_a == [[_ROW]]
    assert calls_b == [[_ROW]]


async def test_run_worker_once_skips_listeners_when_nothing_claimed() -> None:
    client = _FakeSupabaseClient([])
    calls: list[list[dict[str, Any]]] = []

    async def listener(_supabase: Any, rows: list[dict[str, Any]]) -> None:
        calls.append(rows)

    await run_worker_once(client, listeners=[listener])  # type: ignore[arg-type]

    assert calls == []


async def test_run_worker_once_with_no_listeners_still_claims() -> None:
    client = _FakeSupabaseClient([_ROW])
    result = await run_worker_once(client)  # type: ignore[arg-type]
    assert result == [_ROW]
