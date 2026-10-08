"""Healthchecks pings for the workers (worker_pings.py), on a fake httpx transport and a
fake clock: what is sent, the rate limit and the in-flight cap, the 5-second timeout, that
no transport error ever reaches the caller, that the URL (a credential) is never logged,
and how the setting is validated at boot."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Iterable, Iterator, Mapping

import httpx
import pytest
from rendered_logs import rendered

from between_jobs.api import worker_pings
from between_jobs.api.env import ConfigurationError
from between_jobs.api.worker_pings import (
    WorkerPinger,
    WorkerPings,
    healthcheck_env_name,
    load_ping_url,
    parse_ping_url,
)

# Made up: a ping URL is a credential, and this one must never show up in a log line.
URL = "https://hc-ping.example.test/3f9c2a7e-1b4d-4e8a-9c6f-0d5e7a1b2c3d"
URL_FRAGMENTS = ("hc-ping", "3f9c2a7e", "secret-part", "0d5e7a1b2c3d")
ENV_LOGGER = "between_jobs.api.env"
PINGS_LOGGER = "between_jobs.api.worker_pings"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Service:
    """A fake Healthchecks: records what it is asked, answers however the test says."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.error: Exception | None = None
        self.block: asyncio.Event | None = None

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.block is not None:
            await self.block.wait()
        return httpx.Response(self.status, text="OK")

    @property
    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


def _client(service: Service) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(service.handle))


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


def _pinger(service: Service, clock: Clock, **kwargs: float) -> WorkerPinger:
    return WorkerPinger("outbox", URL, _client(service), clock=clock, **kwargs)


# --- what is sent ----------------------------------------------------------------------------


async def test_a_success_is_a_get_of_the_ping_url_and_a_failure_the_same_url_plus_fail() -> None:
    service, clock = Service(), Clock()
    pinger = _pinger(service, clock)

    pinger.succeeded()
    await _settle()
    clock.advance(31)
    pinger.failed()
    await _settle()

    assert service.urls == [URL, URL + "/fail"]
    assert [r.method for r in service.requests] == ["GET", "GET"]
    assert all(r.content == b"" for r in service.requests)
    # An empty GET: httpx's own default headers and nothing the pinger adds (no worker name,
    # no state). A subset, so an httpx upgrade that drops a default header breaks nothing.
    default_headers = {"host", "accept", "accept-encoding", "connection", "user-agent"}
    assert all({name.lower() for name in r.headers} <= default_headers for r in service.requests)


async def test_a_trailing_slash_does_not_double_up_before_fail() -> None:
    service, clock = Service(), Clock()
    pinger = WorkerPinger("outbox", URL + "/", _client(service), clock=clock)
    pinger.failed()
    await _settle()
    assert service.urls == [URL + "/fail"]


async def test_the_call_returns_at_once_whatever_the_service_does() -> None:
    service, clock = Service(), Clock()
    service.block = asyncio.Event()  # the service never answers
    pinger = _pinger(service, clock)

    assert not inspect.iscoroutinefunction(pinger.succeeded)  # a plain call, nothing to await
    pinger.succeeded()
    await _settle()
    assert len(service.requests) == 1
    await pinger.aclose()


# --- rate limit and in-flight cap ---------------------------------------------------------------


async def test_at_most_one_ping_in_30_seconds() -> None:
    service, clock = Service(), Clock()
    pinger = _pinger(service, clock)

    pinger.succeeded()
    await _settle()
    for _ in range(10):  # the outbox ticks every 5 seconds
        clock.advance(2.9)
        pinger.succeeded()
        await _settle()
    assert len(service.requests) == 1  # 29 seconds in

    clock.advance(1.0)
    pinger.succeeded()
    await _settle()
    assert len(service.requests) == 2


async def test_a_failure_shares_the_success_window() -> None:
    service, clock = Service(), Clock()
    pinger = _pinger(service, clock)
    pinger.succeeded()
    await _settle()
    clock.advance(5)
    pinger.failed()
    await _settle()
    assert service.urls == [URL]


async def test_a_suppressed_ping_does_not_restart_the_window() -> None:
    service, clock = Service(), Clock()
    pinger = _pinger(service, clock)
    pinger.succeeded()
    await _settle()
    clock.advance(20)
    pinger.succeeded()  # suppressed
    clock.advance(10)
    pinger.succeeded()  # 30 s after the first: goes
    await _settle()
    assert len(service.requests) == 2


async def test_only_one_ping_is_in_flight_and_the_rest_are_dropped_not_queued() -> None:
    service, clock = Service(), Clock()
    service.block = asyncio.Event()
    pinger = _pinger(service, clock)

    pinger.succeeded()
    await _settle()
    clock.advance(500)  # well past the rate limit, but the first is still waiting
    pinger.succeeded()
    pinger.failed()
    await _settle()
    assert len(service.requests) == 1

    service.block.set()
    await _settle()
    pinger.succeeded()
    await _settle()
    assert len(service.requests) == 2  # and nothing was queued behind the first


async def test_no_ping_when_not_on_an_event_loop_and_no_error() -> None:
    service, clock = Service(), Clock()
    pinger = _pinger(service, clock)
    await asyncio.to_thread(pinger.succeeded)  # a thread with no running loop
    await _settle()
    assert service.requests == []


# --- timeout, errors, logging --------------------------------------------------------------------


async def test_a_ping_that_takes_longer_than_the_timeout_is_given_up_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, clock = Service(), Clock()
    service.block = asyncio.Event()
    pinger = _pinger(service, clock, timeout_seconds=0.05)

    with caplog.at_level(logging.WARNING, logger=PINGS_LOGGER):
        pinger.succeeded()
        await asyncio.sleep(0.3)  # real time: the timeout is a real asyncio timeout

    [warning] = caplog.records
    assert warning.ctx == {"worker": "outbox", "problem": "builtins.TimeoutError"}  # type: ignore[attr-defined]
    assert not any(fragment in rendered([warning]) for fragment in URL_FRAGMENTS)
    clock.advance(31)
    service.block.set()
    pinger.succeeded()  # the slot is free again
    await _settle()
    assert len(service.requests) == 2


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("boom: https://hc-ping.example.test/secret-part"),
        httpx.ReadTimeout("slow"),
        httpx.RemoteProtocolError("bad"),
        RuntimeError("anything at all"),
    ],
)
async def test_a_transport_error_never_reaches_the_caller_and_the_url_is_never_logged(
    caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    service, clock = Service(), Clock()
    service.error = error
    pinger = _pinger(service, clock)

    with caplog.at_level(logging.DEBUG):
        pinger.succeeded()
        await _settle()

    assert len(service.requests) == 1
    [record] = [r for r in caplog.records if r.name == PINGS_LOGGER]
    assert record.levelno == logging.WARNING
    # The whole context, not a few keys of it: anything else added to it (the URL, the error's
    # text) would be written to stdout, where the URL is not redacted.
    assert record.ctx == {  # type: ignore[attr-defined]
        "worker": "outbox",
        "problem": f"{type(error).__module__}.{type(error).__qualname__}",
    }
    out = rendered([record])
    assert URL not in out
    assert not any(fragment in out for fragment in URL_FRAGMENTS)
    assert URL not in caplog.text and "secret-part" not in caplog.text


@pytest.mark.parametrize("status", [404, 500, 301])
async def test_an_unsuccessful_answer_is_a_warning_and_a_redirect_is_not_followed(
    caplog: pytest.LogCaptureFixture, status: int
) -> None:
    service, clock = Service(), Clock()
    service.status = status
    pinger = _pinger(service, clock)

    with caplog.at_level(logging.WARNING, logger=PINGS_LOGGER):
        pinger.succeeded()
        await _settle()

    assert len(service.requests) == 1
    [warning] = caplog.records
    assert warning.ctx == {"worker": "outbox", "problem": f"status {status}"}  # type: ignore[attr-defined]
    assert not any(fragment in rendered([warning]) for fragment in URL_FRAGMENTS)
    assert URL not in caplog.text


async def test_failures_are_logged_at_most_once_a_minute(caplog: pytest.LogCaptureFixture) -> None:
    service, clock = Service(), Clock()
    service.error = httpx.ConnectError("down")
    pinger = _pinger(service, clock)

    with caplog.at_level(logging.WARNING, logger=PINGS_LOGGER):
        for _ in range(6):  # six pings, 31 s apart: 2.5 minutes of failures
            pinger.succeeded()
            await _settle()
            clock.advance(31)

    assert len(service.requests) == 6
    assert len(caplog.records) == 3  # at 0 s, 62 s and 124 s


async def test_a_success_after_failures_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    service, clock = Service(), Clock()
    pinger = _pinger(service, clock)
    with caplog.at_level(logging.DEBUG, logger=PINGS_LOGGER):
        pinger.succeeded()
        await _settle()
    assert caplog.records == []


# --- shutdown ------------------------------------------------------------------------------------


async def test_closing_abandons_a_ping_in_flight_and_stops_further_ones() -> None:
    service, clock = Service(), Clock()
    service.block = asyncio.Event()
    pinger = _pinger(service, clock)
    pinger.succeeded()
    await _settle()

    await pinger.aclose()
    clock.advance(500)
    pinger.succeeded()
    await _settle()

    assert len(service.requests) == 1


async def test_closing_does_not_wait_for_a_ping_the_service_never_answers() -> None:
    service, clock = Service(), Clock()
    service.block = asyncio.Event()
    pinger = _pinger(service, clock, timeout_seconds=30.0)  # only a cancel returns sooner
    pinger.succeeded()
    await _settle()

    await asyncio.wait_for(pinger.aclose(), timeout=1.0)


# --- the setting ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("disable_env", "expected"),
    [
        ("DISABLE_OUTBOX_WORKER", "HEALTHCHECKS_URL_OUTBOX_WORKER"),
        ("DISABLE_JOB_REGISTRY_POLLER", "HEALTHCHECKS_URL_JOB_REGISTRY_POLLER"),
        ("DISABLE_SAVED_SEARCH_MATCHER", "HEALTHCHECKS_URL_SAVED_SEARCH_MATCHER"),
        ("DISABLE_GMAIL_REPLY_CHECKER", "HEALTHCHECKS_URL_GMAIL_REPLY_CHECKER"),
        ("DISABLE_HIRING_SIGNAL_CACHE_PURGE", "HEALTHCHECKS_URL_HIRING_SIGNAL_CACHE_PURGE"),
    ],
)
def test_the_setting_is_named_after_the_workers_disable_flag(
    disable_env: str, expected: str
) -> None:
    assert healthcheck_env_name(disable_env) == expected


@pytest.mark.parametrize(
    "value",
    [
        URL,
        "https://hc-ping.com/0a1b2c3d-0000-4000-8000-000000000000",
        "https://hc-ping.com/pingkey123/outbox-worker",
        "https://healthchecks.internal.example:8443/ping/0a1b2c3d",
        "  " + URL + "\n",
    ],
)
def test_a_good_url_is_accepted_and_trimmed(value: str) -> None:
    assert parse_ping_url("HEALTHCHECKS_URL_X", value) == value.strip()


@pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
def test_unset_or_blank_means_no_check(value: str | None) -> None:
    assert parse_ping_url("HEALTHCHECKS_URL_X", value) is None


@pytest.mark.parametrize(
    "value",
    [
        "http://hc-ping.com/abc",
        "ftp://hc-ping.com/abc",
        "hc-ping.com/abc",
        "//hc-ping.com/abc",
        "https://user:pw@hc-ping.com/abc",
        "https://user@hc-ping.com/abc",
        "https://:pw@hc-ping.com/abc",
        "https://hc-ping.com",
        "https://hc-ping.com/",
        "https:///abc",
        "https://hc-ping.com/abc?x=1",
        "https://hc-ping.com/abc?",
        "https://hc-ping.com/abc#frag",
        "https://hc-ping.com/a bc",
        "https://hc-ping.com/abc\ndef",
        "https://hc-ping.com:notaport/abc",
        "https://hc-ping.com/" + "a" * 3000,
        "not a url",
        "javascript:alert(1)",
    ],
)
def test_a_bad_url_stops_the_boot_naming_the_variable_and_never_the_value(
    caplog: pytest.LogCaptureFixture, value: str
) -> None:
    with caplog.at_level(logging.DEBUG), pytest.raises(ConfigurationError) as raised:
        parse_ping_url("HEALTHCHECKS_URL_OUTBOX_WORKER", value)

    assert "HEALTHCHECKS_URL_OUTBOX_WORKER" in str(raised.value)
    assert value.strip() not in str(raised.value)
    assert value.strip() not in caplog.text
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL] == [
        f"the API cannot start: {raised.value}"
    ]


def test_load_ping_url_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEALTHCHECKS_URL_X", raising=False)
    assert load_ping_url("HEALTHCHECKS_URL_X") is None
    monkeypatch.setenv("HEALTHCHECKS_URL_X", URL)
    assert load_ping_url("HEALTHCHECKS_URL_X") == URL
    monkeypatch.setenv("HEALTHCHECKS_URL_X", "http://nope.example/x")
    with pytest.raises(ConfigurationError):
        load_ping_url("HEALTHCHECKS_URL_X")


# --- the set of pingers --------------------------------------------------------------------------


@pytest.fixture
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "OUTBOX_WORKER",
        "JOB_REGISTRY_POLLER",
        "SAVED_SEARCH_MATCHER",
        "GMAIL_REPLY_CHECKER",
        "HIRING_SIGNAL_CACHE_PURGE",
    ):
        monkeypatch.delenv(f"HEALTHCHECKS_URL_{name}", raising=False)


async def test_the_shared_client_has_the_five_second_timeout_and_follows_no_redirects() -> None:
    client = WorkerPings()._make_client()
    try:
        assert client.follow_redirects is False
        assert client.timeout == httpx.Timeout(5.0)
    finally:
        await client.aclose()


@pytest.mark.usefixtures("_clean_env")
async def test_with_no_url_there_is_no_pinger_and_no_client() -> None:
    pings = WorkerPings()
    assert pings.for_worker("outbox", disable_env="DISABLE_OUTBOX_WORKER", enabled=True) is None
    assert pings.pingers == {}
    assert pings._client is None
    await pings.aclose()


@pytest.mark.usefixtures("_clean_env")
async def test_an_enabled_worker_with_a_url_gets_a_pinger_and_they_share_one_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HEALTHCHECKS_URL_OUTBOX_WORKER", URL)
    monkeypatch.setenv("HEALTHCHECKS_URL_GMAIL_REPLY_CHECKER", URL + "-two")
    pings = WorkerPings()

    outbox = pings.for_worker("outbox", disable_env="DISABLE_OUTBOX_WORKER", enabled=True)
    gmail = pings.for_worker(
        "gmail_reply_checker", disable_env="DISABLE_GMAIL_REPLY_CHECKER", enabled=True
    )
    poller = pings.for_worker(
        "job_registry_poller", disable_env="DISABLE_JOB_REGISTRY_POLLER", enabled=True
    )

    assert outbox is not None and gmail is not None and poller is None
    assert outbox._client is gmail._client
    assert set(pings.pingers) == {"outbox", "gmail_reply_checker"}
    await pings.aclose()
    assert pings._client is None


@pytest.mark.usefixtures("_clean_env")
async def test_a_disabled_worker_gets_no_pinger_even_with_a_url_and_the_boot_says_so(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("HEALTHCHECKS_URL_OUTBOX_WORKER", URL)
    pings = WorkerPings()
    with caplog.at_level(logging.WARNING, logger=PINGS_LOGGER):
        pinger = pings.for_worker("outbox", disable_env="DISABLE_OUTBOX_WORKER", enabled=False)

    assert pinger is None
    assert pings.pingers == {} and pings._client is None
    [warning] = caplog.records
    assert warning.ctx == {"worker": "outbox", "variable": "HEALTHCHECKS_URL_OUTBOX_WORKER"}  # type: ignore[attr-defined]
    assert URL not in caplog.text
    assert not any(fragment in rendered([warning]) for fragment in URL_FRAGMENTS)


@pytest.mark.usefixtures("_clean_env")
async def test_a_bad_url_stops_the_boot_even_for_a_disabled_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HEALTHCHECKS_URL_OUTBOX_WORKER", "http://not-https.example/x")
    with pytest.raises(ConfigurationError):
        WorkerPings().for_worker("outbox", disable_env="DISABLE_OUTBOX_WORKER", enabled=False)


# --- a check that is not a worker's (`for_check`) ---------------------------------------------

CHECK_ENV = "HEALTHCHECKS_URL_SOME_PROBE"
OFF_MESSAGE = "a healthcheck URL is set for a probe that does not run here"


@pytest.mark.usefixtures("_clean_env")
async def test_a_check_with_a_url_gets_a_pinger_under_its_own_name_on_the_shared_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HEALTHCHECKS_URL_OUTBOX_WORKER", URL)
    monkeypatch.setenv(CHECK_ENV, URL + "-probe")
    pings = WorkerPings()

    outbox = pings.for_worker("outbox", disable_env="DISABLE_OUTBOX_WORKER", enabled=True)
    probe = pings.for_check("some_probe", env_name=CHECK_ENV, enabled=True, off_message=OFF_MESSAGE)

    assert outbox is not None and probe is not None
    assert probe.worker == "some_probe" and probe._client is outbox._client
    assert set(pings.pingers) == {"outbox", "some_probe"}
    await pings.aclose()
    assert pings._client is None


@pytest.mark.usefixtures("_clean_env")
async def test_a_check_with_no_url_is_none_but_its_name_is_known(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pings = WorkerPings()
    assert pings.for_check("p", env_name=CHECK_ENV, enabled=True, off_message=OFF_MESSAGE) is None
    assert pings.pingers == {} and pings._client is None

    with caplog.at_level(logging.WARNING, logger=PINGS_LOGGER):
        pings.warn_unrecognised_settings({CHECK_ENV: "anything", "HEALTHCHECKS_URL_OTHER": "x"})
    [warning] = caplog.records  # only the stray one: the check's own name is not
    assert warning.ctx["variable"] == "HEALTHCHECKS_URL_OTHER"  # type: ignore[attr-defined]
    assert CHECK_ENV in warning.ctx["expected"]  # type: ignore[attr-defined]


@pytest.mark.usefixtures("_clean_env")
async def test_a_check_that_is_off_gets_no_pinger_and_the_callers_own_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(CHECK_ENV, URL)
    pings = WorkerPings()
    with caplog.at_level(logging.WARNING, logger=PINGS_LOGGER):
        pinger = pings.for_check(
            "some_probe", env_name=CHECK_ENV, enabled=False, off_message=OFF_MESSAGE
        )

    assert pinger is None and pings.pingers == {} and pings._client is None
    [warning] = caplog.records
    assert warning.getMessage() == OFF_MESSAGE
    assert warning.ctx == {"worker": "some_probe", "variable": CHECK_ENV}  # type: ignore[attr-defined]
    assert not any(fragment in rendered([warning]) for fragment in URL_FRAGMENTS)


@pytest.mark.usefixtures("_clean_env")
@pytest.mark.parametrize("enabled", [True, False])
async def test_a_bad_check_url_stops_the_boot_whether_or_not_the_check_is_used(
    monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    monkeypatch.setenv(CHECK_ENV, "http://not-https.example/x")
    with pytest.raises(ConfigurationError, match=CHECK_ENV):
        WorkerPings().for_check("p", env_name=CHECK_ENV, enabled=enabled, off_message=OFF_MESSAGE)


class _KeysOnly(Mapping[str, str]):
    """An environment that gives its names and nothing else: reading a value fails (and so do
    `get`, `items` and `values`, which read through it)."""

    def __init__(self, names: Iterable[str]) -> None:
        self._names = list(names)

    def __getitem__(self, key: str) -> str:
        raise AssertionError("a value was read")

    def __iter__(self) -> Iterator[str]:
        return iter(self._names)

    def __len__(self) -> int:
        return len(self._names)


def _known(*disable_envs: str) -> WorkerPings:
    pings = WorkerPings()
    for disable_env in disable_envs:
        pings.for_worker("w", disable_env=disable_env, enabled=False)
    return pings


@pytest.mark.usefixtures("_clean_env")
def test_unrecognised_settings_are_found_by_name_without_reading_any_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pings = _known("DISABLE_OUTBOX_WORKER", "DISABLE_GMAIL_REPLY_CHECKER")
    environ = _KeysOnly(
        [
            "HEALTHCHECKS_URL_OUTBOX_WORKER",  # known
            "HEALTHCHECKS_URL_GMAIL_REPLY_CHECKER",  # known
            "HEALTHCHECKS_URL_OUTBOX",
            "healthchecks_url_gmail_reply_checker",
            "Healthcheck_Poller",
            "PATH",
            "DISABLE_OUTBOX_WORKER",
        ]
    )

    with caplog.at_level(logging.WARNING, logger=PINGS_LOGGER):
        pings.warn_unrecognised_settings(environ)

    assert [r.ctx["variable"] for r in caplog.records] == [  # type: ignore[attr-defined]
        "HEALTHCHECKS_URL_OUTBOX",
        "healthchecks_url_gmail_reply_checker",
        "Healthcheck_Poller",
    ]
    assert all(
        r.ctx["expected"]  # type: ignore[attr-defined]
        == ["HEALTHCHECKS_URL_GMAIL_REPLY_CHECKER", "HEALTHCHECKS_URL_OUTBOX_WORKER"]
        for r in caplog.records
    )
    assert not any(fragment in rendered(caplog.records) for fragment in URL_FRAGMENTS)


def test_a_scan_that_fails_is_contained(caplog: pytest.LogCaptureFixture) -> None:
    class Exploding(dict[str, str]):
        def __iter__(self) -> Iterator[str]:
            raise RuntimeError("boom")

    with caplog.at_level(logging.WARNING, logger=PINGS_LOGGER):
        WorkerPings().warn_unrecognised_settings(Exploding())  # does not raise

    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert "boom" not in caplog.text


def test_the_defaults_are_the_ones_the_docs_state() -> None:
    assert worker_pings.PING_TIMEOUT_SECONDS == 5.0
    assert worker_pings.MIN_PING_INTERVAL_SECONDS == 30.0
    assert worker_pings.WARN_INTERVAL_SECONDS == 60.0
