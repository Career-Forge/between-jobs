"""`llm_client.generate` across providers: the request each one gets, the fallback when a model
refuses the first reasoning setting, the destination checks, and the failure reasons.

The SDK is the real one over a mock transport (tests/provider_fakes.py), so what is asserted is
the request that would go on the wire. OpenRouter's bytes are pinned separately, in
tests/test_llm_request_payload.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from openai import DefaultAsyncHttpxClient
from provider_fakes import SDK_HTTP, SdkWire, completion, sdk_error

from between_jobs.api import llm_client, provider_http
from between_jobs.api.errors import ApiError
from between_jobs.api.llm_client import generate, request_fields
from between_jobs.api.provider_checks import PROBE_MAX_TOKENS, probe_model
from between_jobs.api.provider_registry import dialect_for_base_url

KEY = "sk-test-0123456789abcdef0123456789abcdef"


async def _generate(base_url: str | None, **options: Any) -> Any:
    defaults: dict[str, Any] = {
        "api_key": KEY,
        "model": "fixture-model",
        "base_url": base_url,
        "system_prompt": "be brief",
        "user_prompt": "hi",
        "max_tokens": 123,
        "temperature": 0.3,
    }
    return await generate(**{**defaults, **options})


def _wire(monkeypatch: pytest.MonkeyPatch, *answers: Any) -> SdkWire:
    """Answers the requests in order; the last answer repeats."""
    queue = list(answers)

    def handler(_request: Any) -> Any:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return SdkWire(handler).install(monkeypatch)


_MESSAGES = [
    {"role": "system", "content": "be brief"},
    {"role": "user", "content": "hi"},
]


@pytest.mark.parametrize(
    ("base_url", "url", "expected"),
    [
        (
            "https://api.openai.com/v1",
            "https://api.openai.com/v1/chat/completions",
            {"max_completion_tokens": 123, "reasoning_effort": "none"},
        ),
        (
            "https://api.x.ai/v1",
            "https://api.x.ai/v1/chat/completions",
            {"max_tokens": 123, "reasoning_effort": "low"},
        ),
        (
            "https://api.anthropic.com/v1/",
            "https://api.anthropic.com/v1/chat/completions",
            {"max_tokens": 123, "thinking": {"type": "disabled"}},
        ),
        (
            "https://generativelanguage.googleapis.com/v1beta/openai/",
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            {"max_tokens": 123, "reasoning_effort": "minimal"},
        ),
        (
            "https://api.deepseek.com",
            "https://api.deepseek.com/chat/completions",
            {"max_tokens": 123, "temperature": 0.3, "thinking": {"type": "disabled"}},
        ),
        (
            "https://maas.qwencloudapi.com/compatible-mode/v1",
            "https://maas.qwencloudapi.com/compatible-mode/v1/chat/completions",
            {"max_completion_tokens": 123, "temperature": 0.3, "enable_thinking": False},
        ),
        (
            "https://ws-abc123.us-east-1.maas.aliyuncs.com/compatible-mode/v1",
            "https://ws-abc123.us-east-1.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
            {"max_completion_tokens": 123, "temperature": 0.3, "enable_thinking": False},
        ),
        (
            "https://api.groq.com/openai/v1",
            "https://api.groq.com/openai/v1/chat/completions",
            {"max_tokens": 123, "temperature": 0.3},
        ),
        (
            "https://api.fireworks.ai/inference/v1",
            "https://api.fireworks.ai/inference/v1/chat/completions",
            {"max_tokens": 123, "temperature": 0.3},
        ),
        (
            "https://api.cohere.ai/compatibility/v1",
            "https://api.cohere.ai/compatibility/v1/chat/completions",
            {"max_tokens": 123, "temperature": 0.3, "reasoning_effort": "none"},
        ),
        (
            "https://llm.example.test/v1",  # nobody described this host
            "https://llm.example.test/v1/chat/completions",
            {"max_tokens": 123},
        ),
    ],
)
async def test_the_first_request_to_each_provider_is_in_its_own_dialect(
    monkeypatch: pytest.MonkeyPatch, base_url: str, url: str, expected: dict[str, Any]
) -> None:
    wire = _wire(monkeypatch, completion())

    await _generate(base_url)

    [request] = wire.requests
    assert str(request.url) == url
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert wire.bodies() == [{"model": "fixture-model", "messages": _MESSAGES, **expected}]


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.openai.com/v1",
        "https://api.x.ai/v1",
        "https://api.anthropic.com/v1/",
        "https://api.deepseek.com",
        "https://api.groq.com/openai/v1",
        "https://llm.example.test/v1",
    ],
)
async def test_an_uncapped_request_gets_a_ceiling_except_for_openrouter(
    monkeypatch: pytest.MonkeyPatch, base_url: str
) -> None:
    wire = _wire(monkeypatch, completion())

    await _generate(base_url, max_tokens=None, temperature=None)

    [body] = wire.bodies()
    field = dialect_for_base_url(base_url).token_field
    assert body[field] == llm_client.UNCAPPED_CEILING_TOKENS == 8192
    assert "temperature" not in body


async def test_an_uncapped_openrouter_request_keeps_its_null_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = _wire(monkeypatch, completion())

    await _generate(None, max_tokens=None, temperature=None)

    assert wire.bodies()[0]["max_tokens"] is None


async def test_a_temperature_is_dropped_where_the_provider_refuses_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for base_url in (
        "https://api.openai.com/v1",
        "https://api.anthropic.com/v1/",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "https://api.x.ai/v1",
        "https://llm.example.test/v1",
    ):
        wire = _wire(monkeypatch, completion())
        await _generate(base_url, temperature=0.9)
        assert "temperature" not in wire.bodies()[0], base_url


def test_request_fields_is_the_whole_decision_in_one_pure_function() -> None:
    openai = dialect_for_base_url("https://api.openai.com/v1")

    assert request_fields(openai, 0, max_tokens=50, temperature=0.2) == (
        {"max_completion_tokens": 50},
        {"reasoning_effort": "none"},
    )
    assert request_fields(openai, 2, max_tokens=None, temperature=None) == ({}, None)
    openrouter = dialect_for_base_url(None)
    assert request_fields(openrouter, 0, max_tokens=None, temperature=None) == (
        {"max_tokens": None},
        {"reasoning": {"effort": "none"}},
    )
    assert request_fields(openrouter, 0, max_tokens=7, temperature=0.4) == (
        {"max_tokens": 7, "temperature": 0.4},
        {"reasoning": {"effort": "none"}},
    )


# -- falling back when a model refuses the reasoning setting --------------------------------------

_SHAPE = {"error": {"message": "Unsupported parameter", "type": "invalid_request_error"}}


async def test_a_model_that_refuses_the_setting_gets_the_next_one_then_the_plain_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = _wire(monkeypatch, sdk_error(400, _SHAPE), sdk_error(400, _SHAPE), completion("done"))

    response = await _generate("https://api.openai.com/v1", model="flagship-fixture")

    assert response.content == "done"
    assert [b.get("reasoning_effort", "-") for b in wire.bodies()] == ["none", "low", "-"]
    assert all(b["max_completion_tokens"] == 123 for b in wire.bodies())  # the cap holds


async def test_what_worked_is_remembered_for_that_model_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = _wire(monkeypatch, sdk_error(422, _SHAPE), completion())
    await _generate("https://api.openai.com/v1", model="flagship-fixture")
    assert len(wire.requests) == 2

    wire.requests.clear()
    await _generate("https://api.openai.com/v1", model="flagship-fixture")
    assert [b.get("reasoning_effort") for b in wire.bodies()] == ["low"]  # no wasted 400

    wire.requests.clear()
    await _generate("https://api.openai.com/v1", model="small-fixture")
    assert [b.get("reasoning_effort") for b in wire.bodies()] == ["none"]  # another model


async def test_the_fallback_never_makes_more_requests_than_the_dialect_has_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for base_url, attempts in (
        ("https://api.openai.com/v1", 3),
        ("https://api.anthropic.com/v1/", 3),
        ("https://api.deepseek.com", 2),
        ("https://llm.example.test/v1", 1),
    ):
        wire = _wire(monkeypatch, sdk_error(400, _SHAPE))
        with pytest.raises(ApiError) as caught:
            await _generate(base_url)
        assert len(wire.requests) == attempts, base_url
        assert caught.value.details["reason"] == "request_rejected"


async def test_openrouter_is_never_asked_twice_for_a_rejected_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = _wire(monkeypatch, sdk_error(400, _SHAPE))

    with pytest.raises(ApiError) as caught:
        await _generate(None)

    assert len(wire.requests) == 1
    assert caught.value.details["reason"] == "request_rejected"


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (401, _SHAPE, "key_rejected"),
        (402, None, "out_of_credit"),
        (404, None, "model_not_found"),
        (400, {"error": {"code": "model_not_found"}}, "model_not_found"),
        (410, None, "model_retired"),
        (400, {"error": {"code": "Arrearage"}}, "out_of_credit"),
        (403, {"error": {"code": "AllocationQuota.FreeTierOnly"}}, "out_of_credit"),
    ],
)
async def test_an_answer_that_is_not_about_the_shape_is_not_retried_in_another_shape(
    monkeypatch: pytest.MonkeyPatch, status: int, body: Any, reason: str
) -> None:
    wire = _wire(monkeypatch, sdk_error(status, body))

    with pytest.raises(ApiError) as caught:
        await _generate("https://api.openai.com/v1")

    assert len(wire.requests) == 1
    assert caught.value.details["reason"] == reason


# -- failure reasons beyond OpenRouter's ----------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "body", "reason", "code", "retryable"),
    [
        (404, None, "model_not_found", "PROVIDER_REJECTED", False),
        (
            404,
            {"error": {"code": "model_not_found", "message": f"no {KEY}"}},
            "model_not_found",
            "PROVIDER_REJECTED",
            False,
        ),
        (410, None, "model_retired", "PROVIDER_REJECTED", False),
        (
            400,
            {"error": {"code": "model_decommissioned"}},
            "model_retired",
            "PROVIDER_REJECTED",
            False,
        ),
        (
            429,
            {"error": {"code": "insufficient_quota", "type": "insufficient_quota"}},
            "out_of_credit",
            "PROVIDER_REJECTED",
            False,
        ),
        (
            429,
            {"error": {"details": {"error_code": "enforced_spend_limit_reached"}}},
            "out_of_credit",
            "PROVIDER_REJECTED",
            False,
        ),
        (401, None, "key_rejected", "PROVIDER_REJECTED", False),
        (500, None, "provider_error", "PROVIDER_UNAVAILABLE", True),
    ],
)
async def test_a_failed_completion_on_another_provider_is_classified(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    body: Any,
    reason: str,
    code: str,
    retryable: bool,
) -> None:
    _wire(monkeypatch, sdk_error(status, body))

    with pytest.raises(ApiError) as caught:
        await _generate("https://api.openai.com/v1")

    error = caught.value
    assert (error.code, error.details["reason"], error.retryable) == (code, reason, retryable)
    assert KEY not in repr(error.__dict__) and "no sk-" not in repr(error.__dict__)


async def test_a_429_that_is_only_a_throttle_stays_a_throttle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(
        monkeypatch, sdk_error(429, {"error": {"code": "slow_down"}}, {"x-should-retry": "false"})
    )

    with pytest.raises(ApiError) as caught:
        await _generate("https://api.openai.com/v1")

    assert (caught.value.code, caught.value.details["reason"]) == (
        "PROVIDER_RATE_LIMITED",
        "rate_limited",
    )


async def test_openrouters_own_404_is_still_the_general_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch, sdk_error(404, {"code": 404, "message": "No endpoints found"}))

    with pytest.raises(ApiError) as caught:
        await _generate(None)

    assert caught.value.details["reason"] == "request_rejected"


# -- where a request may go, and no redirect ------------------------------------------------------


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.openai.com/v1",
        "https://localhost/v1",
        "https://127.0.0.1/v1",
        "https://169.254.169.254/latest",
        "https://10.0.0.5/v1",
        "https://[::1]/v1",
        "https://user@api.openai.com/v1",
        "https://api.openai.com:8443/v1",
        "https://printer.local/v1",
    ],
)
async def test_a_destination_that_is_not_a_public_dns_name_is_refused_before_any_request(
    monkeypatch: pytest.MonkeyPatch, base_url: str
) -> None:
    wire = _wire(monkeypatch, completion())

    with pytest.raises(ApiError) as caught:
        await _generate(base_url)

    assert caught.value.code == "INVALID_INPUT"
    assert wire.requests == [] and wire.built == []


async def test_a_name_that_resolves_to_a_private_address_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = _wire(monkeypatch, completion())

    async def private(_host: str) -> list[str]:
        return ["10.1.2.3"]

    monkeypatch.setattr(provider_http, "_resolve", private)

    with pytest.raises(ApiError) as caught:
        await _generate("https://api.openai.com/v1")

    assert caught.value.code == "INVALID_INPUT" and wire.requests == []


async def test_an_unresolvable_name_is_an_unreachable_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = _wire(monkeypatch, completion())

    async def nothing(_host: str) -> list[str]:
        raise OSError("no such host")

    monkeypatch.setattr(provider_http, "_resolve", nothing)

    with pytest.raises(ApiError) as caught:
        await _generate("https://api.openai.com/v1")

    assert caught.value.details["reason"] == "unreachable" and wire.requests == []


async def test_the_default_openrouter_address_is_not_looked_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = _wire(monkeypatch, completion())

    async def never(_host: str) -> list[str]:
        raise AssertionError("OpenRouter's own address does not need a DNS check")

    monkeypatch.setattr(provider_http, "_resolve", never)

    await _generate(None)
    await _generate(llm_client.OPENROUTER_BASE_URL)

    assert len(wire.requests) == 2


async def test_the_client_is_built_to_follow_no_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    wire = _wire(monkeypatch, completion())

    await _generate("https://api.openai.com/v1")

    [client] = wire.http_clients
    assert client is not None and client.follow_redirects is False
    assert client.is_closed


@pytest.mark.parametrize("status", [301, 302, 307, 308])
async def test_a_redirect_from_a_provider_is_not_followed(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """The real client `generate` builds, over a mock transport: a provider that answers a
    request with a redirect to another host gets no second request, and the key goes nowhere
    else."""
    seen: list[str] = []

    def handler(request: Any) -> Any:
        seen.append(str(request.url))
        if request.url.host == "api.openai.com":
            return SDK_HTTP.Response(status, headers={"location": "https://evil.example/steal"})
        return completion()

    real = DefaultAsyncHttpxClient

    def with_mock_transport(**options: Any) -> Any:
        return real(transport=SDK_HTTP.MockTransport(handler), **options)

    monkeypatch.setattr(llm_client, "DefaultAsyncHttpxClient", with_mock_transport)
    # the conftest guard on the SDK client is lifted for this test: the real one runs
    from openai import AsyncOpenAI as RealSdk

    monkeypatch.setattr(llm_client, "AsyncOpenAI", RealSdk)

    with pytest.raises(ApiError):
        await _generate("https://api.openai.com/v1")

    assert seen == ["https://api.openai.com/v1/chat/completions"]


# -- the probe that proves a model ----------------------------------------------------------------


async def test_the_model_probe_is_one_tiny_capped_request_in_the_providers_dialect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = _wire(monkeypatch, completion("", finish_reason="length"))

    await probe_model(api_key=KEY, model="fixture-model", base_url="https://api.openai.com/v1")

    [body] = wire.bodies()
    assert body["max_completion_tokens"] == PROBE_MAX_TOKENS == 16
    assert len(wire.requests) == 1
    assert wire.built[0]["timeout"].read == 30.0
    assert wire.built[0]["max_retries"] == 1


async def test_a_reasoning_model_that_spends_its_whole_cap_still_counts_as_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch, completion("", finish_reason="length"))

    await probe_model(api_key=KEY, model="fixture-model", base_url="https://api.x.ai/v1")


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (404, "model_not_found"),
        (401, "key_rejected"),
        (402, "out_of_credit"),
        (410, "model_retired"),
    ],
)
async def test_the_probe_fails_with_the_reason_generate_classifies(
    monkeypatch: pytest.MonkeyPatch, status: int, reason: str
) -> None:
    _wire(monkeypatch, sdk_error(status, None))

    with pytest.raises(ApiError) as caught:
        await probe_model(api_key=KEY, model="m", base_url="https://api.deepseek.com")

    assert caught.value.details["reason"] == reason


# -- a revoked key, and what is a throttle, depend on the provider ---------------------------------

_GEMINI = "https://generativelanguage.googleapis.com/v1beta/openai/"
_GEMINI_KEY = {
    "error": {
        "code": 400,
        "message": "API key not valid. Please pass a valid API key.",
        "status": "INVALID_ARGUMENT",
    }
}


@pytest.mark.parametrize(
    ("base_url", "body", "requests"),
    [
        (_GEMINI, _GEMINI_KEY, 1),
        (_GEMINI, [_GEMINI_KEY], 1),  # the compatibility layer sometimes wraps the error in a list
        (
            "https://api.x.ai/v1",
            {"code": "Client specified an invalid argument", "error": "Incorrect API key provided"},
            1,
        ),
        ("https://api.x.ai/v1", {"error": {"message": "Incorrect API key provided: xai-1***"}}, 1),
    ],
)
async def test_a_revoked_key_on_a_provider_that_answers_it_with_400_is_one_request_and_a_key_error(
    monkeypatch: pytest.MonkeyPatch, base_url: str, body: Any, requests: int
) -> None:
    wire = _wire(monkeypatch, sdk_error(400, body))

    with pytest.raises(ApiError) as caught:
        await _generate(base_url)

    assert len(wire.requests) == requests
    assert (caught.value.code, caught.value.details["reason"]) == (
        "PROVIDER_REJECTED",
        "key_rejected",
    )
    assert caught.value.settings_path == "/profile/integrations"
    assert "valid" not in caught.value.message and "Incorrect" not in caught.value.message


async def test_a_400_about_the_request_on_the_same_provider_still_walks_the_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {"error": {"code": 400, "message": "Unknown name", "status": "INVALID_ARGUMENT"}}
    wire = _wire(monkeypatch, sdk_error(400, body))

    with pytest.raises(ApiError) as caught:
        await _generate(_GEMINI)

    assert len(wire.requests) == 3
    assert caught.value.details["reason"] == "request_rejected"


async def test_the_same_message_on_a_provider_with_no_such_marker_is_not_taken_for_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = _wire(monkeypatch, sdk_error(400, _GEMINI_KEY))

    with pytest.raises(ApiError) as caught:
        await _generate("https://api.openai.com/v1")

    assert len(wire.requests) == 3
    assert caught.value.details["reason"] == "request_rejected"


_QUOTA = {"error": {"code": "insufficient_quota", "type": "insufficient_quota"}}


async def test_qwens_insufficient_quota_is_a_throttle_and_openais_is_an_empty_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch, sdk_error(429, _QUOTA, {"x-should-retry": "false"}))
    with pytest.raises(ApiError) as qwen:
        await _generate("https://maas.qwencloudapi.com/compatible-mode/v1")
    assert (qwen.value.code, qwen.value.details["reason"], qwen.value.retryable) == (
        "PROVIDER_RATE_LIMITED",
        "rate_limited",
        True,
    )

    _wire(monkeypatch, sdk_error(429, _QUOTA, {"x-should-retry": "false"}))
    with pytest.raises(ApiError) as openai:
        await _generate("https://api.openai.com/v1")
    assert (openai.value.details["reason"], openai.value.retryable) == ("out_of_credit", False)

    # Qwen's real empty-account code is still one
    _wire(monkeypatch, sdk_error(400, {"error": {"code": "Arrearage"}}))
    with pytest.raises(ApiError) as arrears:
        await _generate("https://ws-abc123.us-east-1.maas.aliyuncs.com/compatible-mode/v1")
    assert arrears.value.details["reason"] == "out_of_credit"


# The same table is in the resume engine's tests/test_llm_client.py, run through its own
# `_is_shape_rejection`; the two services must agree on every row (the sets below are pinned
# literally there as well).
_SHAPE_BODY = {"error": {"message": "Unsupported parameter", "type": "invalid_request_error"}}
_BAD_KEY_400 = {
    "code": 400,
    "message": "API key not valid. Please pass a valid API key.",
    "status": "INVALID_ARGUMENT",
}
_SHAPE_TABLE: list[tuple[str, int, Any, str, bool]] = [
    ("plain 400", 400, _SHAPE_BODY, "openai", True),
    ("plain 422", 422, _SHAPE_BODY, "openai", True),
    ("no body", 400, None, "openai", True),
    ("401", 401, _SHAPE_BODY, "openai", False),
    ("404", 404, None, "openai", False),
    ("model_not_found code", 400, {"code": "model_not_found"}, "openai", False),
    ("not_found_error type", 400, {"type": "not_found_error"}, "openai", False),
    ("model_decommissioned", 400, {"code": "model_decommissioned"}, "openai", False),
    ("model_retired on 422", 422, {"code": "model_retired"}, "openai", False),
    ("spend limit", 400, {"code": "organization_spend_limit_exceeded"}, "openai", False),
    ("billing type", 400, {"type": "billing_error"}, "openai", False),
    ("enforced limit", 400, {"code": "enforced_spend_limit_reached"}, "openai", False),
    (
        "detail error_code",
        400,
        {"details": {"error_code": "credit_balance_exhausted"}},
        "openai",
        False,
    ),
    ("Arrearage", 400, {"code": "Arrearage"}, "qwen", False),
    (
        "balance code on a 422 is a plain rejection",
        422,
        {"code": "insufficient_quota"},
        "openai",
        True,
    ),
    (
        "insufficient_quota is a balance elsewhere",
        400,
        {"code": "insufficient_quota"},
        "openai",
        False,
    ),
    ("insufficient_quota is a throttle on Qwen", 400, {"code": "insufficient_quota"}, "qwen", True),
    ("Gemini bad key", 400, _BAD_KEY_400, "gemini", False),
    ("Gemini bad key, wrapped in a list", 400, [{"error": _BAD_KEY_400}], "gemini", False),
    (
        "a Gemini 400 about the request",
        400,
        {"code": 400, "message": "Unknown name", "status": "INVALID_ARGUMENT"},
        "gemini",
        True,
    ),
    (
        "xAI bad key, the string the SDK hands over",
        400,
        "Incorrect API key provided: xai-12***",
        "xai",
        False,
    ),
    (
        "xAI bad key, message form",
        400,
        {"error": {"message": "Incorrect API key provided: xai-12***"}},
        "xai",
        False,
    ),
    ("a key message on a dialect that has no marker", 400, _BAD_KEY_400, "openai", True),
]
_HOST_OF = {
    "openai": "https://api.openai.com/v1",
    "gemini": _GEMINI,
    "xai": "https://api.x.ai/v1",
    "qwen": "https://maas.qwencloudapi.com/compatible-mode/v1",
}


@pytest.mark.parametrize(
    ("label", "status", "body", "provider", "expected"),
    _SHAPE_TABLE,
    ids=[row[0] for row in _SHAPE_TABLE],
)
def test_when_a_400_means_try_a_plainer_request(
    label: str, status: int, body: Any, provider: str, expected: bool
) -> None:
    import openai

    response = SDK_HTTP.Response(status, json=body, request=SDK_HTTP.Request("POST", "https://x/y"))
    exc = openai.APIStatusError("boom", response=response, body=body)
    dialect = dialect_for_base_url(_HOST_OF[provider])

    assert llm_client._is_shape_rejection(exc, dialect) is expected, label


def test_the_error_code_sets_are_the_ones_the_resume_engine_reads_a_400_with() -> None:
    """Pinned literally; the engine's tests/test_llm_client.py pins the same lists."""
    assert {
        "credit_balance_exhausted",
        "insufficient_quota",
        "organization_spend_limit_exceeded",
        "project_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
        "enforced_spend_limit_reached",
        "billing_error",
        "Arrearage",
        "AllocationQuota.FreeTierOnly",
    } == llm_client._OUT_OF_CREDIT_CODES
    assert {
        "model_not_found",
        "not_found_error",
        "not-found",
        "not_found",
        "ModelNotFound",
    } == llm_client._MODEL_NOT_FOUND_CODES
    assert {"model_decommissioned", "model_retired"} == llm_client._MODEL_RETIRED_CODES
    assert dialect_for_base_url(_HOST_OF["qwen"]).throttle_codes == {"insufficient_quota"}
    assert dialect_for_base_url(_GEMINI).bad_key_markers == ("api key not valid", "api_key_invalid")
    assert dialect_for_base_url(_HOST_OF["xai"]).bad_key_markers == ("incorrect api key",)


# -- caps: xAI keeps the long-accepted field, Qwen takes the one its docs recommend -----


def test_the_cap_field_of_each_dialect_is_the_documented_one() -> None:
    fields = {
        name: dialect_for_base_url(url).token_field
        for name, url in {**_HOST_OF, "anthropic": "https://api.anthropic.com/v1/"}.items()
    }
    assert fields == {
        "openai": "max_completion_tokens",
        "gemini": "max_tokens",
        "xai": "max_tokens",  # deprecated in xAI's reference but still accepted; see the dialect
        "qwen": "max_completion_tokens",
        "anthropic": "max_tokens",
    }


# -- the OpenAI pair that was reported to misbehave, and an answer of nothing ---------


def test_the_first_openai_attempt_is_still_none_with_the_cap_field() -> None:
    openai_dialect = dialect_for_base_url("https://api.openai.com/v1")

    assert request_fields(openai_dialect, 0, max_tokens=50, temperature=None) == (
        {"max_completion_tokens": 50},
        {"reasoning_effort": "none"},
    )


async def test_an_empty_answer_that_hit_the_length_limit_steps_down_the_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = _wire(
        monkeypatch,
        completion("", finish_reason="length"),
        completion("", finish_reason="length"),
        completion("fine"),
    )

    response = await _generate("https://api.openai.com/v1", model="reasoner-fixture")

    assert response.content == "fine"
    assert [b.get("reasoning_effort", "-") for b in wire.bodies()] == ["none", "low", "-"]
    assert llm_client._working_attempt[("api.openai.com", "reasoner-fixture")] == 2


async def test_an_answer_that_is_still_empty_after_the_last_setting_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = _wire(monkeypatch, completion("", finish_reason="length"))

    with pytest.raises(ApiError) as caught:
        await _generate("https://api.openai.com/v1", model="reasoner-fixture")

    assert len(wire.requests) == 3
    assert (caught.value.code, caught.value.details["reason"]) == (
        "PROVIDER_UNAVAILABLE",
        "empty_response",
    )
    assert llm_client._working_attempt == {}  # a failed ladder teaches nothing


async def test_an_empty_answer_on_a_provider_with_one_setting_is_an_error_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = _wire(monkeypatch, completion("", finish_reason="length"))

    with pytest.raises(ApiError) as caught:
        await _generate("https://api.groq.com/openai/v1")

    assert len(wire.requests) == 1
    assert caught.value.details["reason"] == "empty_response"


async def test_openrouter_still_returns_an_empty_length_answer_as_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = _wire(monkeypatch, completion("", finish_reason="length"))

    response = await _generate(None)

    assert response.content == "" and len(wire.requests) == 1


async def test_the_probe_does_not_walk_the_ladder_for_an_empty_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = _wire(monkeypatch, completion("", finish_reason="length"))

    await probe_model(api_key=KEY, model="reasoner", base_url="https://api.openai.com/v1")

    assert len(wire.requests) == 1


# -- the answer is bounded, and the name lookup is inside the time limit ----------------


async def test_the_client_generate_builds_carries_the_size_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = _wire(monkeypatch, completion())

    await _generate("https://api.openai.com/v1")

    [client] = wire.http_clients
    assert client._event_hooks["response"] == [provider_http.bound_response]


class _Chunks(SDK_HTTP.AsyncByteStream):  # type: ignore[misc]
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def __aiter__(self) -> Any:
        for start in range(0, len(self._data), 65_536):
            yield self._data[start : start + 65_536]


def _real_client_answering(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    real = DefaultAsyncHttpxClient

    def with_mock_transport(**options: Any) -> Any:
        return real(transport=SDK_HTTP.MockTransport(handler), **options)

    monkeypatch.setattr(llm_client, "DefaultAsyncHttpxClient", with_mock_transport)
    from openai import AsyncOpenAI as RealSdk

    monkeypatch.setattr(llm_client, "AsyncOpenAI", RealSdk)


@pytest.mark.parametrize("base_url", [None, "https://api.openai.com/v1"])
async def test_a_completion_over_two_megabytes_is_not_read(
    monkeypatch: pytest.MonkeyPatch, base_url: str | None
) -> None:
    filler = "x" * (provider_http.MAX_RESPONSE_BYTES + 1000)
    body = (
        b'{"id":"g","object":"chat.completion","created":1,"model":"m","choices":[{"index":0,'
        b'"finish_reason":"stop","message":{"role":"assistant","content":"'
        + filler.encode()
        + b'"}}]}'
    )

    def handler(_request: Any) -> Any:
        return SDK_HTTP.Response(200, stream=_Chunks(body))

    _real_client_answering(monkeypatch, handler)

    with pytest.raises(ApiError) as caught:
        await _generate(base_url, max_tokens=5)

    assert (caught.value.code, caught.value.details["reason"], caught.value.retryable) == (
        "PROVIDER_UNAVAILABLE",
        "response_too_large",
        False,
    )


async def test_a_compressed_completion_is_bounded_after_decompression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gzip

    bomb = gzip.compress(b"0" * (provider_http.MAX_RESPONSE_BYTES * 5))

    def handler(_request: Any) -> Any:
        return SDK_HTTP.Response(200, headers={"content-encoding": "gzip"}, stream=_Chunks(bomb))

    _real_client_answering(monkeypatch, handler)

    with pytest.raises(ApiError) as caught:
        await _generate("https://api.openai.com/v1")

    assert caught.value.details["reason"] == "response_too_large"


async def test_a_normal_completion_is_still_read_through_the_real_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _real_client_answering(monkeypatch, lambda _request: completion("fine"))

    assert (await _generate("https://api.openai.com/v1")).content == "fine"


@pytest.mark.parametrize("base_url", ["https://api.openai.com/v1"])
async def test_a_stalled_name_lookup_does_not_outlast_the_call_limit(
    monkeypatch: pytest.MonkeyPatch, base_url: str
) -> None:
    import asyncio

    wire = _wire(monkeypatch, completion())

    async def stalled(_host: str) -> list[str]:
        await asyncio.sleep(30)
        return ["104.18.7.192"]

    monkeypatch.setattr(provider_http, "_resolve", stalled)
    started = asyncio.get_running_loop().time()

    with pytest.raises(ApiError) as caught:
        await _generate(base_url, timeout=0.2)

    assert asyncio.get_running_loop().time() - started < 2.0
    assert caught.value.details["reason"] == "timeout"
    assert wire.requests == []
