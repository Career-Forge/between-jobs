"""The Telegram webhook probe (webhook_probe.py): the pure verdict on a `getWebhookInfo` result,
the once-a-day gating on a fake clock, what is pinged and logged for each verdict, and that a
failing Telegram never raises into the caller.

The call to Telegram and the check's pinger are fakes here; the real client's behaviour is in
test_telegram_client_webhook_info.py and the wiring into the app in test_webhook_probe_wiring.py."""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fake_sentry import FakeSentry
from rendered_logs import rendered

from between_jobs.api import error_reporting, logging_setup, webhook_probe
from between_jobs.api.webhook_probe import (
    ERROR_WINDOW,
    FUTURE_TOLERANCE,
    MAX_ATTEMPTS_PER_CYCLE,
    PENDING_UPDATES_LIMIT,
    WebhookInfoUnavailable,
    WebhookProbe,
    WebhookVerdict,
    assess_webhook_info,
    describe_last_error,
    webhook_report,
)

NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
PROBE_LOGGER = "between_jobs.api.webhook_probe"
# Assembled at runtime so no literal in this public repo looks like a real bot token.
BOT_TOKEN = "123456789:" + "FAKEfakeFAKEfake" + "0123456789abcdefXY"
WEBHOOK_URL = "https://api.example.test/telegram/webhook"
# An address with a secret in a path prefix and in the query, behind this API's own route.
SECRET_WEBHOOK_URL = "https://api.example.test/s3cr3t-prefix/telegram/webhook?token=s3cr3t-query"
# An address on another route altogether, with a secret in its path.
SECRET_OTHER_ROUTE_URL = "https://api.example.test/s3cr3t-path/hook?token=s3cr3t-query"


def epoch(ago: timedelta) -> int:
    return int((NOW - ago).timestamp())


def healthy_info(**overrides: Any) -> dict[str, Any]:
    """What Telegram answers for a webhook that is working: every documented field."""
    info: dict[str, Any] = {
        "url": WEBHOOK_URL,
        "has_custom_certificate": False,
        "pending_update_count": 0,
        "ip_address": "203.0.113.10",
        "max_connections": 40,
        "allowed_updates": ["message", "callback_query"],
    }
    info.update(overrides)
    return info


def verdict_of(info: object) -> WebhookVerdict:
    return assess_webhook_info(info, now=NOW)


# --- the verdict: the rules, one at a time -----------------------------------------------------


def test_a_healthy_webhook_is_ok() -> None:
    assert verdict_of(healthy_info()) == WebhookVerdict("ok", ())


def test_only_the_fields_the_rules_read_are_required() -> None:
    assert verdict_of({"url": WEBHOOK_URL, "pending_update_count": 0}).status == "ok"


def test_extra_and_unreadable_unused_fields_change_nothing() -> None:
    info = healthy_info(
        brand_new_field_from_a_future_bot_api={"nested": [1, 2, 3]},
        max_connections="forty",  # wrong type, but no rule reads it
        ip_address=12345,
        has_custom_certificate="yes",
        last_error_message=["not", "a", "string"],  # only ever logged, and only if usable
    )
    assert verdict_of(info) == WebhookVerdict("ok", ())


@pytest.mark.parametrize("url", ["", "   ", "\n\t"])
def test_a_bot_with_no_webhook_is_a_problem(url: str) -> None:
    assert verdict_of(healthy_info(url=url)) == WebhookVerdict("problem", ("no_webhook_url",))


@pytest.mark.parametrize(("pending", "status"), [(0, "ok"), (1, "ok"), (10, "ok"), (11, "problem")])
def test_the_pending_update_limit_is_above_ten(pending: int, status: str) -> None:
    assert PENDING_UPDATES_LIMIT == 10
    verdict = verdict_of(healthy_info(pending_update_count=pending))
    assert verdict.status == status
    assert verdict.reasons == (() if status == "ok" else ("pending_updates_high",))


def test_a_huge_backlog_is_just_a_problem() -> None:
    assert verdict_of(healthy_info(pending_update_count=10**12)).reasons == (
        "pending_updates_high",
    )


# --- the webhook's path: is Telegram delivering to this API's route? ---------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.test/telegram/webhook",
        "https://api.example.test:8443/telegram/webhook",
        "https://api.example.test/prefix/telegram/webhook",  # a proxy that strips a prefix
        "https://api.example.test/telegram/webhook?unused=1",  # the query is not the path
        "  https://api.example.test/telegram/webhook  ",
        SECRET_WEBHOOK_URL,
    ],
)
def test_a_webhook_ending_in_this_apis_route_is_ok(url: str) -> None:
    assert webhook_probe.WEBHOOK_PATH == "/telegram/webhook"
    assert verdict_of(healthy_info(url=url)) == WebhookVerdict("ok", ())


@pytest.mark.parametrize(
    "url",
    [
        "https://n8n.example.test/webhook/0a1b2c/webhook",  # another service's trigger
        "https://api.example.test/",
        "https://api.example.test",
        "https://api.example.test/hook",
        "https://api.example.test/telegram/webhook/",  # a redirect Telegram does not follow
        "https://api.example.test/telegram/webhook/extra",
        "https://api.example.test/mytelegram/webhook",
        "https://api.example.test/telegram/webhooks",
        "https://api.example.test/hook?next=/telegram/webhook",  # only the query ends right
        "https://api.example.test/hook#/telegram/webhook",
    ],
)
def test_a_webhook_on_another_route_is_a_problem(url: str) -> None:
    assert verdict_of(healthy_info(url=url)) == WebhookVerdict(
        "problem", ("webhook_path_unexpected",)
    )


@pytest.mark.parametrize("url", ["https://[::1/telegram/webhook", "http://[bad/telegram/webhook"])
def test_a_webhook_address_that_cannot_be_parsed_is_unknown(url: str) -> None:
    assert verdict_of(healthy_info(url=url)) == WebhookVerdict("unknown", ("url_invalid",))


async def test_the_path_of_a_webhook_on_another_route_is_never_logged_or_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=PROBE_LOGGER)
    check = Check()
    probe = make(Telegram(healthy_info(url=SECRET_OTHER_ROUTE_URL)), Clock(), check)

    await probe.run_if_due()

    assert check.calls == ["failed"]
    assert probe.report()["reasons"] == ["webhook_path_unexpected"]
    [record] = [r for r in caplog.records if r.name == PROBE_LOGGER]
    assert record.levelno == logging.WARNING
    assert record.ctx["reasons"] == "webhook_path_unexpected"  # type: ignore[attr-defined]
    for text in (caplog.text, rendered([record]), repr(record.ctx), repr(probe.report())):  # type: ignore[attr-defined]
        assert "s3cr3t" not in text and "api.example.test" not in text


# --- the update types the bot is subscribed to -------------------------------------------------


@pytest.mark.parametrize(
    "allowed",
    [
        None,  # null: Telegram's default, every type
        [],  # empty: the same
        ["message", "callback_query"],  # exactly what set_telegram_webhook.py registers
        ["callback_query", "message"],  # order is no matter
        ["message", "callback_query", "chat_member"],  # more than needed is fine
    ],
)
def test_a_subscription_that_includes_what_the_handler_reads_is_ok(allowed: object) -> None:
    assert verdict_of(healthy_info(allowed_updates=allowed)) == WebhookVerdict("ok", ())


def test_a_missing_allowed_updates_is_telegrams_default_and_ok() -> None:
    info = healthy_info()
    del info["allowed_updates"]
    assert verdict_of(info) == WebhookVerdict("ok", ())


@pytest.mark.parametrize(
    "allowed",
    [
        ["message"],
        ["callback_query"],
        ["edited_channel_post"],
        ["chat_member", "inline_query"],
        ["message", "edited_message"],
        ["Message", "Callback_Query"],  # Telegram's names are lower case; these match nothing
        [""],
    ],
)
def test_a_subscription_that_leaves_out_a_type_the_handler_reads_is_a_problem(
    allowed: list[str],
) -> None:
    assert verdict_of(healthy_info(allowed_updates=allowed)) == WebhookVerdict(
        "problem", ("update_types_excluded",)
    )


@pytest.mark.parametrize(
    "allowed",
    [["message", 5], ["message", None], [["message"]], "message", {"message": 1}, 5, True, 1.5],
    ids=repr,
)
def test_a_subscription_that_is_not_a_list_of_names_is_unknown(allowed: object) -> None:
    assert verdict_of(healthy_info(allowed_updates=allowed)) == WebhookVerdict(
        "unknown", ("allowed_updates_invalid",)
    )


def test_problems_are_listed_before_malformed_fields_and_in_a_fixed_order() -> None:
    info = healthy_info(
        url="https://n8n.example.test/webhook/x/webhook",
        pending_update_count=50,
        last_error_date=epoch(timedelta(hours=1)),
        allowed_updates=["message"],
    )
    assert verdict_of(info).reasons == (
        "webhook_path_unexpected",
        "pending_updates_high",
        "recent_delivery_error",
        "update_types_excluded",
    )
    broken = healthy_info(url="", pending_update_count="many", allowed_updates=["message", 5])
    assert verdict_of(broken) == WebhookVerdict(
        "problem", ("no_webhook_url", "pending_update_count_invalid", "allowed_updates_invalid")
    )


@pytest.mark.parametrize("field", ["last_error_date", "last_synchronization_error_date"])
@pytest.mark.parametrize(
    ("ago", "status"),
    [
        (timedelta(minutes=1), "problem"),
        (timedelta(hours=1), "problem"),
        (timedelta(hours=24), "problem"),
        (ERROR_WINDOW, "problem"),  # inclusive: 25 hours ago is still within the last 25 hours
        (ERROR_WINDOW + timedelta(seconds=1), "ok"),
        (timedelta(hours=26), "ok"),
        (timedelta(days=3), "ok"),
        (timedelta(days=3650), "ok"),
    ],
)
def test_an_error_counts_only_if_it_is_within_the_last_25_hours(
    field: str, ago: timedelta, status: str
) -> None:
    assert ERROR_WINDOW.total_seconds() == 25 * 3600
    code = "recent_delivery_error" if field == "last_error_date" else "recent_sync_error"
    verdict = verdict_of(healthy_info(**{field: epoch(ago)}))
    assert verdict.status == status
    assert verdict.reasons == ((code,) if status == "problem" else ())


def test_an_old_error_that_telegram_keeps_reporting_never_alerts() -> None:
    """The docs do not say when `last_error_date` stops being reported once delivery recovers.
    A webhook that failed last week and has worked since still carries the error, and must read
    ok -- today, tomorrow and every day after."""
    stale = healthy_info(
        last_error_date=epoch(timedelta(days=6)), last_error_message="Connection timed out"
    )
    for days_later in range(30):
        assert assess_webhook_info(stale, now=NOW + timedelta(days=days_later)) == WebhookVerdict(
            "ok", ()
        )


@pytest.mark.parametrize(
    ("first_probe_after", "expected"),
    [
        # Probes are about 24 hours apart. The first probe after an error sees it; the next one
        # does too only if it falls within 25 hours of the error; the third never does.
        (timedelta(minutes=30), ["problem", "problem", "ok"]),
        (timedelta(hours=2), ["problem", "ok", "ok"]),
    ],
)
def test_one_error_is_news_for_at_most_two_daily_probes(
    first_probe_after: timedelta, expected: list[str]
) -> None:
    info = healthy_info(last_error_date=int(NOW.timestamp()))
    first_probe = NOW + first_probe_after
    verdicts = [
        assess_webhook_info(info, now=first_probe + timedelta(hours=24 * n)).status
        for n in range(3)
    ]
    assert verdicts == expected


def test_both_error_fields_are_both_reported() -> None:
    info = healthy_info(
        last_error_date=epoch(timedelta(hours=2)),
        last_synchronization_error_date=epoch(timedelta(hours=3)),
        pending_update_count=50,
    )
    assert verdict_of(info).reasons == (
        "pending_updates_high",
        "recent_delivery_error",
        "recent_sync_error",
    )


@pytest.mark.parametrize(
    ("field", "problem_code", "malformed_code"),
    [
        ("last_error_date", "recent_delivery_error", "last_error_date_invalid"),
        (
            "last_synchronization_error_date",
            "recent_sync_error",
            "last_synchronization_error_date_invalid",
        ),
    ],
)
@pytest.mark.parametrize(
    ("ahead", "status"),
    [
        (timedelta(0), "problem"),
        (timedelta(minutes=5), "problem"),  # a little ahead: clock drift, not garbage
        (FUTURE_TOLERANCE, "problem"),  # inclusive: exactly at the limit is still drift
        (FUTURE_TOLERANCE + timedelta(seconds=1), "unknown"),
        (timedelta(hours=1), "unknown"),
        (timedelta(days=1), "unknown"),
        (timedelta(days=2), "unknown"),  # far ahead: unknown, never ok
    ],
)
def test_an_error_time_ahead_of_our_clock_counts_only_within_the_tolerance(
    field: str, problem_code: str, malformed_code: str, ahead: timedelta, status: str
) -> None:
    assert FUTURE_TOLERANCE.total_seconds() == 600  # a literal, like the 25 hours above
    verdict = verdict_of(healthy_info(**{field: int((NOW + ahead).timestamp())}))
    assert verdict.status == status
    assert verdict.reasons == ((problem_code,) if status == "problem" else (malformed_code,))


# --- the verdict: what is missing or malformed is unknown, never ok ----------------------------


@pytest.mark.parametrize("answer", [None, [], [healthy_info()], "ok", 7, 1.5, True, b"{}"])
def test_an_answer_that_is_not_an_object_is_unknown(answer: object) -> None:
    assert verdict_of(answer) == WebhookVerdict("unknown", ("response_not_an_object",))


def test_an_empty_object_is_unknown_for_both_required_fields() -> None:
    assert verdict_of({}) == WebhookVerdict(
        "unknown", ("url_invalid", "pending_update_count_invalid")
    )


def test_a_missing_url_is_unknown_not_a_problem_and_not_ok() -> None:
    info = healthy_info()
    del info["url"]
    assert verdict_of(info) == WebhookVerdict("unknown", ("url_invalid",))


def test_a_missing_pending_count_is_unknown() -> None:
    info = healthy_info()
    del info["pending_update_count"]
    assert verdict_of(info) == WebhookVerdict("unknown", ("pending_update_count_invalid",))


@pytest.mark.parametrize("url", [None, 5, 1.5, True, ["https://x"], {"a": 1}])
def test_a_url_of_the_wrong_type_is_unknown(url: object) -> None:
    assert verdict_of(healthy_info(url=url)) == WebhookVerdict("unknown", ("url_invalid",))


@pytest.mark.parametrize("pending", [None, "3", "", 3.0, 2.5, True, False, -1, [0], {"n": 0}])
def test_a_pending_count_of_the_wrong_type_or_value_is_unknown(pending: object) -> None:
    assert verdict_of(healthy_info(pending_update_count=pending)) == WebhookVerdict(
        "unknown", ("pending_update_count_invalid",)
    )


@pytest.mark.parametrize(
    "value", [None, "1700000000", "", 1700000000.5, True, 0, -5, [1], {"t": 1}, 10**30]
)
@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("last_error_date", "last_error_date_invalid"),
        ("last_synchronization_error_date", "last_synchronization_error_date_invalid"),
    ],
)
def test_an_error_time_that_is_not_a_plausible_unix_time_is_unknown(
    field: str, code: str, value: object
) -> None:
    assert verdict_of(healthy_info(**{field: value})) == WebhookVerdict("unknown", (code,))


def test_a_definite_problem_wins_over_a_malformed_field_and_lists_both() -> None:
    info = healthy_info(url="", pending_update_count="many")
    assert verdict_of(info) == WebhookVerdict(
        "problem", ("no_webhook_url", "pending_update_count_invalid")
    )


def test_a_recent_error_with_a_malformed_sibling_is_still_a_problem() -> None:
    info = healthy_info(
        last_error_date=epoch(timedelta(hours=1)), last_synchronization_error_date="x"
    )
    assert verdict_of(info) == WebhookVerdict(
        "problem", ("recent_delivery_error", "last_synchronization_error_date_invalid")
    )


@pytest.mark.parametrize("field", ["last_error_date", "last_synchronization_error_date"])
@pytest.mark.parametrize("huge", [10**30, 10**400, -(10**400)])
def test_a_number_too_big_for_a_float_is_unknown_not_a_crash(field: str, huge: int) -> None:
    verdict = verdict_of(healthy_info(**{field: huge}))
    assert verdict.status == "unknown" and verdict.reasons[0].endswith("_invalid")


def test_a_pending_count_too_big_for_a_float_is_a_problem_not_a_crash() -> None:
    assert verdict_of(healthy_info(pending_update_count=10**400)).reasons == (
        "pending_updates_high",
    )


JUNK = [None, True, False, 0, -1, 1, 7, 10**30, 1.5, "", "x", "10", [], [1], {}, {"a": 1}, b"x"]


@pytest.mark.parametrize("junk", JUNK, ids=repr)
def test_any_garbage_in_any_field_gives_a_verdict_and_never_raises(junk: object) -> None:
    for field in (
        "url",
        "pending_update_count",
        "last_error_date",
        "last_synchronization_error_date",
        "allowed_updates",
    ):
        verdict = verdict_of(healthy_info(**{field: junk}))
        assert verdict.status in ("ok", "problem", "unknown")
        assert verdict.reasons == tuple(dict.fromkeys(verdict.reasons))  # no duplicates


REASON_CODES = [
    webhook_probe.NO_WEBHOOK_URL,
    webhook_probe.PENDING_UPDATES_HIGH,
    webhook_probe.RECENT_DELIVERY_ERROR,
    webhook_probe.RECENT_SYNC_ERROR,
    webhook_probe.WEBHOOK_PATH_UNEXPECTED,
    webhook_probe.UPDATE_TYPES_EXCLUDED,
    webhook_probe.RESPONSE_NOT_AN_OBJECT,
    webhook_probe.URL_INVALID,
    webhook_probe.PENDING_UPDATE_COUNT_INVALID,
    webhook_probe.LAST_ERROR_DATE_INVALID,
    webhook_probe.LAST_SYNC_ERROR_DATE_INVALID,
    webhook_probe.ALLOWED_UPDATES_INVALID,
    webhook_probe.TELEGRAM_TIMEOUT,
    webhook_probe.TELEGRAM_UNREACHABLE,
    webhook_probe.TELEGRAM_REFUSED,
    webhook_probe.TELEGRAM_ANSWER_MALFORMED,
    webhook_probe.PROBE_TIMED_OUT,
    webhook_probe.PROBE_FAILED,
    webhook_probe.NOT_PROBED_YET,
    webhook_probe.PROBE_NOT_RUNNING,
]


def test_reasons_are_short_fixed_lowercase_codes_and_all_different() -> None:
    assert len(set(REASON_CODES)) == len(REASON_CODES)
    assert all(re.fullmatch(r"[a-z]+(_[a-z]+)*", code) and len(code) <= 40 for code in REASON_CODES)


# --- Telegram's own words, made safe to log ----------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        SECRET_WEBHOOK_URL,
        "https://api.example.test/s3cr3t-path/hook",
        "HTTPS://API.EXAMPLE.TEST/S3CR3T-path",
        "api.example.test/telegram/webhook/s3cr3t-path",  # no scheme
        "api.example.test:8443/telegram/webhook/s3cr3t-path",  # no scheme, with a port
        "//api.example.test/hook/s3cr3t-path",  # scheme-relative
        "sub.api.example.test/s3cr3t-path",
    ],
)
def test_the_error_message_has_every_url_removed(address: str) -> None:
    text = describe_last_error(
        {"last_error_message": f"Wrong response from {address}: 404 Not Found"}
    )
    assert text == "Wrong response from <url> 404 Not Found"


@pytest.mark.parametrize(
    "message",
    [
        "Wrong response from the webhook: 404 Not Found",
        "Wrong response from the webhook: 502 Bad Gateway",
        "Connection timed out",
        "Failed to resolve host: Name or service not known",
        "SSL error {error:1408F10B:SSL routines:ssl3_get_record:wrong version number}",
        "Read timeout expired",
    ],
)
def test_ordinary_telegram_wording_passes_through_untouched(message: str) -> None:
    assert describe_last_error({"last_error_message": message}) == message


@pytest.mark.parametrize(
    "address",
    [
        SECRET_WEBHOOK_URL,
        "api.example.test/telegram/webhook/s3cr3t-path",
        "//api.example.test/s3cr3t",
    ],
)
async def test_an_address_in_the_error_message_never_reaches_the_log(
    address: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=PROBE_LOGGER)
    info = healthy_info(
        last_error_date=epoch(timedelta(hours=2)),
        last_error_message=f"Wrong response from {address}: 404 Not Found",
    )
    await make(Telegram(info), Clock(), Check()).run_if_due()

    [record] = [r for r in caplog.records if r.name == PROBE_LOGGER]
    assert record.ctx["last_error"] == "Wrong response from <url> 404 Not Found"  # type: ignore[attr-defined]
    for text in (caplog.text, rendered([record]), repr(record.ctx)):  # type: ignore[attr-defined]
        assert "s3cr3t" not in text and "api.example.test" not in text


def test_the_error_message_is_cut_scrubbed_and_flattened() -> None:
    text = describe_last_error(
        {"last_error_message": f"line one\nline two\t{BOT_TOKEN} " + "x" * 5000}
    )
    assert text is not None
    assert len(text) <= webhook_probe.MAX_ERROR_TEXT_CHARS
    assert "\n" not in text and "\t" not in text
    assert BOT_TOKEN not in text and BOT_TOKEN.split(":")[1] not in text


@pytest.mark.parametrize("message", [None, "", "   ", 5, ["x"], {"a": 1}])
def test_no_usable_error_message_is_none(message: object) -> None:
    assert describe_last_error({"last_error_message": message}) is None
    assert describe_last_error("not a mapping") is None


def test_a_hostile_message_costs_a_bounded_amount_of_work() -> None:
    """A coarse smoke check on the wall clock; the exact bounds are pinned just below."""
    started = time.perf_counter()
    describe_last_error({"last_error_message": "http://" + "a" * 5_000_000})
    describe_last_error({"last_error_message": ("a:" * 1_000_000) + "b"})
    assert time.perf_counter() - started < 1.0


# The two limits below are literals on purpose: a test that reads the module's constant cannot
# notice the constant being changed. Changing either number means changing the test too.


def test_the_logged_error_text_is_exactly_120_characters_for_a_long_message() -> None:
    text = describe_last_error({"last_error_message": "word " * 2000})
    assert text is not None
    assert len(text) == 120


def test_at_most_400_characters_of_a_hostile_message_are_ever_scanned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[int] = []
    real_redact = logging_setup.redact

    def spy(text: str) -> str:
        seen.append(len(text))
        return real_redact(text)

    monkeypatch.setattr("between_jobs.api.webhook_probe.redact", spy)  # the name it imported
    describe_last_error({"last_error_message": "word " * 2_000_000})
    assert seen and max(seen) <= 400


# --- the daily gating, on a fake clock ---------------------------------------------------------


class Clock:
    """Both clocks the probe reads, moved together."""

    def __init__(self) -> None:
        self.mono = 5_000.0
        self.wall = NOW

    def advance(self, seconds: float) -> None:
        self.mono += seconds
        self.wall += timedelta(seconds=seconds)

    def hours(self, hours: float) -> None:
        self.advance(hours * 3600)


class Telegram:
    """A fake `fetch`: answers what the test says, counts the calls."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers) or [healthy_info()]
        self.calls = 0

    async def __call__(self) -> object:
        self.calls += 1
        answer = self.answers[min(self.calls, len(self.answers)) - 1]
        if isinstance(answer, BaseException):
            raise answer
        return answer


class Check:
    """A recording stand-in for the check's pinger."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.error: Exception | None = None

    def succeeded(self) -> None:
        self.calls.append("succeeded")
        if self.error is not None:
            raise self.error

    def failed(self) -> None:
        self.calls.append("failed")
        if self.error is not None:
            raise self.error


def make(
    telegram: Telegram, clock: Clock, check: Check | None = None, **kwargs: Any
) -> WebhookProbe:
    return WebhookProbe(
        telegram,
        heartbeat=check,
        now=lambda: clock.wall,
        monotonic=lambda: clock.mono,
        **kwargs,
    )


async def test_the_first_tick_probes() -> None:
    telegram, clock, check = Telegram(), Clock(), Check()
    probe = make(telegram, clock, check)

    await probe.run_if_due()

    assert telegram.calls == 1
    assert check.calls == ["succeeded"]


async def test_the_next_ticks_of_the_same_day_do_nothing_and_the_next_day_probes() -> None:
    telegram, clock, check = Telegram(), Clock(), Check()
    probe = make(telegram, clock, check)

    await probe.run_if_due()
    for _ in range(23):  # an hourly worker: 23 more ticks inside the first 24 hours
        clock.hours(1)
        await probe.run_if_due()
    assert telegram.calls == 1
    assert check.calls == ["succeeded"]

    clock.hours(1)  # 24 hours after the first probe
    await probe.run_if_due()
    assert telegram.calls == 2
    assert check.calls == ["succeeded", "succeeded"]


async def test_a_tick_a_second_before_the_day_is_up_waits() -> None:
    telegram, clock = Telegram(), Clock()
    probe = make(telegram, clock)
    await probe.run_if_due()
    clock.advance(24 * 3600 - 1)
    await probe.run_if_due()
    assert telegram.calls == 1
    clock.advance(1)
    await probe.run_if_due()
    assert telegram.calls == 2


async def test_a_restart_probes_again_at_once() -> None:
    telegram, clock = Telegram(), Clock()
    await make(telegram, clock).run_if_due()
    clock.hours(2)

    after_restart = make(telegram, clock)  # a new process: nothing remembered
    await after_restart.run_if_due()

    assert telegram.calls == 2


async def test_the_wall_clock_jumping_does_not_move_the_schedule() -> None:
    """The gate reads the monotonic clock: a clock correction (or a deliberately wrong one)
    can neither starve the probe for days nor make it run every tick."""
    telegram, clock = Telegram(), Clock()
    probe = make(telegram, clock)
    await probe.run_if_due()
    clock.wall -= timedelta(days=30)
    clock.mono += 3600
    await probe.run_if_due()
    assert telegram.calls == 1
    clock.wall += timedelta(days=60)
    await probe.run_if_due()
    assert telegram.calls == 1


def test_the_production_gate_reads_the_monotonic_clock() -> None:
    """Every other test hands the probe both clocks, so none would notice the default being a
    wall clock: an NTP step back would silence the probe (and its ping) for days, a step forward
    would probe on every tick. The default is bound when the function is defined, so patching
    `time.time` could not catch it; the default itself is what is pinned."""
    default = inspect.signature(WebhookProbe.__init__).parameters["monotonic"].default
    assert default is time.monotonic


async def test_an_unknown_answer_is_tried_again_at_the_next_tick_up_to_a_cap() -> None:
    telegram = Telegram(WebhookInfoUnavailable("telegram_unreachable"))
    clock, check = Clock(), Check()
    probe = make(telegram, clock, check)

    attempts_at: list[int] = []
    for hour in range(30):
        before = telegram.calls
        await probe.run_if_due()
        if telegram.calls > before:
            attempts_at.append(hour)
        clock.hours(1)

    assert MAX_ATTEMPTS_PER_CYCLE == 3
    # three attempts on the first three hourly ticks, then a whole day of nothing, then again
    assert attempts_at == [0, 1, 2, 26, 27, 28]
    assert check.calls == []  # unknown pings nothing, however often it is tried


async def test_a_retry_that_succeeds_pings_and_waits_a_full_day() -> None:
    telegram = Telegram(WebhookInfoUnavailable("telegram_timeout"), healthy_info())
    clock, check = Clock(), Check()
    probe = make(telegram, clock, check)

    await probe.run_if_due()
    assert check.calls == [] and telegram.calls == 1
    clock.hours(1)
    await probe.run_if_due()
    assert check.calls == ["succeeded"] and telegram.calls == 2
    clock.hours(23)
    await probe.run_if_due()
    assert telegram.calls == 2
    clock.hours(1)
    await probe.run_if_due()
    assert telegram.calls == 3


async def test_a_run_of_unknowns_does_not_carry_over_into_the_next_cycle() -> None:
    telegram = Telegram(
        WebhookInfoUnavailable("telegram_timeout"),
        WebhookInfoUnavailable("telegram_timeout"),
        healthy_info(),
        WebhookInfoUnavailable("telegram_timeout"),  # and from here on, every call
    )
    clock = Clock()
    probe = make(telegram, clock)

    attempts_at: list[int] = []
    for hour in range(40):
        before = telegram.calls
        await probe.run_if_due()
        if telegram.calls > before:
            attempts_at.append(hour)
        clock.hours(1)

    # unknown, unknown, ok at hours 0-2; a day later the count starts again, so three more
    assert attempts_at == [0, 1, 2, 26, 27, 28]


# --- what is pinged and logged, per verdict ----------------------------------------------------


async def test_ok_pings_a_success_and_logs_at_info_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=PROBE_LOGGER)
    check = Check()
    probe = make(Telegram(healthy_info(pending_update_count=3)), Clock(), check)

    await probe.run_if_due()

    assert check.calls == ["succeeded"]
    [record] = [r for r in caplog.records if r.name == PROBE_LOGGER]
    assert record.levelno == logging.INFO
    assert record.ctx == {"status": "ok", "reasons": "", "pending_updates": 3}  # type: ignore[attr-defined]


async def test_a_problem_pings_fail_and_logs_one_warning_with_the_reason_codes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=PROBE_LOGGER)
    info = healthy_info(
        url=SECRET_WEBHOOK_URL,
        pending_update_count=40,
        last_error_date=epoch(timedelta(hours=3)),
        last_error_message=f"Wrong response from {SECRET_WEBHOOK_URL}: 502 Bad Gateway",
    )
    check = Check()
    probe = make(Telegram(info), Clock(), check)

    await probe.run_if_due()

    assert check.calls == ["failed"]
    [record] = [r for r in caplog.records if r.name == PROBE_LOGGER]
    assert record.levelno == logging.WARNING
    assert record.ctx == {  # type: ignore[attr-defined]
        "status": "problem",
        "reasons": "pending_updates_high,recent_delivery_error",
        "pending_updates": 40,
        "last_error": "Wrong response from <url> 502 Bad Gateway",
    }
    # Neither the webhook's address (its path and query can carry a secret) nor any piece of it.
    for text in (caplog.text, rendered([record]), repr(record.ctx)):  # type: ignore[attr-defined]
        assert "s3cr3t" not in text and "api.example.test" not in text


@pytest.mark.parametrize(
    ("info", "reason"),
    [
        (healthy_info(url="https://n8n.example.test/webhook/x/webhook"), "webhook_path_unexpected"),
        (healthy_info(allowed_updates=["message"]), "update_types_excluded"),
        (healthy_info(allowed_updates=["edited_channel_post"]), "update_types_excluded"),
    ],
    ids=["other_route", "message_only", "other_types"],
)
async def test_a_webhook_that_is_re_pointed_or_narrowed_pings_fail_and_warns(
    info: dict[str, Any], reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=PROBE_LOGGER)
    check = Check()
    probe = make(Telegram(info), Clock(), check)

    await probe.run_if_due()

    assert check.calls == ["failed"]
    assert probe.report()["status"] == "problem" and probe.report()["reasons"] == [reason]
    [record] = [r for r in caplog.records if r.name == PROBE_LOGGER]
    assert record.levelno == logging.WARNING
    assert record.ctx["reasons"] == reason  # type: ignore[attr-defined]


async def test_an_old_error_message_is_not_logged_with_another_problem(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=PROBE_LOGGER)
    info = healthy_info(
        pending_update_count=99,
        last_error_date=epoch(timedelta(days=9)),
        last_error_message="Connection timed out",
    )
    await make(Telegram(info), Clock(), Check()).run_if_due()
    [record] = [r for r in caplog.records if r.name == PROBE_LOGGER]
    assert "last_error" not in record.ctx  # type: ignore[attr-defined]


async def test_unknown_pings_nothing_and_logs_one_warning(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=PROBE_LOGGER)
    check = Check()
    probe = make(Telegram({"url": WEBHOOK_URL}), Clock(), check)  # no count

    await probe.run_if_due()

    assert check.calls == []
    [record] = [r for r in caplog.records if r.name == PROBE_LOGGER]
    assert record.levelno == logging.WARNING
    assert record.ctx["status"] == "unknown"  # type: ignore[attr-defined]
    assert record.ctx["reasons"] == "pending_update_count_invalid"  # type: ignore[attr-defined]


async def test_without_a_check_it_still_probes_and_logs_a_problem(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=PROBE_LOGGER)
    telegram = Telegram(healthy_info(url=""))
    probe = make(telegram, Clock(), None)

    await probe.run_if_due()

    assert telegram.calls == 1
    [record] = [r for r in caplog.records if r.name == PROBE_LOGGER]
    assert record.levelno == logging.WARNING and "no_webhook_url" in record.ctx["reasons"]  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "reason",
    ["telegram_timeout", "telegram_unreachable", "telegram_refused", "telegram_answer_malformed"],
)
async def test_each_way_telegram_can_fail_to_answer_is_unknown_with_its_own_code(
    reason: str,
) -> None:
    check = Check()
    probe = make(Telegram(WebhookInfoUnavailable(reason)), Clock(), check)
    await probe.run_if_due()
    assert check.calls == []
    assert probe.report()["status"] == "unknown" and probe.report()["reasons"] == [reason]


async def test_a_made_up_failure_code_is_probe_failed_not_trusted() -> None:
    probe = make(Telegram(WebhookInfoUnavailable("anything the caller invents")), Clock(), Check())
    await probe.run_if_due()
    assert probe.report()["reasons"] == ["probe_failed"]


# --- failure containment: the caller's real work is never touched ------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        "garbage",
        None,
        [],
        42,
        {"url": 5, "pending_update_count": "x"},
        {"ok": True, "result": {}},
        {},
    ],
    ids=repr,
)
async def test_an_answer_that_is_garbage_is_unknown_and_pings_nothing(answer: object) -> None:
    check = Check()
    probe = make(Telegram(answer), Clock(), check)
    await probe.run_if_due()
    assert check.calls == []
    assert probe.report()["status"] == "unknown"


async def test_an_unexpected_exception_is_unknown_logged_by_type_only_and_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=PROBE_LOGGER)
    boom = RuntimeError(f"GET https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo exploded")
    check = Check()
    probe = make(Telegram(boom), Clock(), check)

    await probe.run_if_due()  # does not raise

    assert check.calls == []
    assert probe.report()["reasons"] == ["probe_failed"]
    [record] = [r for r in caplog.records if r.name == PROBE_LOGGER]
    assert record.ctx["error_type"] == "builtins.RuntimeError"  # type: ignore[attr-defined]
    assert record.exc_info is None  # no traceback: it could carry the message
    for text in (caplog.text, rendered([record]), repr(record.__dict__)):
        assert BOT_TOKEN not in text and "exploded" not in text and "getWebhookInfo" not in text


async def test_a_call_that_never_answers_times_out_and_is_unknown() -> None:
    async def hangs() -> object:
        await asyncio.sleep(3600)
        return healthy_info()

    check = Check()
    probe = WebhookProbe(hangs, heartbeat=check, timeout_seconds=0.05)

    started = time.monotonic()
    # The outer limit is only a backstop: were the probe's own limit gone, this would otherwise
    # sit for the whole hour instead of failing.
    await asyncio.wait_for(probe.run_if_due(), timeout=5)

    assert time.monotonic() - started < 2.0
    assert check.calls == []
    assert probe.report()["reasons"] == ["probe_timed_out"]


def test_the_default_limit_is_short_enough_to_ride_a_tick() -> None:
    # The probe runs inside the hourly cache-purge tick, and that worker is judged stale after
    # three of its intervals; a Telegram answer that dribbles must not hold the tick that long.
    assert 0 < webhook_probe.PROBE_TIMEOUT_SECONDS <= 30.0


async def test_a_default_probe_arms_the_default_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    armed: list[float | None] = []
    real = asyncio.timeout

    def spy(delay: float | None) -> Any:
        armed.append(delay)
        return real(delay)

    monkeypatch.setattr(asyncio, "timeout", spy)  # the probe looks it up when it runs

    async def answers() -> object:
        return healthy_info()

    await WebhookProbe(answers).run_if_due()  # no timeout_seconds override

    assert armed == [webhook_probe.PROBE_TIMEOUT_SECONDS]
    assert armed[0] is not None and armed[0] <= 30.0


@pytest.mark.parametrize("step", ["_schedule_next", "_log", "now"])
async def test_bookkeeping_that_breaks_cannot_hurt_the_caller(
    step: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Whatever goes wrong after the answer is in (the clock, the schedule, the log line), the
    probe's own contract holds: `run_if_due` returns, and says so once, by exception type only."""
    caplog.set_level(logging.INFO, logger=PROBE_LOGGER)
    clock = Clock()

    def boom(*_args: object, **_kwargs: object) -> Any:
        raise ValueError("detail that must not be logged")

    if step == "now":
        probe = WebhookProbe(Telegram(), now=boom, monotonic=lambda: clock.mono)
    else:
        probe = make(Telegram(), clock)
        monkeypatch.setattr(probe, step, boom)

    await probe.run_if_due()  # does not raise

    [record] = [r for r in caplog.records if "could not record its result" in r.getMessage()]
    assert record.levelno == logging.WARNING
    assert record.ctx == {"error_type": "builtins.ValueError"}  # type: ignore[attr-defined]
    assert record.exc_info is None
    assert "detail that must not be logged" not in caplog.text
    if step != "_log":
        assert probe._next_attempt_at is None  # still due: the next tick tries again


async def test_a_cancellation_passes_through_and_records_nothing() -> None:
    started = asyncio.Event()

    async def waits() -> object:
        started.set()
        await asyncio.sleep(3600)
        return healthy_info()

    check = Check()
    probe = WebhookProbe(waits, heartbeat=check)
    task = asyncio.create_task(probe.run_if_due())
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert probe.verdict is None and check.calls == []


@pytest.mark.parametrize(
    "verdict_info", [healthy_info(), healthy_info(url="")], ids=["ok", "problem"]
)
async def test_a_check_that_raises_cannot_hurt_the_caller(
    verdict_info: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=PROBE_LOGGER)
    check = Check()
    check.error = RuntimeError("the pinger broke")
    probe = make(Telegram(verdict_info), Clock(), check)

    await probe.run_if_due()

    assert probe.verdict is not None  # the verdict was still recorded
    assert any("could not ping its check" in r.getMessage() for r in caplog.records)


async def test_nothing_the_probe_finds_or_suffers_reaches_the_error_tracker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reporting is switched on (a fake SDK in place), and a problem, an unknown, a garbage
    answer and an unexpected exception are all run: nothing is captured. A webhook problem or a
    Telegram outage is not a bug in this service."""
    sentry = FakeSentry()
    monkeypatch.setattr(error_reporting, "_sdk", sentry)
    answers = [
        healthy_info(url=""),
        healthy_info(pending_update_count=500),
        WebhookInfoUnavailable("telegram_unreachable"),
        "garbage",
        RuntimeError("bug"),
        TimeoutError(),
    ]
    for answer in answers:
        clock = Clock()
        await make(Telegram(answer), clock, Check()).run_if_due()

    assert sentry.captured == []


# --- what /health shows ------------------------------------------------------------------------


async def test_the_report_before_the_first_probe_is_unknown_not_yet_probed() -> None:
    probe = make(Telegram(), Clock())
    assert probe.report() == {
        "status": "unknown",
        "last_checked_at": None,
        "reasons": ["not_probed_yet"],
    }


async def test_the_report_after_each_kind_of_verdict() -> None:
    clock = Clock()
    for answer, status, reasons in [
        (healthy_info(), "ok", []),
        (healthy_info(pending_update_count=11), "problem", ["pending_updates_high"]),
        ({"url": WEBHOOK_URL}, "unknown", ["pending_update_count_invalid"]),
    ]:
        probe = make(Telegram(answer), clock)
        await probe.run_if_due()
        assert probe.report() == {
            "status": status,
            "last_checked_at": "2026-10-08T12:00:00+00:00",
            "reasons": reasons,
        }


async def test_the_report_shows_whole_seconds_like_the_workers_blocks() -> None:
    """The test clock elsewhere has no microseconds, so a format that kept them would pass."""
    clock = Clock()
    clock.wall = NOW.replace(microsecond=481923)
    probe = make(Telegram(healthy_info()), clock)

    await probe.run_if_due()

    assert probe.report()["last_checked_at"] == "2026-10-08T12:00:00+00:00"


async def test_the_report_keeps_the_last_verdict_until_the_next_probe() -> None:
    telegram, clock = Telegram(healthy_info(url=""), healthy_info()), Clock()
    probe = make(telegram, clock)
    await probe.run_if_due()
    clock.hours(5)
    await probe.run_if_due()
    assert probe.report()["status"] == "problem"  # nothing new has been learned
    assert probe.report()["last_checked_at"] == "2026-10-08T12:00:00+00:00"
    clock.hours(19)
    await probe.run_if_due()
    assert probe.report()["status"] == "ok"
    assert probe.report()["last_checked_at"] == "2026-10-09T12:00:00+00:00"


async def test_a_probe_that_will_never_run_says_so_instead_of_not_yet() -> None:
    probe = WebhookProbe(Telegram(), scheduled=False)
    assert probe.report() == {
        "status": "unknown",
        "last_checked_at": None,
        "reasons": ["probe_not_running"],
    }


def test_no_bot_is_not_configured() -> None:
    assert webhook_report(None) == {
        "status": "not_configured",
        "last_checked_at": None,
        "reasons": [],
    }


async def test_the_report_holds_only_fixed_words_and_a_timestamp() -> None:
    probe = make(
        Telegram(healthy_info(url=SECRET_WEBHOOK_URL, last_error_date=epoch(timedelta(hours=1)))),
        Clock(),
    )
    await probe.run_if_due()
    assert "s3cr3t" not in repr(probe.report()) and "api.example.test" not in repr(probe.report())
