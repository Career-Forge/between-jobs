"""The Discord REST client (api/discord_client.py): where each request goes and with which
credential, that a token can never reach a log or an exception, that a retry is bounded, and that a
file is only ever downloaded from Discord's own CDN. Against the recording fake in
tests/discord_fakes.py -- synthetic, see its docstring."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from discord_fakes import (
    APPLICATION_ID,
    BOT_TOKEN,
    FakeDiscord,
    SleepRecorder,
    discord_client,
    fresh_token,
    http_for,
)

from between_jobs.api.discord_client import (
    ATTACHMENT_HOSTS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_TOTAL_WAIT_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    AttachmentRefused,
    DiscordApiError,
    DiscordClient,
    DiscordError,
    DiscordRateLimited,
    DiscordTokenExpired,
    DiscordTransportError,
    DownloadTooLarge,
    attachment_url_problem,
)
from between_jobs.api.logging_setup import JsonFormatter, format_exception_safely

# -- where requests go, and with what ------------------------------------------------------------


async def test_interaction_calls_go_to_the_webhook_with_the_token_in_the_path() -> None:
    fake = FakeDiscord()
    client = discord_client(fake)
    token = fresh_token()

    await client.edit_interaction_message(token, "@original", {"content": "a"})
    await client.create_followup(token, {"content": "b"})
    await client.edit_interaction_message(token, "123456789", {"content": "c"})

    edit, followup, edit_followup = fake.requests
    base = f"/webhooks/{APPLICATION_ID}/{token}"
    assert (edit.method, edit.path) == ("PATCH", f"{base}/messages/@original")
    assert (followup.method, followup.path) == ("POST", base)
    assert (edit_followup.method, edit_followup.path) == ("PATCH", f"{base}/messages/123456789")
    for request in fake.requests:
        assert "authorization" not in request.headers  # the token is the credential
        assert request.headers["user-agent"].startswith("DiscordBot (")
        assert request.host == "discord.com"
    assert edit.json_body == {"content": "a"}


async def test_a_message_id_comes_back_when_discord_gives_one() -> None:
    client = discord_client(FakeDiscord())
    assert await client.create_followup(fresh_token(), {"content": "x"}) is not None


async def test_the_bot_calls_carry_the_bot_token_in_a_header_and_never_in_a_url() -> None:
    fake = FakeDiscord()
    client = discord_client(fake)

    channel = await client.open_dm("777000111222333444")
    await client.create_channel_message(channel, {"content": "hi"})
    await client.edit_channel_message(channel, "1400000000000000009", {"content": "edited"})

    assert channel == fake.dm_channel
    opened, created, edited = fake.requests
    assert (opened.method, opened.path, opened.json_body) == (
        "POST",
        "/users/@me/channels",
        {"recipient_id": "777000111222333444"},
    )
    assert (created.method, created.path) == ("POST", f"/channels/{channel}/messages")
    assert (edited.method, edited.path) == (
        "PATCH",
        f"/channels/{channel}/messages/1400000000000000009",
    )
    for request in fake.requests:
        assert request.headers["authorization"] == f"Bot {BOT_TOKEN}"
        assert BOT_TOKEN not in request.path


async def test_a_bot_call_without_a_bot_token_is_refused_before_anything_is_sent() -> None:
    fake = FakeDiscord()
    client = discord_client(fake, bot_token=None)

    with pytest.raises(DiscordError):
        await client.open_dm("777000111222333444")
    with pytest.raises(DiscordError):
        await client.create_channel_message("1500000000000000001", {"content": "x"})

    assert fake.requests == []
    assert not client.has_bot_token


async def test_the_api_version_is_pinned() -> None:
    fake = FakeDiscord()
    await discord_client(fake).create_followup(fresh_token(), {"content": "x"})
    # FakeDiscord asserts the /api/v10 prefix of every request it answers.
    assert len(fake.requests) == 1


async def test_a_file_goes_as_multipart_with_the_json_in_payload_json() -> None:
    fake = FakeDiscord()
    client = discord_client(fake)
    body = {"content": "caption", "attachments": [{"id": 0, "filename": "resume.pdf"}]}

    await client.create_followup(fresh_token(), body, files=[("resume.pdf", b"%PDF-bytes")])
    await client.edit_interaction_message(
        fresh_token(), "@original", body, files=[("resume.pdf", b"%PDF-more")]
    )

    first, second = fake.requests
    assert first.json_body == body and first.files == {"resume.pdf": b"%PDF-bytes"}
    assert second.json_body == body and second.files == {"resume.pdf": b"%PDF-more"}


@pytest.mark.parametrize("token", ["", "a/b", "a b", "..", "x?y=1", "a#b", "t\nu", "é" * 30])
async def test_a_token_that_is_not_one_path_segment_never_makes_a_request(token: str) -> None:
    fake = FakeDiscord()
    with pytest.raises(DiscordError):
        await discord_client(fake).create_followup(token, {"content": "x"})
    assert fake.requests == []


@pytest.mark.parametrize("message_id", ["", "@orig", "12/34", "abc", "1 2", "../x"])
async def test_a_message_id_that_is_not_a_snowflake_never_makes_a_request(message_id: str) -> None:
    fake = FakeDiscord()
    with pytest.raises(DiscordError):
        await discord_client(fake).edit_interaction_message(fresh_token(), message_id, {})
    assert fake.requests == []


def test_the_client_refuses_a_bad_application_id_and_never_shows_its_token() -> None:
    with pytest.raises(ValueError):
        DiscordClient(httpx.AsyncClient(), application_id="not-an-id")
    client = discord_client(FakeDiscord())
    # The default repr of an object shows no attributes at all, so the second check alone would
    # pass if the custom one were deleted; the first says it is the custom one.
    assert repr(client) == f"DiscordClient(application_id='{APPLICATION_ID}', bot=True)"
    assert BOT_TOKEN not in repr(client)


def test_a_client_that_could_make_no_attempt_is_refused_at_construction() -> None:
    for attempts in (0, -1):
        with pytest.raises(ValueError):
            DiscordClient(httpx.AsyncClient(), application_id=APPLICATION_ID, max_attempts=attempts)


# -- a token never reaches a log or an exception ------------------------------------------------


async def test_httpx_request_logging_cannot_carry_the_interaction_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """httpx writes every request URL at INFO; the app keeps it at WARNING, and the client's own
    filter rewrites the token if that is ever changed."""
    token = fresh_token()
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("httpx").setLevel(logging.INFO)  # as a changed log level would
        try:
            await discord_client(FakeDiscord()).create_followup(token, {"content": "x"})
        finally:
            logging.getLogger("httpx").setLevel(logging.WARNING)
    assert [r for r in caplog.records if r.name == "httpx"], "httpx logged nothing: no proof"
    for record in caplog.records:
        assert token not in record.getMessage()
        assert token not in str(record.args)


@pytest.mark.parametrize(
    ("status", "code", "expired"),
    [
        (404, 10015, True),  # Unknown Webhook
        (404, 10062, True),  # Unknown Interaction
        (401, 50027, True),  # Invalid Webhook Token
        (404, None, True),
        (401, None, True),
        (404, 10008, False),  # Unknown Message: not about the token
        (403, 50013, False),
        (400, 50035, False),
        (500, None, False),
    ],
)
async def test_a_refusal_names_the_operation_the_status_and_discords_code_only(
    status: int, code: int | None, expired: bool, caplog: pytest.LogCaptureFixture
) -> None:
    fake = FakeDiscord()
    fake.fail(
        "followup", status=status, code=code, body={"message": "tok: TOKEN-IN-BODY", "code": code}
    )
    token = fresh_token()

    with pytest.raises(DiscordApiError) as caught:
        await discord_client(fake).create_followup(token, {"content": "x"})

    error = caught.value
    assert isinstance(error, DiscordTokenExpired) is expired
    assert (error.status_code, error.code, error.operation) == (status, code, "create_followup")
    for text in (str(error), repr(error), repr(error.args)):
        assert token not in text and "TOKEN-IN-BODY" not in text and "discord.com" not in text
    assert error.__cause__ is None
    # The app's formatter shows the numbers and nothing else.
    shown = format_exception_safely(error)
    assert f"status={status}" in shown
    if code is not None:
        assert f"code={code}" in shown
    assert token not in shown


async def test_a_bot_call_refused_as_not_found_is_not_an_expired_token() -> None:
    fake = FakeDiscord()
    fake.fail("channel_message", status=404, code=10003)
    with pytest.raises(DiscordApiError) as caught:
        await discord_client(fake).create_channel_message("1500000000000000001", {"content": "x"})
    assert not isinstance(caught.value, DiscordTokenExpired)


async def test_a_dm_refusal_carries_discords_code_for_the_log() -> None:
    fake = FakeDiscord()
    fake.fail("open_dm", status=403, code=50007)  # "Cannot send messages to this user"
    with pytest.raises(DiscordApiError) as caught:
        await discord_client(fake).open_dm("777000111222333444")
    assert (caught.value.status_code, caught.value.code) == (403, 50007)
    assert BOT_TOKEN not in str(caught.value)


async def test_a_network_failure_is_reported_by_its_class_alone_and_hides_its_cause() -> None:
    fake = FakeDiscord()
    fake.unreachable = True
    token = fresh_token()

    with pytest.raises(DiscordTransportError) as caught:
        await discord_client(fake).create_followup(token, {"content": "x"})

    error = caught.value
    assert error.error_type == "ConnectError"
    assert token not in str(error) and "discord.com" not in str(error)
    assert error.__cause__ is None and error.__suppress_context__
    assert token not in format_exception_safely(error)


async def test_a_logged_failure_leaves_no_token_in_the_json_line() -> None:
    fake = FakeDiscord()
    fake.fail("followup", status=404, code=10015)
    token = fresh_token()
    with pytest.raises(DiscordTokenExpired) as caught:
        await discord_client(fake).create_followup(token, {"content": "x"})

    record = logging.LogRecord(
        "t", logging.WARNING, __file__, 1, "reply failed %s", ("path /webhooks/1/" + token,), None
    )
    record.exc_info = (type(caught.value), caught.value, caught.value.__traceback__)
    line = JsonFormatter().format(record)
    assert token not in line


# -- retries are bounded ------------------------------------------------------------------------


def _limited(retry_after: Any = 0.5) -> dict[str, Any]:
    return {"message": "You are being rate limited.", "retry_after": retry_after, "global": False}


async def test_a_429_is_retried_after_the_wait_discord_asks_for() -> None:
    fake = FakeDiscord()
    fake.fail("followup", status=429, body=_limited(0.25), times=1)
    sleeper = SleepRecorder()
    client = discord_client(fake, sleep=sleeper)

    await client.create_followup(fresh_token(), {"content": "x"})

    assert sleeper.waits == [0.25]
    assert len(fake.of("followup")) == 2


async def test_retrying_stops_at_the_attempt_cap_and_raises_the_rate_limit() -> None:
    fake = FakeDiscord()
    fake.fail("followup", status=429, body=_limited(0.1), times=10)
    sleeper = SleepRecorder()
    client = discord_client(fake, sleep=sleeper, max_attempts=3)

    with pytest.raises(DiscordRateLimited) as caught:
        await client.create_followup(fresh_token(), {"content": "x"})

    assert len(fake.of("followup")) == 3
    assert sleeper.waits == [0.1, 0.1]  # no wait after the last attempt
    assert caught.value.status_code == 429 and caught.value.retry_after == 0.1


async def test_a_wait_longer_than_the_total_bound_is_refused_not_slept() -> None:
    fake = FakeDiscord()
    fake.fail("followup", status=429, body=_limited(60), times=5)
    sleeper = SleepRecorder()
    client = discord_client(fake, sleep=sleeper, max_total_wait_seconds=10.0)

    with pytest.raises(DiscordRateLimited):
        await client.create_followup(fresh_token(), {"content": "x"})

    assert sleeper.waits == []
    assert len(fake.of("followup")) == 1


async def test_the_total_wait_is_capped_across_attempts() -> None:
    fake = FakeDiscord()
    fake.fail("followup", status=429, body=_limited(4), times=10)
    sleeper = SleepRecorder()
    client = discord_client(fake, sleep=sleeper, max_attempts=10, max_total_wait_seconds=10.0)

    with pytest.raises(DiscordRateLimited):
        await client.create_followup(fresh_token(), {"content": "x"})

    assert sum(sleeper.waits) <= 10.0
    assert sleeper.waits == [4, 4]
    assert len(fake.of("followup")) == 3


def _production_client(fake: FakeDiscord, sleeper: SleepRecorder, **kwargs: Any) -> DiscordClient:
    """A client as the server builds it: no retry setting passed, so the shipped defaults apply."""
    return DiscordClient(
        http_for(fake),
        application_id=APPLICATION_ID,
        bot_token=BOT_TOKEN,
        sleep=sleeper,
        **kwargs,
    )


async def test_the_shipped_defaults_make_three_attempts_and_wait_twice() -> None:
    assert DEFAULT_MAX_ATTEMPTS == 3 and DEFAULT_MAX_TOTAL_WAIT_SECONDS == 10.0  # a conscious edit
    fake = FakeDiscord()
    fake.fail("followup", status=429, body=_limited(1), times=50)
    sleeper = SleepRecorder()

    with pytest.raises(DiscordRateLimited):
        await _production_client(fake, sleeper).create_followup(fresh_token(), {"content": "x"})

    assert len(fake.of("followup")) == 3
    assert sleeper.waits == [1, 1]


async def test_the_shipped_default_for_the_total_wait_is_ten_seconds() -> None:
    fake = FakeDiscord()
    fake.fail("followup", status=429, body=_limited(4), times=50)
    sleeper = SleepRecorder()

    with pytest.raises(DiscordRateLimited):
        await _production_client(fake, sleeper, max_attempts=10).create_followup(
            fresh_token(), {"content": "x"}
        )

    assert sleeper.waits == [4, 4]  # a third wait of 4 would make 12


async def test_a_wait_that_brings_the_total_exactly_to_the_bound_is_allowed() -> None:
    fake = FakeDiscord()
    fake.fail("followup", status=429, body=_limited(5), times=50)
    sleeper = SleepRecorder()
    client = discord_client(fake, sleep=sleeper, max_attempts=3, max_total_wait_seconds=10.0)

    with pytest.raises(DiscordRateLimited):
        await client.create_followup(fresh_token(), {"content": "x"})

    assert sleeper.waits == [5, 5]
    assert len(fake.of("followup")) == 3


async def test_every_request_carries_the_request_timeout() -> None:
    assert REQUEST_TIMEOUT_SECONDS == 15.0
    fake = FakeDiscord()
    timeouts: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timeouts.append(dict(request.extensions["timeout"]))
        return fake.handler(request)

    client = DiscordClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        application_id=APPLICATION_ID,
        bot_token=BOT_TOKEN,
    )
    await client.create_followup(fresh_token(), {"content": "as JSON"})
    await client.create_followup(
        fresh_token(), {"content": "as multipart"}, files=[("a.txt", b"hi")]
    )

    assert len(timeouts) == 2
    every = {
        "connect": REQUEST_TIMEOUT_SECONDS,
        "read": REQUEST_TIMEOUT_SECONDS,
        "write": REQUEST_TIMEOUT_SECONDS,
        "pool": REQUEST_TIMEOUT_SECONDS,
    }
    assert timeouts == [every, every]


@pytest.mark.parametrize(
    ("body", "header", "expected"),
    [
        ({"retry_after": 1.5}, None, 1.5),
        ({"retry_after": "2"}, None, 2.0),
        ({}, "3", 3.0),
        (None, "0.5", 0.5),
        ({"retry_after": "soon"}, None, 1.0),
        ({"retry_after": -4}, None, 1.0),
        ({"retry_after": True}, None, 1.0),
        (None, None, 1.0),
    ],
)
async def test_the_wait_is_read_from_the_body_then_the_header_and_never_trusted_blindly(
    body: Any, header: str | None, expected: float
) -> None:
    fake = FakeDiscord()
    headers = {"Retry-After": header} if header is not None else {}
    fake.fail("followup", status=429, body=body if body is not None else {}, headers=headers)
    sleeper = SleepRecorder()

    await discord_client(fake, sleep=sleeper).create_followup(fresh_token(), {"content": "x"})

    assert sleeper.waits == [expected]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500, 502])
async def test_nothing_but_a_429_is_retried(status: int) -> None:
    """Discord counts 401, 403 and 429 answers against a 10,000-per-10-minutes invalid-request
    limit that ends in a ban."""
    fake = FakeDiscord()
    fake.fail("followup", status=status, times=5)
    sleeper = SleepRecorder()

    with pytest.raises(DiscordApiError):
        await discord_client(fake, sleep=sleeper).create_followup(fresh_token(), {"content": "x"})

    assert len(fake.of("followup")) == 1
    assert sleeper.waits == []


# -- downloads ---------------------------------------------------------------------------------

_CDN = "https://cdn.discordapp.com/attachments/1/2/resume.json?ex=1&is=2&hm=3"


@pytest.mark.parametrize(
    "url",
    [
        _CDN,
        "https://media.discordapp.net/attachments/1/2/resume.json",
        "https://CDN.DISCORDAPP.COM/attachments/1/2/resume.json",
        "https://cdn.discordapp.com:443/attachments/1/2/resume.json",
    ],
)
def test_discords_cdn_hosts_over_https_are_fine(url: str) -> None:
    assert attachment_url_problem(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://cdn.discordapp.com/a/b.json",
        "ftp://cdn.discordapp.com/a/b.json",
        "//cdn.discordapp.com/a/b.json",
        "https://example.com/a/b.json",
        "https://discord.com/a/b.json",
        "https://cdn.discordapp.com.evil.example/a/b.json",
        "https://evilcdn.discordapp.com/a/b.json",
        "https://cdn.discordapp.com@evil.example/a/b.json",
        "https://user:pass@cdn.discordapp.com/a/b.json",
        "https://cdn.discordapp.com:8443/a/b.json",
        "https://cdn.discordapp.com:notaport/a/b.json",
        "https://127.0.0.1/a/b.json",
        "https://cdn.discordapp.com/a b.json",
        "https://cdn.discordapp.com/a\nb.json",
        "javascript:alert(1)",
    ],
)
def test_anything_else_is_refused(url: str) -> None:
    assert attachment_url_problem(url) is not None


def test_the_allowed_hosts_are_exactly_discords_two_cdn_names() -> None:
    assert {"cdn.discordapp.com", "media.discordapp.net"} == ATTACHMENT_HOSTS


async def test_a_file_is_downloaded_from_the_cdn_with_no_credential() -> None:
    fake = FakeDiscord(cdn_files={"resume.json": b'{"ok": true}'})
    data = await discord_client(fake).download_attachment(_CDN, max_bytes=1000)

    assert data == b'{"ok": true}'
    (request,) = fake.requests
    assert request.host == "cdn.discordapp.com"
    assert "authorization" not in request.headers


async def test_a_refused_address_is_never_requested() -> None:
    fake = FakeDiscord()
    with pytest.raises(AttachmentRefused):
        await discord_client(fake).download_attachment("https://example.com/x.json", max_bytes=10)
    assert fake.requests == []


async def test_a_redirect_is_refused_and_not_followed() -> None:
    fake = FakeDiscord()
    fake.cdn_redirect_to = "https://example.com/elsewhere.json"
    with pytest.raises(AttachmentRefused):
        await discord_client(fake).download_attachment(_CDN, max_bytes=1000)
    assert [r.host for r in fake.requests] == ["cdn.discordapp.com"]  # one request, no second hop


async def test_a_redirect_is_not_followed_even_by_a_client_that_would_follow_one() -> None:
    fake = FakeDiscord()
    fake.cdn_redirect_to = "https://example.com/elsewhere.json"
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler), follow_redirects=True)
    client = DiscordClient(http, application_id=APPLICATION_ID)
    with pytest.raises(AttachmentRefused):
        await client.download_attachment(_CDN, max_bytes=1000)
    assert [r.host for r in fake.requests] == ["cdn.discordapp.com"]


async def test_a_non_200_answer_is_refused() -> None:
    fake = FakeDiscord()
    fake.cdn_status = 404
    with pytest.raises(AttachmentRefused):
        await discord_client(fake).download_attachment(_CDN, max_bytes=1000)


async def test_a_declared_size_over_the_cap_is_refused_before_the_body_is_read() -> None:
    fake = FakeDiscord(cdn_files={"resume.json": b"x" * 10})
    fake.cdn_headers = {"content-length": "5000"}
    with pytest.raises(DownloadTooLarge):
        await discord_client(fake).download_attachment(_CDN, max_bytes=1000)


async def test_a_body_over_the_cap_is_refused_whatever_the_headers_claim_about_encoding() -> None:
    # The fake's bytes body always gets an exact Content-Length, so this is the declared-size path
    # too; the streamed path, where no size is declared at all, is the next test.
    fake = FakeDiscord(cdn_files={"resume.json": b"x" * 5000})
    fake.cdn_headers = {"transfer-encoding": "chunked"}
    with pytest.raises(DownloadTooLarge):
        await discord_client(fake).download_attachment(_CDN, max_bytes=1000)


async def test_a_body_with_no_declared_size_is_counted_as_it_arrives_and_cut_off_early() -> None:
    produced: list[int] = []

    async def body() -> AsyncIterator[bytes]:
        for index in range(1000):
            produced.append(index)
            yield b"x" * 100

    def handler(request: httpx.Request) -> httpx.Response:
        response = httpx.Response(200, content=body(), request=request)
        assert "content-length" not in response.headers  # nothing declared: only counting helps
        return response

    client = DiscordClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)), application_id=APPLICATION_ID
    )
    with pytest.raises(DownloadTooLarge):
        await client.download_attachment(_CDN, max_bytes=500)
    assert len(produced) < 20  # stopped near the cap; the other ~980 chunks were never read


async def test_a_file_exactly_at_the_cap_is_taken() -> None:
    fake = FakeDiscord(cdn_files={"resume.json": b"x" * 1000})
    assert len(await discord_client(fake).download_attachment(_CDN, max_bytes=1000)) == 1000


async def test_a_download_network_failure_is_reported_by_class_alone() -> None:
    fake = FakeDiscord(cdn_files={"resume.json": b"{}"})
    fake.unreachable = True
    with pytest.raises(DiscordTransportError) as caught:
        await DiscordClient(http_for(fake), application_id=APPLICATION_ID).download_attachment(
            _CDN, max_bytes=100
        )
    assert "hm=3" not in str(caught.value) and "cdn.discordapp.com" not in str(caught.value)


async def test_a_wait_that_is_not_a_number_is_not_trusted() -> None:
    """Python's JSON reader accepts `NaN`; a wait of NaN must fall back, not poison the sleep."""
    answers = iter(
        [
            httpx.Response(429, content=b'{"retry_after": NaN}'),
            httpx.Response(200, json={"id": "1400000000000000001"}),
        ]
    )
    sleeper = SleepRecorder()
    client = DiscordClient(
        httpx.AsyncClient(transport=httpx.MockTransport(lambda request: next(answers))),
        application_id=APPLICATION_ID,
        sleep=sleeper,
    )

    assert await client.create_followup(fresh_token(), {"content": "x"}) == "1400000000000000001"
    assert sleeper.waits == [1.0]
