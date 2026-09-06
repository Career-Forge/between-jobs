"""Tests for `application_status_proposals_store.py` -- currently just
the one read `digest_listener.py` needs to confirm a published proposal
still exists before turning it into a Today item.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from between_jobs.api.application_status_proposals_store import (
    StatusProposalNotFound,
    get_status_proposal,
)

_USER_ID = "00000000-0000-0000-0000-000000000001"
_PROPOSAL_ID = "70000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, column: str, value: Any) -> _ChainBuilder:
        return _ChainBuilder([r for r in self._rows if r.get(column) == value])

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTable:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.rows)


class _FakeSupabase:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._table = _FakeTable(rows)

    def table(self, name: str) -> Any:
        assert name == "application_status_proposals"
        return self._table


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
