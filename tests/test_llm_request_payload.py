"""The exact request `generate` sends to OpenRouter, byte for byte.

Other providers get their own request shape (api/provider_registry.py), chosen from the host the
request goes to. OpenRouter must keep the payload it has always had: it is the default for every
caller, the reasoning-off field it takes is the one thing the other providers reject, and a
change in field order or a stray `null` would be a behaviour change nobody asked for. These
tests pin the bytes, so any change to the OpenRouter request has to be made on purpose.
"""

from __future__ import annotations

from typing import Any

import openai
import pytest

from between_jobs.api import llm_client

_COMPLETION = {
    "id": "gen-1",
    "object": "chat.completion",
    "created": 1,
    "model": "test/model",
    "choices": [
        {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}
    ],
}


def _http() -> Any:
    import openai._exceptions as sdk_exceptions

    names = vars(sdk_exceptions)
    return names.get("httpx2") or names["httpx"]


async def _captured_request(monkeypatch: pytest.MonkeyPatch, **options: Any) -> Any:
    http = _http()
    seen: list[Any] = []

    def handler(request: Any) -> Any:
        seen.append(request)
        return http.Response(200, json=_COMPLETION)

    def build(**sdk_options: Any) -> openai.AsyncOpenAI:
        sdk_options.pop("http_client", None)
        return openai.AsyncOpenAI(
            http_client=http.AsyncClient(transport=http.MockTransport(handler)), **sdk_options
        )

    monkeypatch.setattr(llm_client, "AsyncOpenAI", build)
    await llm_client.generate(
        api_key="test-key-not-real",
        model="test/model",
        base_url=options.pop("base_url", None),
        system_prompt="be brief",
        user_prompt="hi",
        **options,
    )
    [request] = seen
    return request


def _expected_with_cap(cap: int) -> bytes:
    return (
        b'{"messages":[{"role":"system","content":"be brief"},{"role":"user","content":"hi"}],'
        b'"model":"test/model","max_tokens":%d,"reasoning":{"effort":"none"}}' % cap
    )


async def test_the_openrouter_request_body_is_pinned_byte_for_byte(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = await _captured_request(monkeypatch, max_tokens=123)

    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key-not-real"
    assert request.content == _expected_with_cap(123)


async def test_an_uncapped_openrouter_request_still_sends_a_null_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = await _captured_request(monkeypatch)

    assert request.content == (
        b'{"messages":[{"role":"system","content":"be brief"},{"role":"user","content":"hi"}],'
        b'"model":"test/model","max_tokens":null,"reasoning":{"effort":"none"}}'
    )


async def test_naming_the_openrouter_host_outright_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = await _captured_request(
        monkeypatch, max_tokens=123, base_url="https://openrouter.ai/api/v1"
    )

    assert request.content == _expected_with_cap(123)
