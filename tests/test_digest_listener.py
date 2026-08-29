"""Tests for the digest listener (Horizon Sprint 4.0) -- the first real
event_outbox subscriber.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from postgrest.exceptions import APIError

from between_jobs.api.digest_listener import handle_batch

_USER_ID = "00000000-0000-0000-0000-000000000001"
_APPLICATION_ID = "30000000-0000-0000-0000-000000000001"
_SNAPSHOT_ID = "20000000-0000-0000-0000-000000000002"

_APPLICATION_ROW = {
    "id": _APPLICATION_ID,
    "user_id": _USER_ID,
    "active_job_snapshot_id": _SNAPSHOT_ID,
}
_SNAPSHOT_ROW = {"id": _SNAPSHOT_ID, "title": "Staff Engineer", "company_name": "Acme"}


def _outbox_row(
    event_type: str, payload: dict[str, Any], *, event_id: str = "evt-1"
) -> dict[str, Any]:
    return {
        "id": event_id,
        "user_id": _USER_ID,
        "aggregate_id": _APPLICATION_ID,
        "event_type": event_type,
        "payload": payload,
    }


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTable:
    def __init__(self, select_rows: list[dict[str, Any]]) -> None:
        self.select_rows = select_rows

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)


class _FakeTodayItemsTable:
    def __init__(self, *, raise_unique_violation_on: set[str] | None = None) -> None:
        self.insert_calls: list[dict[str, Any]] = []
        self._raise_on = raise_unique_violation_on or set()

    def insert(self, data: dict[str, Any]) -> _InsertBuilder:
        return _InsertBuilder(self, data)


class _InsertBuilder:
    def __init__(self, table: _FakeTodayItemsTable, data: dict[str, Any]) -> None:
        self._table = table
        self._data = data

    async def execute(self) -> SimpleNamespace:
        if self._data["source_outbox_event_id"] in self._table._raise_on:
            raise APIError(
                {"message": "duplicate key", "code": "23505", "details": None, "hint": None}
            )
        self._table.insert_calls.append(self._data)
        return SimpleNamespace(data=[self._data])


class _FakeSupabaseClient:
    def __init__(
        self,
        *,
        applications: list[dict[str, Any]] | None = None,
        job_snapshots: list[dict[str, Any]] | None = None,
        today_items: _FakeTodayItemsTable | None = None,
    ) -> None:
        self._applications = _FakeTable(
            applications if applications is not None else [_APPLICATION_ROW]
        )
        self._job_snapshots = _FakeTable(
            job_snapshots if job_snapshots is not None else [_SNAPSHOT_ROW]
        )
        self.today_items = today_items or _FakeTodayItemsTable()

    def table(self, name: str) -> Any:
        return {
            "applications": self._applications,
            "job_snapshots": self._job_snapshots,
            "today_items": self.today_items,
        }[name]


async def test_application_created_produces_a_job_tracked_item() -> None:
    supabase = _FakeSupabaseClient()
    row = _outbox_row("application.created.v1", {"job_id": "job-1", "status": "saved"})

    inserted = await handle_batch(supabase, [row])  # type: ignore[arg-type]

    assert inserted == 1
    item = supabase.today_items.insert_calls[0]
    assert item["kind"] == "job_tracked"
    assert "Staff Engineer @ Acme" in item["headline"]
    assert item["source_outbox_event_id"] == "evt-1"
    assert item["application_id"] == _APPLICATION_ID
    assert item["user_id"] == _USER_ID


async def test_artifact_generated_produces_a_resume_ready_item_with_score() -> None:
    supabase = _FakeSupabaseClient()
    row = _outbox_row("artifact.generated.v1", {"final_score": 72.0, "warnings": []})

    inserted = await handle_batch(supabase, [row])  # type: ignore[arg-type]

    assert inserted == 1
    item = supabase.today_items.insert_calls[0]
    assert item["kind"] == "resume_ready"
    assert "Staff Engineer @ Acme" in item["headline"]
    assert item["detail"] == "ATS score 72/100"


async def test_artifact_generation_failed_produces_a_resume_failed_item_with_warnings() -> None:
    supabase = _FakeSupabaseClient()
    row = _outbox_row(
        "artifact.generation_failed.v1",
        {"final_score": None, "warnings": ["Fit score too low."]},
    )

    inserted = await handle_batch(supabase, [row])  # type: ignore[arg-type]

    assert inserted == 1
    item = supabase.today_items.insert_calls[0]
    assert item["kind"] == "resume_failed"
    assert item["detail"] == "Fit score too low."


async def test_stage_changed_produces_a_stage_changed_item() -> None:
    supabase = _FakeSupabaseClient()
    row = _outbox_row(
        "application.stage_changed.v1",
        {"application_id": _APPLICATION_ID, "old_stage": "saved", "new_stage": "applied"},
    )

    inserted = await handle_batch(supabase, [row])  # type: ignore[arg-type]

    assert inserted == 1
    item = supabase.today_items.insert_calls[0]
    assert item["kind"] == "stage_changed"
    assert "applied" in item["headline"]


async def test_unrecognized_event_type_is_skipped() -> None:
    supabase = _FakeSupabaseClient()
    row = _outbox_row("provider.validation_changed.v1", {})

    inserted = await handle_batch(supabase, [row])  # type: ignore[arg-type]

    assert inserted == 0
    assert supabase.today_items.insert_calls == []


async def test_missing_application_is_skipped_not_an_error() -> None:
    supabase = _FakeSupabaseClient(applications=[])
    row = _outbox_row("application.created.v1", {})

    inserted = await handle_batch(supabase, [row])  # type: ignore[arg-type]

    assert inserted == 0


async def test_missing_snapshot_falls_back_to_untitled_but_still_inserts() -> None:
    supabase = _FakeSupabaseClient(job_snapshots=[])
    row = _outbox_row("application.created.v1", {})

    inserted = await handle_batch(supabase, [row])  # type: ignore[arg-type]

    assert inserted == 1
    assert "Untitled" in supabase.today_items.insert_calls[0]["headline"]


async def test_duplicate_outbox_event_is_idempotently_skipped() -> None:
    today_items = _FakeTodayItemsTable(raise_unique_violation_on={"evt-1"})
    supabase = _FakeSupabaseClient(today_items=today_items)
    row = _outbox_row("application.created.v1", {})

    inserted = await handle_batch(supabase, [row])  # type: ignore[arg-type]

    assert inserted == 0
    assert today_items.insert_calls == []


async def test_batch_processes_every_row_and_counts_only_successful_inserts() -> None:
    supabase = _FakeSupabaseClient()
    rows = [
        _outbox_row("application.created.v1", {}, event_id="evt-1"),
        _outbox_row("provider.validation_changed.v1", {}, event_id="evt-2"),
        _outbox_row("application.stage_changed.v1", {"new_stage": "applied"}, event_id="evt-3"),
    ]

    inserted = await handle_batch(supabase, rows)  # type: ignore[arg-type]

    assert inserted == 2
    assert len(supabase.today_items.insert_calls) == 2
