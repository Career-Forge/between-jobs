"""The remote backend: a separate resume-engine service, reached over HTTP.

This is the HTTP client in `between_jobs.api.forge_engines_client` behind the `EngineBackend`
seam. Nothing about what is sent, what is read back, or how a failure is reported changed in
the move: each method forwards its arguments to the client function of the same purpose and
returns what that returns, so the client module (and its tests) stay the record of the wire.
"""

from __future__ import annotations

from typing import Any, Literal

import httpx

from between_jobs.api import forge_engines_client as client
from between_jobs.api.credential_resolver import ResolvedCredential
from between_jobs.api.engine_contract import GapAnswerDraft, GapQuestion, Step0Result
from between_jobs.api.forge_engines_client import ForgeApplyResult

from .backend import ENGINE_OPERATIONS, EngineCapabilities

REMOTE_CAPABILITIES = EngineCapabilities(
    kind="remote",
    # What artifacts written through this backend are stamped with. The name is the service's
    # own; the version is copied from its FastAPI app (it exposes no endpoint to ask), so it
    # is a label for the stored row, not a negotiated version.
    generator="forge-engines",
    generator_version="0.0.1",
    operations=ENGINE_OPERATIONS,
    # The service runs a claim-verification pass on every document it writes.
    verifies_claims=True,
)


class RemoteBackend:
    """Stateless: the address is read from `FORGE_ENGINES_BASE_URL` by the client on every call,
    the way it always was."""

    capabilities = REMOTE_CAPABILITIES

    async def apply(
        self,
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
        return await client.call_apply(
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

    async def step0(
        self, http: httpx.AsyncClient, *, job_description: str, credential: ResolvedCredential
    ) -> Step0Result:
        return await client.call_step0(http, job_description=job_description, credential=credential)

    async def gap_interview(
        self,
        http: httpx.AsyncClient,
        *,
        items: list[dict[str, str]],
        credential: ResolvedCredential,
    ) -> list[GapQuestion]:
        return await client.call_gap_interview(http, items=items, credential=credential)

    async def gap_answer_draft(
        self,
        http: httpx.AsyncClient,
        *,
        question: str,
        answer: str,
        candidates: list[dict[str, str]],
        credential: ResolvedCredential,
    ) -> GapAnswerDraft:
        return await client.call_gap_answer_draft(
            http, question=question, answer=answer, candidates=candidates, credential=credential
        )

    async def ingest(
        self, http: httpx.AsyncClient, *, template: dict[str, Any], now: str
    ) -> dict[str, Any]:
        return await client.call_ingest(http, template=template, now=now)

    async def personal(
        self, http: httpx.AsyncClient, *, resume_doc: dict[str, Any], job_context: dict[str, Any]
    ) -> dict[str, Any]:
        return await client.call_personal(http, resume_doc=resume_doc, job_context=job_context)

    async def resolve_header_chips(
        self,
        http: httpx.AsyncClient,
        *,
        personal: dict[str, Any],
        header_layout: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        return await client.resolve_header_chips(
            http, personal=personal, header_layout=header_layout
        )
