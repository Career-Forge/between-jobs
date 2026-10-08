"""The high-fit push over both channels (digest listener -> fan-out -> Telegram and Discord
notifiers), with the real notifiers on fake transports: a user linked on both is told exactly once
on each, and one channel refusing costs the other nothing. Synthetic fixtures, see
tests/discord_fakes.py."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest
import test_digest_listener as digest_fakes
from discord_fakes import BOT_TOKEN, SUBJECT, FakeDiscord, discord_client

from between_jobs.api.app_state import build_notifier_registry, build_push_notifier
from between_jobs.api.channel_push import FanOutNotifier
from between_jobs.api.digest_listener import handle_batch
from between_jobs.api.discord_adapter import DiscordNotifier
from between_jobs.api.logging_setup import format_exception_safely
from between_jobs.api.telegram_adapter import TelegramNotifier, TelegramRenderer

USER = digest_fakes._USER_ID
TELEGRAM_CHAT = "555000111"


class _Identities:
    """`channel_identities` for one user, answering both questions the notifiers ask: which
    channels are linked, and where on one of them."""

    def __init__(self, links: dict[str, str]) -> None:
        self.links = links
        self._columns = ""
        self._filters: dict[str, Any] = {}

    def select(self, columns: str, *_: Any, **__: Any) -> _Identities:
        self._columns = columns
        self._filters = {}
        return self

    def eq(self, column: str, value: Any) -> _Identities:
        self._filters[column] = value
        return self

    async def execute(self) -> SimpleNamespace:
        wanted = self._filters.get("channel")
        rows = [
            {"channel": channel, "external_subject": subject}
            for channel, subject in self.links.items()
            if wanted is None or wanted == channel
        ]
        return SimpleNamespace(data=[{k: r[k] for k in self._columns.split(",")} for r in rows])


class _Supabase(digest_fakes._FakeSupabaseClient):
    def __init__(self, links: dict[str, str], **kwargs: Any) -> None:
        super().__init__(applications=[], **kwargs)
        self.identities = _Identities(links)

    def table(self, name: str) -> Any:
        if name == "channel_identities":
            return self.identities
        return super().table(name)


class _TelegramClient:
    """Stands in for `TelegramClient`: records what the renderer asks it to send."""

    def __init__(self, *, refuse: bool = False) -> None:
        self.sent: list[tuple[int, str]] = []
        self._refuse = refuse

    async def send_message(self, chat_id: int, text: str, **_: Any) -> int:
        if self._refuse:
            raise RuntimeError("telegram refused")
        self.sent.append((chat_id, str(text)))
        return 1


def _fan_out(
    supabase: _Supabase, telegram: _TelegramClient, discord: FakeDiscord | None
) -> FanOutNotifier:
    notifiers: dict[str, Any] = {
        "telegram": TelegramNotifier(supabase, TelegramRenderer(telegram))  # type: ignore[arg-type]
    }
    if discord is not None:
        notifiers["discord"] = DiscordNotifier(supabase, discord_client(discord))  # type: ignore[arg-type]
    return FanOutNotifier(supabase, notifiers)  # type: ignore[arg-type]


LINKED_ON_BOTH = {"telegram": TELEGRAM_CHAT, "discord": SUBJECT}


async def test_a_user_linked_on_telegram_and_discord_is_pushed_to_exactly_once_on_each() -> None:
    supabase = _Supabase(LINKED_ON_BOTH)
    telegram, discord = _TelegramClient(), FakeDiscord()

    inserted = await handle_batch(
        supabase,  # type: ignore[arg-type]
        [digest_fakes._job_match_row()],
        notifier=_fan_out(supabase, telegram, discord),
    )

    assert inserted == 1
    assert len(supabase.high_fit_job_rpc.calls) == 1  # one Today item, not one per channel
    ((chat_id, text),) = telegram.sent
    assert chat_id == int(TELEGRAM_CHAT)
    assert "Backend Engineer" in text
    opened, posted = discord.requests
    assert opened.kind == "open_dm" and opened.json_body == {"recipient_id": SUBJECT}
    assert posted.kind == "channel_message" and posted.authorized_as_bot
    assert posted.json_body is not None
    content = posted.json_body["content"]
    assert "Backend Engineer" in content
    assert "https://boards.greenhouse.io/acme/jobs/1" in content
    assert posted.json_body["allowed_mentions"] == {"parse": []}


async def test_the_same_event_arriving_again_pushes_to_nobody() -> None:
    rpc = digest_fakes._FakeHighFitJobRpc(raise_unique_violation_on={"evt-match-1"})
    supabase = _Supabase(LINKED_ON_BOTH, high_fit_job_rpc=rpc)
    telegram, discord = _TelegramClient(), FakeDiscord()

    inserted = await handle_batch(
        supabase,  # type: ignore[arg-type]
        [digest_fakes._job_match_row(event_id="evt-match-1")],
        notifier=_fan_out(supabase, telegram, discord),
    )

    assert inserted == 0
    assert telegram.sent == [] and discord.requests == []


async def test_a_user_linked_on_discord_alone_is_pushed_to_there_alone() -> None:
    supabase = _Supabase({"discord": SUBJECT})
    telegram, discord = _TelegramClient(), FakeDiscord()

    await handle_batch(
        supabase,  # type: ignore[arg-type]
        [digest_fakes._job_match_row()],
        notifier=_fan_out(supabase, telegram, discord),
    )

    assert telegram.sent == []
    assert [r.kind for r in discord.requests] == ["open_dm", "channel_message"]


async def test_discord_refusing_costs_neither_the_telegram_push_nor_the_today_item(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A person who turned off direct messages from the app's server: Discord answers 50007 ("Cannot
    send messages to this user"). The failure is logged with that code and no token, and nothing
    else is affected."""
    supabase = _Supabase(LINKED_ON_BOTH)
    telegram, discord = _TelegramClient(), FakeDiscord()
    discord.fail("open_dm", status=403, code=50007)

    with caplog.at_level(logging.WARNING):
        inserted = await handle_batch(
            supabase,  # type: ignore[arg-type]
            [digest_fakes._job_match_row()],
            notifier=_fan_out(supabase, telegram, discord),
        )

    assert inserted == 1 and len(supabase.high_fit_job_rpc.calls) == 1
    assert len(telegram.sent) == 1
    assert discord.of("channel_message") == []
    (failed,) = [r for r in caplog.records if "a push failed on one channel" in r.getMessage()]
    assert failed.ctx == {"channel": "discord", "user_id": USER}  # type: ignore[attr-defined]
    assert failed.exc_info is not None
    shown = format_exception_safely(failed.exc_info[1])  # type: ignore[arg-type]
    assert "code=50007" in shown and "status=403" in shown
    assert BOT_TOKEN not in shown and BOT_TOKEN not in caplog.text


async def test_a_push_lost_on_both_channels_is_logged_against_its_event_and_user(
    caplog: pytest.LogCaptureFixture,
) -> None:
    supabase = _Supabase(LINKED_ON_BOTH)
    discord = FakeDiscord()
    discord.fail("open_dm", status=403, code=50007)

    with caplog.at_level(logging.WARNING, logger="between_jobs.api.digest_listener"):
        inserted = await handle_batch(
            supabase,  # type: ignore[arg-type]
            [digest_fakes._job_match_row(event_id="evt-match-9")],
            notifier=_fan_out(supabase, _TelegramClient(refuse=True), discord),
        )

    assert inserted == 1  # the Today item stays
    (lost,) = [r for r in caplog.records if r.name == "between_jobs.api.digest_listener"]
    assert lost.ctx == {"outbox_event_id": "evt-match-9", "user_id": USER}  # type: ignore[attr-defined]


def test_discord_is_registered_to_push_only_when_the_server_has_a_bot_token() -> None:
    from between_jobs.api.telegram_client import TelegramClient

    supabase = _Supabase({})
    telegram = TelegramClient(None, "test-token-not-real")  # type: ignore[arg-type]
    discord = discord_client(FakeDiscord())
    no_bot = discord_client(FakeDiscord(), bot_token=None)

    assert set(build_notifier_registry(supabase, telegram, discord)) == {"telegram", "discord"}  # type: ignore[arg-type]
    assert set(build_notifier_registry(supabase, telegram, no_bot)) == {"telegram"}  # type: ignore[arg-type]
    assert set(build_notifier_registry(supabase, None, discord)) == {"discord"}  # type: ignore[arg-type]
    assert set(build_notifier_registry(supabase, telegram)) == {"telegram"}  # type: ignore[arg-type]
    assert build_notifier_registry(supabase, None, no_bot) == {}  # type: ignore[arg-type]
    push = build_push_notifier(supabase, telegram, discord)  # type: ignore[arg-type]
    assert push is not None and push.channels == frozenset({"telegram", "discord"})
    assert build_push_notifier(supabase, None, no_bot) is None  # type: ignore[arg-type]


async def test_a_user_linked_on_discord_on_a_server_with_no_bot_token_is_skipped_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    supabase = _Supabase(LINKED_ON_BOTH)
    telegram = _TelegramClient()
    notifier = FanOutNotifier(
        supabase,  # type: ignore[arg-type]
        {"telegram": TelegramNotifier(supabase, TelegramRenderer(telegram))},  # type: ignore[arg-type]
    )

    with caplog.at_level(logging.INFO, logger="between_jobs.api.channel_push"):
        await handle_batch(supabase, [digest_fakes._job_match_row()], notifier=notifier)  # type: ignore[arg-type]

    assert len(telegram.sent) == 1
    assert any("no notifier is registered" in r.getMessage() for r in caplog.records)
