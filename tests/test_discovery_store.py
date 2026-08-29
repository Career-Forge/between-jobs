"""Tests for the read-only n8n job-registry facade (Horizon Sprint 4.1).

Fakes asyncpg's pool/connection/transaction protocol -- no real Postgres
connection in a unit test. Live verification against the real n8n
Postgres happens separately, not here.
"""

from __future__ import annotations

from typing import Any

from between_jobs.api.discovery_store import get_job_detail, search_jobs


class _FakeTransaction:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeTransaction:
        self._conn.readonly_transactions += 1
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _FakeConnection:
    def __init__(
        self, *, fetch_rows: list[dict[str, Any]], fetchrow_row: dict[str, Any] | None
    ) -> None:
        self.readonly_transactions = 0
        self._fetch_rows = fetch_rows
        self._fetchrow_row = fetchrow_row
        self.fetch_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchrow_calls: list[tuple[str, tuple[Any, ...]]] = []

    def transaction(self, *, readonly: bool = False) -> _FakeTransaction:
        assert readonly is True, "every discovery_store query must run read-only"
        return _FakeTransaction(self)

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.fetch_calls.append((query, args))
        return self._fetch_rows

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.fetchrow_calls.append((query, args))
        return self._fetchrow_row


class _FakeAcquire:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConnection:
        return self._conn

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _FakePool:
    def __init__(
        self,
        *,
        fetch_rows: list[dict[str, Any]] | None = None,
        fetchrow_row: dict[str, Any] | None = None,
    ) -> None:
        self.conn = _FakeConnection(
            fetch_rows=fetch_rows if fetch_rows is not None else [],
            fetchrow_row=fetchrow_row,
        )

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self.conn)


_JOB_ROW = {
    "id": 1,
    "title": "Staff AI Engineer",
    "company_name": "Acme",
    "location": "Remote",
    "remote": True,
    "apply_url": "https://acme.example/jobs/1",
    "posted_at": "2026-08-20T00:00:00Z",
}


async def test_search_jobs_with_a_query_uses_the_tsvector_match() -> None:
    pool = _FakePool(fetch_rows=[_JOB_ROW])

    results = await search_jobs(pool, query="python rag")

    assert results == [_JOB_ROW]
    assert pool.conn.readonly_transactions == 1
    query, args = pool.conn.fetch_calls[0]
    assert "jd_tsv @@ plainto_tsquery" in query
    assert args[0] == "python rag"


async def test_search_jobs_without_a_query_skips_the_tsvector_filter() -> None:
    pool = _FakePool(fetch_rows=[_JOB_ROW])

    results = await search_jobs(pool, query=None)

    assert results == [_JOB_ROW]
    query, _args = pool.conn.fetch_calls[0]
    assert "jd_tsv" not in query


async def test_search_jobs_caps_the_limit() -> None:
    pool = _FakePool(fetch_rows=[])

    await search_jobs(pool, query=None, limit=9999)

    _query, args = pool.conn.fetch_calls[0]
    assert args[-1] == 100


async def test_get_job_detail_returns_the_full_row_including_jd_text() -> None:
    row = {**_JOB_ROW, "jd_text": "We need a Python engineer."}
    pool = _FakePool(fetchrow_row=row)

    result = await get_job_detail(pool, 1)

    assert result == row
    assert pool.conn.readonly_transactions == 1


async def test_get_job_detail_returns_none_when_not_found() -> None:
    pool = _FakePool(fetchrow_row=None)

    result = await get_job_detail(pool, 999)

    assert result is None
