"""The provider registry (api/provider_registry.py): which dialect a host gets, and which
addresses a key may ever be sent to.

The address tests are the security core of the multi-provider work: a request may name a
provider, a region or a workspace id, and from those the server builds the one address it will
call, or refuses. Every shape of address that has been used to point a check at a different
host is tried against the allowlist here.
"""

# ruff: noqa: RUF001  (lookalike letters and spaces are the point of some cases below)

from __future__ import annotations

import json
from typing import Any

import pytest

from between_jobs.api import llm_client
from between_jobs.api.provider_registry import (
    CUSTOM,
    CUSTOM_ENDPOINTS,
    OPENROUTER,
    PROVIDERS,
    QWEN,
    BaseUrlRejected,
    allowlisted_endpoint,
    dialect_for_base_url,
    effective_base_url,
    public_catalog,
    storable_base_url,
)

# -- dialects --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("base_url", "name", "token_field", "temperature", "first_attempt"),
    [
        (None, "openrouter", "max_tokens", "send", {"reasoning": {"effort": "none"}}),
        ("", "openrouter", "max_tokens", "send", {"reasoning": {"effort": "none"}}),
        (
            "https://openrouter.ai/api/v1",
            "openrouter",
            "max_tokens",
            "send",
            {"reasoning": {"effort": "none"}},
        ),
        (
            "https://api.openai.com/v1",
            "openai",
            "max_completion_tokens",
            "omit",
            {"reasoning_effort": "none"},
        ),
        (
            "https://eu.api.openai.com/v1",
            "openai",
            "max_completion_tokens",
            "omit",
            {"reasoning_effort": "none"},
        ),
        ("https://api.x.ai/v1", "xai", "max_tokens", "omit", {"reasoning_effort": "low"}),
        (
            "https://api.anthropic.com/v1/",
            "anthropic",
            "max_tokens",
            "omit",
            {"thinking": {"type": "disabled"}},
        ),
        (
            "https://generativelanguage.googleapis.com/v1beta/openai/",
            "gemini",
            "max_tokens",
            "omit",
            {"reasoning_effort": "minimal"},
        ),
        (
            "https://api.deepseek.com",
            "deepseek",
            "max_tokens",
            "send",
            {"thinking": {"type": "disabled"}},
        ),
        (
            "https://maas.qwencloudapi.com/compatible-mode/v1",
            "qwen",
            "max_tokens",
            "send",
            {"enable_thinking": False},
        ),
        (
            "https://ws-abc123.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
            "qwen",
            "max_tokens",
            "send",
            {"enable_thinking": False},
        ),
        ("https://api.groq.com/openai/v1", "openai-style", "max_tokens", "send", {}),
        ("https://api.mistral.ai/v1", "openai-style", "max_tokens", "send", {}),
        ("https://api.together.ai/v1", "openai-style", "max_tokens", "send", {}),
        ("https://api.fireworks.ai/inference/v1", "openai-style", "max_tokens", "send", {}),
        (
            "https://api.cohere.ai/compatibility/v1",
            "cohere",
            "max_tokens",
            "send",
            {"reasoning_effort": "none"},
        ),
    ],
)
def test_each_host_gets_its_own_dialect(
    base_url: str | None,
    name: str,
    token_field: str,
    temperature: str,
    first_attempt: dict[str, Any],
) -> None:
    dialect = dialect_for_base_url(base_url)

    assert dialect.name == name
    assert dialect.token_field == token_field
    assert dialect.temperature == temperature
    assert dict(dialect.attempts[0]) == first_attempt


@pytest.mark.parametrize(
    "base_url",
    [
        "https://llm.example.test/v1",
        "https://api.openai.com.evil.example/v1",  # a lookalike is not OpenAI
        "https://notopenrouter.ai/api/v1",
        "https://evil.example/maas.aliyuncs.com",
        "https://maas.aliyuncs.com/v1",  # the suffix needs a workspace label before it
        "https://x.maas.aliyuncs.com.evil.example/v1",
        "http://localhost:11434/v1",
        "not a url",
        "https://",
    ],
)
def test_a_host_nobody_described_gets_the_conservative_dialect(base_url: str) -> None:
    dialect = dialect_for_base_url(base_url)

    assert dialect.name == "conservative"
    assert dialect.token_field == "max_tokens"
    assert dialect.temperature == "omit"
    assert [dict(a) for a in dialect.attempts] == [{}]


def test_the_host_is_matched_whatever_its_case() -> None:
    assert dialect_for_base_url("https://API.OpenAI.com/v1").name == "openai"


def test_every_dialect_ends_in_the_plain_request_except_openrouters() -> None:
    seen = {
        dialect_for_base_url(spec.base_url).name: dialect_for_base_url(spec.base_url)
        for spec in PROVIDERS.values()
        if spec.base_url
    }
    for url in (
        *(endpoint.base_url for endpoint in CUSTOM_ENDPOINTS.values()),
        "https://maas.qwencloudapi.com/compatible-mode/v1",  # Qwen's has no fixed address
    ):
        seen[dialect_for_base_url(url).name] = dialect_for_base_url(url)
    assert {"openrouter", "openai", "xai", "anthropic", "gemini", "deepseek", "qwen"} <= set(seen)
    for name, dialect in seen.items():
        assert dialect.attempts, name
        assert len(dialect.attempts) <= 3, "a request is retried at most twice"
        if name != "openrouter":
            assert dict(dialect.attempts[-1]) == {}, name
    assert len(seen["openrouter"].attempts) == 1


def test_openrouters_dialect_is_the_request_it_always_sent() -> None:
    dialect = dialect_for_base_url(None)

    assert dialect.send_null_cap is True
    assert [dict(a) for a in dialect.attempts] == [{"reasoning": {"effort": "none"}}]
    assert all(
        not d.send_null_cap
        for d in (dialect_for_base_url(s.base_url) for s in PROVIDERS.values() if s.base_url)
        if d.name != "openrouter"
    )


def test_the_registry_and_the_client_agree_on_openrouters_address() -> None:
    assert PROVIDERS[OPENROUTER].base_url == llm_client.OPENROUTER_BASE_URL


# -- the allowlist ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "https://api.groq.com/openai/v1",
        "https://api.groq.com/openai/v1/",
        "  https://api.groq.com/openai/v1  ",
        "HTTPS://API.GROQ.COM/openai/v1",
        "https://api.mistral.ai/v1",
        "https://api.together.ai/v1",
        "https://api.fireworks.ai/inference/v1",
        "https://api.cohere.ai/compatibility/v1",
    ],
)
def test_an_allowlisted_address_is_accepted_in_its_canonical_form(raw: str) -> None:
    endpoint = allowlisted_endpoint(raw)

    assert endpoint.base_url in {e.base_url for e in CUSTOM_ENDPOINTS.values()}
    assert endpoint.base_url == endpoint.base_url.rstrip("/")


@pytest.mark.parametrize(
    "raw",
    [
        # not https, or no scheme at all
        "http://api.groq.com/openai/v1",
        "//api.groq.com/openai/v1",
        "https:api.groq.com/openai/v1",
        "ftp://api.groq.com/openai/v1",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "",
        "   ",
        "api.groq.com/openai/v1",
        # userinfo: the real host is whatever follows the @
        "https://api.groq.com@evil.example/openai/v1",
        "https://user@api.groq.com/openai/v1",
        "https://user:pass@api.groq.com/openai/v1",
        "https://api.groq.com:pass@evil.example/openai/v1",
        # backslashes and escapes that parsers read differently
        "https://evil.example\\@api.groq.com/openai/v1",
        "https://evil.example%5c@api.groq.com/openai/v1",
        "https://api.groq.com%2e/openai/v1",
        "https://api.groq.com/openai/v1%2f..%2f",
        "https://api.groq.com/openai/v1%00",
        "https://api.groq.com/openai/v1\r\nHost: evil.example",
        "https://api.groq.com/openai/v1 evil",
        "https://api .groq.com/openai/v1",
        # internationalised names: no non-ASCII host is accepted, lookalikes included
        "https://аpi.groq.com/openai/v1",
        "https://api.groқ.com/openai/v1",
        "https://api.groq.com。evil.example/openai/v1",
        "https://xn--pi-gla.groq.com/openai/v1",
        # trailing dots and doubled dots
        "https://api.groq.com./openai/v1",
        "https://api.groq.com../openai/v1",
        "https://.api.groq.com/openai/v1",
        # ports, including the default one spelled out
        "https://api.groq.com:443/openai/v1",
        "https://api.groq.com:8443/openai/v1",
        "https://api.groq.com:/openai/v1",
        "https://api.groq.com:99999/openai/v1",
        # query, fragment, path games
        "https://api.groq.com/openai/v1?redirect=https://evil.example",
        "https://api.groq.com/openai/v1#@evil.example",
        "https://api.groq.com/openai/v1/../v2",
        "https://api.groq.com/openai/v1/extra",
        "https://api.groq.com/openai",
        "https://api.groq.com",
        "https://api.groq.com/OPENAI/V1",
        # a longer name that merely contains the host
        "https://evil.api.groq.com/openai/v1",
        "https://api.groq.com.evil.example/openai/v1",
        "https://evilapi.groq.com/openai/v1",
        # IP literals in every spelling, loopback, link-local and metadata addresses
        "https://127.0.0.1/openai/v1",
        "https://127.1/openai/v1",
        "https://2130706433/openai/v1",
        "https://0x7f000001/openai/v1",
        "https://0177.0.0.1/openai/v1",
        "https://169.254.169.254/latest/meta-data/",
        "https://10.0.0.1/v1",
        "https://192.168.1.1/v1",
        "https://[::1]/openai/v1",
        "https://[::ffff:127.0.0.1]/openai/v1",
        "https://[fe80::1]/openai/v1",
        "https://[2001:db8::1]:443/openai/v1",
        "https://localhost/openai/v1",
        "https://localhost:11434/v1",
        "https://metadata.google.internal/computeMetadata/v1",
        # too long
        "https://api.groq.com/" + "a" * 300,
    ],
)
def test_anything_that_is_not_exactly_an_allowlisted_address_is_refused(raw: str) -> None:
    with pytest.raises(BaseUrlRejected):
        allowlisted_endpoint(raw)


def test_every_allowlisted_address_is_a_plain_https_dns_name() -> None:
    for endpoint in CUSTOM_ENDPOINTS.values():
        assert endpoint.base_url.startswith("https://")
        host = endpoint.base_url.split("/")[2]
        assert host.replace(".", "").replace("-", "").isalpha(), host
        assert "." in host and ":" not in host and "@" not in host
        assert allowlisted_endpoint(endpoint.base_url) == endpoint


# -- what a person may send ---------------------------------------------------------------------


def _store(provider: str, **fields: str | None) -> str | None:
    kwargs: dict[str, str | None] = {"region": None, "workspace_id": None, "base_url": None}
    kwargs.update(fields)
    return storable_base_url(PROVIDERS[provider], **kwargs)


@pytest.mark.parametrize(
    "provider", ["openai", "anthropic", "gemini", "xai", "deepseek", "openrouter"]
)
def test_a_provider_with_one_address_stores_none_and_takes_no_other(provider: str) -> None:
    assert _store(provider) is None
    assert _store(provider, base_url=PROVIDERS[provider].base_url) is None
    for bad in (
        "https://evil.example/v1",
        "http://localhost:8000",
        "https://api.groq.com/openai/v1",
    ):
        with pytest.raises(BaseUrlRejected):
            _store(provider, base_url=bad)
    with pytest.raises(BaseUrlRejected):
        _store(provider, region="us")
    with pytest.raises(BaseUrlRejected):
        _store(provider, workspace_id="ws-abc123")


def test_the_custom_provider_needs_an_allowlisted_address() -> None:
    assert _store(CUSTOM, base_url="https://api.groq.com/openai/v1/") == (
        "https://api.groq.com/openai/v1"
    )
    for bad in (None, "", "https://evil.example/v1", "https://api.groq.com@evil.example/v1"):
        with pytest.raises(BaseUrlRejected):
            _store(CUSTOM, base_url=bad)
    with pytest.raises(BaseUrlRejected):
        _store(CUSTOM, base_url="https://api.groq.com/openai/v1", region="us-east-1")


def test_qwen_builds_its_address_from_a_region_and_never_takes_one() -> None:
    assert _store(QWEN) == "https://maas.qwencloudapi.com/compatible-mode/v1"
    assert _store(QWEN, region="qwencloud") == "https://maas.qwencloudapi.com/compatible-mode/v1"
    assert _store(QWEN, region="ap-southeast-1", workspace_id="WS-Abc123") == (
        "https://ws-abc123.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
    )
    with pytest.raises(BaseUrlRejected):
        _store(QWEN, base_url="https://maas.qwencloudapi.com/compatible-mode/v1")


@pytest.mark.parametrize(
    "workspace_id",
    [
        None,
        "",
        "ab",  # too short for a label we would build
        "-abc",
        "abc-",
        "a" * 64,
        "a.b",
        "evil.example/x",
        "ws abc",
        "ws_abc",
        "ws@evil.example",
        "ws:443",
        "ws\\x",
        "ws/../x",
        "ws%2e",
        "wѕ-abc",  # a Cyrillic s
        "ws-é",
        "ws-abc\n",
    ],
)
def test_a_workspace_id_that_could_be_a_host_is_refused(workspace_id: str | None) -> None:
    with pytest.raises(BaseUrlRejected):
        _store(QWEN, region="us-east-1", workspace_id=workspace_id)


@pytest.mark.parametrize(
    "region", ["evil", "us-east-2", "../x", "ap-southeast-1.evil.example", " "]
)
def test_a_region_outside_the_list_is_refused(region: str) -> None:
    with pytest.raises(BaseUrlRejected):
        _store(QWEN, region=region, workspace_id="ws-abc123")


def test_the_global_qwen_platform_takes_no_workspace() -> None:
    with pytest.raises(BaseUrlRejected):
        _store(QWEN, region="qwencloud", workspace_id="ws-abc123")


def test_a_model_studio_region_needs_a_workspace() -> None:
    with pytest.raises(BaseUrlRejected):
        _store(QWEN, region="cn-beijing")


# -- what is stored: checked again whenever it is used ---------------------------------------------


def test_openrouter_is_always_the_default_address_whatever_is_stored() -> None:
    assert effective_base_url(OPENROUTER, None) is None
    assert effective_base_url(OPENROUTER, "https://evil.example/v1") is None


def test_a_provider_with_one_address_ignores_what_is_stored() -> None:
    assert effective_base_url("openai", "https://evil.example/v1") == "https://api.openai.com/v1"
    assert effective_base_url("deepseek", None) == "https://api.deepseek.com"


def test_a_stored_custom_address_must_still_be_allowlisted() -> None:
    assert effective_base_url(CUSTOM, "https://api.mistral.ai/v1") == "https://api.mistral.ai/v1"
    for stored in (None, "", "https://evil.example/v1", "http://localhost:11434/v1"):
        with pytest.raises(BaseUrlRejected):
            effective_base_url(CUSTOM, stored)


def test_a_stored_qwen_address_must_still_be_one_the_server_builds() -> None:
    good = "https://ws-abc123.eu-central-1.maas.aliyuncs.com/compatible-mode/v1"
    assert effective_base_url(QWEN, good) == good
    assert effective_base_url(QWEN, good + "/") == good
    for stored in (
        None,
        "",
        "https://evil.example/compatible-mode/v1",
        "https://ws-abc123.eu-central-1.maas.aliyuncs.com.evil.example/compatible-mode/v1",
        "https://ws.abc.eu-central-1.maas.aliyuncs.com/compatible-mode/v1",
        "https://ws-abc123.mars-1.maas.aliyuncs.com/compatible-mode/v1",
        "https://ws-abc123.eu-central-1.maas.aliyuncs.com/other",
        "http://maas.qwencloudapi.com/compatible-mode/v1",
    ):
        with pytest.raises(BaseUrlRejected):
            effective_base_url(QWEN, stored)


def test_a_provider_the_registry_does_not_know_has_no_address() -> None:
    with pytest.raises(BaseUrlRejected):
        effective_base_url("ollama", "http://localhost:11434/v1")


# -- the catalogue the web app reads --------------------------------------------------------------


def test_the_catalogue_is_public_json_with_the_pages_a_person_needs() -> None:
    catalogue = public_catalog()
    json.dumps(catalogue)  # serialisable as it stands

    assert [p["id"] for p in catalogue] == list(PROVIDERS)
    by_id = {p["id"]: p for p in catalogue}
    for entry in catalogue:
        assert entry["name"] and entry["key_hint"]
        assert entry["model_list"] in {"live", "best_effort"}
        for url in (entry["get_key_url"], entry["manage_url"]):
            assert url is None or url.startswith("https://")
    for provider in ("openrouter", "openai", "anthropic", "gemini", "xai", "deepseek", "qwen"):
        assert by_id[provider]["get_key_url"] and by_id[provider]["manage_url"]
    assert {r["id"] for r in by_id[QWEN]["regions"]} >= {"qwencloud", "us-east-1", "cn-beijing"}
    assert [e["id"] for e in by_id[CUSTOM]["endpoints"]] == list(CUSTOM_ENDPOINTS)
    text = json.dumps(catalogue).lower()
    assert "secret" not in text and "authorization" not in text


def test_every_provider_names_the_page_to_recheck_when_its_behaviour_changes() -> None:
    for spec in PROVIDERS.values():
        if spec.id != CUSTOM:
            assert spec.docs_url.startswith("https://"), spec.id
