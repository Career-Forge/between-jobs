"""The keeper side of one worker instance, enforced (launch plan P2.19, v1).

Three workers -- the job registry poller, the saved-search matcher and the Gmail
reply checker -- select their work with no claim at all, so two copies of the API
(an old and a new container overlapping in a Railway deploy, or an accidental
second replica) would each do all of it, and the matcher and the checker spend the
user's own LLM credit per tick. They hold a lease on a row in `worker_leases`
(see the migration and `claim_worker_lease`); this module keeps that lease alive.

HEARTBEAT, NOT PER-TICK. A lease claimed once per tick with a TTL of a few
intervals is wrong in both directions: the tick has no upper bound (the matcher's
LLM call alone defaults to a 600 s read timeout with 2 retries), so a lease sized
to the interval can lapse mid-tick; and one sized generously leaves the matcher's
successor waiting hours after a SIGKILL. So the keeper renews a short, fixed lease
for the whole life of the process -- asleep or mid-tick -- and `run_supervised`
merely asks, with no I/O, whether it may start a tick.

    TTL 60 s, renewed every 15 s. A holder believes it holds until 58 s after it
    SENT its last successful claim (monotonic clock; 2 s of slack), so it always
    believes it holds a little LESS than the database does. No new tick starts
    inside the last 15 s of that belief. Two lost heartbeats change nothing; an
    outage under about 43 s is invisible to ticks; at 58 s with no answer the
    worker is unknown.

UNKNOWN IS NOT STANDBY. A claim that returns false is a definite answer: someone
else holds it, and this process waits (healthy). A claim that raises, times out, or
returns something that is not a boolean says nothing, and is never read as false.
A holder keeps its pessimistic local deadline through it; a process that has no
answer at all is `unknown`, which is unhealthy, so a missing migration or a broken
grant fails the deploy healthcheck instead of idling behind a 200.

NO CANCELLING. Losing the lease never cancels a running tick: cancellation loses
data (the outbox marks rows published inside its claim; the Gmail checker advances
its watermark before classifying), and a second concurrent tick of these three
workers loses none. The overlap is bounded to one in-flight tick and arises only
when the keeper cannot reach the database for longer than about 58 s or the process
is blocked that long. Writes are not fenced; that is a later upgrade if overlap is
ever seen.

Pure of Supabase: the claim is an injected coroutine function (worker_leases_store
.claim_worker_lease bound to a client), and the clock and sleep are injectable, so
tests run the whole thing on a virtual clock.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import httpx
from postgrest.exceptions import APIError

from .worker_leases_store import LeaseAnswerError

logger = logging.getLogger(__name__)

TTL_SECONDS = 60
RENEW_SECONDS = 15.0
STANDBY_POLL_SECONDS = 10.0
CLAIM_TIMEOUT_SECONDS = 10.0
LOCAL_SLACK_SECONDS = 2.0
TICK_START_MARGIN_SECONDS = 15.0
STANDBY_FRESH_SECONDS = 30.0
"""How long a "someone else holds it" answer is trusted without a newer one:
three standby polls, so one lost poll is not enough to turn a healthy standby
into `unknown`."""
MIN_CYCLE_SECONDS = 0.5
"""A floor under the keeper's pause between attempts, so a claim that always takes
its full timeout can never turn the loop into a hot one."""

FUNCTION_MISSING = "PGRST202"
"""PostgREST's "no such function in the schema cache": the migration hasn't run."""

LeaseState = Literal["held", "standby", "unknown"]


def _always() -> bool:
    return True


class WorkerLease:
    def __init__(
        self,
        worker: str,
        *,
        claim: Callable[[], Awaitable[bool]],
        holder: str = "",
        wants_lease: Callable[[], bool] = _always,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        claim_timeout: float = CLAIM_TIMEOUT_SECONDS,
    ) -> None:
        self.worker = worker
        self._claim = claim
        self._holder = holder  # for log lines only; never in a response body
        self._wants_lease = wants_lease
        self._monotonic = monotonic
        self._sleep = sleep
        self._claim_timeout = claim_timeout
        self._deadline: float | None = None
        self._standby_at: float | None = None
        self._logged: LeaseState | None = None
        self._answered = asyncio.Event()
        self._gate_error_logged = False
        self._task: asyncio.Task[None] | None = None
        self.claim_failures = 0
        self.last_error: str | None = None

    # -- what the supervisor and /health ask (local reads, no I/O) ---------------

    def status(self) -> LeaseState:
        now = self._monotonic()
        if self._deadline is not None and now < self._deadline:
            return "held"
        if self._standby_at is not None and now - self._standby_at <= STANDBY_FRESH_SECONDS:
            return "standby"
        return "unknown"

    def may_tick(self) -> bool:
        """Whether a tick may START now: held, with at least the margin of belief
        left. Read with no `await` between this and starting the tick."""
        return (
            self._deadline is not None
            and self._monotonic() + TICK_START_MARGIN_SECONDS < self._deadline
        )

    @property
    def crashed(self) -> bool:
        """The keeper task ended on its own (not by `stop`). Per-attempt failures
        are contained, so only a BaseException gets here."""
        return self._task is not None and self._task.done() and not self._task.cancelled()

    def report(self) -> dict[str, Any]:
        return {
            "state": self.status(),
            "claim_failures": self.claim_failures,
            "last_claim_error": self.last_error,
        }

    # -- lifecycle ----------------------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run(), name=f"worker-lease:{self.worker}")

    async def first_answer(self) -> None:
        """Returns once the first attempt has finished -- answered or failed, never
        left pending -- so startup can wait for a real state (bounded by the
        claim timeout) before it serves a /health request."""
        await self._answered.wait()

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        if task.done():
            # It ended on its own (see `crashed`). Say why, once, here: otherwise
            # the exception is only ever reported by the garbage collector.
            if not task.cancelled() and (error := task.exception()) is not None:
                logger.error(
                    "worker lease keeper had ended with an error",
                    exc_info=(type(error), error, error.__traceback__),
                    extra={"ctx": self._ctx()},
                )
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("worker lease keeper ended with an error", extra={"ctx": self._ctx()})

    async def run(self) -> None:
        while True:
            cycle_start = self._monotonic()
            if self._wants():
                await self._attempt()
            self._answered.set()
            period = RENEW_SECONDS if self.status() == "held" else STANDBY_POLL_SECONDS
            await self._sleep(max(MIN_CYCLE_SECONDS, cycle_start + period - self._monotonic()))

    # -- one claim ----------------------------------------------------------------

    def _wants(self) -> bool:
        # While the worker's tick has run past its own stale window, stop renewing
        # and let the lease lapse: otherwise a heartbeating but wedged holder would
        # block every standby forever, and Railway never re-checks health after
        # go-live. A callable that itself breaks must not stop the heartbeat.
        try:
            return self._wants_lease()
        except Exception:
            if not self._gate_error_logged:  # once: it would otherwise repeat every cycle
                self._gate_error_logged = True
                logger.warning(
                    "worker lease wedge gate failed; renewing anyway",
                    extra={"ctx": self._ctx()},
                    exc_info=True,
                )
            return True

    async def _attempt(self) -> None:
        sent_at = self._monotonic()
        try:
            async with asyncio.timeout(self._claim_timeout):
                held = await self._claim()
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            self._known_failure("timeout")
        except APIError as e:
            self._known_failure("function_missing" if e.code == FUNCTION_MISSING else "database")
        except LeaseAnswerError:
            self._known_failure("malformed")
        except httpx.HTTPError:
            self._known_failure("transport")
        except Exception:
            # Anything else is a bug here or in the store: say so, with the traceback,
            # on the same doubling schedule as every other failure.
            if self._record_failure("unexpected"):
                logger.warning(
                    "could not claim the worker lease",
                    extra={"ctx": {**self._ctx(), "kind": "unexpected"}},
                    exc_info=True,
                )
        else:
            self._answered_with(held, sent_at)

    def _answered_with(self, held: bool, sent_at: float) -> None:
        self.claim_failures = 0
        self.last_error = None
        if held:
            self._deadline = sent_at + TTL_SECONDS - LOCAL_SLACK_SECONDS
            self._standby_at = None
        else:
            # An explicit refusal clears any belief at once: it was just told no.
            self._deadline = None
            self._standby_at = self._monotonic()
        self._log_transition()

    def _record_failure(self, kind: str) -> bool:
        """Bookkeeping for a claim that said nothing. Returns whether this one
        deserves a log line: the 1st, 2nd, 4th, 8th ... in a row, so a worker stuck
        failing for hours does not bury the log."""
        self.claim_failures += 1
        self.last_error = kind
        # The pessimistic deadline and the last standby answer are left alone: a
        # failure is neither "held" nor "standby", and only time ages them out.
        self._log_transition()
        n = self.claim_failures
        return n & (n - 1) == 0

    def _known_failure(self, kind: str) -> None:
        if not self._record_failure(kind):
            return
        if kind == "function_missing":
            logger.error(
                "worker lease function is missing; apply the database migrations",
                extra={"ctx": {**self._ctx(), "kind": kind}},
            )
        else:
            logger.warning(
                "could not claim the worker lease",
                extra={
                    "ctx": {
                        **self._ctx(),
                        "kind": kind,
                        "consecutive_failures": self.claim_failures,
                    }
                },
            )

    def _log_transition(self) -> None:
        """Lease transitions only, never one line per claim: a standby asks every
        10 s for as long as the other holder lives."""
        state = self.status()
        if state != self._logged:
            logger.info(
                "worker lease state changed", extra={"ctx": {**self._ctx(), "state": state}}
            )
            self._logged = state

    def _ctx(self) -> dict[str, str]:
        return {"worker": self.worker, "holder": self._holder}
