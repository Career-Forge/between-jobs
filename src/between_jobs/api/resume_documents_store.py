"""Persistence for resume-document composition state (Sprint 3.2a) --
Proposal §24.5.1.

Mutable "current state," not a version log -- see the table's own
migration comment for why this differs from profile_versions/
artifact_versions. `get_or_create_document` is the only entry point that
inserts a row; everything else updates a specific known row in place.

Exposes only the update shapes a real consumer needs, added one at a time
as each sprint's own concern reaches this table (Header Composer, section
drag/hide, R6's shape_overrides) -- `accepted_patches`/`template_id` still
have no writer for the same reason.
"""

from __future__ import annotations

from typing import Any, cast

from supabase import AsyncClient


class DocumentNotFound(Exception):
    """A resume_document id doesn't exist, or belongs to another user --
    deliberately indistinguishable from the caller's side."""


async def get_document_for(
    supabase: AsyncClient, user_id: str, *, application_id: str | None
) -> dict[str, Any] | None:
    """`application_id=None` looks up the user's master/default document."""
    query = supabase.table("resume_documents").select("*").eq("user_id", user_id)
    query = (
        query.is_("application_id", None)
        if application_id is None
        else query.eq("application_id", application_id)
    )
    result = await query.execute()
    return cast(dict[str, Any], result.data[0]) if result.data else None


async def get_document(supabase: AsyncClient, user_id: str, document_id: str) -> dict[str, Any]:
    result = (
        await supabase.table("resume_documents")
        .select("*")
        .eq("id", document_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise DocumentNotFound(document_id)
    return cast(dict[str, Any], result.data[0])


async def get_or_create_document(
    supabase: AsyncClient,
    user_id: str,
    *,
    application_id: str | None,
    profile_version_id: str,
    job_snapshot_id: str | None,
) -> dict[str, Any]:
    """Idempotent: the first time a user opens Studio for an application
    (or the master editor), this creates the row; every later open returns
    the existing one unchanged -- `profile_version_id`/`job_snapshot_id`
    passed on a later call are NOT re-applied to an existing row (the
    document keeps referencing whatever version/snapshot it was created
    against, per §24.5.1's own "remains reproducible after either
    changes" -- reproducibility means the reference is stable, not that
    it silently follows the latest)."""
    existing = await get_document_for(supabase, user_id, application_id=application_id)
    if existing is not None:
        return existing

    result = (
        await supabase.table("resume_documents")
        .insert(
            {
                "user_id": user_id,
                "application_id": application_id,
                "profile_version_id": profile_version_id,
                "job_snapshot_id": job_snapshot_id,
            }
        )
        .execute()
    )
    return cast(dict[str, Any], result.data[0])


async def update_header_layout(
    supabase: AsyncClient, user_id: str, document_id: str, header_layout: dict[str, Any]
) -> dict[str, Any]:
    result = (
        await supabase.table("resume_documents")
        .update({"header_layout": header_layout})
        .eq("id", document_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise DocumentNotFound(document_id)
    return cast(dict[str, Any], result.data[0])


async def update_selected_evidence(
    supabase: AsyncClient, user_id: str, document_id: str, evidence_fact_ids: list[str]
) -> dict[str, Any]:
    """Tailor mode's evidence picker (Sprint 3.3e) -- `selected_evidence_
    fact_ids` gets its first writer here. The full list the user wants,
    not a delta: same replace-the-whole-value shape as
    `update_header_layout`/`update_sections`, simpler for a caller than
    add/remove endpoints for a list this short."""
    result = (
        await supabase.table("resume_documents")
        .update({"selected_evidence_fact_ids": evidence_fact_ids})
        .eq("id", document_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise DocumentNotFound(document_id)
    return cast(dict[str, Any], result.data[0])


async def update_assertions(
    supabase: AsyncClient, user_id: str, document_id: str, assertions: list[str]
) -> dict[str, Any]:
    """S2 (honest-score-surfaces.md). Full replace, not a delta -- same
    convention as `update_selected_evidence`. Plain requirement strings,
    fuzzy-matched against freshly re-extracted dealbreaker phrasing at
    scoring time (forge-engines has no stable id for a dealbreaker to key
    on instead -- see `ats_score.apply_dealbreaker_assertions`)."""
    result = (
        await supabase.table("resume_documents")
        .update({"assertions": assertions})
        .eq("id", document_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise DocumentNotFound(document_id)
    return cast(dict[str, Any], result.data[0])


async def update_shape_overrides(
    supabase: AsyncClient, user_id: str, document_id: str, shape_overrides: dict[str, Any]
) -> dict[str, Any]:
    """R6. `shape_overrides` is already `ShapeOverrides.model_dump(exclude_
    none=True)` by the time it reaches here (see resume_documents_routes.py)
    -- only the fields the user actually set are stored, so an untouched
    document stays `{}`, distinguishable from "explicitly set to the
    system default" (see `ShapeOverrides`'s own docstring)."""
    result = (
        await supabase.table("resume_documents")
        .update({"shape_overrides": shape_overrides})
        .eq("id", document_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise DocumentNotFound(document_id)
    return cast(dict[str, Any], result.data[0])


async def update_sections(
    supabase: AsyncClient,
    user_id: str,
    document_id: str,
    *,
    section_order: list[str],
    section_visibility: dict[str, bool],
) -> dict[str, Any]:
    result = (
        await supabase.table("resume_documents")
        .update({"section_order": section_order, "section_visibility": section_visibility})
        .eq("id", document_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise DocumentNotFound(document_id)
    return cast(dict[str, Any], result.data[0])
