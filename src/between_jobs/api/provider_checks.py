"""Checking a model provider's key and listing its models, over the registry's addresses.

TWO CALLS, ONE ANSWER SHAPE.

- `check_key`: the cheapest honest proof a key is good. Where a provider has a free call that
  needs the key and generates nothing (a model list, a balance), that is the check; the key is
  stored only after it passes. A provider with no documented free call (Qwen, the custom hosts)
  is asked for its model list anyway; a 404 or 405 there proves nothing either way, so the
  check says it is `Inconclusive` and the caller falls back to one tiny real completion with a
  model the person named.
- `list_models`: the account's models, normalised to `{id, name, context_length, created}`,
  newest first, capped.

Every call is made through `provider_http.fetch_json` (checked destination, no redirect, bounded
size and time). What a provider answers is read, never echoed: failures are classified from the
status and a few fixed fields of the body (`llm_client.status_reason`), and the message the
person sees is written here. The key is only ever a request header.

WHAT EACH PROVIDER GIVES BACK differs a lot, and none of it was seen against a live service
(see provider_registry.py). OpenAI returns no context length and no name, and lists embedding,
speech and image models next to chat ones; those are dropped by a name filter that is a
heuristic, documented where it is. Anthropic and DeepSeek say more, Gemini needs its native
list because the compatibility one is undocumented, Qwen's list is undocumented altogether.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from .errors import ApiError
from .llm_client import ProviderReason, generate, outcome_for, status_reason
from .provider_http import UpstreamAnswer, UpstreamFailure, fetch_json
from .provider_registry import OPENROUTER_BASE_URL, ProviderSpec

MAX_MODELS_RETURNED = 500
"""The most models one answer carries. A larger list is cut, newest first, and says so."""

MAX_ENTRIES_SCANNED = 5_000
"""The most entries of a provider's list that are looked at, so a hostile or broken answer
within the byte limit still costs a bounded amount of work."""

_INTEGRATIONS_PATH = "/profile/integrations"

_ANTHROPIC_VERSION = "2023-06-01"
"""The API version header Anthropic's native routes require. Documented in its models page."""

_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,199}")


class Inconclusive(Exception):
    """The provider's list route is not there or not documented, so it cannot say whether a
    key is good. Not a failure: the caller checks with a real completion instead."""


@dataclass(frozen=True, slots=True)
class ModelEntry:
    id: str
    name: str
    context_length: int | None
    created: int | None

    def as_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "context_length": self.context_length,
            "created": self.created,
        }


@dataclass(frozen=True, slots=True)
class ModelList:
    models: list[ModelEntry]
    truncated: bool
    listing: str
    """"live", or "unavailable" for a best-effort provider that has no list."""


# -- requests ----------------------------------------------------------------------------------


def _auth_headers(spec: ProviderSpec, secret: str) -> dict[str, str]:
    if spec.auth == "anthropic":
        return {"x-api-key": secret, "anthropic-version": _ANTHROPIC_VERSION}
    if spec.auth == "google":
        return {"x-goog-api-key": secret}
    return {"Authorization": f"Bearer {secret}", "Accept": "application/json"}


def _models_request(spec: ProviderSpec, base_url: str | None) -> tuple[str, dict[str, str]]:
    base = (base_url or spec.base_url or OPENROUTER_BASE_URL).rstrip("/")
    if spec.models_format == "anthropic":
        # 1000 is the documented maximum page; one page is all that is read.
        return f"{base}/models", {"limit": "1000"}
    if spec.models_format == "gemini":
        # The compatibility route's list has no documented shape; the native one does.
        return f"{base.removesuffix('/openai')}/models", {"pageSize": "1000"}
    return f"{base}/models", {}


def _key_check_request(spec: ProviderSpec, base_url: str | None) -> tuple[str, dict[str, str]]:
    base = (base_url or spec.base_url or OPENROUTER_BASE_URL).rstrip("/")
    if spec.key_check == "balance":
        return f"{base}/user/balance", {}
    url, params = _models_request(spec, base_url)
    # One entry is enough to prove a key; a smaller answer is a cheaper one.
    if spec.models_format == "anthropic":
        params = {"limit": "1"}
    elif spec.models_format == "gemini":
        params = {"pageSize": "1"}
    return url, params


# -- failures ----------------------------------------------------------------------------------


def _error_object(body: Any) -> Any:
    """The `error` object of an error answer (`{"error": {...}}`, the shape OpenAI, Anthropic
    and most others use), else the body itself: what the classifier reads codes from, as it does
    for the SDK's `body`."""
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return body["error"]
    return body


_ABOUT_THE_ACCOUNT = frozenset({"FAILED_PRECONDITION"})
"""Google's `status` for a 400 that is the account and not the key: billing not enabled, or
the free tier not available where the request comes from (its errors page)."""


def _status_word(error: Any) -> str | None:
    value = error.get("status") if isinstance(error, dict) else None
    return value if isinstance(value, str) else None


def _reason_of(spec: ProviderSpec, answer: UpstreamAnswer) -> ProviderReason | None:
    """None for a 2xx, else why not. A bare GET has no parameters to get wrong, so a 4xx that
    the general rules call a bad request is this provider's way of refusing the key when its
    registry entry says so, and an unexpected answer otherwise."""
    if 200 <= answer.status < 300:
        return None
    error = _error_object(answer.body)
    reason = status_reason(answer.status, error)
    if reason == "request_rejected":
        if _status_word(error) in _ABOUT_THE_ACCOUNT:
            return "request_rejected"
        return "key_rejected" if answer.status in spec.bad_key_statuses else "provider_error"
    if reason == "unknown":  # a redirect, or any status that is not an answer to this
        return "provider_error"
    return reason


_REASON_TEXT: dict[ProviderReason, str] = {
    "key_rejected": "{name} rejected that key.{hint}",
    "out_of_credit": "{name} says that account has no credit left. Add credit, then try again.",
    "request_rejected": (
        "{name} turned that request down. That points at the account rather than the key -- "
        "billing that is not enabled, or a region the service is not offered in. Check the "
        "account with {name}, then try again."
    ),
    "rate_limited": "{name} is rate limiting this key. Wait a minute, then try again.",
    "provider_error": "{name} couldn't answer that right now. Try again in a moment.",
    "unreachable": "Couldn't reach {name}. Try again in a moment.",
    "timeout": "{name} took too long to answer. Try again in a moment.",
    "empty_response": "{name} answered, but not in a form this server could read.",
    "response_too_large": "{name}'s answer was larger than this server will read.",
}


def provider_failure(spec: ProviderSpec, reason: ProviderReason) -> ApiError:
    """The `ApiError` for a failed key or list call: the code and retry advice that reason
    always has, worded with the provider's name, never with anything it said."""
    code, retryable = outcome_for(reason)
    text = _REASON_TEXT.get(reason, "{name} couldn't answer that right now. Try again in a moment.")
    hint = spec.rejected_hint if reason == "key_rejected" else ""
    return ApiError(
        code,
        text.format(name=spec.name, hint=hint),
        retryable=retryable,
        settings_path=_INTEGRATIONS_PATH if code == "PROVIDER_REJECTED" else None,
        details={"reason": reason, "provider": spec.id},
    )


async def _call(
    http: httpx.AsyncClient,
    spec: ProviderSpec,
    url: str,
    params: dict[str, str],
    secret: str,
) -> UpstreamAnswer:
    try:
        return await fetch_json(
            http, "GET", url, headers=_auth_headers(spec, secret), params=params or None
        )
    except UpstreamFailure as e:
        raise provider_failure(spec, e.reason) from e


# -- the key check -----------------------------------------------------------------------------


async def check_key(
    http: httpx.AsyncClient, spec: ProviderSpec, base_url: str | None, secret: str
) -> None:
    """Returns when the provider accepts the key. ApiError when it does not (or cannot say
    right now); `Inconclusive` when this provider has no route that could say."""
    url, params = _key_check_request(spec, base_url)
    answer = await _call(http, spec, url, params, secret)
    reason = _reason_of(spec, answer)
    if reason == "provider_error" and answer.status in (404, 405) and spec.listing_is_best_effort:
        raise Inconclusive
    if reason is not None:
        raise provider_failure(spec, reason)
    body = answer.body
    if spec.key_check == "balance":
        available = body.get("is_available") if isinstance(body, dict) else None
        if available is True:
            return
        if available is False:
            raise provider_failure(spec, "out_of_credit")
        raise provider_failure(spec, "empty_response")
    if not isinstance(body, dict):
        raise provider_failure(spec, "empty_response")


# -- the model list ------------------------------------------------------------------------------

_NOT_CHAT = re.compile(
    r"embed|moderation|transcribe|whisper|tts|speech|realtime|audio|dall-e|image|imagine|"
    r"video|sora|veo|imagen|davinci|babbage|rerank",
    re.IGNORECASE,
)
"""Names of models that do not do chat. OpenAI's, xAI's and Google's lists carry no capability
flag (OpenAI's has no context length or name at all), so this is a heuristic on the id: it can
hide a chat model whose name contains one of these words, and show a non-chat model without
one. The model chosen is proved by a real completion either way."""


def _as_int(value: object, *, low: int, high: int) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = int(value)
        return number if low <= number <= high else None
    return None


def _created(value: object) -> int | None:
    seconds = _as_int(value, low=1, high=4_102_444_800)
    if seconds is not None:
        return seconds
    if isinstance(value, str) and len(value) <= 40:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return _as_int(parsed.timestamp(), low=1, high=4_102_444_800)
    return None


def _entry(
    raw_id: object,
    name: object = None,
    context: object = None,
    created: object = None,
) -> ModelEntry | None:
    if not isinstance(raw_id, str) or not _MODEL_ID.fullmatch(raw_id):
        return None
    if ".." in raw_id or "//" in raw_id:  # what a chosen model may not contain either
        return None
    label = name if isinstance(name, str) and 0 < len(name) <= 200 else raw_id
    return ModelEntry(raw_id, label, _as_int(context, low=1, high=100_000_000), _created(created))


def _items(body: Any, key: str) -> list[Any]:
    items = body.get(key) if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise ValueError("no list")
    return items[:MAX_ENTRIES_SCANNED]


def _parse_openrouter(body: Any) -> list[ModelEntry]:
    found = []
    for item in _items(body, "data"):
        if not isinstance(item, dict):
            continue
        architecture = item.get("architecture")
        outputs = architecture.get("output_modalities") if isinstance(architecture, dict) else None
        if isinstance(outputs, list) and "text" not in outputs:
            continue
        entry = _entry(
            item.get("id"), item.get("name"), item.get("context_length"), item.get("created")
        )
        if entry:
            found.append(entry)
    return found


def _parse_openai(body: Any) -> list[ModelEntry]:
    now = datetime.now(UTC).date().isoformat()
    found = []
    for item in _items(body, "data"):
        if not isinstance(item, dict):
            continue
        shutdown = item.get("shutdown_date")
        if isinstance(shutdown, str) and shutdown[:10] and shutdown[:10] <= now:
            continue  # already retired; the list still carries it
        entry = _entry(item.get("id"), None, None, item.get("created"))
        if entry and not _NOT_CHAT.search(entry.id):
            found.append(entry)
    return found


def _parse_xai(body: Any) -> list[ModelEntry]:
    try:
        items = _items(body, "data")
    except ValueError:
        items = _items(body, "models")  # /v1/language-models names its list differently
    found = []
    for item in items:
        if not isinstance(item, dict):
            continue
        entry = _entry(item.get("id"), None, item.get("context_length"), item.get("created"))
        if entry and not _NOT_CHAT.search(entry.id):
            found.append(entry)
    return found


def _parse_anthropic(body: Any) -> list[ModelEntry]:
    found = []
    for item in _items(body, "data"):
        if not isinstance(item, dict) or item.get("lifecycle") == "retired":
            continue
        entry = _entry(
            item.get("id"),
            item.get("display_name"),
            item.get("max_input_tokens"),
            item.get("created_at"),
        )
        if entry:
            found.append(entry)
    return found


def _parse_gemini(body: Any) -> list[ModelEntry]:
    found = []
    for item in _items(body, "models"):
        if not isinstance(item, dict):
            continue
        methods = item.get("supportedGenerationMethods")
        if not isinstance(methods, list) or "generateContent" not in methods:
            continue
        name = item.get("name")
        if not isinstance(name, str):
            continue
        entry = _entry(
            name.removeprefix("models/"),
            item.get("displayName"),
            item.get("inputTokenLimit"),
            None,
        )
        if entry and not _NOT_CHAT.search(entry.id):
            found.append(entry)
    return found


def _parse_deepseek(body: Any) -> list[ModelEntry]:
    found = []
    for item in _items(body, "data"):
        if not isinstance(item, dict):
            continue
        entry = _entry(item.get("id"), item.get("name"), item.get("context_window"), None)
        if entry:
            found.append(entry)
    return found


_PARSERS: dict[str, Callable[[Any], list[ModelEntry]]] = {
    "openrouter": _parse_openrouter,
    "openai": _parse_openai,
    "xai": _parse_xai,
    "anthropic": _parse_anthropic,
    "gemini": _parse_gemini,
    "deepseek": _parse_deepseek,
}


def normalise_models(spec: ProviderSpec, body: Any) -> tuple[list[ModelEntry], bool]:
    """(the models, newest first and at most `MAX_MODELS_RETURNED`; whether any were cut).
    ValueError when the body has no list at all."""
    entries = _PARSERS[spec.models_format](body)
    unique = {entry.id: entry for entry in entries}
    ordered = sorted(
        unique.values(),
        key=lambda e: (e.created is None, -(e.created or 0), e.id),
    )
    return ordered[:MAX_MODELS_RETURNED], len(ordered) > MAX_MODELS_RETURNED


async def list_models(
    http: httpx.AsyncClient, spec: ProviderSpec, base_url: str | None, secret: str
) -> ModelList:
    url, params = _models_request(spec, base_url)
    answer = await _call(http, spec, url, params, secret)
    reason = _reason_of(spec, answer)
    if reason == "provider_error" and answer.status in (404, 405) and spec.listing_is_best_effort:
        return ModelList([], False, "unavailable")
    if reason is not None:
        raise provider_failure(spec, reason)
    try:
        models, truncated = normalise_models(spec, answer.body)
    except ValueError as e:
        raise provider_failure(spec, "empty_response") from e
    return ModelList(models, truncated, "live")


# -- proving a model --------------------------------------------------------------------------

PROBE_MAX_TOKENS = 16
"""The answer cap of the one completion that proves a model. A reasoning model may spend all of
it and answer nothing; that is still an accepted request, which is all the probe asks."""

PROBE_TIMEOUT_SECONDS = 30.0


async def probe_model(*, api_key: str, model: str, base_url: str | None) -> None:
    """One real, tiny completion through the provider's dialect. Returns when the provider
    accepted the request for this model; raises the `ApiError` `generate` classifies a failure
    with (an unknown or retired model, a rejected key, no credit, a throttle, an outage)."""
    await generate(
        api_key=api_key,
        model=model,
        base_url=base_url,
        system_prompt="Reply with the single word: ok",
        user_prompt="ok",
        max_tokens=PROBE_MAX_TOKENS,
        timeout=PROBE_TIMEOUT_SECONDS,
        allow_empty_truncation=True,
    )
