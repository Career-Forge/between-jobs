"""The model-key routes (api/credentials_routes.py for the save, api/llm_credentials_routes.py for
the rest): saving a key for each provider, listing models, choosing one, connecting OpenRouter.

The real FastAPI app runs behind TestClient over an in-memory credentials store
(tests/provider_fakes.py) and a mock HTTP transport, so every outbound call a route makes is
visible and answered by the test. Nothing reaches a network, and the keys in here are made up.
Two users share the store wherever isolation is the question.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from provider_fakes import (
    FakeDb,
    SdkWire,
    completion,
    http_client,
    json_response,
    sdk_error,
)

from between_jobs.api import credentials_routes, llm_credentials_routes
from between_jobs.api.app import app
from between_jobs.api.app_state import get_http_client, get_supabase
from between_jobs.api.auth import require_user_id
from between_jobs.api.credential_resolver import resolve
from between_jobs.api.errors import ApiError
from between_jobs.api.logging_setup import JsonFormatter, format_exception_safely

ALICE = "00000000-0000-0000-0000-00000000000a"
BOB = "00000000-0000-0000-0000-00000000000b"
KEY = "sk-test-aaaa0123456789abcdef0123456789abcdef"
OTHER_KEY = "sk-test-bbbb0123456789abcdef0123456789abcdef"
ISSUED_KEY = "sk-or-v1-issued0123456789abcdef0123456789abcdef"
CODE = "oauth-code-0123456789abcdef"
VERIFIER = "v" * 43 + "-._~"
FIXTURE_MODEL = "fixture-model"

OPENAI = "https://api.openai.com/v1"
GROQ = "https://api.groq.com/openai/v1"
QWEN_WS = "https://ws-abc123.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")


class Server:
    """The app with a switchable signed-in user, an in-memory store and a scripted upstream."""

    def __init__(self) -> None:
        self.db = FakeDb()
        self.user = ALICE
        self.upstream: Callable[[httpx.Request], httpx.Response] = lambda r: json_response(200, {})
        self.requests: list[httpx.Request] = []
        self.http = http_client(self._answer)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.upstream(request)

    def hosts(self) -> list[str]:
        return [r.url.host for r in self.requests]


@pytest.fixture
def server() -> Iterator[Server]:
    s = Server()
    app.dependency_overrides[get_supabase] = lambda: s.db
    app.dependency_overrides[require_user_id] = lambda: s.user
    app.dependency_overrides[get_http_client] = lambda: s.http
    yield s
    app.dependency_overrides.clear()


@pytest.fixture
def client(server: Server) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _save(provider: str, **fields: Any) -> dict[str, Any]:
    return {"service": "llm", "provider": provider, "secret": KEY, **fields}


def _error(response: Any) -> dict[str, Any]:
    return dict(response.json()["error"])


def _no_secret(response: Any, *secrets: str) -> None:
    for secret in secrets:
        assert secret not in response.text, "a secret is in the response"


# -- the registry ---------------------------------------------------------------------------------


def test_the_provider_list_is_served_to_the_picker(client: TestClient) -> None:
    response = client.get("/credentials/llm/providers")

    assert response.status_code == 200
    ids = [p["id"] for p in response.json()["providers"]]
    assert ids[:2] == ["openrouter", "openai"] and "custom" in ids and "qwen" in ids


# -- saving a key: nothing is stored unless the provider accepts it -------------------------------


def test_a_key_is_checked_for_free_then_stored_with_no_model_and_no_default(
    client: TestClient, server: Server
) -> None:
    server.upstream = lambda r: json_response(200, {"data": [{"id": "chat-1"}]})

    response = client.post("/credentials", json=_save("openai"))

    assert response.status_code == 201, response.text
    [request] = server.requests
    assert (request.method, str(request.url)) == ("GET", f"{OPENAI}/models")
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = response.json()
    assert body["provider"] == "openai" and body["model"] is None
    assert body["base_url"] is None and body["is_validated"] is True
    assert body["setup_state"] == "needs_model"
    assert "secret_encrypted" not in body
    _no_secret(response, KEY)
    [row] = server.db.credentials_of(ALICE)
    assert row["secret_encrypted"] == f"enc[{KEY}]"
    assert server.db.preferences_of(ALICE) == []  # no default until a model is chosen


def test_a_key_saved_with_a_model_proves_the_model_and_sets_the_default(
    client: TestClient, server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    server.upstream = lambda r: json_response(200, {"data": []})
    probed: list[dict[str, Any]] = []

    async def probe(**kwargs: Any) -> None:
        probed.append(kwargs)

    monkeypatch.setattr(credentials_routes, "probe_model", probe)

    response = client.post("/credentials", json=_save("openai", model=FIXTURE_MODEL))

    assert response.status_code == 201
    assert probed == [{"api_key": KEY, "model": FIXTURE_MODEL, "base_url": OPENAI}]
    assert response.json()["setup_state"] == "ready"
    [pref] = server.db.preferences_of(ALICE)
    assert (pref["capability"], pref["provider"], pref["model"]) == (
        "default",
        "openai",
        FIXTURE_MODEL,
    )


def test_a_model_the_provider_turns_down_stores_nothing_not_even_the_key(
    client: TestClient, server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    server.upstream = lambda r: json_response(200, {"data": []})

    async def probe(**_: Any) -> None:
        raise ApiError("PROVIDER_REJECTED", "not there", details={"reason": "model_not_found"})

    monkeypatch.setattr(credentials_routes, "probe_model", probe)

    response = client.post("/credentials", json=_save("openai", model="no-such-model"))

    assert response.status_code == 502
    assert _error(response)["details"]["reason"] == "model_not_found"
    assert server.db.credentials_of(ALICE) == [] and server.db.preferences_of(ALICE) == []


@pytest.mark.parametrize(
    ("provider", "status", "body", "reason", "http_status"),
    [
        (
            "openai",
            401,
            {"error": {"message": f"Incorrect API key provided: {KEY}"}},
            "key_rejected",
            502,
        ),
        ("anthropic", 401, {"error": {"type": "authentication_error"}}, "key_rejected", 502),
        ("gemini", 400, {"error": {"status": "INVALID_ARGUMENT"}}, "key_rejected", 502),
        ("xai", 400, None, "key_rejected", 502),
        ("deepseek", 401, None, "key_rejected", 502),
        ("deepseek", 402, None, "out_of_credit", 502),
        ("openai", 429, {"error": {"code": "insufficient_quota"}}, "out_of_credit", 502),
        ("openai", 429, None, "rate_limited", 429),
        ("openai", 503, None, "provider_error", 503),
        ("openai", 200, "<html>", "empty_response", 503),
    ],
)
def test_a_bad_key_is_never_stored_and_the_failure_is_classified(
    client: TestClient,
    server: Server,
    provider: str,
    status: int,
    body: Any,
    reason: str,
    http_status: int,
) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if isinstance(body, str):
            return httpx.Response(status, content=body.encode())
        return json_response(status, body)

    server.upstream = answer

    response = client.post("/credentials", json=_save(provider))

    assert response.status_code == http_status
    assert _error(response)["details"]["reason"] == reason
    _no_secret(response, KEY)
    assert "Incorrect" not in response.text  # nothing the provider said
    assert server.db.credentials_of(ALICE) == [] and server.db.preferences_of(ALICE) == []


def test_an_unreachable_provider_stores_nothing(client: TestClient, server: Server) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    server.upstream = down

    response = client.post("/credentials", json=_save("anthropic"))

    assert response.status_code == 503 and _error(response)["retryable"] is True
    assert server.db.credentials_of(ALICE) == []


def test_deepseek_is_checked_against_its_balance(client: TestClient, server: Server) -> None:
    server.upstream = lambda r: json_response(200, {"is_available": True, "balance_infos": []})

    response = client.post("/credentials", json=_save("deepseek"))

    assert response.status_code == 201
    assert str(server.requests[0].url) == "https://api.deepseek.com/user/balance"


def test_an_unknown_provider_is_refused_before_anything_is_sent(
    client: TestClient, server: Server
) -> None:
    for provider in ("ollama", "llamacpp", "OPENAI", "openai ", ""):
        response = client.post("/credentials", json=_save(provider))
        assert response.status_code == 422, provider
    assert server.requests == [] and server.db.credentials_of(ALICE) == []


@pytest.mark.parametrize(
    "provider", ["openai", "anthropic", "gemini", "xai", "deepseek", "openrouter"]
)
@pytest.mark.parametrize(
    "base_url",
    [
        "https://evil.example/v1",
        "http://localhost:11434/v1",
        "https://api.openai.com@evil.example/v1",
    ],
)
def test_a_provider_with_one_address_takes_no_other_and_sends_nothing(
    client: TestClient, server: Server, provider: str, base_url: str
) -> None:
    response = client.post("/credentials", json=_save(provider, base_url=base_url))

    assert response.status_code == 422
    assert _error(response)["details"]["reason"] == "base_url_refused"
    assert server.requests == [] and server.db.credentials_of(ALICE) == []


@pytest.mark.parametrize(
    "secret",
    [
        "short",
        "two words in one key",
        "new\nline-0123456789",
        "caf\u00e9-key-0123456789",
        "k" * 513,
    ],
)
def test_something_that_cannot_be_a_key_is_refused_before_any_request(
    client: TestClient, server: Server, secret: str
) -> None:
    saved = client.post("/credentials", json=_save("openai", secret=secret))
    listed = client.post("/credentials/llm/models", json={"provider": "openai", "secret": secret})

    assert saved.status_code == 422 and listed.status_code == 422
    assert server.requests == [] and server.db.credentials_of(ALICE) == []
    if len(secret) < 500:
        assert secret not in saved.text and secret not in listed.text
    assert _error(saved)["details"]["reason"] == "key_malformed"


def test_the_whitespace_a_paste_leaves_around_a_key_is_trimmed(
    client: TestClient, server: Server
) -> None:
    server.upstream = lambda r: json_response(200, {"data": []})

    response = client.post("/credentials", json=_save("openai", secret=f"  {KEY}\n"))

    assert response.status_code == 201
    assert server.requests[0].headers["authorization"] == f"Bearer {KEY}"
    assert server.db.credentials_of(ALICE)[0]["secret_encrypted"] == f"enc[{KEY}]"


# -- the custom provider and its allowlist --------------------------------------------------------


def test_a_custom_provider_on_the_allowlist_is_checked_there_and_stored_canonically(
    client: TestClient, server: Server
) -> None:
    server.upstream = lambda r: json_response(200, {"data": [{"id": "llama-fixture"}]})

    response = client.post(
        "/credentials", json=_save("custom", base_url="https://API.groq.com/openai/v1/")
    )

    assert response.status_code == 201, response.text
    assert str(server.requests[0].url) == f"{GROQ}/models"
    assert response.json()["base_url"] == GROQ
    assert server.db.credentials_of(ALICE)[0]["base_url"] == GROQ


@pytest.mark.parametrize(
    "base_url",
    [
        None,
        "",
        "https://evil.example/v1",
        "https://api.groq.com@evil.example/openai/v1",
        "https://api.groq.com.evil.example/openai/v1",
        "http://api.groq.com/openai/v1",
        "https://api.groq.com:8443/openai/v1",
        "https://127.0.0.1/v1",
        "https://169.254.169.254/latest/meta-data",
        "https://localhost:11434/v1",
        "https://[::1]/v1",
    ],
)
def test_a_custom_provider_off_the_allowlist_is_refused_and_nothing_is_sent(
    client: TestClient, server: Server, base_url: str | None
) -> None:
    response = client.post("/credentials", json=_save("custom", base_url=base_url))

    assert response.status_code == 422
    assert server.requests == [] and server.db.credentials_of(ALICE) == []


def test_an_address_the_server_would_not_call_is_refused_again_at_the_connection(
    client: TestClient, server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second lock: even an allowlisted name is refused if DNS says it is now private."""
    from between_jobs.api import provider_http

    async def private(_host: str) -> list[str]:
        return ["10.0.0.7"]

    monkeypatch.setattr(provider_http, "_resolve", private)

    response = client.post("/credentials", json=_save("custom", base_url=GROQ))

    assert response.status_code == 422 and server.requests == []


# -- Qwen: region and workspace -------------------------------------------------------------------


def test_qwen_cloud_is_the_default_and_its_address_is_built_by_the_server(
    client: TestClient, server: Server
) -> None:
    server.upstream = lambda r: json_response(200, {"data": [{"id": "qwen-fixture"}]})

    response = client.post("/credentials", json=_save("qwen"))

    assert response.status_code == 201, response.text
    expected = "https://maas.qwencloudapi.com/compatible-mode/v1"
    assert str(server.requests[0].url) == f"{expected}/models"
    assert server.db.credentials_of(ALICE)[0]["base_url"] == expected


def test_a_model_studio_key_is_checked_on_its_own_workspace_host(
    client: TestClient, server: Server
) -> None:
    server.upstream = lambda r: json_response(200, {"data": []})

    response = client.post(
        "/credentials", json=_save("qwen", region="ap-southeast-1", workspace_id="WS-Abc123")
    )

    assert response.status_code == 201, response.text
    assert str(server.requests[0].url) == f"{QWEN_WS}/models"
    assert response.json()["base_url"] == QWEN_WS


@pytest.mark.parametrize(
    "fields",
    [
        {"region": "ap-southeast-1"},
        {"region": "ap-southeast-1", "workspace_id": "a.b"},
        {"region": "ap-southeast-1", "workspace_id": "evil.example/x"},
        {"region": "ap-southeast-1", "workspace_id": "ws@evil.example"},
        {"region": "mars-1", "workspace_id": "ws-abc123"},
        {"region": "qwencloud", "workspace_id": "ws-abc123"},
        {"base_url": "https://maas.qwencloudapi.com/compatible-mode/v1"},
        {"base_url": "https://evil.example/compatible-mode/v1", "region": "qwencloud"},
    ],
)
def test_qwen_input_that_could_name_another_host_is_refused_and_nothing_is_sent(
    client: TestClient, server: Server, fields: dict[str, str]
) -> None:
    response = client.post("/credentials", json=_save("qwen", **fields))

    assert response.status_code == 422
    assert server.requests == [] and server.db.credentials_of(ALICE) == []


def test_qwen_without_a_list_route_needs_a_model_to_check_the_key_with(
    client: TestClient, server: Server
) -> None:
    server.upstream = lambda r: json_response(404, {"error": "no such route"})

    response = client.post("/credentials", json=_save("qwen"))

    assert response.status_code == 422
    assert _error(response)["details"]["reason"] == "model_required_to_check_key"
    assert server.db.credentials_of(ALICE) == []


def test_qwen_without_a_list_route_is_checked_by_the_model_completion_instead(
    client: TestClient, server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    server.upstream = lambda r: json_response(404, None)
    probed: list[dict[str, Any]] = []

    async def probe(**kwargs: Any) -> None:
        probed.append(kwargs)

    monkeypatch.setattr(credentials_routes, "probe_model", probe)

    response = client.post("/credentials", json=_save("qwen", model=FIXTURE_MODEL))

    assert response.status_code == 201
    assert probed[0]["base_url"] == "https://maas.qwencloudapi.com/compatible-mode/v1"
    assert response.json()["setup_state"] == "ready"


def test_a_qwen_401_says_to_check_the_platform_and_region(
    client: TestClient, server: Server
) -> None:
    server.upstream = lambda r: json_response(401, {"code": "invalid_api_key"})

    response = client.post("/credentials", json=_save("qwen"))

    assert response.status_code == 502
    assert "platform and region" in _error(response)["message"]


# -- replacing a key, and OpenRouter's old contract -----------------------------------------------


def test_replacing_a_key_keeps_the_model_chosen_for_it(client: TestClient, server: Server) -> None:
    server.db.add_credential(ALICE, "openai", OTHER_KEY, model=FIXTURE_MODEL)
    server.upstream = lambda r: json_response(200, {"data": []})

    response = client.post("/credentials", json=_save("openai"))

    assert response.status_code == 201
    [row] = server.db.credentials_of(ALICE)
    assert row["secret_encrypted"] == f"enc[{KEY}]" and row["model"] == FIXTURE_MODEL
    assert response.json()["setup_state"] == "ready"


def test_openrouter_with_a_model_still_stores_it_and_sets_the_default(
    client: TestClient, server: Server
) -> None:
    server.upstream = lambda r: json_response(200, {})

    response = client.post("/credentials", json=_save("openrouter", model="vendor/some-model"))

    assert response.status_code == 201
    [request] = server.requests
    assert str(request.url) == "https://openrouter.ai/api/v1/key"
    assert server.db.credentials_of(ALICE)[0]["base_url"] is None
    [pref] = server.db.preferences_of(ALICE)
    assert (pref["provider"], pref["model"]) == ("openrouter", "vendor/some-model")


def test_openrouter_without_a_model_is_saved_and_not_usable_yet(
    client: TestClient, server: Server
) -> None:
    server.upstream = lambda r: json_response(200, {})

    response = client.post("/credentials", json=_save("openrouter"))

    assert response.status_code == 201
    assert response.json()["setup_state"] == "needs_model"
    assert server.db.preferences_of(ALICE) == []


def test_openrouter_takes_no_region_or_workspace(client: TestClient, server: Server) -> None:
    response = client.post("/credentials", json=_save("openrouter", region="us-east-1"))

    assert response.status_code == 422 and server.requests == []


def test_the_credentials_list_carries_the_setup_state_and_no_ciphertext(
    client: TestClient, server: Server
) -> None:
    server.db.add_credential(ALICE, "openai", KEY, model=None)
    server.db.add_credential(ALICE, "anthropic", OTHER_KEY, model=FIXTURE_MODEL)
    server.db.add_credential(ALICE, "deepseek", OTHER_KEY, model=FIXTURE_MODEL, validated=False)
    server.db.add_credential(BOB, "xai", OTHER_KEY, model=FIXTURE_MODEL)

    rows = client.get("/credentials").json()

    assert {r["provider"]: r["setup_state"] for r in rows} == {
        "openai": "needs_model",
        "anthropic": "ready",
        "deepseek": "needs_key",
    }
    assert all("secret_encrypted" not in r for r in rows)


# -- the model rule on the save route, a blank model, and a key that moves host -------


def _default(db: FakeDb, user: str, provider: str, model: str) -> None:
    db.rows["capability_preferences"].append(
        {
            "user_id": user,
            "capability": "default",
            "execution_mode": "byok_first_party",
            "provider": provider,
            "model": model,
        }
    )


@pytest.mark.parametrize("provider", ["openai", "openrouter", "custom"])
@pytest.mark.parametrize(
    "model",
    [
        "<img src=x onerror=alert(1)>../..//" + "A" * 150_000,
        "x" * 201,
        "has space",
        "../../etc/passwd",
        "https://evil.example",
        "a;b",
        "new\nline",
        "\u00e9",
    ],
)
def test_a_model_id_that_is_not_one_is_refused_on_the_save_route_too(
    client: TestClient, server: Server, provider: str, model: str
) -> None:
    fields = {"base_url": GROQ} if provider == "custom" else {}

    response = client.post("/credentials", json=_save(provider, model=model, **fields))

    assert response.status_code == 422
    assert server.requests == []  # nothing was sent to the provider
    assert server.db.credentials_of(ALICE) == [] and server.db.preferences_of(ALICE) == []
    assert "A" * 1000 not in response.text  # and the body is not echoed back


@pytest.mark.parametrize("model", ["", "   "])
def test_a_blank_model_from_the_web_form_still_means_no_model(
    client: TestClient, server: Server, model: str
) -> None:
    server.upstream = lambda r: json_response(200, {})

    response = client.post("/credentials", json=_save("openrouter", model=model))

    assert response.status_code == 201, response.text
    assert response.json()["setup_state"] == "needs_model"
    assert server.db.preferences_of(ALICE) == []


@pytest.mark.parametrize(
    "model", ["gpt-fixture", "vendor/model:free", "accounts/x/models/y", "m@2026-01-01", "a~b+c"]
)
def test_the_ids_providers_really_use_are_accepted_when_saving(
    client: TestClient, server: Server, model: str
) -> None:
    server.upstream = lambda r: json_response(200, {})

    response = client.post("/credentials", json=_save("openrouter", model=model))

    assert response.status_code == 201, response.text
    assert response.json()["model"] == model


MISTRAL = "https://api.mistral.ai/v1"


def test_a_key_moved_to_another_custom_host_does_not_keep_the_old_hosts_model(
    client: TestClient, server: Server
) -> None:
    server.db.add_credential(ALICE, "custom", OTHER_KEY, model="groq-model", base_url=GROQ)
    _default(server.db, ALICE, "custom", "groq-model")
    server.upstream = lambda r: json_response(200, {"data": [{"id": "mistral-model"}]})

    response = client.post("/credentials", json=_save("custom", base_url=MISTRAL))

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["base_url"], body["model"], body["setup_state"]) == (MISTRAL, None, "needs_model")
    [row] = server.db.credentials_of(ALICE)
    assert row["model"] is None and row["base_url"] == MISTRAL
    # the default named the old host's model; leaving it would call Mistral with it
    assert server.db.preferences_of(ALICE) == []


def test_a_key_saved_again_for_the_same_custom_host_keeps_its_model_and_default(
    client: TestClient, server: Server
) -> None:
    server.db.add_credential(ALICE, "custom", OTHER_KEY, model="groq-model", base_url=GROQ)
    _default(server.db, ALICE, "custom", "groq-model")
    server.upstream = lambda r: json_response(200, {"data": []})

    response = client.post("/credentials", json=_save("custom", base_url=GROQ))

    assert response.status_code == 201, response.text
    assert (response.json()["model"], response.json()["setup_state"]) == ("groq-model", "ready")
    [pref] = server.db.preferences_of(ALICE)
    assert (pref["provider"], pref["model"]) == ("custom", "groq-model")


def test_a_new_key_without_a_model_leaves_another_providers_default_alone(
    client: TestClient, server: Server
) -> None:
    server.db.add_credential(ALICE, "openrouter", OTHER_KEY, model="vendor/m")
    _default(server.db, ALICE, "openrouter", "vendor/m")
    server.upstream = lambda r: json_response(200, {"data": []})

    response = client.post("/credentials", json=_save("openai"))

    assert response.status_code == 201
    [pref] = server.db.preferences_of(ALICE)
    assert (pref["provider"], pref["model"]) == ("openrouter", "vendor/m")


# -- removing a key must not strand the default -----------------------------------------


def test_removing_the_default_key_moves_the_default_to_the_one_other_ready_key(
    client: TestClient, server: Server
) -> None:
    server.db.add_credential(ALICE, "openai", KEY, model="gpt-fixture")
    server.db.add_credential(ALICE, "openrouter", OTHER_KEY, model="vendor/m")
    _default(server.db, ALICE, "openai", "gpt-fixture")

    assert client.delete("/credentials/llm/openai").status_code == 204

    [pref] = server.db.preferences_of(ALICE)
    assert (pref["provider"], pref["model"]) == ("openrouter", "vendor/m")
    assert [r["provider"] for r in server.db.credentials_of(ALICE)] == ["openrouter"]


def test_with_two_other_ready_keys_the_choice_is_the_persons_so_the_default_is_cleared(
    client: TestClient, server: Server
) -> None:
    server.db.add_credential(ALICE, "openai", KEY, model="gpt-fixture")
    server.db.add_credential(ALICE, "openrouter", OTHER_KEY, model="vendor/m")
    server.db.add_credential(ALICE, "deepseek", OTHER_KEY, model="deepseek-fixture")
    _default(server.db, ALICE, "openai", "gpt-fixture")

    assert client.delete("/credentials/llm/openai").status_code == 204

    assert server.db.preferences_of(ALICE) == []


def test_other_keys_that_are_not_ready_are_not_a_place_to_move_the_default(
    client: TestClient, server: Server
) -> None:
    server.db.add_credential(ALICE, "openai", KEY, model="gpt-fixture")
    server.db.add_credential(ALICE, "openrouter", OTHER_KEY, model=None)  # needs a model
    server.db.add_credential(ALICE, "deepseek", OTHER_KEY, model="m", validated=False)
    _default(server.db, ALICE, "openai", "gpt-fixture")

    assert client.delete("/credentials/llm/openai").status_code == 204

    assert server.db.preferences_of(ALICE) == []


def test_removing_a_key_that_is_not_the_default_leaves_the_default(
    client: TestClient, server: Server
) -> None:
    server.db.add_credential(ALICE, "openai", KEY, model="gpt-fixture")
    server.db.add_credential(ALICE, "openrouter", OTHER_KEY, model="vendor/m")
    _default(server.db, ALICE, "openrouter", "vendor/m")

    assert client.delete("/credentials/llm/openai").status_code == 204

    [pref] = server.db.preferences_of(ALICE)
    assert (pref["provider"], pref["model"]) == ("openrouter", "vendor/m")


def test_removing_a_key_never_touches_anyone_elses_default(
    client: TestClient, server: Server
) -> None:
    server.db.add_credential(ALICE, "openai", KEY, model="gpt-fixture")
    server.db.add_credential(BOB, "openai", OTHER_KEY, model="gpt-fixture")
    _default(server.db, ALICE, "openai", "gpt-fixture")
    _default(server.db, BOB, "openai", "gpt-fixture")

    assert client.delete("/credentials/llm/openai").status_code == 204

    assert server.db.preferences_of(ALICE) == []
    [bobs] = server.db.preferences_of(BOB)
    assert bobs["provider"] == "openai"
    assert len(server.db.credentials_of(BOB)) == 1


def test_removing_a_search_key_leaves_the_model_default_alone(
    client: TestClient, server: Server
) -> None:
    server.db.add_credential(ALICE, "openai", KEY, model="gpt-fixture")
    _default(server.db, ALICE, "openai", "gpt-fixture")

    assert client.delete("/credentials/search/openai").status_code == 204

    assert len(server.db.preferences_of(ALICE)) == 1


async def test_after_the_default_key_is_removed_the_resolver_finds_another_or_says_so(
    server: Server,
) -> None:
    server.db.add_credential(ALICE, "openai", KEY, model="gpt-fixture")
    server.db.add_credential(ALICE, "openrouter", OTHER_KEY, model="vendor/m")
    _default(server.db, ALICE, "openai", "gpt-fixture")

    await credentials_routes.delete_credential_route("llm", "openai", ALICE, server.db)  # type: ignore[arg-type]
    resolved = await resolve(server.db, ALICE, capability="x")  # type: ignore[arg-type]
    assert (resolved.provider, resolved.model, resolved.secret) == (
        "openrouter",
        "vendor/m",
        OTHER_KEY,
    )

    await credentials_routes.delete_credential_route("llm", "openrouter", ALICE, server.db)  # type: ignore[arg-type]
    with pytest.raises(ApiError) as caught:
        await resolve(server.db, ALICE, capability="x")  # type: ignore[arg-type]
    assert caught.value.code == "SETUP_REQUIRED"
    assert "openai" not in caught.value.message  # not a dead end on the key that was removed


# -- a key with no model is not ready, and says what to do ----------------------------------------


async def test_the_resolver_reports_a_key_without_a_model_as_needing_a_model() -> None:
    db = FakeDb()
    db.add_credential(ALICE, "openai", KEY, model=None)

    with pytest.raises(ApiError) as caught:
        await resolve(db, ALICE, capability="resume_forge")  # type: ignore[arg-type]

    error = caught.value
    assert error.code == "SETUP_REQUIRED" and error.missing == ["model"]
    assert error.settings_path == "/profile/integrations?capability=resume_forge"
    assert "OpenAI" in error.message and "model" in error.message
    assert error.details == {"provider": "openai", "state": "needs_model"}
    assert KEY not in repr(error.__dict__)


async def test_a_default_that_names_a_provider_but_no_model_says_the_same() -> None:
    db = FakeDb()
    db.add_credential(ALICE, "anthropic", KEY, model=None)
    db.rows["capability_preferences"].append(
        {
            "user_id": ALICE,
            "capability": "default",
            "execution_mode": "byok_first_party",
            "provider": "anthropic",
            "model": None,
        }
    )

    with pytest.raises(ApiError) as caught:
        await resolve(db, ALICE, capability="resume_forge")  # type: ignore[arg-type]

    assert caught.value.missing == ["model"] and "Anthropic" in caught.value.message


async def test_no_key_at_all_still_says_there_is_no_execution_mode() -> None:
    with pytest.raises(ApiError) as caught:
        await resolve(FakeDb(), ALICE, capability="resume_forge")  # type: ignore[arg-type]

    assert caught.value.missing == ["execution_mode"]


async def test_an_unvalidated_key_is_not_reported_as_waiting_for_a_model() -> None:
    db = FakeDb()
    db.add_credential(ALICE, "openai", KEY, model=None, validated=False)

    with pytest.raises(ApiError) as caught:
        await resolve(db, ALICE, capability="resume_forge")  # type: ignore[arg-type]

    assert caught.value.missing == ["execution_mode"]


async def test_a_resolved_credential_carries_the_registry_address_for_its_provider() -> None:
    db = FakeDb()
    db.add_credential(ALICE, "openai", KEY, model=FIXTURE_MODEL)
    db.add_credential(ALICE, "custom", KEY, model=FIXTURE_MODEL, base_url=GROQ)
    db.add_credential(ALICE, "qwen", KEY, model=FIXTURE_MODEL, base_url=QWEN_WS)
    db.add_credential(
        ALICE, "openrouter", KEY, model=FIXTURE_MODEL, base_url="https://evil.example"
    )
    db.rows["capability_preferences"].append(
        {
            "user_id": ALICE,
            "capability": "default",
            "execution_mode": "byok_first_party",
            "provider": "openai",
            "model": FIXTURE_MODEL,
        }
    )

    async def resolved(provider: str) -> Any:
        return await resolve(db, ALICE, capability="x", provider=provider)  # type: ignore[arg-type]

    assert (await resolved("openai")).base_url == OPENAI
    assert (await resolved("custom")).base_url == GROQ
    assert (await resolved("qwen")).base_url == QWEN_WS
    # OpenRouter's address is its default (None), whatever an old row says
    assert (await resolved("openrouter")).base_url is None


@pytest.mark.parametrize(
    ("provider", "stored"),
    [
        ("custom", "https://evil.example/v1"),
        ("custom", "http://localhost:11434/v1"),
        ("custom", None),
        ("qwen", "https://evil.example/compatible-mode/v1"),
        ("qwen", None),
        ("ollama", "http://localhost:11434/v1"),
    ],
)
async def test_a_saved_row_with_an_address_that_no_longer_passes_cannot_send_its_key_anywhere(
    provider: str, stored: str | None
) -> None:
    db = FakeDb()
    db.add_credential(ALICE, provider, KEY, model=FIXTURE_MODEL, base_url=stored)
    db.rows["capability_preferences"].append(
        {
            "user_id": ALICE,
            "capability": "default",
            "execution_mode": "byok_first_party",
            "provider": provider,
            "model": FIXTURE_MODEL,
        }
    )

    with pytest.raises(ApiError) as caught:
        await resolve(db, ALICE, capability="x", provider=provider)  # type: ignore[arg-type]

    assert caught.value.code == "SETUP_REQUIRED" and caught.value.missing == ["credential"]
    assert KEY not in repr(caught.value.__dict__)


# -- choosing a model -----------------------------------------------------------------------------


def test_choosing_a_model_proves_it_then_stores_it_and_sets_the_default(
    client: TestClient, server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    server.db.add_credential(ALICE, "openai", KEY, model=None)
    probed: list[dict[str, Any]] = []

    async def probe(**kwargs: Any) -> None:
        probed.append(kwargs)
        assert server.db.credentials_of(ALICE)[0]["model"] is None  # not stored before it passed

    monkeypatch.setattr(llm_credentials_routes, "probe_model", probe)

    response = client.put("/credentials/llm/openai/model", json={"model": FIXTURE_MODEL})

    assert response.status_code == 200, response.text
    assert probed == [{"api_key": KEY, "model": FIXTURE_MODEL, "base_url": OPENAI}]
    assert response.json()["model"] == FIXTURE_MODEL and response.json()["setup_state"] == "ready"
    assert "secret_encrypted" not in response.json()
    [row] = server.db.credentials_of(ALICE)
    assert row["model"] == FIXTURE_MODEL and row["secret_encrypted"] == f"enc[{KEY}]"
    [pref] = server.db.preferences_of(ALICE)
    assert (pref["capability"], pref["provider"], pref["model"]) == (
        "default",
        "openai",
        FIXTURE_MODEL,
    )


def test_the_chosen_model_is_proved_through_the_providers_dialect(
    client: TestClient, server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    server.db.add_credential(ALICE, "openai", KEY, model=None)
    wire = SdkWire(lambda request: completion("", finish_reason="length")).install(monkeypatch)

    response = client.put("/credentials/llm/openai/model", json={"model": FIXTURE_MODEL})

    assert response.status_code == 200, response.text
    [request] = wire.requests
    assert str(request.url) == f"{OPENAI}/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert wire.bodies()[0]["max_completion_tokens"] == 16
    assert wire.bodies()[0]["model"] == FIXTURE_MODEL


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (404, {"error": {"code": "model_not_found"}}, "model_not_found"),
        (410, None, "model_retired"),
        (401, None, "key_rejected"),
        (402, None, "out_of_credit"),
        (429, {"error": {"code": "slow_down"}}, "rate_limited"),
        (500, None, "provider_error"),
    ],
)
def test_a_model_the_provider_turns_down_changes_nothing(
    client: TestClient,
    server: Server,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    body: Any,
    reason: str,
) -> None:
    server.db.add_credential(ALICE, "openai", KEY, model="the-old-model")
    SdkWire(lambda request: sdk_error(status, body, {"x-should-retry": "false"})).install(
        monkeypatch
    )

    response = client.put("/credentials/llm/openai/model", json={"model": "the-new-model"})

    assert _error(response)["details"]["reason"] == reason
    assert response.status_code in {502, 429, 503}
    assert server.db.credentials_of(ALICE)[0]["model"] == "the-old-model"
    assert server.db.preferences_of(ALICE) == []
    _no_secret(response, KEY)


def test_choosing_a_model_needs_a_saved_key_and_proves_nothing_without_one(
    client: TestClient, server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    wire = SdkWire(lambda request: completion()).install(monkeypatch)

    response = client.put("/credentials/llm/openai/model", json={"model": FIXTURE_MODEL})

    assert response.status_code == 409
    assert _error(response)["code"] == "SETUP_REQUIRED" and _error(response)["missing"] == [
        "credential"
    ]
    assert wire.requests == []


@pytest.mark.parametrize(
    "model",
    [
        "",
        " ",
        "has space",
        "new\nline",
        "x" * 201,
        "../../etc/passwd",
        "https://evil.example",
        "a;b",
        "é",
    ],
)
def test_a_model_id_that_is_not_one_is_refused_before_any_request(
    client: TestClient, server: Server, monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    server.db.add_credential(ALICE, "openai", KEY, model=None)
    wire = SdkWire(lambda request: completion()).install(monkeypatch)

    response = client.put("/credentials/llm/openai/model", json={"model": model})

    assert response.status_code == 422 and wire.requests == []


@pytest.mark.parametrize(
    "model", ["gpt-fixture", "vendor/model:free", "accounts/x/models/y", "m@2026-01-01", "a~b+c"]
)
def test_the_ids_providers_really_use_are_accepted(
    client: TestClient, server: Server, monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    server.db.add_credential(ALICE, "openrouter", KEY, model=None)
    SdkWire(lambda request: completion()).install(monkeypatch)

    response = client.put("/credentials/llm/openrouter/model", json={"model": model})

    assert response.status_code == 200, response.text


def test_choosing_a_model_for_an_unknown_provider_is_refused(client: TestClient) -> None:
    assert client.put("/credentials/llm/ollama/model", json={"model": "m"}).status_code == 422


def test_a_key_saved_first_and_a_model_chosen_second_is_then_usable_by_the_resolver(
    client: TestClient, server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    server.upstream = lambda r: json_response(200, {"is_available": True})
    assert client.post("/credentials", json=_save("deepseek")).status_code == 201
    SdkWire(lambda request: completion()).install(monkeypatch)
    assert (
        client.put("/credentials/llm/deepseek/model", json={"model": FIXTURE_MODEL}).status_code
        == 200
    )

    resolved = asyncio.run(resolve(server.db, ALICE, capability="x"))  # type: ignore[arg-type]

    assert (resolved.provider, resolved.model, resolved.base_url) == (
        "deepseek",
        FIXTURE_MODEL,
        "https://api.deepseek.com",
    )
    assert resolved.secret == KEY


# -- listing models -------------------------------------------------------------------------------


def test_models_are_listed_with_a_key_from_the_body_which_is_not_kept(
    client: TestClient, server: Server
) -> None:
    server.upstream = lambda r: json_response(
        200,
        {
            "data": [
                {"id": "old-chat", "created": 1_700_000_000},
                {"id": "new-chat", "created": 1_760_000_000},
            ]
        },
    )

    response = client.post("/credentials/llm/models", json={"provider": "openai", "secret": KEY})

    assert response.status_code == 200, response.text
    assert response.json() == {
        "provider": "openai",
        "listing": "live",
        "truncated": False,
        "models": [
            {
                "id": "new-chat",
                "name": "new-chat",
                "context_length": None,
                "created": 1_760_000_000,
            },
            {
                "id": "old-chat",
                "name": "old-chat",
                "context_length": None,
                "created": 1_700_000_000,
            },
        ],
    }
    assert server.requests[0].headers["authorization"] == f"Bearer {KEY}"
    _no_secret(response, KEY)
    assert server.db.credentials_of(ALICE) == []  # listing is not saving


def test_models_are_listed_with_the_callers_own_saved_key(
    client: TestClient, server: Server
) -> None:
    server.db.add_credential(ALICE, "anthropic", KEY)
    server.upstream = lambda r: json_response(
        200, {"data": [{"id": "m1", "display_name": "M One"}]}
    )

    response = client.post("/credentials/llm/models", json={"provider": "anthropic"})

    assert response.status_code == 200
    assert server.requests[0].headers["x-api-key"] == KEY
    assert response.json()["models"][0]["name"] == "M One"


def test_one_user_can_never_list_models_with_anothers_saved_key(
    client: TestClient, server: Server
) -> None:
    server.db.add_credential(ALICE, "openai", KEY)
    server.upstream = lambda r: json_response(200, {"data": [{"id": "m1"}]})

    server.user = BOB
    stranger = client.post("/credentials/llm/models", json={"provider": "openai"})
    assert stranger.status_code == 409
    assert _error(stranger)["code"] == "SETUP_REQUIRED"
    assert server.requests == []  # nothing went out with anyone's key

    server.db.add_credential(BOB, "openai", OTHER_KEY)
    server.user = BOB
    own = client.post("/credentials/llm/models", json={"provider": "openai"})
    assert own.status_code == 200
    assert server.requests[-1].headers["authorization"] == f"Bearer {OTHER_KEY}"

    server.user = ALICE
    client.post("/credentials/llm/models", json={"provider": "openai"})
    assert server.requests[-1].headers["authorization"] == f"Bearer {KEY}"


def test_one_user_can_never_choose_a_model_on_anothers_key(
    client: TestClient, server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    server.db.add_credential(ALICE, "openai", KEY, model="alices-model")
    wire = SdkWire(lambda request: completion()).install(monkeypatch)
    server.user = BOB

    response = client.put("/credentials/llm/openai/model", json={"model": "bobs-model"})

    assert response.status_code == 409 and wire.requests == []
    assert server.db.credentials_of(ALICE)[0]["model"] == "alices-model"
    assert server.db.credentials_of(BOB) == [] and server.db.preferences_of(BOB) == []


def test_a_saved_row_with_an_address_that_no_longer_passes_is_not_called(
    client: TestClient, server: Server
) -> None:
    server.db.add_credential(ALICE, "custom", KEY, base_url="https://evil.example/v1")

    response = client.post("/credentials/llm/models", json={"provider": "custom"})

    assert response.status_code == 409 and server.requests == []


def test_a_key_in_the_body_brings_its_own_address_and_a_saved_key_cannot_change_it(
    client: TestClient, server: Server
) -> None:
    server.db.add_credential(ALICE, "custom", KEY, base_url=GROQ)
    server.upstream = lambda r: json_response(200, {"data": []})

    client.post(
        "/credentials/llm/models",
        json={"provider": "custom", "base_url": "https://api.mistral.ai/v1"},
    )

    assert str(server.requests[0].url) == f"{GROQ}/models"  # the body's address was not read


@pytest.mark.parametrize(
    "fields",
    [
        {"provider": "openai", "secret": KEY, "base_url": "https://evil.example/v1"},
        {"provider": "custom", "secret": KEY, "base_url": "https://evil.example/v1"},
        {"provider": "custom", "secret": KEY},
        {"provider": "qwen", "secret": KEY, "region": "us-east-1", "workspace_id": "x.y"},
        {"provider": "ollama", "secret": KEY},
        {"provider": "openai", "secret": ""},
        {"provider": "openai", "secret": "k" * 513},
    ],
)
def test_a_listing_request_that_names_another_host_is_refused_and_sends_nothing(
    client: TestClient, server: Server, fields: dict[str, str]
) -> None:
    response = client.post("/credentials/llm/models", json=fields)

    assert response.status_code == 422 and server.requests == []


def test_a_provider_with_no_list_route_answers_unavailable_not_an_error(
    client: TestClient, server: Server
) -> None:
    server.upstream = lambda r: json_response(404, None)

    response = client.post("/credentials/llm/models", json={"provider": "qwen", "secret": KEY})

    assert response.status_code == 200
    assert response.json()["listing"] == "unavailable" and response.json()["models"] == []


def test_a_list_is_capped_and_says_so(client: TestClient, server: Server) -> None:
    server.upstream = lambda r: json_response(
        200, {"data": [{"id": f"chat-{n:05d}", "created": n + 1} for n in range(700)]}
    )

    body = client.post("/credentials/llm/models", json={"provider": "openai", "secret": KEY}).json()

    assert body["truncated"] is True and len(body["models"]) == 500
    assert body["models"][0]["id"] == "chat-00699"


def test_a_huge_list_is_a_failure_not_a_memory_problem(client: TestClient, server: Server) -> None:
    server.upstream = lambda r: httpx.Response(200, content=b"[" + b"0," * 1_300_000 + b"0]")

    response = client.post("/credentials/llm/models", json={"provider": "openai", "secret": KEY})

    assert response.status_code == 503
    assert _error(response)["details"]["reason"] == "response_too_large"


@pytest.mark.parametrize(
    ("status", "reason", "http_status"),
    [(401, "key_rejected", 502), (429, "rate_limited", 429), (500, "provider_error", 503)],
)
def test_a_list_failure_leaks_nothing_the_provider_said(
    client: TestClient, server: Server, status: int, reason: str, http_status: int
) -> None:
    server.upstream = lambda r: json_response(
        status, {"error": {"message": f"bad key {KEY}", "code": "x"}}
    )

    response = client.post("/credentials/llm/models", json={"provider": "xai", "secret": KEY})

    assert response.status_code == http_status
    assert _error(response)["details"]["reason"] == reason
    _no_secret(response, KEY)
    assert "bad key" not in response.text


# -- connecting OpenRouter in one click -----------------------------------------------------------


def _exchange_body(**overrides: str) -> dict[str, str]:
    return {"code": CODE, "code_verifier": VERIFIER, **overrides}


def _openrouter_upstream(
    server: Server, *, exchange: httpx.Response | None = None, key_check: int = 200
) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/keys":
            return exchange or json_response(200, {"key": ISSUED_KEY, "user_id": "u"})
        if request.url.path == "/api/v1/key":
            return json_response(key_check, {"data": {}})
        raise AssertionError(f"unexpected {request.url}")

    server.upstream = answer


def test_the_code_and_verifier_are_exchanged_checked_and_the_key_stored_encrypted(
    client: TestClient, server: Server
) -> None:
    _openrouter_upstream(server)

    response = client.post("/credentials/openrouter/exchange", json=_exchange_body())

    assert response.status_code == 201, response.text
    exchange, check = server.requests
    assert (exchange.method, str(exchange.url)) == (
        "POST",
        "https://openrouter.ai/api/v1/auth/keys",
    )
    assert json.loads(exchange.content) == {
        "code": CODE,
        "code_verifier": VERIFIER,
        "code_challenge_method": "S256",
    }
    assert "authorization" not in exchange.headers  # the code and verifier are the credential
    assert check.headers["authorization"] == f"Bearer {ISSUED_KEY}"
    body = response.json()
    assert (body["provider"], body["model"], body["setup_state"]) == (
        "openrouter",
        None,
        "needs_model",
    )
    assert "secret_encrypted" not in body
    _no_secret(response, ISSUED_KEY, CODE, VERIFIER)
    [row] = server.db.credentials_of(ALICE)
    assert row["secret_encrypted"] == f"enc[{ISSUED_KEY}]" and row["is_validated"] is True
    assert server.db.preferences_of(ALICE) == []


def test_reconnecting_keeps_the_model_already_chosen(client: TestClient, server: Server) -> None:
    server.db.add_credential(ALICE, "openrouter", OTHER_KEY, model="vendor/kept")
    _openrouter_upstream(server)

    response = client.post("/credentials/openrouter/exchange", json=_exchange_body())

    assert response.status_code == 201
    [row] = server.db.credentials_of(ALICE)
    assert row["model"] == "vendor/kept" and row["secret_encrypted"] == f"enc[{ISSUED_KEY}]"
    assert response.json()["setup_state"] == "ready"


@pytest.mark.parametrize(
    "fields",
    [
        {"code_verifier": "short"},
        {"code_verifier": "a" * 129},
        {"code_verifier": "a" * 42 + " "},
        {"code_verifier": "a" * 42 + "/"},
        {"code_verifier": "a" * 42 + "é"},
        {"code": "short"},
        {"code": "has space in it"},
        {"code": "x" * 513},
        {"code": ""},
    ],
)
def test_a_code_or_verifier_that_cannot_be_one_never_reaches_openrouter(
    client: TestClient, server: Server, fields: dict[str, str]
) -> None:
    response = client.post("/credentials/openrouter/exchange", json=_exchange_body(**fields))

    assert response.status_code == 422 and server.requests == []
    assert server.db.credentials_of(ALICE) == []
    for secret in fields.values():
        assert secret == "" or secret not in response.text or len(secret) < 8


def test_a_missing_field_is_a_validation_error_that_does_not_echo_the_other(
    client: TestClient, server: Server
) -> None:
    response = client.post("/credentials/openrouter/exchange", json={"code": CODE})

    assert response.status_code == 422 and server.requests == []
    _no_secret(response, CODE)


@pytest.mark.parametrize("upstream_status", [400, 403])
def test_a_code_openrouter_refuses_is_a_rejected_sign_in_and_nothing_is_stored(
    client: TestClient, server: Server, upstream_status: int
) -> None:
    """A replayed code, a wrong verifier and an expired code all arrive as OpenRouter's 403."""
    _openrouter_upstream(
        server, exchange=json_response(upstream_status, {"error": {"message": f"bad {CODE}"}})
    )

    response = client.post("/credentials/openrouter/exchange", json=_exchange_body())

    assert response.status_code == 502
    error = _error(response)
    assert error["code"] == "PROVIDER_REJECTED" and error["retryable"] is False
    assert error["details"]["reason"] == "oauth_code_rejected"
    assert len(server.requests) == 1  # the key check never ran
    assert server.db.credentials_of(ALICE) == []
    _no_secret(response, CODE, VERIFIER)
    assert "bad " not in response.text


def test_a_replayed_code_is_refused_the_second_time(client: TestClient, server: Server) -> None:
    used = False

    def answer(request: httpx.Request) -> httpx.Response:
        nonlocal used
        if request.url.path == "/api/v1/auth/keys":
            if used:
                return json_response(403, {"error": {"message": "Invalid code or code_verifier"}})
            used = True
            return json_response(200, {"key": ISSUED_KEY})
        return json_response(200, {"data": {}})

    server.upstream = answer

    first = client.post("/credentials/openrouter/exchange", json=_exchange_body())
    second = client.post("/credentials/openrouter/exchange", json=_exchange_body())

    assert (first.status_code, second.status_code) == (201, 502)
    assert _error(second)["details"]["reason"] == "oauth_code_rejected"


def test_the_wrong_verifier_gets_nothing_even_with_the_right_code(
    client: TestClient, server: Server
) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/keys":
            sent = json.loads(request.content)
            if sent["code_verifier"] != VERIFIER:
                return json_response(403, {"error": {"message": "Invalid code or code_verifier"}})
            return json_response(200, {"key": ISSUED_KEY})
        return json_response(200, {"data": {}})

    server.upstream = answer
    other = "w" * 43

    wrong = client.post(
        "/credentials/openrouter/exchange", json=_exchange_body(code_verifier=other)
    )

    assert wrong.status_code == 502 and server.db.credentials_of(ALICE) == []
    _no_secret(wrong, ISSUED_KEY, other)


@pytest.mark.parametrize(
    ("upstream", "http_status", "reason"),
    [
        (lambda: json_response(429, None), 429, "rate_limited"),
        (lambda: json_response(500, None), 503, "provider_error"),
        (lambda: json_response(503, None), 503, "provider_error"),
        (lambda: json_response(405, None), 503, "provider_error"),
        (
            lambda: json_response(302, None, headers={"location": "https://evil.example/"}),
            503,
            "provider_error",
        ),
        (lambda: json_response(200, {"no_key": True}), 503, "empty_response"),
        (lambda: json_response(200, {"key": ""}), 503, "empty_response"),
        (lambda: json_response(200, {"key": 12345}), 503, "empty_response"),
        (lambda: json_response(200, {"key": "has a space in it"}), 503, "empty_response"),
        (lambda: json_response(200, {"key": "k" * 600}), 503, "empty_response"),
        (lambda: json_response(200, ["not", "an", "object"]), 503, "empty_response"),
        (lambda: httpx.Response(200, content=b"<html>proxy</html>"), 503, "empty_response"),
    ],
)
def test_an_upstream_failure_is_classified_and_stores_nothing(
    client: TestClient,
    server: Server,
    upstream: Callable[[], httpx.Response],
    http_status: int,
    reason: str,
) -> None:
    _openrouter_upstream(server, exchange=upstream())

    response = client.post("/credentials/openrouter/exchange", json=_exchange_body())

    assert response.status_code == http_status
    assert _error(response)["details"]["reason"] == reason
    assert len(server.requests) == 1  # no redirect followed, no key check without a key
    assert server.db.credentials_of(ALICE) == []
    _no_secret(response, CODE, VERIFIER)


@pytest.mark.parametrize(
    ("raised", "reason"),
    [(httpx.ConnectError("refused"), "unreachable"), (httpx.ReadTimeout("slow"), "timeout")],
)
def test_an_unreachable_openrouter_is_retryable(
    client: TestClient, server: Server, raised: Exception, reason: str
) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise raised

    server.upstream = down

    response = client.post("/credentials/openrouter/exchange", json=_exchange_body())

    assert response.status_code == 503 and _error(response)["retryable"] is True
    assert _error(response)["details"]["reason"] == reason
    assert server.db.credentials_of(ALICE) == []


def test_an_issued_key_that_fails_its_own_check_is_not_stored(
    client: TestClient, server: Server
) -> None:
    _openrouter_upstream(server, key_check=401)

    response = client.post("/credentials/openrouter/exchange", json=_exchange_body())

    assert response.status_code == 502
    assert server.db.credentials_of(ALICE) == []
    _no_secret(response, ISSUED_KEY, CODE, VERIFIER)


def test_an_exchange_stores_the_key_for_the_signed_in_user_only(
    client: TestClient, server: Server
) -> None:
    server.db.add_credential(ALICE, "openrouter", KEY, model="alices-model")
    _openrouter_upstream(server)
    server.user = BOB

    response = client.post("/credentials/openrouter/exchange", json=_exchange_body())

    assert response.status_code == 201
    assert server.db.credentials_of(BOB)[0]["secret_encrypted"] == f"enc[{ISSUED_KEY}]"
    assert server.db.credentials_of(BOB)[0]["model"] is None  # Alice's model is not Bob's
    assert server.db.credentials_of(ALICE)[0]["secret_encrypted"] == f"enc[{KEY}]"


# -- no secret anywhere ---------------------------------------------------------------------------


def _everything_logged(caplog: pytest.LogCaptureFixture) -> str:
    formatter = JsonFormatter()
    lines = []
    for record in caplog.records:
        lines.append(formatter.format(record))
        lines.append(record.getMessage())
        if record.exc_info and record.exc_info[1] is not None:
            lines.append(format_exception_safely(record.exc_info[1]))
    return "\n".join(lines)


def test_no_key_code_or_verifier_is_in_any_log_line_across_the_failures(
    client: TestClient, server: Server, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    echo = {"error": {"message": f"Incorrect API key provided: {KEY} / {CODE} / {VERIFIER}"}}

    for status in (401, 402, 429, 500):

        def failing(request: httpx.Request, status: int = status) -> httpx.Response:
            return json_response(status, echo)

        server.upstream = failing
        for provider in ("openai", "anthropic", "gemini", "xai", "deepseek", "qwen", "custom"):
            extra = {"base_url": GROQ} if provider == "custom" else {}
            response = client.post("/credentials", json=_save(provider, **extra))
            _no_secret(response, KEY, CODE, VERIFIER)
        listed = client.post("/credentials/llm/models", json={"provider": "openai", "secret": KEY})
        _no_secret(listed, KEY, CODE, VERIFIER)
        _openrouter_upstream(server, exchange=json_response(status, echo))
        exchange = client.post("/credentials/openrouter/exchange", json=_exchange_body())
        _no_secret(exchange, KEY, CODE, VERIFIER, ISSUED_KEY)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connect failed for {KEY}")

    server.upstream = down
    client.post("/credentials", json=_save("openai"))
    client.post("/credentials/openrouter/exchange", json=_exchange_body())

    logged = _everything_logged(caplog)
    assert logged  # the failures were logged, and none of it carries a secret
    for secret in (KEY, CODE, VERIFIER, ISSUED_KEY):
        assert secret not in logged
    assert server.db.credentials_of(ALICE) == []


def test_the_key_a_model_probe_fails_with_is_in_no_log_or_exception_text(
    client: TestClient,
    server: Server,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.DEBUG)
    server.db.add_credential(ALICE, "openai", KEY, model=None)
    SdkWire(lambda request: sdk_error(401, {"error": {"message": f"Incorrect key {KEY}"}})).install(
        monkeypatch
    )

    response = client.put("/credentials/llm/openai/model", json={"model": FIXTURE_MODEL})

    assert response.status_code == 502
    _no_secret(response, KEY)
    assert KEY not in _everything_logged(caplog)
