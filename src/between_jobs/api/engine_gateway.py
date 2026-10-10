"""The one door to the resume engine: which engine answers, decided per call.

When `FORGE_ENGINES_BASE_URL` is set, the engine is the separate service at that address (the
`remote` kind). When it is unset or blank, the engine is the one built into this API (the
`generic` kind), so a clone with only a model key has something to run instead of an error
about a service it never installed.

Routes and orchestrators import the seven functions below, which keep the names and signatures
of the client functions they replaced; the only change at a call site is the import. Each call
reads the variable again (as the client always did), so the answer follows the environment and
nothing is cached: a test or an operator that changes it sees the next call go the other way.

Everything that needs to know WHICH engine is active asks here too -- `GET /capabilities`,
`/health`, the startup log line, the name stamped on a stored document -- so there is one
reading of one variable, not several.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import httpx

from between_jobs.engines import (
    CAPABILITIES_BY_KIND,
    EngineBackend,
    EngineCapabilities,
    EngineKind,
    EngineOperation,
    GenericBackend,
    RemoteBackend,
    not_available,
)

from .credential_resolver import ResolvedCredential
from .engine_contract import GapAnswerDraft, GapQuestion, Step0Result
from .forge_engines_client import ForgeApplyResult, configured_base_url

logger = logging.getLogger(__name__)

_REMOTE = RemoteBackend()
_GENERIC = GenericBackend()


def engine_kind() -> EngineKind:
    """`remote` when an address is configured, otherwise `generic`. A flag, not an address:
    the address is the operator's and goes nowhere but the client."""
    return "remote" if configured_base_url() is not None else "generic"


def log_active_engine() -> None:
    """The one startup line that says which engine writes the resumes: the kind only, never the
    address (the address is the operator's, and a log line is read by more people)."""
    logger.info("resume engine active", extra={"ctx": {"engine": engine_kind()}})


def remote_engine_url() -> str | None:
    """The separate service's address when there is one, for the one reader that has to reach
    it itself: the `/health` probe. Nothing else needs an address, and nothing may send it
    anywhere (`GET /capabilities` says `remote`, never where)."""
    return configured_base_url()


def active_backend() -> EngineBackend:
    return _REMOTE if engine_kind() == "remote" else _GENERIC


def active_capabilities() -> EngineCapabilities:
    return CAPABILITIES_BY_KIND[engine_kind()]


def ensure_available(operation: EngineOperation) -> None:
    """Raises the engine's own "not available" answer if the active engine does not do
    `operation`. For a route that would otherwise spend a model call (reading the job, say)
    before it got as far as asking for something the engine cannot do."""
    if operation not in active_capabilities().operations:
        raise not_available(operation)


async def call_apply(
    http: httpx.AsyncClient,
    *,
    resume_template: dict[str, Any],
    job_snapshot: dict[str, Any],
    credential: ResolvedCredential,
    now: str,
    density: Literal["compact", "balanced", "spacious"] = "balanced",
    locale: str | None = None,
    page_count_override: int | None = None,
    bullet_lead_in: str | None = None,
    summary_mode: str = "auto",
    show_gpa: bool = True,
    show_nationality: bool = False,
    generate_cover_letter: bool = False,
    dealbreaker_assertions: list[str] | None = None,
    force_generate: bool = False,
    header_layout: dict[str, Any] | None = None,
) -> ForgeApplyResult:
    """Writes the resume (and, when asked, the cover letter) for one job. The arguments are
    documented on `forge_engines_client.call_apply`, whose wire the remote engine uses
    unchanged."""
    return await active_backend().apply(
        http,
        resume_template=resume_template,
        job_snapshot=job_snapshot,
        credential=credential,
        now=now,
        density=density,
        locale=locale,
        page_count_override=page_count_override,
        bullet_lead_in=bullet_lead_in,
        summary_mode=summary_mode,
        show_gpa=show_gpa,
        show_nationality=show_nationality,
        generate_cover_letter=generate_cover_letter,
        dealbreaker_assertions=dealbreaker_assertions,
        force_generate=force_generate,
        header_layout=header_layout,
    )


async def call_step0(
    http: httpx.AsyncClient, *, job_description: str, credential: ResolvedCredential
) -> Step0Result:
    return await active_backend().step0(
        http, job_description=job_description, credential=credential
    )


async def call_gap_interview(
    http: httpx.AsyncClient,
    *,
    items: list[dict[str, str]],
    credential: ResolvedCredential,
) -> list[GapQuestion]:
    return await active_backend().gap_interview(http, items=items, credential=credential)


async def call_gap_answer_draft(
    http: httpx.AsyncClient,
    *,
    question: str,
    answer: str,
    candidates: list[dict[str, str]],
    credential: ResolvedCredential,
) -> GapAnswerDraft:
    return await active_backend().gap_answer_draft(
        http, question=question, answer=answer, candidates=candidates, credential=credential
    )


async def call_ingest(
    http: httpx.AsyncClient, *, template: dict[str, Any], now: str
) -> dict[str, Any]:
    return await active_backend().ingest(http, template=template, now=now)


async def call_personal(
    http: httpx.AsyncClient, *, resume_doc: dict[str, Any], job_context: dict[str, Any]
) -> dict[str, Any]:
    return await active_backend().personal(http, resume_doc=resume_doc, job_context=job_context)


async def resolve_header_chips(
    http: httpx.AsyncClient, *, personal: dict[str, Any], header_layout: dict[str, Any] | None
) -> list[dict[str, Any]]:
    return await active_backend().resolve_header_chips(
        http, personal=personal, header_layout=header_layout
    )
