"""The model client's failure handling (api/llm_client.py): one classifier that turns what the
OpenAI SDK raises into the `ApiError` a person is shown, a hard time limit, and a small cap on
the SDK's own retries.

The SDK is the real one wherever it can be: its exceptions are what a real client raises for a
canned HTTP answer (a mock transport, so nothing leaves the process), because the one fact the
classification rests on -- a 402 reaches the caller as a bare `APIStatusError` with
`status_code == 402`, not as a subclass of its own -- is the SDK's behaviour and not ours. No test
here sends a request anywhere, and none uses a real key: the "key" in the leak tests is a made-up
string of the shape a provider echoes back.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from typing import Any, cast

import openai
import pytest
from fake_sentry import FakeSentry

import between_jobs.engines.generic.body as engine_body
from between_jobs.api import error_reporting, llm_client
from between_jobs.api.errors import ApiError, log_api_error
from between_jobs.api.llm_client import (
    CONNECT_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    LONG_GENERATION_TIMEOUT_SECONDS,
    LONG_GENERATION_TOKENS,
    MAX_SDK_RETRIES,
    classify_provider_error,
    default_timeout_seconds,
    generate,
)
from between_jobs.api.logging_setup import JsonFormatter, format_exception_safely
from between_jobs.api.sentry_scrub import before_send, scrub_event
from between_jobs.engines.generic import pipeline

# What a provider echoes back for a bad key: made up, but of the shape the log redactor and the
# error tracker's scrubber know, which is what makes a leak visible.
KEY = "sk-or-v1-0123456789abcdef0123456789abcdef0123456789abcdef"
KEY_FRAGMENT = "0123456789abcdef0123456789abcdef"
ECHO = (
    f"Incorrect API key provided: {KEY}. You can find your API key at https://openrouter.ai/keys."
)


def _sdk_http() -> Any:
    """The HTTP library the installed SDK is built on (it moved from httpx to its own fork)."""
    import openai._exceptions as sdk_exceptions

    names = vars(sdk_exceptions)
    return names.get("httpx2") or names["httpx"]


HTTP = _sdk_http()
REQUEST = HTTP.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
INTEGRATIONS = "/profile/integrations"

Handler = Callable[[Any], Any]


def _answer(status: int, body: Any = None, headers: dict[str, str] | None = None) -> Any:
    return HTTP.Response(status, json=body, headers=headers or {})


def _client(handler: Handler, **options: Any) -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(
        api_key="test-key-not-real",
        base_url=llm_client.OPENROUTER_BASE_URL,
        http_client=HTTP.AsyncClient(transport=HTTP.MockTransport(handler)),
        **options,
    )


async def _sdk_raises(status: int, body: Any = None, headers: dict[str, str] | None = None) -> Any:
    """What the real SDK raises for an HTTP answer of `status`."""
    client = _client(lambda request: _answer(status, body, headers), max_retries=0)
    with pytest.raises(openai.APIError) as raised:
        await client.chat.completions.create(model="m", messages=[{"role": "user", "content": "x"}])
    await client.close()
    return raised.value


def _error_body(status: int, **metadata: Any) -> dict[str, Any]:
    error: dict[str, Any] = {"code": status, "message": "The provider's own words."}
    if metadata:
        error["metadata"] = metadata
    return {"error": error}


# -- the classifier: every status, every SDK exception class, unknown ---------------------------

# (code, reason, message, retryable, settings_path)
KEY_REJECTED = (
    "PROVIDER_REJECTED",
    "key_rejected",
    "Your AI provider rejected the key. Check it in Settings.",
    False,
    INTEGRATIONS,
)
OUT_OF_CREDIT = (
    "PROVIDER_REJECTED",
    "out_of_credit",
    "Your AI provider account is out of credit. Top it up, or switch to a different key in "
    "Settings.",
    False,
    INTEGRATIONS,
)
REQUEST_REJECTED = (
    "PROVIDER_REJECTED",
    "request_rejected",
    "The AI provider turned that request down, often because of the model chosen. Check the "
    "model in Settings, or try a different one.",
    False,
    INTEGRATIONS,
)
RATE_LIMITED = (
    "PROVIDER_RATE_LIMITED",
    "rate_limited",
    "The AI provider is rate limiting this key. Wait a minute, then try again.",
    True,
    None,
)
IN_FLIGHT_BUDGET = (
    "PROVIDER_RATE_LIMITED",
    "in_flight_budget",
    "The AI provider is rate limiting this key. Wait a minute, then try again.",
    True,
    None,
)
UNAVAILABLE = "Couldn't reach the AI provider. Try again in a moment."
PROVIDER_ERROR = ("PROVIDER_UNAVAILABLE", "provider_error", UNAVAILABLE, True, None)
TIMED_OUT = (
    "PROVIDER_UNAVAILABLE",
    "timeout",
    "The AI provider took too long to answer. Try again in a moment.",
    True,
    None,
)
UNREACHABLE = ("PROVIDER_UNAVAILABLE", "unreachable", UNAVAILABLE, True, None)
UNKNOWN = ("PROVIDER_UNAVAILABLE", "unknown", UNAVAILABLE, True, None)
EMPTY = (
    "PROVIDER_UNAVAILABLE",
    "empty_response",
    "The AI provider didn't return an answer. Try again in a moment.",
    True,
    None,
)


def _assert_outcome(error: ApiError, expected: tuple[Any, ...]) -> None:
    code, reason, message, retryable, settings_path = expected
    assert error.code == code
    assert error.message == message
    assert error.retryable is retryable
    assert error.settings_path == settings_path
    assert error.details == {"reason": reason}
    # nothing else of the setup-required shape is borrowed
    assert error.capability is None and error.missing is None and error.run_id is None


@pytest.mark.parametrize(
    ("status", "metadata", "expected", "sdk_class"),
    [
        (400, {}, REQUEST_REJECTED, openai.BadRequestError),
        (401, {}, KEY_REJECTED, openai.AuthenticationError),
        # OpenRouter's payment required: the SDK has no class for it, only the bare base
        (402, {}, OUT_OF_CREDIT, openai.APIStatusError),
        (403, {}, KEY_REJECTED, openai.PermissionDeniedError),
        (404, {}, REQUEST_REJECTED, openai.NotFoundError),
        (408, {}, TIMED_OUT, openai.APIStatusError),
        (409, {}, REQUEST_REJECTED, openai.ConflictError),
        (413, {}, REQUEST_REJECTED, openai.APIStatusError),
        (422, {}, REQUEST_REJECTED, openai.UnprocessableEntityError),
        (429, {}, RATE_LIMITED, openai.RateLimitError),
        (499, {}, REQUEST_REJECTED, openai.APIStatusError),
        (500, {}, PROVIDER_ERROR, openai.InternalServerError),
        (502, {}, PROVIDER_ERROR, openai.InternalServerError),
        (503, {}, PROVIDER_ERROR, openai.InternalServerError),
        (504, {}, PROVIDER_ERROR, openai.InternalServerError),
        (529, {}, PROVIDER_ERROR, openai.InternalServerError),
        # a 402 that is a wait, not an empty account
        (402, {"limit_source": "openrouter_in_flight_budget"}, IN_FLIGHT_BUDGET, None),
        (402, {"limit_source": "something_else"}, OUT_OF_CREDIT, None),
        # a 403 that is a refusal of the content, not of the key
        (403, {"error_type": "content_policy_violation"}, REQUEST_REJECTED, None),
        (403, {"error_type": "refusal"}, REQUEST_REJECTED, None),
        (403, {"reasons": ["harassment"], "flagged_input": "..."}, REQUEST_REJECTED, None),
        (403, {"error_type": "permission_denied"}, KEY_REJECTED, None),
        (403, {"patterns": ["ignore all previous instructions"]}, KEY_REJECTED, None),
    ],
)
async def test_every_status_the_sdk_raises_is_classified(
    status: int, metadata: dict[str, Any], expected: tuple[Any, ...], sdk_class: type | None
) -> None:
    raised = await _sdk_raises(status, _error_body(status, **metadata))
    if sdk_class is not None:
        assert type(raised) is sdk_class
    assert getattr(raised, "status_code", None) == status

    _assert_outcome(classify_provider_error(raised), expected)


async def test_an_answer_that_is_not_json_is_classified_by_its_status_alone() -> None:
    raised = await _sdk_raises(502, None)
    _assert_outcome(classify_provider_error(raised), PROVIDER_ERROR)
    raised = await _sdk_raises(403, "forbidden")  # a plain-text body has no metadata to read
    _assert_outcome(classify_provider_error(raised), KEY_REJECTED)


@pytest.mark.parametrize(
    "body",
    [
        {"error": {"code": 403, "metadata": "not a mapping"}},
        {"error": {"code": 403, "metadata": ["error_type", "refusal"]}},
        {"error": {"code": 403, "metadata": {"error_type": ["refusal"]}}},
        {"error": {"code": 403, "metadata": {"error_type": 7}}},
        {"error": ["refusal"]},
        {"error": "refusal"},
    ],
)
async def test_a_body_of_an_unexpected_shape_is_not_a_refusal(body: Any) -> None:
    raised = await _sdk_raises(403, body)
    _assert_outcome(classify_provider_error(raised), KEY_REJECTED)


def _status_error(status: int) -> openai.APIStatusError:
    request = REQUEST
    return openai.APIStatusError(
        "x", response=HTTP.Response(status, request=request), body={"message": "x"}
    )


@pytest.mark.parametrize("status", [100, 200, 301, 399])
def test_a_status_that_is_not_an_error_status_is_unknown(status: int) -> None:
    _assert_outcome(classify_provider_error(_status_error(status)), UNKNOWN)


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (openai.APITimeoutError(request=REQUEST), TIMED_OUT),
        (openai.APIConnectionError(request=REQUEST), UNREACHABLE),
        (openai.APIConnectionError(message="Connection error.", request=REQUEST), UNREACHABLE),
        (openai.APIError("x", REQUEST, body=None), UNKNOWN),
        (
            openai.APIResponseValidationError(HTTP.Response(200, request=REQUEST), body=None),
            UNKNOWN,
        ),
        (openai.OpenAIError("x"), UNKNOWN),
        (RuntimeError("x"), UNKNOWN),
        (ValueError(), UNKNOWN),
        (KeyboardInterrupt(), UNKNOWN),
    ],
)
def test_every_other_exception_is_classified_by_its_type(
    exception: BaseException, expected: tuple[Any, ...]
) -> None:
    _assert_outcome(classify_provider_error(exception), expected)


def test_the_codes_and_statuses_the_person_and_the_web_app_see() -> None:
    assert classify_provider_error(_status_error(401)).status_code == 502
    assert classify_provider_error(_status_error(429)).status_code == 429
    assert classify_provider_error(_status_error(503)).status_code == 503
    body = classify_provider_error(_status_error(402)).to_body()["error"]
    assert body == {
        "code": "PROVIDER_REJECTED",
        "message": (
            "Your AI provider account is out of credit. Top it up, or switch to a different key "
            "in Settings."
        ),
        "retryable": False,
        "capability": None,
        "missing": None,
        "settings_path": "/profile/integrations",
        "run_id": None,
        "details": {"reason": "out_of_credit"},
    }


def test_nothing_that_tells_the_person_to_retry_is_marked_unretryable_or_the_reverse() -> None:
    """A rejection never works again unchanged; a throttle or an outage can."""
    for status in (400, 401, 402, 403, 404, 409, 422):
        assert classify_provider_error(_status_error(status)).retryable is False
    for status in (408, 429, 500, 502, 503, 504):
        assert classify_provider_error(_status_error(status)).retryable is True


def test_the_link_is_only_on_what_the_person_can_fix_in_integrations() -> None:
    for status in (400, 401, 402, 403, 404, 422):
        assert classify_provider_error(_status_error(status)).settings_path == INTEGRATIONS
    for status in (408, 429, 500, 503):
        assert classify_provider_error(_status_error(status)).settings_path is None


def test_each_reason_has_its_own_outcome_and_none_is_missing() -> None:
    assert set(llm_client._OUTCOMES) == set(llm_client.ProviderReason.__args__)  # type: ignore[attr-defined]


# -- the message of the SDK's exception never leaves --------------------------------------------


def _leaky_exceptions() -> list[BaseException]:
    """Exceptions whose own text, body and request all carry the key."""
    request = HTTP.Request(
        "POST",
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {KEY}"},
    )
    body = {"code": "invalid_api_key", "message": ECHO, "metadata": {"raw": ECHO}}

    def status_error(cls: type[openai.APIStatusError], status: int) -> openai.APIStatusError:
        return cls(ECHO, response=HTTP.Response(status, request=request), body=body)

    return [
        status_error(openai.AuthenticationError, 401),
        status_error(openai.PermissionDeniedError, 403),
        status_error(openai.RateLimitError, 429),
        status_error(openai.InternalServerError, 502),
        status_error(openai.BadRequestError, 400),
        status_error(openai.APIStatusError, 402),
        openai.APIConnectionError(message=ECHO, request=request),
        openai.APITimeoutError(request=request),
        openai.APIError(ECHO, request, body=body),
    ]


class _RaisingClient:
    """A stand-in for `AsyncOpenAI` whose call raises `failure`."""

    def __init__(self, failure: BaseException) -> None:
        self.failure = failure
        self.chat = self
        self.completions = self

    async def create(self, **_: Any) -> Any:
        raise self.failure

    async def __aenter__(self) -> _RaisingClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None


async def _generate_failing_with(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> ApiError:
    monkeypatch.setattr(llm_client, "AsyncOpenAI", lambda **_: _RaisingClient(failure))
    with pytest.raises(ApiError) as raised:
        await generate(
            api_key=KEY, model="m", base_url=None, system_prompt="s", user_prompt="u", max_tokens=10
        )
    return raised.value


def _event_for(error: BaseException) -> dict[str, Any]:
    """The shape of event the SDK builds from an exception and the ones it was raised from."""
    values = []
    link: BaseException | None = error
    while link is not None:
        values.append(
            {
                "type": type(link).__name__,
                "module": type(link).__module__,
                "value": str(link),
                "mechanism": {"type": "generic", "handled": True},
            }
        )
        link = link.__cause__
    return {"level": "error", "exception": {"values": list(reversed(values))}}


@pytest.mark.parametrize("failure", _leaky_exceptions(), ids=lambda f: type(f).__name__)
async def test_the_key_an_sdk_error_echoes_appears_nowhere(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failure: BaseException
) -> None:
    error = await _generate_failing_with(monkeypatch, failure)
    assert error.__cause__ is failure  # the evidence is chained, for the types in the log
    if not isinstance(
        failure, openai.APITimeoutError
    ):  # its text is fixed; its request has the key
        assert KEY in str(failure)  # the premise: the SDK's own text does carry it

    # what the person is shown
    shown = json.dumps(error.to_body()) + str(error) + error.message + repr(error.details)
    assert KEY_FRAGMENT not in shown and "sk-or" not in shown and "Incorrect" not in shown

    # what is logged, as it is written (the JSON line, with the traceback of the whole chain)
    with caplog.at_level(logging.DEBUG, logger="between_jobs.test_llm_client"):
        logger = logging.getLogger("between_jobs.test_llm_client")
        log_api_error(logger, error, ctx={"route": "/x"})
        try:
            raise error
        except ApiError:
            logger.exception("contained", extra={"ctx": {"task": "t"}})
    written = "\n".join(JsonFormatter().format(r) for r in caplog.records)
    written += format_exception_safely(error)
    assert KEY_FRAGMENT not in written and "sk-or" not in written and "Incorrect" not in written
    assert caplog.records  # the lines were really written

    # what the error tracker's scrubber is handed, with the chain the SDK would have recorded
    scrubbed = scrub_event(_event_for(error))
    assert scrubbed is not None
    sent = json.dumps(scrubbed)
    assert KEY_FRAGMENT not in sent and "sk-or" not in sent and "Incorrect" not in sent


def test_the_key_would_have_reached_the_tracker_had_the_sdk_message_been_used() -> None:
    """Guards the guard above: the same event built from the unclassified exception does carry
    the key in its text, so an empty result there is the scrubber's doing and not the helper's."""
    event = _event_for(RuntimeError(ECHO))
    assert KEY_FRAGMENT in json.dumps(event)


async def test_a_provider_failure_made_of_a_leaky_error_is_not_handed_to_the_tracker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = FakeSentry()
    monkeypatch.setattr(error_reporting, "_sdk", sdk)
    error = await _generate_failing_with(monkeypatch, _leaky_exceptions()[0])

    error_reporting.report_exception(error, tags={"error_code": error.code})
    assert before_send({"level": "error"}, {"exc_info": (type(error), error, None)}) is None

    assert sdk.captured == []


@pytest.mark.parametrize(
    "failure", [*_leaky_exceptions()[:2], RuntimeError(ECHO)], ids=lambda f: type(f).__name__
)
def test_the_classifier_alone_never_copies_the_text_of_what_it_is_given(
    failure: BaseException,
) -> None:
    error = classify_provider_error(failure)
    assert KEY_FRAGMENT not in repr(error.__dict__) and ECHO not in repr(error.__dict__)


# -- the real SDK, end to end through generate() -----------------------------------------------


class Wire:
    """`AsyncOpenAI` as `generate` builds it, but over a mock transport: what it was built with
    and every request it sent are kept for the test to read."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.built: list[dict[str, Any]] = []
        self.http_clients: list[Any] = []
        self.clients: list[openai.AsyncOpenAI] = []
        self.requests: list[Any] = []

    def _handle(self, request: Any) -> Any:
        self.requests.append(request)
        return self.handler(request)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> Wire:
        def build(**options: Any) -> openai.AsyncOpenAI:
            # `generate` hands the SDK its own HTTP client (one that follows no redirect); the
            # test swaps in the mock transport, and keeps the original to inspect.
            self.http_clients.append(options.pop("http_client", None))
            self.built.append(options)
            client = openai.AsyncOpenAI(
                http_client=HTTP.AsyncClient(transport=HTTP.MockTransport(self._handle)),
                **options,
            )
            self.clients.append(client)
            return client

        monkeypatch.setattr(llm_client, "AsyncOpenAI", build)
        return self


def _completion(text: str = "hello") -> dict[str, Any]:
    return {
        "id": "gen-1",
        "object": "chat.completion",
        "created": 1,
        "model": "test/model",
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}
        ],
    }


async def _generate(**options: Any) -> llm_client.LLMResponse:
    defaults: dict[str, Any] = {
        "api_key": "test-key-not-real",
        "model": "test/model",
        "base_url": None,
        "system_prompt": "be brief",
        "user_prompt": "hi",
    }
    return await generate(**{**defaults, **options})


async def test_a_good_answer_is_returned_and_the_request_is_what_it_always_was(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = Wire(lambda request: _answer(200, _completion("the answer"))).install(monkeypatch)

    response = await _generate(max_tokens=123)

    assert response.content == "the answer"
    [request] = wire.requests
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key-not-real"
    assert json.loads(request.content) == {
        "model": "test/model",
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ],
        "max_tokens": 123,
        "reasoning": {"effort": "none"},
    }
    [options] = wire.built
    assert options["api_key"] == "test-key-not-real"
    assert options["base_url"] == llm_client.OPENROUTER_BASE_URL


async def test_a_custom_base_url_is_used_and_an_empty_content_is_an_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _completion()
    body["choices"][0]["message"]["content"] = None
    wire = Wire(lambda request: _answer(200, body)).install(monkeypatch)

    response = await _generate(base_url="https://llm.example.test/v1")

    assert response.content == ""
    assert wire.built[0]["base_url"] == "https://llm.example.test/v1"


async def test_the_client_is_closed_when_the_call_is_over(monkeypatch: pytest.MonkeyPatch) -> None:
    wire = Wire(lambda request: _answer(200, _completion())).install(monkeypatch)
    await _generate()
    assert wire.clients[0].is_closed()

    wire = Wire(lambda request: _answer(401, _error_body(401))).install(monkeypatch)
    with pytest.raises(ApiError):
        await _generate()
    assert wire.clients[0].is_closed()


@pytest.mark.parametrize(
    ("status", "expected", "attempts"),
    [
        (401, KEY_REJECTED, 1),
        (402, OUT_OF_CREDIT, 1),
        (403, KEY_REJECTED, 1),
        (400, REQUEST_REJECTED, 1),
        (404, REQUEST_REJECTED, 1),
        (429, RATE_LIMITED, 2),
        (500, PROVIDER_ERROR, 2),
        (502, PROVIDER_ERROR, 2),
    ],
)
async def test_generate_raises_the_classified_error_and_retries_only_what_can_change(
    monkeypatch: pytest.MonkeyPatch, status: int, expected: tuple[Any, ...], attempts: int
) -> None:
    """A rejection is never repeated; a throttle or an outage is repeated ONCE (the cap below)."""
    wire = Wire(
        lambda request: _answer(status, _error_body(status), headers={"retry-after-ms": "1"})
    ).install(monkeypatch)

    with pytest.raises(ApiError) as raised:
        await _generate()

    _assert_outcome(raised.value, expected)
    assert len(wire.requests) == attempts
    assert isinstance(raised.value.__cause__, openai.APIStatusError)


async def test_one_failure_then_a_good_answer_is_a_good_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter([_answer(502, _error_body(502), {"retry-after-ms": "1"}), None])

    def handler(request: Any) -> Any:
        return next(answers) or _answer(200, _completion("second time lucky"))

    wire = Wire(handler).install(monkeypatch)

    assert (await _generate()).content == "second time lucky"
    assert len(wire.requests) == 2


async def test_a_connection_that_cannot_be_made_is_unreachable_after_one_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: Any) -> Any:
        raise HTTP.ConnectError("no route to host", request=request)

    wire = Wire(handler).install(monkeypatch)

    with pytest.raises(ApiError) as raised:
        await _generate()

    _assert_outcome(raised.value, UNREACHABLE)
    assert len(wire.requests) == 2
    assert isinstance(raised.value.__cause__, openai.APIConnectionError)


async def test_a_read_timeout_inside_the_sdk_is_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: Any) -> Any:
        raise HTTP.ReadTimeout("too slow", request=request)

    Wire(handler).install(monkeypatch)

    with pytest.raises(ApiError) as raised:
        await _generate()

    _assert_outcome(raised.value, TIMED_OUT)
    assert isinstance(raised.value.__cause__, openai.APITimeoutError)


# -- an HTTP 200 with no completion in it ---------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"id": "g", "choices": []}, EMPTY),
        ({"id": "g"}, EMPTY),
        ({"id": "g", "choices": None}, EMPTY),
        # OpenRouter: the failure of every upstream candidate arrives as an error object
        ({"error": {"code": 429, "message": "Rate limit exceeded"}}, RATE_LIMITED),
        ({"error": {"code": 402, "message": "no credits"}}, OUT_OF_CREDIT),
        ({"error": {"code": 502, "message": "bad gateway"}}, PROVIDER_ERROR),
        ({"error": {"code": 401, "message": "bad key"}}, KEY_REJECTED),
        (
            {"error": {"code": 403, "metadata": {"error_type": "content_policy_violation"}}},
            REQUEST_REJECTED,
        ),
        ({"id": "g", "choices": [], "error": {"code": 429, "message": "x"}}, RATE_LIMITED),
        # an error object that names no usable status is just an empty answer
        ({"error": {"code": "rate_limited", "message": "x"}}, EMPTY),
        ({"error": {"code": True, "message": "x"}}, EMPTY),
        ({"error": {"code": 1.5, "message": "x"}}, EMPTY),
        ({"error": {"message": "no code"}}, EMPTY),
        ({"error": "plain text"}, EMPTY),
        ({"error": [429]}, EMPTY),
    ],
)
async def test_an_answer_without_a_completion_is_classified_like_any_other_failure(
    monkeypatch: pytest.MonkeyPatch, body: dict[str, Any], expected: tuple[Any, ...]
) -> None:
    Wire(lambda request: _answer(200, body)).install(monkeypatch)

    with pytest.raises(ApiError) as raised:
        await _generate()

    _assert_outcome(raised.value, expected)


def _with_choice(**choice: Any) -> dict[str, Any]:
    """A completion whose one choice is `choice` (an index is filled in)."""
    return {**_completion(), "choices": [{"index": 0, **choice}]}


def _message(content: str | None) -> dict[str, Any]:
    return {"role": "assistant", "content": content}


def _error_in_choice(status: Any, **metadata: Any) -> dict[str, Any]:
    error: dict[str, Any] = {"code": status, "message": "The provider's own words."}
    if metadata:
        error["metadata"] = metadata
    return error


async def _generate_expecting_an_api_error(
    monkeypatch: pytest.MonkeyPatch, answer: Callable[[], Any]
) -> ApiError:
    """`generate` over a mock transport that answers `answer()`. A raw exception of any other kind
    (an AttributeError from reading a completion that is not one) fails the test by name."""
    Wire(lambda request: answer()).install(monkeypatch)
    try:
        await _generate()
    except ApiError as error:
        return error
    except AttributeError as leaked:
        pytest.fail(f"a raw AttributeError escaped generate(): {leaked}")
    pytest.fail("generate() returned an answer")


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        # OpenRouter's documented shape for a generation that fails AFTER the request was
        # accepted: HTTP 200, the error inside the choice, finish_reason "error"
        pytest.param(
            {
                "finish_reason": "error",
                "message": _message(""),
                "error": _error_in_choice(402, error_type="payment_required"),
            },
            OUT_OF_CREDIT,
            id="402 with no text",
        ),
        pytest.param(
            {
                "finish_reason": "error",
                "message": _message("Dear hiring manager, I am wri"),
                "error": _error_in_choice(429),
            },
            RATE_LIMITED,
            id="429 after some text",
        ),
        pytest.param(
            {
                "finish_reason": "error",
                "message": _message(None),
                "error": _error_in_choice(502, error_type="provider_unavailable"),
            },
            PROVIDER_ERROR,
            id="502 with no content at all",
        ),
        pytest.param(
            {
                "finish_reason": "error",
                "message": _message("rejected"),
                "error": _error_in_choice(401),
            },
            KEY_REJECTED,
            id="401",
        ),
        pytest.param(
            {
                "finish_reason": "error",
                "message": _message(""),
                "error": _error_in_choice(403, error_type="content_policy_violation"),
            },
            REQUEST_REJECTED,
            id="403 that is a refusal of the content",
        ),
        # no error object to read a status from
        pytest.param(
            {"finish_reason": "error", "message": _message("")}, EMPTY, id="no error object"
        ),
        pytest.param(
            {"finish_reason": "error", "message": _message("half an answer")},
            EMPTY,
            id="no error object, some text",
        ),
        pytest.param(
            {"finish_reason": "error", "message": _message(""), "error": _error_in_choice("odd")},
            EMPTY,
            id="an error object with no usable status",
        ),
        # an error object is enough, whatever the finish reason says
        pytest.param(
            {
                "finish_reason": "stop",
                "message": _message("text"),
                "error": _error_in_choice(429),
            },
            RATE_LIMITED,
            id="an error object under a finish reason of stop",
        ),
    ],
)
async def test_an_error_inside_the_first_choice_of_a_200_is_classified_not_returned(
    monkeypatch: pytest.MonkeyPatch, choice: dict[str, Any], expected: tuple[Any, ...]
) -> None:
    """The answer is not an answer, even when it holds some text: a generation that ended in an
    error was cut off. Before this, the empty or partial text came back as a success and its
    caller treated the person's empty account or throttled key as a bad answer."""
    error = await _generate_expecting_an_api_error(
        monkeypatch, lambda: _answer(200, _with_choice(**choice))
    )

    _assert_outcome(error, expected)


@pytest.mark.parametrize(
    ("choice", "text"),
    [
        # reasoning that used up max_tokens: a completion that happens to be empty
        pytest.param({"finish_reason": "length", "message": _message("")}, "", id="length, empty"),
        pytest.param(
            {"finish_reason": "length", "message": _message("cut off at the lim")},
            "cut off at the lim",
            id="length, some text",
        ),
        pytest.param(
            {"finish_reason": "content_filter", "message": {**_message(""), "refusal": "no"}},
            "",
            id="content_filter",
        ),
        pytest.param({"finish_reason": "stop", "message": _message(None)}, "", id="no content"),
        pytest.param({"finish_reason": None, "message": _message("x")}, "x", id="no finish reason"),
        # only an error OBJECT counts: free text in the field is not read as one
        pytest.param(
            {"finish_reason": "stop", "message": _message("fine"), "error": "text"},
            "fine",
            id="an error that is not an object",
        ),
    ],
)
async def test_a_completion_that_is_merely_empty_or_short_is_still_a_completion(
    monkeypatch: pytest.MonkeyPatch, choice: dict[str, Any], text: str
) -> None:
    Wire(lambda request: _answer(200, _with_choice(**choice))).install(monkeypatch)

    assert await _generate() == llm_client.LLMResponse(content=text)


def _html() -> Any:
    return HTTP.Response(
        200, content=b"<html>x</html>", headers={"content-type": "text/html; charset=utf-8"}
    )


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        # a proxy, a captive portal or a wrong base URL answering 200 with something else: the
        # SDK hands back the text or the list it parsed, not a completion object
        pytest.param(_html, EMPTY, id="html"),
        pytest.param(lambda: _answer(200, None), EMPTY, id="empty body"),
        pytest.param(lambda: _answer(200, [1, 2, 3]), EMPTY, id="json list"),
        pytest.param(lambda: _answer(200, "just text"), EMPTY, id="json string"),
        pytest.param(lambda: _answer(200, 7), EMPTY, id="json number"),
        pytest.param(lambda: _answer(200, {"choices": "none"}), EMPTY, id="choices not a list"),
        pytest.param(lambda: _answer(200, {"choices": [None]}), EMPTY, id="a choice that is null"),
        pytest.param(lambda: _answer(200, {"choices": ["x"]}), EMPTY, id="a choice that is text"),
        # a choice with no message
        pytest.param(
            lambda: _answer(200, _with_choice(finish_reason="stop", message=None)),
            EMPTY,
            id="message: null",
        ),
        pytest.param(
            lambda: _answer(200, _with_choice(finish_reason="stop")), EMPTY, id="no message"
        ),
        # ... and OpenRouter's own account of why there is none
        pytest.param(
            lambda: _answer(
                200, _with_choice(finish_reason="error", error={"code": 502, "message": "x"})
            ),
            PROVIDER_ERROR,
            id="no message, error 502 in the choice",
        ),
    ],
)
async def test_a_200_that_is_not_a_completion_is_a_provider_failure_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, answer: Callable[[], Any], expected: tuple[Any, ...]
) -> None:
    error = await _generate_expecting_an_api_error(monkeypatch, answer)

    _assert_outcome(error, expected)
    assert error.status_code == 503  # retryable, and not an INTERNAL_ERROR


async def test_a_200_that_is_not_a_completion_is_not_reported_to_the_tracker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It used to be an AttributeError: a 500 INTERNAL_ERROR and an issue in the tracker for what
    is the provider's (or a proxy's) doing."""
    sdk = FakeSentry()
    monkeypatch.setattr(error_reporting, "_sdk", sdk)
    error = await _generate_expecting_an_api_error(monkeypatch, _html)

    error_reporting.report_exception(error)

    assert sdk.captured == []


# -- the time limit and the retry cap --------------------------------------------------------------


async def test_the_client_is_built_with_the_explicit_timeout_and_one_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = Wire(lambda request: _answer(200, _completion())).install(monkeypatch)

    await _generate(max_tokens=1000)

    [options] = wire.built
    assert options["max_retries"] == 1
    timeout = options["timeout"]
    assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (
        10.0,
        120.0,
        120.0,
        120.0,
    )
    # and the SDK client really holds them (the default it would otherwise have is 600 s)
    assert wire.clients[0].max_retries == 1
    assert cast(Any, wire.clients[0].timeout).read == 120.0


def test_the_constants_are_the_ones_chosen() -> None:
    assert MAX_SDK_RETRIES == 1  # the SDK's own default is 2: see the constant's docstring
    assert DEFAULT_TIMEOUT_SECONDS == 120.0
    assert LONG_GENERATION_TIMEOUT_SECONDS == 300.0
    assert LONG_GENERATION_TOKENS == 4_000
    assert CONNECT_TIMEOUT_SECONDS == 10.0


@pytest.mark.parametrize(
    ("max_tokens", "seconds"),
    [
        (1, 120.0),
        (500, 120.0),
        (3_500, 120.0),  # the longest answer the built-in engine asks for
        (4_000, 120.0),
        (4_001, 300.0),
        (8_000, 300.0),  # the resume-to-profile conversion
        (None, 300.0),  # no cap: the model's own limit, as the job scorer asks
    ],
)
def test_a_modest_answer_gets_the_default_and_a_long_or_uncapped_one_gets_more(
    max_tokens: int | None, seconds: float
) -> None:
    assert default_timeout_seconds(max_tokens) == seconds


@pytest.mark.parametrize(
    ("max_tokens", "timeout", "read", "connect"),
    [
        (100, None, 120.0, 10.0),
        (None, None, 300.0, 10.0),
        (8_000, None, 300.0, 10.0),
        (100, 30.0, 30.0, 10.0),  # an explicit value wins, whatever the size asked for
        (8_000, 30.0, 30.0, 10.0),
        (None, 45.5, 45.5, 10.0),
        (100, 5.0, 5.0, 5.0),  # the connection never gets longer than the whole call
        (100, 10.0, 10.0, 10.0),
    ],
)
async def test_the_timeout_is_the_default_for_the_size_unless_the_caller_gives_one(
    monkeypatch: pytest.MonkeyPatch,
    max_tokens: int | None,
    timeout: float | None,
    read: float,
    connect: float,
) -> None:
    wire = Wire(lambda request: _answer(200, _completion())).install(monkeypatch)

    await _generate(max_tokens=max_tokens, **({} if timeout is None else {"timeout": timeout}))

    seen = wire.built[0]["timeout"]
    assert (seen.read, seen.write, seen.pool, seen.connect) == (read, read, read, connect)


@pytest.mark.parametrize("timeout", [0, 0.0, -1, -0.5])
async def test_a_timeout_that_is_not_positive_is_a_programming_error(
    monkeypatch: pytest.MonkeyPatch, timeout: float
) -> None:
    wire = Wire(lambda request: _answer(200, _completion())).install(monkeypatch)
    with pytest.raises(ValueError, match=r"^timeout must be positive$"):
        await _generate(timeout=timeout)
    assert wire.built == []  # no client was built and nothing was sent


async def test_one_call_never_takes_longer_than_its_timeout_whatever_the_sdk_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK's own limits are per operation and its retries come on top: the whole call is
    cut off by a deadline of its own, and the person is told the provider took too long."""

    async def hang(request: Any) -> Any:
        await asyncio.sleep(30)

    Wire(hang).install(monkeypatch)

    started = time.perf_counter()
    with pytest.raises(ApiError) as raised:
        await _generate(timeout=0.2)
    elapsed = time.perf_counter() - started

    _assert_outcome(raised.value, TIMED_OUT)
    assert isinstance(raised.value.__cause__, TimeoutError)
    assert 0.15 < elapsed < 3.0


async def test_the_deadline_covers_the_sdks_retry_as_well(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def slow_failure(request: Any) -> Any:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.4)
        return _answer(502, _error_body(502), {"retry-after-ms": "1"})

    Wire(slow_failure).install(monkeypatch)

    started = time.perf_counter()
    with pytest.raises(ApiError) as raised:
        await _generate(timeout=0.6)  # one attempt fits, two do not

    _assert_outcome(raised.value, TIMED_OUT)
    assert calls == 2
    assert time.perf_counter() - started < 2.0


async def test_a_call_that_is_cancelled_stops_and_closes_its_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The built-in engine's own limit cancels a call that is in flight."""
    started = asyncio.Event()

    async def hang(request: Any) -> Any:
        started.set()
        await asyncio.sleep(30)

    wire = Wire(hang).install(monkeypatch)
    task = asyncio.ensure_future(_generate())
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert wire.clients[0].is_closed()


# -- how the client's limits compose with the built-in engine's ---------------------------------


def test_every_call_the_engine_makes_is_cut_off_before_the_engines_own_limit() -> None:
    """The engine bounds a whole run at 180 s. A single call that asks for the most the engine
    ever asks for is limited to less, so a stuck call ends as a provider failure that says so
    rather than being cancelled by the engine's clock with no word about why."""
    longest_ask = engine_body._BODY_MAX_TOKENS
    assert longest_ask <= LONG_GENERATION_TOKENS
    assert default_timeout_seconds(longest_ask) < pipeline.APPLY_TIMEOUT_SECONDS
    assert default_timeout_seconds(longest_ask) == 120.0 == DEFAULT_TIMEOUT_SECONDS
    assert pipeline.APPLY_TIMEOUT_SECONDS == 180.0


# -- the real client is still unreachable from a test unless it is replaced ------------------------


async def test_the_real_client_still_cannot_be_built_by_a_test_that_does_not_replace_it() -> None:
    with pytest.raises(AssertionError, match="real model client"):
        await _generate()
