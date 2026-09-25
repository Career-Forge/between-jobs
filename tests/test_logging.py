"""Server logging (launch plan P0.3): the JSON formatter, redaction, message-free
tracebacks, request ids, the ApiError and unhandled-error paths, and a guard
against exceptions being swallowed without a trace."""

from __future__ import annotations

import ast
import io
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from between_jobs.api.app import app
from between_jobs.api.app_state import get_supabase
from between_jobs.api.ats_liveness import verify_liveness
from between_jobs.api.auth import require_user_id
from between_jobs.api.logging_setup import (
    JsonFormatter,
    configure_logging,
    format_exception_safely,
    redact,
    request_id_var,
)
from between_jobs.api.search_providers import SearchResult

_FAKE_KEY = "sk-or-v1-0123456789abcdef0123456789abcdef0123456789abcdef"
# Key-shaped test values are assembled at runtime so no literal in this public
# repo looks like a real credential to a secret scanner.
_TOKEN_TAIL = "FAKEfakeFAKEfake" + "0123456789abcdefXY"
_BOT_TOKEN = "123456789:" + _TOKEN_TAIL
_SUPABASE_SECRET = "sb_" + "secret_" + "FAKEfakeFAKE0123456789"
# Kept out of the `raise` lines below: a traceback shows each frame's source
# line, which is code; the message itself must never appear.
_PERSONAL = "could not push: Jordan Rivera, 555-0134"
_ROW_DETAIL = f"row (x, {_FAKE_KEY})"
_SRC = Path(__file__).resolve().parents[1] / "src" / "between_jobs"


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def json_log(caplog: pytest.LogCaptureFixture) -> Iterator[io.StringIO]:
    """What the production handler would write: every record through
    JsonFormatter. Levels are left to caplog, which restores them."""
    caplog.set_level(logging.INFO)
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    yield buffer
    root.removeHandler(handler)


def _record(msg: str, *args: Any, name: str = "test", **kwargs: Any) -> logging.LogRecord:
    record = logging.LogRecord(name, logging.INFO, __file__, 1, msg, args or None, None)
    for key, value in kwargs.items():
        setattr(record, key, value)
    return record


# --- redaction -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        (f"calling openrouter with {_FAKE_KEY}", _FAKE_KEY),
        ("Authorization: Bearer abc.def-ghi_jkl", "abc.def-ghi_jkl"),
        ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA"),
        (
            "token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0"
            ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9",
            "eyJhbGciOiJIUzI1NiJ9",
        ),
        (f"POST https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage", _TOKEN_TAIL),
        (f"bot {_BOT_TOKEN} in the url", _TOKEN_TAIL),
        (f"{_SUPABASE_SECRET} leaked", "FAKEfakeFAKE0123456789"),
        (
            "GET https://api.adzuna.com/v1/api/jobs/us/search/1?app_id=abc&app_key=supersecret1",
            "supersecret1",
        ),
        ('{"api_key": "not-an-sk-shaped-secret"}', "not-an-sk-shaped-secret"),
        ("{'secret': 'abc123secret'}", "abc123secret"),
        ('{"secret_2": "appkey0123"}', "appkey0123"),
        ("client_secret=GOCSPX-abcdefghijklmnopqrstuv", "abcdefghijklmnop"),
        ("X-RapidAPI-Key: 9f8e7d6c5b4a39281706f5e4d3c2b1a0", "9f8e7d6c5b4a3928"),
        ("firecrawl key fc-0123456789abcdef0123", "0123456789abcdef0123"),
        ("groq key gsk_0123456789abcdefghijABCD", "0123456789abcdefghij"),
        ("password=hunter2hunter2", "hunter2hunter2"),
        ("reply from jordan.rivera@example.com", "jordan.rivera@example.com"),
    ],
)
def test_redact_removes_secrets(text: str, secret: str) -> None:
    assert secret not in redact(text)


@pytest.mark.parametrize(
    "text",
    [
        "scored 30 jobs for saved search 5f1c; max_tokens=500 tokens: 12",
        "board risk-assessment-pipeline and whisk-technologies-inc at 12:30:45",
        "sqlstate 23505 for 3f2b1c9e-8a7d-4e6f-9b0a-1c2d3e4f5a6b",
    ],
)
def test_redact_leaves_ordinary_text_alone(text: str) -> None:
    assert redact(text) == text


# --- exceptions --------------------------------------------------------------


def test_tracebacks_keep_types_frames_and_safe_fields_but_never_messages() -> None:
    request = httpx.Request("POST", f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage")
    response = httpx.Response(403, request=request, text="Forbidden: bot was blocked by the user")
    try:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(_PERSONAL) from e
    except RuntimeError as outer:
        text = format_exception_safely(outer)

    assert "httpx.HTTPStatusError [status=403 host=api.telegram.org]" in text
    assert "builtins.RuntimeError" in text
    assert "direct cause" in text
    assert "raise_for_status" in text  # frames stay
    assert _TOKEN_TAIL not in text
    assert "Jordan Rivera" not in text
    assert "blocked by the user" not in text


def test_formatter_writes_the_safe_traceback_and_postgres_sqlstate() -> None:
    try:
        raise APIError({"message": _ROW_DETAIL, "code": "23505", "details": None})
    except APIError as e:
        record = _record("insert failed")
        record.exc_info = (type(e), e, e.__traceback__)

    entry = json.loads(JsonFormatter().format(record))

    assert entry["exc"]["type"] == "postgrest.exceptions.APIError"
    assert "code=23505" in entry["exc"]["traceback"]
    assert _FAKE_KEY not in json.dumps(entry)
    assert "row (x," not in entry["exc"]["traceback"]


# --- formatter ---------------------------------------------------------------


def test_formatter_writes_one_json_object_with_request_id_and_ctx() -> None:
    token = request_id_var.set("req-12345678")
    try:
        line = JsonFormatter().format(
            _record("scored %d jobs", 30, ctx={"saved_search_id": "abc", "count": 3})
        )
    finally:
        request_id_var.reset(token)

    entry = json.loads(line)
    assert "\n" not in line
    assert entry["msg"] == "scored 30 jobs"
    assert entry["level"] == "INFO"
    assert entry["request_id"] == "req-12345678"
    assert entry["ctx"] == {"saved_search_id": "abc", "count": 3}


def test_formatter_output_is_ascii_safe_even_with_a_lone_surrogate() -> None:
    line = JsonFormatter().format(_record("bad field", ctx={"fields": "body.\udcff"}))
    line.encode("utf-8")  # would raise if the surrogate were written raw


def test_formatter_strips_client_and_query_from_uvicorn_access_lines() -> None:
    record = _record(
        '%s - "%s %s HTTP/%s" %d',
        "203.0.113.7:5000",
        "GET",
        "/profile/integrations/gmail/callback?code=4/0AVMBsJh-secret&state=abc",
        "1.1",
        200,
        name="uvicorn.access",
    )

    entry = json.loads(JsonFormatter().format(record))

    assert "4/0AVMBsJh-secret" not in entry["msg"]
    assert "203.0.113.7" not in entry["msg"]
    assert entry["msg"].startswith('- - "GET /profile/integrations/gmail/callback?<query-redacted>')


def test_configure_logging_scopes_log_level_to_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    root = logging.getLogger()
    app_logger = logging.getLogger("between_jobs")
    previous = (root.level, app_logger.level)
    try:
        monkeypatch.setenv("LOG_LEVEL", "debug")
        configure_logging()
        configure_logging()
        marked = [h for h in root.handlers if getattr(h, "_between_jobs_handler", False)]
        assert len(marked) == 1
        assert app_logger.level == logging.DEBUG
        assert root.level == logging.WARNING
        # DEBUG must not reach the libraries that print prompts, URLs or headers.
        for name in ("httpx", "httpcore", "openai", "hpack"):
            assert logging.getLogger(name).getEffectiveLevel() == logging.WARNING

        for unknown in ("LOUD", "NOTSET", "TRACE"):
            monkeypatch.setenv("LOG_LEVEL", unknown)
            configure_logging()
            assert app_logger.level == logging.INFO
    finally:
        root.setLevel(previous[0])
        app_logger.setLevel(previous[1])


# --- request ids ---------------------------------------------------------------


def test_every_response_carries_a_request_id() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert len(response.headers["x-request-id"]) == 32


def test_a_well_formed_incoming_request_id_is_kept_and_a_bad_one_replaced() -> None:
    with TestClient(app) as client:
        kept = client.get("/health", headers={"X-Request-ID": "trace-abc-12345678"})
        replaced = client.get("/health", headers={"X-Request-ID": "short"})
    assert kept.headers["x-request-id"] == "trace-abc-12345678"
    assert replaced.headers["x-request-id"] != "short"
    assert len(replaced.headers["x-request-id"]) == 32


# --- error paths ------------------------------------------------------------------


class _RaisingSessionsTable:
    def insert(self, _row: dict[str, Any]) -> _RaisingSessionsTable:
        return self

    async def execute(self) -> Any:
        raise APIError(
            {
                "message": f"connection string leaked {_FAKE_KEY}",
                "code": "XX000",
                "details": None,
                "hint": None,
            }
        )


class _RaisingSupabase:
    def table(self, _name: str) -> _RaisingSessionsTable:
        return _RaisingSessionsTable()


def test_api_error_handler_logs_the_cause_type_and_never_the_request_body(
    caplog: pytest.LogCaptureFixture, json_log: io.StringIO
) -> None:
    app.dependency_overrides[get_supabase] = lambda: _RaisingSupabase()
    app.dependency_overrides[require_user_id] = lambda: "11111111-1111-1111-1111-111111111111"

    with TestClient(app) as client:
        response = client.post(
            "/sessions",
            json={"context": {"openrouter_key": _FAKE_KEY}},
            headers={"X-Request-ID": "req-api-error-1"},
        )

    assert response.status_code == 500
    records = [r for r in caplog.records if r.name == "between_jobs.api.app"]
    assert len(records) == 1
    ctx = records[0].ctx  # type: ignore[attr-defined]
    assert ctx["code"] == "INTERNAL_ERROR"
    assert ctx["route"] == "/sessions"
    assert ctx["cause_type"] == "postgrest.exceptions.APIError"
    assert ctx["cause_code"] == "XX000"
    assert records[0].levelno == logging.ERROR

    written = json_log.getvalue()
    assert '"request_id": "req-api-error-1"' in written
    assert _FAKE_KEY not in caplog.text
    assert _FAKE_KEY not in written


def test_an_unhandled_error_answers_500_with_the_request_id_and_logs_it(
    caplog: pytest.LogCaptureFixture, json_log: io.StringIO
) -> None:
    def boom() -> Any:
        raise RuntimeError(f"unexpected, with {_FAKE_KEY} in the message")

    app.dependency_overrides[get_supabase] = boom
    app.dependency_overrides[require_user_id] = lambda: "11111111-1111-1111-1111-111111111111"

    with TestClient(app) as client:
        response = client.post(
            "/sessions", json={"context": {}}, headers={"X-Request-ID": "req-unhandled-1"}
        )

    assert response.status_code == 500
    assert response.headers["x-request-id"] == "req-unhandled-1"
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    lines = [json.loads(line) for line in json_log.getvalue().splitlines()]
    unhandled = [line for line in lines if line["msg"] == "unhandled error"]
    assert len(unhandled) == 1
    assert unhandled[0]["request_id"] == "req-unhandled-1"
    assert unhandled[0]["exc"]["type"] == "builtins.RuntimeError"
    assert _FAKE_KEY not in json_log.getvalue()


# --- liveness regression --------------------------------------------------------


async def test_a_malformed_idna_host_is_kept_not_a_crash() -> None:
    result = SearchResult(
        provider="serper",
        title="Data Engineer",
        company="Sample Co",
        location=None,
        remote=None,
        apply_url="https://xn--a.com/jobs/2",
        snippet="",
        posted_at=None,
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as http:
        alive, dead = await verify_liveness(http, [result])
    assert len(alive) == 1
    assert dead == 0


# --- no silent swallowing ---------------------------------------------------------


_BROAD = {"Exception", "BaseException"}


def _names(node: ast.expr | None) -> list[str]:
    if node is None:
        return ["BaseException"]
    items = node.elts if isinstance(node, ast.Tuple) else [node]
    return [
        item.id if isinstance(item, ast.Name) else item.attr
        for item in items
        if isinstance(item, ast.Name | ast.Attribute)
    ]


def _logs_or_raises(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in ("logger", "logging")
        ):
            return True
        # a module's own logging helper (worker_supervision._log_failure)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id.startswith("_log")
        ):
            return True
    return False


# The formatter can't log a failure from inside itself; its guarded attribute
# reads are the one place a broad catch may stay silent.
_MAY_STAY_SILENT = {"api/logging_setup.py"}


def test_no_broad_exception_is_swallowed_without_a_trace() -> None:
    """A broad catch (`except Exception`, a bare `except`, `suppress(Exception)`)
    hides a failure completely unless it logs or re-raises. Every one in the
    package must do one of the two."""
    offenders = []
    for path in sorted(_SRC.rglob("*.py")):
        if str(path.relative_to(_SRC)) in _MAY_STAY_SILENT:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            where = f"{path.relative_to(_SRC)}:{getattr(node, 'lineno', '?')}"
            if (
                isinstance(node, ast.ExceptHandler)
                and _BROAD & set(_names(node.type))
                and not _logs_or_raises(node)
            ):
                offenders.append(where)
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "attr", getattr(node.func, "id", None)) == "suppress"
                and _BROAD & {n for arg in node.args for n in _names(arg)}
            ):
                offenders.append(where)
    assert offenders == []
