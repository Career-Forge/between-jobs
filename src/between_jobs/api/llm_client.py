"""between-jobs' own direct OpenRouter client -- how this backend calls an LLM directly
rather than through the separate resume-engine service. Company-intel synthesis has nothing to do
with resume generation, so it doesn't belong in that service; this is a narrow OpenAI-compatible
wrapper for this backend's actual need: one system+user turn.

WHEN THE PROVIDER FAILS. `generate` turns every failure of the model provider into an
`ApiError` through ONE function, `classify_provider_error`, so a revoked key, an empty account,
a throttle, an outage and a bad model id each answer differently:

    401, 403            PROVIDER_REJECTED     key_rejected      "check the key in Integrations"
    402                 PROVIDER_REJECTED     out_of_credit     "top it up or switch key"
    other 4xx           PROVIDER_REJECTED     request_rejected  (a bad model id, an unsupported
                                                                 parameter, content refused)
    429                 PROVIDER_RATE_LIMITED rate_limited      retryable
    5xx, 408, timeout,
    no connection       PROVIDER_UNAVAILABLE  provider_error / timeout / unreachable, retryable
    anything else       PROVIDER_UNAVAILABLE  unknown, retryable

Two refinements are OpenRouter's own, documented: a 403 that is a content refusal (typed
`error_type`, or a moderation block) is `request_rejected` and not a key problem, and a 402 that
is its in-flight spending budget is `in_flight_budget` (a wait, not an empty account).

An HTTP 200 can carry a failure too, and it is classified the same way. OpenRouter reports one
that happens after it accepted the request as an `error` object with an HTTP status in it: at the
top level when there is no completion at all (empty `choices`), or inside the first choice, whose
`finish_reason` is then "error" (even when it holds some partial text, which is not an answer).
That status is classified like any other. When there is no usable status, or the 200 is not a
completion at all (an HTML page from a proxy, an empty body, a list, a choice with no message),
the answer is `empty_response`. A finish reason of "length" or "content_filter" is a completion
that happened to be empty, and is returned as one.

The reason is a short token in the error's `details` (the web app and the logs read it); the
message is written here, in plain words, and says what to do. THE SDK EXCEPTION'S OWN MESSAGE
IS NEVER USED: the provider's error text can echo a fragment of the API key
("Incorrect API key provided: sk-or-...") and would otherwise reach the person, the logs and the
error tracker. Only the status, the exception's type and a few documented fields of the error
body (compared to fixed strings, never printed) are read. The original exception stays
chained as `__cause__`, which the log formatter and the error tracker's scrubber reduce to its
type, because they drop the message of every exception from this SDK.

Three more reasons are named for providers other than OpenRouter, from documented fields of the
error body or the status: `model_not_found` (404, or a body code that says so), `model_retired`
(410, or a body code that says so) and, in the key and model-list checks, `response_too_large`.
A 429 or a 400/403 whose body code names an exhausted balance or spend limit is `out_of_credit`,
not a throttle, whichever provider said it: those codes appear nowhere else.

WHAT A REQUEST LOOKS LIKE. The cap field, whether reasoning is switched off and how, and
whether a temperature goes out all depend on the provider, and the provider is known from the
host of the address (api/provider_registry.py, `Dialect`). OpenRouter's request is the one this
module has always sent, byte for byte. For the others the dialect lists reasoning settings best
first, and `generate` falls to the next only when the provider answers 400 or 422 to the shape
(a model that does not take the setting), within the one time limit and at most as many
requests as the dialect has settings.

Not in scope, deliberately: marking the person's stored key as bad. The callers of `generate` are
handed a bare secret and know no credential id; the person gets the message with the link instead.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    DefaultAsyncHttpxClient,
    Timeout,
)
from openai import APIError as OpenAIAPIError

from .errors import ApiError, ErrorCode
from .provider_http import UpstreamFailure, assert_safe_destination
from .provider_registry import Dialect, dialect_for_base_url

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
"""Per this project's standing rule: LLM calls always route through
OpenRouter unless explicitly told otherwise."""

DEFAULT_TIMEOUT_SECONDS = 120.0
"""How long one `generate` call may take in all, retries included. A call that declares a modest
output cap (up to `LONG_GENERATION_TOKENS`) gets this. It is shorter than the built-in resume
engine's overall limit (`engines.generic.pipeline.APPLY_TIMEOUT_SECONDS`, 180 s), whose longest
call asks for 3,500 tokens: a single stuck call ends as a provider failure by itself instead of
being cut off by the engine's clock with no word about why. The engine's own limit still bounds
the several calls of one run, and cancels a call that is in flight when it fires."""

LONG_GENERATION_TIMEOUT_SECONDS = 300.0
"""The budget for a call that asks for a long answer (more than `LONG_GENERATION_TOKENS`) or sets
no cap at all: the resume-to-profile conversion asks for up to 8,000 tokens, and a slow model
writes those at a few dozen tokens a second. Not an unbounded wait: it is still a hard limit."""

LONG_GENERATION_TOKENS = 4_000

CONNECT_TIMEOUT_SECONDS = 10.0
"""How long to wait to open a connection. The SDK's own default is 5 s; a provider's edge that is
slow to accept should not read as an outage, and a dead one should not hold the request."""

MAX_SDK_RETRIES = 1
"""How many times the SDK may repeat a request on its own (it does so for a connection error, a
timeout, 408, 409, 429 and 5xx, with a short backoff). Its default is two. One is enough to absorb
the common single blip -- an upstream 502 from the router, a dropped connection -- without a
person waiting through three attempts, and without hitting a key that is already being throttled
three times; none would make every blip the person's problem. Whatever the SDK does, the whole call
is still bounded by the timeout above."""

_INTEGRATIONS_PATH = "/profile/integrations"
"""The web app's Integrations page, the same `settings_path` the SETUP_REQUIRED errors use."""

KEY_REJECTED_MESSAGE = "Your AI provider rejected the key. Check it in Integrations."
OUT_OF_CREDIT_MESSAGE = (
    "Your AI provider account is out of credit. Top it up, or switch to a different key in "
    "Integrations."
)
REQUEST_REJECTED_MESSAGE = (
    "The AI provider turned that request down, often because of the model chosen. Check the "
    "model in Integrations, or try a different one."
)
RATE_LIMITED_MESSAGE = "The AI provider is rate limiting this key. Wait a minute, then try again."
UNAVAILABLE_MESSAGE = "Couldn't reach the AI provider. Try again in a moment."
TIMEOUT_MESSAGE = "The AI provider took too long to answer. Try again in a moment."
EMPTY_RESPONSE_MESSAGE = "The AI provider didn't return an answer. Try again in a moment."
MODEL_NOT_FOUND_MESSAGE = (
    "The AI provider doesn't have that model for your account. Pick a different model in "
    "Integrations."
)
MODEL_RETIRED_MESSAGE = (
    "The AI provider has retired that model. Pick a different model in Integrations."
)
RESPONSE_TOO_LARGE_MESSAGE = "The AI provider's answer was larger than this server will read."

ProviderReason = Literal[
    "key_rejected",
    "out_of_credit",
    "request_rejected",
    "rate_limited",
    "in_flight_budget",
    "timeout",
    "unreachable",
    "provider_error",
    "empty_response",
    "model_not_found",
    "model_retired",
    "response_too_large",
    "unknown",
]


@dataclass(frozen=True, slots=True)
class _Outcome:
    code: ErrorCode
    message: str
    retryable: bool
    settings_path: str | None = None


_OUTCOMES: Mapping[ProviderReason, _Outcome] = {
    "key_rejected": _Outcome("PROVIDER_REJECTED", KEY_REJECTED_MESSAGE, False, _INTEGRATIONS_PATH),
    "out_of_credit": _Outcome(
        "PROVIDER_REJECTED", OUT_OF_CREDIT_MESSAGE, False, _INTEGRATIONS_PATH
    ),
    "request_rejected": _Outcome(
        "PROVIDER_REJECTED", REQUEST_REJECTED_MESSAGE, False, _INTEGRATIONS_PATH
    ),
    "rate_limited": _Outcome("PROVIDER_RATE_LIMITED", RATE_LIMITED_MESSAGE, True),
    "in_flight_budget": _Outcome("PROVIDER_RATE_LIMITED", RATE_LIMITED_MESSAGE, True),
    "timeout": _Outcome("PROVIDER_UNAVAILABLE", TIMEOUT_MESSAGE, True),
    "unreachable": _Outcome("PROVIDER_UNAVAILABLE", UNAVAILABLE_MESSAGE, True),
    "provider_error": _Outcome("PROVIDER_UNAVAILABLE", UNAVAILABLE_MESSAGE, True),
    "empty_response": _Outcome("PROVIDER_UNAVAILABLE", EMPTY_RESPONSE_MESSAGE, True),
    "model_not_found": _Outcome(
        "PROVIDER_REJECTED", MODEL_NOT_FOUND_MESSAGE, False, _INTEGRATIONS_PATH
    ),
    "model_retired": _Outcome(
        "PROVIDER_REJECTED", MODEL_RETIRED_MESSAGE, False, _INTEGRATIONS_PATH
    ),
    "response_too_large": _Outcome("PROVIDER_UNAVAILABLE", RESPONSE_TOO_LARGE_MESSAGE, False),
    "unknown": _Outcome("PROVIDER_UNAVAILABLE", UNAVAILABLE_MESSAGE, True),
}

_REFUSAL_ERROR_TYPES = frozenset({"content_policy_violation", "refusal"})
"""OpenRouter's typed `error.metadata.error_type` for a request that was understood and declined
(a content filter, a model's refusal). They arrive as a 403, which is otherwise a key problem."""

_IN_FLIGHT_BUDGET = "openrouter_in_flight_budget"
"""OpenRouter's `error.metadata.limit_source` for a 402 that is a wait, not an empty account."""


def _metadata(body: object) -> Mapping[str, object]:
    """`metadata` of an error body (the SDK hands over the `error` object), or an empty mapping."""
    if isinstance(body, Mapping):
        metadata = body.get("metadata")
        if isinstance(metadata, Mapping):
            return metadata
    return {}


_OUT_OF_CREDIT_CODES = frozenset(
    {
        # OpenAI's error-codes guide: a 429 that is a balance or a spend limit, not a throttle.
        "credit_balance_exhausted",
        "insufficient_quota",
        "organization_spend_limit_exceeded",
        "project_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
        # Anthropic: an organisation's spend cap, and a billing problem.
        "enforced_spend_limit_reached",
        "billing_error",
        # Qwen: arrears, and a free quota that is the only thing left.
        "Arrearage",
        "AllocationQuota.FreeTierOnly",
    }
)
_MODEL_NOT_FOUND_CODES = frozenset(
    {"model_not_found", "not_found_error", "not-found", "not_found", "ModelNotFound"}
)
# Unverified: Groq-style decommissioning code, and a generic spelling. A status of 410 is the
# documented-by-HTTP signal; these are extra. Re-check each provider's error page before relying.
_MODEL_RETIRED_CODES = frozenset({"model_decommissioned", "model_retired"})


def _body_tokens(body: object) -> frozenset[str]:
    """The short `code`, `type` and `details.error_code` strings of an error body, for comparing
    with the fixed sets above. Never printed or stored: a body can quote the key."""
    found: set[str] = set()
    if isinstance(body, Mapping):
        for field in ("code", "type"):
            value = body.get(field)
            if isinstance(value, str) and len(value) <= 64:
                found.add(value)
        details = body.get("details")
        if isinstance(details, Mapping):
            value = details.get("error_code")
            if isinstance(value, str) and len(value) <= 64:
                found.add(value)
    return frozenset(found)


def _reason_for_status(
    status: int, body: object, *, named_model_errors: bool = False
) -> ProviderReason:
    """`named_model_errors` is for every provider but OpenRouter: there a 404, a 410 or a body
    code that names the model is a missing or retired model, where OpenRouter's own 4xx answers
    have always been the general `request_rejected`."""
    metadata = _metadata(body)
    tokens = _body_tokens(body)
    if status in (400, 402, 403, 429) and tokens & _OUT_OF_CREDIT_CODES:
        return "out_of_credit"
    if named_model_errors and tokens & _MODEL_RETIRED_CODES:
        return "model_retired"
    if (
        named_model_errors
        and (status == 404 or tokens & _MODEL_NOT_FOUND_CODES)
        and 400 <= status < 500
        and status not in (401, 402, 403, 429)
    ):
        return "model_not_found"
    if named_model_errors and status == 410:
        return "model_retired"
    if status == 401:
        return "key_rejected"
    if status == 402:
        if metadata.get("limit_source") == _IN_FLIGHT_BUDGET:
            return "in_flight_budget"
        return "out_of_credit"
    if status == 403:
        error_type = metadata.get("error_type")
        refused = (isinstance(error_type, str) and error_type in _REFUSAL_ERROR_TYPES) or (
            "reasons" in metadata
        )
        return "request_rejected" if refused else "key_rejected"
    if status == 408:
        return "timeout"
    if status == 429:
        return "rate_limited"
    if 400 <= status < 500:
        return "request_rejected"
    if status >= 500:
        return "provider_error"
    return "unknown"


def status_reason(status: int, body: object) -> ProviderReason:
    """The reason an HTTP status and error body stand for, for a call that is not a completion
    (the key and model-list checks): the same rules `generate` classifies with."""
    return _reason_for_status(status, body)


def _error(reason: ProviderReason) -> ApiError:
    outcome = _OUTCOMES[reason]
    return ApiError(
        outcome.code,
        outcome.message,
        retryable=outcome.retryable,
        settings_path=outcome.settings_path,
        details={"reason": reason},
    )


def outcome_for(reason: ProviderReason) -> tuple[ErrorCode, bool]:
    """(code, retryable) of a reason, so a check can word its own message and keep the code."""
    outcome = _OUTCOMES[reason]
    return outcome.code, outcome.retryable


def classify_provider_error(exc: BaseException, *, named_model_errors: bool = False) -> ApiError:
    """The `ApiError` a failed model call is answered with: the code, the plain-words message,
    whether trying again can help, the Integrations link when the person can fix it there, and
    the machine-readable reason in `details`. Anything this does not recognise is a retryable
    PROVIDER_UNAVAILABLE with reason `unknown`.

    Reads the exception's type and, for an HTTP answer, its status and a few documented fields of
    its body. Never its message (see the module docstring). The caller chains `exc` as the
    cause (`raise classify_provider_error(e) from e`)."""
    if isinstance(exc, APIStatusError):
        return _error(
            _reason_for_status(exc.status_code, exc.body, named_model_errors=named_model_errors)
        )
    if isinstance(exc, APITimeoutError):
        return _error("timeout")
    if isinstance(exc, APIConnectionError):
        return _error("unreachable")
    return _error("unknown")


def _completion_error(source: object) -> ApiError:
    """The `ApiError` for an HTTP 200 that is not a usable completion. `source` is the response
    (OpenRouter answers with no `choices` when every upstream candidate failed) or its first
    choice (a generation that failed after it was accepted). The failure is then an `error`
    object (`{"code": 429, "message": ..., "metadata": ...}`), which the SDK keeps as an extra
    field. When it names an HTTP status, that status is classified like any other; otherwise
    the answer is simply empty. Anything without an `error` field (a page, a list, a string)
    gets the same."""
    inline = getattr(source, "error", None)
    if isinstance(inline, Mapping):
        status = inline.get("code")
        if isinstance(status, int) and not isinstance(status, bool):
            return _error(_reason_for_status(status, inline))
    return _error("empty_response")


def default_timeout_seconds(max_tokens: int | None) -> float:
    """The budget for a call that asks for `max_tokens` (None: no cap, so the model's own limit)."""
    if max_tokens is None or max_tokens > LONG_GENERATION_TOKENS:
        return LONG_GENERATION_TIMEOUT_SECONDS
    return DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str


UNCAPPED_CEILING_TOKENS = 8_192
"""The cap sent to a provider other than OpenRouter when the caller sets none. Every request
path carries a cap: some providers refuse a request without one (Anthropic's API requires it)
and the rest would let a model write until its own limit. 8,192 is below the output limit of
every model this was checked against, so it cannot be refused as too large, and is far above what
the callers that set none (a batch of job scores) write. OpenRouter keeps the `null` it always
sent."""

_ATTEMPT_MEMORY_LIMIT = 256
_working_attempt: dict[tuple[str, str], int] = {}
"""(host, model) -> index of the dialect attempt that last worked, so a model that refuses the
first setting is not asked for it again by every call in this process. Bounded; cleared when
full (a lost entry costs one rejected request)."""


def _host_key(base_url: str) -> str:
    return base_url.split("//", 1)[-1].split("/", 1)[0].lower()


def request_fields(
    dialect: Dialect, attempt: int, *, max_tokens: int | None, temperature: float | None
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """(the keyword arguments for the SDK call, the extra body fields) of one attempt of a
    dialect. Pure, so the shape of each provider's request is testable without a client."""
    fields: dict[str, Any] = {}
    if max_tokens is not None or dialect.send_null_cap:
        fields[dialect.token_field] = max_tokens
    if temperature is not None and dialect.temperature == "send":
        fields["temperature"] = temperature
    extra = dict(dialect.attempts[attempt])
    return fields, (extra or None)


def _is_shape_rejection(exc: OpenAIAPIError) -> bool:
    """A 400 or 422 that says nothing more specific than "I do not take this request": the
    case where a plainer request may be accepted."""
    return (
        isinstance(exc, APIStatusError)
        and exc.status_code in (400, 422)
        and _reason_for_status(exc.status_code, exc.body, named_model_errors=True)
        == "request_rejected"
    )


async def generate(
    *,
    api_key: str,
    model: str,
    base_url: str | None,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int | None = None,
    temperature: float | None = None,
    timeout: float | None = None,
) -> LLMResponse:
    """One system+user turn. `timeout` is the most this call may take in all, the SDK's own
    retry and the dialect's fallbacks included (the default depends on the size of the answer
    asked for, see `default_timeout_seconds`). A provider failure raises the `ApiError` that
    `classify_provider_error` makes of it."""
    budget = default_timeout_seconds(max_tokens) if timeout is None else timeout
    if budget <= 0:
        raise ValueError("timeout must be positive")
    target = base_url or OPENROUTER_BASE_URL
    dialect = dialect_for_base_url(target)
    is_openrouter = target == OPENROUTER_BASE_URL
    if not is_openrouter:
        try:
            await assert_safe_destination(target)
        except UpstreamFailure as e:
            raise _error(e.reason) from e
    if max_tokens is None and not is_openrouter:
        max_tokens = UNCAPPED_CEILING_TOKENS
    memory_key = (_host_key(target), model)
    attempt = _working_attempt.get(memory_key, 0)
    if attempt >= len(dialect.attempts):
        attempt = 0
    try:
        async with (
            asyncio.timeout(budget),
            DefaultAsyncHttpxClient(follow_redirects=False) as http_client,
            AsyncOpenAI(
                api_key=api_key,
                base_url=target,
                timeout=Timeout(budget, connect=min(CONNECT_TIMEOUT_SECONDS, budget)),
                max_retries=MAX_SDK_RETRIES,
                # No redirect is followed: a provider has no business sending a request that
                # carries a key to another host. (The SDK's own client follows them.)
                http_client=http_client,
            ) as client,
        ):
            while True:
                fields, extra = request_fields(
                    dialect, attempt, max_tokens=max_tokens, temperature=temperature
                )
                try:
                    response = await client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        # This is grounded extraction from provided evidence, not a task
                        # that benefits from chain-of-thought -- and OpenRouter's own
                        # docs warn that reasoning tokens count against max_tokens, so a
                        # reasoning model can silently burn the whole budget and return
                        # empty content. `effort: "none"` is OpenRouter's documented,
                        # cross-model way to turn that off; non-reasoning models ignore it.
                        # Other providers' equivalents are the dialect's attempts.
                        extra_body=extra,
                        **fields,
                    )
                except OpenAIAPIError as e:
                    if attempt + 1 < len(dialect.attempts) and _is_shape_rejection(e):
                        attempt += 1
                        continue
                    raise
                break
    except TimeoutError as e:
        raise _error("timeout") from e
    except OpenAIAPIError as e:
        raise classify_provider_error(e, named_model_errors=not is_openrouter) from e

    if attempt:
        if len(_working_attempt) >= _ATTEMPT_MEMORY_LIMIT:
            _working_attempt.clear()
        _working_attempt[memory_key] = attempt

    # An HTTP 200 is not always a completion, and the SDK does not treat any of these as an
    # error, so each needs its own guard rather than a crash on `.choices[0].message`:
    # - an empty `choices`, when every upstream candidate for a routed model failed (seen in
    #   practice with free-tier models under load), with the failure in a top-level `error`;
    # - a first choice whose `finish_reason` is "error", or that carries an `error` object: the
    #   generation failed after OpenRouter accepted it, possibly with partial text, which is not
    #   an answer to hand on;
    # - a body that is not a completion object at all, which the SDK returns as the text or the
    #   list it parsed (a wrong base URL, a proxy or captive portal answering 200), and a choice
    #   with no message.
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or not choices:
        raise _completion_error(response)
    choice = choices[0]
    if getattr(choice, "finish_reason", None) == "error" or isinstance(
        getattr(choice, "error", None), Mapping
    ):
        raise _completion_error(choice)
    message = getattr(choice, "message", None)
    if message is None:
        raise _completion_error(choice)
    content = getattr(message, "content", None)
    return LLMResponse(content=content if isinstance(content, str) else "")
