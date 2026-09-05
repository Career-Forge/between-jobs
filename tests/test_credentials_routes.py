"""Tests for the BYOK credentials HTTP endpoints (Sprint 2.7e).

Exercises the real FastAPI routes via TestClient, same shape as
test_applications_routes.py -- proves request parsing, dependency wiring,
the OpenRouter-validation branch, and error-code mapping actually work
end to end. The outbound call to OpenRouter is faked at the httpx client
boundary (`get_http_client`'s dependency override), never a real network
call in a unit test.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from between_jobs.api.app import app
from between_jobs.api.app_state import get_http_client, get_supabase
from between_jobs.api.auth import require_user_id

_USER_ID = "00000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTable:
    def __init__(
        self, *, select_rows: list[dict[str, Any]], upsert_row: dict[str, Any] | None = None
    ) -> None:
        self.select_rows = select_rows
        self.upsert_row = upsert_row
        self.upsert_calls: list[dict[str, Any]] = []
        self.delete_calls = 0

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def upsert(self, data: dict[str, Any], on_conflict: str = "") -> _ChainBuilder:
        self.upsert_calls.append(data)
        rows = [self.upsert_row] if self.upsert_row is not None else []
        return _ChainBuilder(rows)

    def delete(self) -> _ChainBuilder:
        self.delete_calls += 1
        return _ChainBuilder([])


class _FakeRpcBuilder:
    def __init__(self, data: Any) -> None:
        self._data = data

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._data)


class _FakeSupabaseClient:
    def __init__(
        self,
        *,
        provider_credentials: _FakeTable | None = None,
        capability_preferences: _FakeTable | None = None,
    ) -> None:
        self.provider_credentials = provider_credentials or _FakeTable(
            select_rows=[], upsert_row={"id": "cred-1", "service": "llm", "provider": "openrouter"}
        )
        self.capability_preferences = capability_preferences or _FakeTable(
            select_rows=[], upsert_row={"capability": "default"}
        )

    def table(self, name: str) -> Any:
        return {
            "provider_credentials": self.provider_credentials,
            "capability_preferences": self.capability_preferences,
        }[name]

    def rpc(self, fn: str, params: dict[str, Any]) -> _FakeRpcBuilder:
        if fn == "encrypt_secret":
            return _FakeRpcBuilder("ciphertext-abc")
        if fn == "decrypt_secret":
            return _FakeRpcBuilder("sk-or-v1-plaintext")
        raise AssertionError(f"unexpected rpc: {fn}")


class _FakeHttpClient:
    def __init__(self, *, status_code: int = 200, raise_error: bool = False) -> None:
        self.status_code = status_code
        self.raise_error = raise_error
        self.requests: list[tuple[str, dict[str, Any]]] = []

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self.requests.append((url, kwargs))
        if self.raise_error:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(status_code=self.status_code, request=httpx.Request("GET", url))

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.requests.append((url, kwargs))
        if self.raise_error:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(status_code=self.status_code, request=httpx.Request("POST", url))


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")


def _client(supabase: _FakeSupabaseClient, http: _FakeHttpClient) -> TestClient:
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    app.dependency_overrides[get_http_client] = lambda: http
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Any:
    yield
    app.dependency_overrides.clear()


def _save_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "service": "llm",
        "provider": "openrouter",
        "secret": "sk-or-v1-plaintext",
        "model": "anthropic/claude-sonnet-4-6",
    }
    body.update(overrides)
    return body


def test_save_credential_success_validates_then_persists() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(status_code=200)

    with _client(supabase, http) as client:
        response = client.post("/credentials", json=_save_body())

    assert response.status_code == 201
    body = response.json()
    assert "secret_encrypted" not in body
    assert len(http.requests) == 1
    url, kwargs = http.requests[0]
    assert url == "https://openrouter.ai/api/v1/key"
    assert kwargs["headers"]["Authorization"] == "Bearer sk-or-v1-plaintext"


def test_save_credential_rejects_unsupported_service_provider() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(status_code=200)

    with _client(supabase, http) as client:
        response = client.post(
            "/credentials", json=_save_body(service="firecrawl", provider="firecrawl")
        )

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "INVALID_INPUT"
    assert http.requests == []  # never even attempted validation


def test_save_credential_provider_rejected() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(status_code=401)

    with _client(supabase, http) as client:
        response = client.post("/credentials", json=_save_body())

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "PROVIDER_REJECTED"


def test_save_credential_provider_unreachable() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(raise_error=True)

    with _client(supabase, http) as client:
        response = client.post("/credentials", json=_save_body())

    assert response.status_code == 503
    body = response.json()["error"]
    assert body["code"] == "PROVIDER_UNAVAILABLE"
    assert body["retryable"] is True


def test_list_credentials_route_redacts_secret() -> None:
    table = _FakeTable(
        select_rows=[
            {
                "id": "cred-1",
                "service": "llm",
                "provider": "openrouter",
                "secret_encrypted": "ciphertext-abc",
            }
        ]
    )
    supabase = _FakeSupabaseClient(provider_credentials=table)
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.get("/credentials")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert "secret_encrypted" not in body[0]


def test_save_llm_credential_still_sets_the_default_preference() -> None:
    preferences = _FakeTable(select_rows=[], upsert_row={"capability": "default"})
    supabase = _FakeSupabaseClient(capability_preferences=preferences)
    http = _FakeHttpClient(status_code=200)

    with _client(supabase, http) as client:
        response = client.post("/credentials", json=_save_body())

    assert response.status_code == 201
    assert len(preferences.upsert_calls) == 1
    assert preferences.upsert_calls[0]["provider"] == "openrouter"


def test_save_you_com_credential_validates_via_a_real_search_call() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(status_code=200)

    with _client(supabase, http) as client:
        response = client.post(
            "/credentials",
            json=_save_body(service="search", provider="you_com", secret="yc-key", model=None),
        )

    assert response.status_code == 201
    url, kwargs = http.requests[0]
    assert url == "https://ydc-index.io/v1/search"
    assert kwargs["headers"]["X-API-Key"] == "yc-key"
    assert kwargs["json"]["count"] == 1


def test_save_firecrawl_credential_validates_via_credit_usage() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(status_code=200)

    with _client(supabase, http) as client:
        response = client.post(
            "/credentials",
            json=_save_body(service="search", provider="firecrawl", secret="fc-key", model=None),
        )

    assert response.status_code == 201
    url, kwargs = http.requests[0]
    assert url == "https://api.firecrawl.dev/v2/team/credit-usage"
    assert kwargs["headers"]["Authorization"] == "Bearer fc-key"


def test_save_search_credential_does_not_touch_capability_preferences() -> None:
    preferences = _FakeTable(select_rows=[])
    supabase = _FakeSupabaseClient(capability_preferences=preferences)
    http = _FakeHttpClient(status_code=200)

    with _client(supabase, http) as client:
        response = client.post(
            "/credentials",
            json=_save_body(service="search", provider="you_com", secret="yc-key", model=None),
        )

    assert response.status_code == 201
    assert preferences.upsert_calls == []


def test_save_you_com_credential_rejected() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(status_code=401)

    with _client(supabase, http) as client:
        response = client.post(
            "/credentials",
            json=_save_body(service="search", provider="you_com", secret="bad-key", model=None),
        )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "PROVIDER_REJECTED"


def test_save_serper_credential_validates_via_a_real_search_call() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(status_code=200)

    with _client(supabase, http) as client:
        response = client.post(
            "/credentials",
            json=_save_body(service="search", provider="serper", secret="sp-key", model=None),
        )

    assert response.status_code == 201
    url, kwargs = http.requests[0]
    assert url == "https://google.serper.dev/search"
    assert kwargs["headers"]["X-API-KEY"] == "sp-key"
    assert kwargs["json"]["num"] == 1


def test_save_serper_credential_rejected() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(status_code=403)

    with _client(supabase, http) as client:
        response = client.post(
            "/credentials",
            json=_save_body(service="search", provider="serper", secret="bad-key", model=None),
        )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "PROVIDER_REJECTED"


def test_save_brave_credential_validates_via_a_real_search_call() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(status_code=200)

    with _client(supabase, http) as client:
        response = client.post(
            "/credentials",
            json=_save_body(service="search", provider="brave", secret="brave-key", model=None),
        )

    assert response.status_code == 201
    url, kwargs = http.requests[0]
    assert url == "https://api.search.brave.com/res/v1/web/search"
    assert kwargs["headers"]["X-Subscription-Token"] == "brave-key"
    assert kwargs["params"]["count"] == 1


def test_save_jsearch_credential_validates_via_a_real_search_call() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(status_code=200)

    with _client(supabase, http) as client:
        response = client.post(
            "/credentials",
            json=_save_body(service="search", provider="jsearch", secret="js-key", model=None),
        )

    assert response.status_code == 201
    url, kwargs = http.requests[0]
    assert url == "https://jsearch.p.rapidapi.com/search-v2"
    assert kwargs["headers"]["X-RapidAPI-Key"] == "js-key"
    assert kwargs["headers"]["X-RapidAPI-Host"] == "jsearch.p.rapidapi.com"


def test_save_apollo_credential_validates_via_the_free_health_endpoint() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(status_code=200)

    with _client(supabase, http) as client:
        response = client.post(
            "/credentials",
            json=_save_body(service="search", provider="apollo", secret="apollo-key", model=None),
        )

    assert response.status_code == 201
    url, kwargs = http.requests[0]
    assert url == "https://api.apollo.io/api/v1/auth/health"
    assert kwargs["headers"]["X-Api-Key"] == "apollo-key"


def test_save_apollo_credential_rejects_an_invalid_key() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(status_code=401)

    with _client(supabase, http) as client:
        response = client.post(
            "/credentials",
            json=_save_body(service="search", provider="apollo", secret="bad-key", model=None),
        )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "PROVIDER_REJECTED"


def test_save_hunter_credential_validates_via_the_free_account_endpoint() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(status_code=200)

    with _client(supabase, http) as client:
        response = client.post(
            "/credentials",
            json=_save_body(service="search", provider="hunter", secret="hunter-key", model=None),
        )

    assert response.status_code == 201
    url, kwargs = http.requests[0]
    assert url == "https://api.hunter.io/v2/account"
    assert kwargs["params"]["api_key"] == "hunter-key"


def test_save_hunter_credential_rejects_an_invalid_key() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(status_code=401)

    with _client(supabase, http) as client:
        response = client.post(
            "/credentials",
            json=_save_body(service="search", provider="hunter", secret="bad-key", model=None),
        )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "PROVIDER_REJECTED"


def test_save_exa_credential_validates_via_a_real_search_call() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(status_code=200)

    with _client(supabase, http) as client:
        response = client.post(
            "/credentials",
            json=_save_body(service="search", provider="exa", secret="exa-key", model=None),
        )

    assert response.status_code == 201
    url, kwargs = http.requests[0]
    assert url == "https://api.exa.ai/search"
    assert kwargs["headers"]["x-api-key"] == "exa-key"


def test_save_exa_credential_rejects_an_invalid_key() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(status_code=401)

    with _client(supabase, http) as client:
        response = client.post(
            "/credentials",
            json=_save_body(service="search", provider="exa", secret="bad-key", model=None),
        )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "PROVIDER_REJECTED"


def test_save_adzuna_credential_rejects_missing_secret_2() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(status_code=200)

    with _client(supabase, http) as client:
        response = client.post(
            "/credentials",
            json=_save_body(service="search", provider="adzuna", secret="app-id", model=None),
        )

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "INVALID_INPUT"
    assert http.requests == []  # never even attempted validation


def test_save_adzuna_credential_validates_via_a_real_search_call() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(status_code=200)

    with _client(supabase, http) as client:
        response = client.post(
            "/credentials",
            json=_save_body(
                service="search",
                provider="adzuna",
                secret="app-id-value",
                secret_2="app-key-value",
                model=None,
            ),
        )

    assert response.status_code == 201
    url, kwargs = http.requests[0]
    assert url == "https://api.adzuna.com/v1/api/jobs/us/search/1"
    assert kwargs["params"]["app_id"] == "app-id-value"
    assert kwargs["params"]["app_key"] == "app-key-value"


def test_save_adzuna_credential_rejected_on_410() -> None:
    """Adzuna's real auth-failure status is 410 ("Authorisation failed"),
    not 401/403 -- confirmed against its own live OpenAPI spec."""
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(status_code=410)

    with _client(supabase, http) as client:
        response = client.post(
            "/credentials",
            json=_save_body(
                service="search",
                provider="adzuna",
                secret="bad-id",
                secret_2="bad-key",
                model=None,
            ),
        )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "PROVIDER_REJECTED"


def test_save_adzuna_credential_persists_secret_2() -> None:
    table = _FakeTable(
        select_rows=[], upsert_row={"id": "cred-1", "service": "search", "provider": "adzuna"}
    )
    supabase = _FakeSupabaseClient(provider_credentials=table)
    http = _FakeHttpClient(status_code=200)

    with _client(supabase, http) as client:
        response = client.post(
            "/credentials",
            json=_save_body(
                service="search",
                provider="adzuna",
                secret="app-id-value",
                secret_2="app-key-value",
                model=None,
            ),
        )

    assert response.status_code == 201
    # This fake's encrypt_secret RPC returns a fixed ciphertext regardless
    # of plaintext, so this can't prove secret vs secret_2 encrypt to
    # DIFFERENT values -- provider_credentials_store's own tests cover
    # that. What this proves is the real gap this phase closes: the
    # upserted row actually carries a populated secret_2_encrypted at
    # all, not left null the way every save did before P4c.
    assert table.upsert_calls[0]["secret_2_encrypted"] is not None


def test_save_usajobs_credential_rejects_missing_secret_2() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(status_code=200)

    with _client(supabase, http) as client:
        response = client.post(
            "/credentials",
            json=_save_body(service="search", provider="usajobs", secret="auth-key", model=None),
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"
    assert http.requests == []


def test_save_usajobs_credential_validates_via_a_real_search_call() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(status_code=200)

    with _client(supabase, http) as client:
        response = client.post(
            "/credentials",
            json=_save_body(
                service="search",
                provider="usajobs",
                secret="auth-key-value",
                secret_2="dev@example.com",
                model=None,
            ),
        )

    assert response.status_code == 201
    url, kwargs = http.requests[0]
    assert url == "https://data.usajobs.gov/api/search"
    assert kwargs["headers"]["Authorization-Key"] == "auth-key-value"
    assert kwargs["headers"]["User-Agent"] == "dev@example.com"


def test_save_usajobs_credential_rejected() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(status_code=401)

    with _client(supabase, http) as client:
        response = client.post(
            "/credentials",
            json=_save_body(
                service="search",
                provider="usajobs",
                secret="bad-key",
                secret_2="dev@example.com",
                model=None,
            ),
        )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "PROVIDER_REJECTED"


def test_delete_credential_route() -> None:
    table = _FakeTable(select_rows=[])
    supabase = _FakeSupabaseClient(provider_credentials=table)
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.delete("/credentials/llm/openrouter")

    assert response.status_code == 204
    assert table.delete_calls == 1
