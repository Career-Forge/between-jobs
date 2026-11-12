"""CORS, as the deployed web app needs it (launch plan P2.3): the bundle is
served from one origin and calls the API on another, so every response a
browser is meant to read -- including the error envelope for a failure nothing
handled -- must carry the CORS headers.

Unset, any origin may read a response (a self-hosted API keeps working, and the boot
says so). With CORS_ALLOWED_ORIGINS set only the listed origins get the headers, on
every kind of response, and a page on any other site is left without them, which is
what stops its script from reading the answer."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from between_jobs.api.app import ConfiguredCORSMiddleware, app
from between_jobs.api.env import ConfigurationError

_ORIGIN = "https://app.example.com"
_OTHER_ORIGIN = "https://evil.example"
_EXTENSION = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
_BOOM = "/__test_unhandled_error"
_APP_LOGGER = "between_jobs.api.app"


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)


@contextmanager
def _client_with_a_route_that_raises() -> Iterator[TestClient]:
    async def boom() -> None:
        raise RuntimeError("nothing handles this")

    app.add_api_route(_BOOM, boom)
    try:
        # The real server turns an unhandled error into a 500 response; the test
        # client would otherwise re-raise it.
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) != _BOOM]


def test_an_unhandled_error_still_carries_the_cors_headers() -> None:
    """request_id_middleware answers an unhandled error with its own 500. If
    CORS sat inside it, that response would lack the headers and a browser
    would hand the page a network error instead of the envelope."""
    with _client_with_a_route_that_raises() as client:
        response = client.get(_BOOM, headers={"Origin": _ORIGIN})

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.headers["access-control-allow-origin"] == "*"
    assert "x-request-id" in response.headers
    # ...and the page is allowed to read that id, to quote in a bug report.
    assert "x-request-id" in response.headers["access-control-expose-headers"].lower()


def test_a_handled_error_carries_the_cors_headers() -> None:
    with TestClient(app) as client:
        response = client.get("/no-such-route", headers={"Origin": _ORIGIN})

    assert response.status_code == 404
    assert response.headers["access-control-allow-origin"] == "*"


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE", "PUT"])
def test_the_preflight_the_web_app_sends_is_allowed(method: str) -> None:
    """apiFetch sends Authorization and Content-Type on every call, which makes
    the browser preflight it; the answer has to name both headers and the
    method (Access-Control-Allow-Headers: * does not cover Authorization, so
    it matters that the middleware echoes the requested list)."""
    with TestClient(app) as client:
        response = client.options(
            "/link/code",
            headers={
                "Origin": _ORIGIN,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed_headers and "content-type" in allowed_headers
    assert method in response.headers["access-control-allow-methods"]


# -- CORS_ALLOWED_ORIGINS set: only the listed origins -------------------------------------------


@pytest.fixture
def _locked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", f"{_ORIGIN},{_EXTENSION}")


@pytest.mark.usefixtures("_locked")
@pytest.mark.parametrize("origin", [_ORIGIN, _EXTENSION])
def test_a_listed_origin_gets_its_own_address_back_on_every_kind_of_response(origin: str) -> None:
    with _client_with_a_route_that_raises() as client:
        handled = client.get("/no-such-route", headers={"Origin": origin})
        unhandled = client.get(_BOOM, headers={"Origin": origin})

    assert handled.status_code == 404
    assert unhandled.status_code == 500
    for response in (handled, unhandled):
        assert response.headers["access-control-allow-origin"] == origin
        assert "origin" in response.headers["vary"].lower()
        assert "x-request-id" in response.headers["access-control-expose-headers"].lower()
    assert unhandled.json()["error"]["code"] == "INTERNAL_ERROR"


@pytest.mark.usefixtures("_locked")
@pytest.mark.parametrize(
    "origin",
    [
        _OTHER_ORIGIN,
        "https://app.example.com.evil.example",  # a listed host as the start of another
        "http://app.example.com",  # the same host over http
        "https://app.example.com:8443",  # the same host on another port
        "https://sub.app.example.com",
        "null",  # a sandboxed frame or a file: page
        "chrome-extension://ponmlkjihgfedcbaponmlkjihgfedcba",  # another extension
        "*",
    ],
)
def test_any_other_origin_gets_no_cors_headers_so_its_page_cannot_read_the_answer(
    origin: str,
) -> None:
    with _client_with_a_route_that_raises() as client:
        handled = client.get("/no-such-route", headers={"Origin": origin})
        unhandled = client.get(_BOOM, headers={"Origin": origin})

    assert handled.status_code == 404
    assert unhandled.status_code == 500
    for response in (handled, unhandled):
        # Without Access-Control-Allow-Origin a browser refuses the page its answer, whatever else
        # is set (Starlette adds Access-Control-Expose-Headers regardless, which grants nothing).
        assert "access-control-allow-origin" not in response.headers
        assert "access-control-allow-credentials" not in response.headers


@pytest.mark.usefixtures("_locked")
def test_a_request_with_no_origin_is_untouched() -> None:
    """A server, a webhook or curl sends no Origin: CORS is about browsers, so nothing changes."""
    with TestClient(app) as client:
        response = client.get("/no-such-route")

    assert response.status_code == 404
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.usefixtures("_locked")
@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE", "PUT"])
def test_the_preflight_from_a_listed_origin_is_allowed(method: str) -> None:
    with TestClient(app) as client:
        response = client.options(
            "/link/code",
            headers={
                "Origin": _ORIGIN,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == _ORIGIN
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed_headers and "content-type" in allowed_headers
    assert method in response.headers["access-control-allow-methods"]


@pytest.mark.usefixtures("_locked")
@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE", "PUT"])
def test_the_preflight_from_any_other_origin_is_refused(method: str) -> None:
    with TestClient(app) as client:
        response = client.options(
            "/link/code",
            headers={
                "Origin": _OTHER_ORIGIN,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.usefixtures("_locked")
def test_credentials_are_never_allowed() -> None:
    with TestClient(app) as client:
        response = client.get("/no-such-route", headers={"Origin": _ORIGIN})

    assert "access-control-allow-credentials" not in response.headers


def test_the_list_is_read_when_the_request_arrives(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not when the module is imported: a boot check and a test both need to control it."""
    with TestClient(app) as client:
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", _ORIGIN)
        listed = client.get("/no-such-route", headers={"Origin": _ORIGIN})
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", _OTHER_ORIGIN)
        now_unlisted = client.get("/no-such-route", headers={"Origin": _ORIGIN})
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS")
        open_again = client.get("/no-such-route", headers={"Origin": _ORIGIN})

    assert listed.headers["access-control-allow-origin"] == _ORIGIN
    assert "access-control-allow-origin" not in now_unlisted.headers
    assert open_again.headers["access-control-allow-origin"] == "*"


# -- the boot says which it is -------------------------------------------------------------------


def _open_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and "CORS_ALLOWED_ORIGINS" in r.getMessage()
    ]


def test_booting_with_no_list_says_that_any_website_can_read_the_responses(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=_APP_LOGGER), TestClient(app):
        pass

    [message] = _open_warnings(caplog)
    assert "any website" in message


@pytest.mark.usefixtures("_locked")
def test_booting_with_a_list_says_nothing(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=_APP_LOGGER), TestClient(app):
        pass

    assert _open_warnings(caplog) == []


# -- the status codes a page reads ---------------------------------------------------------------


@pytest.mark.usefixtures("_locked")
@pytest.mark.parametrize(
    ("method", "path", "body", "status"),
    [
        ("GET", "/extension/lookup", None, 401),  # no token
        ("POST", "/link/code", {"channel": "carrier-pigeon"}, 401),
        ("PUT", "/health", None, 405),  # a method the route does not have
        ("HEAD", "/health", None, 200),
        ("GET", "/no-such-route", None, 404),
    ],
)
def test_a_listed_origin_reads_every_status_and_any_other_origin_reads_none(
    method: str, path: str, body: dict[str, str] | None, status: int
) -> None:
    with TestClient(app) as client:
        listed = client.request(method, path, json=body, headers={"Origin": _ORIGIN})
        other = client.request(method, path, json=body, headers={"Origin": _OTHER_ORIGIN})

    assert listed.status_code == other.status_code == status
    assert listed.headers["access-control-allow-origin"] == _ORIGIN
    assert "access-control-allow-origin" not in other.headers


# -- a bad list stops the boot, in the server and not only in a test client ----------------------


async def _drive_the_lifespan() -> tuple[list[Message], BaseException | None]:
    """What a server does: one lifespan scope, startup first. Returns the messages the app sent
    and what it raised (Starlette sends "startup failed" and then re-raises)."""
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        sent.append(message)

    try:
        await app({"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}, receive, send)
    except BaseException as error:
        return sent, error
    return sent, None


async def test_a_bad_list_answers_the_lifespan_with_startup_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lifespan scope goes through the middleware first. If the middleware raised for a bad
    list before the lifespan ran, no startup-failed message would be sent: uvicorn would call the
    lifespan unsupported, log "startup complete" and keep serving 500s. A test client re-raises
    whatever the app raises, so only the raw messages tell the two apart."""
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://user:hunter2@between-jobs.tech")
    created: list[str] = []

    async def create_supabase_client_must_not_run() -> tuple[object, str]:
        created.append("created")
        raise AssertionError("a refused boot must not go on to open the database client")

    monkeypatch.setattr(
        "between_jobs.api.app.create_supabase_client", create_supabase_client_must_not_run
    )

    sent, error = await _drive_the_lifespan()
    assert created == []

    assert [message["type"] for message in sent] == ["lifespan.startup.failed"]
    assert isinstance(error, ConfigurationError)
    assert "hunter2" not in str(sent) and "hunter2" not in str(error)


async def test_a_good_list_lets_the_lifespan_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", _ORIGIN)

    sent, error = await _drive_the_lifespan()

    assert error is None
    assert [message["type"] for message in sent][:1] == ["lifespan.startup.complete"]


@pytest.mark.parametrize("scope_type", ["lifespan", "websocket"])
async def test_the_middleware_hands_a_scope_that_is_not_http_on_without_reading_the_setting(
    monkeypatch: pytest.MonkeyPatch, scope_type: str
) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "not an origin")
    seen: list[str] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        raise AssertionError("nothing is sent")

    await ConfiguredCORSMiddleware(inner)({"type": scope_type}, receive, send)

    assert seen == [scope_type]


async def test_an_http_request_with_a_bad_list_is_still_refused_not_answered_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the environment changed after boot (it cannot in a running server), a request is not
    answered with the headers of a list nobody checked."""
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "not an origin")

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        raise AssertionError("the request must not reach the app")

    async def receive() -> Message:
        return {"type": "http.request"}

    async def send(message: Message) -> None:
        raise AssertionError("nothing is sent")

    with pytest.raises(ConfigurationError):
        await ConfiguredCORSMiddleware(inner)(
            {"type": "http", "headers": [], "method": "GET", "path": "/"}, receive, send
        )
