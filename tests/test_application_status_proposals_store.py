"""Tests for `application_status_proposals_store.py` -- the one read
`digest_listener.py` needs to confirm a published proposal still exists
before turning it into a Today item, plus the reverse lookup and write
side R4's Accept/Dismiss routes use.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from between_jobs.api.application_status_proposals_store import (
    STAGE_MAP,
    StatusProposalNotFound,
    dismiss_other_pending_proposals,
    get_status_proposal,
    get_status_proposal_for_today_item,
    resolve_status_proposal,
)

_USER_ID = "00000000-0000-0000-0000-000000000001"
_PROPOSAL_ID = "70000000-0000-0000-0000-000000000001"
_ITEM_ID = "60000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, column: str, value: Any) -> _ChainBuilder:
        return _ChainBuilder([r for r in self._rows if r.get(column) == value])

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _UpdateBuilder:
    def __init__(self, table: _FakeTable, data: dict[str, Any]) -> None:
        self._table = table
        self._data = data
        self._filters: dict[str, Any] = {}
        self._not_filters: dict[str, Any] = {}

    def eq(self, column: str, value: Any) -> _UpdateBuilder:
        self._filters[column] = value
        return self

    def neq(self, column: str, value: Any) -> _UpdateBuilder:
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


class _FakeTable:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.rows)

    def update(self, data: dict[str, Any]) -> _UpdateBuilder:
        return _UpdateBuilder(self, data)


class _FakeSupabase:
    def __init__(
        self,
        proposals: list[dict[str, Any]],
        *,
        links: list[dict[str, Any]] | None = None,
    ) -> None:
        self._proposals = _FakeTable(proposals)
        self._links = _FakeTable(links if links is not None else [])

    def table(self, name: str) -> Any:
        if name == "application_status_proposals":
            return self._proposals
        if name == "today_item_status_proposals":
            return self._links
        raise AssertionError(f"unexpected table: {name}")


async def test_get_status_proposal_found() -> None:
    row = {"id": _PROPOSAL_ID, "user_id": _USER_ID, "status": "pending"}
    supabase = _FakeSupabase([row])

    result = await get_status_proposal(supabase, _USER_ID, _PROPOSAL_ID)  # type: ignore[arg-type]

    assert result == row


async def test_get_status_proposal_not_found_raises() -> None:
    supabase = _FakeSupabase([])

    with pytest.raises(StatusProposalNotFound):
        await get_status_proposal(supabase, _USER_ID, _PROPOSAL_ID)  # type: ignore[arg-type]


async def test_get_status_proposal_belonging_to_another_user_is_not_found() -> None:
    row = {"id": _PROPOSAL_ID, "user_id": "someone-else", "status": "pending"}
    supabase = _FakeSupabase([row])

    with pytest.raises(StatusProposalNotFound):
        await get_status_proposal(supabase, _USER_ID, _PROPOSAL_ID)  # type: ignore[arg-type]


async def test_get_status_proposal_for_today_item_resolves_via_the_link() -> None:
    proposal = {"id": _PROPOSAL_ID, "user_id": _USER_ID, "status": "pending"}
    link = {"today_item_id": _ITEM_ID, "application_status_proposal_id": _PROPOSAL_ID}
    supabase = _FakeSupabase([proposal], links=[link])

    result = await get_status_proposal_for_today_item(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        _ITEM_ID,
    )

    assert result == proposal


async def test_get_status_proposal_for_today_item_not_found_when_no_link() -> None:
    supabase = _FakeSupabase([], links=[])

    with pytest.raises(StatusProposalNotFound):
        await get_status_proposal_for_today_item(
            supabase,  # type: ignore[arg-type]
            _USER_ID,
            _ITEM_ID,
        )


async def test_get_status_proposal_for_today_item_not_found_when_proposal_itself_is_gone() -> None:
    """The link row can outlive the proposal it points to only in
    contrived test setups (real `on delete cascade` prevents this in
    production) -- still, the lookup must raise cleanly rather than
    crash if it ever did."""
    link = {"today_item_id": _ITEM_ID, "application_status_proposal_id": _PROPOSAL_ID}
    supabase = _FakeSupabase([], links=[link])

    with pytest.raises(StatusProposalNotFound):
        await get_status_proposal_for_today_item(
            supabase,  # type: ignore[arg-type]
            _USER_ID,
            _ITEM_ID,
        )


async def test_resolve_status_proposal_sets_status_and_resolved_at() -> None:
    proposal = {"id": _PROPOSAL_ID, "user_id": _USER_ID, "status": "pending", "resolved_at": None}
    supabase = _FakeSupabase([proposal])

    result = await resolve_status_proposal(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        _PROPOSAL_ID,
        status="accepted",
    )

    assert result["status"] == "accepted"
    assert result["resolved_at"] is not None


async def test_resolve_status_proposal_not_found_raises() -> None:
    supabase = _FakeSupabase([])

    with pytest.raises(StatusProposalNotFound):
        await resolve_status_proposal(
            supabase,  # type: ignore[arg-type]
            _USER_ID,
            _PROPOSAL_ID,
            status="dismissed",
        )


async def test_resolve_status_proposal_raises_when_already_resolved() -> None:
    """Regression guard for a real, adversarially-confirmed bug: the
    original update had no `status='pending'` guard, so a proposal
    already resolved one way could be silently flipped the other way --
    the real race-safe enforcement underneath `today_routes.py`'s own
    friendlier early check."""
    proposal = {
        "id": _PROPOSAL_ID,
        "user_id": _USER_ID,
        "status": "dismissed",
        "resolved_at": "2026-09-06T00:00:00Z",
    }
    supabase = _FakeSupabase([proposal])

    with pytest.raises(StatusProposalNotFound):
        await resolve_status_proposal(
            supabase,  # type: ignore[arg-type]
            _USER_ID,
            _PROPOSAL_ID,
            status="accepted",
        )

    assert proposal["status"] == "dismissed"  # untouched


async def test_dismiss_other_pending_proposals_dismisses_pending_siblings() -> None:
    application_id = "50000000-0000-0000-0000-000000000001"
    accepted = {
        "id": _PROPOSAL_ID,
        "user_id": _USER_ID,
        "application_id": application_id,
        "status": "accepted",
    }
    sibling = {
        "id": "70000000-0000-0000-0000-000000000002",
        "user_id": _USER_ID,
        "application_id": application_id,
        "status": "pending",
        "resolved_at": None,
    }
    supabase = _FakeSupabase([accepted, sibling])

    await dismiss_other_pending_proposals(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        application_id,
        except_proposal_id=_PROPOSAL_ID,
    )

    assert sibling["status"] == "dismissed"
    assert sibling["resolved_at"] is not None
    assert accepted["status"] == "accepted"  # the just-accepted one is untouched


async def test_dismiss_other_pending_proposals_leaves_already_resolved_siblings_alone() -> None:
    application_id = "50000000-0000-0000-0000-000000000001"
    accepted = {
        "id": _PROPOSAL_ID,
        "user_id": _USER_ID,
        "application_id": application_id,
        "status": "accepted",
    }
    already_dismissed_sibling = {
        "id": "70000000-0000-0000-0000-000000000002",
        "user_id": _USER_ID,
        "application_id": application_id,
        "status": "dismissed",
        "resolved_at": "2026-09-01T00:00:00Z",
    }
    supabase = _FakeSupabase([accepted, already_dismissed_sibling])

    await dismiss_other_pending_proposals(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        application_id,
        except_proposal_id=_PROPOSAL_ID,
    )

    assert already_dismissed_sibling["resolved_at"] == "2026-09-01T00:00:00Z"  # untouched


async def test_dismiss_other_pending_proposals_ignores_other_applications() -> None:
    application_id = "50000000-0000-0000-0000-000000000001"
    other_application_proposal = {
        "id": "70000000-0000-0000-0000-000000000003",
        "user_id": _USER_ID,
        "application_id": "50000000-0000-0000-0000-000000000099",
        "status": "pending",
        "resolved_at": None,
    }
    supabase = _FakeSupabase([other_application_proposal])

    await dismiss_other_pending_proposals(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        application_id,
        except_proposal_id=_PROPOSAL_ID,
    )

    assert other_application_proposal["status"] == "pending"  # a different application entirely


def test_stage_map_only_covers_real_kanban_statuses() -> None:
    real_statuses = {
        "saved",
        "applied",
        "screening",
        "interviewing",
        "offer",
        "rejected",
        "withdrawn",
    }
    assert set(STAGE_MAP.values()) <= real_statuses


def test_stage_map_excludes_the_non_stage_proposed_types() -> None:
    assert "application.acknowledged" not in STAGE_MAP
    assert "recruiter.replied" not in STAGE_MAP
    assert "unknown" not in STAGE_MAP


def test_stage_map_exact_values() -> None:
    """Strengthens the range/membership-only checks above -- an
    adversarially-flagged gap: a transposed value (e.g. offer.received
    mapping to 'rejected' instead of 'offer') would still pass every
    other test in this file."""
    assert STAGE_MAP == {
        "assessment.received": "screening",
        "interview.requested": "interviewing",
        "interview.scheduled": "interviewing",
        "application.rejected": "rejected",
        "offer.received": "offer",
    }
