"""The transactional outbox worker (Sprint 2.6d; first subscriber wired
in Horizon Sprint 4.0) -- Proposal §20.

Rows land in `event_outbox` two ways: `change_application_stage`'s
Postgres function (applications_store.change_stage) inserts one inside
the same transaction as the state change it describes; `record_event`'s
`outbox_event_type` param (applications_store.py, Sprint 4.0) appends one
as a second, deliberately non-atomic call for `application.created`/
`application.prepared` -- see that function's own docstring for why. This
module never inserts, only claims and publishes what's already there.

`run_worker_once`/`run_worker_forever` accept `listeners` -- callables
run against each claimed batch (`digest_listener.handle_batch` is the
first, Sprint 4.0). "Publish" still just means "claimed, marked
published" (the claim RPC's own docstring), not "every listener
succeeded" -- there's still no raw connection to hold a claim open across
a listener's own writes, so a listener that raises loses that batch
rather than blocking the claim forever. Acceptable for a listener as
simple and low-risk as the digest one (a deterministic Postgres write,
no network calls) -- a future listener with real failure modes (an
outbound API call, say) would need its own retry/dead-letter story, not
inherited from this loop for free."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast

from supabase import AsyncClient

from .worker_supervision import Sleep, WorkerState, run_supervised

_DEFAULT_BATCH_SIZE = 20
_DEFAULT_POLL_INTERVAL_SECONDS = 5.0

Listener = Callable[[AsyncClient, list[dict[str, Any]]], Awaitable[Any]]


async def run_worker_once(
    supabase: AsyncClient,
    *,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    listeners: list[Listener] | None = None,
) -> list[dict[str, Any]]:
    """Claims and publishes up to `batch_size` unpublished rows in one
    atomic call to `claim_and_publish_outbox_batch` -- real `FOR UPDATE
    SKIP LOCKED` protection against concurrent worker instances, since
    the claim and the `published_at` write happen in the same statement.
    Then hands the claimed batch to every listener in turn. Returns
    whatever it claimed (possibly empty), regardless of what the
    listeners did with it."""
    result = await supabase.rpc("claim_and_publish_outbox_batch", {"p_limit": batch_size}).execute()
    rows = cast(list[dict[str, Any]], result.data)
    if rows:
        for listener in listeners or []:
            await listener(supabase, rows)
    return rows


async def run_worker_forever(
    supabase: AsyncClient,
    *,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
    listeners: list[Listener] | None = None,
    state: WorkerState | None = None,
    sleep: Sleep = asyncio.sleep,
) -> None:
    """Claims and publishes a batch, then sleeps, forever -- supervised, so a
    failed tick is logged and retried rather than ending the worker (see
    worker_supervision.py). Shutdown is `asyncio.CancelledError` from
    app.py's lifespan, as before."""

    async def tick() -> None:
        await run_worker_once(supabase, batch_size=batch_size, listeners=listeners)

    await run_supervised(
        tick,
        state=state or WorkerState(name="outbox", interval_seconds=poll_interval_seconds),
        sleep=sleep,
    )
