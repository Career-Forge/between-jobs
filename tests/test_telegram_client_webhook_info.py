"""`TelegramClient.get_webhook_info` (the daily webhook probe's question) on a fake transport.

The bot token is part of the Bot API URL path, so the point of most of these tests is what the
call must never do: let the token reach an exception's text, its chain, a log record, or an
error report -- even when the library underneath puts the whole URL in its own message."""

from __future__ import annotations

import json
import logging
import traceback
from collections.abc import Callable

import httpx
import pytest
from rendered_logs import rendered

from between_jobs.api.logging_setup import format_exception_safely
from between_jobs.api.telegram_client import TelegramApiError, TelegramClient

# Assembled at runtime so no literal in this public repo looks like a real bot token.
TOKEN_SECRET_PART = "FAKEfakeFAKEfake" + "0123456789abcdefXY"
BOT_TOKEN = "123456789:" + TOKEN_SECRET_PART
URL = f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo"

INFO = {
    "url": "https://api.example.test/telegram/webhook",
    "has_custom_certificate": False,
    "pending_update_count": 2,
    "max_connections": 40,
    "allowed_updates": ["message", "callback_query"],
}


def client_for(handler: Callable[[httpx.Request], httpx.Response]) -> TelegramClient:
    return TelegramClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), BOT_TOKEN)


def answering(status: int, body: object = None, *, content: bytes | None = None) -> TelegramClient:
    def handler(_request: httpx.Request) -> httpx.Response:
        if content is not None:
            return httpx.Response(status, content=content)
        return httpx.Response(status, json=body)

    return client_for(handler)


def raising(error: Callable[[httpx.Request], Exception]) -> TelegramClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error(request)

    return client_for(handler)


def everything_said_about(error: BaseException) -> str:
    """Every way the exception could be turned into text, including its whole chain."""
    parts = [
        str(error),
        repr(error),
        repr(error.args),
        "".join(traceback.format_exception(error)),
        format_exception_safely(error),
        repr(vars(error)),
    ]
    link: BaseException | None = error
    while link is not None:
        parts.append(f"{link!r} {link.args!r}")
        link = link.__cause__ or link.__context__
    return "\n".join(parts)


# --- the call --------------------------------------------------------------------------------


async def test_it_gets_the_method_url_with_no_body_and_returns_the_result_object() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": INFO})

    result = await client_for(handler).get_webhook_info()

    assert result == INFO
    [request] = seen
    assert request.method == "GET"
    assert str(request.url) == URL
    assert request.content == b""


async def test_the_result_is_returned_unchecked_whatever_it_holds() -> None:
    """Deciding what it means is the probe's job; this method only gets the object."""
    odd = {"url": 5, "pending_update_count": "many", "something_new": [1]}
    assert await answering(200, {"ok": True, "result": odd}).get_webhook_info() == odd
    assert await answering(200, {"ok": True, "result": {}}).get_webhook_info() == {}


async def test_it_asks_for_a_bounded_time() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": INFO})

    await client_for(handler).get_webhook_info()
    assert seen[0].extensions["timeout"] == {
        "connect": 10.0,
        "read": 10.0,
        "write": 10.0,
        "pool": 10.0,
    }


# --- every failure is a TelegramApiError with a code and nothing else ---------------------------


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (401, {"ok": False, "error_code": 401, "description": "Unauthorized"}),
        (404, {"ok": False, "error_code": 404, "description": "Not Found"}),
        (429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 3}}),
        (500, {"ok": False, "error_code": 500, "description": "Internal Server Error"}),
        (502, None),
        (301, None),
    ],
)
async def test_a_non_2xx_answer_is_refused_with_its_status(status: int, body: object) -> None:
    with pytest.raises(TelegramApiError) as raised:
        await answering(status, body).get_webhook_info()
    assert raised.value.code == "refused"
    assert raised.value.status_code == status
    assert str(raised.value) == f"the Telegram Bot API call failed (refused, HTTP {status})"


async def test_telegrams_own_description_text_is_never_put_in_the_error() -> None:
    body = {"ok": False, "error_code": 401, "description": f"Unauthorized {BOT_TOKEN} bad token"}
    with pytest.raises(TelegramApiError) as raised:
        await answering(401, body).get_webhook_info()
    assert "Unauthorized" not in everything_said_about(raised.value)


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"not json at all",
        b"<html>Bad gateway</html>",
        b"\xff\xfe\x00 binary",
        b"[]",
        b"null",
        b'"ok"',
        b"42",
        b'{"ok": false, "description": "nope"}',
        b'{"ok": "true", "result": {}}',
        b'{"ok": 1, "result": {}}',
        b'{"ok": true}',
        b'{"ok": true, "result": null}',
        b'{"ok": true, "result": []}',
        b'{"ok": true, "result": "x"}',
        b'{"result": {}}',
        b"[" * 100_000 + b"]" * 100_000,  # nested past what the JSON reader will take
    ],
    ids=lambda c: repr(c[:30]),
)
async def test_a_2xx_answer_that_is_not_ok_true_with_a_result_object_is_malformed(
    content: bytes,
) -> None:
    with pytest.raises(TelegramApiError) as raised:
        await answering(200, content=content).get_webhook_info()
    assert raised.value.code == "malformed"
    assert raised.value.status_code == 200


@pytest.mark.parametrize(
    ("make_error", "code"),
    [
        (lambda r: httpx.ConnectTimeout("timed out", request=r), "timeout"),
        (lambda r: httpx.ReadTimeout(f"timed out reading {r.url}", request=r), "timeout"),
        (lambda r: httpx.PoolTimeout(f"no connection for {r.url}", request=r), "timeout"),
        (
            lambda r: httpx.ConnectError(f"All connection attempts failed for {r.url}", request=r),
            "transport",
        ),
        (lambda r: httpx.ReadError(f"connection reset: {r.url}", request=r), "transport"),
        (lambda r: httpx.ProxyError(f"proxy refused {r.url}", request=r), "transport"),
        (lambda r: httpx.RemoteProtocolError(f"bad frame from {r.url}", request=r), "transport"),
        (lambda r: httpx.UnsupportedProtocol(f"unsupported: {r.url}", request=r), "transport"),
        (lambda r: httpx.InvalidURL(f"Invalid URL {r.url}"), "transport"),
    ],
)
async def test_a_transport_failure_is_a_code_that_holds_nothing_the_library_said(
    make_error: Callable[[httpx.Request], Exception], code: str
) -> None:
    with pytest.raises(TelegramApiError) as raised:
        await raising(make_error).get_webhook_info()

    error = raised.value
    assert error.code == code and error.status_code is None
    assert str(error) == f"the Telegram Bot API call failed ({code})"
    said = everything_said_about(error)
    assert (
        BOT_TOKEN not in said and TOKEN_SECRET_PART not in said and "api.telegram.org" not in said
    )
    # The library's exception is not kept anywhere: not as the cause, not as the context.
    assert error.__cause__ is None and error.__context__ is None


async def test_something_that_is_not_an_http_error_is_not_dressed_up() -> None:
    """Only what httpx raises for a request is turned into a code. Anything else is a bug and
    propagates as it is -- to the probe, which logs its type and nothing more."""
    with pytest.raises(RuntimeError):
        await raising(lambda r: RuntimeError(f"bug near {r.url}")).get_webhook_info()


async def test_a_client_that_was_closed_is_a_bug_not_a_telegram_outage() -> None:
    telegram = client_for(lambda _r: httpx.Response(200, json={"ok": True, "result": INFO}))
    await telegram._http.aclose()
    with pytest.raises(RuntimeError):
        await telegram.get_webhook_info()


# --- the token never reaches a log line -----------------------------------------------------------


@pytest.mark.parametrize("outcome", ["ok", "refused", "transport", "malformed"])
async def test_no_log_line_the_call_causes_holds_the_token_even_with_httpx_logging_on(
    outcome: str, caplog: pytest.LogCaptureFixture
) -> None:
    """httpx logs every request URL at INFO, and `configure_logging` keeps it at WARNING. Here
    it is turned ON, and the records are written through the production formatter: the token
    still must not be in what comes out (the redactor knows a bot token's shape). The failure a
    caller would log with its traceback is added to the same output."""
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="httpx")
    telegram = {
        "ok": answering(200, {"ok": True, "result": INFO}),
        "refused": answering(401, {"ok": False}),
        "transport": raising(lambda r: httpx.ConnectError(f"cannot reach {r.url}", request=r)),
        "malformed": answering(200, content=b"garbage"),
    }[outcome]

    failure: TelegramApiError | None = None
    try:
        await telegram.get_webhook_info()
    except TelegramApiError as error:
        failure = error
    records = list(caplog.records)
    if failure is not None:
        records.append(
            logging.LogRecord(
                "caller", logging.ERROR, __file__, 1, "failed", None, (type(failure), failure, None)
            )
        )

    if outcome != "transport":  # a request that never got an answer has no INFO line to speak of
        assert any(r.name == "httpx" for r in caplog.records)  # the line is there to be tested
    written = rendered(records)
    assert written and BOT_TOKEN not in written and TOKEN_SECRET_PART not in written


async def test_the_clients_own_repr_has_no_token() -> None:
    telegram = answering(200, {"ok": True, "result": INFO})
    assert BOT_TOKEN not in repr(telegram) and BOT_TOKEN not in str(telegram)


def test_the_error_serialises_for_logs_with_its_code_and_status_only() -> None:
    error = TelegramApiError("refused", status_code=401)
    assert json.dumps({"code": error.code, "status": error.status_code}) == (
        '{"code": "refused", "status": 401}'
    )
    assert "code=refused" in format_exception_safely(error)
    assert "status=401" in format_exception_safely(error)
