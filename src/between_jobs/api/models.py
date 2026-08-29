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
