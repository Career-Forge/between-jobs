"""The request `generate` sends to OpenRouter: every field and value, pinned.

Other providers get their own request shape (api/provider_registry.py), chosen from the host the
request goes to. OpenRouter must keep the payload it has always had: it is the default for every
caller, the reasoning-off field it takes is the one thing the other providers reject, and a
dropped field or a stray `null` would be a behaviour change nobody asked for. These tests pin the
fields and values, so any change to the OpenRouter request has to be made on purpose. The body is
compared as parsed JSON: the order the SDK writes the keys in depends on its version and is not
part of the request's meaning.
"""

from __future__ import annotations

import json
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


def _expected_with_cap(cap: int | None) -> dict[str, Any]:
    return {
        "messages": [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}],
        "model": "test/model",
        "max_tokens": cap,
        "reasoning": {"effort": "none"},
    }


async def test_the_openrouter_request_body_is_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = await _captured_request(monkeypatch, max_tokens=123)

    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key-not-real"
    assert json.loads(request.content) == _expected_with_cap(123)


async def test_an_uncapped_openrouter_request_still_sends_a_null_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = await _captured_request(monkeypatch)

    assert json.loads(request.content) == _expected_with_cap(None)


async def test_naming_the_openrouter_host_outright_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = await _captured_request(
        monkeypatch, max_tokens=123, base_url="https://openrouter.ai/api/v1"
    )

    assert json.loads(request.content) == _expected_with_cap(123)
