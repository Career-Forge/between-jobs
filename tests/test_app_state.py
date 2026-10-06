"""Which channels a server can take a message from (`app_state.channel_enabled`).

A link code for a channel nothing here serves could never be redeemed, so the answer is fail
closed: a channel with no adapter, and a Discord that nothing has switched on, are both False.
The link route's own allow-list and the app's lifespan hide both defaults from the route tests
(the lifespan sets the Discord flag explicitly), so they are pinned here against a request with
no app behind it."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from fastapi import Request

from between_jobs.api.app_state import channel_enabled, discord_enabled, telegram_enabled


def _request(**state: Any) -> Request:
    """A request whose app carries exactly this state: no FastAPI app, no lifespan."""
    return cast(Request, SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state))))


def test_telegram_is_enabled_exactly_when_the_server_has_a_bot() -> None:
    assert channel_enabled(_request(telegram_client=object()), "telegram") is True
    assert channel_enabled(_request(telegram_client=None), "telegram") is False
    assert telegram_enabled(_request(telegram_client=object())) is True
    assert telegram_enabled(_request(telegram_client=None)) is False


def test_discord_is_off_unless_something_switched_it_on() -> None:
    assert channel_enabled(_request(telegram_client=None, discord_enabled=True), "discord") is True
    assert (
        channel_enabled(_request(telegram_client=None, discord_enabled=False), "discord") is False
    )
    # an app whose lifespan never set the flag: off, not on
    assert discord_enabled(_request(telegram_client=None)) is False
    assert channel_enabled(_request(telegram_client=None), "discord") is False


def test_discord_does_not_follow_telegram_and_telegram_does_not_follow_discord() -> None:
    assert (
        channel_enabled(_request(telegram_client=object(), discord_enabled=False), "discord")
        is False
    )
    assert (
        channel_enabled(_request(telegram_client=None, discord_enabled=True), "telegram") is False
    )


def test_a_channel_nothing_serves_is_never_enabled() -> None:
    for state in (
        {"telegram_client": None},
        {"telegram_client": object(), "discord_enabled": True},
    ):
        for channel in ("slack", "extension", "web", "", "TELEGRAM"):
            assert channel_enabled(_request(**state), channel) is False, channel
