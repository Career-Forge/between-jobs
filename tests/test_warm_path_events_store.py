"""Tests for events warm-path persistence (outreach-contactfinder.md
Phase D) -- per-user, immutable-run shape mirroring company_intel_store.py's
own test suite.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from between_jobs.api.warm_path_events import WarmPathEvent
from between_jobs.api.warm_path_events_store import create_run, get_events_for_run, get_latest_run

_USER_ID = "00000000-0000-0000-0000-000000000001"
_APPLICATION_ID = "30000000-0000-0000-0000-000000000001"
_RUN_ID = "70000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def order(self, *_: Any, **__: Any) -> _ChainBuilder:
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
        self.insert_calls: list[Any] = []
        self._next_id = 1

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def insert(self, data: Any) -> _ChainBuilder:
        self.insert_calls.append(data)
        if isinstance(data, list):
            rows = []
            for row in data:
                rows.append({"id": f"row-{self._next_id}", **row})
                self._next_id += 1
        else:
            rows = [self.insert_row] if self.insert_row is not None else [{**data, "id": "row-1"}]
        self.select_rows.extend(rows)
        return _ChainBuilder(rows)


class _FakeSupabaseClient:
    def __init__(
        self,
        *,
        runs: _FakeTable | None = None,
        events: _FakeTable | None = None,
    ) -> None:
        self.warm_path_runs = runs or _FakeTable(
            select_rows=[], insert_row={"id": _RUN_ID, "application_id": _APPLICATION_ID}
        )
        self.warm_path_events = events or _FakeTable(select_rows=[])

    def table(self, name: str) -> Any:
        return {
            "warm_path_runs": self.warm_path_runs,
            "warm_path_events": self.warm_path_events,
        }[name]


def _event() -> WarmPathEvent:
    return WarmPathEvent(
        event_name="Data+AI Summit",
        event_url="https://conf.example/data-ai-summit",
        event_date="2026-10-01",
        location="New York",
        certainty="confirmed_speaker",
        speaker_name="Jane Doe",
        speaker_title="Staff Engineer",
        talk_topic="Scaling ML infra",
        source_title="Jane Doe to speak at Data+AI Summit",
        source_snippet="Jane Doe, Staff Engineer at Acme, will present on scaling ML infra.",
    )


async def test_create_run_inserts_the_run_and_its_events() -> None:
    supabase = _FakeSupabaseClient()

    run = await create_run(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        application_id=_APPLICATION_ID,
        company_name="Acme",
        events=[_event()],
        providers_used=["you_com"],
        warnings=[],
    )

    assert run["id"] == _RUN_ID
    assert len(supabase.warm_path_events.insert_calls) == 1
    inserted = supabase.warm_path_events.insert_calls[0]
    assert inserted[0]["run_id"] == _RUN_ID
    assert inserted[0]["event_name"] == "Data+AI Summit"
    assert inserted[0]["speaker_name"] == "Jane Doe"


async def test_create_run_with_no_events_skips_the_events_insert() -> None:
    supabase = _FakeSupabaseClient()

    await create_run(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        application_id=_APPLICATION_ID,
        company_name="Acme",
        events=[],
        providers_used=[],
        warnings=["You.com key missing"],
    )

    assert supabase.warm_path_events.insert_calls == []


async def test_get_latest_run_returns_none_when_nothing_exists() -> None:
    supabase = _FakeSupabaseClient(runs=_FakeTable(select_rows=[]))
    result = await get_latest_run(supabase, _USER_ID, _APPLICATION_ID)  # type: ignore[arg-type]
    assert result is None


async def test_get_events_for_run_returns_the_rows() -> None:
    rows = [{"id": "event-1", "event_name": "Data+AI Summit"}]
    supabase = _FakeSupabaseClient(events=_FakeTable(select_rows=rows))

    result = await get_events_for_run(supabase, _RUN_ID)  # type: ignore[arg-type]

    assert result == rows
