"""Discord is optional, like Telegram: what a server with none of its settings does (nothing: the
endpoint is a 404 FEATURE_DISABLED, no link code is minted, /capabilities answers exactly as it
always did), what a half-set configuration does (stops the boot, naming the variable and never its
value), and what a configured one turns on."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
from discord_fakes import APPLICATION_ID, BOT_TOKEN, PUBLIC_KEY_HEX, body_of, ping, signed_headers
from fastapi.testclient import TestClient
from test_link_routes import _FakeSupabaseClient

from between_jobs.api.app import app
from between_jobs.api.app_state import get_supabase
from between_jobs.api.auth import require_user_id
from between_jobs.api.discord_client import DiscordClient

_USER_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    for name in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_WEBHOOK_SECRET",
        "DISCORD_APPLICATION_ID",
        "DISCORD_PUBLIC_KEY",
        "DISCORD_BOT_TOKEN",
        "DISCORD_INSTALL_URL",
    ):
        monkeypatch.delenv(name, raising=False)


@contextmanager
def _client(supabase: Any = None) -> Iterator[TestClient]:
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    if supabase is not None:
        app.dependency_overrides[get_supabase] = lambda: supabase
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _configure(monkeypatch: pytest.MonkeyPatch, *, token: bool = False) -> None:
    monkeypatch.setenv("DISCORD_APPLICATION_ID", APPLICATION_ID)
    monkeypatch.setenv("DISCORD_PUBLIC_KEY", PUBLIC_KEY_HEX)
    if token:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", BOT_TOKEN)


# -- off ---------------------------------------------------------------------------------------


def test_with_no_discord_settings_the_app_boots_and_discord_is_off() -> None:
    with _client() as client:
        assert client.get("/health").status_code == 200
        assert client.app.state.discord_enabled is False  # type: ignore[attr-defined]
        assert client.app.state.discord_client is None  # type: ignore[attr-defined]


def test_capabilities_answers_exactly_what_it_always_did_when_discord_is_off() -> None:
    with _client() as client:
        assert client.get("/capabilities").json() == {
            "telegram": False,
            "telegram_bot_username": None,
            "tester_program_required": False,
            "engine": "remote",
        }


def test_the_interactions_endpoint_is_a_404_feature_disabled_and_never_a_401() -> None:
    with _client() as client:
        unsigned = client.post("/discord/interactions", json={"type": 1})
        body = body_of(ping())
        signed = client.post(
            "/discord/interactions",
            content=body,
            headers=signed_headers(body, timestamp=1_790_000_000),
        )
    for response in (unsigned, signed):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "FEATURE_DISABLED"


def test_no_discord_link_code_is_minted_when_discord_is_off() -> None:
    supabase = _FakeSupabaseClient()
    with _client(supabase) as client:
        response = client.post("/link/code", json={"channel": "discord"})
    assert response.status_code == 404 and response.json()["error"]["code"] == "FEATURE_DISABLED"
    assert supabase.link_codes.insert_calls == []


# -- half set ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "named"),
    [
        ({"DISCORD_APPLICATION_ID": APPLICATION_ID}, "DISCORD_PUBLIC_KEY"),
        ({"DISCORD_PUBLIC_KEY": PUBLIC_KEY_HEX}, "DISCORD_APPLICATION_ID"),
        ({"DISCORD_BOT_TOKEN": BOT_TOKEN}, "DISCORD_BOT_TOKEN"),
        (
            {"DISCORD_APPLICATION_ID": "x", "DISCORD_PUBLIC_KEY": PUBLIC_KEY_HEX},
            "DISCORD_APPLICATION_ID",
        ),
        (
            {"DISCORD_APPLICATION_ID": APPLICATION_ID, "DISCORD_PUBLIC_KEY": "abcd"},
            "DISCORD_PUBLIC_KEY",
        ),
    ],
)
def test_a_half_set_or_malformed_configuration_stops_the_boot_naming_only_variables(
    monkeypatch: pytest.MonkeyPatch, values: dict[str, str], named: str
) -> None:
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError) as raised, _client():
        pass
    message = str(raised.value)
    assert named in message
    for value in values.values():
        if len(value) > 8:  # a short stand-in could appear in the message by chance
            assert value not in message


# -- on ----------------------------------------------------------------------------------------


def test_a_configured_server_turns_discord_on(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    with _client() as client:
        state = client.app.state  # type: ignore[attr-defined]
        assert state.discord_enabled is True
        assert state.discord_config.application_id == APPLICATION_ID
        assert isinstance(state.discord_client, DiscordClient)
        assert state.discord_client.has_bot_token is False
        assert client.get("/capabilities").json() == {
            "telegram": False,
            "telegram_bot_username": None,
            "tester_program_required": False,
            "engine": "remote",
            "discord": True,
            # Nothing set: Discord's own install link for the application.
            "discord_install_url": (
                f"https://discord.com/oauth2/authorize?client_id={APPLICATION_ID}"
            ),
        }


def test_the_client_the_lifespan_builds_speaks_for_this_application_over_the_shared_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every end-to-end Discord test hands the endpoint a client of its own, so this is the one
    place the client a real boot builds is used: it must carry the configured application id and
    send through the server's shared HTTP client."""
    _configure(monkeypatch, token=True)
    sent: list[tuple[httpx.AsyncClient, str, str, dict[str, str]]] = []

    async def fake_request(
        self: httpx.AsyncClient, method: str, url: Any, **kwargs: Any
    ) -> httpx.Response:
        sent.append((self, method, str(url), dict(kwargs.get("headers") or {})))
        return httpx.Response(
            200, json={"id": "1500000000000000009"}, request=httpx.Request(method, str(url))
        )

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    with _client() as client:
        state = client.app.state  # type: ignore[attr-defined]
        discord = state.discord_client
        asyncio.run(discord.create_followup("interaction-token-not-real", {"content": "hi"}))
        asyncio.run(discord.create_channel_message("1500000000000000001", {"content": "hi"}))
        shared = state.http

    followup, channel_message = sent
    assert followup[0] is shared and channel_message[0] is shared
    assert followup[2] == (
        f"https://discord.com/api/v10/webhooks/{APPLICATION_ID}/interaction-token-not-real"
    )
    assert "Authorization" not in followup[3]  # an interaction token needs no bot token...
    assert channel_message[3]["Authorization"] == f"Bot {BOT_TOKEN}"  # ...a channel message does


def test_a_bot_token_gives_the_client_one(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, token=True)
    with _client() as client:
        assert client.app.state.discord_client.has_bot_token is True  # type: ignore[attr-defined]
        assert BOT_TOKEN not in client.get("/capabilities").text


def test_the_install_address_is_offered_to_the_web_app(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    monkeypatch.setenv("DISCORD_INSTALL_URL", "https://example.com/add-the-app/")
    with _client() as client:
        body = client.get("/capabilities").json()
    assert (
        body["discord"] is True and body["discord_install_url"] == "https://example.com/add-the-app"
    )


def test_a_discord_link_code_is_minted_when_discord_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    supabase = _FakeSupabaseClient()
    with _client(supabase) as client:
        response = client.post("/link/code", json={"channel": "discord"})
    assert response.status_code == 201 and response.json()["channel"] == "discord"
    (inserted,) = supabase.link_codes.insert_calls
    assert inserted["channel"] == "discord" and inserted["user_id"] == _USER_ID


def test_discord_being_on_does_not_turn_telegram_on(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    supabase = _FakeSupabaseClient()
    with _client(supabase) as client:
        response = client.post("/link/code", json={"channel": "telegram"})
    assert response.status_code == 404
    assert supabase.link_codes.insert_calls == []
