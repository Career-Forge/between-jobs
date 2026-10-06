"""Per-user rate limits for the routes that spend money or heavy compute.

Most of what this API does is a few cheap database reads and writes. A handful of
routes are different: they call an LLM, a search or scrape provider, the forge-engines
service or the LaTeX compiler, and each of those costs real money or real CPU every
time. Without a bound, one signed-in user (or one buggy client in a retry loop) decides
how much of that gets spent. This module is that bound.

How it works. A route that spends something carries `dependencies=[Depends(limit("name"))]`.
That dependency runs after authentication (it needs the user id, and it must never touch
the database for an unauthenticated caller), claims one slot for (user, bucket) in
Postgres, and answers 429 `RATE_LIMITED` with a `Retry-After` header when the bucket is
full. The counter is a fixed window held in `api_rate_limits` and claimed by the atomic SQL
function `claim_rate_limit_slot`, so two requests from one user arriving together never
both read a stale count. It lives in the database, not in process memory, because the API
can run as more than one worker process and an in-memory counter is only ever right inside
one of them.

Which requests count. Every attempt that has passed authentication counts, including one
that then fails validation, hits a missing application or errors out: the limit bounds how
often an expensive path can be STARTED, which is what costs money. A request that fails
authentication is never counted (it is refused before the limiter runs), and neither is a
body that is not even valid JSON (FastAPI refuses that before it runs any dependency,
authentication included). A request refused by the limiter is not counted again.

Failing open. If the limiter itself cannot answer (the RPC errors, the database is
unreachable, the answer is malformed) the request is let through and an error line is
logged. A request that reaches this point needs the database to do its real work anyway,
so a database outage fails it regardless; what must not happen is a bug or a missing
migration in the limiter taking the whole product down. The cost of that choice is that an
outage of the limiter alone is an outage of the protection, which is why it logs at ERROR.
The one error that does NOT fail open is "this user id has no account" (the counter row
cannot be created because the account is gone): authentication only checks a token's
signature and expiry, so the access token of a deleted account keeps verifying until it
expires, and without this refusal it would carry a bucket that never fills. That caller is
refused with 401 AUTH_REQUIRED instead, before the route's expensive work runs.

Tuning. `RATE_LIMITS` is the one place the numbers live. Changing one needs no migration:
the window and the maximum are passed to the SQL function on every call. `/extension/
draft-answer` keeps its own older limiter (`extension_rate_limit.py`) and is not part of
this table.

Not a route dependency: work started through the Telegram webhook runs inside that route's
own handler under the bot's own secret, not under a user session, so it cannot carry one.
Its one expensive action, generating a resume, claims the same "prepare" bucket directly
(`rate_limit_error_or_none`, in `channel_core._start_prepare`, before the generation is
handed to a background task), so the bot is no way round the limit on the web.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Awaitable, Callable
from typing import Any, NamedTuple

from fastapi import Depends
from postgrest.exceptions import APIError

from supabase import AsyncClient

from .app_state import get_supabase
from .auth import require_user_id
from .errors import ApiError

logger = logging.getLogger(__name__)

HOUR = 3600
DAY = 24 * HOUR

_FOREIGN_KEY_VIOLATION = "23503"
"""What `claim_rate_limit_slot` raises when the user id is not in `auth.users`: the first call
for a (user, bucket) inserts a row that references it."""

RATE_LIMITS: dict[str, tuple[int, int]] = {
    # -- the four limits set by the maintainer, for the most expensive actions -----------------
    # A full discovery: live-search provider calls, the registry, liveness probes and one
    # batched LLM scoring call. Every call spends one of these slots, so the Discover page
    # must never search on its own when it opens (only on a deliberate click) or visiting
    # the page would use up the budget.
    "discover": (20, HOUR),
    # The headline generation: forge-engines pipeline, several LLM calls, one or two
    # artifacts. Retries of one click share an idempotency key and return the stored result
    # without re-running, but they are still attempts and still count.
    "prepare": (10, HOUR),
    # Several search-provider calls plus an LLM synthesis per click, on the user's own keys.
    "company_intel": (10, DAY),
    # Question generation: an LLM call plus a forge-engines ingest, once per session.
    "interview_practice_session": (10, DAY),
    # -- every other route that spends something; numbers chosen by cost class ---------------
    # Same cost class as company intel (a search fan-out plus LLM calls): same budget.
    "contact_research": (10, DAY),
    "warm_path_events": (10, DAY),
    # Two LLM calls per click (the Step 0 analysis, then the brief itself).
    "positioning_brief": (30, HOUR),
    # One LLM call per click; a person works through a handful of contacts per sitting.
    "outreach_draft": (60, HOUR),
    # One paid third-party lookup per click (Apollo, Hunter, Exa), on the user's own key.
    "contact_lookup": (60, HOUR),
    # One LLM call per answer; a practice session is five answers.
    "interview_practice_answer": (60, HOUR),
    # One or two LLM calls (the question wording, or the bullet draft). Shared by both gap
    # interview routes: they are one flow.
    "gap_interview": (60, HOUR),
    # The Step 0 LLM analysis. Higher than its siblings because the Tailor panel asks for
    # it every time it is opened, not only on a deliberate click.
    "tailor_coverage": (120, HOUR),
    # No LLM: two light forge-engines calls and one read, but the Header Composer asks for
    # a preview on every layout edit, so the number is high.
    "header_preview": (300, HOUR),
    # A search-provider call on the user's own key (a shared cache absorbs repeats). Shared
    # by the per-application and the standalone search.
    "hiring_signal_search": (60, HOUR),
    # Adding a posting by URL: possibly a Firecrawl scrape, always a registry lookup and
    # writes. Shared with "track a discovered job", which is the same act from Discover.
    "job_ingest": (60, HOUR),
    # Each call compiles the stored LaTeX to a PDF in the LaTeX service (the export checklist
    # compiles too, and the workspace asks for it on open). Compiles are also capped
    # process-wide by latex_service_client; this keeps one user from filling that queue.
    "pdf_compile": (120, HOUR),
    # Saving a key makes a live call to the provider to check it. Without a bound this is a
    # way to test other people's stolen keys through this server.
    "credential_save": (30, HOUR),
    # The extension reports one small count-only record each time an autofill finishes: a
    # single insert, no model, provider or compile call. A person fills a handful of forms an
    # hour; the bound is only there so a stuck client loop cannot grow the event log without
    # limit, so it sits well above any real use.
    "fill_outcome": (300, HOUR),
}
"""bucket -> (max requests, window seconds). The only place these numbers live."""


class RateLimitDecision(NamedTuple):
    allowed: bool
    retry_after_seconds: int
    """Whole seconds until the window ends; 0 when allowed."""


async def claim_rate_limit_slot(
    supabase: AsyncClient, user_id: str, bucket: str
) -> RateLimitDecision:
    """Atomically counts one request for this user in `bucket` and says whether it fits.

    A refusal is a normal answer, not an error. Raises (RuntimeError, or whatever the RPC
    raised) when the limiter cannot be asked or answers nonsense; `limit` turns that into
    "let the request through"."""
    max_requests, window_seconds = RATE_LIMITS[bucket]
    result = await supabase.rpc(
        "claim_rate_limit_slot",
        {
            "p_user_id": user_id,
            "p_bucket": bucket,
            "p_window_seconds": window_seconds,
            "p_max_requests": max_requests,
        },
    ).execute()
    rows = result.data
    row = rows[0] if isinstance(rows, list) and len(rows) == 1 else None
    if (
        not isinstance(row, dict)
        or not isinstance(row.get("allowed"), bool)
        or not isinstance(row.get("retry_after_seconds"), int)
    ):
        raise RuntimeError("claim_rate_limit_slot returned an unexpected shape")
    return RateLimitDecision(row["allowed"], row["retry_after_seconds"])


def describe_wait(seconds: int) -> str:
    """A human wait, rounded UP so nobody retries a moment too early: "less than a minute",
    "about 12 minutes", "about 3 hours". The web app has the same wording
    (web/src/lib/rateLimitMessage.ts); keep the two in step."""
    if seconds < 60:
        return "less than a minute"
    if seconds < HOUR:
        minutes = math.ceil(seconds / 60)
        if minutes < 60:
            return "about a minute" if minutes == 1 else f"about {minutes} minutes"
        return "about an hour"  # 59 minutes and a bit rounds up to the hour, not "60 minutes"
    if seconds < DAY:
        hours = math.ceil(seconds / HOUR)
        if hours < 24:
            return "about an hour" if hours == 1 else f"about {hours} hours"
        return "about a day"
    days = math.ceil(seconds / DAY)
    return "about a day" if days == 1 else f"about {days} days"


def _describe_window(window_seconds: int) -> str:
    if window_seconds == HOUR:
        return "hour"
    if window_seconds == DAY:
        return "day"
    return describe_wait(window_seconds).removeprefix("about ")


def rate_limited_error(bucket: str, retry_after_seconds: int) -> ApiError:
    max_requests, window_seconds = RATE_LIMITS[bucket]
    return ApiError(
        "RATE_LIMITED",
        f"You've reached the limit for this action ({max_requests} per "
        f"{_describe_window(window_seconds)}). "
        f"Try again in {describe_wait(retry_after_seconds)}.",
        retryable=True,
        details={"retry_after_seconds": retry_after_seconds, "bucket": bucket},
    )


async def rate_limit_error_or_none(
    supabase: AsyncClient, user_id: str, bucket: str
) -> ApiError | None:
    """Counts one request for this user in `bucket`. None when it may go ahead (it fits, or the
    limiter could not answer -- see "Failing open" in the module docstring); the RATE_LIMITED
    error to answer with when the bucket is full, or AUTH_REQUIRED when the user id has no
    account any more. The one place that decides, shared by the route dependency and by
    callers that are not routes (the Telegram bot's prepare)."""
    try:
        decision = await claim_rate_limit_slot(supabase, user_id, bucket)
    except APIError as e:
        if e.code == _FOREIGN_KEY_VIOLATION:
            logger.warning(
                "rate limiter refused a user id with no account",
                extra={"ctx": {"bucket": bucket}},
            )
            return ApiError("AUTH_REQUIRED", "This account no longer exists.")
        logger.error(
            "rate limiter unavailable; letting the request through",
            exc_info=True,
            extra={"ctx": {"bucket": bucket}},
        )
        return None
    except Exception:
        logger.error(
            "rate limiter unavailable; letting the request through",
            exc_info=True,
            extra={"ctx": {"bucket": bucket}},
        )
        return None
    if decision.allowed:
        return None
    return rate_limited_error(bucket, max(1, decision.retry_after_seconds))


_BUCKET_OF: dict[Callable[..., Any], str] = {}


def limiter_bucket(dependency: Callable[..., Any]) -> str | None:
    """The bucket a dependency made by `limit` enforces, or None for any other callable.
    What lets a test tell which routes carry a limiter."""
    return _BUCKET_OF.get(dependency)


def limit(
    bucket: str, *, auth: Callable[..., Awaitable[str]] = require_user_id
) -> Callable[..., Awaitable[None]]:
    """A FastAPI dependency that claims one slot in `bucket` for the authenticated user.

    Use it as `dependencies=[Depends(limit("prepare"))]` on the route. `auth` is the
    dependency that yields the user id; a route authenticated differently (the extension's
    scoped token) passes its own. FastAPI runs a dependency once per request however many
    times it is named, so the limiter and the handler share one verification."""
    if bucket not in RATE_LIMITS:
        raise ValueError(f"unknown rate-limit bucket {bucket!r}; add it to RATE_LIMITS")

    async def enforce_rate_limit(
        user_id: str = Depends(auth),
        supabase: AsyncClient = Depends(get_supabase),
    ) -> None:
        error = await rate_limit_error_or_none(supabase, user_id, bucket)
        if error is not None:
            raise error

    _BUCKET_OF[enforce_rate_limit] = bucket
    return enforce_rate_limit
