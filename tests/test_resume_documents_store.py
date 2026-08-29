"""Tests for resume-document composition persistence (Sprint 3.2a)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from between_jobs.api.resume_documents_store import (
    DocumentNotFound,
    get_document,
    get_document_for,
    get_or_create_document,
    update_assertions,
    update_header_layout,
    update_sections,
    update_selected_evidence,
)

_USER_ID = "00000000-0000-0000-0000-000000000001"
_APPLICATION_ID = "30000000-0000-0000-0000-000000000001"
_PROFILE_VERSION_ID = "40000000-0000-0000-0000-000000000001"
_SNAPSHOT_ID = "20000000-0000-0000-0000-000000000002"
_DOCUMENT_ID = "50000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.is_calls: list[tuple[str, Any]] = []
        self.eq_calls: list[tuple[str, Any]] = []

    def eq(self, column: str, value: Any) -> _ChainBuilder:
        self.eq_calls.append((column, value))
        return self

    def is_(self, column: str, value: Any) -> _ChainBuilder:
        self.is_calls.append((column, value))
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
        self.update_calls: list[dict[str, Any]] = []
        self.last_chain: _ChainBuilder | None = None

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        self.last_chain = _ChainBuilder(self.select_rows)
        return self.last_chain

    def insert(self, data: dict[str, Any]) -> _ChainBuilder:
        self.insert_calls.append(data)
        rows = [self.insert_row] if self.insert_row is not None else [{**data, "id": _DOCUMENT_ID}]
        return _ChainBuilder(rows)

    def update(self, data: dict[str, Any]) -> _ChainBuilder:
        self.update_calls.append(data)
        rows = [{**self.select_rows[0], **data}] if self.select_rows else []
        return _ChainBuilder(rows)


class _FakeSupabaseClient:
    def __init__(self, table: _FakeTable) -> None:
        self.resume_documents = table

    def table(self, name: str) -> Any:
        assert name == "resume_documents"
        return self.resume_documents


def _row(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": _DOCUMENT_ID,
        "user_id": _USER_ID,
        "application_id": _APPLICATION_ID,
        "profile_version_id": _PROFILE_VERSION_ID,
        "job_snapshot_id": _SNAPSHOT_ID,
        "section_order": [],
        "section_visibility": {},
        "header_layout": {},
    }
    row.update(overrides)
    return row


async def test_get_document_for_application_filters_by_eq_not_is() -> None:
    table = _FakeTable(select_rows=[_row()])
    client = _FakeSupabaseClient(table)

    result = await get_document_for(client, _USER_ID, application_id=_APPLICATION_ID)  # type: ignore[arg-type]

    assert result == _row()
    assert table.last_chain is not None
    assert ("application_id", _APPLICATION_ID) in table.last_chain.eq_calls
    assert table.last_chain.is_calls == []


async def test_get_document_for_master_uses_is_null() -> None:
    master_row = _row(application_id=None, job_snapshot_id=None)
    table = _FakeTable(select_rows=[master_row])
    client = _FakeSupabaseClient(table)

    result = await get_document_for(client, _USER_ID, application_id=None)  # type: ignore[arg-type]

    assert result == master_row
    assert table.last_chain is not None
    assert ("application_id", None) in table.last_chain.is_calls


async def test_get_document_for_returns_none_when_missing() -> None:
    table = _FakeTable(select_rows=[])
    client = _FakeSupabaseClient(table)

    result = await get_document_for(client, _USER_ID, application_id=_APPLICATION_ID)  # type: ignore[arg-type]

    assert result is None


async def test_get_document_found() -> None:
    table = _FakeTable(select_rows=[_row()])
    client = _FakeSupabaseClient(table)

    result = await get_document(client, _USER_ID, _DOCUMENT_ID)  # type: ignore[arg-type]

    assert result == _row()


async def test_get_document_not_found_raises() -> None:
    table = _FakeTable(select_rows=[])
    client = _FakeSupabaseClient(table)

    with pytest.raises(DocumentNotFound):
        await get_document(client, _USER_ID, _DOCUMENT_ID)  # type: ignore[arg-type]


async def test_get_or_create_returns_existing_without_inserting() -> None:
    table = _FakeTable(select_rows=[_row()])
    client = _FakeSupabaseClient(table)

    result = await get_or_create_document(
        client,  # type: ignore[arg-type]
        _USER_ID,
        application_id=_APPLICATION_ID,
        profile_version_id=_PROFILE_VERSION_ID,
        job_snapshot_id=_SNAPSHOT_ID,
    )

    assert result == _row()
    assert table.insert_calls == []


async def test_get_or_create_inserts_when_missing() -> None:
    table = _FakeTable(select_rows=[])
    client = _FakeSupabaseClient(table)

    result = await get_or_create_document(
        client,  # type: ignore[arg-type]
        _USER_ID,
        application_id=_APPLICATION_ID,
        profile_version_id=_PROFILE_VERSION_ID,
        job_snapshot_id=_SNAPSHOT_ID,
    )

    assert len(table.insert_calls) == 1
    inserted = table.insert_calls[0]
    assert inserted["application_id"] == _APPLICATION_ID
    assert inserted["profile_version_id"] == _PROFILE_VERSION_ID
    assert inserted["job_snapshot_id"] == _SNAPSHOT_ID
    assert result["id"] == _DOCUMENT_ID


async def test_get_or_create_master_document_has_null_application_id() -> None:
    table = _FakeTable(select_rows=[])
    client = _FakeSupabaseClient(table)

    await get_or_create_document(
        client,  # type: ignore[arg-type]
        _USER_ID,
        application_id=None,
        profile_version_id=_PROFILE_VERSION_ID,
        job_snapshot_id=None,
    )

    assert table.insert_calls[0]["application_id"] is None
    assert table.insert_calls[0]["job_snapshot_id"] is None


async def test_update_header_layout_success() -> None:
    table = _FakeTable(select_rows=[_row()])
    client = _FakeSupabaseClient(table)
    layout = {"chips": [{"field": "email", "display_mode": "full"}]}

    result = await update_header_layout(client, _USER_ID, _DOCUMENT_ID, layout)  # type: ignore[arg-type]

    assert table.update_calls == [{"header_layout": layout}]
    assert result["header_layout"] == layout


async def test_update_header_layout_not_found_raises() -> None:
    table = _FakeTable(select_rows=[])
    client = _FakeSupabaseClient(table)

    with pytest.raises(DocumentNotFound):
        await update_header_layout(client, _USER_ID, _DOCUMENT_ID, {})  # type: ignore[arg-type]


async def test_update_selected_evidence_success() -> None:
    table = _FakeTable(select_rows=[_row()])
    client = _FakeSupabaseClient(table)
    fact_ids = ["fact-1", "fact-2"]

    result = await update_selected_evidence(client, _USER_ID, _DOCUMENT_ID, fact_ids)  # type: ignore[arg-type]

    assert table.update_calls == [{"selected_evidence_fact_ids": fact_ids}]
    assert result["selected_evidence_fact_ids"] == fact_ids


async def test_update_selected_evidence_not_found_raises() -> None:
    table = _FakeTable(select_rows=[])
    client = _FakeSupabaseClient(table)

    with pytest.raises(DocumentNotFound):
        await update_selected_evidence(client, _USER_ID, _DOCUMENT_ID, [])  # type: ignore[arg-type]


async def test_update_assertions_success() -> None:
    table = _FakeTable(select_rows=[_row()])
    client = _FakeSupabaseClient(table)
    assertions = ["On-site role"]

    result = await update_assertions(client, _USER_ID, _DOCUMENT_ID, assertions)  # type: ignore[arg-type]

    assert table.update_calls == [{"assertions": assertions}]
    assert result["assertions"] == assertions


async def test_update_assertions_not_found_raises() -> None:
    table = _FakeTable(select_rows=[])
    client = _FakeSupabaseClient(table)

    with pytest.raises(DocumentNotFound):
        await update_assertions(client, _USER_ID, _DOCUMENT_ID, [])  # type: ignore[arg-type]


async def test_update_sections_success() -> None:
    table = _FakeTable(select_rows=[_row()])
    client = _FakeSupabaseClient(table)

    result = await update_sections(
        client,  # type: ignore[arg-type]
        _USER_ID,
        _DOCUMENT_ID,
        section_order=["experience", "education"],
        section_visibility={"education": True},
    )

    assert table.update_calls == [
        {"section_order": ["experience", "education"], "section_visibility": {"education": True}}
    ]
    assert result["section_order"] == ["experience", "education"]


async def test_update_sections_not_found_raises() -> None:
    table = _FakeTable(select_rows=[])
    client = _FakeSupabaseClient(table)

    with pytest.raises(DocumentNotFound):
        await update_sections(
            client,  # type: ignore[arg-type]
            _USER_ID,
            _DOCUMENT_ID,
            section_order=[],
            section_visibility={},
        )
