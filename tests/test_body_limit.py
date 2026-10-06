"""The request-body size cap (api/body_limit.py).

The middleware is exercised two ways: directly as an ASGI app, where every awkward case can be
built byte by byte (a Content-Length that lies, a body that streams past the cap, a header that
is not a number), and through the real FastAPI app, which proves the 413 comes out the other
side of the request-id and CORS middleware with their headers on it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, cast

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from between_jobs.api.app import app
from between_jobs.api.auth import require_user_id
from between_jobs.api.body_limit import (
    DEFAULT_MAX_REQUEST_BODY_BYTES,
    PATH_PREFIX_LIMITS,
    BodyLimitMiddleware,
    describe_bytes,
    limit_for_path,
    max_request_body_bytes,
    too_large_error,
)
from between_jobs.api.env import ConfigurationError

_CAP = 1000


@pytest.fixture(autouse=True)
def _small_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", str(_CAP))


# -- a tiny ASGI harness --------------------------------------------------------------------


class _Handler:
    """The application behind the middleware: reads the whole body, answers with its length,
    and remembers what it was shown."""

    def __init__(self, *, start_response_first: bool = False, raise_after: bool = False) -> None:
        self.called = 0
        self.bytes_seen = 0
        self.saw_disconnect = False
        self.start_response_first = start_response_first
        self.raise_after = raise_after

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.called += 1
        if self.start_response_first:
            await send({"type": "http.response.start", "status": 200, "headers": []})
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                self.saw_disconnect = True
                if self.raise_after:
                    raise RuntimeError("the client went away")
                return
            self.bytes_seen += len(message.get("body", b""))
            if not message.get("more_body", False):
                break
        if not self.start_response_first:
            await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": str(self.bytes_seen).encode()})


class _AfterRefusal:
    """An application that keeps using its ASGI interface after it has been told the client
    went away: it reads once more, and answers anyway. Neither may get through."""

    def __init__(self, *, read_again: bool = False, respond: bool = False) -> None:
        self.read_again = read_again
        self.respond = respond
        self.second_receive: Message | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if message["type"] == "http.disconnect" or not message.get("more_body", False):
                break
        if self.read_again:
            self.second_receive = await receive()
        if self.respond:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"late"})


class _Result:
    def __init__(self) -> None:
        self.messages: list[Message] = []

    @property
    def status(self) -> int:
        return int(next(m for m in self.messages if m["type"] == "http.response.start")["status"])

    @property
    def headers(self) -> dict[str, str]:
        start = next(m for m in self.messages if m["type"] == "http.response.start")
        return {k.decode().lower(): v.decode() for k, v in start["headers"]}

    @property
    def body(self) -> bytes:
        return b"".join(
            m.get("body", b"") for m in self.messages if m["type"] == "http.response.body"
        )

    def json(self) -> Any:
        return json.loads(self.body)

    @property
    def starts(self) -> int:
        return sum(1 for m in self.messages if m["type"] == "http.response.start")


async def _call(
    inner: Callable[[Scope, Receive, Send], Any],
    *,
    chunks: list[bytes],
    content_length: list[str] | None = None,
    path: str = "/anything",
    prefix_limits: dict[str, int] | None = None,
    receive_forbidden: bool = False,
) -> _Result:
    headers = [(b"content-length", value.encode("utf-8")) for value in (content_length or [])]
    scope: Scope = {"type": "http", "method": "POST", "path": path, "headers": headers}
    queue: list[Message] = [
        {"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1}
        for i, chunk in enumerate(chunks)
    ] or [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive() -> Message:
        assert not receive_forbidden, "the body must not be read at all"
        if queue:
            return queue.pop(0)
        return {"type": "http.disconnect"}

    result = _Result()

    async def send(message: Message) -> None:
        result.messages.append(message)

    middleware = BodyLimitMiddleware(inner, prefix_limits=prefix_limits)
    await middleware(scope, receive, send)
    return result


# -- under the cap --------------------------------------------------------------------------


async def test_a_body_under_the_cap_passes_untouched() -> None:
    handler = _Handler()

    result = await _call(handler, chunks=[b"a" * 400, b"b" * 400], content_length=["800"])

    assert result.status == 200
    assert result.body == b"800"
    assert handler.bytes_seen == 800


async def test_a_body_of_exactly_the_cap_passes_and_one_byte_more_does_not() -> None:
    at_cap = await _call(_Handler(), chunks=[b"x" * _CAP], content_length=[str(_CAP)])
    over = await _call(_Handler(), chunks=[b"x" * (_CAP + 1)], content_length=[str(_CAP + 1)])

    assert at_cap.status == 200
    assert over.status == 413


async def test_a_request_with_no_body_and_no_content_length_passes() -> None:
    result = await _call(_Handler(), chunks=[])
    assert result.status == 200
    assert result.body == b"0"


# -- a declared length over the cap --------------------------------------------------------


async def test_a_declared_length_over_the_cap_is_refused_before_a_byte_is_read() -> None:
    handler = _Handler()

    result = await _call(
        handler,
        chunks=[b"x" * 10],
        content_length=[str(_CAP + 1)],
        receive_forbidden=True,
    )

    assert result.status == 413
    assert handler.called == 0
    error = result.json()["error"]
    assert error["code"] == "PAYLOAD_TOO_LARGE"
    assert error["retryable"] is False
    assert error["details"] == {"max_bytes": _CAP}
    assert "1000 bytes" in error["message"]
    assert result.headers["content-type"] == "application/json"
    assert result.headers["connection"] == "close"


async def test_an_absurdly_large_declared_length_is_just_a_413() -> None:
    result = await _call(_Handler(), chunks=[], content_length=["9" * 40], receive_forbidden=True)
    assert result.status == 413


# -- a body that streams past the cap ------------------------------------------------------


async def test_a_chunked_body_that_streams_past_the_cap_is_cut_off_at_the_cap() -> None:
    handler = _Handler()

    result = await _call(handler, chunks=[b"x" * 400] * 5)  # 2000 bytes, no Content-Length

    assert result.status == 413
    assert result.starts == 1  # the application's own answer was dropped, not sent after it
    assert result.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert handler.bytes_seen <= _CAP  # the handler never saw more than the cap
    assert handler.saw_disconnect


async def test_a_content_length_that_lies_low_does_not_let_the_body_through() -> None:
    handler = _Handler()

    result = await _call(handler, chunks=[b"x" * 600, b"x" * 600], content_length=["10"])

    assert result.status == 413
    assert handler.bytes_seen <= _CAP


async def test_the_chunk_that_would_cross_the_cap_is_not_passed_on() -> None:
    """The cap is a hard one: 600 + 600 > 1000, so the second chunk never reaches the handler."""
    handler = _Handler()

    await _call(handler, chunks=[b"x" * 600, b"x" * 600])

    assert handler.bytes_seen == 600


async def test_if_the_application_already_started_answering_the_body_is_still_cut_off() -> None:
    handler = _Handler(start_response_first=True)

    result = await _call(handler, chunks=[b"x" * 600, b"x" * 600])

    assert handler.bytes_seen <= _CAP
    assert handler.saw_disconnect
    # Too late for a 413: the application's own response had already begun.
    assert result.status == 200
    assert result.starts == 1


async def test_an_error_the_application_raises_after_the_refusal_is_not_a_second_failure() -> None:
    handler = _Handler(raise_after=True)

    result = await _call(handler, chunks=[b"x" * 600, b"x" * 600])  # does not raise

    assert result.status == 413


async def test_an_error_the_application_raises_with_no_refusal_still_propagates() -> None:
    async def broken(scope: Scope, receive: Receive, send: Send) -> None:
        raise RuntimeError("a real bug")

    with pytest.raises(RuntimeError, match="a real bug"):
        await _call(broken, chunks=[b"hi"])


# -- a Content-Length that is not a length -------------------------------------------------


@pytest.mark.parametrize(
    "values",
    [["abc"], ["-5"], ["+5"], ["1e3"], ["1_0"], ["0x10"], [""], [" "], ["5.0"], ["١٢"]],
)
async def test_a_malformed_content_length_is_a_400_never_a_500(values: list[str]) -> None:
    handler = _Handler()

    result = await _call(handler, chunks=[b"x"], content_length=values, receive_forbidden=True)

    assert result.status == 400
    assert result.json()["error"]["code"] == "INVALID_INPUT"
    assert (
        result.headers["connection"] == "close"
    )  # the unread body must not be reused as a request
    assert handler.called == 0


@pytest.mark.parametrize("digits", [4300, 4301, 5000, 100_000])
async def test_a_content_length_of_thousands_of_digits_is_refused_not_a_crash(digits: int) -> None:
    """Python 3.12+ refuses to turn a string of more than 4300 digits into an int, and that
    refusal used to escape the middleware as an unhandled error (a 500)."""
    handler = _Handler()

    result = await _call(handler, chunks=[], content_length=["9" * digits], receive_forbidden=True)

    assert result.status == 413
    assert handler.called == 0


async def test_two_enormous_content_lengths_that_differ_are_a_400() -> None:
    result = await _call(_Handler(), chunks=[], content_length=["9" * 5000, "8" * 5000])
    assert result.status == 400


async def test_leading_zeros_do_not_make_a_length_a_different_length() -> None:
    same = await _call(_Handler(), chunks=[b"x" * 7], content_length=["007", "7"])
    zeros = await _call(_Handler(), chunks=[], content_length=["0" * 5000])

    assert same.status == 200
    assert zeros.status == 200  # 5000 zeros is a length of 0


async def test_two_content_lengths_that_disagree_are_a_400() -> None:
    result = await _call(_Handler(), chunks=[b"x"], content_length=["1", "2"])
    assert result.status == 400


async def test_two_identical_content_lengths_are_one_length() -> None:
    result = await _call(_Handler(), chunks=[b"x" * 5], content_length=["5", "5"])
    assert result.status == 200


# -- after the refusal ---------------------------------------------------------------------


async def test_an_application_that_answers_after_the_refusal_is_not_heard() -> None:
    """The 413 is the only response; a response the application still sends after being told
    its client went away is dropped, not sent as a second one."""
    handler = _AfterRefusal(respond=True)

    result = await _call(handler, chunks=[b"x" * 600] * 3)

    assert result.status == 413
    assert result.starts == 1
    assert b"late" not in result.body


async def test_an_application_that_reads_again_after_the_refusal_only_ever_hears_disconnect() -> (
    None
):
    """It must not be handed the rest of the body, and reading on must not make the middleware
    refuse (and answer) a second time."""
    handler = _AfterRefusal(read_again=True)

    result = await _call(handler, chunks=[b"x" * 600] * 3)

    assert handler.second_receive == {"type": "http.disconnect"}
    assert result.status == 413
    assert result.starts == 1


# -- other kinds of scope ------------------------------------------------------------------


async def test_a_lifespan_scope_passes_straight_through() -> None:
    seen: list[str] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    middleware = BodyLimitMiddleware(inner)
    await middleware({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
    await middleware({"type": "websocket", "headers": []}, None, None)  # type: ignore[arg-type]

    assert seen == ["lifespan", "websocket"]


# -- the limit itself: default, environment, per-prefix ------------------------------------


def test_the_default_is_one_mebibyte(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAX_REQUEST_BODY_BYTES")
    assert max_request_body_bytes() == DEFAULT_MAX_REQUEST_BODY_BYTES == 1_048_576


def test_an_empty_setting_reads_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "")
    assert max_request_body_bytes() == 1_048_576


def test_the_setting_overrides_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", " 2048 ")
    assert max_request_body_bytes() == 2048


@pytest.mark.parametrize("raw", ["0", "-1", "big", "1.5", "1_000", "+10", "1e6", " ", "0x400"])
def test_a_bad_setting_stops_the_boot_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, raw: str
) -> None:
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", raw)

    with pytest.raises(ConfigurationError) as excinfo:
        max_request_body_bytes()

    assert str(excinfo.value) == "MAX_REQUEST_BODY_BYTES must be a positive whole number of bytes"
    critical = [r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL]
    assert critical == [
        "the API cannot start: MAX_REQUEST_BODY_BYTES must be a positive whole number of bytes"
    ]


def test_the_lifespan_refuses_to_boot_on_a_bad_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "lots")

    with pytest.raises(ConfigurationError, match="MAX_REQUEST_BODY_BYTES"), TestClient(app):
        pass


def test_the_longest_matching_prefix_wins_and_unmatched_paths_get_the_default() -> None:
    prefixes = {"/upload": 5000, "/upload/huge": 90000}
    assert limit_for_path("/upload/resume", 1000, prefixes) == 5000
    assert limit_for_path("/upload/huge/file", 1000, prefixes) == 90000
    assert limit_for_path("/applications", 1000, prefixes) == 1000
    assert limit_for_path("/applications", 1000, {}) == 1000


def test_a_prefix_matches_the_start_of_a_path_never_the_middle() -> None:
    prefixes = {"/upload": 5000}
    assert limit_for_path("/x/upload/y", 1000, prefixes) == 1000
    assert limit_for_path("/x/upload", 1000, prefixes) == 1000
    assert limit_for_path("/upload", 1000, prefixes) == 5000


def test_the_only_raised_cap_is_the_resume_upload() -> None:
    assert PATH_PREFIX_LIMITS == {"/profile/import-document": 5 * 1024 * 1024}


def test_the_resume_upload_path_gets_five_mebibytes_and_its_neighbours_the_default() -> None:
    default = 1024 * 1024
    five = 5 * 1024 * 1024
    assert limit_for_path("/profile/import-document", default, PATH_PREFIX_LIMITS) == five
    assert limit_for_path("/profile/versions", default, PATH_PREFIX_LIMITS) == default
    assert limit_for_path("/profile/import", default, PATH_PREFIX_LIMITS) == default
    assert limit_for_path("/profile/current", default, PATH_PREFIX_LIMITS) == default
    assert limit_for_path("/applications", default, PATH_PREFIX_LIMITS) == default


async def test_the_upload_path_takes_five_mebibytes_through_the_middleware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    five = 5 * 1024 * 1024
    monkeypatch.delenv("MAX_REQUEST_BODY_BYTES")  # the default (1 MiB) is what it overrides
    path = "/profile/import-document"

    at_cap = await _call(
        _Handler(), chunks=[b"x" * five], content_length=[str(five)], path=path, prefix_limits=None
    )
    over = await _call(
        _Handler(),
        chunks=[b"x" * (five + 1)],
        content_length=[str(five + 1)],
        path=path,
        prefix_limits=None,
        receive_forbidden=True,
    )
    streamed = await _call(_Handler(), chunks=[b"x" * 1_048_576] * 6, path=path, prefix_limits=None)
    elsewhere = await _call(
        _Handler(),
        chunks=[b"x" * 1_048_577],
        content_length=["1048577"],
        path="/profile/versions",
        prefix_limits=None,
        receive_forbidden=True,
    )

    assert at_cap.status == 200
    assert over.status == 413 and over.json()["error"]["details"] == {"max_bytes": five}
    assert streamed.status == 413  # 6 MiB with no declared length is cut off at the cap
    assert elsewhere.status == 413
    assert elsewhere.json()["error"]["details"] == {"max_bytes": 1024 * 1024}


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (1_048_576, "1 MiB"),
        (2 * 1_048_576, "2 MiB"),
        (2048, "2 KiB"),
        (1024, "1 KiB"),
        (1000, "1000 bytes"),
        (1025, "1025 bytes"),
        (1_048_577, "1048577 bytes"),
        (1_048_576 + 1024, "1025 KiB"),
    ],
)
def test_a_size_is_said_in_the_unit_a_person_would_use(count: int, expected: str) -> None:
    assert describe_bytes(count) == expected


def test_the_error_for_the_default_cap_says_one_mebibyte_and_carries_the_number() -> None:
    error = too_large_error(DEFAULT_MAX_REQUEST_BODY_BYTES)

    assert error.code == "PAYLOAD_TOO_LARGE"
    assert error.status_code == 413
    assert "(the limit is 1 MiB)" in error.message
    assert error.details == {"max_bytes": 1_048_576}


async def test_a_request_over_the_default_cap_is_refused_in_mebibytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAX_REQUEST_BODY_BYTES")

    result = await _call(
        _Handler(), chunks=[], content_length=[str(1_048_576 + 1)], receive_forbidden=True
    )

    assert result.status == 413
    error = result.json()["error"]
    assert error["details"] == {"max_bytes": 1_048_576}
    assert "1 MiB" in error["message"]


async def test_a_prefix_override_lets_a_known_route_take_more() -> None:
    prefixes = {"/upload": 5000}
    big = [b"x" * 3000]

    allowed = await _call(
        _Handler(),
        chunks=big,
        content_length=["3000"],
        path="/upload/resume",
        prefix_limits=prefixes,
    )
    elsewhere = await _call(
        _Handler(), chunks=big, content_length=["3000"], path="/other", prefix_limits=prefixes
    )

    assert allowed.status == 200
    assert elsewhere.status == 413


async def test_the_limit_is_read_on_every_request(monkeypatch: pytest.MonkeyPatch) -> None:
    first = await _call(_Handler(), chunks=[b"x" * 500], content_length=["500"])
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "100")
    second = await _call(_Handler(), chunks=[b"x" * 500], content_length=["500"])

    assert (first.status, second.status) == (200, 413)


# -- through the real app ------------------------------------------------------------------

_ORIGIN = "https://app.example.com"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    app.dependency_overrides[require_user_id] = lambda: "11111111-1111-1111-1111-111111111111"
    try:
        with TestClient(app, raise_server_exceptions=False) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


@contextmanager
def _echo_route() -> Iterator[list[int]]:
    """A POST route that reads its body and records how many bytes it was given."""
    seen: list[int] = []

    async def echo(request: Request) -> dict[str, int]:
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            seen.append(total)
        return {"bytes": total}

    app.add_api_route("/__test_body_echo", echo, methods=["POST"])
    try:
        yield seen
    finally:
        app.router.routes[:] = [
            r for r in app.router.routes if getattr(r, "path", None) != "/__test_body_echo"
        ]


def test_a_declared_oversize_body_gets_a_413_that_carries_cors_and_the_request_id(
    client: TestClient,
) -> None:
    with _echo_route() as seen:
        response = client.post(
            "/__test_body_echo",
            content=b"x" * (_CAP + 1),
            headers={"Origin": _ORIGIN, "X-Request-ID": "req-too-big-1"},
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["x-request-id"] == "req-too-big-1"
    assert seen == []


def test_a_streamed_oversize_body_gets_the_same_413_through_the_whole_stack(
    client: TestClient,
) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(10):
            yield b"x" * 300

    with _echo_route() as seen:
        response = client.post(
            "/__test_body_echo",
            content=chunks(),  # no Content-Length: sent chunked
            headers={"Origin": _ORIGIN, "X-Request-ID": "req-too-big-2"},
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["x-request-id"] == "req-too-big-2"
    assert not seen or max(seen) <= _CAP  # the handler never saw more than the cap


def test_a_body_under_the_cap_reaches_the_route(client: TestClient) -> None:
    with _echo_route():
        response = client.post("/__test_body_echo", content=b"x" * 900)

    assert response.status_code == 200
    assert response.json() == {"bytes": 900}


def test_a_real_json_route_still_validates_and_answers_normally(client: TestClient) -> None:
    response = client.post("/link/code", json={"channel": "carrier-pigeon"})

    assert response.status_code == 422 or response.status_code == 404  # reached the route, not 413
    assert response.status_code != 413


def test_a_malformed_content_length_through_the_real_app_is_not_a_500(client: TestClient) -> None:
    with _echo_route():
        response = client.post(
            "/__test_body_echo", content=b"hi", headers={"Content-Length": "banana"}
        )

    # The HTTP client library itself may refuse to send it; if it is sent, it is a 4xx.
    assert response.status_code < 500


def test_the_middleware_is_inside_cors_and_the_request_id_middleware() -> None:
    names = [cast(Any, m.cls).__name__ for m in app.user_middleware]
    assert names.index("CORSMiddleware") < names.index("BaseHTTPMiddleware")
    assert names.index("BaseHTTPMiddleware") < names.index("BodyLimitMiddleware")
