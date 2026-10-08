"""Shared FastAPI dependency-provider functions, reading from `app.state`.

Split out from app.py so route modules (e.g. telegram_webhook.py) can
depend on these without importing the app module itself -- app.py needs
to include those routers, so the reverse import would be circular.
"""

from __future__ import annotations

import logging
from typing import cast

import httpx
from fastapi import Depends, Request

from supabase import AsyncClient

from .channel_envelope import Notifier
from .channel_push import FanOutNotifier
from .discord_adapter import CHANNEL as DISCORD_CHANNEL
from .discord_adapter import build_notifier as build_discord_notifier
from .discord_client import DiscordClient
from .discord_config import DiscordConfig
from .errors import ApiError
from .telegram_adapter import TelegramRenderer, build_notifier, webhook_info_fetcher
from .telegram_client import TelegramClient
from .telegram_identity import CHANNEL as TELEGRAM_CHANNEL
from .webhook_probe import WEBHOOK_CHECK_ENV, WEBHOOK_CHECK_NAME, WebhookProbe
from .worker_pings import WorkerPings

logger = logging.getLogger(__name__)


def get_supabase(request: Request) -> AsyncClient:
    return cast(AsyncClient, request.app.state.supabase)


def get_http_client(request: Request) -> httpx.AsyncClient:
    return cast(httpx.AsyncClient, request.app.state.http)


def get_hiring_http_client(request: Request) -> httpx.AsyncClient:
    """The HTTP client Hiring Signals is handed: a separate client that refuses,
    at request time, every host that is not one of the four search providers
    (see `hiring_signal_search.refuse_non_provider_hosts`). Not `app.state.http`,
    which the ATS poller and Gmail also use for their own hosts."""
    return cast(httpx.AsyncClient, request.app.state.hiring_http)


def telegram_enabled(request: Request) -> bool:
    """Whether this server was started with a Telegram bot. The lifespan sets
    both Telegram values together or neither (see `app.lifespan`), so either
    one answers it."""
    return request.app.state.telegram_client is not None


def discord_enabled(request: Request) -> bool:
    """Whether this server can take a Discord interaction. The lifespan sets
    `app.state.discord_enabled` from the Discord settings (`discord_config`): true when the
    application id and public key are configured, false otherwise, so nothing here offers a
    Discord link on a server that could not redeem one."""
    return bool(getattr(request.app.state, "discord_enabled", False))


def channel_enabled(request: Request, channel: str) -> bool:
    """Whether a link code for `channel` could be redeemed on this server: the channel has an
    adapter here. A channel the vocabulary knows but nothing serves answers False."""
    if channel == TELEGRAM_CHANNEL:
        return telegram_enabled(request)
    if channel == DISCORD_CHANNEL:
        return discord_enabled(request)
    return False


def build_notifier_registry(
    supabase: AsyncClient,
    telegram_client: TelegramClient | None,
    discord_client: DiscordClient | None = None,
) -> dict[str, Notifier]:
    """The notifier of every channel this server can push to, by channel name. A channel with
    no adapter on this server has no entry, and a user linked there is skipped (with a log
    line) by `FanOutNotifier`. A new channel's adapter adds its entry here. Discord has one only
    when the server has a bot token: without it nothing can be sent to a person unprompted."""
    registry: dict[str, Notifier] = {}
    telegram = build_notifier(supabase, telegram_client)
    if telegram is not None:
        registry[TELEGRAM_CHANNEL] = telegram
    discord = build_discord_notifier(supabase, discord_client)
    if discord is not None:
        registry[DISCORD_CHANNEL] = discord
    return registry


def build_push_notifier(
    supabase: AsyncClient,
    telegram_client: TelegramClient | None,
    discord_client: DiscordClient | None = None,
) -> FanOutNotifier | None:
    """What the outbox listener pushes through: every channel a user linked that has an
    entry in `build_notifier_registry`, or None on a server with no channel to push to."""
    registry = build_notifier_registry(supabase, telegram_client, discord_client)
    return FanOutNotifier(supabase, registry) if registry else None


def build_webhook_probe(
    telegram_client: TelegramClient | None, pings: WorkerPings, *, host_enabled: bool
) -> WebhookProbe | None:
    """The daily Telegram webhook probe (webhook_probe.py), or None on a server with no bot.

    `host_enabled` says whether the worker the probe rides on (the hiring-signal cache purge)
    is running here. The check's setting (HEALTHCHECKS_URL_TELEGRAM_WEBHOOK) is read and
    validated whatever else is true, so a malformed value stops the boot; a URL for a probe
    that will not run is said out loud, since its check would go down (once it has been pinged
    at least once; a check never pinged stays "new" and never alerts)."""
    if telegram_client is None:
        pings.for_check(
            WEBHOOK_CHECK_NAME,
            env_name=WEBHOOK_CHECK_ENV,
            enabled=False,
            off_message=(
                "a healthcheck URL is set for the Telegram webhook probe, but this server has "
                "no Telegram bot, so nothing is probed and the check will go down once it has "
                "been pinged before; delete that check"
            ),
        )
        return None
    heartbeat = pings.for_check(
        WEBHOOK_CHECK_NAME,
        env_name=WEBHOOK_CHECK_ENV,
        enabled=host_enabled,
        off_message=(
            "a healthcheck URL is set for the Telegram webhook probe, but the worker it runs "
            "in (DISABLE_HIRING_SIGNAL_CACHE_PURGE) is switched off, so nothing is probed and "
            "the check will go down once it has been pinged before; delete that check or "
            "switch the worker on"
        ),
    )
    if not host_enabled:
        logger.warning(
            "the Telegram webhook probe will not run: it runs inside the hiring-signal cache "
            "purge worker, which is switched off (DISABLE_HIRING_SIGNAL_CACHE_PURGE)"
        )
    return WebhookProbe(
        webhook_info_fetcher(telegram_client), heartbeat=heartbeat, scheduled=host_enabled
    )


def _require_telegram(request: Request) -> None:
    if not telegram_enabled(request):
        raise ApiError("FEATURE_DISABLED", "Telegram isn't set up on this server.")


def get_telegram_client(request: Request) -> TelegramClient:
    """The bot client, or 404 FEATURE_DISABLED on a server running without one.
    Every Telegram route depends on this (or `get_webhook_secret`), so none of
    them does anything -- least of all compare a secret against nothing -- when
    Telegram isn't configured."""
    _require_telegram(request)
    return cast(TelegramClient, request.app.state.telegram_client)


def get_telegram_renderer(
    telegram: TelegramClient = Depends(get_telegram_client),
) -> TelegramRenderer:
    """The bot as a channel renderer: what the webhook hands to the channel-neutral logic.
    Built on `get_telegram_client`, so it is just as unavailable (404 FEATURE_DISABLED) on a
    server running without a bot, and a test that overrides that client gets its fake
    wrapped here."""
    return TelegramRenderer(telegram)


def get_webhook_secret(request: Request) -> str:
    _require_telegram(request)
    return cast(str, request.app.state.telegram_webhook_secret)


def get_discord_config(request: Request) -> DiscordConfig:
    """The Discord settings, or 404 FEATURE_DISABLED on a server running without them. The
    interactions route depends on this, so it does nothing -- least of all compare a signature
    against a missing key -- when Discord is not configured."""
    config = getattr(request.app.state, "discord_config", None)
    if config is None or not discord_enabled(request):
        raise ApiError("FEATURE_DISABLED", "Discord isn't set up on this server.")
    return cast(DiscordConfig, config)


def get_discord_client(request: Request) -> DiscordClient:
    """The Discord REST client (built on the app's shared HTTP client, with the bot token when
    the server has one), or 404 FEATURE_DISABLED on a server running without Discord."""
    get_discord_config(request)
    return cast(DiscordClient, request.app.state.discord_client)
