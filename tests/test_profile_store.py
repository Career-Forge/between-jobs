"""Tests for profile version persistence (Sprint 2.5c)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from between_jobs.api.profile import CareerFact, ImportedProfile
from between_jobs.api.profile_store import (
    VersionAlreadyActivated,
    VersionNotFound,
    activate_version,
    count_versions,
    create_pending_version,
    delete_pending_version,
    get_active_version,
    get_version,
    list_career_facts,
)

_USER_ID = "00000000-0000-0000-0000-000000000001"
_VERSION_ID = "10000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    """A chainable query-builder stub: every filter/order/limit method is a
    no-op that returns self (the fakes in this project never re-implement
    real filtering -- they're configured with the answer a test wants and
    just hand it back, matching test_telegram_webhook.py's _FakeQuery)."""

    def __init__(self, rows: list[dict[str, Any]], count: int | None = None) -> None:
        self._rows = rows
        self._count = count

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def is_(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def order(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def limit(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    @property
    def not_(self) -> _ChainBuilder:
        # Real postgrest usage is `.not_.is_(...)` (a property, not a call)
        # -- matches SyncFilterRequestBuilder.not_ on the installed client.
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows, count=self._count)


class _FakeProfileVersionsTable:
    def __init__(
        self,
        *,
        select_rows: list[dict[str, Any]],
        insert_row: dict[str, Any] | None = None,
        update_row: dict[str, Any] | None = None,
        select_count: int | None = None,
    ) -> None:
        self.select_rows = select_rows
        self.insert_row = insert_row
        self.update_row = update_row
        self.select_count = select_count
        self.insert_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.delete_calls: int = 0

    def select(self, columns: str, **_: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows, count=self.select_count)

    def insert(self, data: dict[str, Any]) -> _ChainBuilder:
        self.insert_calls.append(data)
        rows = [self.insert_row] if self.insert_row is not None else []
        return _ChainBuilder(rows)

    def update(self, data: dict[str, Any]) -> _ChainBuilder:
        self.update_calls.append(data)
        rows = [self.update_row] if self.update_row is not None else []
        return _ChainBuilder(rows)

    def delete(self) -> _ChainBuilder:
        self.delete_calls += 1
        return _ChainBuilder([])


class _FakeCareerFactsTable:
    def __init__(self, *, select_rows: list[dict[str, Any]] | None = None) -> None:
        self.select_rows = select_rows or []
        self.insert_calls: list[list[dict[str, Any]]] = []

    def insert(self, data: list[dict[str, Any]]) -> _ChainBuilder:
        self.insert_calls.append(data)
        return _ChainBuilder([])

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)


class _FakeSupabaseClient:
    def __init__(
        self,
        profile_versions: _FakeProfileVersionsTable,
        career_facts: _FakeCareerFactsTable | None = None,
    ) -> None:
        self.profile_versions = profile_versions
        self.career_facts = career_facts or _FakeCareerFactsTable()

    def table(self, name: str) -> Any:
        if name == "profile_versions":
            return self.profile_versions
        if name == "career_facts":
            return self.career_facts
        raise AssertionError(f"unexpected table: {name}")


def _imported(facts: tuple[CareerFact, ...] = ()) -> ImportedProfile:
    return ImportedProfile(
        canonical_json={"personal": {"name": "Jane Doe"}},
        content_hash="abc123",
        schema_version="1.0",
        career_facts=facts,
    )


async def test_create_pending_version_inserts_version_and_facts() -> None:
    inserted_row = {"id": _VERSION_ID, "user_id": _USER_ID, "activated_at": None}
    table = _FakeProfileVersionsTable(select_rows=[], insert_row=inserted_row)
    client = _FakeSupabaseClient(table)
    facts = (
        CareerFact("experience", "exp_1", {"title": "Eng"}, "/experience/0"),
        CareerFact("education", "edu_1", {"degree": "BS"}, "/education/0"),
    )

    result = await create_pending_version(client, _USER_ID, _imported(facts), "telegram_json_paste")  # type: ignore[arg-type]

    assert result == inserted_row
    assert len(table.insert_calls) == 1
    assert table.insert_calls[0]["source_kind"] == "telegram_json_paste"
    assert table.insert_calls[0]["content_hash"] == "abc123"
    assert len(client.career_facts.insert_calls) == 1
    assert len(client.career_facts.insert_calls[0]) == 2


async def test_create_pending_version_omits_supersedes_id_by_default() -> None:
    inserted_row = {"id": _VERSION_ID, "user_id": _USER_ID, "activated_at": None}
    table = _FakeProfileVersionsTable(select_rows=[], insert_row=inserted_row)
    client = _FakeSupabaseClient(table)

    await create_pending_version(client, _USER_ID, _imported(), "web_json_paste")  # type: ignore[arg-type]

    assert "supersedes_id" not in table.insert_calls[0]


async def test_create_pending_version_sets_supersedes_id_when_given() -> None:
    inserted_row = {"id": _VERSION_ID, "user_id": _USER_ID, "activated_at": None}
    table = _FakeProfileVersionsTable(select_rows=[], insert_row=inserted_row)
    client = _FakeSupabaseClient(table)

    await create_pending_version(
        client,  # type: ignore[arg-type]
        _USER_ID,
        _imported(),
        "gap_interview",
        supersedes_id="previous-version-id",
    )

    assert table.insert_calls[0]["supersedes_id"] == "previous-version-id"
    assert table.insert_calls[0]["source_kind"] == "gap_interview"


async def test_create_pending_version_dedupes_on_identical_content_hash() -> None:
    existing_row = {"id": "existing-id", "user_id": _USER_ID, "activated_at": None}
    table = _FakeProfileVersionsTable(select_rows=[existing_row])
    client = _FakeSupabaseClient(table)

    result = await create_pending_version(client, _USER_ID, _imported(), "telegram_json_paste")  # type: ignore[arg-type]

    assert result == existing_row
    assert table.insert_calls == []


async def test_get_version_found() -> None:
    row = {"id": _VERSION_ID, "user_id": _USER_ID}
    client = _FakeSupabaseClient(_FakeProfileVersionsTable(select_rows=[row]))
    result = await get_version(client, _USER_ID, _VERSION_ID)  # type: ignore[arg-type]
    assert result == row


async def test_get_version_not_found_raises() -> None:
    client = _FakeSupabaseClient(_FakeProfileVersionsTable(select_rows=[]))
    with pytest.raises(VersionNotFound):
        await get_version(client, _USER_ID, _VERSION_ID)  # type: ignore[arg-type]


async def test_get_active_version_returns_none_when_absent() -> None:
    client = _FakeSupabaseClient(_FakeProfileVersionsTable(select_rows=[]))
    result = await get_active_version(client, _USER_ID)  # type: ignore[arg-type]
    assert result is None


async def test_get_active_version_returns_the_row() -> None:
    row = {"id": _VERSION_ID, "activated_at": "2026-08-06T00:00:00Z"}
    client = _FakeSupabaseClient(_FakeProfileVersionsTable(select_rows=[row]))
    result = await get_active_version(client, _USER_ID)  # type: ignore[arg-type]
    assert result == row


async def test_activate_version_updates_and_returns_row() -> None:
    updated_row = {"id": _VERSION_ID, "activated_at": "2026-08-06T00:00:00Z"}
    table = _FakeProfileVersionsTable(select_rows=[], update_row=updated_row)
    client = _FakeSupabaseClient(table)

    result = await activate_version(client, _USER_ID, _VERSION_ID)  # type: ignore[arg-type]

    assert result == updated_row
    assert len(table.update_calls) == 1
    assert "activated_at" in table.update_calls[0]


async def test_activate_version_not_found_raises() -> None:
    client = _FakeSupabaseClient(_FakeProfileVersionsTable(select_rows=[], update_row=None))
    with pytest.raises(VersionNotFound):
        await activate_version(client, _USER_ID, _VERSION_ID)  # type: ignore[arg-type]


async def test_delete_pending_version_deletes_when_not_activated() -> None:
    row = {"id": _VERSION_ID, "user_id": _USER_ID, "activated_at": None}
    table = _FakeProfileVersionsTable(select_rows=[row])
    client = _FakeSupabaseClient(table)

    await delete_pending_version(client, _USER_ID, _VERSION_ID)  # type: ignore[arg-type]

    assert table.delete_calls == 1


async def test_delete_pending_version_refuses_to_delete_an_activated_version() -> None:
    row = {"id": _VERSION_ID, "user_id": _USER_ID, "activated_at": "2026-08-06T00:00:00Z"}
    table = _FakeProfileVersionsTable(select_rows=[row])
    client = _FakeSupabaseClient(table)

    with pytest.raises(VersionAlreadyActivated):
        await delete_pending_version(client, _USER_ID, _VERSION_ID)  # type: ignore[arg-type]

    assert table.delete_calls == 0


async def test_count_versions_returns_the_count() -> None:
    client = _FakeSupabaseClient(_FakeProfileVersionsTable(select_rows=[], select_count=4))
    result = await count_versions(client, _USER_ID)  # type: ignore[arg-type]
    assert result == 4


async def test_count_versions_returns_zero_when_none_configured() -> None:
    client = _FakeSupabaseClient(_FakeProfileVersionsTable(select_rows=[], select_count=None))
    result = await count_versions(client, _USER_ID)  # type: ignore[arg-type]
    assert result == 0


async def test_list_career_facts_returns_rows() -> None:
    rows = [
        {"id": "fact-1", "fact_type": "experience", "value_json": {"title": "Eng"}},
        {"id": "fact-2", "fact_type": "education", "value_json": {"degree": "BS"}},
    ]
    client = _FakeSupabaseClient(
        _FakeProfileVersionsTable(select_rows=[]), _FakeCareerFactsTable(select_rows=rows)
    )
    result = await list_career_facts(client, _USER_ID, _VERSION_ID)  # type: ignore[arg-type]
    assert result == rows


async def test_list_career_facts_empty_for_a_version_with_no_facts() -> None:
    client = _FakeSupabaseClient(
        _FakeProfileVersionsTable(select_rows=[]), _FakeCareerFactsTable(select_rows=[])
    )
    result = await list_career_facts(client, _USER_ID, _VERSION_ID)  # type: ignore[arg-type]
    assert result == []
