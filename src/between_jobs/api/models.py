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
