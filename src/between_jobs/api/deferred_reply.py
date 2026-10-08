"""Supervised background work for a reply that outlives the request that asked for it.

Generating a resume takes tens of seconds, and Telegram stops waiting on a webhook long
before that: it times the request out and delivers the same update again. So the webhook
does the quick part inline (the per-user limit, the "this can take a minute" message),
answers 200 at once, and hands the slow part to `DeferredReplies`, which runs it as a task of
its own and sends the person the result when it is done.

What the registry is responsible for, because asyncio will not be:

- Strong references. The event loop keeps only a weak reference to a task, so a task nobody
  else holds can be garbage-collected mid-run. The registry holds each one until it ends.
- A bound. At most `max_concurrent` (3 by default) run at once, across every user in this
  process, and a caller that passes a `key` (the person's verified user id) gets at most one
  in flight per key: otherwise one person tapping a button three times would hold every place
  and everyone else would be told "busy". `try_reserve` answers None for a request that is over
  either limit (`holds` says which), which the caller turns into a message. It never queues: a
  queue behind a slow engine is how a spammer turns into unbounded work, and a person who is
  told "busy" can simply ask again. This is on top of the per-user hourly limit
  (`rate_limits`), not instead of it.
  A place is held from `try_reserve` until it is released -- when the task ends, or when the
  caller gives it back because nothing will run -- so a caller should reserve as late as it can
  and release as early as it can.
- Failure containment. An exception in the work is logged (`logger.exception`, carrying the
  request id of the delivery that started it and its update id), reported to the error tracker
  when one is configured (error_reporting.py; this task runs outside any request, so nothing
  else would see it), and the person is told something went wrong, through the `on_failure`
  callback; a failure of that message is logged too, never raised. Nothing escapes into the
  event loop's "task exception was never retrieved" handler.
- Shutdown. `shutdown` waits a short grace period for what is running to finish, then
  cancels the rest and waits for them to end. A task that is cancelled says nothing to the
  person: shutdown is not a failure they should be told about.

What happens to work in flight when the process dies or restarts: it is lost. The webhook's
update claim (`telegram_updates_store`) was completed when the handler answered, so Telegram
does not redeliver the update, and nothing resumes the work. The person gets neither a resume
nor an error message and has to send the request again -- the "this can take a minute" message
they already got is the only trace. A deploy therefore costs whoever was waiting at that
moment one repeat. (The grace period in `shutdown` exists so a deploy that lands just as a
generation is finishing does not cost it.) Persisting the work and resuming it on boot would
fix that and is a larger change than this module is.

The registry is per process. With several API processes each has its own cap and its own
one-per-user rule, so the fleet-wide bound is the cap times the number of processes.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from .error_reporting import report_exception

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENT = 3
SHUTDOWN_GRACE_SECONDS = 5.0
"""How long `shutdown` lets running work finish before cancelling it. Short on purpose: a
platform stops a container a few seconds after asking it to, and a generation that needs
longer than this would not have finished anyway."""


class Slot:
    """One of the registry's places. Taken with `try_reserve`, given to `start` (after which
    the task owns it) or given back with `release`. Releasing twice is harmless. Releasing
    frees the place and the key it was reserved under, together."""

    def __init__(
        self, semaphore: asyncio.Semaphore, active_keys: set[str], key: str | None
    ) -> None:
        self._semaphore = semaphore
        self._active_keys = active_keys
        self._key = key
        self._released = False

    def release(self) -> None:
        if not self._released:
            self._released = True
            if self._key is not None:
                self._active_keys.discard(self._key)
            self._semaphore.release()


class DeferredReplies:
    def __init__(self, max_concurrent: int = DEFAULT_MAX_CONCURRENT) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._tasks: set[asyncio.Task[None]] = set()
        self._active_keys: set[str] = set()

    @property
    def running(self) -> int:
        return len(self._tasks)

    def holds(self, key: str) -> bool:
        """Whether a place reserved under `key` is still held."""
        return key in self._active_keys

    async def try_reserve(self, key: str | None = None) -> Slot | None:
        """A place for one more piece of work, or None if all are taken or `key` already holds
        one (`holds` tells the two apart). Never waits: with a place free the acquire below
        completes without suspending, so the checks and the take are one step as far as any
        other task can tell."""
        if key is not None and key in self._active_keys:
            return None
        if self._semaphore.locked():
            return None
        await self._semaphore.acquire()
        if key is not None:
            self._active_keys.add(key)
        return Slot(self._semaphore, self._active_keys, key)

    def start(
        self,
        slot: Slot,
        work: Callable[[], Awaitable[None]],
        *,
        name: str,
        context: Mapping[str, Any] | None = None,
        on_failure: Callable[[], Awaitable[None]] | None = None,
    ) -> asyncio.Task[None]:
        """Runs `work()` as a task and returns it. The task owns `slot` from here and gives it
        back when it ends, however it ends -- including being cancelled before it ever ran.

        `context` (ids and counts only, like every log context) is added to the lines logged
        about this work. `on_failure` is called after an exception in `work`, to tell the
        person; it is not called for a cancellation."""
        task = asyncio.create_task(
            self._run(work, name, dict(context or {}), on_failure), name=f"deferred:{name}"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(lambda _task: slot.release())
        return task

    async def _run(
        self,
        work: Callable[[], Awaitable[None]],
        name: str,
        context: dict[str, Any],
        on_failure: Callable[[], Awaitable[None]] | None,
    ) -> None:
        ctx = {"task": name, **context}
        try:
            await work()
        except asyncio.CancelledError:
            logger.info("deferred work cancelled; the user is not messaged", extra={"ctx": ctx})
            raise
        except Exception as failure:
            logger.exception("deferred work failed", extra={"ctx": ctx})
            # Before the user is told: a failing `on_failure` must not cost the report. The
            # tags are the task's name only; the request and update ids belong in the logs.
            report_exception(failure, tags={"task": name})
            if on_failure is not None:
                try:
                    await on_failure()
                except Exception:
                    logger.exception(
                        "could not tell the user the deferred work failed", extra={"ctx": ctx}
                    )

    async def shutdown(self, *, grace_seconds: float | None = None) -> None:
        """Waits up to `grace_seconds` (default `SHUTDOWN_GRACE_SECONDS`) for running work,
        cancels what is left and waits for it to end. Safe to call with nothing running, and
        more than once."""
        grace = SHUTDOWN_GRACE_SECONDS if grace_seconds is None else grace_seconds
        loop = asyncio.get_running_loop()
        # A task of a loop that no longer exists can neither finish nor be awaited here.
        self._tasks.difference_update({t for t in self._tasks if t.get_loop() is not loop})
        if not self._tasks:
            return
        _done, pending = await asyncio.wait(set(self._tasks), timeout=grace)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


registry = DeferredReplies()
"""The process's registry, shared by every request. Tests that need their own bound make a
`DeferredReplies` and pass it to the code under test."""


async def shutdown_deferred_replies() -> None:
    """Called once by the app's lifespan, before the clients the work uses are closed."""
    await registry.shutdown()
