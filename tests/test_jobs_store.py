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
    guess_company_name_from_url,
    lookup_registry_posting,
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
    def __init__(
        self,
        jobs: _FakeTable,
        job_snapshots: _FakeTable,
        *,
        job_registry_postings: _FakeTable | None = None,
        job_registry_companies: _FakeTable | None = None,
    ) -> None:
        self.jobs = jobs
        self.job_snapshots = job_snapshots
        self.job_registry_postings = job_registry_postings or _FakeTable(select_rows=[])
        self.job_registry_companies = job_registry_companies or _FakeTable(select_rows=[])

    def table(self, name: str) -> Any:
        if name == "jobs":
            return self.jobs
        if name == "job_snapshots":
            return self.job_snapshots
        if name == "job_registry_postings":
            return self.job_registry_postings
        if name == "job_registry_companies":
            return self.job_registry_companies
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


async def test_create_job_from_paste_accepts_a_different_source_kind() -> None:
    """outreach-v2-search-first.md Phase J: a real caller other than
    manual paste passes its own source_kind, and it's the value actually
    persisted -- not silently overridden back to "manual_paste"."""
    new_job = {"id": _JOB_ID, "canonical_url": "https://acme.example/jobs/1"}
    new_snapshot = {"id": _SNAPSHOT_ID, "job_id": _JOB_ID}
    jobs = _FakeTable(select_rows=[], insert_row=new_job)
    snapshots = _FakeTable(select_rows=[], insert_row=new_snapshot)
    client = _FakeSupabaseClient(jobs, snapshots)

    await create_job_from_paste(
        client,  # type: ignore[arg-type]
        **_paste_kwargs(),
        source_kind="url_ingest",
    )

    assert snapshots.insert_calls[0]["source_kind"] == "url_ingest"


async def test_lookup_registry_posting_returns_none_when_not_found() -> None:
    client = _FakeSupabaseClient(_FakeTable(select_rows=[]), _FakeTable(select_rows=[]))
    result = await lookup_registry_posting(client, "https://acme.example/jobs/1")  # type: ignore[arg-type]
    assert result is None


async def test_lookup_registry_posting_returns_the_real_row_with_company_name() -> None:
    posting_row = {
        "title": "Software Engineer",
        "location": "Remote",
        "jd_text": "A real, full job description.",
        "company_id": "company-1",
    }
    company_row = {"name": "Acme"}
    client = _FakeSupabaseClient(
        _FakeTable(select_rows=[]),
        _FakeTable(select_rows=[]),
        job_registry_postings=_FakeTable(select_rows=[posting_row]),
        job_registry_companies=_FakeTable(select_rows=[company_row]),
    )

    result = await lookup_registry_posting(client, "https://acme.example/jobs/1")  # type: ignore[arg-type]

    assert result == {
        "title": "Software Engineer",
        "location": "Remote",
        "company": "Acme",
        "jd_text": "A real, full job description.",
    }


async def test_lookup_registry_posting_handles_a_missing_company_id() -> None:
    posting_row = {
        "title": "Software Engineer",
        "location": None,
        "jd_text": "",
        "company_id": None,
    }
    client = _FakeSupabaseClient(
        _FakeTable(select_rows=[]),
        _FakeTable(select_rows=[]),
        job_registry_postings=_FakeTable(select_rows=[posting_row]),
    )

    result = await lookup_registry_posting(client, "https://acme.example/jobs/1")  # type: ignore[arg-type]

    assert result is not None
    assert result["company"] is None
    assert result["jd_text"] is None  # "" normalized to None, same as a missing value


def test_guess_company_name_from_url_uses_the_custom_domain() -> None:
    """A real URL this project's own live Firecrawl trial actually
    scraped (Coinbase's own custom careers domain)."""
    assert guess_company_name_from_url("https://www.coinbase.com/careers/positions/8105437") == (
        "Coinbase"
    )


def test_guess_company_name_from_url_handles_shared_ats_hosts() -> None:
    """Three real hosts confirmed against this project's own registry
    data -- the company slug lives in the path, not the domain."""
    assert (
        guess_company_name_from_url("https://job-boards.greenhouse.io/caylent/jobs/6010596004")
        == "Caylent"
    )
    assert (
        guess_company_name_from_url(
            "https://jobs.lever.co/margo-group/f0e3a179-2230-4047-adf7-68798aec838e"
        )
        == "Margo Group"
    )
    assert (
        guess_company_name_from_url(
            "https://jobs.ashbyhq.com/medraai/59fefef3-506e-4566-a2ae-b4ce5aa40e21"
        )
        == "Medraai"
    )


def test_guess_company_name_from_url_handles_a_workday_tenant_subdomain() -> None:
    """Not in the explicit shared-host set, but the plain domain-label
    fallback still gets it right by coincidence of how Workday addresses
    tenants."""
    assert (
        guess_company_name_from_url("https://amd.wd5.myworkdayjobs.com/en-US/External/job/1")
        == "Amd"
    )


def test_guess_company_name_from_url_falls_back_to_unknown_company() -> None:
    assert guess_company_name_from_url("https://job-boards.greenhouse.io/") == "Unknown Company"
    assert guess_company_name_from_url("not a url") == "Unknown Company"


def test_guess_company_name_from_url_handles_an_empty_string() -> None:
    assert guess_company_name_from_url("") == "Unknown Company"


def test_guess_company_name_from_url_handles_a_scheme_less_url() -> None:
    """urlparse gives no hostname at all for a bare string with no
    "://" -- the same parsing gap scrape_denylist.py had to guard
    against. Here it fails safe to the existing "unknown" fallback
    rather than a wrong guess, so no fix is needed -- just documenting
    the real behavior."""
    assert guess_company_name_from_url("acme.example/jobs/1") == "Unknown Company"
    assert guess_company_name_from_url("job-boards.greenhouse.io/acme/jobs/1") == "Unknown Company"


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
