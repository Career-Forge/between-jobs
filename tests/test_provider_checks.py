"""Key checks and model lists for every provider (api/provider_checks.py), over a mock transport.

The response bodies below are fixtures shaped from each provider's documentation, not claims
that a real account answers exactly so (no real key was used; see provider_registry.py). Model
ids in them are made up.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from provider_fakes import http_client, json_response

from between_jobs.api.errors import ApiError
from between_jobs.api.provider_checks import (
    MAX_MODELS_RETURNED,
    Inconclusive,
    check_key,
    list_models,
    normalise_models,
)
from between_jobs.api.provider_registry import PROVIDERS

KEY = "sk-test-0123456789abcdef0123456789abcdef"
QWEN_BASE = "https://maas.qwencloudapi.com/compatible-mode/v1"
GROQ_BASE = "https://api.groq.com/openai/v1"


def _base(provider: str) -> str | None:
    return {"qwen": QWEN_BASE, "custom": GROQ_BASE}.get(provider)


class Recorder:
    def __init__(self, response: httpx.Response | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.response = response or json_response(200, {"data": []})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response


# -- what each check sends -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "url", "headers", "absent"),
    [
        (
            "openai",
            "https://api.openai.com/v1/models",
            {"authorization": f"Bearer {KEY}"},
            ["x-api-key"],
        ),
        (
            "anthropic",
            "https://api.anthropic.com/v1/models?limit=1",
            {"x-api-key": KEY, "anthropic-version": "2023-06-01"},
            ["authorization"],
        ),
        (
            "gemini",
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
            {"x-goog-api-key": KEY},
            ["authorization"],
        ),
        ("xai", "https://api.x.ai/v1/models", {"authorization": f"Bearer {KEY}"}, []),
        (
            "deepseek",
            "https://api.deepseek.com/user/balance",
            {"authorization": f"Bearer {KEY}"},
            [],
        ),
        ("qwen", f"{QWEN_BASE}/models", {"authorization": f"Bearer {KEY}"}, []),
        ("custom", f"{GROQ_BASE}/models", {"authorization": f"Bearer {KEY}"}, []),
    ],
)
async def test_each_key_check_is_the_providers_free_call_with_the_key_in_a_header(
    provider: str, url: str, headers: dict[str, str], absent: list[str]
) -> None:
    recorder = Recorder(json_response(200, {"data": [], "models": [], "is_available": True}))

    async with http_client(recorder) as http:
        await check_key(http, PROVIDERS[provider], _base(provider), KEY)

    [request] = recorder.requests
    assert request.method == "GET" and str(request.url) == url
    for name, value in headers.items():
        assert request.headers[name] == value
    for name in absent:
        assert name not in request.headers
    assert KEY not in str(request.url)
    assert request.content == b""


async def test_the_deepseek_balance_decides_not_only_the_status() -> None:
    spec = PROVIDERS["deepseek"]
    async with http_client(lambda r: json_response(200, {"is_available": False})) as http:
        with pytest.raises(ApiError) as empty:
            await check_key(http, spec, None, KEY)
    async with http_client(lambda r: json_response(200, {"is_available": "yes"})) as http:
        with pytest.raises(ApiError) as odd:
            await check_key(http, spec, None, KEY)
    async with http_client(lambda r: json_response(200, {"is_available": True})) as http:
        await check_key(http, spec, None, KEY)

    assert (empty.value.code, empty.value.details["reason"]) == (
        "PROVIDER_REJECTED",
        "out_of_credit",
    )
    assert (odd.value.code, odd.value.details["reason"]) == (
        "PROVIDER_UNAVAILABLE",
        "empty_response",
    )


# -- how a failure is classified ------------------------------------------------------------------

_REJECTED = ("PROVIDER_REJECTED", "key_rejected", False)
_NO_CREDIT = ("PROVIDER_REJECTED", "out_of_credit", False)
_THROTTLED = ("PROVIDER_RATE_LIMITED", "rate_limited", True)
_DOWN = ("PROVIDER_UNAVAILABLE", "provider_error", True)
_EMPTY = ("PROVIDER_UNAVAILABLE", "empty_response", True)


@pytest.mark.parametrize(
    ("provider", "status", "body", "expected"),
    [
        ("openai", 401, {"error": {"code": "invalid_api_key"}}, _REJECTED),
        ("openai", 403, None, _REJECTED),
        ("openai", 402, None, _NO_CREDIT),
        ("openai", 429, {"error": {"code": "credit_balance_exhausted"}}, _NO_CREDIT),
        ("openai", 429, {"code": "insufficient_quota"}, _NO_CREDIT),
        ("openai", 429, {"code": "organization_spend_limit_exceeded"}, _NO_CREDIT),
        ("openai", 429, {"code": "slow_down"}, _THROTTLED),
        ("openai", 429, None, _THROTTLED),
        ("openai", 500, None, _DOWN),
        ("openai", 503, {"code": "server_is_overloaded"}, _DOWN),
        ("openai", 404, None, _DOWN),  # a free route that is not there is the provider's fault
        ("openai", 400, None, _DOWN),  # a bare GET has nothing to get wrong; 400 is not the key
        ("openai", 302, None, _DOWN),
        ("xai", 400, None, _REJECTED),  # xAI files a bad key under 400 as well
        ("xai", 401, None, _REJECTED),
        ("gemini", 400, None, _REJECTED),  # INVALID_ARGUMENT on a malformed key
        ("gemini", 403, None, _REJECTED),
        ("gemini", 402, None, _NO_CREDIT),  # prepaid credits depleted
        ("gemini", 429, None, _THROTTLED),
        ("anthropic", 401, {"type": "authentication_error"}, _REJECTED),
        # a key that works on several workspaces, used without `anthropic-workspace-id`
        (
            "anthropic",
            400,
            {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "anthropic-workspace-id is required when authenticating with an "
                    "identity-linked API key",
                },
            },
            _REJECTED,
        ),
        (
            "gemini",
            400,
            {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "API key not valid"}},
            _REJECTED,
        ),
        # billing not enabled, or a region without the free tier: the account, not the key
        (
            "gemini",
            400,
            {"error": {"code": 400, "status": "FAILED_PRECONDITION", "message": "free tier"}},
            ("PROVIDER_REJECTED", "request_rejected", False),
        ),
        ("anthropic", 402, {"type": "billing_error"}, _NO_CREDIT),
        ("anthropic", 429, {"details": {"error_code": "enforced_spend_limit_reached"}}, _NO_CREDIT),
        ("anthropic", 529, None, _DOWN),
        ("deepseek", 401, None, _REJECTED),
        ("deepseek", 402, None, _NO_CREDIT),
        ("deepseek", 503, None, _DOWN),
        ("qwen", 401, {"code": "invalid_api_key"}, _REJECTED),
        ("qwen", 400, {"code": "Arrearage"}, _NO_CREDIT),
        ("qwen", 403, {"code": "AllocationQuota.FreeTierOnly"}, _NO_CREDIT),
        ("qwen", 429, {"code": "Throttling.RateQuota"}, _THROTTLED),
        ("custom", 401, None, _REJECTED),
        ("openai", 200, "<html>captive portal</html>", _EMPTY),
        ("openai", 200, [1, 2], _EMPTY),
    ],
)
async def test_a_failed_check_is_classified_with_the_existing_taxonomy(
    provider: str, status: int, body: Any, expected: tuple[str, str, bool]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(body, str):
            return httpx.Response(status, content=body.encode())
        return json_response(status, body)

    async with http_client(handler) as http:
        with pytest.raises(ApiError) as caught:
            await check_key(http, PROVIDERS[provider], _base(provider), KEY)

    error = caught.value
    assert (error.code, error.details["reason"], error.retryable) == expected
    assert error.details["provider"] == provider


async def test_a_wrong_platform_hint_is_added_for_qwen_only() -> None:
    async with http_client(lambda r: json_response(401, {"code": "invalid_api_key"})) as http:
        with pytest.raises(ApiError) as qwen:
            await check_key(http, PROVIDERS["qwen"], QWEN_BASE, KEY)
        with pytest.raises(ApiError) as openai:
            await check_key(http, PROVIDERS["openai"], None, KEY)

    assert "platform and region" in qwen.value.message
    assert "platform" not in openai.value.message
    assert qwen.value.message.startswith("Qwen Cloud rejected that key.")


async def test_a_workspace_key_hint_is_added_for_anthropic_only() -> None:
    async with http_client(lambda r: json_response(400, {"error": {"type": "x"}})) as http:
        with pytest.raises(ApiError) as anthropic:
            await check_key(http, PROVIDERS["anthropic"], None, KEY)
        with pytest.raises(ApiError) as xai:
            await check_key(http, PROVIDERS["xai"], None, KEY)

    assert anthropic.value.message.startswith("Anthropic rejected that key.")
    assert "single workspace" in anthropic.value.message
    assert "workspace" not in xai.value.message


async def test_a_gemini_account_problem_is_not_called_a_rejected_key() -> None:
    body = {"error": {"code": 400, "status": "FAILED_PRECONDITION", "message": "region"}}
    async with http_client(lambda r: json_response(400, body)) as http:
        with pytest.raises(ApiError) as caught:
            await check_key(http, PROVIDERS["gemini"], None, KEY)

    assert "rejected that key" not in caught.value.message
    assert "account" in caught.value.message
    assert caught.value.settings_path == "/profile/integrations"


@pytest.mark.parametrize("provider", ["qwen", "custom"])
@pytest.mark.parametrize("status", [404, 405])
async def test_a_best_effort_provider_without_a_list_route_is_inconclusive(
    provider: str, status: int
) -> None:
    async with http_client(lambda r: json_response(status, {"error": "no such route"})) as http:
        with pytest.raises(Inconclusive):
            await check_key(http, PROVIDERS[provider], _base(provider), KEY)


async def test_a_best_effort_provider_that_refuses_the_key_is_not_inconclusive() -> None:
    async with http_client(lambda r: json_response(401, None)) as http:
        with pytest.raises(ApiError) as caught:
            await check_key(http, PROVIDERS["qwen"], QWEN_BASE, KEY)

    assert caught.value.details["reason"] == "key_rejected"


async def test_nothing_the_provider_says_reaches_the_error() -> None:
    echo = {"error": {"message": f"Incorrect API key provided: {KEY}.", "code": "invalid_api_key"}}
    async with http_client(lambda r: json_response(401, echo)) as http:
        with pytest.raises(ApiError) as caught:
            await check_key(http, PROVIDERS["openai"], None, KEY)

    error = caught.value
    assert KEY not in repr(error.__dict__) and "Incorrect" not in repr(error.__dict__)
    assert error.settings_path == "/profile/integrations"


@pytest.mark.parametrize(
    ("raised", "reason", "retryable"),
    [
        (httpx.ConnectError("refused"), "unreachable", True),
        (httpx.ReadTimeout("slow"), "timeout", True),
    ],
)
async def test_an_unreachable_or_slow_provider_is_retryable(
    raised: Exception, reason: str, retryable: bool
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise raised

    async with http_client(handler) as http:
        with pytest.raises(ApiError) as caught:
            await check_key(http, PROVIDERS["xai"], None, KEY)

    assert caught.value.code == "PROVIDER_UNAVAILABLE"
    assert (caught.value.details["reason"], caught.value.retryable) == (reason, retryable)


# -- listing models, per provider -----------------------------------------------------------------


async def _listed(provider: str, body: Any, base: str | None = None) -> Any:
    async with http_client(lambda r: json_response(200, body)) as http:
        return await list_models(http, PROVIDERS[provider], base or _base(provider), KEY)


def _ids(result: Any) -> list[str]:
    return [m.id for m in result.models]


async def test_openai_lists_chat_models_only_newest_first_without_the_retired() -> None:
    result = await _listed(
        "openai",
        {
            "object": "list",
            "data": [
                {"id": "chat-small", "object": "model", "created": 1_700_000_000, "owned_by": "o"},
                {"id": "chat-big", "object": "model", "created": 1_760_000_000, "owned_by": "o"},
                {"id": "text-embedding-x", "created": 1_760_000_100},
                {"id": "tts-hd-x", "created": 1_760_000_200},
                {"id": "whisper-x", "created": 1_760_000_300},
                {"id": "dall-e-x", "created": 1_760_000_400},
                {"id": "gpt-image-x", "created": 1_760_000_500},
                {"id": "omni-moderation-x", "created": 1_760_000_600},
                {"id": "chat-retired", "created": 1_750_000_000, "shutdown_date": "2020-01-01"},
                {"id": "chat-retiring", "created": 1_740_000_000, "shutdown_date": "2999-01-01"},
            ],
        },
    )

    assert _ids(result) == ["chat-big", "chat-retiring", "chat-small"]
    assert result.listing == "live" and result.truncated is False
    assert result.models[0].as_json() == {
        "id": "chat-big",
        "name": "chat-big",  # OpenAI sends no display name
        "context_length": None,  # nor a context length
        "created": 1_760_000_000,
    }


async def test_anthropic_lists_names_context_and_rfc3339_dates() -> None:
    result = await _listed(
        "anthropic",
        {
            "data": [
                {
                    "type": "model",
                    "id": "old-model",
                    "display_name": "Old Model",
                    "created_at": "2025-01-01T00:00:00Z",
                    "max_input_tokens": 200000,
                    "lifecycle": "deprecated",
                },
                {
                    "type": "model",
                    "id": "new-model",
                    "display_name": "New Model",
                    "created_at": "2026-06-01T00:00:00+00:00",
                    "max_input_tokens": 1000000,
                    "lifecycle": "active",
                },
                {"id": "gone-model", "created_at": "2024-01-01T00:00:00Z", "lifecycle": "retired"},
            ],
            "has_more": False,
        },
    )

    assert _ids(result) == ["new-model", "old-model"]
    assert result.models[0].as_json() == {
        "id": "new-model",
        "name": "New Model",
        "context_length": 1_000_000,
        "created": 1_780_272_000,
    }


async def test_gemini_uses_the_native_list_and_keeps_models_that_can_generate() -> None:
    recorder = Recorder(
        json_response(
            200,
            {
                "models": [
                    {
                        "name": "models/chatty-1",
                        "displayName": "Chatty One",
                        "inputTokenLimit": 1048576,
                        "supportedGenerationMethods": ["generateContent", "countTokens"],
                    },
                    {
                        "name": "models/embed-1",
                        "displayName": "Embedder",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                    {
                        "name": "models/speech-tts-1",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {"name": "models/no-methods"},
                ],
                "nextPageToken": "more",
            },
        )
    )
    async with http_client(recorder) as http:
        result = await list_models(http, PROVIDERS["gemini"], None, KEY)

    [request] = recorder.requests
    assert str(request.url) == (
        "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000"
    )
    assert request.headers["x-goog-api-key"] == KEY and KEY not in str(request.url)
    assert result.models[0].as_json() == {
        "id": "chatty-1",
        "name": "Chatty One",
        "context_length": 1_048_576,
        "created": None,
    }
    assert _ids(result) == ["chatty-1"]


async def test_xai_lists_text_models_with_context_and_dates() -> None:
    result = await _listed(
        "xai",
        {
            "data": [
                {"id": "grok-x", "created": 1_780_000_000, "context_length": 500000, "aliases": []},
                {"id": "grok-imagine-image-x", "created": 1_790_000_000},
                {"id": "grok-video-x", "created": 1_790_000_001},
            ]
        },
    )

    assert [m.as_json() for m in result.models] == [
        {"id": "grok-x", "name": "grok-x", "context_length": 500000, "created": 1_780_000_000}
    ]


async def test_xai_language_models_may_name_their_list_differently() -> None:
    result = await _listed("xai", {"models": [{"id": "grok-y", "created": 5}]})

    assert _ids(result) == ["grok-y"]


async def test_deepseek_lists_names_and_context_without_dates() -> None:
    result = await _listed(
        "deepseek",
        {
            "object": "list",
            "data": [
                {
                    "id": "ds-b",
                    "object": "model",
                    "name": "DS B",
                    "context_window": 1048576,
                    "max_output_tokens": 393216,
                },
                {"id": "ds-a", "object": "model", "context_window": 65536},
            ],
        },
    )

    assert [m.as_json() for m in result.models] == [
        {"id": "ds-a", "name": "ds-a", "context_length": 65536, "created": None},
        {"id": "ds-b", "name": "DS B", "context_length": 1048576, "created": None},
    ]


async def test_openrouter_lists_text_models_with_context_and_dates() -> None:
    result = await _listed(
        "openrouter",
        {
            "data": [
                {
                    "id": "vendor/text-model:free",
                    "name": "Vendor: Text",
                    "created": 1_770_000_000,
                    "context_length": 128000,
                    "architecture": {"output_modalities": ["text"]},
                },
                {
                    "id": "vendor/image-model",
                    "created": 1_780_000_000,
                    "architecture": {"output_modalities": ["image"]},
                },
                {"id": "vendor/no-architecture", "created": 1_760_000_000},
            ]
        },
    )

    assert _ids(result) == ["vendor/text-model:free", "vendor/no-architecture"]
    assert result.models[0].name == "Vendor: Text"


@pytest.mark.parametrize("provider", ["qwen", "custom"])
async def test_a_best_effort_list_is_read_like_openai_and_missing_is_not_an_error(
    provider: str,
) -> None:
    live = await _listed(provider, {"data": [{"id": "some-model", "created": 9}]})
    assert (live.listing, _ids(live)) == ("live", ["some-model"])

    for status in (404, 405):

        def gone_handler(request: httpx.Request, status: int = status) -> httpx.Response:
            return json_response(status, None)

        async with http_client(gone_handler) as http:
            gone = await list_models(http, PROVIDERS[provider], _base(provider), KEY)
        assert (gone.listing, gone.models, gone.truncated) == ("unavailable", [], False)


async def test_a_provider_whose_list_is_documented_does_not_get_that_leniency() -> None:
    async with http_client(lambda r: json_response(404, None)) as http:
        with pytest.raises(ApiError) as caught:
            await list_models(http, PROVIDERS["openai"], None, KEY)

    assert caught.value.details["reason"] == "provider_error"


async def test_a_list_failure_is_classified_like_a_key_check_failure() -> None:
    async with http_client(lambda r: json_response(401, None)) as http:
        with pytest.raises(ApiError) as caught:
            await list_models(http, PROVIDERS["anthropic"], None, KEY)

    assert (caught.value.code, caught.value.details["reason"]) == (
        "PROVIDER_REJECTED",
        "key_rejected",
    )


# -- hostile or broken lists ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider", ["openai", "anthropic", "gemini", "xai", "deepseek", "openrouter"]
)
@pytest.mark.parametrize("body", [None, [], "text", 7, {}, {"data": None}, {"data": {"a": 1}}])
async def test_an_answer_without_a_list_is_a_provider_failure_not_a_crash(
    provider: str, body: Any
) -> None:
    async with http_client(lambda r: json_response(200, body)) as http:
        with pytest.raises(ApiError) as caught:
            await list_models(http, PROVIDERS[provider], None, KEY)

    assert caught.value.details["reason"] == "empty_response"


def test_entries_that_are_not_models_are_skipped_not_fatal() -> None:
    spec = PROVIDERS["openai"]
    hostile: list[Any] = [
        None,
        7,
        "str",
        [],
        {},
        {"id": None},
        {"id": 5},
        {"id": ""},
        {"id": " leading-space"},
        {"id": "has space"},
        {"id": "new\nline"},
        {"id": "x" * 201},
        {"id": "unicodé"},
        {"id": "../../etc/passwd"[:0] + "a b"},
        {"id": "ok-one", "created": True},
        {"id": "ok-two", "created": -5},
        {"id": "ok-three", "created": 10**20},
        {"id": "ok-four", "created": "not a date"},
        {"id": "ok-five", "created": {"nested": [1]}},
    ]

    models, truncated = normalise_models(spec, {"data": hostile})

    assert {m.id for m in models} == {"ok-one", "ok-two", "ok-three", "ok-four", "ok-five"}
    assert all(m.created is None for m in models)
    assert truncated is False


def test_a_huge_name_or_context_is_dropped_not_kept() -> None:
    spec = PROVIDERS["anthropic"]

    [model], _ = normalise_models(
        spec,
        {
            "data": [
                {
                    "id": "m",
                    "display_name": "N" * 5000,
                    "max_input_tokens": 10**30,
                    "created_at": "x" * 500,
                }
            ]
        },
    )

    assert (model.name, model.context_length, model.created) == ("m", None, None)


def test_the_list_is_capped_deduplicated_and_says_it_was_cut() -> None:
    spec = PROVIDERS["openai"]
    data = [
        {"id": f"chat-{n:05d}", "created": 1_000_000 + n} for n in range(MAX_MODELS_RETURNED + 40)
    ]
    data += [{"id": "chat-00000", "created": 1_000_000}] * 3  # duplicates

    models, truncated = normalise_models(spec, {"data": data})

    assert len(models) == MAX_MODELS_RETURNED and truncated is True
    assert models[0].id == f"chat-{MAX_MODELS_RETURNED + 39:05d}"  # newest first
    assert len({m.id for m in models}) == len(models)


def test_a_list_far_beyond_the_cap_is_only_partly_scanned() -> None:
    spec = PROVIDERS["deepseek"]
    data = [{"id": f"m{n}"} for n in range(20_000)]

    models, truncated = normalise_models(spec, {"data": data})

    assert len(models) == MAX_MODELS_RETURNED and truncated is True


def test_models_without_dates_sort_after_dated_ones_then_by_id() -> None:
    spec = PROVIDERS["deepseek"]

    models, _ = normalise_models(spec, {"data": [{"id": "b"}, {"id": "a"}]})
    assert [m.id for m in models] == ["a", "b"]

    spec = PROVIDERS["openrouter"]
    models, _ = normalise_models(
        spec,
        {"data": [{"id": "undated"}, {"id": "old", "created": 5}, {"id": "new", "created": 9}]},
    )
    assert [m.id for m in models] == ["new", "old", "undated"]


async def test_a_list_over_the_byte_limit_is_a_failure_not_a_memory_problem() -> None:
    big = httpx.Response(200, content=b"[" + b"0," * (1_200_000) + b"0]")

    async with http_client(lambda r: big) as http:
        with pytest.raises(ApiError) as caught:
            await list_models(http, PROVIDERS["openai"], None, KEY)

    assert caught.value.details["reason"] == "response_too_large"
    assert caught.value.retryable is False
