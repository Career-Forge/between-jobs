"""The whole app, with Discord switched on, for the tests that post signed interactions to
`/discord/interactions` (tests/test_discord_webhook.py, tests/test_discord_conversations.py).

`Harness` boots the real FastAPI app and its lifespan with the Discord settings in the
environment (the throwaway key from `discord_fakes`), replaces the three things that reach the
outside world -- the Supabase client, the app's HTTP client and the Discord client -- with fakes
over one `MockTransport` network, and posts correctly signed requests. Leaving the `with` block
runs the app's shutdown, which finishes the background work the requests started, so everything
the person would have seen has happened by then.
"""

from __future__ import annotations

import os
import time
from contextlib import ExitStack
from types import TracebackType
from typing import Any
from unittest import mock

import httpx
from channel_fakes import ComposedSupabase
from discord_fakes import (
    APPLICATION_ID,
    BOT_TOKEN,
    CLAIM_RPCS,
    PRIVATE_KEY,
    PUBLIC_KEY_HEX,
    FakeDiscord,
    FakeInteractionLedger,
    body_of,
    sign,
)
from fastapi.testclient import TestClient

from between_jobs.api import deferred_reply, discord_webhook
from between_jobs.api.app import app
from between_jobs.api.app_state import get_discord_client, get_http_client, get_supabase
from between_jobs.api.discord_client import DiscordClient


class DiscordSupabase(ComposedSupabase):
    """The conversation tests' fake database, plus the interaction claims."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.ledger = FakeInteractionLedger()

    def rpc(self, fn: str, params: dict[str, Any]) -> Any:
        if fn in CLAIM_RPCS:
            return self.ledger.rpc(fn, params)
        return super().rpc(fn, params)


class NoDatabase:
    """A database nothing may touch: every access is recorded and refused."""

    def __init__(self) -> None:
        self.touched: list[str] = []

    def table(self, name: str) -> Any:
        self.touched.append(f"table:{name}")
        raise AssertionError(f"the database was touched: table {name}")

    def rpc(self, name: str, _params: dict[str, Any]) -> Any:
        self.touched.append(f"rpc:{name}")
        raise AssertionError(f"the database was touched: rpc {name}")

    @property
    def auth(self) -> Any:
        self.touched.append("auth")
        raise AssertionError("the database was touched: auth")


class Harness:
    def __init__(
        self,
        supabase: Any,
        world: FakeDiscord | None = None,
        *,
        bot_token: str | None = BOT_TOKEN,
        discord_on: bool = True,
        env: dict[str, str] | None = None,
    ) -> None:
        self.supabase = supabase
        self.world = world or FakeDiscord()
        self._bot_token = bot_token
        self._discord_on = discord_on
        self._env = env or {}
        self._stack = ExitStack()
        self.client: TestClient | None = None
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(self.world.handler))
        self.discord = DiscordClient(self.http, application_id=APPLICATION_ID, bot_token=bot_token)

    def __enter__(self) -> Harness:
        env = {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test-key-not-real",
            **self._env,
        }
        if self._discord_on:
            env["DISCORD_APPLICATION_ID"] = APPLICATION_ID
            env["DISCORD_PUBLIC_KEY"] = PUBLIC_KEY_HEX
            if self._bot_token is not None:
                env["DISCORD_BOT_TOKEN"] = self._bot_token
        self._stack.enter_context(mock.patch.dict(os.environ, env))
        for name in (
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_WEBHOOK_SECRET",
            "WEB_APP_URL",
            *(() if self._discord_on else ("DISCORD_APPLICATION_ID", "DISCORD_PUBLIC_KEY")),
            *(() if self._bot_token is not None else ("DISCORD_BOT_TOKEN",)),
        ):
            os.environ.pop(name, None)

        async def link_ready(_supabase: Any) -> bool:
            return True

        self._stack.enter_context(
            mock.patch("between_jobs.api.channel_core.link_schema_ready", link_ready)
        )
        # The pause after the answer is for the real Discord; the fakes need none.
        self._stack.enter_context(
            mock.patch.object(discord_webhook, "_AFTER_RESPONSE_PAUSE_SECONDS", 0.0)
        )
        app.dependency_overrides[get_supabase] = lambda: self.supabase
        app.dependency_overrides[get_http_client] = lambda: self.http
        app.dependency_overrides[get_discord_client] = lambda: self.discord
        self.client = TestClient(app, raise_server_exceptions=False)
        self.client.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            assert self.client is not None
            self.client.__exit__(exc_type, exc, tb)
        finally:
            app.dependency_overrides.clear()
            self._stack.close()

    # -- requests -------------------------------------------------------------------------

    def post_raw(self, body: bytes, headers: dict[str, str]) -> Any:
        assert self.client is not None
        return self.client.post("/discord/interactions", content=body, headers=headers)

    def post(
        self,
        payload: dict[str, Any],
        *,
        timestamp: int | str | None = None,
        key: Any = PRIVATE_KEY,
    ) -> Any:
        """A correctly signed request (or, with another `key`, one signed by someone else)."""
        body = body_of(payload)
        stamp = str(int(time.time()) if timestamp is None else timestamp)
        return self.post_raw(
            body, {"X-Signature-Ed25519": sign(body, stamp, key), "X-Signature-Timestamp": stamp}
        )

    def settle(self, *, timeout: float = 10.0) -> None:
        """Waits until nothing the requests started is still running."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if (
                discord_webhook.interaction_registry.running == 0
                and deferred_reply.registry.running == 0
            ):
                return
            time.sleep(0.01)
        raise AssertionError("background work did not finish")
