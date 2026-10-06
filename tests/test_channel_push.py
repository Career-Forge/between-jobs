"""An unprompted message on every channel a user linked (`channel_push.FanOutNotifier`), and the
digest listener's push through it.

The first half pins the fan-out on its own: one message per linked channel, a channel with no
notifier skipped with a log line, one channel failing without stopping the next, and what it
answers (True when any channel took the message, False when nothing was sent and nothing refused,
a raise when nothing was sent and a channel refused). The second half
runs the real digest listener over it: a user with two channels gets exactly one push on each, a
user with one behaves as before, and a push is never repeated for an event the listener has seen
(the Today item's unique key answers that before the push is reached)."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest
import test_digest_listener as digest_fakes
from postgrest.exceptions import APIError

from between_jobs.api.app_state import build_notifier_registry, build_push_notifier
from between_jobs.api.channel_envelope import RichText, rich
from between_jobs.api.channel_push import FanOutNotifier
from between_jobs.api.digest_listener import handle_batch
from between_jobs.api.telegram_adapter import TelegramNotifier
from between_jobs.api.telegram_client import TelegramClient

USER = digest_fakes._USER_ID


class _Identities:
    """`channel_identities` for one user: the channels they have linked."""

    def __init__(self, channels: list[str], *, error: Exception | None = None) -> None:
        self._rows = [{"channel": channel} for channel in channels]
        self._error = error
        self.filters: list[tuple[str, Any]] = []
        self.lookups = 0

    def select(self, *_: Any, **__: Any) -> _Identities:
        self.lookups += 1
        return self

    def eq(self, column: str, value: Any) -> _Identities:
        self.filters.append((column, value))
        return self

    async def execute(self) -> SimpleNamespace:
        if self._error is not None:
            raise self._error
        return SimpleNamespace(data=self._rows)


class _Supabase:
    def __init__(self, channels: list[str], *, error: Exception | None = None) -> None:
        self.identities = _Identities(channels, error=error)

    def table(self, name: str) -> _Identities:
        assert name == "channel_identities"
        return self.identities


class _Notifier:
    """One channel's notifier: records what it is asked to say, and can refuse or find no chat."""

    def __init__(self, *, refuse: bool = False, linked: bool = True) -> None:
        self.sent: list[tuple[str, str]] = []
        self.attempts = 0
        self.raised: list[Exception] = []
        self._refuse = refuse
        self._linked = linked

    async def notify(self, user_id: str, text: RichText) -> bool:
        self.attempts += 1
        if self._refuse:
            error = RuntimeError("the channel refused the message: secret-request-detail")
            self.raised.append(error)
            raise error
        if not self._linked:
            return False
        self.sent.append((user_id, text.plain_text()))
        return True


def _fan_out(channels: list[str], notifiers: dict[str, _Notifier]) -> FanOutNotifier:
    return FanOutNotifier(_Supabase(channels), notifiers)  # type: ignore[arg-type]


# -- the fan-out on its own ----------------------------------------------------------------


async def test_a_user_linked_on_two_channels_is_told_once_on_each() -> None:
    telegram, discord = _Notifier(), _Notifier()
    fan_out = _fan_out(["telegram", "discord"], {"telegram": telegram, "discord": discord})

    sent = await fan_out.notify(USER, rich("a match"))

    assert sent is True
    assert telegram.sent == [(USER, "a match")]
    assert discord.sent == [(USER, "a match")]


async def test_only_the_channels_the_user_linked_are_asked() -> None:
    telegram, discord = _Notifier(), _Notifier()
    fan_out = _fan_out(["telegram"], {"telegram": telegram, "discord": discord})

    await fan_out.notify(USER, rich("a match"))

    assert telegram.sent == [(USER, "a match")]
    assert discord.sent == []  # registered, but this user never linked it


async def test_the_lookup_is_of_this_users_channels_only() -> None:
    supabase = _Supabase(["telegram"])

    await FanOutNotifier(supabase, {"telegram": _Notifier()}).notify(USER, rich("x"))  # type: ignore[arg-type]

    assert supabase.identities.filters == [("user_id", USER)]
    assert supabase.identities.lookups == 1  # the fan-out asks once, whatever the channel count


async def test_a_channel_with_no_notifier_is_skipped_with_a_log_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="between_jobs.api.channel_push")
    telegram = _Notifier()
    fan_out = _fan_out(["extension", "telegram"], {"telegram": telegram})

    sent = await fan_out.notify(USER, rich("a match"))

    assert sent is True
    assert telegram.sent == [(USER, "a match")]  # the registered channel is still served
    (skipped,) = [r for r in caplog.records if "no notifier" in r.getMessage()]
    assert skipped.ctx == {"channel": "extension"}  # type: ignore[attr-defined]


async def test_one_channel_failing_does_not_stop_the_others(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="between_jobs.api.channel_push")
    discord, telegram = _Notifier(refuse=True), _Notifier()
    # "discord" sorts first, so it is attempted first and is the one that fails
    fan_out = _fan_out(["telegram", "discord"], {"telegram": telegram, "discord": discord})

    sent = await fan_out.notify(USER, rich("a match"))

    assert sent is True  # Telegram took it
    assert telegram.sent == [(USER, "a match")]
    (failure,) = [r for r in caplog.records if r.levelno == logging.WARNING]
    # the channel AND the person, so a lost push can be traced; never the text
    assert failure.ctx == {"channel": "discord", "user_id": USER}  # type: ignore[attr-defined]
    assert failure.exc_info is not None
    assert "a match" not in failure.getMessage()  # the text is never logged


async def test_a_message_that_reached_one_channel_is_sent_however_the_others_fared() -> None:
    """True means ANY channel took it: a later channel finding no chat, or refusing, does not
    take that back (the last channel's answer must not be the answer)."""
    for later in (_Notifier(linked=False), _Notifier(refuse=True)):
        discord = _Notifier()
        # alphabetical order: "discord" takes it, then "telegram" finds no chat or refuses
        fan_out = _fan_out(["discord", "telegram"], {"discord": discord, "telegram": later})

        assert await fan_out.notify(USER, rich("a match")) is True
        assert discord.sent == [(USER, "a match")]


async def test_a_channel_with_no_chat_is_a_no_and_not_a_refusal() -> None:
    fan_out = _fan_out(
        ["discord", "telegram"],
        {"discord": _Notifier(linked=False), "telegram": _Notifier(linked=False)},
    )

    assert await fan_out.notify(USER, rich("a match")) is False


async def test_it_raises_when_nothing_was_sent_and_a_channel_refused() -> None:
    """The `Notifier` contract: False is "nothing to send it to", and a refusal is raised. Every
    linked channel is still tried first."""
    telegram = _Notifier(linked=False)
    fan_out = _fan_out(
        ["discord", "telegram"], {"discord": _Notifier(refuse=True), "telegram": telegram}
    )

    with pytest.raises(RuntimeError, match="the channel refused"):
        await fan_out.notify(USER, rich("a match"))


async def test_it_raises_the_first_refusal_after_trying_every_channel() -> None:
    first = _Notifier(refuse=True)
    second = _Notifier(refuse=True)
    notifiers = {"discord": first, "telegram": second}
    fan_out = _fan_out(["discord", "telegram"], notifiers)

    with pytest.raises(RuntimeError) as raised:
        await fan_out.notify(USER, rich("a match"))

    assert first.attempts == 1 and second.attempts == 1  # the second was tried after the first
    assert raised.value is first.raised[0]  # and it is the first one that is raised


async def test_a_user_with_nothing_linked_is_a_normal_no() -> None:
    telegram = _Notifier()

    assert await _fan_out([], {"telegram": telegram}).notify(USER, rich("x")) is False
    assert telegram.sent == []


async def test_a_failed_lookup_of_the_users_channels_reaches_the_caller() -> None:
    fan_out = FanOutNotifier(
        _Supabase([], error=ConnectionError("db down")),  # type: ignore[arg-type]
        {"telegram": _Notifier()},
    )

    with pytest.raises(ConnectionError):
        await fan_out.notify(USER, rich("x"))


def test_the_registry_is_copied_so_nothing_adds_to_it_later() -> None:
    registry: dict[str, _Notifier] = {"telegram": _Notifier()}
    fan_out = _fan_out(["telegram"], registry)

    registry["discord"] = _Notifier()

    assert fan_out.channels == frozenset({"telegram"})


# -- how the app builds it -----------------------------------------------------------------


def test_a_server_with_a_bot_registers_telegram_and_nothing_else() -> None:
    supabase = _Supabase([])
    client = TelegramClient(None, "test-token-not-real")  # type: ignore[arg-type]

    registry = build_notifier_registry(supabase, client)  # type: ignore[arg-type]

    assert set(registry) == {"telegram"}
    assert isinstance(registry["telegram"], TelegramNotifier)
    push = build_push_notifier(supabase, client)  # type: ignore[arg-type]
    assert push is not None and push.channels == frozenset({"telegram"})


def test_a_server_without_a_bot_has_nothing_to_push_through() -> None:
    supabase = _Supabase([])

    assert build_notifier_registry(supabase, None) == {}  # type: ignore[arg-type]
    assert build_push_notifier(supabase, None) is None  # type: ignore[arg-type]


# -- the digest listener over it -------------------------------------------------------------


class _DigestSupabase(digest_fakes._FakeSupabaseClient):
    """The digest listener's fake database, plus the user's linked channels."""

    def __init__(self, channels: list[str], **kwargs: Any) -> None:
        super().__init__(applications=[], **kwargs)
        self.identities = _Identities(channels)

    def table(self, name: str) -> Any:
        if name == "channel_identities":
            return self.identities
        return super().table(name)


async def test_a_user_linked_on_two_channels_gets_exactly_one_push_per_channel() -> None:
    telegram, discord = _Notifier(), _Notifier()
    supabase = _DigestSupabase(["telegram", "discord"])
    notifier = FanOutNotifier(supabase, {"telegram": telegram, "discord": discord})  # type: ignore[arg-type]
    row = digest_fakes._job_match_row()

    inserted = await handle_batch(supabase, [row], notifier=notifier)  # type: ignore[arg-type]

    assert inserted == 1
    for channel in (telegram, discord):
        ((user_id, text),) = channel.sent
        assert user_id == USER
        assert "Backend Engineer" in text
        assert "https://boards.greenhouse.io/acme/jobs/1" in text
    assert len(supabase.high_fit_job_rpc.calls) == 1  # one Today item, not one per channel


async def test_a_user_with_only_telegram_is_pushed_to_exactly_as_before() -> None:
    telegram = _Notifier()
    supabase = _DigestSupabase(["telegram"])
    notifier = FanOutNotifier(supabase, {"telegram": telegram})  # type: ignore[arg-type]

    inserted = await handle_batch(
        supabase,  # type: ignore[arg-type]
        [digest_fakes._job_match_row()],
        notifier=notifier,
    )

    assert inserted == 1
    ((user_id, text),) = telegram.sent
    assert user_id == USER and "Strong fit -- backend skills align" in text


async def test_a_failing_channel_costs_neither_the_other_push_nor_the_today_item() -> None:
    telegram = _Notifier()
    supabase = _DigestSupabase(["telegram", "discord"])
    notifier = FanOutNotifier(
        supabase,  # type: ignore[arg-type]
        {"telegram": telegram, "discord": _Notifier(refuse=True)},
    )

    inserted = await handle_batch(
        supabase,  # type: ignore[arg-type]
        [digest_fakes._job_match_row()],
        notifier=notifier,
    )

    assert inserted == 1
    assert len(supabase.high_fit_job_rpc.calls) == 1
    assert len(telegram.sent) == 1


async def test_a_push_lost_on_every_channel_is_logged_against_its_event_and_user(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When nothing got through, the operator can tell which person missed which push: the
    listener's own warning carries the outbox event and the user. The Today item stays."""
    supabase = _DigestSupabase(["telegram"])
    notifier = FanOutNotifier(supabase, {"telegram": _Notifier(refuse=True)})  # type: ignore[arg-type]

    with caplog.at_level(logging.WARNING, logger="between_jobs.api.digest_listener"):
        inserted = await handle_batch(
            supabase,  # type: ignore[arg-type]
            [digest_fakes._job_match_row(event_id="evt-match-9")],
            notifier=notifier,
        )

    assert inserted == 1
    assert len(supabase.high_fit_job_rpc.calls) == 1  # the Today item is saved
    (lost,) = [r for r in caplog.records if r.name == "between_jobs.api.digest_listener"]
    assert lost.ctx == {"outbox_event_id": "evt-match-9", "user_id": USER}  # type: ignore[attr-defined]
    assert "secret-request-detail" not in lost.getMessage()


async def test_a_push_one_channel_took_is_not_logged_as_lost(
    caplog: pytest.LogCaptureFixture,
) -> None:
    supabase = _DigestSupabase(["telegram", "discord"])
    notifier = FanOutNotifier(
        supabase,  # type: ignore[arg-type]
        {"telegram": _Notifier(), "discord": _Notifier(refuse=True)},
    )

    with caplog.at_level(logging.WARNING, logger="between_jobs.api.digest_listener"):
        await handle_batch(
            supabase,  # type: ignore[arg-type]
            [digest_fakes._job_match_row()],
            notifier=notifier,
        )

    assert [r for r in caplog.records if r.name == "between_jobs.api.digest_listener"] == []


async def test_an_event_the_listener_has_seen_before_is_never_pushed_again() -> None:
    """The same outbox event arriving twice (a batch run again after a failure further on) hits
    the Today item's unique key, which `handle_batch` swallows before it reaches the push: so
    no channel is told a second time. The same job for the same saved search under a NEW event
    id is stopped the same way, by the (saved search, apply url) key."""
    telegram, discord = _Notifier(), _Notifier()
    rpc = digest_fakes._FakeHighFitJobRpc(raise_unique_violation_on={"evt-match-1"})
    supabase = _DigestSupabase(["telegram", "discord"], high_fit_job_rpc=rpc)
    notifier = FanOutNotifier(supabase, {"telegram": telegram, "discord": discord})  # type: ignore[arg-type]

    inserted = await handle_batch(
        supabase,  # type: ignore[arg-type]
        [digest_fakes._job_match_row(event_id="evt-match-1")],
        notifier=notifier,
    )

    assert inserted == 0
    assert telegram.sent == [] and discord.sent == []
    assert supabase.identities.lookups == 0  # not even the user's channels were looked up


async def test_an_insert_that_fails_for_another_reason_pushes_nothing() -> None:
    class _Broken(digest_fakes._FakeRpcBuilder):
        async def execute(self) -> SimpleNamespace:
            raise APIError({"message": "boom", "code": "XX000", "details": None, "hint": None})

    class _Db(_DigestSupabase):
        def rpc(self, fn: str, params: dict[str, Any]) -> Any:
            return _Broken(self.high_fit_job_rpc, params)

    telegram = _Notifier()
    supabase = _Db(["telegram"])
    notifier = FanOutNotifier(supabase, {"telegram": telegram})  # type: ignore[arg-type]

    with pytest.raises(APIError):
        await handle_batch(
            supabase,  # type: ignore[arg-type]
            [digest_fakes._job_match_row()],
            notifier=notifier,
        )

    assert telegram.sent == []
