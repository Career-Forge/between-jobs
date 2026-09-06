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


class _FakeRpcBuilder:
    def __init__(self, table: Any, params: dict[str, Any]) -> None:
        self._table = table
        self._params = params

    async def execute(self) -> SimpleNamespace:
        if self._params["p_source_outbox_event_id"] in self._table.raise_on:
            raise APIError(
                {"message": "duplicate key", "code": "23505", "details": None, "hint": None}
            )
        self._table.calls.append(self._params)
        return SimpleNamespace(data=[{"id": "today-item-1", **self._params}])


class _FakeHighFitJobRpc:
    def __init__(self, *, raise_unique_violation_on: set[str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.raise_on = raise_unique_violation_on or set()


class _FakeStatusProposalRpc:
    def __init__(self, *, raise_unique_violation_on: set[str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.raise_on = raise_unique_violation_on or set()


class _FakeChannelIdentitiesTable:
    def __init__(self, chat_id: int | None) -> None:
        self._rows = [{"external_subject": str(chat_id)}] if chat_id is not None else []

    def select(self, *_: Any, **__: Any) -> _FakeChannelIdentitiesTable:
        return self

    def eq(self, *_: Any, **__: Any) -> _FakeChannelIdentitiesTable:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTelegramClient:
    def __init__(self, *, raise_on_send: bool = False) -> None:
        self.sent: list[tuple[int, str]] = []
        self._raise_on_send = raise_on_send

    async def send_message(self, chat_id: int, text: str, **_: Any) -> None:
        if self._raise_on_send:
            raise RuntimeError("telegram send failed")
        self.sent.append((chat_id, text))


class _FakeSupabaseClient:
    def __init__(
        self,
        *,
        applications: list[dict[str, Any]] | None = None,
        job_snapshots: list[dict[str, Any]] | None = None,
        today_items: _FakeTodayItemsTable | None = None,
        high_fit_job_rpc: _FakeHighFitJobRpc | None = None,
        status_proposal_rpc: _FakeStatusProposalRpc | None = None,
        telegram_chat_id: int | None = None,
        saved_search_exists: bool = True,
        status_proposal_exists: bool = True,
    ) -> None:
        self._applications = _FakeTable(
            applications if applications is not None else [_APPLICATION_ROW]
        )
        self._job_snapshots = _FakeTable(
            job_snapshots if job_snapshots is not None else [_SNAPSHOT_ROW]
        )
        self.today_items = today_items or _FakeTodayItemsTable()
        self.high_fit_job_rpc = high_fit_job_rpc or _FakeHighFitJobRpc()
        self.status_proposal_rpc = status_proposal_rpc or _FakeStatusProposalRpc()
        self._channel_identities = _FakeChannelIdentitiesTable(telegram_chat_id)
        self._saved_searches = _FakeTable(
            [{"id": _SAVED_SEARCH_ID, "user_id": _USER_ID}] if saved_search_exists else []
        )
        self._application_status_proposals = _FakeTable(
            [{"id": _PROPOSAL_ID, "user_id": _USER_ID, "status": "pending"}]
            if status_proposal_exists
            else []
        )

    def table(self, name: str) -> Any:
        return {
            "applications": self._applications,
            "job_snapshots": self._job_snapshots,
            "today_items": self.today_items,
            "channel_identities": self._channel_identities,
            "saved_searches": self._saved_searches,
            "application_status_proposals": self._application_status_proposals,
        }[name]

    def rpc(self, fn: str, params: dict[str, Any]) -> _FakeRpcBuilder:
        if fn == "insert_high_fit_job_today_item":
            return _FakeRpcBuilder(self.high_fit_job_rpc, params)
        if fn == "insert_status_proposal_today_item":
            return _FakeRpcBuilder(self.status_proposal_rpc, params)
        raise AssertionError(f"unexpected rpc: {fn}")


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


_SAVED_SEARCH_ID = "40000000-0000-0000-0000-000000000001"


def _job_match_row(*, event_id: str = "evt-match-1", **payload_overrides: Any) -> dict[str, Any]:
    payload = {
        "apply_url": "https://boards.greenhouse.io/acme/jobs/1",
        "title": "Backend Engineer",
        "company": "Acme",
        "location": "Remote",
        "score100": 78,
        "bin": "Strong",
        "one_liner": "Strong fit -- backend skills align",
        "snippet": "Build things.",
        "provider": "registry",
        **payload_overrides,
    }
    return {
        "id": event_id,
        "user_id": _USER_ID,
        "aggregate_id": _SAVED_SEARCH_ID,
        "event_type": "job_registry.match_found.v1",
        "payload": payload,
    }


async def test_job_match_found_produces_a_high_fit_job_item_without_touching_applications() -> None:
    supabase = _FakeSupabaseClient(applications=[])  # no application exists at all
    row = _job_match_row()

    inserted = await handle_batch(supabase, [row])  # type: ignore[arg-type]

    assert inserted == 1
    call = supabase.high_fit_job_rpc.calls[0]
    assert call["p_user_id"] == _USER_ID
    assert call["p_saved_search_id"] == _SAVED_SEARCH_ID
    assert call["p_apply_url"] == "https://boards.greenhouse.io/acme/jobs/1"
    assert call["p_score100"] == 78
    assert call["p_bin"] == "Strong"
    assert "Backend Engineer" in call["p_headline"]
    assert "Acme" in call["p_headline"]
    assert call["p_detail"] == "Strong fit -- backend skills align"


async def test_job_match_found_headline_omits_company_when_absent() -> None:
    supabase = _FakeSupabaseClient(applications=[])
    row = _job_match_row(company=None)

    await handle_batch(supabase, [row])  # type: ignore[arg-type]

    headline = supabase.high_fit_job_rpc.calls[0]["p_headline"]
    assert "Backend Engineer" in headline
    assert " @ " not in headline


async def test_job_match_found_is_idempotent_on_source_outbox_event_id() -> None:
    rpc = _FakeHighFitJobRpc(raise_unique_violation_on={"evt-match-1"})
    supabase = _FakeSupabaseClient(applications=[], high_fit_job_rpc=rpc)
    row = _job_match_row()

    inserted = await handle_batch(supabase, [row])  # type: ignore[arg-type]

    assert inserted == 0
    assert rpc.calls == []


async def test_job_match_found_skips_gracefully_when_the_saved_search_was_deleted() -> None:
    """A user can delete a saved search between the matcher publishing
    this event and the outbox worker processing it -- must never surface
    as an uncaught foreign-key violation out of the RPC insert (a real,
    previously-live bug: 23503 isn't the 23505 `handle_batch` already
    catches, so it would have propagated out of `run_worker_forever`'s
    bare `while True` loop and killed the whole outbox worker)."""
    supabase = _FakeSupabaseClient(applications=[], saved_search_exists=False)
    telegram = _FakeTelegramClient()
    row = _job_match_row()

    inserted = await handle_batch(supabase, [row], telegram=telegram)  # type: ignore[arg-type]

    assert inserted == 0
    assert supabase.high_fit_job_rpc.calls == []
    assert telegram.sent == []


async def test_job_match_found_pushes_a_telegram_message_when_chat_id_resolves() -> None:
    supabase = _FakeSupabaseClient(applications=[], telegram_chat_id=555)
    telegram = _FakeTelegramClient()
    row = _job_match_row()

    inserted = await handle_batch(supabase, [row], telegram=telegram)  # type: ignore[arg-type]

    assert inserted == 1
    assert len(telegram.sent) == 1
    chat_id, text = telegram.sent[0]
    assert chat_id == 555
    assert "Backend Engineer" in text
    assert "Strong fit -- backend skills align" in text
    assert "https://boards.greenhouse.io/acme/jobs/1" in text


async def test_job_match_found_skips_the_push_when_no_telegram_identity_is_linked() -> None:
    supabase = _FakeSupabaseClient(applications=[], telegram_chat_id=None)
    telegram = _FakeTelegramClient()
    row = _job_match_row()

    inserted = await handle_batch(supabase, [row], telegram=telegram)  # type: ignore[arg-type]

    assert inserted == 1
    assert telegram.sent == []


async def test_job_match_found_never_pushes_without_a_telegram_client() -> None:
    supabase = _FakeSupabaseClient(applications=[], telegram_chat_id=555)
    row = _job_match_row()

    inserted = await handle_batch(supabase, [row])  # type: ignore[arg-type]

    assert inserted == 1


async def test_job_match_found_push_failure_does_not_affect_the_insert_count() -> None:
    supabase = _FakeSupabaseClient(applications=[], telegram_chat_id=555)
    telegram = _FakeTelegramClient(raise_on_send=True)
    row = _job_match_row()

    inserted = await handle_batch(supabase, [row], telegram=telegram)  # type: ignore[arg-type]

    assert inserted == 1
    assert telegram.sent == []


async def test_job_match_found_does_not_push_on_idempotent_duplicate() -> None:
    rpc = _FakeHighFitJobRpc(raise_unique_violation_on={"evt-match-1"})
    supabase = _FakeSupabaseClient(applications=[], high_fit_job_rpc=rpc, telegram_chat_id=555)
    telegram = _FakeTelegramClient()
    row = _job_match_row()

    inserted = await handle_batch(supabase, [row], telegram=telegram)  # type: ignore[arg-type]

    assert inserted == 0
    assert telegram.sent == []


_PROPOSAL_ID = "70000000-0000-0000-0000-000000000001"


def _status_proposal_row(
    *, event_id: str = "evt-proposal-1", **payload_overrides: Any
) -> dict[str, Any]:
    payload = {
        "application_id": _APPLICATION_ID,
        "outreach_draft_id": "60000000-0000-0000-0000-000000000001",
        "proposed_type": "interview.requested",
        "confidence": 0.5,
        "evidence_spans": ["We'd love to schedule a call this week"],
        **payload_overrides,
    }
    return {
        "id": event_id,
        "user_id": _USER_ID,
        "aggregate_id": _PROPOSAL_ID,
        "event_type": "gmail_reply.status_proposed.v1",
        "payload": payload,
    }


async def test_status_proposed_produces_a_status_proposal_item_without_touching_applications() -> (
    None
):
    supabase = _FakeSupabaseClient(applications=[])  # no application row exists at all
    row = _status_proposal_row()

    inserted = await handle_batch(supabase, [row])  # type: ignore[arg-type]

    assert inserted == 1
    call = supabase.status_proposal_rpc.calls[0]
    assert call["p_user_id"] == _USER_ID
    assert call["p_application_id"] == _APPLICATION_ID
    assert call["p_application_status_proposal_id"] == _PROPOSAL_ID
    assert call["p_source_outbox_event_id"] == "evt-proposal-1"
    assert "interview requested" in call["p_headline"]


async def test_status_proposed_headline_reflects_the_proposed_type() -> None:
    supabase = _FakeSupabaseClient(applications=[])
    row = _status_proposal_row(proposed_type="offer.received", confidence=0.6)

    await handle_batch(supabase, [row])  # type: ignore[arg-type]

    call = supabase.status_proposal_rpc.calls[0]
    assert "offer received" in call["p_headline"]
    assert "60%" in call["p_detail"]


async def test_status_proposed_is_idempotent_on_source_outbox_event_id() -> None:
    rpc = _FakeStatusProposalRpc(raise_unique_violation_on={"evt-proposal-1"})
    supabase = _FakeSupabaseClient(applications=[], status_proposal_rpc=rpc)
    row = _status_proposal_row()

    inserted = await handle_batch(supabase, [row])  # type: ignore[arg-type]

    assert inserted == 0
    assert rpc.calls == []


async def test_status_proposed_skips_gracefully_when_the_proposal_was_deleted() -> None:
    """The application (and therefore this proposal, via its own `on
    delete cascade`) can be deleted between the poller publishing this
    event and the outbox worker processing it -- must never surface as
    an uncaught foreign-key violation out of the RPC insert, the same
    real bug class Job Finder P10 fixed for job_registry.match_found.v1
    (a different SQLSTATE, 23503, than the 23505 unique-violation
    `handle_batch` already catches, which would otherwise propagate out
    of `run_worker_forever`'s bare `while True` loop and kill the whole
    outbox worker)."""
    supabase = _FakeSupabaseClient(applications=[], status_proposal_exists=False)
    row = _status_proposal_row()

    inserted = await handle_batch(supabase, [row])  # type: ignore[arg-type]

    assert inserted == 0
    assert supabase.status_proposal_rpc.calls == []


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
