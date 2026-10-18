"""The guarded outbound call (api/provider_http.py): where a request may go, what it may bring
back, and how long it may take."""

# ruff: noqa: RUF001  (lookalike letters and spaces are the point of some cases below)

from __future__ import annotations

import asyncio
import gzip
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from provider_fakes import http_client, json_response

from between_jobs.api import provider_http
from between_jobs.api.errors import ApiError
from between_jobs.api.provider_http import (
    MAX_RESPONSE_BYTES,
    UpstreamFailure,
    assert_safe_destination,
    check_destination_shape,
    fetch_json,
)

KEY = "sk-test-0123456789abcdef0123456789abcdef"
URL = "https://api.openai.com/v1/models"


# -- the shape of a destination --------------------------------------------------------------


def test_a_plain_https_dns_name_is_a_destination() -> None:
    assert check_destination_shape(URL) == "api.openai.com"
    assert check_destination_shape("https://API.OpenAI.com:443/v1") == "api.openai.com"


@pytest.mark.parametrize(
    "url",
    [
        "http://api.openai.com/v1/models",
        "ftp://api.openai.com/",
        "https:///v1/models",
        "https://localhost/v1",
        "https://localhost./v1",
        "https://printer.local/v1",
        "https://box.localhost/v1",
        "https://db.internal/v1",
        "https://nas.lan/v1",
        "https://router.home.arpa/v1",
        "https://intranet/v1",  # a bare name has no dot
        "https://api.openai.com:8443/v1",
        "https://api.openai.com./v1",
        "https://user@api.openai.com/v1",
        "https://user:pw@api.openai.com/v1",
        "https://127.0.0.1/v1",
        "https://127.0.0.1:443/v1",
        "https://10.1.2.3/v1",
        "https://172.16.0.1/v1",
        "https://192.168.0.1/v1",
        "https://169.254.169.254/latest/meta-data",
        "https://100.64.0.1/v1",
        "https://0.0.0.0/v1",
        "https://8.8.8.8/v1",  # a public IP literal is refused too: names only
        "https://[::1]/v1",
        "https://[::ffff:10.0.0.1]/v1",
        "https://[fe80::1]/v1",
        "https://[2606:4700::1111]/v1",
        "https://аpi.openai.com/v1",
        "not a url",
        "",
    ],
)
def test_anything_else_is_not_a_destination(url: str) -> None:
    with pytest.raises(ApiError) as caught:
        check_destination_shape(url)

    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.details["reason"] == "base_url_refused"


# -- what a name resolves to -------------------------------------------------------------------


def _resolving_to(monkeypatch: pytest.MonkeyPatch, *addresses: str) -> None:
    async def resolve(_host: str) -> list[str]:
        return list(addresses)

    monkeypatch.setattr(provider_http, "_resolve", resolve)


async def test_a_name_that_resolves_to_public_addresses_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _resolving_to(monkeypatch, "104.18.7.192", "2606:4700::6812:7c0")

    await assert_safe_destination(URL)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.255.255.254",
        "10.0.0.5",
        "172.20.1.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "192.0.2.1",
        "198.18.0.1",
        "224.0.0.1",
        "255.255.255.255",
        "::1",
        "::",
        "fe80::1",
        "fe80::1%en0",
        "fc00::1",
        "fd12:3456::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "::ffff:169.254.169.254",
    ],
)
async def test_a_name_that_resolves_into_a_private_range_is_refused(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    _resolving_to(monkeypatch, address)

    with pytest.raises(ApiError) as caught:
        await assert_safe_destination(URL)

    assert caught.value.code == "INVALID_INPUT"


async def test_one_private_address_among_public_ones_is_enough_to_refuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _resolving_to(monkeypatch, "104.18.7.192", "10.0.0.5")

    with pytest.raises(ApiError):
        await assert_safe_destination(URL)


async def test_a_name_that_does_not_resolve_is_unreachable_not_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing(_host: str) -> list[str]:
        raise OSError("name or service not known")

    monkeypatch.setattr(provider_http, "_resolve", failing)
    with pytest.raises(UpstreamFailure) as caught:
        await assert_safe_destination(URL)
    assert caught.value.reason == "unreachable"

    _resolving_to(monkeypatch)  # an empty answer
    with pytest.raises(UpstreamFailure):
        await assert_safe_destination(URL)

    _resolving_to(monkeypatch, "not-an-address")
    with pytest.raises(UpstreamFailure):
        await assert_safe_destination(URL)


async def test_a_refused_destination_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[httpx.Request] = []
    _resolving_to(monkeypatch, "169.254.169.254")

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return json_response(200, {})

    async with http_client(handler) as http:
        with pytest.raises(ApiError):
            await fetch_json(http, "GET", URL, headers={"Authorization": f"Bearer {KEY}"})

    assert sent == []


# -- the call itself -------------------------------------------------------------------------------


async def test_a_good_answer_is_parsed_and_the_key_travels_only_in_the_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return json_response(200, {"data": [1]}, headers={"retry-after": "3", "x-secret": "no"})

    async with http_client(handler) as http:
        answer = await fetch_json(
            http, "GET", URL, headers={"Authorization": f"Bearer {KEY}"}, params={"limit": "1"}
        )

    assert (answer.status, answer.body) == (200, {"data": [1]})
    assert dict(answer.headers) == {"retry-after": "3"}  # only what is asked to be kept
    [request] = seen
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in str(request.url) and "limit=1" in str(request.url)


async def test_a_body_that_is_not_json_is_none_not_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>captive portal</html>")

    async with http_client(handler) as http:
        answer = await fetch_json(http, "GET", URL, headers={})

    assert (answer.status, answer.body) == (200, None)


async def test_an_error_status_is_returned_for_the_caller_to_judge() -> None:
    async with http_client(lambda r: json_response(401, {"error": {"message": KEY}})) as http:
        answer = await fetch_json(http, "GET", URL, headers={})

    assert answer.status == 401


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_a_redirect_is_never_followed(status: int) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "api.openai.com":
            return httpx.Response(status, headers={"location": "https://evil.example/steal"})
        return json_response(200, {"leaked": True})

    async with http_client(handler) as http:
        answer = await fetch_json(
            http, "POST", URL, headers={"Authorization": f"Bearer {KEY}"}, json_body={"a": 1}
        )

    assert seen == [URL]  # one request, to the host asked for
    assert answer.status == status
    assert answer.body is None


async def test_an_answer_that_declares_itself_too_large_is_not_read() -> None:
    async def body() -> AsyncIterator[bytes]:
        raise AssertionError("the body of an answer that declared itself too large was read")
        yield b""  # pragma: no cover

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-length": str(MAX_RESPONSE_BYTES + 1)}, content=body()
        )

    async with http_client(handler) as http:
        with pytest.raises(UpstreamFailure) as caught:
            await fetch_json(http, "GET", URL, headers={})

    assert caught.value.reason == "response_too_large"


async def test_an_answer_that_never_stops_is_cut_at_the_limit() -> None:
    produced = 0

    async def endless() -> AsyncIterator[bytes]:
        nonlocal produced
        while True:
            produced += 1
            yield b"x" * 65536

    async with http_client(lambda r: httpx.Response(200, content=endless())) as http:
        with pytest.raises(UpstreamFailure) as caught:
            await fetch_json(http, "GET", URL, headers={}, max_bytes=1024 * 1024)

    assert caught.value.reason == "response_too_large"
    assert produced <= 1024 * 1024 // 65536 + 2  # stopped reading right after the limit


async def test_a_small_compressed_answer_that_expands_past_the_limit_is_cut() -> None:
    bomb = gzip.compress(b"0" * (MAX_RESPONSE_BYTES * 4))
    assert len(bomb) < MAX_RESPONSE_BYTES  # small on the wire

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=bomb, headers={"content-encoding": "gzip"})

    async with http_client(handler) as http:
        with pytest.raises(UpstreamFailure) as caught:
            await fetch_json(http, "GET", URL, headers={})

    assert caught.value.reason == "response_too_large"


async def test_a_slow_answer_is_cut_by_one_limit_for_the_whole_call() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)
        return json_response(200, {})

    async with http_client(handler) as http:
        with pytest.raises(UpstreamFailure) as caught:
            await fetch_json(http, "GET", URL, headers={}, timeout_seconds=0.05)

    assert caught.value.reason == "timeout"


@pytest.mark.parametrize(
    ("raised", "reason"),
    [
        (httpx.ConnectError("refused"), "unreachable"),
        (httpx.ReadError("reset"), "unreachable"),
        (httpx.RemoteProtocolError("bad"), "unreachable"),
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ConnectTimeout("slow"), "timeout"),
    ],
)
async def test_a_transport_failure_is_a_reason_not_the_exception(
    raised: Exception, reason: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise raised

    async with http_client(handler) as http:
        with pytest.raises(UpstreamFailure) as caught:
            await fetch_json(http, "GET", URL, headers={"Authorization": f"Bearer {KEY}"})

    assert caught.value.reason == reason
    assert KEY not in str(caught.value) and KEY not in repr(caught.value)


async def test_the_post_body_is_sent_as_json() -> None:
    seen: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return json_response(200, {})

    async with http_client(handler) as http:
        await fetch_json(http, "POST", URL, headers={}, json_body={"code": "c"})

    assert seen[0].content == b'{"code":"c"}'
