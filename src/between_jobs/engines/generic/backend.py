"""The built-in backend: the engine that ships in this repository and runs inside the API.

It is what answers when no separate resume-engine service is configured
(`FORGE_ENGINES_BASE_URL` unset or blank), so a clone with only a model key has an engine
instead of an error about a service it never heard of.

It does five of the seven operations (see `GENERIC_CAPABILITIES.operations`): writing a resume
and cover letter (`apply`), reading a job's requirements (`step0`), turning the profile into
the engine's document and its plain text and header fields (`ingest`, `personal`), and
resolving the header chips. The other two, the gap interview and drafting a bullet from its
answer, answer `NOT_AVAILABLE_IN_GENERIC_ENGINE`: an operation that is not implemented says so
plainly, never with a made-up result. Nor does it invent what it does not compute: no ATS
score, no fit read, no Honest Floor decision; `apply`'s result leaves them empty.

Model calls go through the caller's own credential and `api/llm_client`; the constructor takes
the function that makes them (`generate`) so tests can supply a scripted one.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

import httpx

import between_jobs
from between_jobs.api.credential_resolver import ResolvedCredential
from between_jobs.api.engine_contract import GapAnswerDraft, GapQuestion, Step0Result
from between_jobs.api.errors import ApiError
from between_jobs.api.forge_engines_client import ForgeApplyResult

from ..backend import EngineCapabilities, EngineOperation
from .header import flat_personal, resolve_chips
from .llm import LlmGenerate, ModelSession, default_generate
from .pipeline import run_apply
from .plaintext import resume_text
from .sources import build_corpus
from .step0 import read_job

STEP0_TIMEOUT_SECONDS = 60.0
"""One model call, the same bound the separate engine's client gives its equivalent."""

GENERIC_CAPABILITIES = EngineCapabilities(
    kind="generic",
    generator="between-jobs-builtin",
    generator_version=between_jobs.__version__,
    operations=frozenset({"apply", "step0", "ingest", "personal", "resolve_header_chips"}),
    # Every fact in a document is checked against the profile it was written from, and what
    # the check had to repair or replace is reported as "unsupported claim" warnings.
    verifies_claims=True,
)

_WHAT_IT_IS = {
    "apply": "Writing a resume and cover letter",
    "step0": "Analysing a job's requirements",
    "gap_interview": "The gap interview",
    "gap_answer_draft": "Drafting a bullet from a gap interview answer",
    "ingest": "Preparing your resume text",
    "personal": "Preparing your resume text",
    "resolve_header_chips": "The resume header preview",
}
"""What a person was trying to do, worded for them, per operation."""


def not_available(operation: EngineOperation) -> ApiError:
    """The answer for an operation this server's built-in engine does not do.

    It is a 409, not a 5xx: nothing is broken, the server was set up without the hosted
    engine, and a 5xx would be logged as a failure and reported to the error tracker every
    time somebody clicked the button. It is not retryable: asking again changes nothing until
    the operator connects the hosted engine, which the message says in words a person who
    does not run the server can act on (use another feature)."""
    return ApiError(
        "NOT_AVAILABLE_IN_GENERIC_ENGINE",
        f"{_WHAT_IT_IS[operation]} isn't available on this server's built-in engine. "
        "It needs the hosted resume engine to be connected; until then, use another feature.",
        details={"operation": operation, "engine": "generic"},
    )


class GenericBackend:
    """Stateless, and needs no address: it runs in this process. The `http` client every
    operation is handed is never used; nothing here makes a network request except the model
    call, through `api/llm_client`."""

    capabilities = GENERIC_CAPABILITIES

    def __init__(self, generate: LlmGenerate | None = None) -> None:
        self._generate: LlmGenerate = generate or default_generate

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
        return await run_apply(
            ModelSession(credential, self._generate),
            resume_template=resume_template,
            job_snapshot=job_snapshot,
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
        session = ModelSession(credential, self._generate)
        try:
            async with asyncio.timeout(STEP0_TIMEOUT_SECONDS):
                return await read_job(session, job_description)
        except TimeoutError as e:
            raise ApiError(
                "PROVIDER_UNAVAILABLE",
                "Reading the job took too long. Try again in a moment.",
                retryable=True,
            ) from e

    async def gap_interview(
        self,
        http: httpx.AsyncClient,
        *,
        items: list[dict[str, str]],
        credential: ResolvedCredential,
    ) -> list[GapQuestion]:
        raise not_available("gap_interview")

    async def gap_answer_draft(
        self,
        http: httpx.AsyncClient,
        *,
        question: str,
        answer: str,
        candidates: list[dict[str, str]],
        credential: ResolvedCredential,
    ) -> GapAnswerDraft:
        raise not_available("gap_answer_draft")

    async def ingest(
        self, http: httpx.AsyncClient, *, template: dict[str, Any], now: str
    ) -> dict[str, Any]:
        """The built-in engine's resume document is the validated profile itself. Deterministic."""
        corpus = build_corpus(template)
        return {"engine": "between-jobs-builtin", "template": corpus.canonical_json, "now": now}

    async def personal(
        self, http: httpx.AsyncClient, *, resume_doc: dict[str, Any], job_context: dict[str, Any]
    ) -> dict[str, Any]:
        """`{"resume_text", "personal", "resume_source"}` from a document `ingest` made. The job
        is not needed: nothing the built-in engine prints depends on it."""
        template = resume_doc.get("template")
        if not isinstance(template, dict):
            raise ApiError("INVALID_INPUT", "That resume document wasn't made by this engine.")
        corpus = build_corpus(template)
        return {
            "resume_text": resume_text(corpus.template),
            "personal": flat_personal(corpus.template),
            "resume_source": "profile",
        }

    async def resolve_header_chips(
        self,
        http: httpx.AsyncClient,
        *,
        personal: dict[str, Any],
        header_layout: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        return resolve_chips(personal, header_layout)
