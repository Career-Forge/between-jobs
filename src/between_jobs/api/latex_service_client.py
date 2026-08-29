"""HTTP client for the latex-service sub-project (Sprint 3.3f).

Talks to the public latex-service FastAPI wrapper (default :5700, per its
own Dockerfile EXPOSE) over plain HTTP -- server to server, same shape as
forge_engines_client.py's `_post` helper. Kept as its own small client
rather than folded into that one: latex-service returns a raw PDF body on
success, not the `{...}` JSON envelope every forge-engines call shares,
so the success/error split doesn't fit `_post`'s signature.
"""

from __future__ import annotations

import os

import httpx

from .errors import ApiError

_DEFAULT_BASE_URL = "http://localhost:5700"
"""Matches latex-service's own Dockerfile-exposed port. Overridable via
LATEX_SERVICE_BASE_URL for anything other than local dev against a
same-machine service."""

_COMPILE_TIMEOUT_SECONDS = 30.0
"""Two pdflatex passes over a single-page resume -- generous but bounded;
compiler.py itself already enforces a 20s subprocess timeout per pass."""


def _base_url() -> str:
    return os.environ.get("LATEX_SERVICE_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")


async def call_compile(http: httpx.AsyncClient, *, latex: str) -> bytes:
    """Calls latex-service's `POST /compile` and returns the raw PDF
    bytes. Maps its CompileError/CompileTimeout JSON error responses onto
    this platform's own structured error contract (Appendix B)."""
    try:
        response = await http.post(
            f"{_base_url()}/compile", json={"latex": latex}, timeout=_COMPILE_TIMEOUT_SECONDS
        )
    except httpx.HTTPError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "Couldn't reach the PDF renderer. Try again in a moment.",
            retryable=True,
        ) from e

    if response.status_code == 200:
        return response.content
    if response.status_code in (422, 504):
        raise ApiError(
            "RUN_FAILED", f"This resume didn't compile to PDF: {_error_detail(response)}"
        )
    raise ApiError(
        "PROVIDER_UNAVAILABLE",
        "The PDF renderer couldn't complete this run. Try again in a moment.",
        retryable=True,
    )


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict) and "message" in body:
        return str(body["message"])
    return str(body)[:200]
