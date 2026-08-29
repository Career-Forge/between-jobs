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

from between_jobs.api.telegram_client import TelegramClient

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
    assert captured["body"] == {"chat_id": 123, "text": "hello"}


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
