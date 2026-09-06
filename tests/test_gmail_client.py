"""Tests for the Gmail REST client (outreach-contactfinder.md Phase F) --
the authorize-URL builder, the token exchange/refresh calls, draft
creation, and the config helper's clean SETUP_REQUIRED failure mode.
"""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from between_jobs.api.errors import ApiError
from between_jobs.api.gmail_client import (
    build_authorize_url,
    create_draft,
    exchange_code_for_tokens,
    get_thread,
    refresh_access_token,
    require_gmail_oauth_config,
)


def test_build_authorize_url_requests_offline_access_and_forces_consent() -> None:
    url = build_authorize_url(
        client_id="client-123",
        redirect_uri="http://localhost:8012/oauth/gmail/callback",
        state="abc",
    )
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    assert params["client_id"] == ["client-123"]
    assert params["redirect_uri"] == ["http://localhost:8012/oauth/gmail/callback"]
    assert params["state"] == ["abc"]
    assert params["access_type"] == ["offline"]
    assert params["prompt"] == ["consent"]
    assert params["scope"] == [
        "https://www.googleapis.com/auth/gmail.compose "
        "https://www.googleapis.com/auth/gmail.readonly"
    ]


def test_require_gmail_oauth_config_raises_setup_required_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_REDIRECT_URI", raising=False)

    with pytest.raises(ApiError) as exc_info:
        require_gmail_oauth_config()
    assert exc_info.value.code == "SETUP_REQUIRED"


def test_require_gmail_oauth_config_returns_the_three_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-123")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret-456")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8012/oauth/gmail/callback")

    config = require_gmail_oauth_config()
    assert config["client_id"] == "client-123"
    assert config["client_secret"] == "secret-456"
    assert config["redirect_uri"] == "http://localhost:8012/oauth/gmail/callback"


class _FakeHttp:
    def __init__(self, *, status_code: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self.body = body or {}
        self.requests: list[tuple[str, dict[str, Any]]] = []

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.requests.append((url, kwargs))
        return httpx.Response(
            status_code=self.status_code, json=self.body, request=httpx.Request("POST", url)
        )

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self.requests.append((url, kwargs))
        return httpx.Response(
            status_code=self.status_code, json=self.body, request=httpx.Request("GET", url)
        )


async def test_exchange_code_for_tokens_returns_both_tokens() -> None:
    http = _FakeHttp(
        body={
            "access_token": "at-1",
            "refresh_token": "rt-1",
            "scope": "https://www.googleapis.com/auth/gmail.compose "
            "https://www.googleapis.com/auth/gmail.readonly",
        }
    )

    result = await exchange_code_for_tokens(
        http,  # type: ignore[arg-type]
        code="auth-code",
        client_id="client-123",
        client_secret="secret-456",
        redirect_uri="http://localhost:8012/oauth/gmail/callback",
    )

    assert result["access_token"] == "at-1"
    assert result["refresh_token"] == "rt-1"
    assert result["scope"] == (
        "https://www.googleapis.com/auth/gmail.compose "
        "https://www.googleapis.com/auth/gmail.readonly"
    )
    _url, kwargs = http.requests[0]
    assert kwargs["data"]["grant_type"] == "authorization_code"


async def test_exchange_code_for_tokens_handles_a_missing_scope() -> None:
    http = _FakeHttp(body={"access_token": "at-1", "refresh_token": "rt-1"})

    result = await exchange_code_for_tokens(
        http,  # type: ignore[arg-type]
        code="auth-code",
        client_id="client-123",
        client_secret="secret-456",
        redirect_uri="http://localhost:8012/oauth/gmail/callback",
    )
    assert result["scope"] is None


async def test_exchange_code_for_tokens_handles_a_missing_refresh_token() -> None:
    """A real Google behavior: re-authorizing without prompt=consent (or
    an already-consented client) omits refresh_token entirely."""
    http = _FakeHttp(body={"access_token": "at-1"})

    result = await exchange_code_for_tokens(
        http,  # type: ignore[arg-type]
        code="auth-code",
        client_id="client-123",
        client_secret="secret-456",
        redirect_uri="http://localhost:8012/oauth/gmail/callback",
    )
    assert result["refresh_token"] is None


async def test_exchange_code_for_tokens_raises_provider_rejected_on_a_bad_code() -> None:
    http = _FakeHttp(status_code=400, body={"error": "invalid_grant"})

    with pytest.raises(ApiError) as exc_info:
        await exchange_code_for_tokens(
            http,  # type: ignore[arg-type]
            code="bad-code",
            client_id="client-123",
            client_secret="secret-456",
            redirect_uri="http://localhost:8012/oauth/gmail/callback",
        )
    assert exc_info.value.code == "PROVIDER_REJECTED"


async def test_refresh_access_token_returns_the_new_access_token() -> None:
    http = _FakeHttp(body={"access_token": "at-2"})

    access_token = await refresh_access_token(
        http,  # type: ignore[arg-type]
        refresh_token="rt-1",
        client_id="client-123",
        client_secret="secret-456",
    )
    assert access_token == "at-2"


async def test_refresh_access_token_raises_provider_rejected_on_a_revoked_token() -> None:
    http = _FakeHttp(status_code=400, body={"error": "invalid_grant"})

    with pytest.raises(ApiError) as exc_info:
        await refresh_access_token(
            http,  # type: ignore[arg-type]
            refresh_token="revoked",
            client_id="client-123",
            client_secret="secret-456",
        )
    assert exc_info.value.code == "PROVIDER_REJECTED"


async def test_create_draft_returns_the_draft_id_and_thread_id_and_never_calls_send() -> None:
    http = _FakeHttp(body={"id": "draft-abc", "message": {"id": "msg-1", "threadId": "thread-1"}})

    draft = await create_draft(
        http,  # type: ignore[arg-type]
        access_token="at-1",
        to_email="jane.doe@acme.example",
        subject="Loved your PyData talk",
        body_text="Saw your talk -- would love to chat.",
    )

    assert draft["id"] == "draft-abc"
    assert draft["thread_id"] == "thread-1"
    url, kwargs = http.requests[0]
    assert url.endswith("/drafts")
    assert "send" not in url

    raw = kwargs["json"]["message"]["raw"]
    decoded = base64.urlsafe_b64decode(raw).decode()
    assert "To: jane.doe@acme.example" in decoded
    assert "Subject: Loved your PyData talk" in decoded


async def test_create_draft_raises_provider_rejected_on_401() -> None:
    http = _FakeHttp(status_code=401)

    with pytest.raises(ApiError) as exc_info:
        await create_draft(
            http,  # type: ignore[arg-type]
            access_token="expired",
            to_email="jane.doe@acme.example",
            subject="Subject",
            body_text="Body",
        )
    assert exc_info.value.code == "PROVIDER_REJECTED"


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


async def test_get_thread_parses_a_simple_single_part_message() -> None:
    http = _FakeHttp(
        body={
            "id": "thread-1",
            "messages": [
                {
                    "id": "msg-1",
                    "labelIds": ["SENT"],
                    "internalDate": "1735689600000",
                    "snippet": "Saw your talk...",
                    "payload": {
                        "mimeType": "text/plain",
                        "body": {"data": _b64url("Saw your talk -- would love to chat.")},
                    },
                }
            ],
        }
    )

    thread = await get_thread(http, access_token="at-1", thread_id="18cabc1234def567")  # type: ignore[arg-type]

    assert thread["id"] == "thread-1"
    assert len(thread["messages"]) == 1
    message = thread["messages"][0]
    assert message["id"] == "msg-1"
    assert message["label_ids"] == ["SENT"]
    assert message["internal_date_ms"] == 1735689600000
    assert message["body_text"] == "Saw your talk -- would love to chat."


async def test_get_thread_finds_a_nested_multipart_text_plain_part() -> None:
    """A real reply commonly arrives as multipart/alternative (plain +
    html) nested inside a multipart/mixed (with a quoted-thread
    attachment) -- a single-level parts scan would miss this."""
    http = _FakeHttp(
        body={
            "id": "thread-1",
            "messages": [
                {
                    "id": "msg-2",
                    "labelIds": ["INBOX"],
                    "internalDate": "1735776000000",
                    "snippet": "Thanks for reaching out",
                    "payload": {
                        "mimeType": "multipart/mixed",
                        "parts": [
                            {
                                "mimeType": "multipart/alternative",
                                "parts": [
                                    {
                                        "mimeType": "text/plain",
                                        "body": {"data": _b64url("Thanks for reaching out!")},
                                    },
                                    {
                                        "mimeType": "text/html",
                                        "body": {
                                            "data": _b64url("<p>Thanks for reaching out!</p>")
                                        },
                                    },
                                ],
                            }
                        ],
                    },
                }
            ],
        }
    )

    thread = await get_thread(http, access_token="at-1", thread_id="18cabc1234def567")  # type: ignore[arg-type]

    assert thread["messages"][0]["body_text"] == "Thanks for reaching out!"


async def test_get_thread_falls_back_to_snippet_when_no_plain_text_part_exists() -> None:
    http = _FakeHttp(
        body={
            "id": "thread-1",
            "messages": [
                {
                    "id": "msg-3",
                    "labelIds": ["INBOX"],
                    "internalDate": "1735776000000",
                    "snippet": "Thanks for reaching out",
                    "payload": {
                        "mimeType": "text/html",
                        "body": {"data": _b64url("<p>Thanks for reaching out!</p>")},
                    },
                }
            ],
        }
    )

    thread = await get_thread(http, access_token="at-1", thread_id="18cabc1234def567")  # type: ignore[arg-type]

    assert thread["messages"][0]["body_text"] == "Thanks for reaching out"


async def test_get_thread_parses_every_message_in_a_multi_message_thread() -> None:
    """Regression test for a real, adversarially-confirmed coverage gap:
    every prior get_thread test supplied exactly one message, so a
    regression that only processed the first message (messages[:1], an
    early break, or state bleeding between loop iterations) would have
    passed unnoticed -- the real reply-checking use case is inherently
    multi-message (a sent draft plus a real reply)."""
    http = _FakeHttp(
        body={
            "id": "thread-1",
            "messages": [
                {
                    "id": "msg-sent",
                    "labelIds": ["SENT"],
                    "internalDate": "1735689600000",
                    "snippet": "Saw your talk...",
                    "payload": {
                        "mimeType": "text/plain",
                        "body": {"data": _b64url("Saw your talk -- would love to chat.")},
                    },
                },
                {
                    "id": "msg-reply",
                    "labelIds": ["INBOX"],
                    "internalDate": "1735776000000",
                    "snippet": "Thanks for reaching out",
                    "payload": {
                        "mimeType": "text/plain",
                        "body": {"data": _b64url("Thanks for reaching out! Let's chat.")},
                    },
                },
            ],
        }
    )

    thread = await get_thread(http, access_token="at-1", thread_id="18cabc1234def567")  # type: ignore[arg-type]

    assert len(thread["messages"]) == 2
    sent, reply = thread["messages"]
    assert sent["id"] == "msg-sent"
    assert sent["label_ids"] == ["SENT"]
    assert sent["body_text"] == "Saw your talk -- would love to chat."
    assert reply["id"] == "msg-reply"
    assert reply["label_ids"] == ["INBOX"]
    assert reply["body_text"] == "Thanks for reaching out! Let's chat."


async def test_get_thread_decodes_a_body_whose_base64_length_is_congruent_to_2_mod_4() -> None:
    """Regression test for a real, adversarially-confirmed gap: every
    prior test's decoded payload had base64 length congruent to 0 mod 4
    -- a subtly wrong padding formula correct only for that case would
    have passed unnoticed. "Test" is 4 raw bytes -> unpadded base64url
    length 6 (6 % 4 == 2)."""
    http = _FakeHttp(
        body={
            "id": "thread-1",
            "messages": [
                {
                    "id": "msg-1",
                    "labelIds": ["INBOX"],
                    "internalDate": "1735776000000",
                    "snippet": "Test",
                    "payload": {"mimeType": "text/plain", "body": {"data": _b64url("Test")}},
                }
            ],
        }
    )

    thread = await get_thread(http, access_token="at-1", thread_id="18cabc1234def567")  # type: ignore[arg-type]

    assert thread["messages"][0]["body_text"] == "Test"


async def test_get_thread_decodes_a_body_whose_base64_length_is_congruent_to_3_mod_4() -> None:
    """ "Hi" is 2 raw bytes -> unpadded base64url length 3 (3 % 4 == 3)."""
    http = _FakeHttp(
        body={
            "id": "thread-1",
            "messages": [
                {
                    "id": "msg-1",
                    "labelIds": ["INBOX"],
                    "internalDate": "1735776000000",
                    "snippet": "Hi",
                    "payload": {"mimeType": "text/plain", "body": {"data": _b64url("Hi")}},
                }
            ],
        }
    )

    thread = await get_thread(http, access_token="at-1", thread_id="18cabc1234def567")  # type: ignore[arg-type]

    assert thread["messages"][0]["body_text"] == "Hi"


async def test_get_thread_falls_back_to_snippet_when_a_plain_text_part_has_no_inline_data() -> None:
    """Regression test for a real, adversarially-confirmed HIGH-severity
    bug: a large plain-text body is returned as {attachmentId, size}
    with no inline `data` at all -- this used to be treated as "found
    empty content" instead of falling back to the snippet."""
    http = _FakeHttp(
        body={
            "id": "thread-1",
            "messages": [
                {
                    "id": "msg-1",
                    "labelIds": ["INBOX"],
                    "internalDate": "1735776000000",
                    "snippet": "Thanks for reaching out",
                    "payload": {
                        "mimeType": "text/plain",
                        "body": {"attachmentId": "ANGjdJ...", "size": 41000},
                    },
                }
            ],
        }
    )

    thread = await get_thread(http, access_token="at-1", thread_id="18cabc1234def567")  # type: ignore[arg-type]

    assert thread["messages"][0]["body_text"] == "Thanks for reaching out"


async def test_get_thread_rejects_a_malformed_thread_id() -> None:
    """Regression test for a real, adversarially-confirmed hardening
    gap: thread_id is interpolated directly into the request URL path --
    a value containing "/"/"?"/"#" must never reach that interpolation
    unchecked, even though no caller sources it from user input today."""
    http = _FakeHttp()

    with pytest.raises(ApiError) as exc_info:
        await get_thread(http, access_token="at-1", thread_id="../not-a-real-id?evil=1")  # type: ignore[arg-type]

    assert exc_info.value.code == "INVALID_INPUT"
    assert http.requests == []


async def test_get_thread_raises_provider_rejected_on_401() -> None:
    http = _FakeHttp(status_code=401)

    with pytest.raises(ApiError) as exc_info:
        await get_thread(http, access_token="expired", thread_id="18cabc1234def567")  # type: ignore[arg-type]
    assert exc_info.value.code == "PROVIDER_REJECTED"


async def test_get_thread_raises_provider_unavailable_on_a_network_error() -> None:
    class _RaisingHttp:
        async def get(self, *_args: Any, **_kwargs: Any) -> httpx.Response:
            raise httpx.ConnectError("boom")

    with pytest.raises(ApiError) as exc_info:
        await get_thread(_RaisingHttp(), access_token="at-1", thread_id="18cabc1234def567")  # type: ignore[arg-type]
    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"
