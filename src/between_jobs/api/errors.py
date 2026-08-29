"""Structured API error contract (Sprint 2.6a) -- Proposal Appendix B.

One exception type, one FastAPI handler, applied across every client-
facing route at once. `profile_routes.py` shipped with plain
HTTPException mapping and said so in its own docstring, flagging this
exact migration as Sprint 2.6's job rather than something to do
piecemeal per endpoint. `app.py`'s original `/sessions` handler also had
a real instance of the thing Appendix B explicitly warns against: on an
unrecognized Postgrest error it forwarded the raw `f"{e.code}: {e.message}"`
straight into the response `detail` -- a raw database error string leaking
to the client. That path now raises INTERNAL_ERROR with a generic message
instead.

The Telegram webhook is deliberately NOT part of this migration --
Telegram calls that endpoint, not this platform's own clients, and it
doesn't parse (or care about) this JSON envelope. Its one HTTPException
(bad webhook secret) stays as-is.
"""

from __future__ import annotations

from typing import Any, Literal

ErrorCode = Literal[
    "AUTH_REQUIRED",
    "FORBIDDEN",
    "SETUP_REQUIRED",
    "INVALID_INPUT",
    "NOT_FOUND",
    "STALE_REFERENCE",
    "AMBIGUOUS_REFERENCE",
    "PROVIDER_REJECTED",
    "PROVIDER_RATE_LIMITED",
    "PROVIDER_UNAVAILABLE",
    "POLICY_REVIEW_REQUIRED",
    "INSUFFICIENT_EVIDENCE",
    "RUN_CANCELLED",
    "RUN_FAILED",
    "CONFLICT",
    "INTERNAL_ERROR",
]
"""Every code but INTERNAL_ERROR is Appendix B's own core list, verbatim.
INTERNAL_ERROR isn't in the appendix -- it's this platform's own fallback
for failures that don't fit any named code (an unmapped database error, a
genuine bug), so those still reach the client as the documented envelope
shape instead of FastAPI's default unstructured 500 body."""

_STATUS_BY_CODE: dict[ErrorCode, int] = {
    "AUTH_REQUIRED": 401,
    "FORBIDDEN": 403,
    "SETUP_REQUIRED": 409,
    "INVALID_INPUT": 422,
    "NOT_FOUND": 404,
    "STALE_REFERENCE": 409,
    "AMBIGUOUS_REFERENCE": 409,
    "PROVIDER_REJECTED": 502,
    "PROVIDER_RATE_LIMITED": 429,
    "PROVIDER_UNAVAILABLE": 503,
    "POLICY_REVIEW_REQUIRED": 403,
    "INSUFFICIENT_EVIDENCE": 422,
    "RUN_CANCELLED": 409,
    "RUN_FAILED": 500,
    "CONFLICT": 409,
    "INTERNAL_ERROR": 500,
}


class ApiError(Exception):
    """Raise from route or store code for any caller-facing failure. The
    handler registered in app.py catches this and serializes it to
    Appendix B's exact envelope shape -- callers never build that shape
    by hand."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        capability: str | None = None,
        missing: list[str] | None = None,
        settings_path: str | None = None,
        run_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message
        self.retryable = retryable
        self.capability = capability
        self.missing = missing
        self.settings_path = settings_path
        self.run_id = run_id
        self.details = details or {}

    @property
    def status_code(self) -> int:
        return _STATUS_BY_CODE[self.code]

    def to_body(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "capability": self.capability,
                "missing": self.missing,
                "settings_path": self.settings_path,
                "run_id": self.run_id,
                "details": self.details,
            }
        }
