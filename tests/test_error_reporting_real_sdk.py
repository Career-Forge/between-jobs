"""The real `sentry_sdk` behind error reporting, with a recording transport (no network).

The rest of the error-reporting tests use a fake SDK so they need nothing installed. These
run only where the package is installed (CI installs the project's dependencies, which
include it), and show what a fake cannot: that the SDK accepts the options we pass, and that
what its integrations collect from a hostile request is gone by the time an event is handed
to the transport."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from between_jobs.api import error_reporting
from between_jobs.api.app import app
from between_jobs.api.credential_resolver import ResolvedCredential
from between_jobs.api.errors import ApiError
from between_jobs.api.forge_engines_client import call_apply
from between_jobs.api.sentry_scrub import MESSAGE_OMITTED

# Everything from the SDK is reached through these two names (typed Any), so that this module
# type-checks the same whether or not the package is installed.
_SKIP = "sentry-sdk is not installed here; CI installs the project dependencies, which include it"
sentry_sdk: Any = pytest.importorskip("sentry_sdk", reason=_SKIP)
sentry_transport: Any = pytest.importorskip("sentry_sdk.transport", reason=_SKIP)

PUBLIC_KEY = "0123456789abcdef0123456789abcdef"
DSN = f"https://{PUBLIC_KEY}@o123456.ingest.sentry.io/4501234567"
JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiIxMjM0NTY3ODkwIiwiZW1haWwiOiJhQGIuY29tIn0."
    "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)
OPENROUTER_KEY = "sk-or-v1-" + "0123456789abcdef" * 4
EMAIL = "jane.doe+jobs@example.com"
IP = "203.0.113.77"
COOKIE_VALUE = "sb-session-value-9f8e7d6c"
BODY_TEXT = "Jane Doe, 12 Example Street: my full resume text"
QUERY_VALUE = "oauth-code-5d4c3b2a"

# What a person's resume and a job posting hold, as the resume engine would be sent them.
RESUME_NAME = "Pat Smith"
RESUME_PHONE = "+1 201 555 0187"
RESUME_CITY = "Montclair, NJ"
RESUME_EMPLOYER = "Initech Holdings"
RESUME_BULLET = "Led the payments migration serving forty million users"
JOB_TEXT = "Principal engineer for the Initech Holdings treasury platform"


def _recorder_class() -> Any:
    """A transport that collects every envelope item the SDK would have sent. Built with
    `type()` because its base class is only known when the SDK is installed."""

    def init(self: Any) -> None:
        # With the DSN, as the SDK's own transports are made: the trace header's public key
        # comes from it.
        sentry_transport.Transport.__init__(self, {"dsn": DSN})
        self.items = []
        self.envelopes = []

    def capture_envelope(self: Any, envelope: Any) -> None:
        self.envelopes.append((envelope.headers, [item.type for item in envelope.items]))
        for item in envelope.items:
            self.items.append((item.type, item.payload.json))

    def flush(self: Any, timeout: float, callback: Any = None) -> None:
        pass

    def kill(self: Any) -> None:
        pass

    def events(self: Any) -> list[dict[str, Any]]:
        return [payload for kind, payload in self.items if kind == "event"]

    return type(
        "Recorder",
        (sentry_transport.Transport,),
        {
            "__init__": init,
            "capture_envelope": capture_envelope,
            "flush": flush,
            "kill": kill,
            "events": property(events),
        },
    )


Recorder: Any = _recorder_class()


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    transport = Recorder()
    real_options = error_reporting.sentry_options
    monkeypatch.setattr(
        error_reporting,
        "sentry_options",
        lambda config, integrations: {**real_options(config, integrations), "transport": transport},
    )
    monkeypatch.setattr(error_reporting, "_sdk", None)
    monkeypatch.setattr(error_reporting, "_failure_warned", False)
    monkeypatch.setenv("SENTRY_DSN", DSN)
    monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    yield transport
    # Leave no live client behind for the tests that follow.
    sentry_sdk.get_global_scope().set_client(sentry_sdk.client.NonRecordingClient())


class _ApplyRequest(BaseModel):
    resume_template: dict[str, Any]
    credential: dict[str, Any]
    job_posting: dict[str, Any]
    new_required_field: str  # added by a newer build of the engine than this API was built for


_SKEWED_ENGINE = FastAPI()
"""A real FastAPI service with the default validation handling, one required field ahead of
what this API sends -- what a deploy-order skew between the two services looks like."""


@_SKEWED_ENGINE.post("/apply")
async def _apply(body: _ApplyRequest) -> dict[str, Any]:
    return {}


@contextmanager
def _routes() -> Iterator[None]:
    async def unhandled(thing_id: str) -> None:
        raise RuntimeError(f"unhandled with {OPENROUTER_KEY} for {EMAIL} using {JWT}")

    async def engine_rejects(thing_id: str) -> None:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_SKEWED_ENGINE)) as http:
            await call_apply(
                http,
                resume_template={
                    "personal": {"name": RESUME_NAME, "phone": RESUME_PHONE, "city": RESUME_CITY},
                    "experience": [{"company": RESUME_EMPLOYER, "bullets": [RESUME_BULLET]}],
                },
                job_snapshot={
                    "job_id": "job-1",
                    "title": "Principal Engineer",
                    "company_name": "Initech",
                    "description_text": JOB_TEXT,
                },
                credential=ResolvedCredential(
                    provider="openrouter",
                    model="anthropic/claude-sonnet-4-6",
                    secret=OPENROUTER_KEY,
                    base_url=None,
                    source="byok",
                ),
                now="2026-10-01T00:00:00.000Z",
            )

    async def server_error(thing_id: str) -> None:
        raise ApiError("INTERNAL_ERROR", "Something went wrong.")

    async def client_error(thing_id: str) -> None:
        raise ApiError("NOT_FOUND", "No such thing.")

    paths = {
        "/__real/unhandled/{thing_id}": unhandled,
        "/__real/engine-rejects/{thing_id}": engine_rejects,
        "/__real/server/{thing_id}": server_error,
        "/__real/client/{thing_id}": client_error,
    }
    for path, endpoint in paths.items():
        app.add_api_route(path, endpoint, methods=["GET", "POST"])
    try:
        yield
    finally:
        app.router.routes[:] = [
            r for r in app.router.routes if getattr(r, "path", None) not in paths
        ]


HOSTILE_HEADERS = {
    "Authorization": f"Bearer {JWT}",
    "Cookie": f"sb-access-token={COOKIE_VALUE}",
    "apikey": OPENROUTER_KEY,
    "X-Telegram-Bot-Api-Secret-Token": "webhook-secret-value",
    "X-Forwarded-For": IP,
    "X-Real-IP": IP,
    "X-Request-ID": "req-real-sdk-0001",
    # A stranger asking this service to record the request in full.
    "sentry-trace": "0123456789abcdef0123456789abcdef-0123456789abcdef-1",
    "baggage": "sentry-trace_id=0123456789abcdef0123456789abcdef,sentry-sampled=true",
}


def _hostile_request(client: TestClient, path: str) -> int:
    return client.post(
        f"{path}?code={QUERY_VALUE}&email={EMAIL}",
        headers=HOSTILE_HEADERS,
        json={"resume_text": BODY_TEXT, "openrouter_key": OPENROUTER_KEY},
    ).status_code


def test_the_options_we_pass_are_accepted_and_reporting_is_on(recorder: Any) -> None:
    with TestClient(app) as client:
        assert error_reporting.reporting_enabled()
        assert client.get("/health").status_code == 200
    assert recorder.items == []


def test_a_hostile_request_that_fails_reaches_the_transport_with_none_of_its_secrets(
    recorder: Any,
) -> None:
    with _routes(), TestClient(app, raise_server_exceptions=False) as client:
        assert _hostile_request(client, "/__real/unhandled/abc") == 500

    [event] = recorder.events
    blob = json.dumps(event, ensure_ascii=False)
    for leaked in (
        JWT,
        OPENROUTER_KEY,
        EMAIL,
        IP,
        COOKIE_VALUE,
        BODY_TEXT,
        QUERY_VALUE,
        "webhook-secret-value",
    ):
        assert leaked not in blob, leaked

    assert event["level"] == "error"
    assert event["environment"] == "production"
    assert event["tags"] == {
        "route": "/__real/unhandled/{thing_id}",
        "method": "POST",
        "request_id": "req-real-sdk-0001",
    }
    [exception] = event["exception"]["values"]
    assert exception["type"] == "RuntimeError"
    assert "<email>" in exception["value"]
    assert all("vars" not in frame for frame in exception["stacktrace"]["frames"])
    assert set(event["request"]) <= {"url", "method", "headers"}
    assert "?" not in event["request"]["url"]
    assert {name.lower() for name in event["request"]["headers"]} <= {
        "accept",
        "content-length",
        "content-type",
        "host",
        "origin",
        "user-agent",
        "x-request-id",
    }
    for absent in ("user", "server_name", "_meta"):
        assert absent not in event
    assert event.get("extra", {}).get("sys.argv") in (None, "[Filtered]")


def test_a_server_error_from_the_api_is_one_event_and_a_client_error_is_none(
    recorder: Any,
) -> None:
    with _routes(), TestClient(app, raise_server_exceptions=False) as client:
        assert _hostile_request(client, "/__real/server/abc") == 500
        assert _hostile_request(client, "/__real/client/abc") == 404

    [event] = recorder.events
    assert event["exception"]["values"][0]["type"] == "ApiError"
    assert event["tags"]["error_code"] == "INTERNAL_ERROR"


def test_a_resume_engine_that_rejects_a_run_reports_where_and_never_what_the_person_wrote(
    recorder: Any,
) -> None:
    """The engine's default 422 echoes the whole request body, and a deploy-order skew between
    the two services makes that answer likely. It must reach neither the person's error
    message nor Sentry."""
    with _routes(), TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/__real/engine-rejects/abc")

    assert response.status_code == 500
    assert response.json()["error"]["message"] == (
        "The resume engine rejected this run: body.new_required_field: Field required"
    )
    [event] = recorder.events
    blob = json.dumps(event, ensure_ascii=False)
    for leaked in (
        RESUME_NAME,
        RESUME_PHONE,
        RESUME_CITY,
        RESUME_EMPLOYER,
        RESUME_BULLET,
        JOB_TEXT,
        OPENROUTER_KEY,
    ):
        assert leaked not in blob, leaked
    assert event["tags"]["error_code"] == "RUN_FAILED"
    [exception] = event["exception"]["values"]
    assert exception["type"] == "ApiError"
    assert exception["value"] == MESSAGE_OMITTED


def test_the_trace_header_of_an_error_envelope_is_valid_and_not_mangled_by_the_scrubber(
    recorder: Any,
) -> None:
    with _routes(), TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/__real/unhandled/abc").status_code == 500

    [headers] = [headers for headers, kinds in recorder.envelopes if kinds == ["event"]]
    assert headers["trace"]["public_key"] == PUBLIC_KEY
    assert headers["trace"]["environment"] == "production"
    assert "[Filtered]" not in json.dumps(headers)
    [event] = recorder.events
    # The SDK moves the context into the envelope header; it is not left in the event as well.
    assert "dynamic_sampling_context" not in event["contexts"]["trace"]


def test_the_trace_header_of_a_transaction_keeps_its_sampling_decision(
    recorder: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "1")
    with _routes(), TestClient(app, raise_server_exceptions=False) as client:
        client.get("/__real/client/abc")

    transaction_headers = [
        headers for headers, kinds in recorder.envelopes if "transaction" in kinds
    ]
    assert transaction_headers, "tracing was switched on, so a transaction is expected"
    for headers in transaction_headers:
        trace = headers["trace"]
        assert trace["public_key"] == PUBLIC_KEY
        assert trace["sampled"] == "true" and trace["sample_rate"] == "1.0"
        assert "sample_rand" in trace and "transaction" in trace
        assert "[Filtered]" not in json.dumps(headers)


def test_log_lines_are_not_events_and_a_forged_trace_header_starts_no_transaction(
    recorder: Any,
) -> None:
    log = logging.getLogger("between_jobs.test_real_sdk")
    with _routes(), TestClient(app, raise_server_exceptions=False) as client:
        log.error("an error line that is only a log line")
        try:
            raise ValueError("caught and logged")
        except ValueError:
            log.exception("and an exception line")
        client.get("/__real/client/abc", headers=HOSTILE_HEADERS)
        client.get("/health", headers=HOSTILE_HEADERS)

    assert recorder.items == []


def test_a_trace_rate_that_is_set_records_transactions_that_are_scrubbed_too(
    recorder: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "1")
    with _routes(), TestClient(app, raise_server_exceptions=False) as client:
        client.post(
            f"/__real/client/abc?code={QUERY_VALUE}",
            headers=HOSTILE_HEADERS,
            json={"resume_text": BODY_TEXT},
        )

    transactions = [payload for kind, payload in recorder.items if kind == "transaction"]
    assert transactions, "tracing was switched on, so a transaction is expected"
    blob = json.dumps(transactions, ensure_ascii=False)
    for leaked in (JWT, OPENROUTER_KEY, COOKIE_VALUE, BODY_TEXT, QUERY_VALUE, IP):
        assert leaked not in blob, leaked


async def test_a_report_made_in_a_worker_task_arrives_with_its_tags_and_fingerprint(
    recorder: Any,
) -> None:
    error_reporting.init_error_reporting()
    try:
        raise ConnectionError("database unreachable at db.example.internal")
    except ConnectionError as error:
        error_reporting.report_exception(
            error,
            tags={"worker": "outbox", "consecutive_failures": "2"},
            fingerprint=["worker-tick-failure", "outbox", "builtins.ConnectionError"],
        )
    await error_reporting.flush_error_reporting(timeout=1.0)

    [event] = recorder.events
    assert event["fingerprint"] == ["worker-tick-failure", "outbox", "builtins.ConnectionError"]
    assert event["tags"] == {"worker": "outbox", "consecutive_failures": "2"}


def test_no_trace_headers_leave_on_a_request_this_service_makes(recorder: Any) -> None:
    seen: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        error_reporting.init_error_reporting()
        urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/x").read()
    finally:
        server.shutdown()
        server.server_close()

    assert seen
    assert {name.lower() for name in seen[0]} & {"sentry-trace", "baggage"} == set()


async def test_flushing_with_the_real_sdk_returns_promptly(recorder: Any) -> None:
    error_reporting.init_error_reporting()
    await asyncio.wait_for(error_reporting.flush_error_reporting(timeout=1.0), timeout=5.0)


async def test_an_unreachable_sentry_costs_a_request_and_the_shutdown_next_to_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real transport, aimed at a closed local port: the report cannot be delivered, and
    neither the request that caused it nor the shutdown flush may wait for that."""
    monkeypatch.setattr(error_reporting, "_sdk", None)
    monkeypatch.setenv("SENTRY_DSN", "https://0123456789abcdef@127.0.0.1:9/1")
    monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    try:
        with _routes(), TestClient(app, raise_server_exceptions=False) as client:
            started = time.monotonic()
            assert client.get("/__real/unhandled/abc").status_code == 500
            assert time.monotonic() - started < 2.0
            started = time.monotonic()
            await error_reporting.flush_error_reporting(timeout=1.0)
            assert time.monotonic() - started < 4.0
    finally:
        sentry_sdk.get_client().close(timeout=0)
        sentry_sdk.get_global_scope().set_client(sentry_sdk.client.NonRecordingClient())
