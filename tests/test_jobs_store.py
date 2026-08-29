"""Tests for job/job-snapshot persistence (Sprint 2.6b)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from between_jobs.api.jobs_store import (
    JobNotFound,
    SnapshotNotFound,
    create_job_from_paste,
    get_job,
    get_snapshot,
)

_JOB_ID = "20000000-0000-0000-0000-000000000001"
_SNAPSHOT_ID = "20000000-0000-0000-0000-000000000002"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def limit(self, *_: Any, **__: Any) -> _ChainBuilder:
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

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def insert(self, data: dict[str, Any]) -> _ChainBuilder:
        self.insert_calls.append(data)
        rows = [self.insert_row] if self.insert_row is not None else []
        return _ChainBuilder(rows)


class _FakeSupabaseClient:
    def __init__(self, jobs: _FakeTable, job_snapshots: _FakeTable) -> None:
        self.jobs = jobs
        self.job_snapshots = job_snapshots

    def table(self, name: str) -> Any:
        if name == "jobs":
            return self.jobs
        if name == "job_snapshots":
            return self.job_snapshots
        raise AssertionError(f"unexpected table: {name}")


def _paste_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "title": "Staff Engineer",
        "company_name": "Acme",
        "description_text": "Build things.",
        "canonical_url": "https://acme.example/jobs/1",
        "location_text": "Remote",
    }
    kwargs.update(overrides)
    return kwargs


async def test_create_job_from_paste_creates_job_and_snapshot_when_neither_exist() -> None:
    new_job = {"id": _JOB_ID, "canonical_url": "https://acme.example/jobs/1"}
    new_snapshot = {"id": _SNAPSHOT_ID, "job_id": _JOB_ID}
    jobs = _FakeTable(select_rows=[], insert_row=new_job)
    snapshots = _FakeTable(select_rows=[], insert_row=new_snapshot)
    client = _FakeSupabaseClient(jobs, snapshots)

    job, snapshot = await create_job_from_paste(client, **_paste_kwargs())  # type: ignore[arg-type]

    assert job == new_job
    assert snapshot == new_snapshot
    assert len(jobs.insert_calls) == 1
    assert len(snapshots.insert_calls) == 1
    assert snapshots.insert_calls[0]["job_id"] == _JOB_ID
    assert snapshots.insert_calls[0]["source_kind"] == "manual_paste"


async def test_create_job_from_paste_reuses_job_found_by_canonical_url() -> None:
    existing_job = {"id": _JOB_ID, "canonical_url": "https://acme.example/jobs/1"}
    new_snapshot = {"id": _SNAPSHOT_ID, "job_id": _JOB_ID}
    jobs = _FakeTable(select_rows=[existing_job])
    snapshots = _FakeTable(select_rows=[], insert_row=new_snapshot)
    client = _FakeSupabaseClient(jobs, snapshots)

    job, _snapshot = await create_job_from_paste(client, **_paste_kwargs())  # type: ignore[arg-type]

    assert job == existing_job
    assert jobs.insert_calls == []
    assert len(snapshots.insert_calls) == 1


async def test_create_job_from_paste_dedupes_identical_content_under_the_same_job() -> None:
    existing_job = {"id": _JOB_ID}
    existing_snapshot = {"id": _SNAPSHOT_ID, "job_id": _JOB_ID, "content_hash": "whatever"}
    jobs = _FakeTable(select_rows=[existing_job])
    snapshots = _FakeTable(select_rows=[existing_snapshot])
    client = _FakeSupabaseClient(jobs, snapshots)

    _job, snapshot = await create_job_from_paste(client, **_paste_kwargs())  # type: ignore[arg-type]

    assert snapshot == existing_snapshot
    assert jobs.insert_calls == []
    assert snapshots.insert_calls == []


async def test_create_job_from_paste_without_a_url_always_creates_a_new_job() -> None:
    new_job = {"id": _JOB_ID, "canonical_url": None}
    new_snapshot = {"id": _SNAPSHOT_ID, "job_id": _JOB_ID}
    # select_rows is non-empty here to prove the lookup is skipped entirely
    # when there's no URL to key it on -- if the store called the lookup
    # anyway, it would wrongly "find" this row and skip creating a job.
    jobs = _FakeTable(select_rows=[{"id": "wrong-job"}], insert_row=new_job)
    snapshots = _FakeTable(select_rows=[], insert_row=new_snapshot)
    client = _FakeSupabaseClient(jobs, snapshots)

    job, _snapshot = await create_job_from_paste(
        client,  # type: ignore[arg-type]
        **_paste_kwargs(canonical_url=None),
    )

    assert job == new_job
    assert len(jobs.insert_calls) == 1
    assert jobs.insert_calls[0]["canonical_url"] is None


async def test_get_job_found() -> None:
    row = {"id": _JOB_ID}
    client = _FakeSupabaseClient(_FakeTable(select_rows=[row]), _FakeTable(select_rows=[]))
    result = await get_job(client, _JOB_ID)  # type: ignore[arg-type]
    assert result == row


async def test_get_job_not_found_raises() -> None:
    client = _FakeSupabaseClient(_FakeTable(select_rows=[]), _FakeTable(select_rows=[]))
    with pytest.raises(JobNotFound):
        await get_job(client, _JOB_ID)  # type: ignore[arg-type]


async def test_get_snapshot_found() -> None:
    row = {"id": _SNAPSHOT_ID}
    client = _FakeSupabaseClient(_FakeTable(select_rows=[]), _FakeTable(select_rows=[row]))
    result = await get_snapshot(client, _SNAPSHOT_ID)  # type: ignore[arg-type]
    assert result == row


async def test_get_snapshot_not_found_raises() -> None:
    client = _FakeSupabaseClient(_FakeTable(select_rows=[]), _FakeTable(select_rows=[]))
    with pytest.raises(SnapshotNotFound):
        await get_snapshot(client, _SNAPSHOT_ID)  # type: ignore[arg-type]
