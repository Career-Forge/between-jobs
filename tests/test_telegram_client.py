"""Tests for TelegramClient's actual HTTP calls (Sprint 2.5d).

Every other test in this project replaces TelegramClient with a fake, so
its real request-building logic has never been exercised directly --
worth closing now that it grew a second base URL (file downloads use
`/file/bot<token>/`, not `/bot<token>/`) and two new endpoints. Uses
httpx's own `MockTransport` (built in, no new dependency) rather than a
live network call.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from between_jobs.api.telegram_client import DownloadTooLarge, Html, TelegramClient, escape, render

_BOT_TOKEN = "test-token-not-real"


def _client(handler: Any) -> TelegramClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return TelegramClient(http, _BOT_TOKEN)


async def test_send_message_posts_chat_id_and_text() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {}})

    telegram = _client(handler)
    await telegram.send_message(123, "hello")

    assert captured["url"] == f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"
    assert captured["body"] == {"chat_id": 123, "text": "hello", "parse_mode": "HTML"}


async def test_send_message_includes_reply_markup_when_given() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {}})

    telegram = _client(handler)
    keyboard = {"inline_keyboard": [[{"text": "Yes", "callback_data": "yes"}]]}
    await telegram.send_message(123, "confirm?", reply_markup=keyboard)

    assert captured["body"]["reply_markup"] == keyboard


async def test_send_message_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "description": "bad request"})

    telegram = _client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await telegram.send_message(123, "hello")


async def test_send_document_posts_multipart_with_chat_id_and_file() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["content_type"] = request.headers["content-type"]
        captured["body"] = request.content
        return httpx.Response(200, json={"ok": True, "result": {}})

    telegram = _client(handler)
    await telegram.send_document(123, "resume.pdf", b"%PDF-1.5 fake", caption="Here you go")

    assert captured["url"] == f"https://api.telegram.org/bot{_BOT_TOKEN}/sendDocument"
    assert captured["content_type"].startswith("multipart/form-data")
    assert b'name="chat_id"' in captured["body"]
    assert b"123" in captured["body"]
    assert b'name="caption"' in captured["body"]
    assert b"Here you go" in captured["body"]
    assert b'filename="resume.pdf"' in captured["body"]
    assert b"%PDF-1.5 fake" in captured["body"]


async def test_send_document_omits_caption_field_when_not_given() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json={"ok": True, "result": {}})

    telegram = _client(handler)
    await telegram.send_document(123, "resume.pdf", b"%PDF-1.5 fake")

    assert b'name="caption"' not in captured["body"]


async def test_send_document_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "description": "bad request"})

    telegram = _client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await telegram.send_document(123, "resume.pdf", b"%PDF-1.5 fake")


async def test_answer_callback_query_posts_the_id() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": True})

    telegram = _client(handler)
    await telegram.answer_callback_query("cbq-123")

    assert captured["url"] == f"https://api.telegram.org/bot{_BOT_TOKEN}/answerCallbackQuery"
    assert captured["body"] == {"callback_query_id": "cbq-123"}


async def test_get_file_path_returns_the_path_from_the_response() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200, json={"ok": True, "result": {"file_id": "abc", "file_path": "documents/x.json"}}
        )

    telegram = _client(handler)
    file_path = await telegram.get_file_path("abc")

    assert file_path == "documents/x.json"
    assert "getFile" in captured["url"]
    assert "file_id=abc" in captured["url"]


async def test_download_file_hits_the_file_host_not_the_bot_host() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, content=b'{"personal": {}}')

    telegram = _client(handler)
    content = await telegram.download_file("documents/x.json")

    assert content == b'{"personal": {}}'
    assert captured["url"] == f"https://api.telegram.org/file/bot{_BOT_TOKEN}/documents/x.json"


async def test_download_document_chains_get_file_path_and_download_file() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "getFile" in str(request.url):
            return httpx.Response(
                200, json={"ok": True, "result": {"file_path": "documents/resume.json"}}
            )
        return httpx.Response(200, content=b'{"personal": {"name": "Jane"}}')

    telegram = _client(handler)
    content = await telegram.download_document("file-id-1")

    assert content == b'{"personal": {"name": "Jane"}}'
    assert len(calls) == 2
    assert "bot" in calls[0] and "getFile" in calls[0]
    assert "file/bot" in calls[1]


async def test_a_download_within_the_cap_is_returned_whole() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 100)

    assert await _client(handler).download_file("documents/x.json", max_bytes=100) == b"x" * 100


async def test_a_download_that_declares_itself_over_the_cap_is_not_read() -> None:
    read: list[int] = []

    async def body() -> Any:
        for _ in range(10):
            read.append(1)
            yield b"x" * 100

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Length": "1000"}, content=body())

    with pytest.raises(DownloadTooLarge) as refused:
        await _client(handler).download_file("documents/x.json", max_bytes=500)

    assert refused.value.max_bytes == 500
    assert read == []  # refused on the header, before a byte of the body


async def test_a_download_that_streams_past_the_cap_is_cut_off_early() -> None:
    """No Content-Length (chunked), or one that lies: the bytes are counted as they arrive and
    the rest of the file is never pulled in."""
    produced: list[int] = []

    async def body() -> Any:
        for _ in range(1000):
            produced.append(1)
            yield b"x" * 100

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body())  # an iterator: sent chunked, no length

    with pytest.raises(DownloadTooLarge):
        await _client(handler).download_file("documents/x.json", max_bytes=500)

    assert len(produced) < 20  # nowhere near all 1000 chunks (100 kB)


async def test_a_download_with_no_cap_is_unchanged() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 5000)

    assert len(await _client(handler).download_file("documents/x.json")) == 5000


async def test_a_download_error_status_still_raises_with_a_cap() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(httpx.HTTPStatusError):
        await _client(handler).download_file("documents/x.json", max_bytes=500)


async def test_download_document_passes_the_cap_on() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "getFile" in str(request.url):
            return httpx.Response(
                200, json={"ok": True, "result": {"file_path": "documents/r.json"}}
            )
        return httpx.Response(200, content=b"x" * 600)

    with pytest.raises(DownloadTooLarge):
        await _client(handler).download_document("file-id", max_bytes=500)


# -- parse_mode and escaping (P0.9) ---------------------------------------------------------


async def test_every_message_is_sent_in_html_mode() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    telegram = _client(handler)
    await telegram.send_message(1, "plain")
    await telegram.send_message(1, Html("<b>marked up</b>"))
    await telegram.send_message(1, "with a keyboard", reply_markup={"inline_keyboard": []})

    assert [b["parse_mode"] for b in bodies] == ["HTML", "HTML", "HTML"]


async def test_plain_text_is_escaped_so_it_can_never_be_read_as_markup() -> None:
    """Names, titles and error messages come from outside; `<b>` in a job title is text."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {}})

    await _client(handler).send_message(1, "C++ <Lead> & more: <b>x</b>")

    assert captured["body"]["text"] == "C++ &lt;Lead&gt; &amp; more: &lt;b&gt;x&lt;/b&gt;"


async def test_html_is_sent_as_written() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {}})

    await _client(handler).send_message(1, Html("Tap <code>this</code> &amp; copy"))

    assert captured["body"]["text"] == "Tap <code>this</code> &amp; copy"


async def test_a_message_telegram_cannot_parse_is_resent_once_as_plain_text() -> None:
    """A user told nothing is worse off than one who sees a stray tag."""
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(
                400,
                json={
                    "ok": False,
                    "description": "Bad Request: can't parse entities: Unsupported start tag",
                },
            )
        return httpx.Response(200, json={"ok": True, "result": {}})

    await _client(handler).send_message(1, Html("Hello <b>there</b> &amp; welcome"))

    assert len(bodies) == 2
    assert bodies[1]["text"] == "Hello there & welcome"
    assert "parse_mode" not in bodies[1]


async def test_the_plain_text_retry_happens_once_and_a_second_failure_raises() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            400, json={"ok": False, "description": "Bad Request: can't parse entities"}
        )

    with pytest.raises(httpx.HTTPStatusError):
        await _client(handler).send_message(1, "hello")

    assert calls == 2  # the message, one plain-text retry, then it gives up


async def test_a_different_bad_request_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"ok": False, "description": "chat not found"})

    with pytest.raises(httpx.HTTPStatusError):
        await _client(handler).send_message(1, "hello")

    assert calls == 1


async def test_a_document_caption_stays_plain_text() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json={"ok": True, "result": {}})

    await _client(handler).send_document(1, "r.pdf", b"%PDF", caption="Resume -- 1 < 2 & done")

    assert b'name="parse_mode"' not in captured["body"]
    assert b"1 < 2 & done" in captured["body"]


@pytest.mark.parametrize(
    "markup",
    [
        "<b>never closed",
        "closed too early</b>",
        "<i><b>crossed</i></b>",
        '<a href="https://example.com">link</a>',
        "<script>alert(1)</script>",
        "1 < 2",
        "a > b",
        "fish & chips",
        "<b >spaced</b>",
    ],
)
def test_html_that_telegram_would_reject_fails_when_it_is_built(markup: str) -> None:
    with pytest.raises(ValueError):
        Html(markup)


@pytest.mark.parametrize(
    "markup",
    [
        "plain",
        "<b>bold</b> and <i>italic</i> and <code>code</code>",
        "<pre>a &lt;block&gt; &amp; more</pre>",
        "caf\u00e9 &#38; &quot;quoted&quot;",
        "{placeholders} are fine until rendered",
    ],
)
def test_valid_html_is_accepted(markup: str) -> None:
    assert Html(markup) == markup


def test_render_escapes_every_value_and_returns_html() -> None:
    out = render(Html("<b>{title}</b> at <b>{company}</b>"), title="A <Lead> & Co", company="x>y")

    assert isinstance(out, Html)
    assert out == "<b>A &lt;Lead&gt; &amp; Co</b> at <b>x&gt;y</b>"


def test_escape_leaves_quotes_alone() -> None:
    assert escape('say "hi" it\'s') == 'say "hi" it\'s'


# -- message ids, editing a message, and a callback's note (channel envelope) ----------------


def _answering(result: Any) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": result})

    return handler


async def test_send_message_returns_the_id_telegram_gave_the_message() -> None:
    assert await _client(_answering({"message_id": 42})).send_message(1, "hi") == 42


@pytest.mark.parametrize("result", [{}, {"message_id": True}, {"message_id": "7"}, True, None, []])
async def test_send_message_returns_none_when_the_answer_carries_no_usable_id(
    result: Any,
) -> None:
    assert await _client(_answering(result)).send_message(1, "hi") is None


async def test_send_message_returns_none_when_the_answer_is_not_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    assert await _client(handler).send_message(1, "hi") is None


@pytest.mark.parametrize("body", [[], "text", 42])
async def test_a_reply_that_is_valid_json_but_not_an_object_carries_no_id(body: Any) -> None:
    """The message was accepted: reading its id must not raise, or the webhook would fail
    after the fact and Telegram's redelivery would send the person a duplicate."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    assert await _client(handler).send_message(1, "hi") is None
    assert await _client(handler).edit_message_text(1, 9, Html("hi")) is None


async def test_send_document_returns_the_id_telegram_gave_the_message() -> None:
    client = _client(_answering({"message_id": 43}))
    assert await client.send_document(1, "r.pdf", b"%PDF") == 43
    assert await _client(_answering({})).send_document(1, "r.pdf", b"%PDF") is None


async def test_edit_message_text_posts_the_chat_the_message_and_the_html_text() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})

    edited = await _client(handler).edit_message_text(123, 9, Html("now <b>done</b>"))

    assert captured["url"] == f"https://api.telegram.org/bot{_BOT_TOKEN}/editMessageText"
    assert captured["body"] == {
        "chat_id": 123,
        "message_id": 9,
        "text": "now <b>done</b>",
        "parse_mode": "HTML",
    }
    assert edited == 9


async def test_edit_message_text_escapes_plain_text_and_can_set_a_keyboard() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {}})

    keyboard = {"inline_keyboard": [[{"text": "Undo", "callback_data": "undo"}]]}
    edited = await _client(handler).edit_message_text(1, 2, "a <b> & c", reply_markup=keyboard)

    assert captured["body"]["text"] == "a &lt;b&gt; &amp; c"
    assert captured["body"]["reply_markup"] == keyboard
    assert edited is None  # the answer carried no id


async def test_an_edit_telegram_cannot_parse_is_resent_once_as_plain_text() -> None:
    bodies: list[dict[str, Any]] = []
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        paths.append(request.url.path.rsplit("/", 1)[-1])
        if len(bodies) == 1:
            return httpx.Response(
                400, json={"ok": False, "description": "Bad Request: can't parse entities"}
            )
        return httpx.Response(200, json={"ok": True, "result": {}})

    await _client(handler).edit_message_text(1, 2, Html("Hello <b>there</b> &amp; welcome"))

    assert paths == ["editMessageText", "editMessageText"]
    assert bodies[1]["text"] == "Hello there & welcome"
    assert "parse_mode" not in bodies[1]
    assert bodies[1]["message_id"] == 2


async def test_edit_message_text_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "description": "message is not modified"})

    with pytest.raises(httpx.HTTPStatusError):
        await _client(handler).edit_message_text(1, 2, "same")


async def test_answer_callback_query_can_show_a_note_to_the_person_who_tapped() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": True})

    await _client(handler).answer_callback_query("cbq-9", text="Saved")

    assert captured["body"] == {"callback_query_id": "cbq-9", "text": "Saved"}
