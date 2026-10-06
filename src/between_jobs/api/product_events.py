"""A small, append-only log of product events: counts and outcomes, never content.

It exists so the people running a tester program can answer "how far did each tester get?"
(searched, generated a resume, downloaded it, filled a form) without reading anyone's data. One
row per event goes into `product_events`; the table, its closed lists and its grants are in
`supabase/migrations/20261005162139_create_product_events_and_tester_enrollments.sql`, and what
`n_a` and `n_b` mean for each event is written there too.

What is never recorded, by construction: an IP address, a user agent, a URL or path, resume, job
description or answer text, a form-field value or label, a third-party SDK's id, a session
replay. The table has no column that could hold any of those, `capability` and `ats_type` are
closed lists, and a test fails if a column that could is ever added. That test reads the live
catalog of a database built from every migration
(`tests/integration/test_local_product_events.py`, the column-set and CHECK-list tests), so a
later `alter table` cannot slip past it; a fast tripwire in `tests/test_product_events.py` fails
as soon as any later migration touches the table at all.

Recording is fire-and-forget and fails open, because analytics must never be the reason a person's
request fails or slows down:

* `emit_event` schedules the insert as a background task and returns at once; it does not wait
  for the database and it never raises.
* The insert itself (`write_event`) is bounded by a short timeout and catches every ordinary
  exception, logging one WARNING line (the event name and the error's type and SQLSTATE, never a
  message that could quote a value). Cancellation is not swallowed.
* No more than `MAX_IN_FLIGHT` inserts are ever pending at once: past that an event is dropped
  with a WARNING, so a slow database cannot turn a burst of requests into a burst of waiting
  tasks. The cap is deliberately far below the shared Supabase HTTP client's connection pool
  (100): hung inserts can then never occupy every connection and make an unrelated read wait for
  the insert timeout. A dropped event is the accepted cost; the log is a sample of use, not a
  ledger.
* `setup_required` is the one event a caller can cause over and over with no route's rate limit
  in the way (some routes that raise SETUP_REQUIRED have no limiter, and others raise it before
  theirs runs), so `emit_setup_required` records at most `_SETUP_EVENTS_PER_HOUR` of them per
  user per hour. The count is kept in this process's memory, so with several workers the real
  bound is that number times the worker count. A count cap rather than a "same stop within N
  minutes" dedupe, so every real stop is kept until a stuck loop fills the cap and the latest
  stop a cohort report reads is never stale.

`tracked` is the usual way in for a route that has a clear start and end: it times the work and
decides the outcome from how the block ends, and never changes what the block returns or raises.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, cast, get_args

from postgrest.types import ReturnMethod

from supabase import AsyncClient

from .errors import ApiError
from .search_providers import ats_label_for_url

logger = logging.getLogger(__name__)

# The four Literals below are mirrored, value for value, by CHECK constraints on the table. The
# integration test that reads the live catalog (tests/integration/test_local_product_events.py)
# fails when either side changes without the other, whichever migration changed it.
EventName = Literal[
    "discover_search",
    "setup_required",
    "prepare_finished",
    "artifact_downloaded",
    "extension_fill",
    "artifact_rated",
    "copy_panel_opened",
]
Outcome = Literal["ok", "partial", "failed", "setup_required"]
Capability = Literal[
    "application_answer_generation",
    "company_intel",
    "contact_enrichment",
    "contact_research",
    "gmail_draft",
    "gmail_reply_check",
    "hiring_signals",
    "interview_practice",
    "job_scoring",
    "job_url_ingest",
    "linkedin_discovery",
    "outreach_writer",
    "positioning_brief",
    "prepare_application",
    "profile",
    "warm_path_events",
]
"""Exactly the keys the backend resolves a credential for (`credential_resolver.resolve`) or names
in a SETUP_REQUIRED error, plus `profile` (the missing-resume setup step)."""
AtsType = Literal[
    "amazon",
    "apple",
    "ashby",
    "avature",
    "bamboohr",
    "deshaw",
    "eightfold",
    "google",
    "greenhouse",
    "icims",
    "jobvite",
    "lever",
    "microsoft",
    "oracle",
    "personio",
    "recruitee",
    "smartrecruiters",
    "successfactors",
    "taleo",
    "workable",
    "workday",
    "yc",
]
"""Every applicant-tracking system the job registry polls or the URL classifier recognizes. A
closed list rather than free text so a host name or a URL can never be stored in its place."""

EVENTS: frozenset[str] = frozenset(get_args(EventName))
OUTCOMES: frozenset[str] = frozenset(get_args(Outcome))
CAPABILITIES: frozenset[str] = frozenset(get_args(Capability))
ATS_TYPES: frozenset[str] = frozenset(get_args(AtsType))

DOCUMENT_RESUME = 1
DOCUMENT_COVER_LETTER = 2
"""What `n_a` holds on an `artifact_downloaded` event."""

_TABLE = "product_events"
_INSERT_TIMEOUT_SECONDS = 2.0
MAX_IN_FLIGHT = 20
"""At most this many inserts pending at once. It must stay well under the shared Supabase HTTP
client's pool (httpx allows 100 connections): if every insert hangs, they hold at most this many,
so the connections that remain keep serving real requests instead of waiting out the insert
timeout. A healthy insert takes about ten milliseconds, so this is only reached by a stalled
table or by a burst of roughly two thousand events a second."""
_MAX_COUNT = 2_147_483_647
"""The largest value an `integer` column holds; a count past it is clamped, never an error."""

_MAX_CLASSIFIED_URL_CHARS = 512
"""How much of a posting URL `ats_type_of_url` looks at. Which system a URL is on is decided by
its host and the first segments of its path (a host is at most 253 characters), so the rest never
matters; and the classifier's patterns take time quadratic in the length of an unbroken run of
word characters, so an unbounded, user-supplied URL would stall the event loop."""

_SETUP_EVENTS_PER_HOUR = 30
_SETUP_WINDOW_SECONDS = 3600.0
_SETUP_MAX_USERS = 10_000
_setup_window: dict[str, tuple[float, int]] = {}
"""{user id: (when this user's current window started, setup_required events recorded in it)},
on `time.monotonic`. Bounded by `_SETUP_MAX_USERS`."""

_in_flight: set[asyncio.Task[bool]] = set()
"""Strong references to the insert tasks. The event loop keeps only weak ones, so without this a
task could be collected before it ran."""


def capability_or_none(value: str | None) -> Capability | None:
    """`value` when it is one of the known capability keys, else None (a key added to the
    backend before it is added to the list must not make the insert fail its CHECK)."""
    return cast(Capability, value) if value in CAPABILITIES else None


def ats_type_of_url(url: str | None) -> AtsType | None:
    """The applicant-tracking system a posting URL is on, or None when it is not one this log
    knows (an unknown ATS is recorded as unknown, never guessed). Only the system's name leaves
    this function; the URL itself is never kept."""
    if not url:
        return None
    label = ats_label_for_url(url[:_MAX_CLASSIFIED_URL_CHARS])
    return cast(AtsType, label) if label in ATS_TYPES else None


def _count(value: int | None) -> int | None:
    if value is None:
        return None
    return max(0, min(int(value), _MAX_COUNT))


def _row(
    user_id: str,
    event: EventName,
    *,
    capability: Capability | None,
    application_id: str | None,
    ats_type: AtsType | None,
    outcome: Outcome | None,
    n_a: int | None,
    n_b: int | None,
    duration_ms: int | None,
) -> dict[str, Any]:
    """The insert payload: only what is set (the rest is null by default)."""
    row: dict[str, Any] = {"user_id": user_id, "event": event}
    optional: dict[str, Any] = {
        "capability": capability,
        "application_id": application_id,
        "ats_type": ats_type,
        "outcome": outcome,
        "n_a": _count(n_a),
        "n_b": _count(n_b),
        "duration_ms": _count(duration_ms),
    }
    row.update({key: value for key, value in optional.items() if value is not None})
    return row


async def write_event(supabase: AsyncClient, row: dict[str, Any]) -> bool:
    """Inserts one event row with the service-role client. True when it was stored.

    Never raises for an ordinary failure and never waits longer than the timeout: a failed,
    refused or hung insert is logged at WARNING and reported as False. (`except Exception` does
    not catch cancellation, which must keep propagating.)"""
    try:
        await asyncio.wait_for(
            supabase.table(_TABLE).insert(row, returning=ReturnMethod.minimal).execute(),
            timeout=_INSERT_TIMEOUT_SECONDS,
        )
    except Exception as e:
        # The type and SQLSTATE only: a database error's message can quote the row's values.
        code = getattr(e, "code", None)
        logger.warning(
            "product event not recorded",
            extra={
                "ctx": {
                    "event": row.get("event"),
                    "error_type": f"{type(e).__module__}.{type(e).__qualname__}",
                    **({"error_code": code} if isinstance(code, str) and len(code) <= 16 else {}),
                }
            },
        )
        return False
    return True


def _live_tasks() -> list[asyncio.Task[bool]]:
    """The insert tasks still pending on the running loop. Tasks stranded on a loop that has
    since been closed (a test's, or a restarted worker's) are forgotten here, so they cannot
    count against the cap forever."""
    loop = asyncio.get_running_loop()
    for task in [t for t in _in_flight if t.get_loop().is_closed()]:
        _in_flight.discard(task)
    return [t for t in _in_flight if t.get_loop() is loop]


def emit_event(
    supabase: AsyncClient,
    user_id: str,
    event: EventName,
    *,
    capability: Capability | None = None,
    application_id: str | None = None,
    ats_type: AtsType | None = None,
    outcome: Outcome | None = None,
    n_a: int | None = None,
    n_b: int | None = None,
    duration_ms: int | None = None,
) -> None:
    """Records one event in the background and returns immediately. Never raises.

    `application_id` must be one the caller owns: it is stored as given, so a route passes it
    only after it has checked the application belongs to `user_id`."""
    try:
        if len(_live_tasks()) >= MAX_IN_FLIGHT:
            logger.warning("product event dropped: too many inserts in flight")
            return
        row = _row(
            user_id,
            event,
            capability=capability,
            application_id=application_id,
            ats_type=ats_type,
            outcome=outcome,
            n_a=n_a,
            n_b=n_b,
            duration_ms=duration_ms,
        )
        # Looked up as a module attribute, at call time, so a test can replace the writer.
        task = asyncio.get_running_loop().create_task(write_event(supabase, row))
        task.add_done_callback(_in_flight.discard)
        _in_flight.add(task)
    except Exception:
        logger.warning("product event not scheduled", exc_info=True)


def _purge_expired_setup_windows(now: float) -> None:
    for user_id in [
        u for u, (start, _) in _setup_window.items() if now - start >= _SETUP_WINDOW_SECONDS
    ]:
        del _setup_window[user_id]


def _setup_slot_free(user_id: str, now: float | None = None) -> bool:
    """Takes one of the user's `_SETUP_EVENTS_PER_HOUR` slots in the current hour. False when
    they are all taken, or when this is a new user and `_SETUP_MAX_USERS` others are already
    being counted (memory stays bounded; the event is dropped rather than the table grown)."""
    now = time.monotonic() if now is None else now
    window = _setup_window.get(user_id)
    if window is not None:
        start, count = window
        if now - start < _SETUP_WINDOW_SECONDS:
            if count >= _SETUP_EVENTS_PER_HOUR:
                return False
            _setup_window[user_id] = (start, count + 1)
            return True
    elif len(_setup_window) >= _SETUP_MAX_USERS:
        _purge_expired_setup_windows(now)
        if len(_setup_window) >= _SETUP_MAX_USERS:
            return False
    _setup_window[user_id] = (now, 1)  # a new user, or one whose last window has run out
    return True


def emit_setup_required(supabase: AsyncClient | None, user_id: str | None, error: ApiError) -> None:
    """The `setup_required` event for a SETUP_REQUIRED error shown to a signed-in user. A no-op
    for any other error, when there is no user or no client to record it with, and once the user
    has had `_SETUP_EVENTS_PER_HOUR` recorded in the hour (see the module docstring)."""
    if error.code != "SETUP_REQUIRED" or supabase is None or not isinstance(user_id, str):
        return
    if not _setup_slot_free(user_id):
        logger.debug("setup_required event not recorded: this user's hourly cap is reached")
        return
    emit_event(
        supabase,
        user_id,
        "setup_required",
        capability=capability_or_none(error.capability),
        outcome="setup_required",
    )


async def flush(timeout: float | None = 5.0) -> None:
    """Waits for the inserts that are in flight on this loop (up to `timeout` seconds). For
    shutdown, so the last requests' events are not lost to a deploy, and for tests."""
    tasks = _live_tasks()
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)


@dataclass
class EventDraft:
    """What a `tracked` block fills in as it learns it; the event is written when the block ends."""

    application_id: str | None = None
    ats_type: AtsType | None = None
    n_a: int | None = None
    n_b: int | None = None
    outcome: Outcome = "ok"
    """What the event says if the block ends without raising. Set to "partial" for a run that
    completed but delivered less than all of what was asked."""
    armed: bool = True
    """False means: write nothing, whatever happens. A block that starts with this False and
    sets it True once it is sure the attempt is a real one (the application exists and is the
    caller's) records nothing for a request that never got that far."""


@asynccontextmanager
async def tracked(
    supabase: AsyncClient, user_id: str, event: EventName, *, armed: bool = True
) -> AsyncIterator[EventDraft]:
    """Times a block of work and records `event` when it ends.

    The outcome follows how the block ends: it ran to the end (`draft.outcome`, "ok" unless the
    block says otherwise), it raised SETUP_REQUIRED ("setup_required", with the capability named
    in the error), or it raised anything else ("failed"). An exception always propagates
    unchanged and a return value is never touched. Cancellation records nothing."""
    draft = EventDraft(armed=armed)
    started = time.monotonic()

    def finish(outcome: Outcome, capability: Capability | None = None) -> None:
        if not draft.armed:
            return
        emit_event(
            supabase,
            user_id,
            event,
            capability=capability,
            application_id=draft.application_id,
            ats_type=draft.ats_type,
            outcome=outcome,
            n_a=draft.n_a,
            n_b=draft.n_b,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    try:
        yield draft
    except ApiError as e:
        if e.code == "SETUP_REQUIRED":
            finish("setup_required", capability_or_none(e.capability))
        else:
            finish("failed")
        raise
    except Exception:
        finish("failed")
        raise
    finish(draft.outcome)
