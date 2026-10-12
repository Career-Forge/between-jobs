"""Tests for the Gmail REST client (outreach-contactfinder.md Phase F) --
the authorize-URL builder, the token exchange/refresh calls, draft
creation, and the config helper's clean SETUP_REQUIRED failure mode.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from between_jobs.api.errors import ApiError
from between_jobs.api.gmail_client import (
    GMAIL_RECONNECT_REASON,
    build_authorize_url,
    create_draft,
    exchange_code_for_tokens,
    get_thread,
    needs_reconnect,
    reconnect_required_error,
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


def test_require_gmail_oauth_config_is_feature_disabled_when_unset(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Gmail is optional: unset is "this server has no Gmail" (404 FEATURE_DISABLED), not a setup
    step the person could do, and not a startup failure to shout about in the log."""
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_REDIRECT_URI", raising=False)

    with caplog.at_level("DEBUG"), pytest.raises(ApiError) as exc_info:
        require_gmail_oauth_config()
    assert exc_info.value.code == "FEATURE_DISABLED"
    assert exc_info.value.message == "Gmail isn't set up on this server."
    assert caplog.records == []


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


# -- when Google stops honouring a connection, and when it is only having a bad moment ----------

_GOOGLE_TEXT = "Token has been expired or revoked. Internal reference 4c1f-secret-ish"
_THREAD = "18cabc1234def567"


class _RawHttp:
    """Answers every call with the given status and raw body (which need not be JSON)."""

    def __init__(self, status_code: int, content: bytes | str = b"") -> None:
        self.status_code = status_code
        self.content = content if isinstance(content, bytes) else content.encode()
        self.calls = 0

    async def post(self, url: str, **_kwargs: Any) -> httpx.Response:
        self.calls += 1
        return httpx.Response(
            self.status_code, content=self.content, request=httpx.Request("POST", url)
        )

    async def get(self, url: str, **_kwargs: Any) -> httpx.Response:
        self.calls += 1
        return httpx.Response(
            self.status_code, content=self.content, request=httpx.Request("GET", url)
        )


class _UnreachableHttp:
    async def post(self, url: str, **_kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError("no route", request=httpx.Request("POST", url))

    get = post


def _oauth_error(code: str, *, text: str = _GOOGLE_TEXT) -> str:
    return json.dumps({"error": code, "error_description": text})


def _gmail_error(status: int, reason: str | None, *, text: str = _GOOGLE_TEXT) -> str:
    errors = [{"domain": "global", "reason": reason, "message": text}] if reason else []
    return json.dumps({"error": {"code": status, "message": text, "errors": errors}})


async def _refresh(http: Any) -> str:
    return await refresh_access_token(
        http, refresh_token="rt-1", client_id="client-123", client_secret="secret-456"
    )


async def _draft(http: Any) -> Any:
    return await create_draft(
        http, access_token="at", to_email="a@b.example", subject="s", body_text="b"
    )


async def _thread(http: Any) -> Any:
    return await get_thread(http, access_token="at", thread_id=_THREAD)


_GMAIL_CALLS = [_draft, _thread]
_GMAIL_CALL_IDS = ["create_draft", "get_thread"]


def _assert_reconnect(error: ApiError) -> None:
    assert (error.code, error.status_code) == ("PROVIDER_REJECTED", 502)
    assert (
        error.details
        == {"reason": GMAIL_RECONNECT_REASON}
        == {"reason": "gmail_reconnect_required"}
    )
    assert error.settings_path == "/profile/integrations"
    assert error.retryable is False
    assert needs_reconnect(error)
    assert "Reconnect Gmail in Integrations" in error.message
    assert _GOOGLE_TEXT not in error.message and "secret" not in error.message.lower()


def test_the_reconnect_error_is_a_rejected_provider_call_that_points_at_integrations() -> None:
    _assert_reconnect(reconnect_required_error())


def test_nothing_but_that_error_needs_a_reconnect() -> None:
    assert not needs_reconnect(ApiError("PROVIDER_REJECTED", "Google wouldn't refresh."))
    assert not needs_reconnect(
        ApiError("PROVIDER_REJECTED", "x", details={"reason": "key_rejected"})
    )
    assert not needs_reconnect(ApiError("PROVIDER_UNAVAILABLE", "x", retryable=True))
    assert not needs_reconnect(RuntimeError("gmail_reconnect_required"))
    assert not needs_reconnect(ValueError())


@pytest.mark.parametrize("error", ["invalid_grant", "admin_policy_enforced"])
@pytest.mark.parametrize("status", [400, 401])
async def test_a_refresh_token_google_no_longer_honours_is_a_reconnect(
    error: str, status: int
) -> None:
    with pytest.raises(ApiError) as raised:
        await _refresh(_RawHttp(status, _oauth_error(error)))
    _assert_reconnect(raised.value)


@pytest.mark.parametrize(
    "body",
    [
        _oauth_error("invalid_client"),  # the operator's client id or secret: not the person's
        _oauth_error("deleted_client"),
        _oauth_error("invalid_request"),
        _oauth_error("unauthorized_client"),
        json.dumps({"error": "Invalid_Grant"}),  # not the documented spelling
        json.dumps({"error": {"nested": "invalid_grant"}}),
        json.dumps(["invalid_grant"]),
        json.dumps({"error": "invalid_grant extra words"}),
        json.dumps({"error_description": "invalid_grant"}),
        "<html>invalid_grant</html>",
        "",
    ],
)
async def test_any_other_refusal_to_refresh_is_not_a_reason_to_send_the_person_to_reconnect(
    body: str,
) -> None:
    with pytest.raises(ApiError) as raised:
        await _refresh(_RawHttp(400, body))
    error = raised.value
    assert (error.code, error.retryable) == ("PROVIDER_REJECTED", False)
    assert not needs_reconnect(error) and error.details == {}
    assert _GOOGLE_TEXT not in error.message


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
async def test_a_refresh_that_hits_a_rate_limit_or_an_outage_is_transient_whatever_the_body(
    status: int,
) -> None:
    with pytest.raises(ApiError) as raised:
        await _refresh(_RawHttp(status, _oauth_error("invalid_grant")))
    error = raised.value
    assert (error.code, error.retryable) == ("PROVIDER_UNAVAILABLE", True)
    assert not needs_reconnect(error)


async def test_a_refresh_that_cannot_reach_google_is_transient() -> None:
    with pytest.raises(ApiError) as raised:
        await _refresh(_UnreachableHttp())
    assert (raised.value.code, raised.value.retryable) == ("PROVIDER_UNAVAILABLE", True)
    assert not needs_reconnect(raised.value)


@pytest.mark.parametrize("call", _GMAIL_CALLS, ids=_GMAIL_CALL_IDS)
@pytest.mark.parametrize(
    "body",
    [
        "",
        _gmail_error(401, "authError"),
        "<html>nope</html>",
        json.dumps(["not", "an", "object"]),
    ],
)
async def test_gmail_answering_401_is_a_reconnect_whatever_it_says(call: Any, body: str) -> None:
    with pytest.raises(ApiError) as raised:
        await call(_RawHttp(401, body))
    _assert_reconnect(raised.value)


@pytest.mark.parametrize("call", _GMAIL_CALLS, ids=_GMAIL_CALL_IDS)
async def test_gmail_saying_a_scope_is_missing_is_a_reconnect(call: Any) -> None:
    with pytest.raises(ApiError) as raised:
        await call(_RawHttp(403, _gmail_error(403, "insufficientPermissions")))
    _assert_reconnect(raised.value)


@pytest.mark.parametrize("call", _GMAIL_CALLS, ids=_GMAIL_CALL_IDS)
@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (403, "rateLimitExceeded"),
        (403, "userRateLimitExceeded"),
        (429, None),
        (429, "rateLimitExceeded"),
    ],
)
async def test_gmail_rate_limits_are_transient(call: Any, status: int, reason: str | None) -> None:
    with pytest.raises(ApiError) as raised:
        await call(_RawHttp(status, _gmail_error(status, reason)))
    error = raised.value
    assert (error.code, error.status_code, error.retryable) == ("PROVIDER_RATE_LIMITED", 429, True)
    assert not needs_reconnect(error) and _GOOGLE_TEXT not in error.message


@pytest.mark.parametrize("call", _GMAIL_CALLS, ids=_GMAIL_CALL_IDS)
@pytest.mark.parametrize(
    "body",
    [
        _gmail_error(403, "dailyLimitExceeded"),
        _gmail_error(403, "domainPolicy"),
        _gmail_error(403, "somethingNew"),
        _gmail_error(403, None),
        _gmail_error(403, "not a token with spaces"),
        _gmail_error(403, "x" * 200),
        json.dumps({"error": {"errors": "not a list"}}),
        json.dumps({"error": {"errors": [None]}}),
        "",
        "<html>forbidden</html>",
    ],
)
async def test_a_gmail_403_that_reconnecting_would_not_change_is_a_plain_refusal(
    call: Any, body: str
) -> None:
    with pytest.raises(ApiError) as raised:
        await call(_RawHttp(403, body))
    error = raised.value
    assert (error.code, error.retryable) == ("PROVIDER_REJECTED", False)
    assert not needs_reconnect(error) and error.details == {}
    assert error.message.startswith("Gmail refused to ")
    assert _GOOGLE_TEXT not in error.message


@pytest.mark.parametrize("call", _GMAIL_CALLS, ids=_GMAIL_CALL_IDS)
@pytest.mark.parametrize("status", [400, 404, 408, 500, 502, 503, 504])
async def test_gmail_failures_that_are_not_about_the_connection_are_transient(
    call: Any, status: int
) -> None:
    with pytest.raises(ApiError) as raised:
        await call(_RawHttp(status, _gmail_error(status, "backendError")))
    error = raised.value
    assert (error.code, error.status_code, error.retryable) == ("PROVIDER_UNAVAILABLE", 503, True)
    assert not needs_reconnect(error) and _GOOGLE_TEXT not in error.message


@pytest.mark.parametrize("call", _GMAIL_CALLS, ids=_GMAIL_CALL_IDS)
@pytest.mark.parametrize("reason", ["insufficientPermissions", "rateLimitExceeded", "authError"])
@pytest.mark.parametrize("status", [400, 404, 500, 503])
async def test_a_403_reason_means_nothing_on_any_other_status(
    call: Any, reason: str, status: int
) -> None:
    """The reasons that make a 403 a reconnect or a rate limit are read only from a 403."""
    with pytest.raises(ApiError) as raised:
        await call(_RawHttp(status, _gmail_error(status, reason)))
    error = raised.value
    assert (error.code, error.retryable) == ("PROVIDER_UNAVAILABLE", True)
    assert not needs_reconnect(error)


async def test_a_draft_that_cannot_reach_gmail_is_transient() -> None:
    with pytest.raises(ApiError) as raised:
        await _draft(_UnreachableHttp())
    assert (raised.value.code, raised.value.retryable) == ("PROVIDER_UNAVAILABLE", True)
    assert not needs_reconnect(raised.value)


@pytest.mark.parametrize("call", _GMAIL_CALLS, ids=_GMAIL_CALL_IDS)
async def test_the_refusal_messages_name_what_was_being_done(call: Any) -> None:
    doing = "create that draft" if call is _draft else "fetch that thread"
    with pytest.raises(ApiError) as refused:
        await call(_RawHttp(403, _gmail_error(403, "domainPolicy")))
    with pytest.raises(ApiError) as down:
        await call(_RawHttp(503, ""))
    assert refused.value.message == f"Gmail refused to {doing}."
    assert down.value.message == f"Gmail couldn't {doing} right now."
