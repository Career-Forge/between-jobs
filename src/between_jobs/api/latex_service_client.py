"""HTTP client for the latex-service sub-project (Sprint 3.3f).

Talks to the public latex-service FastAPI wrapper (default :5700, per its
own Dockerfile EXPOSE) over plain HTTP -- server to server, same shape as
forge_engines_client.py's `_post` helper. Kept as its own small client
rather than folded into that one: latex-service returns a raw PDF body on
success, not the `{...}` JSON envelope every forge-engines call shares,
so the success/error split doesn't fit `_post`'s signature.
"""

from __future__ import annotations

import asyncio
import os
import re

import httpx

from .env import optional_env, refuse
from .errors import ApiError

_DEFAULT_BASE_URL = "http://localhost:5700"
"""Matches latex-service's own Dockerfile-exposed port. Overridable via
LATEX_SERVICE_BASE_URL for anything other than local dev against a
same-machine service."""

_COMPILE_TIMEOUT_SECONDS = 30.0
"""Two pdflatex passes over a single-page resume -- generous but bounded;
compiler.py itself already enforces a 20s subprocess timeout per pass."""


_DEFAULT_MAX_CONCURRENCY = 2
"""How many compiles this process has in flight at once, unless LATEX_MAX_CONCURRENCY says
otherwise. Pdflatex is CPU-bound and the service runs on a small container, so the number
that keeps every compile fast is small; more than this only lengthens each of them."""

_QUEUE_WAIT_SECONDS = 30.0
"""How long a compile waits for a free slot before giving up with a retryable error. Bounded
so a burst of PDF requests turns into "busy, try again" instead of an ever-longer pile of
open requests."""

_PLAIN_INTEGER = re.compile(r"[0-9]+")


def _base_url() -> str:
    return os.environ.get("LATEX_SERVICE_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")


def latex_max_concurrency() -> int:
    """LATEX_MAX_CONCURRENCY, or 2 when it is unset or blank. Anything that is not a plain
    positive integer stops the API from starting (`refuse`), naming the setting and never its
    value; `app.lifespan` calls this once so that happens at boot."""
    raw = optional_env("LATEX_MAX_CONCURRENCY")
    if raw is None:
        return _DEFAULT_MAX_CONCURRENCY
    raw = raw.strip()
    if not _PLAIN_INTEGER.fullmatch(raw) or int(raw) < 1:
        refuse("LATEX_MAX_CONCURRENCY must be a positive whole number")
    return int(raw)


_SEMAPHORES: dict[asyncio.AbstractEventLoop, tuple[int, asyncio.Semaphore]] = {}
"""Event loop -> (the cap it was made for, its semaphore). A plain dict, pruned by hand
(`_compile_slots`): a semaphore that has had to make a caller wait holds a reference to its
loop, so keying a weak-reference dictionary on the loop would never let a closed loop go."""


def _compile_slots() -> asyncio.Semaphore:
    """This process's compile semaphore for the running event loop, made on first use. An
    asyncio primitive belongs to one loop, so it is never made at import time and never shared
    across loops; one loop per process is the production case, and a test that runs its own
    loop gets its own semaphore. Entries of loops that have since closed are dropped on every
    call, so the registry holds only loops still open. If the configured number has changed
    since it was made, a new one replaces it (compiles already in flight keep and release the
    one they took)."""
    loop = asyncio.get_running_loop()
    for closed in [other for other in _SEMAPHORES if other.is_closed()]:
        del _SEMAPHORES[closed]
    limit = latex_max_concurrency()
    entry = _SEMAPHORES.get(loop)
    if entry is None or entry[0] != limit:
        entry = (limit, asyncio.Semaphore(limit))
        _SEMAPHORES[loop] = entry
    return entry[1]


async def call_compile(http: httpx.AsyncClient, *, latex: str) -> bytes:
    """Calls latex-service's `POST /compile` and returns the raw PDF
    bytes. Maps its CompileError/CompileTimeout JSON error responses onto
    this platform's own structured error contract (Appendix B).

    At most `latex_max_concurrency()` compiles run at once in this process; the rest wait
    their turn for up to `_QUEUE_WAIT_SECONDS`, then fail with a retryable
    PROVIDER_UNAVAILABLE. The wait is the only thing that can time out here: a slot is taken
    only once acquired and given back in a `finally`, so a waiter that is cancelled or times
    out never releases (or holds) a slot it does not own."""
    slots = _compile_slots()
    try:
        async with asyncio.timeout(_QUEUE_WAIT_SECONDS):
            await slots.acquire()
    except TimeoutError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "The PDF renderer is busy right now. Try again in a moment.",
            retryable=True,
        ) from e
    try:
        return await _compile(http, latex)
    finally:
        slots.release()


async def _compile(http: httpx.AsyncClient, latex: str) -> bytes:
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
