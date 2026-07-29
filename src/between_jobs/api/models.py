"""Request/response models for the spine's HTTP boundary."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class CreateSessionRequest(BaseModel):
    """No real auth yet (Sprint 2.1 is plumbing, not identity) -- `user_id`
    is trusted as given rather than derived from a verified JWT. Real auth
    (Supabase Auth + JWT verification) is the next foundational piece, not
    a detail to fake past in this sprint."""

    user_id: str
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
