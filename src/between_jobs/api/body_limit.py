"""A cap on how large a request body may be.

Every route here takes a small JSON document; the biggest legitimate one is a pasted resume
or a job description, far under a megabyte. Left unbounded, one request could hold hundreds
of megabytes in memory (FastAPI reads a whole JSON body before it validates anything) and
push that through validation, logging and the database. This middleware refuses it first.

It is a pure ASGI middleware, not a `BaseHTTPMiddleware`, because it has to sit between
the server and the application and stand in for `receive`: a body that arrives in chunks,
with no `Content-Length` or with one that lies, can only be bounded by counting the bytes as
they come.

Two ways a request is refused, both with the platform's own error envelope:

- A declared `Content-Length` over the cap is refused straight away (413 PAYLOAD_TOO_LARGE),
  before a byte of the body is read. A `Content-Length` that is not a plain non-negative
  integer, or that appears twice with different values, is a 400 INVALID_INPUT: the request
  is malformed, not too large.
- A body that streams past the cap (chunked, or longer than it said) is cut off at the cap:
  the first chunk that would cross it is NOT passed on, the 413 is sent, and the application
  is told the client disconnected, so a handler never sees more than the cap. If the
  application has already started its own response by then it is too late to answer 413;
  the body is still cut off.

The 413 closes the connection (`Connection: close`): the rest of an unread body is still on
the wire, and reusing the connection would mean parsing it as the next request.

Where it sits (see app.py): inside the CORS middleware and inside the request-id middleware,
so a 413 carries the CORS headers (without them a browser shows a network error instead of
the message) and the `X-Request-ID` header (so it can be quoted in a bug report).

The default is 1 MiB, set by MAX_REQUEST_BODY_BYTES. `PATH_PREFIX_LIMITS` is the hook for a
route that legitimately takes more (a resume upload, when there is one): a path prefix ->
bytes mapping, longest matching prefix wins, empty today. The limit is read on every request,
like the DISABLE_* flags, and validated at startup (`app.lifespan`), so a bad value stops the
API from starting instead of failing requests one by one.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .env import optional_env, refuse
from .errors import ApiError

logger = logging.getLogger(__name__)

DEFAULT_MAX_REQUEST_BODY_BYTES = 1024 * 1024

PATH_PREFIX_LIMITS: dict[str, int] = {}
"""Path prefix -> the largest body, in bytes, that paths under it may send. Empty today; the
place to raise the cap for a route that takes an upload, e.g. {"/resume-documents/upload":
10 * 1024 * 1024}. The match is on the raw path string, so end a prefix where the route's
path ends or on a slash ("/upload/" would not also raise "/uploads-other"). The longest
matching prefix wins; anything unmatched gets the default."""

_PLAIN_INTEGER = re.compile(r"[0-9]+")

_MAX_PLAIN_DIGITS = 18
"""A declared length with more digits than this (an exabyte and up) is beyond any cap anyone
could configure, so it is not converted to a number: Python refuses to turn a string of more
than 4300 digits into an int at all, and that refusal must not escape as a 500."""


def max_request_body_bytes() -> int:
    """The default cap: MAX_REQUEST_BODY_BYTES, or 1 MiB when it is unset or blank. Anything
    that is not a plain positive integer stops the API from starting (`refuse`), naming the
    setting and never its value."""
    raw = optional_env("MAX_REQUEST_BODY_BYTES")
    if raw is None:
        return DEFAULT_MAX_REQUEST_BODY_BYTES
    raw = raw.strip()
    if not _PLAIN_INTEGER.fullmatch(raw) or int(raw) < 1:
        refuse("MAX_REQUEST_BODY_BYTES must be a positive whole number of bytes")
    return int(raw)


def limit_for_path(path: str, default: int, prefix_limits: Mapping[str, int]) -> int:
    """The cap for a request path: the longest matching prefix's, else `default`."""
    best: str | None = None
    for prefix in prefix_limits:
        if path.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    return default if best is None else prefix_limits[best]


class _MalformedContentLength(Exception):
    pass


def _declared_length(scope: Scope) -> int | None:
    """The request's Content-Length, None when it has none. Raises when it is present but
    not a plain non-negative integer, or is repeated with different values. A length too long
    to be a real one (see `_MAX_PLAIN_DIGITS`) comes back as `sys.maxsize`, which is over every
    cap."""
    values: set[str] = set()
    for name, value in scope.get("headers", ()):
        if name.lower() != b"content-length":
            continue
        text = value.decode("latin-1").strip()
        if not _PLAIN_INTEGER.fullmatch(text):
            raise _MalformedContentLength
        values.add(text.lstrip("0") or "0")
    if len(values) > 1:
        raise _MalformedContentLength
    if not values:
        return None
    (digits,) = values
    return int(digits) if len(digits) <= _MAX_PLAIN_DIGITS else sys.maxsize


def describe_bytes(count: int) -> str:
    """A size in the unit a person would say it in: "1 MiB", "2 KiB", else plain bytes."""
    if count >= 1024 * 1024 and count % (1024 * 1024) == 0:
        return f"{count // (1024 * 1024)} MiB"
    if count >= 1024 and count % 1024 == 0:
        return f"{count // 1024} KiB"
    return f"{count} bytes"


def too_large_error(limit: int) -> ApiError:
    return ApiError(
        "PAYLOAD_TOO_LARGE",
        f"That request is too large to send (the limit is {describe_bytes(limit)}). "
        "Shorten it and try again.",
        details={"max_bytes": limit},
    )


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, prefix_limits: Mapping[str, int] | None = None) -> None:
        self.app = app
        self._prefix_limits = PATH_PREFIX_LIMITS if prefix_limits is None else prefix_limits

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = limit_for_path(scope.get("path", ""), max_request_body_bytes(), self._prefix_limits)
        try:
            declared = _declared_length(scope)
        except _MalformedContentLength:
            logger.info(
                "request refused: malformed Content-Length",
                extra={"ctx": {"method": scope.get("method"), "status": 400}},
            )
            error = ApiError("INVALID_INPUT", "The Content-Length header is not valid.")
            await JSONResponse(
                status_code=400, content=error.to_body(), headers={"Connection": "close"}
            )(scope, receive, send)
            return

        if declared is not None and declared > limit:
            await self._refuse_too_large(scope, receive, send, limit, how="declared")
            return

        received = 0
        refused = False  # the 413 has been sent; everything the application says is dropped
        app_started = False  # the application began its own response before the cap was hit

        async def guarded_receive() -> Message:
            nonlocal received, refused
            if refused:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] != "http.request":
                return message
            received += len(message.get("body", b""))
            if received > limit:
                refused = True
                if not app_started:
                    await self._refuse_too_large(scope, receive, send, limit, how="streamed")
                return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal app_started
            if refused:
                return
            if message["type"] == "http.response.start":
                app_started = True
            await send(message)

        try:
            await self.app(scope, guarded_receive, guarded_send)
        except Exception:
            # Once the 413 is sent the application is told its client went away and
            # aborts however it likes; that abort is not an error worth a second report.
            if not refused:
                raise

    async def _refuse_too_large(
        self, scope: Scope, receive: Receive, send: Send, limit: int, *, how: str
    ) -> None:
        logger.info(
            "request refused: body over the size limit",
            extra={
                "ctx": {
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "limit_bytes": limit,
                    "kind": how,
                    "status": 413,
                }
            },
        )
        error = too_large_error(limit)
        await JSONResponse(
            status_code=error.status_code, content=error.to_body(), headers={"Connection": "close"}
        )(scope, receive, send)
