"""Tests for the n8n->between-jobs registry seed import (Job Finder P1,
job-finder-port.md)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from scripts.import_job_registry_seed import (
    _BATCH_SIZE,
    _interval_to_postgres_literal,
    company_row_to_upsert,
    import_registry,
    job_row_to_upsert,
)

_NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)


def _company_row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "Acme Corp",
        "ats_type": "greenhouse",
        "slug": "acme",
        "api_base": "",
        "is_active": True,
        "poll_interval": timedelta(hours=6),
        "next_poll_at": _NOW,
        "last_polled_at": None,
        "etag": None,
        "last_modified": None,
        "consecutive_failures": 0,
        "tier": "probe",
        "relevant_yield": 0,
        "tier_weight": None,
    }
    base.update(overrides)
    return base


def _job_row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "external_id": "12345",
        "title": "Staff Engineer",
        "jd_text": "Build things.",
        "location": "Remote",
        "remote": True,
        "apply_url": "https://boards.greenhouse.io/acme/jobs/12345",
        "posted_at": _NOW,
        "status": "active",
        "first_seen": _NOW,
        "last_seen": _NOW,
        "closed_at": None,
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "salary_period": None,
        "sponsorship_signal": "unknown",
        "extracted_at": None,
        "extraction_version": None,
        "company_ats_type": "greenhouse",
        "company_slug": "acme",
        "company_api_base": "",
    }
    base.update(overrides)
    return base


def test_interval_to_postgres_literal_round_trips_seconds() -> None:
    assert _interval_to_postgres_literal(timedelta(hours=6)) == "21600.0 seconds"


def test_company_row_to_upsert_maps_every_field() -> None:
    row = _company_row(
        last_polled_at=_NOW,
        etag='W/"abc"',
        last_modified="Mon, 01 Jan 2026",
        consecutive_failures=2,
        tier="hot",
        relevant_yield=5,
        tier_weight=0.8,
    )

    result = company_row_to_upsert(row)

    assert result["name"] == "Acme Corp"
    assert result["ats_type"] == "greenhouse"
    assert result["slug"] == "acme"
    assert result["api_base"] == ""
    assert result["is_active"] is True
    assert result["poll_interval"] == "21600.0 seconds"
    assert result["next_poll_at"] == _NOW.isoformat()
    assert result["last_polled_at"] == _NOW.isoformat()
    assert result["etag"] == 'W/"abc"'
    assert result["consecutive_failures"] == 2
    assert result["tier"] == "hot"
    assert result["relevant_yield"] == 5
    assert result["tier_weight"] == 0.8
    assert "id" not in result


def test_company_row_to_upsert_handles_null_optionals() -> None:
    row = _company_row(api_base=None, last_polled_at=None, tier_weight=None)

    result = company_row_to_upsert(row)

    assert result["api_base"] == ""
    assert result["last_polled_at"] is None
    assert result["tier_weight"] is None


def test_job_row_to_upsert_maps_every_field_when_company_resolves() -> None:
    row = _job_row(
        salary_min=120000,
        salary_max=180000,
        salary_currency="USD",
        salary_period="year",
        sponsorship_signal="explicit_yes",
    )
    company_ids = {("greenhouse", "acme", ""): "company-uuid-1"}

    result = job_row_to_upsert(row, company_ids)

    assert result is not None
    assert result["company_id"] == "company-uuid-1"
    # Recomputed from the resolved (ats_type, slug, api_base) key, not
    # n8n's own row["board"] -- see job_row_to_upsert's own comment for
    # the real cross-company-collision bug this fixes.
    assert result["board"] == "greenhouse:acme:"
    assert result["external_id"] == "12345"
    assert result["title"] == "Staff Engineer"
    assert result["posted_at"] == _NOW.isoformat()
    assert result["status"] == "active"
    assert result["salary_min"] == 120000.0
    assert result["salary_max"] == 180000.0
    assert result["sponsorship_signal"] == "explicit_yes"
    assert "company_ats_type" not in result


def test_job_row_to_upsert_handles_null_optionals() -> None:
    row = _job_row(posted_at=None, closed_at=None, salary_min=None)
    company_ids = {("greenhouse", "acme", ""): "company-uuid-1"}

    result = job_row_to_upsert(row, company_ids)

    assert result is not None
    assert result["posted_at"] is None
    assert result["closed_at"] is None
    assert result["salary_min"] is None


def test_job_row_to_upsert_returns_none_when_company_unresolved() -> None:
    row = _job_row()
    result = job_row_to_upsert(row, {})
    assert result is None


def test_job_row_to_upsert_treats_null_api_base_as_empty_string_key() -> None:
    row = _job_row(company_api_base=None)
    company_ids = {("greenhouse", "acme", ""): "company-uuid-1"}

    result = job_row_to_upsert(row, company_ids)

    assert result is not None
    assert result["company_id"] == "company-uuid-1"


class _FakeN8nPool:
    def __init__(self, company_rows: list[dict[str, Any]], job_rows: list[dict[str, Any]]) -> None:
        self._company_rows = company_rows
        self._job_rows = job_rows

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self)


class _FakeConn:
    def __init__(self, pool: _FakeN8nPool) -> None:
        self._pool = pool

    def transaction(self, readonly: bool = False) -> _FakeTransaction:
        return _FakeTransaction()

    async def fetch(self, query: str) -> list[dict[str, Any]]:
        if "from companies" in query:
            return self._pool._company_rows
        return self._pool._job_rows


class _FakeAcquire:
    def __init__(self, pool: _FakeN8nPool) -> None:
        self._pool = pool

    async def __aenter__(self) -> _FakeConn:
        return _FakeConn(self._pool)

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self._range: tuple[int, int] | None = None

    def select(self, *_: Any, **__: Any) -> _FakeChainBuilder:
        return self

    def range(self, start: int, end: int) -> _FakeChainBuilder:
        """Mirrors real PostgREST `.range()` semantics closely enough to
        catch the real bug this file's own regression test exists for: an
        unranged `.select()` silently caps at PostgREST's default row
        limit, so any fake here that always returns every row regardless
        of range would never have caught it."""
        self._range = (start, end)
        return self

    async def execute(self) -> SimpleNamespace:
        if self._range is None:
            return SimpleNamespace(data=self._rows)
        start, end = self._range
        return SimpleNamespace(data=self._rows[start : end + 1])


class _FakeTable:
    def __init__(self, existing_rows: list[dict[str, Any]]) -> None:
        self.existing_rows = existing_rows
        self.upsert_calls: list[list[dict[str, Any]]] = []

    def upsert(self, rows: list[dict[str, Any]], on_conflict: str) -> _FakeChainBuilder:
        self.upsert_calls.append(rows)
        return _FakeChainBuilder(self.existing_rows)

    def select(self, *_: Any, **__: Any) -> _FakeChainBuilder:
        return _FakeChainBuilder(self.existing_rows)


class _FakeSupabaseClient:
    def __init__(self, companies_after_upsert: list[dict[str, Any]]) -> None:
        self.job_registry_companies = _FakeTable(companies_after_upsert)
        self.job_registry_postings = _FakeTable([])

    def table(self, name: str) -> Any:
        return {
            "job_registry_companies": self.job_registry_companies,
            "job_registry_postings": self.job_registry_postings,
        }[name]


async def test_import_registry_upserts_companies_then_resolves_postings() -> None:
    company_rows = [_company_row()]
    job_rows = [_job_row()]
    n8n_pool = _FakeN8nPool(company_rows, job_rows)
    supabase = _FakeSupabaseClient(
        companies_after_upsert=[
            {"id": "company-uuid-1", "ats_type": "greenhouse", "slug": "acme", "api_base": ""}
        ]
    )

    summary = await import_registry(n8n_pool, supabase, dry_run=False)  # type: ignore[arg-type]

    assert summary.companies_seen == 1
    assert summary.companies_upserted == 1
    assert summary.jobs_seen == 1
    assert summary.jobs_upserted == 1
    assert summary.jobs_skipped_unresolved_company == 0
    assert len(supabase.job_registry_companies.upsert_calls) == 1
    assert len(supabase.job_registry_postings.upsert_calls) == 1
    assert supabase.job_registry_postings.upsert_calls[0][0]["company_id"] == "company-uuid-1"


async def test_import_registry_counts_skipped_postings_with_no_resolvable_company() -> None:
    n8n_pool = _FakeN8nPool([_company_row()], [_job_row(company_slug="unknown-co")])
    supabase = _FakeSupabaseClient(
        companies_after_upsert=[
            {"id": "company-uuid-1", "ats_type": "greenhouse", "slug": "acme", "api_base": ""}
        ]
    )

    summary = await import_registry(n8n_pool, supabase, dry_run=False)  # type: ignore[arg-type]

    assert summary.jobs_skipped_unresolved_company == 1
    assert summary.jobs_upserted == 0


async def test_import_registry_dry_run_writes_nothing() -> None:
    n8n_pool = _FakeN8nPool([_company_row()], [_job_row()])
    supabase = _FakeSupabaseClient(companies_after_upsert=[])

    summary = await import_registry(n8n_pool, supabase, dry_run=True)  # type: ignore[arg-type]

    assert summary.companies_seen == 1
    assert summary.jobs_seen == 1
    assert supabase.job_registry_companies.upsert_calls == []
    assert supabase.job_registry_postings.upsert_calls == []


async def test_import_registry_resolves_postings_past_the_first_page_of_companies() -> None:
    """Regression test for a real bug caught only against the live ~16k-row
    registry: selecting companies back with a single unranged `.select()`
    silently capped at PostgREST's default row limit (1,000), so a live
    run resolved company ids for only the first page and skipped ~93% of
    postings as "unresolved company" even though every company existed.
    A fixture bigger than one batch (_BATCH_SIZE=500) is required to catch
    this -- every earlier test here uses 1 company, which a single-page
    fake could satisfy either way."""
    company_count = _BATCH_SIZE + 50
    company_rows = [
        _company_row(name=f"Company {i}", slug=f"company-{i}") for i in range(company_count)
    ]
    job_rows = [
        _job_row(
            external_id=str(i),
            company_slug=f"company-{i}",
        )
        for i in range(company_count)
    ]
    n8n_pool = _FakeN8nPool(company_rows, job_rows)
    companies_after_upsert = [
        {"id": f"uuid-{i}", "ats_type": "greenhouse", "slug": f"company-{i}", "api_base": ""}
        for i in range(company_count)
    ]
    supabase = _FakeSupabaseClient(companies_after_upsert=companies_after_upsert)

    summary = await import_registry(n8n_pool, supabase, dry_run=False)  # type: ignore[arg-type]

    assert summary.companies_seen == company_count
    assert summary.jobs_seen == company_count
    assert summary.jobs_skipped_unresolved_company == 0
    assert summary.jobs_upserted == company_count
