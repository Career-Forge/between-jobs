"""The one place that says which model providers this API can talk to, where, and how.

Everything a provider needs is data in `PROVIDERS` and `CUSTOM_ENDPOINTS` below, not an `if` in
a route: its address (or the rule that builds one), the pages a person needs to get and manage
a key, how to check a key for free, how to list its models, and its DIALECT.

THE DIALECT. "OpenAI-compatible" is true of the path and the message list, and of very little
else. The field that caps the answer, the way reasoning is turned off, and whether a
temperature is accepted all differ, and a field one provider requires another answers with a
400. `Dialect` records those three choices, and `dialect_for_base_url` infers it from the HOST
of the address the request goes to, so the 19 callers of `llm_client.generate` and the contract
with the resume engine need no new parameter. OpenRouter's dialect is the request this project
has always sent (tests/test_llm_request_payload.py pins its bytes). A host nobody has described
gets `CONSERVATIVE`: the one cap field every OpenAI-style server takes, nothing extra, no
temperature.

A dialect lists its reasoning settings as ATTEMPTS, best first. Which one a model accepts
depends on the model, not the provider (a flagship that cannot run with reasoning off answers
400 to the field that is right for its small sibling), and a model list says too little to
decide ahead of time. `llm_client.generate` therefore tries the first and, only when the
provider answers 400 or 422, the next, up to the length of the list (at most three requests,
inside the one time limit) and remembers which one worked for that model. The last attempt of
every dialect other than OpenRouter's is the plain request with no extra field.

WHERE AN ADDRESS COMES FROM. Never from a request. A provider with one host has it here; Qwen's
is built from a region in a fixed list and a workspace id held to a strict pattern; the
`custom` provider takes an address only if, after normalising, it is exactly one entry of
`CUSTOM_ENDPOINTS`. `storable_base_url` checks what a person sent, `effective_base_url` checks
what is stored (a row written by an older version is checked too) and returns the address a
call may use.

WHAT IS NOT KNOWN. The facts below come from each provider's documentation as read on the date
in `FACTS_CHECKED`. Where the documentation was silent, unclear or contradicted itself, the
choice made is the conservative one and the comment says so and names the page to re-read. A
real key was not available, so no provider's behaviour here has been seen against the live
service.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal
from urllib.parse import urlsplit

from .errors import ApiError

FACTS_CHECKED = "2026-10-18"
"""When the provider documentation behind this file was last read."""

OPENROUTER = "openrouter"
CUSTOM = "custom"
QWEN = "qwen"


# -- dialects ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Dialect:
    name: str
    token_field: Literal["max_tokens", "max_completion_tokens"]
    """The field that caps the answer."""
    send_null_cap: bool
    """OpenRouter's historic request carries `"max_tokens": null` when no cap is given. Kept
    byte for byte; every other dialect leaves the field out instead."""
    temperature: Literal["send", "omit"]
    attempts: tuple[Mapping[str, Any], ...]
    """Extra top-level body fields, one mapping per attempt, best first (see the module
    docstring). Never empty."""


def _attempts(*steps: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    return tuple(MappingProxyType(dict(step)) for step in steps)


OPENROUTER_DIALECT = Dialect(
    "openrouter",
    "max_tokens",
    True,
    "send",
    # OpenRouter's documented, cross-model way to turn reasoning off; models without
    # reasoning ignore it. No second attempt: this is the request it has always received.
    _attempts({"reasoning": {"effort": "none"}}),
)

CONSERVATIVE_DIALECT = Dialect("conservative", "max_tokens", False, "omit", _attempts({}))
"""For a host this file does not describe. `max_tokens` is the cap field every OpenAI-style
server takes; no extra field can be rejected; no temperature, because some reasoning models
refuse one."""

OPENAI_DIALECT = Dialect(
    "openai",
    # Chat Completions: `max_completion_tokens` counts reasoning and visible tokens;
    # `max_tokens` is deprecated and refused by the o-series.
    "max_completion_tokens",
    False,
    # Reasoning models refuse temperature unless reasoning is off.
    "omit",
    # `none` is accepted by the small models and refused (400) by the flagship, which takes
    # only low and up; plain last, for a model that takes neither.
    _attempts({"reasoning_effort": "none"}, {"reasoning_effort": "low"}, {}),
)

XAI_DIALECT = Dialect(
    "xai",
    # xAI's comparison page says Chat Completions `max_tokens` maps onto the Responses
    # `max_output_tokens`, so this field is documented. `max_completion_tokens` is reported by
    # a third party only. Re-check: https://docs.x.ai/developers/model-capabilities/legacy/chat-completions
    "max_tokens",
    False,
    # Not found in xAI's pages; unverified, so not sent.
    "omit",
    # xAI says reasoning cannot be disabled on the current Grok models; `low` is the least.
    # A model without reasoning may refuse the field, hence the plain second attempt.
    # Re-check: https://docs.x.ai/developers/model-capabilities/text/reasoning
    _attempts({"reasoning_effort": "low"}, {}),
)

ANTHROPIC_DIALECT = Dialect(
    "anthropic",
    "max_tokens",  # the compatibility layer lists both as fully supported
    False,
    # Models from 4.7 on answer 400 to any temperature below 1; the docs for the 5.x models
    # were not read for this, so it is left out.
    "omit",
    # The layer ignores `reasoning_effort`; thinking is controlled by the native field.
    # `disabled` is refused by some models, `between_tools` is accepted by one, and thinking
    # tokens count toward the cap. Re-check: https://platform.claude.com/docs/en/build-with-claude/thinking
    _attempts({"thinking": {"type": "disabled"}}, {"thinking": {"type": "between_tools"}}, {}),
)

GEMINI_DIALECT = Dialect(
    "gemini",
    # UNVERIFIED: the compatibility page names neither cap field. `max_tokens` is the
    # conservative choice. Re-check: https://ai.google.dev/gemini-api/docs/openai
    "max_tokens",
    False,
    # Gemini 3.6 and later ignore a custom temperature and warn that they will refuse it.
    "omit",
    # Reasoning cannot be turned off on the 3.x models; `minimal` is the least where it is
    # accepted, `low` elsewhere. How the layer maps `minimal` for every model is not
    # documented. Re-check: https://ai.google.dev/gemini-api/docs/thinking
    _attempts({"reasoning_effort": "minimal"}, {"reasoning_effort": "low"}, {}),
)

DEEPSEEK_DIALECT = Dialect(
    "deepseek",
    "max_tokens",  # the only cap field in its schema
    False,
    # Accepted with thinking off, silently ignored with it on; never an error.
    "send",
    # Thinking is on by default. Re-check: https://api-docs.deepseek.com/guides/thinking_mode
    _attempts({"thinking": {"type": "disabled"}}, {}),
)

QWEN_DIALECT = Dialect(
    "qwen",
    # `max_completion_tokens` is documented for the newest models only, `max_tokens` is the
    # safe field outside thinking mode, and thinking is switched off below.
    "max_tokens",
    False,
    "send",  # 0 <= t < 2
    # `enable_thinking` is not an OpenAI field: it is a top-level body field on this API.
    # A model that cannot be switched off refuses it, hence the plain second attempt.
    # Re-check: https://docs.qwencloud.com/developer-guides/text-generation/thinking
    _attempts({"enable_thinking": False}, {}),
)

OPENAI_STYLE_DIALECT = Dialect("openai-style", "max_tokens", False, "send", _attempts({}))
"""Groq, Mistral, Together and Fireworks: `max_tokens`, a temperature, nothing extra. Their
reasoning controls differ per model and were not researched; the plain request is the one that
cannot be refused for that reason."""

COHERE_DIALECT = Dialect(
    "cohere",
    "max_tokens",
    False,
    "send",
    # Only `none` and `high` are supported. Re-check: https://docs.cohere.com/docs/compatibility-api
    _attempts({"reasoning_effort": "none"}, {}),
)

_QWEN_HOST_SUFFIX = ".maas.aliyuncs.com"


def _host_of(base_url: str | None) -> str | None:
    if not base_url:
        return None
    try:
        host = urlsplit(base_url.strip()).hostname
    except ValueError:
        return None
    return host.lower() if host else None


_DIALECT_BY_HOST: dict[str, Dialect] = {
    "openrouter.ai": OPENROUTER_DIALECT,
    "api.openai.com": OPENAI_DIALECT,
    "us.api.openai.com": OPENAI_DIALECT,
    "eu.api.openai.com": OPENAI_DIALECT,
    "api.x.ai": XAI_DIALECT,
    "us.api.x.ai": XAI_DIALECT,
    "api.anthropic.com": ANTHROPIC_DIALECT,
    "generativelanguage.googleapis.com": GEMINI_DIALECT,
    "api.deepseek.com": DEEPSEEK_DIALECT,
    "maas.qwencloudapi.com": QWEN_DIALECT,
    "api.groq.com": OPENAI_STYLE_DIALECT,
    "api.mistral.ai": OPENAI_STYLE_DIALECT,
    "api.together.ai": OPENAI_STYLE_DIALECT,
    "api.fireworks.ai": OPENAI_STYLE_DIALECT,
    "api.cohere.ai": COHERE_DIALECT,
}


def dialect_for_base_url(base_url: str | None) -> Dialect:
    """The dialect of the host a request goes to. No address means OpenRouter, the default of
    every caller; an address this file does not describe means `CONSERVATIVE_DIALECT`."""
    if not base_url:
        return OPENROUTER_DIALECT
    host = _host_of(base_url)
    if host is None:
        return CONSERVATIVE_DIALECT
    if host in _DIALECT_BY_HOST:
        return _DIALECT_BY_HOST[host]
    if host.endswith(_QWEN_HOST_SUFFIX):
        return QWEN_DIALECT
    return CONSERVATIVE_DIALECT


# -- providers -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Region:
    id: str
    name: str
    needs_workspace: bool
    get_key_url: str


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    id: str
    name: str
    base_url: str | None
    """The one address, or None when it is built (Qwen) or chosen from a list (custom)."""
    get_key_url: str
    manage_url: str
    key_hint: str
    """What a key looks like, in words. A hint for the person; never enforced, because most
    of these prefixes are not stated in the provider's own documentation."""
    key_check: Literal["openrouter_key", "models", "balance"]
    models_format: Literal["openrouter", "openai", "anthropic", "gemini", "xai", "deepseek"]
    auth: Literal["bearer", "anthropic", "google"]
    bad_key_statuses: frozenset[int]
    """The statuses a key check answers for a key the provider refuses. xAI and Google fold a
    bad key into 400 as well; the check is a bare GET, so a 400 there can only be the key."""
    listing_is_best_effort: bool
    """True when the model-list route is undocumented or varies (Qwen, the custom hosts): a
    404 there is "no list", not an error."""
    rejected_hint: str = ""
    """Appended to "<name> rejected that key." when the likely cause is a mix-up of platform or
    region rather than a mistyped key."""
    docs_url: str = ""
    """The page to re-read when this provider's behaviour looks different."""
    regions: tuple[Region, ...] = ()


_BAD_KEY = frozenset({401, 403})

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
"""Same value as `llm_client.OPENROUTER_BASE_URL` (a test holds the two together)."""

_QWEN_PLATFORM_ID = "qwencloud"
_MODEL_STUDIO_REGIONS = (
    ("cn-beijing", "China (Beijing)"),
    ("ap-southeast-1", "Singapore"),
    ("eu-central-1", "Germany (Frankfurt)"),
    ("ap-northeast-1", "Japan (Tokyo)"),
    ("cn-hongkong", "China (Hong Kong)"),
    ("us-east-1", "US (Virginia)"),
)
"""Alibaba Cloud Model Studio's regions, from its regions page. A key is bound to one of them."""

_QWEN_GLOBAL_URL = "https://maas.qwencloudapi.com/compatible-mode/v1"

PROVIDERS: dict[str, ProviderSpec] = {
    OPENROUTER: ProviderSpec(
        OPENROUTER,
        "OpenRouter",
        OPENROUTER_BASE_URL,
        "https://openrouter.ai/keys",
        "https://openrouter.ai/settings/keys",
        "Starts with sk-or-.",
        "openrouter_key",
        "openrouter",
        "bearer",
        _BAD_KEY,
        False,
        docs_url="https://openrouter.ai/docs/api-reference/overview",
    ),
    "openai": ProviderSpec(
        "openai",
        "OpenAI",
        "https://api.openai.com/v1",
        "https://platform.openai.com/settings/organization/api-keys",
        "https://platform.openai.com/settings/organization/limits",
        "Usually starts with sk-.",
        "models",
        "openai",
        "bearer",
        _BAD_KEY,
        False,
        docs_url="https://developers.openai.com/api/reference/resources/models/methods/list",
    ),
    "anthropic": ProviderSpec(
        "anthropic",
        "Anthropic",
        # Documented as a test-and-compare layer, not a production one.
        "https://api.anthropic.com/v1/",
        "https://platform.claude.com/settings/keys",
        "https://platform.claude.com/settings/billing",
        "Starts with sk-ant-.",
        "models",
        "anthropic",
        "anthropic",
        _BAD_KEY,
        False,
        docs_url="https://platform.claude.com/docs/en/api/openai-sdk",
    ),
    "gemini": ProviderSpec(
        "gemini",
        "Google Gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "https://aistudio.google.com/apikey",
        "https://aistudio.google.com/apikey",
        "A Google AI Studio key.",
        "models",
        "gemini",
        "google",
        # The docs' troubleshooting page says 403 is a bad key; INVALID_ARGUMENT (400) is
        # what a malformed one has been seen to produce. Unverified for this endpoint.
        frozenset({400, 401, 403}),
        False,
        docs_url="https://ai.google.dev/gemini-api/docs/troubleshooting",
    ),
    "xai": ProviderSpec(
        "xai",
        "xAI",
        "https://api.x.ai/v1",
        "https://console.x.ai/team/default/api-keys",
        "https://console.x.ai/team/default/api-keys",
        "Usually starts with xai-.",
        "models",
        "xai",
        "bearer",
        # xAI's debugging page lists a bad key under both 400 and 401.
        frozenset({400, 401, 403}),
        False,
        docs_url="https://docs.x.ai/developers/debugging",
    ),
    "deepseek": ProviderSpec(
        "deepseek",
        "DeepSeek",
        "https://api.deepseek.com",
        "https://platform.deepseek.com/api_keys",
        "https://platform.deepseek.com/top_up",
        "A key from the DeepSeek platform.",
        "balance",
        "deepseek",
        "bearer",
        _BAD_KEY,
        False,
        docs_url="https://api-docs.deepseek.com/api/get-user-balance",
    ),
    QWEN: ProviderSpec(
        QWEN,
        "Qwen Cloud",
        None,
        "https://home.qwencloud.com/api-keys",
        "https://home.qwencloud.com/api-keys",
        "Starts with sk-ws- on QwenCloud; Model Studio keys start with sk-.",
        "models",
        "openai",
        "bearer",
        _BAD_KEY,
        True,
        # The same 401 answers a wrong key and a key used on the wrong platform or region.
        rejected_hint=" A key only works on the platform and region it was created in -- check "
        "both.",
        docs_url="https://docs.qwencloud.com/api-reference/preparation/api-key",
        regions=(
            Region(_QWEN_PLATFORM_ID, "QwenCloud", False, "https://home.qwencloud.com/api-keys"),
            *(
                Region(
                    rid,
                    f"Alibaba Cloud Model Studio, {label}",
                    True,
                    f"https://modelstudio.console.alibabacloud.com/{rid}/model/settings/api-key",
                )
                for rid, label in _MODEL_STUDIO_REGIONS
            ),
        ),
    ),
    CUSTOM: ProviderSpec(
        CUSTOM,
        "Other OpenAI-compatible provider",
        None,
        "",
        "",
        "The key your provider gave you.",
        "models",
        "openai",
        "bearer",
        _BAD_KEY,
        True,
        docs_url="",
    ),
}


@dataclass(frozen=True, slots=True)
class CustomEndpoint:
    id: str
    name: str
    base_url: str
    get_key_url: str


CUSTOM_ENDPOINTS: dict[str, CustomEndpoint] = {
    e.id: e
    for e in (
        # Each address is the one the provider's own OpenAI-compatibility page tells you to use
        # (read 2026-10-18). Together also answered on api.together.xyz in the past; only the
        # address its current page names is allowed.
        CustomEndpoint(
            "groq", "Groq", "https://api.groq.com/openai/v1", "https://console.groq.com/keys"
        ),
        CustomEndpoint(
            "mistral", "Mistral", "https://api.mistral.ai/v1", "https://console.mistral.ai/api-keys"
        ),
        CustomEndpoint(
            "together",
            "Together AI",
            "https://api.together.ai/v1",
            "https://api.together.ai/settings/api-keys",
        ),
        CustomEndpoint(
            "fireworks",
            "Fireworks AI",
            "https://api.fireworks.ai/inference/v1",
            "https://app.fireworks.ai/settings/users/api-keys",
        ),
        CustomEndpoint(
            "cohere",
            "Cohere",
            "https://api.cohere.ai/compatibility/v1",
            "https://dashboard.cohere.com/api-keys",
        ),
    )
}
"""The only addresses the `custom` provider accepts. Adding one is a code change and a review,
on purpose: the server sends a person's key to whatever is here."""

_BY_URL = {e.base_url: e for e in CUSTOM_ENDPOINTS.values()}


def get_provider(provider_id: str) -> ProviderSpec | None:
    return PROVIDERS.get(provider_id)


# -- addresses -----------------------------------------------------------------------------


class BaseUrlRejected(ValueError):
    """An address this server will not send a key to. The message is safe to show."""


_URL_SAFE = re.compile(r"[A-Za-z0-9:/._-]{1,200}")
"""Everything an allowed address can contain. Anything else (a backslash, an @, a % escape, a
space, a control character, a non-ASCII letter) is refused before the address is parsed, so
no parser disagreement can turn an address into a different host."""

_WORKSPACE_ID = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{1,61}[A-Za-z0-9])")
"""A workspace id becomes a DNS label of the address, so it is held to one: ASCII letters,
digits and hyphens, 3 to 63 characters, no hyphen at either end. The real format is not stated
in Alibaba's documentation; this accepts every plausible id and nothing that could be a host."""

_QWEN_MODEL_STUDIO_URL = re.compile(
    r"https://([a-z0-9][a-z0-9-]{1,61}[a-z0-9])\.("
    + "|".join(re.escape(r) for r, _ in _MODEL_STUDIO_REGIONS)
    + r")\.maas\.aliyuncs\.com/compatible-mode/v1"
)


def _normalise_https(raw: str) -> tuple[str, str]:
    """(lower-cased host, path without a trailing slash) of an https address with nothing else
    in it, or BaseUrlRejected."""
    text = raw.strip()
    if not _URL_SAFE.fullmatch(text):
        raise BaseUrlRejected("That address has characters this server does not accept.")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as e:
        raise BaseUrlRejected("That address is not valid.") from e
    if (
        parts.scheme != "https"
        or parts.username is not None
        or parts.password is not None
        or port is not None
        or ":" in parts.netloc  # a port spelled out, even an empty one
        or parts.query
        or parts.fragment
        or not parts.hostname
    ):
        raise BaseUrlRejected("Only a plain https address on the standard port is accepted.")
    host = parts.hostname.lower()
    if host.endswith(".") or ".." in host:
        raise BaseUrlRejected("That address is not valid.")
    return host, parts.path.rstrip("/")


def allowlisted_endpoint(raw: str) -> CustomEndpoint:
    """The `CUSTOM_ENDPOINTS` entry `raw` names, exactly, or BaseUrlRejected."""
    host, path = _normalise_https(raw)
    endpoint = _BY_URL.get(f"https://{host}{path}")
    if endpoint is None:
        raise BaseUrlRejected(
            "That address is not one this server sends keys to. Allowed: "
            + ", ".join(e.name for e in CUSTOM_ENDPOINTS.values())
            + "."
        )
    return endpoint


def _qwen_url(region: str | None, workspace_id: str | None) -> str:
    chosen = region or _QWEN_PLATFORM_ID
    known = {r.id: r for r in PROVIDERS[QWEN].regions}
    if chosen not in known:
        raise BaseUrlRejected("That Qwen region is not one this server knows.")
    if not known[chosen].needs_workspace:
        if workspace_id:
            raise BaseUrlRejected("QwenCloud keys do not take a workspace id.")
        return _QWEN_GLOBAL_URL
    if not workspace_id or not _WORKSPACE_ID.fullmatch(workspace_id):
        raise BaseUrlRejected(
            "A Model Studio key needs its workspace id: letters, digits and hyphens only."
        )
    return f"https://{workspace_id.lower()}.{chosen}.maas.aliyuncs.com/compatible-mode/v1"


def storable_base_url(
    spec: ProviderSpec,
    *,
    region: str | None,
    workspace_id: str | None,
    base_url: str | None,
) -> str | None:
    """What to store for a key a person is saving, built from what they sent; BaseUrlRejected
    for anything this server will not call.

    None means "the provider's one address" (OpenRouter, OpenAI, ...): nothing is stored, so
    an address this file corrects later is picked up by every saved key. Only Qwen's built
    address and a custom provider's allowlisted address are stored."""
    if spec.id == QWEN:
        if base_url:
            raise BaseUrlRejected("Qwen's address is built from its region; do not send one.")
        return _qwen_url(region, workspace_id)
    if region or workspace_id:
        raise BaseUrlRejected(f"{spec.name} does not take a region or workspace id.")
    if spec.id == CUSTOM:
        if not base_url:
            raise BaseUrlRejected("Choose which provider's address to use.")
        return allowlisted_endpoint(base_url).base_url
    if base_url and not _is_the_providers_own(spec, base_url):
        raise BaseUrlRejected(f"{spec.name} does not take a custom address.")
    return None


def _is_the_providers_own(spec: ProviderSpec, raw: str) -> bool:
    try:
        host, path = _normalise_https(raw)
    except BaseUrlRejected:
        return False
    assert spec.base_url is not None
    return f"https://{host}{path}" == spec.base_url.rstrip("/")


def effective_base_url(provider_id: str, stored: str | None) -> str | None:
    """The address a call for this provider may use: the provider's own, or the stored one
    when it still passes the rule for that provider. None for OpenRouter, whose default every
    caller already relies on. Raises BaseUrlRejected for a stored address that no longer
    passes (a row written before this rule existed) and for a provider this file does not
    know."""
    spec = PROVIDERS.get(provider_id)
    if spec is None:
        raise BaseUrlRejected("That provider is not supported.")
    if spec.id == OPENROUTER:
        return None
    if spec.id == QWEN:
        if not stored:
            raise BaseUrlRejected("This Qwen key has no region saved. Save the key again.")
        host, path = _normalise_https(stored)
        url = f"https://{host}{path}"
        if url != _QWEN_GLOBAL_URL and not _QWEN_MODEL_STUDIO_URL.fullmatch(url):
            raise BaseUrlRejected("The saved Qwen address is not one this server accepts.")
        return url
    if spec.id == CUSTOM:
        if not stored:
            raise BaseUrlRejected("This key has no provider address saved. Save the key again.")
        return allowlisted_endpoint(stored).base_url
    assert spec.base_url is not None
    return spec.base_url


# -- what the web app is told ----------------------------------------------------------------


def public_catalog() -> list[dict[str, Any]]:
    """The provider list the web app draws its picker from: ids, names, the pages for getting
    and managing a key, regions, and the addresses the custom provider accepts. Public data,
    no secret in it."""
    out: list[dict[str, Any]] = []
    for spec in PROVIDERS.values():
        entry: dict[str, Any] = {
            "id": spec.id,
            "name": spec.name,
            "key_hint": spec.key_hint,
            "get_key_url": spec.get_key_url or None,
            "manage_url": spec.manage_url or None,
            "model_list": "best_effort" if spec.listing_is_best_effort else "live",
        }
        if spec.regions:
            entry["regions"] = [
                {
                    "id": r.id,
                    "name": r.name,
                    "needs_workspace": r.needs_workspace,
                    "get_key_url": r.get_key_url,
                }
                for r in spec.regions
            ]
        if spec.id == CUSTOM:
            entry["endpoints"] = [
                {
                    "id": e.id,
                    "name": e.name,
                    "base_url": e.base_url,
                    "get_key_url": e.get_key_url,
                }
                for e in CUSTOM_ENDPOINTS.values()
            ]
        out.append(entry)
    return out


def invalid_input(error: BaseUrlRejected) -> ApiError:
    return ApiError("INVALID_INPUT", str(error), details={"reason": "base_url_refused"})
