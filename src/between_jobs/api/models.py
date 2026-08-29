"""Request/response models for the spine's HTTP boundary."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CreateSessionRequest(BaseModel):
    """`user_id` is deliberately NOT a field here (Sprint 2.2) -- it comes
    from the verified access token (`auth.require_user_id`), never from
    caller-supplied input. A client cannot create a session for anyone
    but themselves."""

    context: dict[str, Any] = {}


class SessionResponse(BaseModel):
    id: str
    user_id: str
    context: dict[str, Any]
    created_at: str
    updated_at: str


class ErrorResponse(BaseModel):
    error: str
    message: str


class GapInterviewDraftRequest(BaseModel):
    """S4b (honest-score-surfaces.md): the question that was asked and the
    candidate's freeform answer to it -- everything the draft endpoint
    needs from the caller; it resolves the candidate-entity list itself
    from the user's own active profile."""

    question: str
    answer: str


class GapInterviewApproveRequest(BaseModel):
    """S4b: the candidate's final, explicitly-approved bullet + target
    entity -- may differ from what the draft endpoint proposed (the
    candidate can edit the bullet or pick a different entity before
    approving; this is the one and only value actually applied)."""

    bullet: str
    entity_pointer: str


class ImportProfileRequest(BaseModel):
    """The raw text of a resume-template JSON paste or .json file upload --
    unvalidated until `profile.import_profile()` runs on it. Kept as a
    plain string rather than a pre-parsed dict so JSON-syntax errors are
    caught by our own honest error messages (profile.py), not FastAPI's
    generic 422 body-parsing failure."""

    raw_text: str


class MintLinkCodeRequest(BaseModel):
    """Sprint 2.8c. `channel` matches channel_identities' own vocabulary
    (Proposal §14) -- only "telegram" is actually wired to a bot command
    yet (the route rejects anything else), but the field isn't narrowed
    to a single literal so the shape doesn't need to change when a second
    channel's bot integration arrives."""

    channel: str = Field(min_length=1)


class CreateApplicationFromPasteRequest(BaseModel):
    """Sprint 2.6f's manual-paste lane (Proposal §18) -- no URL scraping,
    the caller supplies the job content directly. `canonical_url` is
    optional: some pastes (forwarded emails, screenshots retyped by hand)
    have no URL at all, and a job with no URL always gets its own row
    rather than being deduped against anything (jobs_store's own rule)."""

    title: str = Field(min_length=1)
    company_name: str = Field(min_length=1)
    description_text: str = Field(min_length=1)
    canonical_url: str | None = None
    location_text: str | None = None


class ChangeApplicationStageRequest(BaseModel):
    """`idempotency_key` is caller-supplied, not server-generated --
    Proposal's own "idempotency keys on all commands" guardrail means the
    caller (the one who can actually retry) owns picking it, e.g. a fresh
    uuid per click of a "Mark as Applied" button, so a network retry of
    the same click doesn't double-record the transition."""

    new_status: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)


class SaveCredentialRequest(BaseModel):
    """Sprint 2.7e. `model` is free-text, not a dropdown of known models --
    Proposal §11: "Every model identifier is configuration, never durable
    domain data," and a hardcoded model list goes stale exactly the way
    this project's own model-currency discipline warns against.

    `model` is optional as of Horizon Sprint 5.0 -- an LLM credential
    needs one (which model to call), but a search-provider credential
    (You.com, Firecrawl) doesn't have a "model" concept at all; forcing
    one would mean either a placeholder value or a second request shape,
    neither of which is honest."""

    service: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    secret: str = Field(min_length=1)
    model: str | None = None
    base_url: str | None = None


class PrepareApplicationRequest(BaseModel):
    """Sprint 3.0e. No profile_version_id/job_snapshot_id here, unlike
    Proposal §9's MCP-facing `PrepareApplicationInput` -- this HTTP route
    resolves both itself (the user's active profile, the application's
    current active_job_snapshot_id) rather than trusting a caller-supplied
    id, matching this project's "resolve server-side, don't trust a
    caller-supplied reference to sensitive resolution" posture."""

    idempotency_key: str = Field(min_length=16, max_length=128)
    force_generate: bool = False
    """S4c (honest-score-surfaces.md): "Generate anyway" -- an explicit
    human override of the pre-generation gate, after seeing its own
    honest `fit` read (Honest Floor). Defaults False; every existing
    caller is unaffected."""
    generate_cover_letter: bool = False
    """C1/C2 (coverforge-port.md): opt-in, defaults False. D1's decision --
    bundled into this same `/prepare` call rather than a separate
    endpoint, matching n8n's real parallel resume+cover generation and
    Proposal §7.3's "share one profile/job-snapshot/artifact transaction"
    note. A boolean, not the unwired `PrepareApplicationInput.
    requested_artifacts` list (Proposal §9's MCP-facing schema, a separate
    model this HTTP route has never used) -- same shape as `force_generate`
    above."""


class UpdateHeaderLayoutRequest(BaseModel):
    """Sprint 3.2c. `header_layout` stays `dict[str, Any]` rather than a
    typed model -- its shape is forge-engines' `HeaderLayout` (Sprint
    3.2b), which this platform treats as opaque configuration it stores
    and forwards, the same way `resume_template`/`pass1`/`pass2` are
    opaque dicts at the forge-engines HTTP boundary itself."""

    header_layout: dict[str, Any] = Field(default_factory=dict)


class PreviewHeaderRequest(BaseModel):
    """`header_layout=None` previews the document's already-saved layout;
    a caller mid-edit sends the DRAFT layout instead, so the preview
    reflects unsaved changes without requiring a save first."""

    header_layout: dict[str, Any] | None = None


class UpdateSectionsRequest(BaseModel):
    """Sprint 3.2d. `section_order` is unconstrained here (not a Literal
    enum of known section names) -- the resume_documents row itself has
    no CHECK constraint on it either, matching this project's usual
    unconstrained-status-text posture, and validating against forge-
    engines' renderable section set would need this file to know that
    set, which is forge-engines' own concern, not this HTTP boundary's."""

    section_order: list[str]
    section_visibility: dict[str, bool] = Field(default_factory=dict)


class UpdateSelectedEvidenceRequest(BaseModel):
    """Sprint 3.3e's evidence picker -- the full set of career_fact ids
    the user wants considered evidence for this document, replacing
    whatever was selected before."""

    evidence_fact_ids: list[str] = Field(default_factory=list)


class ShapeOverrides(BaseModel):
    """R6 (resumeforge-shape-and-fit.md): resume settings, stored as one
    jsonb blob on `resume_documents.shape_overrides` -- both the master
    document (defaults) and a per-application document (overrides) use
    this exact same shape, merged by `shape_overrides.merge` before a
    generation call.

    Every field is optional with NO default rendered here on purpose:
    unset (`None`) means "no opinion, inherit from the next layer down the
    precedence chain" (per-application -> master -> system default). A
    stored `{}` and a stored `{"page_count": null}` both mean that. Storing
    the literal system-default value instead (e.g. `"auto"`) would mean
    "explicitly pinned to auto" -- indistinguishable from "unset" only
    because "auto" happens to BE today's system default -- so the real
    system defaults live in `shape_overrides.py`'s `resolve()`, not here."""

    model_config = ConfigDict(extra="ignore")

    page_count: Literal["auto", "1", "2"] | None = None
    density: Literal["compact", "balanced", "spacious"] | None = None
    """S6 (honest-score-surfaces.md): all three values render distinctly --
    forge-engines has its own Spacious LaTeX macro family (looser vspace,
    same font size as Balanced) and a density-aware page-line-budget scale.
    See `shape_overrides.py`."""
    summary: Literal["auto", "on", "off"] | None = None
    """Decision #3 (resumeforge-shape-and-fit.md §4): the system DEFAULT
    (applied when this is unset all the way down the chain) is "off", not
    "auto" -- summary is opt-in. See `shape_overrides.py`'s `resolve()`."""
    bullet_style: Literal["plain", "bold_lead_in"] | None = None
    """Decision #2: forge-engines' own `bullet_lead_in` defaults to "none"
    (the wiki-recommended default) -- "plain" here maps onto that."""
    region: str | None = None
    """A country code forge-engines' locale profiles understand (or any
    string -- an unrecognized one resolves to forge-engines' own DEFAULT
    profile, same as today). Merged into `locale_resolver.
    resolve_locale_for_prepare`'s existing `document_override`/
    `user_default` parameters, which have accepted real values since R4b
    but were fed `None` until this sprint gave them a source."""
    show_gpa: bool | None = None
    """GPA renders unconditionally whenever present in the profile today
    (Resume Hard Rules: never GPA on Pranav's OWN resume, but this is a
    BYOK platform for every user, not just him) -- system default is
    `False` (Pranav's own rule, generalized as the platform default, not
    hardcoded as HIS rule specifically)."""
    show_nationality: bool | None = None
    """S5 (honest-score-surfaces.md, D1): opt-in only -- system default
    `False`. Even when on, forge-engines only actually renders a nationality
    chip when the RESOLVED locale's `effective_fields` also says
    "expected" (today: DE/AT) -- this flag alone is never sufficient. The
    resolved locale isn't exposed to this frontend before generation, so
    the UI can't gate the toggle's visibility on it; it gates rendering
    itself, backend-side. Render-only -- forge-engines' own whitelists keep
    this out of every LLM prompt."""


class UpdateShapeOverridesRequest(BaseModel):
    shape_overrides: ShapeOverrides


class UpdateAssertionsRequest(BaseModel):
    """S2 (honest-score-surfaces.md) -- the full set of dealbreaker
    requirement strings the candidate has personally confirmed are true
    for them (e.g. "on-site work is fine"), replacing whatever was
    asserted before -- same full-replace convention as
    `UpdateSelectedEvidenceRequest`. Plain strings, not ids: a dealbreaker
    has no stable identity across generations (Step0 re-extracts it fresh
    every run), so the requirement TEXT itself is the only handle there
    is -- matched fuzzily at scoring time, see forge-engines'
    `ats_score.apply_dealbreaker_assertions`."""

    assertions: list[str] = Field(default_factory=list)
