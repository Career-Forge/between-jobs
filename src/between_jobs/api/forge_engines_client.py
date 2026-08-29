"""HTTP client for forge-engines' service (Sprint 3.0d, extended 3.2c).

Talks to the private forge-engines FastAPI service (default :5682, per its
own Dockerfile) over plain HTTP -- there's no auth between them today
because nothing routes untrusted traffic to forge-engines directly; only
this backend calls it, server to server. `call_apply()` is the apply
flow's endpoint; `resolve_header_chips()` (Sprint 3.2c) is the Studio's
deterministic header preview. forge-engines' other routes (/ingest,
/personal, /bubbles, /allocate, /assemble, /ats/score) are either covered
by this platform's own equivalent (profile.py for ingest) or aren't
consumed by anything yet.

Request/response translation lives here, not in the route layer (3.0e):
between-jobs' own `ResolvedCredential` and `job_snapshots` row shapes map
onto forge-engines' wire format with a few fixed field renames, and that
mapping belongs next to the HTTP call it serves.

`credential.secret` is a live BYOK key -- it goes into the JSON body only,
never into a URL, header echoed to a log, or exception message.
"""

from __future__ import annotations

import os
from typing import Any, Literal, cast

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .credential_resolver import ResolvedCredential
from .engine_contract import AtsAttempt, ForgeFitResult, GapAnswerDraft, GapQuestion, Step0Result
from .errors import ApiError

_DEFAULT_BASE_URL = "http://localhost:5682"
"""Matches forge-engines' own Dockerfile-exposed port. Overridable via
FORGE_ENGINES_BASE_URL for anything other than local dev against a
same-machine service."""

_APPLY_TIMEOUT_SECONDS = 180.0
"""/apply makes several sequential LLM calls (seniority, fit, Pass1, Step0,
Pass2, ATS extract, and -- since Sprint 3.0b -- a second ATS extract on
regen) -- generous enough that a slow but healthy run doesn't get killed."""

_HEADER_RESOLVE_TIMEOUT_SECONDS = 15.0
"""Pure/deterministic, no LLM call inside forge-engines -- should be near-
instant. Short timeout so a hung request can't stall the Studio's
live-typing header preview."""


def _base_url() -> str:
    return os.environ.get("FORGE_ENGINES_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")


async def _post(
    http: httpx.AsyncClient, path: str, body: dict[str, Any], *, timeout: float
) -> dict[str, Any]:
    """Shared error mapping for every forge-engines call: never lets an
    httpx exception or a raw non-2xx response reach the caller, matching
    this project's structured-error contract (Appendix B)."""
    try:
        response = await http.post(f"{_base_url()}{path}", json=body, timeout=timeout)
    except httpx.HTTPError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "Couldn't reach the resume engine. Try again in a moment.",
            retryable=True,
        ) from e

    if response.status_code >= 500:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "The resume engine couldn't complete this run. Try again in a moment.",
            retryable=True,
        )
    if response.status_code >= 400:
        raise ApiError(
            "RUN_FAILED", f"The resume engine rejected this run: {_error_detail(response)}"
        )
    return cast(dict[str, Any], response.json())


class GateInfo(BaseModel):
    outcome: str
    reason: str = ""
    cautions: list[str] = Field(default_factory=list)


class ForgeApplyResult(BaseModel):
    """Typed view over forge-engines' `POST /apply` response -- only the
    fields this platform's prepare_application flow actually reads.
    `job`/`personal`/`seniority`/`pass1`/`step0` stay unparsed: typing
    forge-engines' own internal TypedDicts here would be a second copy to
    keep in sync by hand, the same reasoning forge-engines' own
    service/models.py gives for not re-declaring ITS wrapped functions'
    shapes. `fit` is the one exception (S4c, honest-score-surfaces.md) --
    it was already arriving in this same response and silently dropped by
    `extra="ignore"` until now; typing it is the entire fix."""

    model_config = ConfigDict(extra="ignore")

    resume: dict[str, Any] | None
    cover_letter: dict[str, Any] | None = None
    """C1/C2 (coverforge-port.md): `None` unless `generate_cover_letter` was
    requested -- same "untyped, don't re-declare forge-engines' internal
    TypedDict here" reasoning as `resume` above. Shape when present:
    `{"latex": str, "word_count": int}` (`cover_assemble.
    AssembledCoverLetter`)."""
    ats_attempts: list[AtsAttempt] = Field(default_factory=list)
    regenerated: bool
    gate: GateInfo
    fit: ForgeFitResult
    shape_report: dict[str, Any] | None = None
    """Untyped for the same reason `resume` is -- see the class docstring.
    R5: `shape_report["warnings"]` includes pin-conflict messages
    (`ContentPlan.pinWarnings`) alongside R2's page-fill warnings; surfaced
    via `shape_warnings` below rather than re-declaring `ShapeReport` here."""
    violations: list[dict[str, Any]] = Field(default_factory=list)
    """R7: forge-engines' `validate_resume` findings against the FINAL
    resume -- untyped dicts (`Violation`'s own `code`/`message`/
    `regenerable` fields), same "don't re-declare forge-engines' internal
    TypedDicts here" reasoning as `resume`/`shape_report`. Surfaced as
    human-readable strings via `violation_messages` below, folded into the
    same shared `warnings` channel R4's deferred-locale note and R5's
    pin-conflict messages already ride -- see `prepare_orchestrator.py`."""
    claim_warnings: list[str] = Field(default_factory=list)
    """C4 (coverforge-port.md): already-formatted "unsupported claim (...)"
    strings from forge-engines' claim-verification Judge (one per resume/
    cover-letter claim flagged `contradicted` or `unverifiable`) -- plain
    strings, not re-typed here, same reasoning as `violation_messages`
    above. Folded into the shared `warnings` channel in
    `prepare_orchestrator.py`; NOT itself a new gate -- generation always
    completes regardless of what this contains."""

    @property
    def generated(self) -> bool:
        return self.resume is not None

    @property
    def shape_warnings(self) -> list[str]:
        return list(self.shape_report.get("warnings", [])) if self.shape_report else []

    @property
    def violation_messages(self) -> list[str]:
        return [f"validation: {v['message']}" for v in self.violations]

    @property
    def final_ats(self) -> AtsAttempt | None:
        """The last attempt -- post-regen when a regen happened, otherwise
        the only attempt there is. None when nothing was generated."""
        return self.ats_attempts[-1] if self.ats_attempts else None


def job_posting_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Maps a `job_snapshots` row (Sprint 2.6b) onto forge-engines'
    `JobPostingRequest` shape (Sprint 3.0a). A fixed field rename, not a
    design decision -- both sides' shapes were already settled by earlier
    sprints; this just bridges them."""
    return {
        "job_id": str(snapshot["job_id"]),
        "title": snapshot["title"],
        "company": snapshot["company_name"],
        "location": snapshot.get("location_text") or "",
        "description": snapshot["description_text"],
        "url": snapshot.get("source_url") or "",
        "source": snapshot.get("source_kind") or "manual_paste",
    }


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return str(body)[:200]


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
) -> ForgeApplyResult:
    """Calls forge-engines' `POST /apply` and returns a typed result.

    `locale` (R4) and `page_count_override`/`bullet_lead_in`/`summary_mode`/
    `show_gpa` (R6) are already precedence-resolved by the caller --
    `locale_resolver.resolve_locale_for_prepare` /
    `shape_overrides.resolve` -- this client is just the wire, same as
    every other field here. `summary_mode`/`show_gpa` default to
    forge-engines' own pre-R6-behavior defaults ("auto"/`True`), NOT the
    platform's system default ("off"/`False`) -- a caller that omits them
    gets unchanged output, matching `ApplyRequest`'s own defaults; the real
    `/prepare` flow (`prepare_orchestrator.py`) always passes explicit,
    resolved values. `dealbreaker_assertions` (S2) is the per-application
    document's own `assertions` list, read straight through with no
    precedence chain of its own -- an assertion has no master/default
    concept, see `prepare_orchestrator.py`. `force_generate` (S4c) is the
    candidate's own explicit "Generate anyway" click, see
    `pipeline.run_apply`'s docstring for what it does and doesn't change.
    `show_nationality` (S5) defaults `False` like `force_generate`, not
    `True` like `show_gpa` -- this is a new opt-in, not a preserved
    pre-existing default; forge-engines still double-gates it against the
    resolved locale before ever actually rendering a chip. `generate_cover_
    letter` (C1/C2) defaults `False` -- when true, forge-engines produces
    `cover_letter` in the same `/apply` response alongside `resume`,
    matching n8n's real parallel generation."""
    posting = job_posting_payload(job_snapshot)
    body = {
        "resume_template": resume_template,
        "job_url": posting["url"],
        "now": now,
        "credential": {
            "secret": credential.secret,
            "model": credential.model,
            "base_url": credential.base_url,
        },
        "job_posting": posting,
        "density": density,
        "locale": locale,
        "page_count_override": page_count_override,
        "bullet_lead_in": bullet_lead_in,
        "summary_mode": summary_mode,
        "show_gpa": show_gpa,
        "show_nationality": show_nationality,
        "generate_cover_letter": generate_cover_letter,
        "dealbreaker_assertions": dealbreaker_assertions,
        "force_generate": force_generate,
    }
    data = await _post(http, "/apply", body, timeout=_APPLY_TIMEOUT_SECONDS)
    return ForgeApplyResult.model_validate(data)


_STEP0_TIMEOUT_SECONDS = 60.0
"""One LLM call, not several -- shorter than /apply's budget but still
generous for a real model round trip."""


async def call_step0(
    http: httpx.AsyncClient, *, job_description: str, credential: ResolvedCredential
) -> Step0Result:
    """Calls forge-engines' `POST /step0` (Sprint 3.3b) -- standalone JD
    requirement clustering, independent of the full `/apply` pipeline.
    The one real LLM call the Tailor panel's coverage view needs; matching
    it against the user's own career_facts happens deterministically,
    entirely on this side (no second LLM stage)."""
    data = await _post(
        http,
        "/step0",
        {
            "job_description": job_description,
            "credential": {
                "secret": credential.secret,
                "model": credential.model,
                "base_url": credential.base_url,
            },
        },
        timeout=_STEP0_TIMEOUT_SECONDS,
    )
    return Step0Result.model_validate(data)


_GAP_INTERVIEW_TIMEOUT_SECONDS = 45.0
"""One LLM call, wording a handful of short questions from already-resolved
pairs -- less work than /step0's full JD clustering, so a shorter budget."""


async def call_gap_interview(
    http: httpx.AsyncClient,
    *,
    items: list[dict[str, str]],
    credential: ResolvedCredential,
) -> list[GapQuestion]:
    """Calls forge-engines' `POST /gap-interview` (S4a, honest-score-
    surfaces.md). `items` is `tailor.pick_gap_interview_questions`'s own
    output (already-resolved (cluster_name, bridge_skill) pairs) -- the
    LLM only words each one into a question, never decides which gaps get
    asked about."""
    if not items:
        return []
    data = await _post(
        http,
        "/gap-interview",
        {
            "items": items,
            "credential": {
                "secret": credential.secret,
                "model": credential.model,
                "base_url": credential.base_url,
            },
        },
        timeout=_GAP_INTERVIEW_TIMEOUT_SECONDS,
    )
    raw_questions = data.get("questions")
    return [GapQuestion.model_validate(q) for q in raw_questions] if raw_questions else []


async def call_gap_answer_draft(
    http: httpx.AsyncClient,
    *,
    question: str,
    answer: str,
    candidates: list[dict[str, str]],
    credential: ResolvedCredential,
) -> GapAnswerDraft:
    """Calls forge-engines' `POST /gap-interview/draft` (S4b, honest-score-
    surfaces.md). A 422 (the LLM couldn't produce a usable draft -- see
    that endpoint's own docstring) surfaces through `_post`'s normal
    non-2xx mapping like any other forge-engines rejection, same as every
    other call in this module."""
    data = await _post(
        http,
        "/gap-interview/draft",
        {
            "question": question,
            "answer": answer,
            "candidates": candidates,
            "credential": {
                "secret": credential.secret,
                "model": credential.model,
                "base_url": credential.base_url,
            },
        },
        timeout=_GAP_INTERVIEW_TIMEOUT_SECONDS,
    )
    return GapAnswerDraft.model_validate(data)


async def call_ingest(
    http: httpx.AsyncClient, *, template: dict[str, Any], now: str
) -> dict[str, Any]:
    """Calls forge-engines' `POST /ingest` -- deterministic, no LLM.
    `template` is a profile_version's `canonical_json` as-is: the two
    shapes match field-for-field (confirmed in Sprint 3.0d)."""
    data = await _post(
        http, "/ingest", {"template": template, "now": now}, timeout=_HEADER_RESOLVE_TIMEOUT_SECONDS
    )
    return cast(dict[str, Any], data["resume_doc"])


async def call_personal(
    http: httpx.AsyncClient, *, resume_doc: dict[str, Any], job_context: dict[str, Any]
) -> dict[str, Any]:
    """Calls forge-engines' `POST /personal` -- deterministic, no LLM.
    Returns `{resume_text, personal, resume_source}`; callers that just
    need the flat `Personal` shape (e.g. header resolution) read
    `result["personal"]`."""
    return await _post(
        http,
        "/personal",
        {"resume_doc": resume_doc, "job_context": job_context},
        timeout=_HEADER_RESOLVE_TIMEOUT_SECONDS,
    )


async def resolve_header_chips(
    http: httpx.AsyncClient, *, personal: dict[str, Any], header_layout: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Calls forge-engines' `POST /header/resolve` (Sprint 3.2c) -- the
    Studio's deterministic, zero-LLM header-composer preview. Returns
    resolved chips (`{field, text, href}`, plain text, never LaTeX-
    escaped) in display order -- what the caller draws, not what gets
    compiled; the actual artifact's header still comes from
    `build_header_from_personal`/`/apply`/`/assemble` when a document is
    actually generated."""
    data = await _post(
        http,
        "/header/resolve",
        {"personal": personal, "header_layout": header_layout},
        timeout=_HEADER_RESOLVE_TIMEOUT_SECONDS,
    )
    return cast(list[dict[str, Any]], data["chips"])
