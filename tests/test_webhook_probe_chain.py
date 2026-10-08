"""The whole chain with nothing replaced but the network: the real `TelegramClient` on a fake
transport that answers like the Bot API, the adapter's fetcher, and the real probe.

The other files fake one link at a time (the client's answers, the probe's `fetch`); this one
shows the links fit -- a real-shaped `getWebhookInfo` answer comes out as the right verdict and
the right ping, and every way the call can fail comes out as `unknown` with its own code."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from rendered_logs import rendered

from between_jobs.api.telegram_adapter import webhook_info_fetcher
from between_jobs.api.telegram_client import TelegramApiError, TelegramClient
from between_jobs.api.webhook_probe import PROBE_FAILED, WebhookInfoUnavailable, WebhookProbe

# Assembled at runtime so no literal in this public repo looks like a real bot token.
BOT_TOKEN = "123456789:" + "FAKEfakeFAKEfake" + "0123456789abcdefXY"
NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)


class Check:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def succeeded(self) -> None:
        self.calls.append("succeeded")

    def failed(self) -> None:
        self.calls.append("failed")


def probe_on(
    handler: Callable[[httpx.Request], httpx.Response], check: Check | None = None
) -> WebhookProbe:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WebhookProbe(
        webhook_info_fetcher(TelegramClient(http, BOT_TOKEN)),
        heartbeat=check,
        now=lambda: NOW,
    )


def answer(result: dict[str, object]) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _request: httpx.Response(200, json={"ok": True, "result": result})


# What `getWebhookInfo` answers for a working webhook, field for field as the Bot API documents
# `WebhookInfo` (url, has_custom_certificate, pending_update_count, ip_address, max_connections,
# allowed_updates) -- and the same webhook after a failed delivery.
WORKING = {
    "url": "https://api.example.test/telegram/webhook",
    "has_custom_certificate": False,
    "pending_update_count": 0,
    "ip_address": "203.0.113.10",
    "max_connections": 40,
    "allowed_updates": ["message", "callback_query"],
}


async def test_a_working_webhook_is_ok_and_pings_a_success() -> None:
    check = Check()
    probe = probe_on(answer(WORKING), check)
    await probe.run_if_due()
    assert probe.report()["status"] == "ok" and check.calls == ["succeeded"]


async def test_a_recent_delivery_error_is_a_problem_and_pings_fail() -> None:
    failing = {
        **WORKING,
        "pending_update_count": 4,
        "last_error_date": int((NOW - timedelta(hours=2)).timestamp()),
        "last_error_message": "Wrong response from the webhook: 502 Bad Gateway",
    }
    check = Check()
    probe = probe_on(answer(failing), check)
    await probe.run_if_due()
    assert probe.report()["reasons"] == ["recent_delivery_error"] and check.calls == ["failed"]


async def test_a_bot_with_no_webhook_is_a_problem() -> None:
    """Telegram answers an empty `url` for a bot that has none (it uses `getUpdates`)."""
    check = Check()
    probe = probe_on(answer({**WORKING, "url": ""}), check)
    await probe.run_if_due()
    assert probe.report()["reasons"] == ["no_webhook_url"] and check.calls == ["failed"]


async def test_an_error_from_last_week_that_telegram_still_reports_is_ok() -> None:
    old = {**WORKING, "last_error_date": int((NOW - timedelta(days=7)).timestamp())}
    check = Check()
    probe = probe_on(answer(old), check)
    await probe.run_if_due()
    assert probe.report()["status"] == "ok" and check.calls == ["succeeded"]


@pytest.mark.parametrize(
    ("handler", "reason"),
    [
        (
            lambda r: httpx.Response(
                401, json={"ok": False, "error_code": 401, "description": "Unauthorized"}
            ),
            "telegram_refused",
        ),
        (
            lambda r: httpx.Response(
                429, json={"ok": False, "error_code": 429, "parameters": {"retry_after": 5}}
            ),
            "telegram_refused",
        ),
        (lambda r: httpx.Response(502, text="<html>Bad Gateway</html>"), "telegram_refused"),
        (lambda r: httpx.Response(200, text="not json"), "telegram_answer_malformed"),
        (lambda r: httpx.Response(200, json={"ok": True}), "telegram_answer_malformed"),
        (
            lambda r: httpx.Response(200, json={"ok": True, "result": []}),
            "telegram_answer_malformed",
        ),
    ],
)
async def test_an_answer_that_is_not_a_webhook_info_is_unknown_with_its_own_code(
    handler: Callable[[httpx.Request], httpx.Response], reason: str
) -> None:
    check = Check()
    probe = probe_on(handler, check)
    await probe.run_if_due()
    assert probe.report()["status"] == "unknown" and probe.report()["reasons"] == [reason]
    assert check.calls == []


class _ClientWithAnUnmappedFailure(TelegramClient):
    """A client that fails with a code the adapter has no word for, as a code added to
    `TelegramApiError` later would, before anyone maps it."""

    async def get_webhook_info(self) -> dict[str, Any]:
        raise TelegramApiError("a_code_nobody_mapped")


def _unmapped_client() -> TelegramClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(answer(WORKING)))
    return _ClientWithAnUnmappedFailure(http, BOT_TOKEN)


async def test_an_unmapped_client_failure_is_labeled_probe_failed_by_the_fetcher() -> None:
    fetch = webhook_info_fetcher(_unmapped_client())
    with pytest.raises(WebhookInfoUnavailable) as raised:
        await fetch()
    assert raised.value.reason == PROBE_FAILED
    assert raised.value.__context__ is None  # nothing of the original travels with it


async def test_an_unmapped_client_failure_reads_unknown_probe_failed_and_pings_nothing() -> None:
    check = Check()
    probe = WebhookProbe(webhook_info_fetcher(_unmapped_client()), heartbeat=check, now=lambda: NOW)
    await probe.run_if_due()
    assert probe.report()["status"] == "unknown" and probe.report()["reasons"] == ["probe_failed"]
    assert check.calls == []


def _raise(
    error: Callable[[httpx.Request], Exception],
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error(request)

    return handler


@pytest.mark.parametrize(
    ("make_error", "reason"),
    [
        (lambda r: httpx.ConnectError(f"cannot reach {r.url}", request=r), "telegram_unreachable"),
        (lambda r: httpx.ConnectTimeout(f"timed out {r.url}", request=r), "telegram_timeout"),
        (lambda r: httpx.ReadTimeout(f"timed out {r.url}", request=r), "telegram_timeout"),
    ],
)
async def test_telegram_being_unreachable_is_unknown_and_the_token_is_nowhere(
    make_error: Callable[[httpx.Request], Exception],
    reason: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    check = Check()
    probe = probe_on(_raise(make_error), check)

    await probe.run_if_due()

    assert probe.report()["reasons"] == [reason] and check.calls == []
    assert BOT_TOKEN not in caplog.text and BOT_TOKEN not in rendered(caplog.records)
    assert BOT_TOKEN.split(":")[1] not in caplog.text
    assert BOT_TOKEN not in repr(probe.report())
