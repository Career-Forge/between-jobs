"""Request/response models for the spine's HTTP boundary."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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
