"""Tests for generated-document-version persistence (Sprint 3.0e)."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

from between_jobs.api.artifact_versions_store import (
    artifact_id_for,
    create_version,
    download_content,
    get_existing_artifact_ids,
    get_latest_version,
)

_USER_ID = "00000000-0000-0000-0000-000000000001"
_APPLICATION_ID = "30000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def order(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def limit(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def in_(self, *_: Any, **__: Any) -> _ChainBuilder:
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
        rows = [{**data, "id": "version-row-1"}] if self.insert_row is None else [self.insert_row]
        return _ChainBuilder(rows)


class _FakeBucket:
    def __init__(self, *, download_bytes: bytes = b"") -> None:
        self.uploads: list[tuple[str, bytes, dict[str, Any]]] = []
        self.downloads: list[str] = []
        self._download_bytes = download_bytes

    async def upload(self, path: str, file: bytes, file_options: dict[str, Any]) -> None:
        self.uploads.append((path, file, file_options))

    async def download(self, path: str) -> bytes:
        self.downloads.append(path)
        return self._download_bytes


class _FakeStorage:
    def __init__(self, bucket: _FakeBucket) -> None:
        self._bucket = bucket

    def from_(self, bucket_id: str) -> _FakeBucket:
        assert bucket_id == "artifacts"
        return self._bucket


class _FakeSupabaseClient:
    def __init__(self, artifact_versions: _FakeTable, bucket: _FakeBucket) -> None:
        self.artifact_versions = artifact_versions
        self.storage = _FakeStorage(bucket)

    def table(self, name: str) -> Any:
        if name == "artifact_versions":
            return self.artifact_versions
        raise AssertionError(f"unexpected table: {name}")


def test_artifact_id_for_is_deterministic() -> None:
    first = artifact_id_for(_APPLICATION_ID, "resume")
    second = artifact_id_for(_APPLICATION_ID, "resume")
    assert first == second


def test_artifact_id_for_differs_by_document_kind() -> None:
    resume_id = artifact_id_for(_APPLICATION_ID, "resume")
    cover_letter_id = artifact_id_for(_APPLICATION_ID, "cover_letter")
    assert resume_id != cover_letter_id


async def test_create_version_uploads_then_inserts_version_one() -> None:
    table = _FakeTable(select_rows=[])
    bucket = _FakeBucket()
    client = _FakeSupabaseClient(table, bucket)

    result = await create_version(
        client,  # type: ignore[arg-type]
        _USER_ID,
        application_id=_APPLICATION_ID,
        document_kind="resume",
        content=b"\\begin{document}hello\\end{document}",
        media_type="application/x-tex",
        generator="forge-engines",
        generator_version="0.0.1",
        profile_version_id="pv-1",
        job_snapshot_id="js-1",
        evidence_fact_ids=[],
        warnings=["Borderline seniority match."],
    )

    assert len(bucket.uploads) == 1
    path, content, options = bucket.uploads[0]
    expected_artifact_id = artifact_id_for(_APPLICATION_ID, "resume")
    assert path == f"{_USER_ID}/{expected_artifact_id}/1"
    assert options == {"content-type": "application/x-tex"}

    assert len(table.insert_calls) == 1
    inserted = table.insert_calls[0]
    assert inserted["artifact_id"] == expected_artifact_id
    assert inserted["version"] == 1
    assert inserted["document_kind"] == "resume"
    assert inserted["storage_key"] == path
    assert inserted["sha256"] == hashlib.sha256(content).hexdigest()
    assert inserted["warnings"] == ["Borderline seniority match."]
    assert inserted["shape_report"] == {}
    assert result["artifact_id"] == expected_artifact_id


async def test_create_version_stores_the_shape_report_when_given() -> None:
    """R6: persisted alongside `warnings` so the export checklist can read
    it back later, from a separate HTTP call that has no other access to
    forge-engines' in-memory ShapeReport."""
    table = _FakeTable(select_rows=[])
    client = _FakeSupabaseClient(table, _FakeBucket())
    report = {"tier": "mid", "fill_ratio": 0.82, "target_pages": 1}

    await create_version(
        client,  # type: ignore[arg-type]
        _USER_ID,
        application_id=_APPLICATION_ID,
        document_kind="resume",
        content=b"\\begin{document}hello\\end{document}",
        media_type="application/x-tex",
        generator="forge-engines",
        generator_version="0.0.1",
        profile_version_id="pv-1",
        job_snapshot_id="js-1",
        evidence_fact_ids=[],
        warnings=[],
        shape_report=report,
    )

    assert table.insert_calls[0]["shape_report"] == report


async def test_create_version_increments_from_the_current_max_version() -> None:
    expected_artifact_id = artifact_id_for(_APPLICATION_ID, "resume")
    table = _FakeTable(select_rows=[{"version": 3}])
    bucket = _FakeBucket()
    client = _FakeSupabaseClient(table, bucket)

    await create_version(
        client,  # type: ignore[arg-type]
        _USER_ID,
        application_id=_APPLICATION_ID,
        document_kind="resume",
        content=b"content",
        media_type="application/x-tex",
        generator="forge-engines",
        generator_version="0.0.1",
        profile_version_id="pv-1",
        job_snapshot_id="js-1",
        evidence_fact_ids=[],
        warnings=[],
    )

    assert table.insert_calls[0]["version"] == 4
    path, _content, _options = bucket.uploads[0]
    assert path == f"{_USER_ID}/{expected_artifact_id}/4"


async def test_get_latest_version_returns_none_when_nothing_exists() -> None:
    table = _FakeTable(select_rows=[])
    client = _FakeSupabaseClient(table, _FakeBucket())

    result = await get_latest_version(client, _USER_ID, _APPLICATION_ID, "resume")  # type: ignore[arg-type]

    assert result is None


async def test_get_latest_version_returns_the_top_row() -> None:
    row = {"id": "version-row-9", "version": 3, "storage_key": "some/key"}
    table = _FakeTable(select_rows=[row])
    client = _FakeSupabaseClient(table, _FakeBucket())

    result = await get_latest_version(client, _USER_ID, _APPLICATION_ID, "resume")  # type: ignore[arg-type]

    assert result == row


async def test_get_existing_artifact_ids_returns_empty_set_for_empty_input() -> None:
    """No query at all for an empty application list -- Applications
    Kanban K1's own reason this exists (a whole page's resume_exists in
    one batch call, including the zero-applications case)."""
    table = _FakeTable(select_rows=[{"artifact_id": "should-not-be-returned"}])
    client = _FakeSupabaseClient(table, _FakeBucket())

    result = await get_existing_artifact_ids(client, _USER_ID, [])  # type: ignore[arg-type]

    assert result == set()


async def test_get_existing_artifact_ids_returns_the_ids_that_exist() -> None:
    resume_id = artifact_id_for(_APPLICATION_ID, "resume")
    table = _FakeTable(select_rows=[{"artifact_id": resume_id}])
    client = _FakeSupabaseClient(table, _FakeBucket())

    result = await get_existing_artifact_ids(
        client,  # type: ignore[arg-type]
        _USER_ID,
        [resume_id, "some-other-artifact-id-with-no-versions"],
    )

    assert result == {resume_id}


async def test_download_content_reads_from_the_artifacts_bucket() -> None:
    bucket = _FakeBucket(download_bytes=b"\\begin{document}hello\\end{document}")
    client = _FakeSupabaseClient(_FakeTable(select_rows=[]), bucket)

    content = await download_content(client, "some/storage/key")  # type: ignore[arg-type]

    assert content == b"\\begin{document}hello\\end{document}"
    assert bucket.downloads == ["some/storage/key"]
