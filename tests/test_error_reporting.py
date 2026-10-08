"""Error reporting (error_reporting.py): off unless SENTRY_DSN is set, validated at boot,
started with exactly the safe options, and never able to fail a request or a worker.

The SDK is a fake (tests/fake_sentry.py) put into `sys.modules`, so none of this needs
`sentry_sdk` installed. test_error_reporting_real_sdk.py runs the real one where present."""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Any

import pytest
from fake_sentry import FakeSentry
from fastapi.testclient import TestClient
from rendered_logs import rendered

from between_jobs.api import error_reporting
from between_jobs.api.app import app
from between_jobs.api.env import ConfigurationError
from between_jobs.api.error_reporting import (
    flush_error_reporting,
    init_error_reporting,
    load_sentry_config,
    report_exception,
    reporting_enabled,
    sentry_options,
)
from between_jobs.api.sentry_scrub import before_send, scrub_breadcrumb

# Made up, but shaped like a real DSN: a public key, an ingest host, a project id.
DSN = "https://0123456789abcdef0123456789abcdef@o123456.ingest.sentry.io/4501234567"
ENV_LOGGER = "between_jobs.api.env"
REPORTING_LOGGER = "between_jobs.api.error_reporting"
# Pieces of DSN (its public key, host and project id) that no log line may carry.
DSN_FRAGMENTS = ("0123456789abcdef", "o123456", "4501234567", "ingest.sentry.io")
_SETTINGS = (
    "SENTRY_DSN",
    "SENTRY_ENVIRONMENT",
    "SENTRY_TRACES_SAMPLE_RATE",
    "RAILWAY_GIT_COMMIT_SHA",
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(error_reporting, "_sdk", None)
    monkeypatch.setattr(error_reporting, "_failure_warned", False)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)


@pytest.fixture
def sdk(monkeypatch: pytest.MonkeyPatch) -> FakeSentry:
    return FakeSentry().install(monkeypatch)


def _critical(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL]


# --- off ----------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
def test_with_no_dsn_nothing_is_imported_and_reporting_is_off(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is not None:
        monkeypatch.setenv("SENTRY_DSN", value)
    imported: list[str] = []
    real_import = importlib.import_module

    def spy(name: str, package: str | None = None) -> Any:
        imported.append(name)
        return real_import(name, package)

    monkeypatch.setattr(importlib, "import_module", spy)
    # An import of the SDK would fail loudly, not quietly succeed.
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)

    assert init_error_reporting() is False
    assert not reporting_enabled()
    assert [name for name in imported if name.startswith("sentry_sdk")] == []


def test_the_other_settings_are_not_even_read_when_there_is_no_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "not a number")
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "bad environment!")
    assert load_sentry_config() is None
    assert init_error_reporting() is False


def test_reporting_does_nothing_and_never_raises_when_off(
    caplog: pytest.LogCaptureFixture, sdk: FakeSentry
) -> None:
    with caplog.at_level(logging.DEBUG):
        report_exception(RuntimeError("x"), tags={"worker": "w"}, fingerprint=["a"])
        asyncio.run(flush_error_reporting())

    assert not reporting_enabled()
    # A self-hoster with no DSN must never see a warning about an SDK they did not set up.
    assert caplog.records == []
    assert not error_reporting._failure_warned
    assert sdk.captured == [] and sdk.flush_calls == []


def test_importing_and_booting_the_app_with_no_dsn_never_imports_the_sdk() -> None:
    """In a fresh interpreter: other tests of this process import the real SDK, and a plain
    `import sentry_sdk` (or `from sentry_sdk... import`) anywhere the app reaches would load it
    for every self-hoster, whatever they configured. The spy test above only sees
    `importlib.import_module`."""
    code = (
        "import sys\n"
        "import between_jobs.api.app\n"
        "import between_jobs.api.error_reporting as er\n"
        "assert er.init_error_reporting() is False\n"
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] == 'sentry_sdk')\n"
        "assert not loaded, loaded\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("SENTRY_")}
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr


# --- the DSN ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dsn",
    [
        DSN,
        "https://key@sentry.example.com/1",
        "https://key@sentry.example.com:9000/1",
        "https://pub:secret@sentry.example.com/12",
        "https://key@sentry.example.com/prefix/12",
        "  " + DSN + "\n",
    ],
)
def test_a_well_formed_dsn_is_accepted(monkeypatch: pytest.MonkeyPatch, dsn: str) -> None:
    monkeypatch.setenv("SENTRY_DSN", dsn)
    config = load_sentry_config()
    assert config is not None
    assert config.dsn == dsn.strip()


@pytest.mark.parametrize(
    "dsn",
    [
        "http://key@sentry.example.com/1",
        "ftp://key@sentry.example.com/1",
        "https://sentry.example.com/1",
        "https://@sentry.example.com/1",
        "https://key@/1",
        "https://key@sentry.example.com",
        "https://key@sentry.example.com/",
        "https://key@sentry.example.com/1?x=1",
        "https://key@sentry.example.com/1#frag",
        "https://key@sentry.example.com/a b",
        "https://key@sentry.example.com/1\n2",
        "https://key@sentry.example.com:notaport/1",
        "https://key@sentry.example.com/%2e%2e",
        "https://key@sentry.example.com//1",
        "https://key@sentry.example.com/1/",
        "https://key@sentry.example.com/1.json",
        "https://key@sentry.example.com/1:x",
        "https://key@sentry.example.com/12!",
        "not a dsn at all",
        "key@sentry.example.com/1",
        "https://key@" + "a" * 600 + ".example.com/1",
    ],
)
def test_a_malformed_dsn_stops_the_boot_naming_the_variable_and_never_the_value(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    sdk: FakeSentry,
    dsn: str,
) -> None:
    monkeypatch.setenv("SENTRY_DSN", dsn)
    with caplog.at_level(logging.DEBUG), pytest.raises(ConfigurationError) as raised:
        init_error_reporting()

    assert "SENTRY_DSN" in str(raised.value)
    assert dsn.strip() not in str(raised.value)
    assert dsn.strip() not in caplog.text
    assert _critical(caplog) == [f"the API cannot start: {raised.value}"]
    assert sdk.init_calls == []


def test_the_dsn_is_not_in_the_config_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_DSN", DSN)
    config = load_sentry_config()
    assert config is not None
    assert DSN not in repr(config)
    assert "0123456789abcdef" not in repr(config)


# --- the other settings ----------------------------------------------------------------------


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_DSN", DSN)
    config = load_sentry_config()
    assert config is not None
    assert config.environment == "production"
    assert config.release is None
    assert config.traces_sample_rate == 0.0


def test_a_blank_environment_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_DSN", DSN)
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "   ")
    config = load_sentry_config()
    assert config is not None
    assert config.environment == "production"


def test_environment_and_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_DSN", DSN)
    monkeypatch.setenv("SENTRY_ENVIRONMENT", " staging ")
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "2a8c33e5d1f0c9b8a7e6d5c4b3a2f1e0d9c8b7a6")
    config = load_sentry_config()
    assert config is not None
    assert config.environment == "staging"
    assert config.release == "2a8c33e5d1f0c9b8a7e6d5c4b3a2f1e0d9c8b7a6"


@pytest.mark.parametrize("release", ["", "has space", "a/b", "x" * 100, "bad\nline", "-lead"])
def test_a_release_that_is_not_a_plain_identifier_is_ignored_not_fatal(
    monkeypatch: pytest.MonkeyPatch, release: str
) -> None:
    monkeypatch.setenv("SENTRY_DSN", DSN)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", release)
    config = load_sentry_config()
    assert config is not None
    assert config.release is None


@pytest.mark.parametrize("environment", ["has space", "a/b", "x" * 65, "new\nline", "-lead", "é"])
def test_a_bad_environment_stops_the_boot(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, environment: str
) -> None:
    monkeypatch.setenv("SENTRY_DSN", DSN)
    monkeypatch.setenv("SENTRY_ENVIRONMENT", environment)
    with caplog.at_level(logging.CRITICAL, logger=ENV_LOGGER), pytest.raises(ConfigurationError):
        load_sentry_config()
    [message] = _critical(caplog)
    assert "SENTRY_ENVIRONMENT" in message
    assert environment not in message


@pytest.mark.parametrize(
    ("raw", "expected"), [("0", 0.0), ("0.0", 0.0), ("0.25", 0.25), (" 1 ", 1.0)]
)
def test_the_trace_rate_is_a_number_from_zero_to_one(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: float
) -> None:
    monkeypatch.setenv("SENTRY_DSN", DSN)
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", raw)
    config = load_sentry_config()
    assert config is not None
    assert config.traces_sample_rate == expected


@pytest.mark.parametrize(
    "raw", ["1.01", "-0.1", "2", "abc", "nan", "inf", "-inf", "0,5", "50%", "1e400", "true"]
)
def test_a_trace_rate_out_of_range_stops_the_boot(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, raw: str
) -> None:
    monkeypatch.setenv("SENTRY_DSN", DSN)
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", raw)
    with caplog.at_level(logging.CRITICAL, logger=ENV_LOGGER), pytest.raises(ConfigurationError):
        load_sentry_config()
    [message] = _critical(caplog)
    assert "SENTRY_TRACES_SAMPLE_RATE" in message
    assert raw not in message


# --- starting the SDK ----------------------------------------------------------------------------


def test_a_valid_dsn_starts_the_sdk_once_with_exactly_the_safe_options(
    monkeypatch: pytest.MonkeyPatch, sdk: FakeSentry
) -> None:
    monkeypatch.setenv("SENTRY_DSN", DSN)
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "staging")
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc1234")

    assert init_error_reporting() is True
    assert reporting_enabled()

    options = dict(sdk.options)
    starlette, fastapi, logs = options.pop("integrations")
    assert isinstance(starlette, sdk.StarletteIntegration)
    assert isinstance(fastapi, sdk.FastApiIntegration)
    assert isinstance(logs, sdk.LoggingIntegration)
    # Neither events nor breadcrumbs from log lines.
    assert logs.kwargs == {"level": None, "event_level": None}
    assert options == {
        "dsn": DSN,
        "environment": "staging",
        "release": "abc1234",
        "traces_sample_rate": None,
        "send_default_pii": False,
        "max_request_body_size": "never",
        "include_local_variables": False,
        "auto_session_tracking": False,
        "before_send": before_send,
        "before_send_transaction": before_send,
        "before_breadcrumb": scrub_breadcrumb,
        "auto_enabling_integrations": False,
        "trace_propagation_targets": [],
    }


def test_starting_says_so_in_the_log_without_the_dsn_or_any_piece_of_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, sdk: FakeSentry
) -> None:
    monkeypatch.setenv("SENTRY_DSN", DSN)
    with caplog.at_level(logging.INFO, logger=REPORTING_LOGGER):
        init_error_reporting()

    [record] = [r for r in caplog.records if r.name == REPORTING_LOGGER]
    assert record.getMessage() == "error reporting is on"
    assert record.ctx == {"environment": "production", "traces_sample_rate": 0.0}  # type: ignore[attr-defined]
    out = rendered([record])
    assert not any(fragment in out for fragment in DSN_FRAGMENTS)


def test_without_a_release_the_option_is_left_out(
    monkeypatch: pytest.MonkeyPatch, sdk: FakeSentry
) -> None:
    monkeypatch.setenv("SENTRY_DSN", DSN)
    init_error_reporting()
    assert "release" not in sdk.options
    assert sdk.options["environment"] == "production"


def test_tracing_is_off_unless_a_rate_is_set(
    monkeypatch: pytest.MonkeyPatch, sdk: FakeSentry
) -> None:
    """A rate of 0 is passed as None: with a number set the SDK would still continue a trace
    a stranger's `sentry-trace` header started."""
    monkeypatch.setenv("SENTRY_DSN", DSN)
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0")
    init_error_reporting()
    assert sdk.options["traces_sample_rate"] is None


def test_a_set_trace_rate_is_passed_on(monkeypatch: pytest.MonkeyPatch, sdk: FakeSentry) -> None:
    monkeypatch.setenv("SENTRY_DSN", DSN)
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")
    init_error_reporting()
    assert sdk.options["traces_sample_rate"] == 0.1


def test_sentry_options_is_pure() -> None:
    config = error_reporting.SentryConfig(
        dsn=DSN, environment="e", release=None, traces_sample_rate=0.0
    )
    first = sentry_options(config, ["a"])
    second = sentry_options(config, ["a"])
    assert first == second


def test_an_sdk_that_cannot_start_stops_the_boot_without_leaking_the_dsn(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, sdk: FakeSentry
) -> None:
    monkeypatch.setenv("SENTRY_DSN", DSN)
    sdk.init_error = ValueError(f"cannot use {DSN}")
    with caplog.at_level(logging.DEBUG), pytest.raises(ConfigurationError) as raised:
        init_error_reporting()

    assert "SENTRY_DSN" in str(raised.value)
    assert DSN not in str(raised.value) and "0123456789abcdef" not in str(raised.value)
    # Not chained: the traceback uvicorn prints would carry the SDK's own message.
    assert raised.value.__context__ is None and raised.value.__cause__ is None
    assert DSN not in caplog.text
    assert not reporting_enabled()
    # The line the service writes: the type of the failure and nothing the SDK said (its
    # messages can quote the DSN).
    [record] = [r for r in caplog.records if r.name == REPORTING_LOGGER]
    assert record.levelno == logging.ERROR
    assert record.ctx == {"error_type": "builtins.ValueError"}  # type: ignore[attr-defined]
    out = rendered([record])
    assert not any(fragment in out for fragment in (*DSN_FRAGMENTS, "cannot use"))


def test_a_dsn_with_no_sdk_installed_stops_the_boot(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SENTRY_DSN", DSN)
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)
    with caplog.at_level(logging.CRITICAL, logger=ENV_LOGGER), pytest.raises(ConfigurationError):
        init_error_reporting()
    [message] = _critical(caplog)
    assert "SENTRY_DSN" in message and "sentry-sdk" in message
    assert DSN not in message


def test_turning_the_dsn_off_again_turns_reporting_off(
    monkeypatch: pytest.MonkeyPatch, sdk: FakeSentry
) -> None:
    monkeypatch.setenv("SENTRY_DSN", DSN)
    assert init_error_reporting() is True
    monkeypatch.delenv("SENTRY_DSN")
    assert init_error_reporting() is False
    assert not reporting_enabled()


# --- reporting ------------------------------------------------------------------------------------


def test_report_exception_hands_the_error_tags_and_fingerprint_to_the_sdk(
    monkeypatch: pytest.MonkeyPatch, sdk: FakeSentry
) -> None:
    monkeypatch.setenv("SENTRY_DSN", DSN)
    init_error_reporting()
    error = RuntimeError("boom")

    report_exception(error, tags={"worker": "outbox", "n": 3}, fingerprint=["w", "outbox"])  # type: ignore[dict-item]
    report_exception(error)

    assert sdk.captured == [
        (error, {"tags": {"worker": "outbox", "n": "3"}, "fingerprint": ["w", "outbox"]}),
        (error, {}),
    ]


def test_an_sdk_that_raises_cannot_fail_the_caller_and_is_logged_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, sdk: FakeSentry
) -> None:
    monkeypatch.setenv("SENTRY_DSN", DSN)
    init_error_reporting()
    sdk.capture_error = RuntimeError(f"transport down for {DSN}")
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            report_exception(ValueError("x"), tags={"a": "b"})

    [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert DSN not in caplog.text
    assert warning.ctx == {"error_type": "builtins.RuntimeError"}  # type: ignore[attr-defined]
    out = rendered([warning])
    assert not any(fragment in out for fragment in (*DSN_FRAGMENTS, "transport down"))


# --- shutdown flush ------------------------------------------------------------------------------


async def test_flush_runs_in_a_thread_and_does_not_block_the_event_loop(
    monkeypatch: pytest.MonkeyPatch, sdk: FakeSentry
) -> None:
    monkeypatch.setenv("SENTRY_DSN", DSN)
    init_error_reporting()
    sdk.flush_delay = 0.4
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    task = asyncio.create_task(ticker())
    started = time.monotonic()
    await flush_error_reporting(timeout=2.0)
    elapsed = time.monotonic() - started
    task.cancel()

    assert elapsed >= 0.4
    assert ticks >= 8  # the loop kept running while the flush waited
    [(timeout, thread)] = sdk.flush_calls
    assert timeout == 2.0
    assert thread != threading.get_ident()


async def test_a_flush_that_hangs_is_given_up_on_after_the_timeout(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, sdk: FakeSentry
) -> None:
    monkeypatch.setenv("SENTRY_DSN", DSN)
    init_error_reporting()
    gate = threading.Event()
    sdk.flush_gate = gate
    try:
        started = time.monotonic()
        with caplog.at_level(logging.WARNING):
            await flush_error_reporting(timeout=0.05)
        assert time.monotonic() - started < 3.0
    finally:
        gate.set()
    assert [r for r in caplog.records if r.levelno == logging.WARNING]


async def test_a_flush_that_raises_is_swallowed(
    monkeypatch: pytest.MonkeyPatch, sdk: FakeSentry
) -> None:
    monkeypatch.setenv("SENTRY_DSN", DSN)
    init_error_reporting()
    sdk.flush_error = RuntimeError("network")
    await flush_error_reporting(timeout=0.1)


# --- the app: boot and shutdown ------------------------------------------------------------------


def test_booting_with_none_of_the_settings_behaves_as_before(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)  # any attempt to import it fails
    with caplog.at_level(logging.INFO), TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert not reporting_enabled()
    assert "error reporting" not in caplog.text


def test_booting_with_a_dsn_starts_reporting_before_serving_and_flushes_at_shutdown(
    monkeypatch: pytest.MonkeyPatch, sdk: FakeSentry
) -> None:
    monkeypatch.setenv("SENTRY_DSN", DSN)
    with TestClient(app) as client:
        assert len(sdk.init_calls) == 1  # already started when the first request is served
        assert client.get("/health").status_code == 200
        assert sdk.flush_calls == []
    # Flushed once, with the module's default bound, which is short: an unreachable Sentry
    # must not hold the shutdown for longer than the product-event flush (5 s) next to it.
    [(timeout, _thread)] = sdk.flush_calls
    assert timeout == error_reporting.FLUSH_TIMEOUT_SECONDS <= 5.0


def test_booting_with_a_malformed_dsn_is_refused_naming_the_variable_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, sdk: FakeSentry
) -> None:
    monkeypatch.setenv("SENTRY_DSN", "http://not-https@example.com/1")
    with (
        caplog.at_level(logging.CRITICAL, logger=ENV_LOGGER),
        pytest.raises(ConfigurationError),
        TestClient(app),
    ):
        pass

    [message] = _critical(caplog)
    assert "SENTRY_DSN" in message
    assert "not-https" not in message
    assert sdk.init_calls == []


def test_an_sdk_that_fails_at_shutdown_does_not_break_the_shutdown(
    monkeypatch: pytest.MonkeyPatch, sdk: FakeSentry
) -> None:
    monkeypatch.setenv("SENTRY_DSN", DSN)
    sdk.flush_error = RuntimeError("network")
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
