"""The engine seam: what the app asks of whatever writes resumes, and what each engine says
it can do.

Everything that generates or analyses a document (a resume and cover letter, the Tailor
panel's job analysis, the gap interview, the interview-practice resume text, the header
preview) is one of seven operations. A backend answers them, and the app never looks at
which backend it is talking to except through `EngineCapabilities`:

- `RemoteBackend` (`remote.py`) is the HTTP client of a separate resume-engine service the
  operator runs. It is chosen when `FORGE_ENGINES_BASE_URL` is set.
- `GenericBackend` (`generic/`) is the engine that ships in this repository and runs inside
  the API process. It is chosen when that variable is unset or blank, so a clone with only a
  model key still has an engine.

The choice is made per call by `between_jobs.api.engine_gateway`, which is also the only module
the routes import engine functions from.

The signatures mirror the client functions in `between_jobs.api.forge_engines_client` one for
one (the `http` client first, everything else keyword-only), so the move from calling that
module to calling a backend changed no argument at any call site.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, get_args

import httpx

from between_jobs.api.credential_resolver import ResolvedCredential
from between_jobs.api.engine_contract import GapAnswerDraft, GapQuestion, Step0Result
from between_jobs.api.forge_engines_client import ForgeApplyResult

EngineKind = Literal["remote", "generic"]
"""Which engine answers: a separate service (`remote`) or the one built into this API
(`generic`). A flag for the operator and the web app to read, never an address."""

EngineOperation = Literal[
    "apply",
    "step0",
    "gap_interview",
    "gap_answer_draft",
    "ingest",
    "personal",
    "resolve_header_chips",
]
"""The seven things an engine is asked. Each is also a method of `EngineBackend` with the same
name, and `EngineCapabilities.operations` lists the ones a backend can answer."""

ENGINE_OPERATIONS: frozenset[EngineOperation] = frozenset(get_args(EngineOperation))


@dataclass(frozen=True, slots=True)
class EngineCapabilities:
    """What one backend can do and who it says wrote a document.

    `operations` is the truth about `EngineBackend`'s methods: one that is not listed raises
    `NOT_AVAILABLE_IN_GENERIC_ENGINE` instead of answering (`tests/test_engine_backends.py`
    pins that for every backend).

    `generator` and `generator_version` are stored on every artifact the backend writes
    (`artifact_versions`), so a document always names the engine that actually produced it.

    `verifies_claims` is whether a resume or cover letter from this backend has been checked
    against the candidate's own profile, with the findings reported as warnings that begin with
    "unsupported claim". The export checklist reads it (through `claims_were_verified_by`)
    so a document nobody checked is reported as not checked, never as a pass.
    """

    kind: EngineKind
    generator: str
    generator_version: str
    operations: frozenset[EngineOperation]
    verifies_claims: bool


class EngineBackend(Protocol):
    """The seven operations. `RemoteBackend` and `GenericBackend` satisfy it structurally."""

    @property
    def capabilities(self) -> EngineCapabilities: ...

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
        """Writes the resume (and, when asked, the cover letter) for one job. The result's
        score fields (`fit`, `ats_attempts`, `gate`) are present only when the engine
        computes them: an engine that does not leaves them empty, it never invents them."""
        ...

    async def step0(
        self, http: httpx.AsyncClient, *, job_description: str, credential: ResolvedCredential
    ) -> Step0Result:
        """Clusters a job description's requirements and key terms (one model call)."""
        ...

    async def gap_interview(
        self,
        http: httpx.AsyncClient,
        *,
        items: list[dict[str, str]],
        credential: ResolvedCredential,
    ) -> list[GapQuestion]:
        """Words one question per (requirement cluster, bridge skill) pair it is handed. The
        caller decides which gaps are asked about; the engine only chooses the words."""
        ...

    async def gap_answer_draft(
        self,
        http: httpx.AsyncClient,
        *,
        question: str,
        answer: str,
        candidates: list[dict[str, str]],
        credential: ResolvedCredential,
    ) -> GapAnswerDraft:
        """Turns the candidate's typed answer into one bullet and picks which of their
        existing entries it belongs to."""
        ...

    async def ingest(
        self, http: httpx.AsyncClient, *, template: dict[str, Any], now: str
    ) -> dict[str, Any]:
        """Turns a profile version's `canonical_json` into the engine's resume document.
        Deterministic: no model call."""
        ...

    async def personal(
        self, http: httpx.AsyncClient, *, resume_doc: dict[str, Any], job_context: dict[str, Any]
    ) -> dict[str, Any]:
        """Returns `{"resume_text", "personal", "resume_source"}` for a resume document and a
        job. Callers read `resume_text` (the plain text interview practice is built from) or
        `personal` (the flat header fields). Deterministic: no model call."""
        ...

    async def resolve_header_chips(
        self,
        http: httpx.AsyncClient,
        *,
        personal: dict[str, Any],
        header_layout: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Resolves the header composer's layout into the chips to draw (`field`, `text`,
        `href`) in display order. Deterministic: no model call."""
        ...
