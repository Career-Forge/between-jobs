"""Tests for the Today-feed HTTP endpoints (Horizon Sprint 4.0)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from between_jobs.api.app import app
from between_jobs.api.app_state import get_supabase
from between_jobs.api.auth import require_user_id

_USER_ID = "00000000-0000-0000-0000-000000000001"
_ITEM_ID = "60000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def neq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def is_(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def order(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def in_(self, column: str, values: list[Any]) -> _ChainBuilder:
        return _ChainBuilder([row for row in self._rows if row[column] in values])

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTable:
    def __init__(
        self, *, select_rows: list[dict[str, Any]], update_row: dict[str, Any] | None = None
    ) -> None:
        self.select_rows = select_rows
        self.update_row = update_row

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def update(self, _data: dict[str, Any]) -> _ChainBuilder:
        rows = [self.update_row] if self.update_row is not None else []
        return _ChainBuilder(rows)


class _ProposalsUpdateBuilder:
    """Unlike `_ChainBuilder`'s fixed-return `.update()` above, this one
    genuinely filters against and mutates `_ProposalsFakeTable.rows` --
    an adversarial review found the fixed-return shape meant none of the
    proposal-related tests could actually observe what status/fields a
    real `resolve_status_proposal`/`dismiss_other_pending_proposals` call
    persisted, only the test author's own pre-canned "what update()
    returns" value."""

    def __init__(self, table: _ProposalsFakeTable, data: dict[str, Any]) -> None:
        self._table = table
        self._data = data
        self._filters: dict[str, Any] = {}
        self._not_filters: dict[str, Any] = {}

    def eq(self, column: str, value: Any) -> _ProposalsUpdateBuilder:
        self._filters[column] = value
        return self

    def neq(self, column: str, value: Any) -> _ProposalsUpdateBuilder:
        self._not_filters[column] = value
        return self

    async def execute(self) -> SimpleNamespace:
        matched = [
            r
            for r in self._table.rows
            if all(r.get(k) == v for k, v in self._filters.items())
            and all(r.get(k) != v for k, v in self._not_filters.items())
        ]
        for row in matched:
            row.update(self._data)
        return SimpleNamespace(data=matched)


class _ProposalsFakeTable:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.update_calls: list[dict[str, Any]] = []

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.rows)

    def update(self, data: dict[str, Any]) -> _ProposalsUpdateBuilder:
        self.update_calls.append(data)
        return _ProposalsUpdateBuilder(self, data)


class _RpcBuilder:
    def __init__(self, data: Any) -> None:
        self._data = data

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._data)


class _FakeSupabaseClient:
    def __init__(
        self,
        today_items: _FakeTable,
        *,
        job_matches: _FakeTable | None = None,
        status_proposal_links: _FakeTable | None = None,
        status_proposals: _ProposalsFakeTable | None = None,
        applications: _FakeTable | None = None,
    ) -> None:
        self.today_items = today_items
        self.job_matches = job_matches or _FakeTable(select_rows=[])
        self.status_proposal_links = status_proposal_links or _FakeTable(select_rows=[])
        self.status_proposals = status_proposals or _ProposalsFakeTable([])
        self.applications = applications or _FakeTable(select_rows=[])
        self.change_stage_calls: list[dict[str, Any]] = []
        self.change_stage_error: APIError | None = None

    def table(self, name: str) -> Any:
        if name == "today_items":
            return self.today_items
        if name == "today_item_job_matches":
            return self.job_matches
        if name == "today_item_status_proposals":
            return self.status_proposal_links
        if name == "application_status_proposals":
            return self.status_proposals
        if name == "applications":
            return self.applications
        raise AssertionError(f"unexpected table: {name}")

    def rpc(self, fn: str, params: dict[str, Any]) -> _RpcBuilder:
        if fn == "change_application_stage":
            self.change_stage_calls.append(params)
            if self.change_stage_error is not None:
                raise self.change_stage_error
            return _RpcBuilder({"id": params["p_application_id"], "status": params["p_new_status"]})
        raise AssertionError(f"unexpected rpc: {fn}")


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")


def _client(supabase: _FakeSupabaseClient) -> TestClient:
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Any:
    yield
    app.dependency_overrides.clear()


def test_list_my_today_items_returns_undismissed_rows() -> None:
    rows = [
        {"id": _ITEM_ID, "kind": "job_tracked", "headline": "🆕 Tracking Staff Engineer @ Acme"}
    ]
    supabase = _FakeSupabaseClient(_FakeTable(select_rows=rows))

    with _client(supabase) as client:
        response = client.get("/today")

    assert response.status_code == 200
    assert response.json() == [{**rows[0], "job_match": None, "status_proposal": None}]


def test_list_my_today_items_embeds_the_job_match_for_high_fit_job_items() -> None:
    rows = [{"id": _ITEM_ID, "kind": "high_fit_job", "headline": "🎯 High-fit match"}]
    match = {"today_item_id": _ITEM_ID, "apply_url": "https://x.example/1", "score100": 78}
    supabase = _FakeSupabaseClient(
        _FakeTable(select_rows=rows), job_matches=_FakeTable(select_rows=[match])
    )

    with _client(supabase) as client:
        response = client.get("/today")

    assert response.status_code == 200
    assert response.json() == [{**rows[0], "job_match": match, "status_proposal": None}]


def test_dismiss_my_today_item_success() -> None:
    updated = {"id": _ITEM_ID, "dismissed_at": "2026-08-21T00:00:00Z"}
    supabase = _FakeSupabaseClient(_FakeTable(select_rows=[], update_row=updated))

    with _client(supabase) as client:
        response = client.post(f"/today/{_ITEM_ID}/dismiss")

    assert response.status_code == 200
    assert response.json() == updated


def test_dismiss_my_today_item_not_found_returns_404() -> None:
    supabase = _FakeSupabaseClient(_FakeTable(select_rows=[], update_row=None))

    with _client(supabase) as client:
        response = client.post(f"/today/{_ITEM_ID}/dismiss")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


_PROPOSAL_ID = "70000000-0000-0000-0000-000000000001"
_APPLICATION_ID = "50000000-0000-0000-0000-000000000001"


def _proposal_supabase(
    *,
    proposed_type: str = "interview.requested",
    proposal_status: str = "pending",
    change_stage_error: APIError | None = None,
    linked: bool = True,
    extra_proposals: list[dict[str, Any]] | None = None,
    dismiss_row: dict[str, Any] | None = None,
) -> _FakeSupabaseClient:
    proposal = {
        "id": _PROPOSAL_ID,
        "user_id": _USER_ID,
        "application_id": _APPLICATION_ID,
        "proposed_type": proposed_type,
        "confidence": 0.5,
        "status": proposal_status,
        "resolved_at": None,
    }
    link = {"today_item_id": _ITEM_ID, "application_status_proposal_id": _PROPOSAL_ID}
    resolved_dismiss_row = (
        dismiss_row
        if dismiss_row is not None
        else {"id": _ITEM_ID, "dismissed_at": "2026-09-06T00:00:00Z"}
    )
    all_proposals = [proposal, *(extra_proposals or [])]
    supabase = _FakeSupabaseClient(
        _FakeTable(select_rows=[], update_row=resolved_dismiss_row),
        status_proposal_links=_FakeTable(select_rows=[link] if linked else []),
        status_proposals=_ProposalsFakeTable(all_proposals),
    )
    supabase.change_stage_error = change_stage_error
    return supabase


def test_accept_my_status_proposal_success() -> None:
    supabase = _proposal_supabase(proposed_type="interview.requested")

    with _client(supabase) as client:
        response = client.post(f"/today/{_ITEM_ID}/accept-proposal")

    assert response.status_code == 200
    assert len(supabase.change_stage_calls) == 1
    call = supabase.change_stage_calls[0]
    assert call["p_application_id"] == _APPLICATION_ID
    assert call["p_new_status"] == "interviewing"
    assert call["p_actor_type"] == "user"
    assert call["p_idempotency_key"] == f"gmail_reply.status_proposal_accepted:{_PROPOSAL_ID}"
    proposal_row = supabase.status_proposals.rows[0]
    assert proposal_row["status"] == "accepted"
    assert proposal_row["resolved_at"] is not None


def test_accept_my_status_proposal_returns_404_when_no_proposal_is_linked() -> None:
    supabase = _proposal_supabase(linked=False)

    with _client(supabase) as client:
        response = client.post(f"/today/{_ITEM_ID}/accept-proposal")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert supabase.change_stage_calls == []


def test_accept_my_status_proposal_rejects_an_unmappable_proposed_type() -> None:
    """`recruiter.replied` has no corresponding Kanban stage -- even a
    direct API call must be refused, not just hidden from the UI."""
    supabase = _proposal_supabase(proposed_type="recruiter.replied")

    with _client(supabase) as client:
        response = client.post(f"/today/{_ITEM_ID}/accept-proposal")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"
    assert supabase.change_stage_calls == []


def test_accept_my_status_proposal_returns_404_when_the_application_is_gone() -> None:
    application_not_found = APIError(
        {"message": "application not found", "code": "P0001", "details": None, "hint": None}
    )
    supabase = _proposal_supabase(change_stage_error=application_not_found)

    with _client(supabase) as client:
        response = client.post(f"/today/{_ITEM_ID}/accept-proposal")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_accept_my_status_proposal_returns_conflict_when_already_resolved() -> None:
    """Regression guard for a real, adversarially-confirmed bug: a stale
    request (two browser tabs, a duplicate click) against an already-
    resolved proposal must never re-trigger a real stage change."""
    supabase = _proposal_supabase(proposal_status="dismissed")

    with _client(supabase) as client:
        response = client.post(f"/today/{_ITEM_ID}/accept-proposal")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"
    assert supabase.change_stage_calls == []
    assert supabase.status_proposals.rows[0]["status"] == "dismissed"  # untouched


def test_accept_my_status_proposal_dismisses_other_pending_proposals_for_the_application() -> None:
    """Regression guard: an older sibling proposal for the SAME
    application left `'pending'` after this one is accepted could later
    be accepted itself and silently regress the stage `change_
    application_stage` has no compare-and-swap against."""
    sibling_id = "70000000-0000-0000-0000-000000000002"
    sibling = {
        "id": sibling_id,
        "user_id": _USER_ID,
        "application_id": _APPLICATION_ID,
        "proposed_type": "assessment.received",
        "confidence": 0.4,
        "status": "pending",
        "resolved_at": None,
    }
    supabase = _proposal_supabase(extra_proposals=[sibling])

    with _client(supabase) as client:
        response = client.post(f"/today/{_ITEM_ID}/accept-proposal")

    assert response.status_code == 200
    sibling_row = next(r for r in supabase.status_proposals.rows if r["id"] == sibling_id)
    assert sibling_row["status"] == "dismissed"
    assert sibling_row["resolved_at"] is not None


def test_dismiss_my_status_proposal_success() -> None:
    supabase = _proposal_supabase()

    with _client(supabase) as client:
        response = client.post(f"/today/{_ITEM_ID}/dismiss-proposal")

    assert response.status_code == 200
    assert supabase.change_stage_calls == []
    assert supabase.status_proposals.rows[0]["status"] == "dismissed"


def test_dismiss_my_status_proposal_returns_404_when_no_proposal_is_linked() -> None:
    supabase = _proposal_supabase(linked=False)

    with _client(supabase) as client:
        response = client.post(f"/today/{_ITEM_ID}/dismiss-proposal")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_dismiss_my_status_proposal_returns_conflict_when_already_resolved() -> None:
    supabase = _proposal_supabase(proposal_status="accepted")

    with _client(supabase) as client:
        response = client.post(f"/today/{_ITEM_ID}/dismiss-proposal")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"
    assert supabase.status_proposals.rows[0]["status"] == "accepted"  # untouched
