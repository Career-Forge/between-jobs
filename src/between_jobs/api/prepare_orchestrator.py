"""The prepare_application + PDF-compile orchestration, shared across
channels (Sprint 3.4b).

Originally lived directly in `applications_routes.py` (Sprint 3.0e/3.3f) --
extracted here once the Telegram bridge needed the exact same "run the
engine, store the artifact, compile a PDF" logic the web route already
had. Per this repo's own channel principle (CLAUDE.md: "Channels are
renderers; logic lives in the core service"), that logic belongs in one
place regardless of which channel triggered it -- the HTTP route and the
Telegram webhook handler both call these functions and do nothing more
than turn the result into their own surface's response shape (a JSON
body vs. a chat message + file).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

import httpx

from supabase import AsyncClient

from .applications_store import (
    ApplicationNotFound,
    get_application,
    get_event_by_idempotency_key,
    record_event,
)
from .artifact_versions_store import create_version, download_content, get_latest_version
from .credential_resolver import resolve
from .engine_contract import ArtifactRef, PrepareApplicationResult
from .errors import ApiError
from .forge_engines_client import call_apply
from .jobs_store import SnapshotNotFound, get_snapshot
from .latex_service_client import call_compile
from .locale_resolver import resolve_locale_for_prepare
from .profile_store import get_active_version
from .resume_documents_store import get_document_for
from .shape_overrides import merge as merge_shape_overrides
from .shape_overrides import resolve as resolve_shape_overrides

_GENERATOR = "forge-engines"
_GENERATOR_VERSION = "0.0.1"
"""Mirrors forge-engines' own `FastAPI(title="forge-engines", version="0.0.1")`
(service/app.py) -- copied, not queried live, since that field is static
and forge-engines exposes no dedicated version endpoint to query yet."""

_RESUME_MEDIA_TYPE = "application/x-tex"
_COVER_LETTER_MEDIA_TYPE = "application/x-tex"


async def run_prepare_application(
    supabase: AsyncClient,
    http: httpx.AsyncClient,
    user_id: str,
    application_id: str,
    idempotency_key: str,
    *,
    force_generate: bool = False,
    generate_cover_letter: bool = False,
) -> dict[str, Any]:
    """Proposal §24.1's end-to-end flow, headless (no PDF compilation in
    this step -- see `latest_resume_pdf` for that). Resolves the user's
    active profile and the application's current job snapshot itself
    rather than trusting a caller-supplied id -- neither the web route
    nor the Telegram handler should ever pass profile/job text or ids
    through directly.

    Idempotent on `idempotency_key`: a retried call with the same key
    returns the previously computed result without re-running the engine
    (real LLM spend) or writing a second artifact version. The pre-check
    happens BEFORE the engine call specifically so a retry never
    double-spends -- `record_event`'s own idempotent insert alone would
    only prevent a duplicate audit-trail row, not a duplicate run. Not
    airtight against a genuine concurrent double-fire (two calls with the
    same key racing past the pre-check together) -- accepted as a small,
    low-stakes gap for a manually-triggered, human-paced action, the same
    shape as `applications_store.create_application`'s own accepted
    unatomic gap.

    `force_generate` (S4c, honest-score-surfaces.md): "Generate anyway" --
    threaded straight through to `call_apply`; see `pipeline.run_apply`'s
    own docstring for exactly what it does and doesn't override. Each
    click sends its own fresh `idempotency_key` (the frontend generates
    one per call), so this never fights the idempotent-retry dedup above
    -- that's about not double-spending on a RETRY of the same click, an
    orthogonal concern to whether the gate was overridden.

    `generate_cover_letter` (C1/C2, coverforge-port.md): opt-in, defaults
    False, threaded straight through to `call_apply`. When true, a
    `cover_letter` artifact is written the same way `resume` is (own
    `document_kind`, own version row) -- forge-engines has no
    application-answers generation yet, so `application_answers_id` stays
    None regardless. `evidence_fact_ids` stays empty -- forge-engines' Pass1 selects
    achievements by ids it generates internally during ingest, which
    don't map onto this platform's `career_facts.id` values. Closing that
    gap needs either forge-engines surfacing which source achievements it
    actually used, or this platform embedding career_fact ids into the
    resume_template it sends -- guessing at either now would be worse
    than leaving this an honest, labeled gap.

    Claim verification (C4, coverforge-port.md) runs unconditionally on
    every real generation, resume and cover letter alike -- unlike
    `generate_cover_letter`, there is no request-level opt-out.
    `forge_result.claim_warnings` (already-formatted "unsupported claim
    (...)" strings) folds into the same shared `warnings` list everything
    else here rides; it never blocks or rewrites anything, matching this
    project's "advises, never blocks" convention -- see
    `forge_engines.claim_verify`'s own module docstring for the full
    scope and the one accepted limitation (a verifier outage and "nothing
    to flag" are indistinguishable downstream).
    """
    existing = await get_event_by_idempotency_key(supabase, user_id, idempotency_key)
    if existing is not None:
        return cast(dict[str, Any], existing["payload"])

    try:
        application = await get_application(supabase, user_id, application_id)
    except ApplicationNotFound as e:
        raise ApiError("NOT_FOUND", f"no application found for id {application_id!r}") from e

    profile_version = await get_active_version(supabase, user_id)
    if profile_version is None:
        raise ApiError(
            "SETUP_REQUIRED",
            "Import and activate a resume profile before preparing an application.",
            capability="profile",
            missing=["profile_version"],
            settings_path="/profile",
        )

    try:
        job_snapshot = await get_snapshot(supabase, application["active_job_snapshot_id"])
    except SnapshotNotFound as e:
        raise ApiError("INTERNAL_ERROR", "This application's job snapshot is missing.") from e

    credential = await resolve(supabase, user_id, capability="prepare_application")

    # R6: master document = defaults, per-application document = overrides
    # -- same `application_id is null` convention resume_documents itself
    # uses. Neither document is required to exist yet (a user who has
    # never opened Studio/the profile editor has created neither) --
    # `get_document_for` returning `None` just means "no overrides at
    # this layer," same as an empty `shape_overrides` blob would.
    master_doc = await get_document_for(supabase, user_id, application_id=None)
    per_app_doc = await get_document_for(supabase, user_id, application_id=application_id)
    master_overrides = (master_doc or {}).get("shape_overrides") or {}
    per_app_overrides = (per_app_doc or {}).get("shape_overrides") or {}

    # `region` is resolved via locale_resolver's own finer-grained 3-step
    # chain (per-application -> master -> job-text detection), NOT via
    # `resolve_shape_overrides`'s generic 2-step merge below -- the two
    # documents' region choices are kept distinct here on purpose, whereas
    # the OTHER shape settings (page count, density, ...) have no third
    # "detect from context" fallback and so need no such distinction.
    locale = resolve_locale_for_prepare(
        document_override=per_app_overrides.get("region"),
        user_default=master_overrides.get("region"),
        job_location_text=job_snapshot.get("location_text"),
    )
    resolved_shape = resolve_shape_overrides(
        merge_shape_overrides(master_overrides, per_app_overrides)
    )

    # S2 (honest-score-surfaces.md): unlike shape_overrides, an assertion has
    # no master/default concept -- "on-site work is fine" is inherently a
    # claim about THIS job, not a standing preference to merge in for every
    # application -- so this reads only the per-application document, never
    # the master one.
    dealbreaker_assertions = (per_app_doc or {}).get("assertions") or []

    forge_result = await call_apply(
        http,
        resume_template=profile_version["canonical_json"],
        job_snapshot=job_snapshot,
        credential=credential,
        now=datetime.now(UTC).isoformat(),
        locale=locale,
        density=resolved_shape.density,
        page_count_override=resolved_shape.page_count_override,
        bullet_lead_in=resolved_shape.bullet_lead_in,
        summary_mode=resolved_shape.summary_mode,
        show_gpa=resolved_shape.show_gpa,
        show_nationality=resolved_shape.show_nationality,
        generate_cover_letter=generate_cover_letter,
        dealbreaker_assertions=dealbreaker_assertions,
        force_generate=force_generate,
    )

    warnings = list(forge_result.gate.cautions)
    if forge_result.gate.outcome != "proceed" and forge_result.gate.reason:
        warnings = [forge_result.gate.reason, *warnings]
    warnings += forge_result.shape_warnings
    warnings += forge_result.violation_messages
    # C4 (coverforge-port.md): claim-verification findings ride this SAME
    # shared channel -- `export_checklist._no_unsupported_claims` reads
    # them back off the stored `artifact_versions.warnings` by their
    # `"unsupported claim"` prefix rather than a separate stored field.
    warnings += forge_result.claim_warnings

    resume_ref: ArtifactRef | None = None
    if forge_result.resume is not None:
        content = forge_result.resume["latex"].encode("utf-8")
        version_row = await create_version(
            supabase,
            user_id,
            application_id=application_id,
            document_kind="resume",
            content=content,
            media_type=_RESUME_MEDIA_TYPE,
            generator=_GENERATOR,
            generator_version=_GENERATOR_VERSION,
            profile_version_id=profile_version["id"],
            job_snapshot_id=job_snapshot["id"],
            evidence_fact_ids=[],
            warnings=warnings,
            shape_report=forge_result.shape_report,
        )
        resume_ref = ArtifactRef(
            artifact_id=version_row["artifact_id"],
            version_id=version_row["id"],
            media_type=version_row["media_type"],
            sha256=version_row["sha256"],
        )

    cover_letter_ref: ArtifactRef | None = None
    if forge_result.cover_letter is not None:
        cover_content = forge_result.cover_letter["latex"].encode("utf-8")
        cover_version_row = await create_version(
            supabase,
            user_id,
            application_id=application_id,
            document_kind="cover_letter",
            content=cover_content,
            media_type=_COVER_LETTER_MEDIA_TYPE,
            generator=_GENERATOR,
            generator_version=_GENERATOR_VERSION,
            profile_version_id=profile_version["id"],
            job_snapshot_id=job_snapshot["id"],
            evidence_fact_ids=[],
            warnings=[],
        )
        cover_letter_ref = ArtifactRef(
            artifact_id=cover_version_row["artifact_id"],
            version_id=cover_version_row["id"],
            media_type=cover_version_row["media_type"],
            sha256=cover_version_row["sha256"],
        )

    result = PrepareApplicationResult(
        run_id=str(uuid.uuid4()),
        profile_version_id=profile_version["id"],
        job_snapshot_id=job_snapshot["id"],
        resume=resume_ref,
        cover_letter=cover_letter_ref,
        application_answers_id=None,
        ats_attempts=forge_result.ats_attempts,
        final_score=(
            float(forge_result.final_ats.overall_score) if forge_result.final_ats else None
        ),
        fit=forge_result.fit,
        warnings=warnings,
        evidence_fact_ids=[],
    )

    payload = result.model_dump(mode="json")
    await record_event(
        supabase,
        user_id,
        application_id=application_id,
        event_type="application.prepared",
        payload=payload,
        actor_type="user",
        actor_id=user_id,
        idempotency_key=idempotency_key,
        outbox_event_type=(
            "artifact.generated.v1" if resume_ref is not None else "artifact.generation_failed.v1"
        ),
    )
    return payload


async def _latest_document_pdf(
    supabase: AsyncClient,
    http: httpx.AsyncClient,
    user_id: str,
    application_id: str,
    *,
    document_kind: str,
    not_found_message: str,
) -> tuple[dict[str, Any], bytes]:
    """Looks up the latest artifact of `document_kind` for this application
    and compiles it. Compiling on every call (not caching a PDF copy) keeps
    the stored LaTeX artifact as the one source of truth -- LaTeX in, PDF
    out, always freshly derived from it, per §24.5.8's "exactly one
    authoritative renderer" rule."""
    try:
        await get_application(supabase, user_id, application_id)
    except ApplicationNotFound as e:
        raise ApiError("NOT_FOUND", f"no application found for id {application_id!r}") from e

    version_row = await get_latest_version(supabase, user_id, application_id, document_kind)
    if version_row is None:
        raise ApiError("NOT_FOUND", not_found_message, retryable=False)

    latex_bytes = await download_content(supabase, version_row["storage_key"])
    pdf_bytes = await call_compile(http, latex=latex_bytes.decode("utf-8"))
    return version_row, pdf_bytes


async def latest_resume_pdf(
    supabase: AsyncClient, http: httpx.AsyncClient, user_id: str, application_id: str
) -> tuple[dict[str, Any], bytes]:
    return await _latest_document_pdf(
        supabase,
        http,
        user_id,
        application_id,
        document_kind="resume",
        not_found_message="No resume has been generated for this application yet.",
    )


async def latest_cover_letter_pdf(
    supabase: AsyncClient, http: httpx.AsyncClient, user_id: str, application_id: str
) -> tuple[dict[str, Any], bytes]:
    """C1/C2 (coverforge-port.md): same shape as `latest_resume_pdf`, own
    `document_kind`."""
    return await _latest_document_pdf(
        supabase,
        http,
        user_id,
        application_id,
        document_kind="cover_letter",
        not_found_message="No cover letter has been generated for this application yet.",
    )
