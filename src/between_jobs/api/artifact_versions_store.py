"""Persistence for generated document versions (Sprint 3.0e) -- Proposal
§24.3, `document_kind` per this sprint's own migration
(20260816090000_artifact_versions_document_kind_and_bucket.sql).

`artifact_id` is never looked up or minted with a fresh random id -- it's
derived deterministically from `(application_id, document_kind)` via
uuid5. The same inputs always produce the same artifact_id, so "which
artifact_id does this application's resume use" needs no registry row or
query to answer; `unique (artifact_id, version)` (2.7a) is the only
constraint doing real enforcement work.

`create_version`'s "read current max version, then insert" is a real but
small unatomic gap -- two concurrent prepares for the same
(application_id, document_kind) could both compute the same next version
and have the second insert fail on the unique constraint. Same accepted
shape as `applications_store.create_application`'s two-sequential-call
gap: flagged, not hidden, and low-stakes here since prepare is a
manually-triggered, low-frequency action, not a hot path needing a
dedicated RPC function the way `change_application_stage` (2.6d) or
`consume_link_code` (2.8d) did for invariants that actually mattered.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any, cast

from supabase import AsyncClient

_ARTIFACT_ID_NAMESPACE = uuid.UUID("6f6e9b64-6b8e-4c3e-9a0c-2f6a2b1a2d4e")
"""Fixed, arbitrary namespace for uuid5 artifact ids -- any stable UUID
works here; what matters is that it never changes (changing it would
silently orphan every artifact_id ever derived from it)."""

_BUCKET = "artifacts"


def artifact_id_for(application_id: str, document_kind: str) -> str:
    return str(uuid.uuid5(_ARTIFACT_ID_NAMESPACE, f"{application_id}:{document_kind}"))


async def _next_version(supabase: AsyncClient, artifact_id: str) -> int:
    result = (
        await supabase.table("artifact_versions")
        .select("version")
        .eq("artifact_id", artifact_id)
        .order("version", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        return 1
    row = cast(dict[str, Any], result.data[0])
    return cast(int, row["version"]) + 1


async def get_latest_version(
    supabase: AsyncClient, user_id: str, application_id: str, document_kind: str
) -> dict[str, Any] | None:
    """The row `create_version` most recently wrote for this
    (application_id, document_kind) -- e.g. what "download the resume PDF"
    and the export checklist both mean by "the current resume"."""
    artifact_id = artifact_id_for(application_id, document_kind)
    result = (
        await supabase.table("artifact_versions")
        .select("*")
        .eq("user_id", user_id)
        .eq("artifact_id", artifact_id)
        .order("version", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    return cast(dict[str, Any], result.data[0])


async def download_content(supabase: AsyncClient, storage_key: str) -> bytes:
    return await supabase.storage.from_(_BUCKET).download(storage_key)


async def create_version(
    supabase: AsyncClient,
    user_id: str,
    *,
    application_id: str,
    document_kind: str,
    content: bytes,
    media_type: str,
    generator: str,
    generator_version: str,
    profile_version_id: str,
    job_snapshot_id: str | None,
    evidence_fact_ids: list[str],
    warnings: list[str],
    shape_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Uploads `content` to Storage, then inserts the version row pointing
    at it. Storage first: an orphaned object with no row is harmless
    (nothing ever reads storage without a row's storage_key to find it),
    but a row pointing at an object that was never actually written would
    be a dangling reference every future read of this artifact hits."""
    artifact_id = artifact_id_for(application_id, document_kind)
    version = await _next_version(supabase, artifact_id)
    sha256 = hashlib.sha256(content).hexdigest()
    storage_key = f"{user_id}/{artifact_id}/{version}"

    await supabase.storage.from_(_BUCKET).upload(storage_key, content, {"content-type": media_type})

    result = (
        await supabase.table("artifact_versions")
        .insert(
            {
                "user_id": user_id,
                "application_id": application_id,
                "artifact_id": artifact_id,
                "version": version,
                "document_kind": document_kind,
                "storage_key": storage_key,
                "media_type": media_type,
                "sha256": sha256,
                "generator": generator,
                "generator_version": generator_version,
                "profile_version_id": profile_version_id,
                "job_snapshot_id": job_snapshot_id,
                "evidence_fact_ids": evidence_fact_ids,
                "warnings": warnings,
                "shape_report": shape_report or {},
            }
        )
        .execute()
    )
    return cast(dict[str, Any], result.data[0])
