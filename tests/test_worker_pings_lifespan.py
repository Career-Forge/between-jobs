"""The healthcheck pings wired into the real app lifespan.

Boots the real FastAPI app with the workers enabled but their loops replaced by minimal
supervised ones (one trivial tick, then idle), a fake database client and a fake Healthchecks
behind httpx's mock transport -- so nothing here reaches a real service. What is pinned:

- nothing configured: the app starts exactly as before, with no pinger and no extra client;
- each worker's check URL comes from the setting named after its DISABLE_* flag, and only an
  enabled worker pings; a worker switched off never does, whatever is set for it;
- a malformed URL stops the boot naming the setting, even for a worker that is switched off,
  and leaves nothing running (the shared client is closed, if one was made);
- a setting that looks like one of these but is named after no worker is ignored and warned
  about at boot, by name only;
- the pingers are closed at shutdown.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from rendered_logs import rendered

from between_jobs.api import app as app_module
from between_jobs.api import worker_pings
from between_jobs.api.app import app
from between_jobs.api.env import ConfigurationError
from between_jobs.api.worker_pings import WorkerPings
from between_jobs.api.worker_supervision import run_supervised

# worker name -> (its DISABLE_* flag, the setting that holds its check's URL)
WORKERS = {
    "outbox": ("DISABLE_OUTBOX_WORKER", "HEALTHCHECKS_URL_OUTBOX_WORKER"),
    "job_registry_poller": ("DISABLE_JOB_REGISTRY_POLLER", "HEALTHCHECKS_URL_JOB_REGISTRY_POLLER"),
    "saved_search_matcher": (
        "DISABLE_SAVED_SEARCH_MATCHER",
        "HEALTHCHECKS_URL_SAVED_SEARCH_MATCHER",
    ),
    "gmail_reply_checker": (
        "DISABLE_GMAIL_REPLY_CHECKER",
        "HEALTHCHECKS_URL_GMAIL_REPLY_CHECKER",
    ),
    "hiring_signal_cache_purge": (
        "DISABLE_HIRING_SIGNAL_CACHE_PURGE",
        "HEALTHCHECKS_URL_HIRING_SIGNAL_CACHE_PURGE",
    ),
}


def _url(worker: str) -> str:
    return f"https://hc-ping.example.test/{worker}-3f9c2a7e"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("RAILWAY_DEPLOYMENT_ID", raising=False)
    monkeypatch.setenv("WORKER_LEASES", "off")
    for _flag, setting in WORKERS.values():
        monkeypatch.delenv(setting, raising=False)
    # Whatever the machine running the tests has in its environment that looks like a
    # healthcheck setting would otherwise show up as a stray one.
    for name in [n for n in os.environ if n.upper().startswith("HEALTHCHECK")]:
        monkeypatch.delenv(name, raising=False)


class FakeHealthchecks:
    def __init__(self) -> None:
        self.urls: list[str] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        return httpx.Response(200, text="OK")


@pytest.fixture
def healthchecks(monkeypatch: pytest.MonkeyPatch) -> FakeHealthchecks:
    service = FakeHealthchecks()
    monkeypatch.setattr(
        WorkerPings,
        "_make_client",
        lambda self: httpx.AsyncClient(transport=httpx.MockTransport(service.handle)),
    )
    return service


def _boot(monkeypatch: pytest.MonkeyPatch, *, enabled: set[str]) -> None:
    """The listed workers enabled, the rest switched off; each enabled worker's loop is a
    real supervised loop whose tick does nothing, so it pings like the real ones do."""

    async def fake_create() -> tuple[Any, str]:
        return SimpleNamespace(), "https://example.supabase.co"

    monkeypatch.setattr(app_module, "create_supabase_client", fake_create)

    def make_loop(*_args: Any, state: Any = None, **_kwargs: Any) -> Any:
        async def tick() -> None:
            await asyncio.sleep(0)

        async def loop() -> None:
            await run_supervised(tick, state=state)

        return loop()

    for attribute in (
        "run_worker_forever",
        "run_poller_forever",
        "run_matcher_forever",
        "run_reply_check_forever",
        "run_hiring_cache_purge_forever",
    ):
        monkeypatch.setattr(app_module, attribute, make_loop)
    for worker, (flag, _setting) in WORKERS.items():
        if worker in enabled:
            monkeypatch.delenv(flag, raising=False)
        else:
            monkeypatch.setenv(flag, "1")


def _wait_for(condition: Any, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)


# --- nothing configured --------------------------------------------------------------------


def test_with_no_urls_set_the_app_starts_as_before_and_nothing_pings(
    monkeypatch: pytest.MonkeyPatch, healthchecks: FakeHealthchecks
) -> None:
    _boot(monkeypatch, enabled=set(WORKERS))
    with TestClient(app) as client:
        _wait_for(lambda: all(w.last_success_at for w in client.app.state.workers.workers.values()))  # type: ignore[attr-defined]
        pings = client.app.state.worker_pings  # type: ignore[attr-defined]
        workers = client.app.state.workers.workers  # type: ignore[attr-defined]
        assert client.get("/health").status_code == 200

    assert pings.pingers == {} and pings._client is None
    assert all(state.heartbeat is None for state in workers.values())
    assert healthchecks.urls == []


# --- the settings, worker by worker ---------------------------------------------------------------


def test_each_enabled_worker_pings_the_url_in_its_own_setting_and_no_other(
    monkeypatch: pytest.MonkeyPatch, healthchecks: FakeHealthchecks
) -> None:
    _boot(monkeypatch, enabled=set(WORKERS))
    for worker, (_flag, setting) in WORKERS.items():
        monkeypatch.setenv(setting, _url(worker))

    with TestClient(app) as client:
        _wait_for(lambda: len(healthchecks.urls) >= len(WORKERS))
        workers = client.app.state.workers.workers  # type: ignore[attr-defined]
        pings = client.app.state.worker_pings  # type: ignore[attr-defined]

    assert sorted(healthchecks.urls) == sorted(_url(worker) for worker in WORKERS)
    for worker in WORKERS:
        assert workers[worker].heartbeat is pings.pingers[worker]


def test_only_the_workers_with_a_url_ping(
    monkeypatch: pytest.MonkeyPatch, healthchecks: FakeHealthchecks
) -> None:
    _boot(monkeypatch, enabled=set(WORKERS))
    monkeypatch.setenv(WORKERS["gmail_reply_checker"][1], _url("gmail_reply_checker"))

    with TestClient(app) as client:
        _wait_for(lambda: healthchecks.urls)
        _wait_for(lambda: all(w.last_success_at for w in client.app.state.workers.workers.values()))  # type: ignore[attr-defined]
        workers = client.app.state.workers.workers  # type: ignore[attr-defined]

    assert healthchecks.urls == [_url("gmail_reply_checker")]
    assert [name for name, state in workers.items() if state.heartbeat is not None] == [
        "gmail_reply_checker"
    ]


def test_a_worker_switched_off_never_pings_even_with_a_url_and_the_boot_warns(
    monkeypatch: pytest.MonkeyPatch,
    healthchecks: FakeHealthchecks,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _boot(monkeypatch, enabled=set(WORKERS) - {"outbox"})
    for worker, (_flag, setting) in WORKERS.items():
        monkeypatch.setenv(setting, _url(worker))

    with (
        caplog.at_level(logging.WARNING, logger="between_jobs.api.worker_pings"),
        TestClient(app) as client,
    ):
        _wait_for(lambda: len(healthchecks.urls) >= len(WORKERS) - 1)
        state = client.app.state.workers.workers["outbox"]  # type: ignore[attr-defined]
        assert state.enabled is False and state.heartbeat is None

    assert _url("outbox") not in healthchecks.urls
    assert sorted(healthchecks.urls) == sorted(_url(w) for w in WORKERS if w != "outbox")
    [warning] = [r for r in caplog.records if r.name == "between_jobs.api.worker_pings"]
    assert warning.ctx == {"worker": "outbox", "variable": "HEALTHCHECKS_URL_OUTBOX_WORKER"}  # type: ignore[attr-defined]
    assert "hc-ping.example.test" not in caplog.text


# --- a malformed URL -----------------------------------------------------------------------------


@pytest.mark.parametrize("worker", list(WORKERS))
@pytest.mark.parametrize("enabled", [True, False])
def test_a_malformed_url_stops_the_boot_naming_the_setting_for_any_worker_enabled_or_not(
    monkeypatch: pytest.MonkeyPatch,
    healthchecks: FakeHealthchecks,
    caplog: pytest.LogCaptureFixture,
    worker: str,
    enabled: bool,
) -> None:
    _boot(monkeypatch, enabled=set(WORKERS) if enabled else set())
    setting = WORKERS[worker][1]
    monkeypatch.setenv(setting, "http://hc-ping.example.test/not-https-secret-part")

    with (
        caplog.at_level(logging.CRITICAL, logger="between_jobs.api.env"),
        pytest.raises(ConfigurationError, match=setting),
        TestClient(app),
    ):
        pass

    [message] = [r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL]
    assert setting in message and "not-https-secret-part" not in message
    assert healthchecks.urls == []


def test_a_failed_boot_closes_the_shared_ping_client(
    monkeypatch: pytest.MonkeyPatch, healthchecks: FakeHealthchecks
) -> None:
    """The first worker's valid URL makes the shared client; a later worker's malformed one
    stops the boot, and the half-built set must not leave that client open."""
    _boot(monkeypatch, enabled=set(WORKERS))
    monkeypatch.setenv(WORKERS["outbox"][1], _url("outbox"))
    monkeypatch.setenv(WORKERS["gmail_reply_checker"][1], "http://bad.example.test/x")
    clients: list[httpx.AsyncClient] = []

    def make(self: WorkerPings) -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=httpx.MockTransport(healthchecks.handle))
        clients.append(client)
        return client

    monkeypatch.setattr(WorkerPings, "_make_client", make)
    with pytest.raises(ConfigurationError), TestClient(app):
        pass

    assert len(clients) == 1 and clients[0].is_closed


# --- a setting named after no worker ----------------------------------------------------------

PINGS_LOGGER = "between_jobs.api.worker_pings"


@pytest.mark.parametrize(
    "name",
    [
        "HEALTHCHECKS_URL_OUTBOX",  # the natural guess: /health calls the worker "outbox"
        "healthchecks_url_outbox_worker",  # the right name in the wrong case
        "HEALTHCHECK_URL_OUTBOX_WORKER",  # a missing "S"
        "HEALTHCHECKS_URL_",
        "HEALTHCHECKS_URL_OUTBOX_WORKERS",
    ],
)
def test_a_setting_named_after_no_worker_is_ignored_and_warned_about_by_name_only(
    monkeypatch: pytest.MonkeyPatch,
    healthchecks: FakeHealthchecks,
    caplog: pytest.LogCaptureFixture,
    name: str,
) -> None:
    _boot(monkeypatch, enabled=set(WORKERS))
    monkeypatch.setenv(name, _url("outbox"))

    with caplog.at_level(logging.WARNING, logger=PINGS_LOGGER), TestClient(app) as client:
        _wait_for(lambda: all(w.last_success_at for w in client.app.state.workers.workers.values()))  # type: ignore[attr-defined]
        pings: WorkerPings = client.app.state.worker_pings  # type: ignore[attr-defined]
        assert pings.pingers == {} and pings._client is None  # the boot went ahead, unmonitored

    [warning] = [r for r in caplog.records if r.name == PINGS_LOGGER]
    assert warning.levelno == logging.WARNING
    assert warning.ctx["variable"] == name  # type: ignore[attr-defined]
    assert warning.ctx["expected"] == sorted(setting for _flag, setting in WORKERS.values())  # type: ignore[attr-defined]
    assert "HEALTHCHECKS_URL_OUTBOX_WORKER" in warning.ctx["expected"]  # type: ignore[attr-defined]
    # The value is the check's credential: neither the record nor what is written has it.
    for text in (caplog.text, rendered([warning]), repr(warning.ctx)):  # type: ignore[attr-defined]
        assert "hc-ping.example.test" not in text and "3f9c2a7e" not in text
    assert healthchecks.urls == []


def test_the_five_right_names_raise_no_such_warning(
    monkeypatch: pytest.MonkeyPatch,
    healthchecks: FakeHealthchecks,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _boot(monkeypatch, enabled=set(WORKERS))
    for worker, (_flag, setting) in WORKERS.items():
        monkeypatch.setenv(setting, _url(worker))

    with caplog.at_level(logging.WARNING, logger=PINGS_LOGGER), TestClient(app):
        _wait_for(lambda: len(healthchecks.urls) >= len(WORKERS))

    assert [r for r in caplog.records if r.name == PINGS_LOGGER] == []


def test_a_setting_for_a_worker_that_is_switched_off_is_a_known_name_with_its_own_warning(
    monkeypatch: pytest.MonkeyPatch,
    healthchecks: FakeHealthchecks,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _boot(monkeypatch, enabled=set(WORKERS) - {"outbox"})
    monkeypatch.setenv(WORKERS["outbox"][1], _url("outbox"))

    with caplog.at_level(logging.WARNING, logger=PINGS_LOGGER), TestClient(app):
        pass

    [warning] = [r for r in caplog.records if r.name == PINGS_LOGGER]
    assert warning.ctx == {"worker": "outbox", "variable": "HEALTHCHECKS_URL_OUTBOX_WORKER"}  # type: ignore[attr-defined]


def test_nothing_set_at_all_raises_no_warning(
    monkeypatch: pytest.MonkeyPatch,
    healthchecks: FakeHealthchecks,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _boot(monkeypatch, enabled=set(WORKERS))
    with caplog.at_level(logging.DEBUG, logger=PINGS_LOGGER), TestClient(app):
        pass
    assert [r for r in caplog.records if r.name == PINGS_LOGGER] == []


# --- shutdown ------------------------------------------------------------------------------------


def test_the_pingers_and_their_client_are_closed_at_shutdown(
    monkeypatch: pytest.MonkeyPatch, healthchecks: FakeHealthchecks
) -> None:
    _boot(monkeypatch, enabled={"outbox"})
    monkeypatch.setenv(WORKERS["outbox"][1], _url("outbox"))

    with TestClient(app) as client:
        pings: WorkerPings = client.app.state.worker_pings  # type: ignore[attr-defined]
        pinger = pings.pingers["outbox"]
        assert pings._client is not None and not pinger._closed

    assert pinger._closed
    assert pings._client is None


def test_the_timeout_and_rate_limit_are_the_documented_ones() -> None:
    assert worker_pings.PING_TIMEOUT_SECONDS == 5.0
    assert worker_pings.MIN_PING_INTERVAL_SECONDS == 30.0
