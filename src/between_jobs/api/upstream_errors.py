"""What an upstream service's error answer may contribute to this API's own error message.

The resume engine and the PDF renderer are separate FastAPI services, and a 4xx from either
becomes an `ApiError` whose message the person sees. FastAPI's default 422 for a request it
cannot validate puts the offending request body in every entry's `input` (a resume, a job
description, a credential), and a skew between two deploys makes exactly that answer likely.
So a validation list is never stringified: the message gets only WHERE each problem is (`loc`)
and WHY (`msg`), capped, and never `input`, `ctx` or `url`. (The error tracker is covered
separately: it is sent no `ApiError` message at all, see sentry_scrub.py.)"""

from __future__ import annotations

from typing import Any

import httpx

MAX_DETAIL_CHARS = 200
"""The longest message returned, whatever shape the answer has."""

MAX_ISSUES = 5
MAX_LOC_PART_CHARS = 40
MAX_ISSUE_MESSAGE_CHARS = 80

UNRECOGNISED = "the service reported a problem it did not describe"
"""What a validation list with no usable entry yields: better than printing the list."""


def _issue(entry: Any) -> str | None:
    """`body.field: Field required` for one entry of a validation list, or None when the
    entry says nothing usable. Reads `loc` and `msg` only."""
    if not isinstance(entry, dict):
        return None
    loc = entry.get("loc")
    where = ""
    if isinstance(loc, list | tuple):
        where = ".".join(
            str(part)[:MAX_LOC_PART_CHARS]
            for part in loc
            if isinstance(part, str | int) and not isinstance(part, bool)
        )
    msg = entry.get("msg")
    what = msg[:MAX_ISSUE_MESSAGE_CHARS] if isinstance(msg, str) else ""
    if where and what:
        return f"{where}: {what}"
    return where or what or None


def _issues(entries: list[Any]) -> str | None:
    found = [text for text in map(_issue, entries[:MAX_ISSUES]) if text is not None]
    return "; ".join(found)[:MAX_DETAIL_CHARS] if found else None


def upstream_error_detail(response: httpx.Response) -> str:
    """A short, safe description of the problem an upstream service's non-2xx answer reports.

    A string `detail` (an `HTTPException`'s text) or `message` (the services' own
    `{"error", "message"}` answers) is kept, cut to `MAX_DETAIL_CHARS`. A `detail` list (a
    validation failure) becomes at most `MAX_ISSUES` lines of `where: why`, and is never
    printed whole, whatever it holds: `UNRECOGNISED` when no entry says anything usable. Any
    other answer -- plain text, or JSON of a shape the two services do not produce -- is shown
    cut to `MAX_DETAIL_CHARS`, as it always was."""
    try:
        body: Any = response.json()
    except ValueError:
        return response.text[:MAX_DETAIL_CHARS]
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, str):
            return detail[:MAX_DETAIL_CHARS]
        message = body.get("message")
        if isinstance(detail, list):
            issues = _issues(detail)
            if issues is not None:
                return issues
            return message[:MAX_DETAIL_CHARS] if isinstance(message, str) else UNRECOGNISED
        if isinstance(message, str):
            return message[:MAX_DETAIL_CHARS]
    return str(body)[:MAX_DETAIL_CHARS]
