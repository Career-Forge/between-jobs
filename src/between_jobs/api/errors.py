"""Structured API error contract (Sprint 2.6a) -- Proposal Appendix B.

One exception type, one FastAPI handler, applied across every client-
facing route at once. `profile_routes.py` shipped with plain
HTTPException mapping and said so in its own docstring, flagging this
exact migration as Sprint 2.6's job rather than something to do
piecemeal per endpoint. One early handler also had a real instance of the
thing Appendix B explicitly warns against: on an unrecognized Postgrest
error it forwarded the raw `f"{e.code}: {e.message}"` straight into the
response `detail` -- a raw database error string leaking to the client.
The fallback for an unmapped database error is INTERNAL_ERROR with a
generic message instead.

The Telegram webhook is deliberately NOT part of this migration --
Telegram calls that endpoint, not this platform's own clients, and it
doesn't parse (or care about) this JSON envelope. Its one HTTPException
(bad webhook secret) stays as-is.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
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
    "FEATURE_DISABLED",
    "RATE_LIMITED",
    "PAYLOAD_TOO_LARGE",
    "UNSUPPORTED_MEDIA_TYPE",
]
"""Every code but INTERNAL_ERROR, FEATURE_DISABLED, RATE_LIMITED, PAYLOAD_TOO_LARGE and
UNSUPPORTED_MEDIA_TYPE is
Appendix B's own core list, verbatim. INTERNAL_ERROR isn't in the appendix -- it's this platform's
own fallback for failures that don't fit any named code (an unmapped database
error, a genuine bug), so those still reach the client as the documented
envelope shape instead of FastAPI's default unstructured 500 body.
FEATURE_DISABLED is the other platform-own code: a feature an operator has
switched off with a `DISABLE_*` environment flag (Hiring Signals is the first
to answer with it). It is a 404 -- from the client's side the route does not
exist -- but a distinct code, so a UI can tell "turned off" from "no such
thing".
RATE_LIMITED is this platform's own per-user limit (api/rate_limits.py): the caller
has used up their budget for an action and should wait. It is a 429 like
PROVIDER_RATE_LIMITED but means something different -- that one says an upstream
provider throttled us -- so a client must not conflate them. Its `details` carry
`retry_after_seconds` and the `bucket`, and the response has a `Retry-After` header.
PAYLOAD_TOO_LARGE is a request body over the size cap (api/body_limit.py): a 413 that
retrying unchanged cannot fix.
UNSUPPORTED_MEDIA_TYPE is an uploaded file that is not one the route reads (a resume that is
not a PDF or a DOCX, or whose bytes disagree with what it says it is): a 415."""

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
    "FEATURE_DISABLED": 404,
    "RATE_LIMITED": 429,
    "PAYLOAD_TOO_LARGE": 413,
    "UNSUPPORTED_MEDIA_TYPE": 415,
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


def log_api_error(
    logger: logging.Logger, exc: ApiError, *, ctx: Mapping[str, object] | None = None
) -> None:
    """Writes the one log line an `ApiError` gets, wherever it was turned into an answer: the
    web handler in app.py, and the chat bot, which answers in a message and so never reaches
    that handler.

    `ctx` is what the caller knows about the request (a route, or a channel and an update id):
    ids and names only, never a message, a body or a value out of one. What is added here is
    the code, the status and the cause's type (and a Postgres SQLSTATE when it carries one),
    which is what makes a 500 diagnosable; the cause's message is left out because database
    and provider messages can quote the values involved.

    The level says whose problem it is: a client error is INFO, a provider refusing or failing
    (a user's own key, a rate limit, the resume engine being down) is WARNING because that is
    not this service breaking, and anything else is ERROR."""
    line: dict[str, object] = {"code": exc.code, "status": exc.status_code, **(ctx or {})}
    cause = exc.__cause__
    if cause is not None:
        line["cause_type"] = f"{type(cause).__module__}.{type(cause).__qualname__}"
        cause_code = getattr(cause, "code", None)
        if isinstance(cause_code, str) and len(cause_code) <= 16:
            line["cause_code"] = cause_code
    if exc.status_code < 500:
        level = logging.INFO
    elif exc.code.startswith("PROVIDER_"):
        level = logging.WARNING
    else:
        level = logging.ERROR
    logger.log(level, "api error %s", exc.code, extra={"ctx": line})
