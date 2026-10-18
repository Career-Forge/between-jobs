"""Secrets of the model-provider flow stay out of logs and error reports.

A key, an OAuth code and a PKCE verifier are credentials for as long as they live. The routes
never put one in a log line (tests/test_llm_credentials_routes.py proves it across the failures);
this file covers the safety nets under that: the log redactor knows the shapes of the newly
supported providers' keys and of the verifier, and the error tracker's scrubber drops a
verifier by name.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient
from provider_fakes import FakeDb

from between_jobs.api.app import app
from between_jobs.api.app_state import get_http_client, get_supabase
from between_jobs.api.auth import require_user_id
from between_jobs.api.logging_setup import JsonFormatter, format_exception_safely, redact
from between_jobs.api.sentry_scrub import before_send, is_secret_key

VERIFIER = "Zk3-._~" + "q" * 40
# Shapes of keys, made up: a prefix and a long random-looking tail.
KEYS = {
    "openai": "sk-proj-AbCdEfGh0123456789AbCdEfGh0123456789",
    "anthropic": "sk-ant-api03-AbCdEfGh0123456789AbCdEfGh0123456789",
    "openrouter": "sk-or-v1-0123456789abcdef0123456789abcdef0123456789abcdef",
    "xai": "xai-AbCdEfGh0123456789AbCdEfGh0123456789",
    "qwen": "sk-ws-AbCdEfGh0123456789AbCdEfGh0123456789",
    "qwen_plan": "sk-sp-AbCdEfGh0123456789AbCdEfGh0123456789",
    "deepseek": "sk-" + "0123456789abcdef" * 2,
    "gemini": "AIzaSyA-0123456789abcdefghijklmnopqrstu",
    "fireworks": "fw_AbCdEfGh0123456789AbCdEfGh",
    "groq": "gsk_AbCdEfGh0123456789AbCdEfGh0123456789",
}


@pytest.mark.parametrize("name", list(KEYS))
def test_the_redactor_knows_the_shape_of_each_providers_key(name: str) -> None:
    key = KEYS[name]

    assert key not in redact(f"upstream said: Incorrect API key provided: {key}. Check it.")
    assert key not in redact(f"api_key={key}")
    assert key not in redact(f'{{"secret": "{key}"}}')
    assert key not in redact(f"Authorization: Bearer {key}")


def test_a_header_that_carries_a_google_or_anthropic_key_is_redacted_by_name() -> None:
    assert "plainvalue" not in redact("x-goog-api-key: plainvalue")
    assert "plainvalue" not in redact("x-api-key=plainvalue")
    assert "plainvalue" not in redact("anthropic-api-key: plainvalue")


def test_a_code_verifier_is_redacted_by_name_in_any_spelling() -> None:
    for text in (
        f"code_verifier={VERIFIER}",
        f'"code_verifier": "{VERIFIER}"',
        f"code-verifier: {VERIFIER}",
        f"POST body {{'code_verifier': '{VERIFIER}'}}",
    ):
        assert VERIFIER not in redact(text), text


def test_the_error_tracker_drops_a_verifier_and_a_code_by_name() -> None:
    assert is_secret_key("code_verifier") and is_secret_key("codeVerifier")
    event = {
        "level": "error",
        "message": "boom",
        "extra": {
            "code_verifier": VERIFIER,
            "request": {"codeVerifier": VERIFIER, "secret": KEYS["openai"]},
            "detail": f"upstream echo {KEYS['anthropic']} and {KEYS['fireworks']}",
        },
        "request": {
            "url": f"https://api.example.test/credentials?code={VERIFIER}",
            "data": {"code_verifier": VERIFIER, "code": "abcdefgh12345678"},
            "headers": {"Authorization": f"Bearer {KEYS['openrouter']}", "x-goog-api-key": "g"},
            "query_string": f"code_verifier={VERIFIER}",
        },
        "exception": {
            "values": [
                {
                    "type": "RuntimeError",
                    "value": f"key {KEYS['xai']} {KEYS['qwen']} {KEYS['gemini']}",
                }
            ]
        },
    }

    scrubbed = before_send(event, {})

    text = json.dumps(scrubbed)
    for secret in (VERIFIER, "abcdefgh12345678", *KEYS.values()):
        assert secret not in text, secret


# -- an unexpected failure deep in the flow ----------------------------------------------------


class _ExplodingDb(FakeDb):
    """A store that fails in the middle of saving a key with the key in its message, the way a
    database driver or a decryption failure can quote what it was handling."""

    def rpc(self, fn: str, params: dict[str, Any]) -> Any:
        if fn == "encrypt_secret":
            raise RuntimeError(f"encrypt failed for {params['p_plaintext']}")
        return super().rpc(fn, params)


def test_a_crash_while_saving_a_key_does_not_put_the_key_in_the_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import httpx
    from provider_fakes import http_client, json_response

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")
    caplog.set_level(logging.DEBUG)
    key = KEYS["anthropic"]

    def upstream(request: httpx.Request) -> httpx.Response:
        return json_response(200, {"data": []})

    app.dependency_overrides[get_supabase] = lambda: _ExplodingDb()
    app.dependency_overrides[require_user_id] = lambda: "00000000-0000-0000-0000-00000000000a"
    app.dependency_overrides[get_http_client] = lambda: http_client(upstream)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/credentials", json={"service": "llm", "provider": "anthropic", "secret": key}
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert key not in response.text
    formatter = JsonFormatter()
    for record in caplog.records:
        assert key not in formatter.format(record)
        assert key not in record.getMessage()
        if record.exc_info and record.exc_info[1] is not None:
            assert key not in format_exception_safely(record.exc_info[1])
