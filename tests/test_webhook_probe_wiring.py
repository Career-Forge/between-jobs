"""The Telegram webhook probe inside the real app: booted through the lifespan, riding on the
real cache-purge worker's tick, reporting to a fake Healthchecks and to `/health`.

The other workers are switched off (tests/conftest.py), Supabase is a stub, the purge itself is a
counter, and `TelegramClient.get_webhook_info` is scripted; so nothing here reaches a real
service. What is pinned: when the probe exists and runs, what each verdict does to the check and
to `/health` (and that `/health` never turns 503 because of Telegram), that a Telegram failure
never touches the purge worker's own job or its own ping, how the check's setting is validated,
and that the bot token appears nowhere in what the app logs."""

from __future__ import annotations

import logging
import os
import re
import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from rendered_logs import rendered

from between_jobs.api import app as app_module
from between_jobs.api import hiring_signal_cache
from between_jobs.api.app import app
from between_jobs.api.env import ConfigurationError
from between_jobs.api.telegram_client import TelegramApiError, TelegramClient
from between_jobs.api.webhook_probe import (
    PROBE_INTERVAL_SECONDS,
    PROBE_TIMEOUT_SECONDS,
    WEBHOOK_CHECK_ENV,
)
from between_jobs.api.worker_pings import WorkerPings

PURGE_FLAG = "DISABLE_HIRING_SIGNAL_CACHE_PURGE"
PURGE_CHECK_ENV = "HEALTHCHECKS_URL_HIRING_SIGNAL_CACHE_PURGE"
PROBE_URL = "https://hc-ping.example.test/webhook-probe-7d1e4b9a"
PURGE_URL = "https://hc-ping.example.test/purge-3f9c2a7e"
PROBE_LOGGER = "between_jobs.api.webhook_probe"
PINGS_LOGGER = "between_jobs.api.worker_pings"
# Assembled at runtime so no literal in this public repo looks like a real bot token.
BOT_TOKEN = "123456789:" + "FAKEfakeFAKEfake" + "0123456789abcdefXY"
HEALTHY = {
    "url": "https://api.example.test/telegram/webhook",
    "pending_update_count": 0,
    "allowed_updates": ["message", "callback_query"],
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")
    monkeypatch.delenv("RAILWAY_DEPLOYMENT_ID", raising=False)
    monkeypatch.delenv(PURGE_FLAG, raising=False)  # the purge worker is the probe's host
    for name in [n for n in os.environ if n.upper().startswith("HEALTHCHECK")]:
        monkeypatch.delenv(name, raising=False)


class FakeHealthchecks:
    def __init__(self) -> None:
        self.urls: list[str] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        return httpx.Response(200, text="OK")


class Telegram:
    """Scripts `getWebhookInfo`: what it answers (or raises), and how often it was asked."""

    def __init__(self) -> None:
        self.answer: Any = dict(HEALTHY)
        self.calls = 0


class Purge:
    def __init__(self) -> None:
        self.sweeps = 0


@pytest.fixture
def healthchecks(monkeypatch: pytest.MonkeyPatch) -> FakeHealthchecks:
    service = FakeHealthchecks()
    monkeypatch.setattr(
        WorkerPings,
        "_make_client",
        lambda self: httpx.AsyncClient(transport=httpx.MockTransport(service.handle)),
    )
    return service


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> Telegram:
    script = Telegram()

    async def get_webhook_info(self: TelegramClient) -> dict[str, Any]:
        script.calls += 1
        if isinstance(script.answer, BaseException):
            raise script.answer
        return dict(script.answer)

    monkeypatch.setattr(TelegramClient, "get_webhook_info", get_webhook_info)
    return script


@pytest.fixture
def purge(monkeypatch: pytest.MonkeyPatch) -> Purge:
    """The real purge worker, supervised and pinged as in production; its sweep a counter."""
    counter = Purge()

    async def fake_create() -> tuple[Any, str]:
        return SimpleNamespace(), "https://example.supabase.co"

    async def sweep(*_args: Any, **_kwargs: Any) -> int:
        counter.sweeps += 1
        return 0

    monkeypatch.setattr(app_module, "create_supabase_client", fake_create)
    monkeypatch.setattr(hiring_signal_cache, "purge_all_expired", sweep)
    return counter


def wait_for(condition: Any, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return bool(condition())


def settle(client: TestClient, purge: Purge) -> None:
    """The purge worker has finished its first tick (the probe, if any, ran inside it)."""
    state = client.app.state.workers.workers["hiring_signal_cache_purge"]  # type: ignore[attr-defined]
    assert wait_for(lambda: state.last_success_at is not None)
    assert purge.sweeps >= 1


def probe_block(client: TestClient) -> dict[str, Any]:
    response = client.get("/health")
    assert response.status_code == 200
    block: dict[str, Any] = response.json()["telegram_webhook"]
    return block


# --- the verdicts, through the real worker ----------------------------------------------------


def test_a_healthy_webhook_pings_the_check_and_health_says_ok(
    monkeypatch: pytest.MonkeyPatch,
    healthchecks: FakeHealthchecks,
    telegram: Telegram,
    purge: Purge,
) -> None:
    monkeypatch.setenv(WEBHOOK_CHECK_ENV, PROBE_URL)

    with TestClient(app) as client:
        settle(client, purge)
        assert wait_for(lambda: PROBE_URL in healthchecks.urls)
        block = probe_block(client)

    assert telegram.calls == 1
    assert healthchecks.urls == [PROBE_URL]
    assert block["status"] == "ok" and block["reasons"] == []
    # Whole seconds and UTC, as the workers' blocks show their times; the real clock has
    # microseconds, so a format that kept them would fail here.
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", str(block["last_checked_at"])
    )


def test_a_problem_pings_fail_and_health_reports_it_without_turning_503(
    monkeypatch: pytest.MonkeyPatch,
    healthchecks: FakeHealthchecks,
    telegram: Telegram,
    purge: Purge,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(WEBHOOK_CHECK_ENV, PROBE_URL)
    telegram.answer = {**HEALTHY, "url": "", "pending_update_count": 30}

    with (
        caplog.at_level(logging.INFO, logger=PROBE_LOGGER),
        TestClient(app) as client,
    ):
        settle(client, purge)
        assert wait_for(lambda: healthchecks.urls)
        response = client.get("/health")
        head = client.head("/health")

    assert healthchecks.urls == [PROBE_URL + "/fail"]
    assert response.status_code == 200 and head.status_code == 200
    body = response.json()
    assert body["status"] == "ok"  # the service is healthy; its webhook is not
    assert body["telegram_webhook"]["status"] == "problem"
    assert body["telegram_webhook"]["reasons"] == ["no_webhook_url", "pending_updates_high"]
    [warning] = [r for r in caplog.records if r.name == PROBE_LOGGER]
    assert warning.levelno == logging.WARNING
    assert warning.ctx["reasons"] == "no_webhook_url,pending_updates_high"  # type: ignore[attr-defined]


def test_no_answer_from_telegram_pings_nothing_and_health_says_unknown(
    monkeypatch: pytest.MonkeyPatch,
    healthchecks: FakeHealthchecks,
    telegram: Telegram,
    purge: Purge,
) -> None:
    monkeypatch.setenv(WEBHOOK_CHECK_ENV, PROBE_URL)
    telegram.answer = TelegramApiError("refused", status_code=401)  # a wrong bot token

    with TestClient(app) as client:
        settle(client, purge)
        assert wait_for(lambda: probe_block(client)["reasons"] != ["not_probed_yet"])
        block = probe_block(client)

    assert block["status"] == "unknown" and block["reasons"] == ["telegram_refused"]
    assert telegram.calls == 1
    assert healthchecks.urls == []


@pytest.mark.parametrize(
    "failure",
    [
        TelegramApiError("transport"),
        TelegramApiError("timeout"),
        TelegramApiError("malformed", status_code=200),
        RuntimeError("something in the client broke"),
        TimeoutError(),
    ],
    ids=repr,
)
def test_a_failing_telegram_never_touches_the_purge_workers_job_or_its_own_ping(
    monkeypatch: pytest.MonkeyPatch,
    healthchecks: FakeHealthchecks,
    telegram: Telegram,
    purge: Purge,
    failure: BaseException,
) -> None:
    monkeypatch.setenv(WEBHOOK_CHECK_ENV, PROBE_URL)
    monkeypatch.setenv(PURGE_CHECK_ENV, PURGE_URL)
    telegram.answer = failure

    with TestClient(app) as client:
        settle(client, purge)
        assert wait_for(lambda: PURGE_URL in healthchecks.urls)
        state = client.app.state.workers.workers["hiring_signal_cache_purge"]  # type: ignore[attr-defined]
        assert state.consecutive_failures == 0 and state.last_error_type is None
        response = client.get("/health")

    assert purge.sweeps >= 1
    assert healthchecks.urls == [PURGE_URL]  # the worker pinged; the probe did not
    assert response.status_code == 200
    assert response.json()["workers"]["hiring_signal_cache_purge"]["status"] == "running"


def test_a_restart_probes_again(
    healthchecks: FakeHealthchecks, telegram: Telegram, purge: Purge
) -> None:
    for _ in range(2):
        with TestClient(app) as client:
            settle(client, purge)
            assert wait_for(lambda: probe_block(client)["status"] == "ok")
    assert telegram.calls == 2


def test_one_probe_per_boot_however_many_requests_come_in(
    healthchecks: FakeHealthchecks, telegram: Telegram, purge: Purge
) -> None:
    with TestClient(app) as client:
        settle(client, purge)
        for _ in range(5):
            client.get("/health")
    assert telegram.calls == 1


def test_the_booted_probe_runs_on_the_production_limits(
    healthchecks: FakeHealthchecks, telegram: Telegram, purge: Purge
) -> None:
    """The wiring hands the probe no overrides: its time limit, its schedule and its clock are
    the module's own. A wiring edit that loosened any of them would pass every other test."""
    with TestClient(app) as client:
        settle(client, purge)
        probe = client.app.state.webhook_probe  # type: ignore[attr-defined]
        assert probe._timeout == PROBE_TIMEOUT_SECONDS
        assert probe._interval == PROBE_INTERVAL_SECONDS
        assert probe._monotonic is time.monotonic


def test_an_unmapped_telegram_failure_reads_unknown_probe_failed_in_health(
    monkeypatch: pytest.MonkeyPatch,
    healthchecks: FakeHealthchecks,
    telegram: Telegram,
    purge: Purge,
) -> None:
    monkeypatch.setenv(WEBHOOK_CHECK_ENV, PROBE_URL)
    telegram.answer = TelegramApiError("a_code_nobody_mapped")

    with TestClient(app) as client:
        settle(client, purge)
        assert wait_for(lambda: probe_block(client)["reasons"] != ["not_probed_yet"])
        block = probe_block(client)

    assert block["status"] == "unknown" and block["reasons"] == ["probe_failed"]
    assert healthchecks.urls == []


# --- when there is no probe, and /health says why -------------------------------------------------


def test_a_server_without_a_bot_has_no_probe_and_says_not_configured(
    monkeypatch: pytest.MonkeyPatch,
    healthchecks: FakeHealthchecks,
    telegram: Telegram,
    purge: Purge,
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET")

    with TestClient(app) as client:
        settle(client, purge)
        assert client.app.state.webhook_probe is None  # type: ignore[attr-defined]
        block = probe_block(client)

    assert block == {"status": "not_configured", "last_checked_at": None, "reasons": []}
    assert telegram.calls == 0 and healthchecks.urls == []


@pytest.mark.parametrize("purge_on", [True, False])
def test_a_web_only_server_logs_no_warning_from_the_probe_wiring_or_the_purge_tick(
    monkeypatch: pytest.MonkeyPatch,
    healthchecks: FakeHealthchecks,
    telegram: Telegram,
    purge: Purge,
    caplog: pytest.LogCaptureFixture,
    purge_on: bool,
) -> None:
    """A server with no bot has nothing to probe and nothing to say. Not filtered by logger on
    purpose: no logger may warn, whether the purge worker (and so its tick, with nothing riding
    on it) runs or not."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET")
    if not purge_on:
        monkeypatch.setenv(PURGE_FLAG, "1")

    with caplog.at_level(logging.WARNING), TestClient(app) as client:
        if purge_on:
            settle(client, purge)  # a real purge tick has run, with nothing riding on it
        else:
            time.sleep(0.2)

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_a_check_url_on_a_server_without_a_bot_is_ignored_and_warned_about_by_name_only(
    monkeypatch: pytest.MonkeyPatch,
    healthchecks: FakeHealthchecks,
    telegram: Telegram,
    purge: Purge,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET")
    monkeypatch.setenv(WEBHOOK_CHECK_ENV, PROBE_URL)

    with caplog.at_level(logging.WARNING, logger=PINGS_LOGGER), TestClient(app) as client:
        settle(client, purge)

    [warning] = [r for r in caplog.records if r.name == PINGS_LOGGER]
    assert warning.ctx == {"worker": "telegram_webhook", "variable": WEBHOOK_CHECK_ENV}  # type: ignore[attr-defined]
    assert "no Telegram bot" in warning.getMessage()
    assert "hc-ping.example.test" not in caplog.text and "7d1e4b9a" not in caplog.text
    assert healthchecks.urls == []


def test_with_the_purge_worker_off_the_probe_never_runs_and_health_says_so(
    monkeypatch: pytest.MonkeyPatch,
    healthchecks: FakeHealthchecks,
    telegram: Telegram,
    purge: Purge,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(PURGE_FLAG, "1")

    with (
        caplog.at_level(logging.WARNING, logger="between_jobs.api.app_state"),
        TestClient(app) as client,
    ):
        time.sleep(0.2)
        assert client.app.state.webhook_probe is not None  # type: ignore[attr-defined]
        block = probe_block(client)

    assert block == {"status": "unknown", "last_checked_at": None, "reasons": ["probe_not_running"]}
    assert telegram.calls == 0 and purge.sweeps == 0
    assert any("will not run" in r.getMessage() for r in caplog.records)


def test_switching_hiring_signals_off_leaves_the_probe_running(
    monkeypatch: pytest.MonkeyPatch,
    healthchecks: FakeHealthchecks,
    telegram: Telegram,
    purge: Purge,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The feature switch is not the purge worker's switch: the worker, and the probe riding on
    it, keep running with Hiring signals off. `/health` must not say otherwise."""
    monkeypatch.setenv("DISABLE_HIRING_SIGNALS", "1")
    monkeypatch.setenv(WEBHOOK_CHECK_ENV, PROBE_URL)

    with (
        caplog.at_level(logging.WARNING, logger="between_jobs.api.app_state"),
        TestClient(app) as client,
    ):
        settle(client, purge)
        assert wait_for(lambda: PROBE_URL in healthchecks.urls)
        block = probe_block(client)

    assert telegram.calls == 1
    assert block["status"] == "ok" and block["reasons"] == []
    assert not any("will not run" in r.getMessage() for r in caplog.records)


def test_a_check_url_with_the_purge_worker_off_is_ignored_and_warned_about_by_name_only(
    monkeypatch: pytest.MonkeyPatch,
    healthchecks: FakeHealthchecks,
    telegram: Telegram,
    purge: Purge,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(PURGE_FLAG, "1")
    monkeypatch.setenv(WEBHOOK_CHECK_ENV, PROBE_URL)

    with caplog.at_level(logging.WARNING, logger=PINGS_LOGGER), TestClient(app):
        time.sleep(0.2)

    [warning] = [r for r in caplog.records if r.name == PINGS_LOGGER]
    assert warning.ctx == {"worker": "telegram_webhook", "variable": WEBHOOK_CHECK_ENV}  # type: ignore[attr-defined]
    assert PURGE_FLAG in warning.getMessage()
    assert "hc-ping.example.test" not in caplog.text
    assert healthchecks.urls == [] and telegram.calls == 0


def test_with_no_check_url_the_probe_still_runs_and_logs_a_problem(
    healthchecks: FakeHealthchecks,
    telegram: Telegram,
    purge: Purge,
    caplog: pytest.LogCaptureFixture,
) -> None:
    telegram.answer = {**HEALTHY, "pending_update_count": 99}

    with (
        caplog.at_level(logging.INFO, logger=PROBE_LOGGER),
        TestClient(app) as client,
    ):
        settle(client, purge)
        assert wait_for(lambda: probe_block(client)["status"] == "problem")

    assert telegram.calls == 1 and healthchecks.urls == []
    assert any(r.levelno == logging.WARNING for r in caplog.records)


# --- the setting ---------------------------------------------------------------------------------


@pytest.mark.parametrize("bot", [True, False])
@pytest.mark.parametrize("purge_on", [True, False])
@pytest.mark.parametrize(
    "bad",
    [
        "http://hc-ping.example.test/not-https-secret-part",
        "https://hc-ping.example.test/",
        "https://hc-ping.example.test/x?secret=1",
        "https://user:pass@hc-ping.example.test/x",
        "not a url",
    ],
)
def test_a_malformed_check_url_stops_the_boot_naming_the_setting_never_the_value(
    monkeypatch: pytest.MonkeyPatch,
    healthchecks: FakeHealthchecks,
    telegram: Telegram,
    purge: Purge,
    caplog: pytest.LogCaptureFixture,
    bot: bool,
    purge_on: bool,
    bad: str,
) -> None:
    if not bot:
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
        monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET")
    if not purge_on:
        monkeypatch.setenv(PURGE_FLAG, "1")
    monkeypatch.setenv(WEBHOOK_CHECK_ENV, bad)

    with (
        caplog.at_level(logging.CRITICAL, logger="between_jobs.api.env"),
        pytest.raises(ConfigurationError, match=WEBHOOK_CHECK_ENV),
        TestClient(app),
    ):
        pass

    [message] = [r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL]
    assert WEBHOOK_CHECK_ENV in message
    assert "secret-part" not in message and "user:pass" not in message
    assert telegram.calls == 0 and healthchecks.urls == []


@pytest.mark.parametrize("bot", [True, False])
def test_the_checks_own_name_is_never_reported_as_a_stray_setting(
    monkeypatch: pytest.MonkeyPatch,
    healthchecks: FakeHealthchecks,
    telegram: Telegram,
    purge: Purge,
    caplog: pytest.LogCaptureFixture,
    bot: bool,
) -> None:
    if not bot:
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
        monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET")
    monkeypatch.setenv(WEBHOOK_CHECK_ENV, PROBE_URL)

    with caplog.at_level(logging.WARNING, logger=PINGS_LOGGER), TestClient(app) as client:
        settle(client, purge)

    messages = [r.getMessage() for r in caplog.records if r.name == PINGS_LOGGER]
    assert not any("looks like a worker's healthcheck URL" in m for m in messages)


def test_a_near_miss_of_the_checks_name_is_warned_about_and_the_right_name_is_listed(
    monkeypatch: pytest.MonkeyPatch,
    healthchecks: FakeHealthchecks,
    telegram: Telegram,
    purge: Purge,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("HEALTHCHECKS_URL_TELEGRAM", PROBE_URL)

    with caplog.at_level(logging.WARNING, logger=PINGS_LOGGER), TestClient(app) as client:
        settle(client, purge)

    [warning] = [r for r in caplog.records if r.name == PINGS_LOGGER]
    assert warning.ctx["variable"] == "HEALTHCHECKS_URL_TELEGRAM"  # type: ignore[attr-defined]
    assert WEBHOOK_CHECK_ENV in warning.ctx["expected"]  # type: ignore[attr-defined]
    assert healthchecks.urls == []


def test_the_probes_pinger_is_closed_at_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    healthchecks: FakeHealthchecks,
    telegram: Telegram,
    purge: Purge,
) -> None:
    monkeypatch.setenv(WEBHOOK_CHECK_ENV, PROBE_URL)

    with TestClient(app) as client:
        settle(client, purge)
        pings: WorkerPings = client.app.state.worker_pings  # type: ignore[attr-defined]
        pinger = pings.pingers["telegram_webhook"]
        assert not pinger._closed

    assert pinger._closed and pings._client is None


def test_a_failed_boot_closes_the_client_the_probes_pinger_shares(
    monkeypatch: pytest.MonkeyPatch,
    healthchecks: FakeHealthchecks,
    telegram: Telegram,
    purge: Purge,
) -> None:
    """The probe's valid URL makes the shared ping client; a worker's malformed one then stops
    the boot, and the half-built set must not leave that client open."""
    clients: list[httpx.AsyncClient] = []

    def make(self: WorkerPings) -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=httpx.MockTransport(healthchecks.handle))
        clients.append(client)
        return client

    monkeypatch.setattr(WorkerPings, "_make_client", make)
    monkeypatch.setenv(WEBHOOK_CHECK_ENV, PROBE_URL)
    monkeypatch.setenv(PURGE_CHECK_ENV, "http://not-https.example.test/x")

    with pytest.raises(ConfigurationError, match=PURGE_CHECK_ENV), TestClient(app):
        pass

    assert len(clients) == 1 and clients[0].is_closed
    assert telegram.calls == 0


# --- /health keeps its shape ----------------------------------------------------------------------


def test_health_keeps_every_key_it_had_and_adds_one(
    healthchecks: FakeHealthchecks, telegram: Telegram, purge: Purge
) -> None:
    with TestClient(app) as client:
        settle(client, purge)
        body = client.get("/health").json()

    assert list(body) == ["status", "workers", "dependencies", "telegram_webhook"]
    assert set(body["workers"]) == {
        "outbox",
        "job_registry_poller",
        "saved_search_matcher",
        "gmail_reply_checker",
        "hiring_signal_cache_purge",
    }
    assert set(body["telegram_webhook"]) == {"status", "last_checked_at", "reasons"}
    assert set(body["dependencies"].values()) == {"not_checked"}


@pytest.mark.parametrize(
    "answer",
    [
        {**HEALTHY, "url": ""},
        {"url": 5},
        TelegramApiError("transport"),
        RuntimeError("boom"),
    ],
    ids=repr,
)
def test_no_telegram_trouble_ever_changes_the_health_status_code(
    healthchecks: FakeHealthchecks, telegram: Telegram, purge: Purge, answer: Any
) -> None:
    telegram.answer = answer
    with TestClient(app) as client:
        settle(client, purge)
        assert wait_for(lambda: probe_block(client)["reasons"] != ["not_probed_yet"])
        response = client.get("/health")
    assert response.status_code == 200 and response.json()["status"] == "ok"
    assert response.json()["telegram_webhook"]["status"] in ("problem", "unknown")


# --- the token ------------------------------------------------------------------------------------


def test_the_bot_token_is_in_no_log_line_of_a_boot_that_probes_and_fails(
    monkeypatch: pytest.MonkeyPatch,
    healthchecks: FakeHealthchecks,
    telegram: Telegram,
    purge: Purge,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(WEBHOOK_CHECK_ENV, PROBE_URL)
    telegram.answer = RuntimeError(f"GET https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo")
    caplog.set_level(logging.DEBUG)

    with TestClient(app) as client:
        settle(client, purge)
        assert wait_for(lambda: probe_block(client)["reasons"] == ["probe_failed"])

    assert BOT_TOKEN not in caplog.text and BOT_TOKEN not in rendered(caplog.records)
    assert BOT_TOKEN.split(":")[1] not in caplog.text
