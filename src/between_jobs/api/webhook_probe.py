"""A daily health probe for the Telegram webhook.

WHY. Telegram stops delivering messages silently when the webhook's address is wrong,
unreachable, or answers errors: nothing arrives, so nothing shows in this service's own logs.
Telegram is the one party that knows, and `getWebhookInfo` is how to ask it. Once a day this
module asks, turns the answer into a verdict (`assess_webhook_info`), and tells an external
dead-man's switch (a Healthchecks check, via worker_pings.py) whether delivery looks healthy.

THE VERDICT is three-state, because "the answer was fine" and "I could not get an answer" are
different facts and only the first may ever read as healthy:

- `ok`: every field the rules read was present and well-formed, and none of them is a problem.
- `problem`: a definite sign that messages may not be arriving. Reason codes: `no_webhook_url`
  (the bot has no webhook set), `webhook_path_unexpected` (the webhook's address does not end
  in `WEBHOOK_PATH`, the route this API serves, so Telegram is delivering to something else),
  `pending_updates_high` (more than 10 updates are waiting for delivery), `recent_delivery_error`
  and `recent_sync_error` (Telegram reports an error from the last 25 hours), and
  `update_types_excluded` (the bot is subscribed to a list of update types that leaves out
  `message` or `callback_query`, which are all the webhook handler reads). A response with a
  definite problem is a problem even if another of its fields is malformed; the malformed
  field's code is listed after the problem's.
- `unknown`: no verdict is possible. The answer was not an object, or a field the rules read
  was missing or of the wrong type (`url_invalid`, `pending_update_count_invalid`,
  `last_error_date_invalid`, `last_synchronization_error_date_invalid`,
  `allowed_updates_invalid`), or Telegram could not be asked or did not answer properly
  (`telegram_timeout`, `telegram_unreachable`, `telegram_refused`, `telegram_answer_malformed`,
  `probe_timed_out`, `probe_failed`). Fields the rules do not read (`has_custom_certificate`,
  `ip_address`, `max_connections`) are not checked, `last_error_message` is only ever logged,
  and unknown extra fields are ignored.

THE TWO CHECKS THAT NEED NO KNOWLEDGE OF THIS SERVER'S ADDRESS. The webhook's path is compared
with the route this API serves by its END (`endswith`), so a proxy that strips a path prefix
before forwarding does not raise a false alarm every day; only a webhook that was pointed at
some other route (another service's, say) is caught. `allowed_updates` is the list of update
types the bot is subscribed to: an absent, null or empty list is Telegram's default (every type
the handler reads included) and is fine; a non-empty list that leaves out `message` or
`callback_query` means those updates are never delivered, whatever else looks healthy.

WHY `recent_sync_error` IS A PROBLEM. Telegram documents `last_synchronization_error_date` only
as the time of the most recent error while synchronizing available updates with Telegram's own
datacenters, which is not a failure to reach this webhook, and does not say whether it clears.
It is treated as a problem because updates may be delayed while it lasts, and it ages out
after 25 hours like the delivery error; there is nothing for the operator to fix.

WHY 25 HOURS. Telegram's documentation of `WebhookInfo` says that `last_error_date` is the
"most recent error" when delivering an update, and says nothing about when, or whether, that
stops being reported once delivery recovers. So the rule must not depend on it clearing: an
error that is still reported long after delivery recovered would otherwise raise an alarm every
day for ever. Only an error newer than the previous daily probe can be news, so only one from
the last 25 hours (a day, and an hour of slack for the probe's own drift) counts. The cost is
that a real error is reported by the next probe, and by the one after it when that falls
within 25 hours of the error too: at most twice, then it ages out.

WHAT A DAILY PROBE CANNOT SEE. Telegram records a delivery error only when it tries to deliver
an update. A webhook that points at a dead address, on a bot nobody has messaged since, shows
no error and no pending updates until the first message arrives. The probe catches an empty
webhook, a webhook on another route, a narrowed subscription, a backlog, and an error after
traffic; it is not a synthetic end-to-end test. It also does not know this server's public
address, so a webhook re-pointed at another live service on this API's own route (a dev copy
of the API on another host, say) still reads ok: only the host differs, and nothing here says
which host is right. `scripts/set_telegram_webhook.py --info` shows the host Telegram has.

WHAT HAPPENS WITH A VERDICT (`WebhookProbe`). `ok` pings the check as a success, `problem`
pings its `/fail` address and logs one WARNING with the reason codes, `unknown` pings nothing:
the check's period and grace then raise the alarm when the pings stop, which is exactly right
when nobody can tell. A problem is NOT reported to the error tracker, and neither is Telegram
being down: neither is a bug in this service. The probe never raises into its caller.

HOW OFTEN. At most one probe per `PROBE_INTERVAL_SECONDS` (24 hours) of this process's
monotonic clock, kept in memory: a restart probes again at once (after a deploy is exactly
when a wrong webhook shows). An attempt that ends `unknown` is tried again at the next tick
rather than waiting a day, at most `MAX_ATTEMPTS_PER_CYCLE` attempts in a row, so one lost
packet does not leave the check silent for a day and a bad bot token costs three calls, not
one a minute. The caller (the hourly cache-purge worker) decides how often `run_if_due` is
asked; asking is cheap.

WHAT IS LOGGED. Reason codes, the pending-update count, and (for a recent delivery error)
Telegram's `last_error_message` cut to 120 characters, with every URL-shaped run removed (one
with a scheme, one that starts with `//`, and a dotted host name followed by a path) and the log
redactor applied. Never the webhook's address or any part of its path (a path can carry a
secret), never the bot token, never an exception's message: only its type.

Pure of the app: the clock, the monotonic clock and the call to Telegram are injected, so the
tests run it on fakes. It does not import the Telegram client (the channel boundary keeps
that to the adapter, the client and the wiring); `fetch` is whatever the wiring passes in.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, TypeGuard
from urllib.parse import urlsplit

from .logging_setup import redact
from .worker_supervision import Heartbeat

logger = logging.getLogger(__name__)

WEBHOOK_CHECK_ENV = "HEALTHCHECKS_URL_TELEGRAM_WEBHOOK"
"""The setting that holds the check's ping URL (a credential, like the workers' URLs)."""
WEBHOOK_CHECK_NAME = "telegram_webhook"
"""What the check's pinger is called in log lines."""

WEBHOOK_PATH = "/telegram/webhook"
"""The route this API serves Telegram's updates on (telegram_webhook.py), and the path
scripts/set_telegram_webhook.py registers. A webhook whose address does not end in it is
delivering to something else."""
REQUIRED_UPDATE_TYPES = ("message", "callback_query")
"""The update types the webhook handler reads. A bot subscribed to a list without them never
gets them."""

PENDING_UPDATES_LIMIT = 10
"""More than this many updates waiting for delivery is a problem; exactly this many is not."""
ERROR_WINDOW = timedelta(hours=25)
"""An error this recent (inclusive) counts. See "WHY 25 HOURS"."""
FUTURE_TOLERANCE = timedelta(minutes=10)
"""How far ahead of this server's clock Telegram's error time may be before it is read as
garbage rather than as clock drift."""

PROBE_INTERVAL_SECONDS = 24 * 3600.0
RETRY_AFTER_UNKNOWN_SECONDS = 1800.0
"""Half an hour: the cache-purge worker ticks hourly, so the next tick retries."""
MAX_ATTEMPTS_PER_CYCLE = 3
"""Attempts in a row that may end `unknown` before the probe waits the whole interval."""
PROBE_TIMEOUT_SECONDS = 15.0
"""One attempt, in total. The probe runs inside another worker's tick: it must not hold it."""

MAX_ERROR_TEXT_CHARS = 120
_MAX_ERROR_TEXT_SCANNED_CHARS = 400

Status = Literal["ok", "problem", "unknown"]

# -- reason codes: short fixed words, never text that came from outside -----------------------
NO_WEBHOOK_URL = "no_webhook_url"
PENDING_UPDATES_HIGH = "pending_updates_high"
RECENT_DELIVERY_ERROR = "recent_delivery_error"
RECENT_SYNC_ERROR = "recent_sync_error"
WEBHOOK_PATH_UNEXPECTED = "webhook_path_unexpected"
UPDATE_TYPES_EXCLUDED = "update_types_excluded"

RESPONSE_NOT_AN_OBJECT = "response_not_an_object"
URL_INVALID = "url_invalid"
PENDING_UPDATE_COUNT_INVALID = "pending_update_count_invalid"
LAST_ERROR_DATE_INVALID = "last_error_date_invalid"
LAST_SYNC_ERROR_DATE_INVALID = "last_synchronization_error_date_invalid"
ALLOWED_UPDATES_INVALID = "allowed_updates_invalid"

TELEGRAM_TIMEOUT = "telegram_timeout"
TELEGRAM_UNREACHABLE = "telegram_unreachable"
TELEGRAM_REFUSED = "telegram_refused"
TELEGRAM_ANSWER_MALFORMED = "telegram_answer_malformed"
PROBE_TIMED_OUT = "probe_timed_out"
PROBE_FAILED = "probe_failed"

NOT_PROBED_YET = "not_probed_yet"
PROBE_NOT_RUNNING = "probe_not_running"

FETCH_FAILURE_REASONS = frozenset(
    {TELEGRAM_TIMEOUT, TELEGRAM_UNREACHABLE, TELEGRAM_REFUSED, TELEGRAM_ANSWER_MALFORMED}
)
"""The reasons a `fetch` may give for not having an answer."""


class WebhookInfoUnavailable(Exception):
    """`fetch` could not get a usable answer from Telegram. `reason` is one of
    `FETCH_FAILURE_REASONS`, or `PROBE_FAILED` for a failure nobody mapped (the probe reads any
    other word as `PROBE_FAILED` too); the message is that word and nothing else."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class WebhookVerdict:
    status: Status
    reasons: tuple[str, ...] = ()


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _error_state(
    info: Mapping[str, Any], field: str, *, now: datetime
) -> Literal["none", "old", "recent", "malformed"]:
    """Where an optional error-time field stands: absent ("none"), older than `ERROR_WINDOW`
    ("old"), within it ("recent"), or present but not a plausible Unix time ("malformed": not
    an integer, not positive, or further ahead of this clock than `FUTURE_TOLERANCE`)."""
    if field not in info:
        return "none"
    value = info[field]
    # Whole seconds throughout: Telegram's number can be any size JSON allows, and an int that
    # is too big to become a float must not make this raise.
    now_epoch = int(now.timestamp())
    if (
        not _is_int(value)
        or value <= 0
        or value - now_epoch > int(FUTURE_TOLERANCE.total_seconds())
    ):
        return "malformed"
    return "recent" if now_epoch - value <= int(ERROR_WINDOW.total_seconds()) else "old"


def _url_path(url: str) -> str | None:
    """The path of a webhook address, or None when it cannot be parsed. The address itself
    is never kept, logged or put in an error: it is only asked where its path ends."""
    try:
        return urlsplit(url.strip()).path
    except ValueError:
        return None


def assess_webhook_info(info: object, *, now: datetime) -> WebhookVerdict:
    """The verdict on a `getWebhookInfo` result (see the module docstring for the rules).

    For a server that HAS a bot: an empty `url` is a problem here, so call it only then.
    `now` must be timezone-aware. Pure; never raises for any `info`."""
    if not isinstance(info, Mapping):
        return WebhookVerdict("unknown", (RESPONSE_NOT_AN_OBJECT,))

    problems: list[str] = []
    malformed: list[str] = []

    url = info.get("url")
    if not isinstance(url, str):
        malformed.append(URL_INVALID)
    elif not url.strip():
        problems.append(NO_WEBHOOK_URL)
    else:
        path = _url_path(url)
        if path is None:
            malformed.append(URL_INVALID)
        elif not path.endswith(WEBHOOK_PATH):
            problems.append(WEBHOOK_PATH_UNEXPECTED)

    pending = info.get("pending_update_count")
    if not _is_int(pending) or pending < 0:
        malformed.append(PENDING_UPDATE_COUNT_INVALID)
    elif pending > PENDING_UPDATES_LIMIT:
        problems.append(PENDING_UPDATES_HIGH)

    for field, problem_code, malformed_code in (
        ("last_error_date", RECENT_DELIVERY_ERROR, LAST_ERROR_DATE_INVALID),
        ("last_synchronization_error_date", RECENT_SYNC_ERROR, LAST_SYNC_ERROR_DATE_INVALID),
    ):
        state = _error_state(info, field, now=now)
        if state == "recent":
            problems.append(problem_code)
        elif state == "malformed":
            malformed.append(malformed_code)

    # Absent, null or empty is Telegram's default subscription, which includes every type the
    # handler reads. Only a list that names types (all strings) can leave one of them out.
    allowed = info.get("allowed_updates")
    if allowed is not None:
        if not isinstance(allowed, list) or not all(isinstance(kind, str) for kind in allowed):
            malformed.append(ALLOWED_UPDATES_INVALID)
        elif allowed and not set(REQUIRED_UPDATE_TYPES) <= set(allowed):
            problems.append(UPDATE_TYPES_EXCLUDED)

    if problems:
        return WebhookVerdict("problem", tuple(problems + malformed))
    if malformed:
        return WebhookVerdict("unknown", tuple(malformed))
    return WebhookVerdict("ok")


# What counts as URL-shaped, up to the next space: either an optional scheme and then `//`
# (`https://host/path`, `//host/path`), or a dotted host name, with an optional port, followed
# by a path (`host.example/path/secret`). Telegram's wording is not documented, so an address
# quoted WITHOUT its scheme must go too. Over-matching is harmless here (the text is only a log
# hint); a lone host name with no path is not matched, and no ordinary Telegram wording is.
_URL_SHAPED = re.compile(
    r"(?i)(?:\b[a-z][a-z0-9+.\-]{1,15}:)?//\S*"
    r"|\b[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9][a-z0-9\-]*)+(?::\d+)?/\S*"
)


def describe_last_error(info: object) -> str | None:
    """Telegram's `last_error_message`, made safe to log: cut BEFORE it is scanned, every
    URL-shaped run replaced (a webhook's path can carry a secret; see `_URL_SHAPED` for what
    that covers), control characters and runs of spaces collapsed, the log redactor applied,
    then cut to `MAX_ERROR_TEXT_CHARS`. None when there is no message."""
    if not isinstance(info, Mapping):
        return None
    raw = info.get("last_error_message")
    if not isinstance(raw, str):
        return None
    text = _URL_SHAPED.sub("<url>", raw[:_MAX_ERROR_TEXT_SCANNED_CHARS])
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())
    return redact(text)[:MAX_ERROR_TEXT_CHARS] or None


class WebhookProbe:
    """Asks Telegram about its webhook at most once a day and reports what it hears.

    Not thread-safe: it lives on the event loop, called from one worker's tick."""

    def __init__(
        self,
        fetch: Callable[[], Awaitable[object]],
        *,
        heartbeat: Heartbeat | None = None,
        scheduled: bool = True,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        interval_seconds: float = PROBE_INTERVAL_SECONDS,
        retry_seconds: float = RETRY_AFTER_UNKNOWN_SECONDS,
        max_attempts: int = MAX_ATTEMPTS_PER_CYCLE,
        timeout_seconds: float = PROBE_TIMEOUT_SECONDS,
    ) -> None:
        """`fetch` returns the `getWebhookInfo` result or raises `WebhookInfoUnavailable`.
        `heartbeat` is the check's pinger, if one is configured. `scheduled` is False on a
        server where nothing will ever call `run_if_due` (the worker that hosts the probe is
        switched off), so `/health` can say so instead of "not yet"."""
        self._fetch = fetch
        self._heartbeat = heartbeat
        self._scheduled = scheduled
        self._now = now
        self._monotonic = monotonic
        self._interval = interval_seconds
        self._retry = retry_seconds
        self._max_attempts = max(1, max_attempts)
        self._timeout = timeout_seconds
        self._next_attempt_at: float | None = None  # None: due now
        self._unknown_in_a_row = 0
        self.verdict: WebhookVerdict | None = None
        self.checked_at: datetime | None = None

    # -- what /health reads -----------------------------------------------------------------

    def report(self) -> dict[str, Any]:
        """The last verdict as `/health` shows it: status, when it was reached, reason codes.
        Fixed words and a timestamp only -- the body is public."""
        if not self._scheduled:
            return {"status": "unknown", "last_checked_at": None, "reasons": [PROBE_NOT_RUNNING]}
        if self.verdict is None or self.checked_at is None:
            return {"status": "unknown", "last_checked_at": None, "reasons": [NOT_PROBED_YET]}
        return {
            "status": self.verdict.status,
            "last_checked_at": self.checked_at.isoformat(timespec="seconds"),
            "reasons": list(self.verdict.reasons),
        }

    # -- the probe --------------------------------------------------------------------------

    async def run_if_due(self) -> None:
        """Probes, tells the check and logs, if a probe is due; does nothing otherwise.
        Never raises (a cancellation, which is how shutdown reaches it, passes through)."""
        due = self._next_attempt_at
        if due is not None and self._monotonic() < due:
            return
        try:
            now = self._now()
            verdict, info, logged = await self._attempt(now)
            self.verdict, self.checked_at = verdict, now
            self._schedule_next(verdict)
            self._tell_the_check(verdict)
            if not logged:
                self._log(verdict, info)
        except Exception as problem:
            # Bookkeeping or logging itself broke. The next tick may try again; say what, once.
            logger.warning(
                "the telegram webhook probe could not record its result",
                extra={"ctx": {"error_type": _type_name(problem)}},
            )

    async def _attempt(self, now: datetime) -> tuple[WebhookVerdict, object, bool]:
        """(verdict, the raw answer if there was one, whether the failure is already logged).
        Any failure of `fetch` is an `unknown` verdict, never an exception."""
        try:
            async with asyncio.timeout(self._timeout):
                info = await self._fetch()
        except WebhookInfoUnavailable as unavailable:
            reason = unavailable.reason if unavailable.reason in FETCH_FAILURE_REASONS else None
            return WebhookVerdict("unknown", (reason or PROBE_FAILED,)), None, False
        except TimeoutError:
            return WebhookVerdict("unknown", (PROBE_TIMED_OUT,)), None, False
        except Exception as unexpected:
            # Not an answer from Telegram, so a bug or an odd environment. Logged by TYPE only
            # (a message can quote the request URL, which holds the bot token), and not sent to
            # the error tracker: whatever it was, the probe's job is to say "unknown".
            logger.warning(
                "telegram webhook probe: the call failed unexpectedly",
                extra={
                    "ctx": {
                        "status": "unknown",
                        "reasons": PROBE_FAILED,
                        "error_type": _type_name(unexpected),
                    }
                },
            )
            return WebhookVerdict("unknown", (PROBE_FAILED,)), None, True
        return assess_webhook_info(info, now=now), info, False

    def _schedule_next(self, verdict: WebhookVerdict) -> None:
        now = self._monotonic()
        if verdict.status == "unknown":
            self._unknown_in_a_row += 1
            if self._unknown_in_a_row < self._max_attempts:
                self._next_attempt_at = now + self._retry
                return
        self._unknown_in_a_row = 0
        self._next_attempt_at = now + self._interval

    def _tell_the_check(self, verdict: WebhookVerdict) -> None:
        heartbeat = self._heartbeat
        if heartbeat is None or verdict.status == "unknown":
            return  # nothing to say when nobody can tell: the missing ping is the alarm
        try:
            if verdict.status == "ok":
                heartbeat.succeeded()
            else:
                heartbeat.failed()
        except Exception:
            logger.warning("the telegram webhook probe could not ping its check")

    def _log(self, verdict: WebhookVerdict, info: object) -> None:
        ctx: dict[str, Any] = {"status": verdict.status, "reasons": ",".join(verdict.reasons)}
        if isinstance(info, Mapping) and _is_int(info.get("pending_update_count")):
            ctx["pending_updates"] = info["pending_update_count"]
        if verdict.status == "ok":
            logger.info("telegram webhook probe: delivery looks healthy", extra={"ctx": ctx})
            return
        if RECENT_DELIVERY_ERROR in verdict.reasons:
            last_error = describe_last_error(info)
            if last_error is not None:
                ctx["last_error"] = last_error
        if verdict.status == "problem":
            logger.warning(
                "telegram webhook probe: messages may not be arriving", extra={"ctx": ctx}
            )
        else:
            logger.warning(
                "telegram webhook probe: could not tell whether messages are arriving",
                extra={"ctx": ctx},
            )


def _type_name(error: BaseException) -> str:
    return f"{type(error).__module__}.{type(error).__qualname__}"


def webhook_report(probe: WebhookProbe | None) -> dict[str, Any]:
    """The `telegram_webhook` block of `/health`: the probe's report, or `not_configured` on
    a server with no Telegram bot. Never changes `/health`'s status code."""
    if probe is None:
        return {"status": "not_configured", "last_checked_at": None, "reasons": []}
    return probe.report()
