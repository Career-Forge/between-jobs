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
    assert params["scope"] == ["https://www.googleapis.com/auth/gmail.compose"]


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


async def test_exchange_code_for_tokens_returns_both_tokens() -> None:
    http = _FakeHttp(body={"access_token": "at-1", "refresh_token": "rt-1"})

    result = await exchange_code_for_tokens(
        http,  # type: ignore[arg-type]
        code="auth-code",
        client_id="client-123",
        client_secret="secret-456",
        redirect_uri="http://localhost:8012/oauth/gmail/callback",
    )

    assert result["access_token"] == "at-1"
    assert result["refresh_token"] == "rt-1"
    _url, kwargs = http.requests[0]
    assert kwargs["data"]["grant_type"] == "authorization_code"


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


async def test_create_draft_returns_the_draft_id_and_never_calls_send() -> None:
    http = _FakeHttp(body={"id": "draft-abc"})

    draft_id = await create_draft(
        http,  # type: ignore[arg-type]
        access_token="at-1",
        to_email="jane.doe@acme.example",
        subject="Loved your PyData talk",
        body_text="Saw your talk -- would love to chat.",
    )

    assert draft_id == "draft-abc"
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
