"""Tests for Proposal §11's CredentialResolver (Sprint 2.7e).

Exercises the real capability_preferences_store/provider_credentials_store
functions against a combined fake client, not mocks of those functions --
this is what proves the wiring between the three modules actually works,
not just resolve()'s own branching logic in isolation.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from between_jobs.api.credential_resolver import (
    ResolvedCredential,
    resolve,
    try_get_secret,
    try_get_secret_pair,
)
from between_jobs.api.errors import ApiError

_USER_ID = "00000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTable:
    def __init__(self, *, select_rows: list[dict[str, Any]]) -> None:
        self.select_rows = select_rows

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)


class _FakeRpcBuilder:
    def __init__(self, data: Any) -> None:
        self._data = data

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._data)


class _FakeSupabaseClient:
    def __init__(
        self,
        *,
        preferences: dict[str, dict[str, Any]] | None = None,
        credentials: dict[tuple[str, str], dict[str, Any]] | None = None,
    ) -> None:
        # keyed by capability / (service, provider) -- lets one fake
        # client answer different queries differently, matching how the
        # real tables would behave, without reimplementing .eq() filtering.
        self._preferences = preferences or {}
        self._credentials = credentials or {}
        self._last_credential_query: tuple[str, str] = ("", "")

    def table(self, name: str) -> Any:
        if name == "capability_preferences":
            return _PreferenceTable(self)
        if name == "provider_credentials":
            return _CredentialTable(self)
        raise AssertionError(f"unexpected table: {name}")

    def rpc(self, fn: str, params: dict[str, Any]) -> _FakeRpcBuilder:
        assert fn == "decrypt_secret"
        return _FakeRpcBuilder(f"decrypted:{params['p_ciphertext']}")


class _PreferenceTable:
    def __init__(self, client: _FakeSupabaseClient) -> None:
        self._client = client
        self._capability = ""

    def select(self, *_: Any, **__: Any) -> _PreferenceTable:
        return self

    def eq(self, column: str, value: Any) -> _PreferenceTable:
        if column == "capability":
            self._capability = value
        return self

    async def execute(self) -> SimpleNamespace:
        row = self._client._preferences.get(self._capability)
        return SimpleNamespace(data=[row] if row else [])


class _CredentialTable:
    def __init__(self, client: _FakeSupabaseClient) -> None:
        self._client = client
        self._service = ""
        self._provider = ""

    def select(self, *_: Any, **__: Any) -> _CredentialTable:
        return self

    def eq(self, column: str, value: Any) -> _CredentialTable:
        if column == "service":
            self._service = value
        if column == "provider":
            self._provider = value
        return self

    async def execute(self) -> SimpleNamespace:
        key = (self._service, self._provider)
        self._client._last_credential_query = key
        row = self._client._credentials.get(key)
        return SimpleNamespace(data=[row] if row else [])


def _credential_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4-6",
        "base_url": None,
        "secret_encrypted": "ciphertext-abc",
    }
    row.update(overrides)
    return row


def _preference_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "execution_mode": "byok_first_party",
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4-6",
    }
    row.update(overrides)
    return row


async def test_resolve_returns_credential_for_a_capability_specific_preference() -> None:
    client = _FakeSupabaseClient(
        preferences={"resume_forge": _preference_row()},
        credentials={("llm", "openrouter"): _credential_row()},
    )

    result = await resolve(client, _USER_ID, capability="resume_forge")  # type: ignore[arg-type]

    assert result == ResolvedCredential(
        provider="openrouter",
        model="anthropic/claude-sonnet-4-6",
        secret="decrypted:ciphertext-abc",
        base_url=None,
        source="byok",
    )


async def test_resolve_falls_back_to_default_capability() -> None:
    client = _FakeSupabaseClient(
        preferences={"default": _preference_row()},
        credentials={("llm", "openrouter"): _credential_row()},
    )

    result = await resolve(client, _USER_ID, capability="cover_letter")  # type: ignore[arg-type]

    assert result.provider == "openrouter"


async def test_resolve_raises_setup_required_when_no_preference_at_all() -> None:
    client = _FakeSupabaseClient()

    with pytest.raises(ApiError) as exc_info:
        await resolve(client, _USER_ID, capability="resume_forge")  # type: ignore[arg-type]

    assert exc_info.value.code == "SETUP_REQUIRED"
    assert exc_info.value.capability == "resume_forge"
    assert exc_info.value.missing == ["execution_mode"]


async def test_resolve_raises_setup_required_when_disabled() -> None:
    client = _FakeSupabaseClient(
        preferences={"resume_forge": _preference_row(execution_mode="disabled")}
    )

    with pytest.raises(ApiError) as exc_info:
        await resolve(client, _USER_ID, capability="resume_forge")  # type: ignore[arg-type]

    assert exc_info.value.code == "SETUP_REQUIRED"


@pytest.mark.parametrize("mode", ["hosted_credit", "self_host", "some_future_mode"])
async def test_resolve_raises_setup_required_for_unsupported_modes(mode: str) -> None:
    client = _FakeSupabaseClient(preferences={"resume_forge": _preference_row(execution_mode=mode)})

    with pytest.raises(ApiError) as exc_info:
        await resolve(client, _USER_ID, capability="resume_forge")  # type: ignore[arg-type]

    assert exc_info.value.code == "SETUP_REQUIRED"


async def test_resolve_raises_setup_required_when_provider_or_model_missing() -> None:
    client = _FakeSupabaseClient(
        preferences={"resume_forge": _preference_row(provider=None, model=None)}
    )

    with pytest.raises(ApiError) as exc_info:
        await resolve(client, _USER_ID, capability="resume_forge")  # type: ignore[arg-type]

    assert set(exc_info.value.missing or []) == {"provider", "model"}


async def test_resolve_raises_setup_required_when_credential_missing() -> None:
    client = _FakeSupabaseClient(preferences={"resume_forge": _preference_row()})

    with pytest.raises(ApiError) as exc_info:
        await resolve(client, _USER_ID, capability="resume_forge")  # type: ignore[arg-type]

    assert exc_info.value.code == "SETUP_REQUIRED"
    assert exc_info.value.missing == ["credential"]


async def test_resolve_honors_an_explicit_provider_override() -> None:
    client = _FakeSupabaseClient(
        preferences={"resume_forge": _preference_row(provider="openrouter")},
        credentials={
            ("llm", "anthropic"): _credential_row(
                provider="anthropic", secret_encrypted="anthropic-cipher"
            )
        },
    )

    result = await resolve(client, _USER_ID, capability="resume_forge", provider="anthropic")  # type: ignore[arg-type]

    assert result.provider == "anthropic"
    assert result.secret == "decrypted:anthropic-cipher"


async def test_resolve_honors_an_explicit_service_override() -> None:
    # Horizon Sprint 5.0 -- a non-LLM capability resolving a non-"llm"
    # service credential, e.g. company_intel resolving its LLM leg
    # explicitly (service defaults to "llm" already; this proves passing
    # a different one actually changes which table row gets read).
    client = _FakeSupabaseClient(
        preferences={"company_intel": _preference_row(provider="you_com", model="ignored")},
        credentials={("search", "you_com"): _credential_row(provider="you_com")},
    )

    result = await resolve(client, _USER_ID, capability="company_intel", service="search")  # type: ignore[arg-type]

    assert result.provider == "you_com"
    assert client._last_credential_query == ("search", "you_com")


async def test_try_get_secret_returns_the_decrypted_secret_when_present() -> None:
    client = _FakeSupabaseClient(
        credentials={("search", "firecrawl"): _credential_row(secret_encrypted="fc-cipher")}
    )

    secret = await try_get_secret(client, _USER_ID, service="search", provider="firecrawl")  # type: ignore[arg-type]

    assert secret == "decrypted:fc-cipher"


async def test_try_get_secret_returns_none_when_absent() -> None:
    client = _FakeSupabaseClient()

    secret = await try_get_secret(client, _USER_ID, service="search", provider="you_com")  # type: ignore[arg-type]

    assert secret is None


async def test_try_get_secret_pair_returns_both_decrypted_values_when_present() -> None:
    client = _FakeSupabaseClient(
        credentials={
            ("search", "adzuna"): _credential_row(
                provider="adzuna",
                secret_encrypted="app-id-cipher",
                secret_2_encrypted="app-key-cipher",
            )
        }
    )

    pair = await try_get_secret_pair(client, _USER_ID, service="search", provider="adzuna")  # type: ignore[arg-type]

    assert pair == ("decrypted:app-id-cipher", "decrypted:app-key-cipher")


async def test_try_get_secret_pair_returns_none_when_absent() -> None:
    client = _FakeSupabaseClient()

    pair = await try_get_secret_pair(client, _USER_ID, service="search", provider="adzuna")  # type: ignore[arg-type]

    assert pair is None


async def test_try_get_secret_pair_returns_none_when_only_secret_saved() -> None:
    """A `secret` with no `secret_2` is a partially-saved 2-value
    credential -- unusable for Adzuna/USAJobs, degrade the same as
    entirely absent, never a half-working pair."""
    client = _FakeSupabaseClient(
        credentials={("search", "adzuna"): _credential_row(provider="adzuna")}
    )

    pair = await try_get_secret_pair(client, _USER_ID, service="search", provider="adzuna")  # type: ignore[arg-type]

    assert pair is None
