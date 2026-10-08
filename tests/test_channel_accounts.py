"""Accounts behind a chat sender (api/channel_accounts.py): Telegram's are handed to
`telegram_identity` untouched, Discord's are made by the same rules with the channel in the
metadata, and a channel nobody wrote rules for gets nothing."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from postgrest.exceptions import APIError

from between_jobs.api import channel_accounts as accounts
from between_jobs.api import telegram_identity

SUBJECT = "777000111222333444"
EXISTING = "00000000-0000-0000-0000-000000000001"
NEW = "00000000-0000-0000-0000-000000000002"


class _Chain:
    def __init__(self, rows: list[dict[str, Any]], owner: _Supabase, *, writing: str = "") -> None:
        self._rows = rows
        self._owner = owner
        self._writing = writing

    def select(self, *_: Any) -> _Chain:
        return self

    def eq(self, column: str, value: Any) -> _Chain:
        self._owner.filters.append((column, value))
        return self

    async def execute(self) -> SimpleNamespace:
        if self._writing == "insert" and self._owner.insert_error is not None:
            raise self._owner.insert_error
        return SimpleNamespace(data=self._rows)


class _Table:
    def __init__(self, owner: _Supabase) -> None:
        self._owner = owner

    def select(self, *_: Any) -> _Chain:
        rows = self._owner.lookup_results.pop(0) if self._owner.lookup_results else []
        return _Chain(rows, self._owner)

    def insert(self, data: dict[str, Any]) -> _Chain:
        self._owner.inserted.append(data)
        return _Chain([{}], self._owner, writing="insert")

    def delete(self) -> _Chain:
        self._owner.deletes += 1
        return _Chain([], self._owner)


class _Admin:
    def __init__(self, owner: _Supabase, user: SimpleNamespace) -> None:
        self._owner = owner
        self._user = user

    async def create_user(self, attributes: dict[str, Any]) -> SimpleNamespace:
        self._owner.created.append(attributes)
        return SimpleNamespace(user=SimpleNamespace(id=NEW))

    async def get_user_by_id(self, user_id: str) -> SimpleNamespace:
        self._owner.asked_for.append(user_id)
        return SimpleNamespace(user=self._user)


class _Supabase:
    def __init__(
        self,
        *,
        lookups: list[list[dict[str, Any]]] | None = None,
        user: SimpleNamespace | None = None,
        insert_error: Exception | None = None,
    ) -> None:
        self.lookup_results = lookups or []
        self.insert_error = insert_error
        self.inserted: list[dict[str, Any]] = []
        self.created: list[dict[str, Any]] = []
        self.asked_for: list[str] = []
        self.filters: list[tuple[str, Any]] = []
        self.deletes = 0
        self.auth = SimpleNamespace(
            admin=_Admin(self, user or SimpleNamespace(app_metadata={}, email="x"))
        )

    def table(self, name: str) -> _Table:
        assert name == "channel_identities"
        return _Table(self)


async def test_a_known_discord_sender_resolves_to_their_user_without_making_one() -> None:
    supabase: Any = _Supabase(lookups=[[{"user_id": EXISTING}]])

    user = await accounts.resolve_or_create_user_id_for_subject(supabase, "discord", SUBJECT)

    assert user == EXISTING and supabase.created == [] and supabase.inserted == []
    assert ("channel", "discord") in supabase.filters
    assert ("external_subject", SUBJECT) in supabase.filters
    assert ("external_tenant", "") in supabase.filters


async def test_a_new_discord_sender_gets_a_bot_only_account_and_an_identity_row() -> None:
    supabase: Any = _Supabase(lookups=[[]])

    user = await accounts.resolve_or_create_user_id_for_subject(supabase, "discord", SUBJECT)

    assert user == NEW
    (attributes,) = supabase.created
    assert attributes["email_confirm"] is True
    assert attributes["email"].startswith("discord-") and SUBJECT not in attributes["email"]
    assert attributes["app_metadata"] == {
        "bj_provisioned_by": "discord",
        "bj_discord_subject": SUBJECT,
    }
    (row,) = supabase.inserted
    assert row["user_id"] == NEW and row["channel"] == "discord"
    assert row["external_subject"] == SUBJECT and row["external_tenant"] == ""
    assert row["verified_at"]


async def test_each_account_gets_its_own_random_email() -> None:
    first, second = _Supabase(lookups=[[]]), _Supabase(lookups=[[]])
    for supabase in (first, second):
        client: Any = supabase
        await accounts.resolve_or_create_user_id_for_subject(client, "discord", SUBJECT)
    assert first.created[0]["email"] != second.created[0]["email"]


async def test_losing_the_race_for_a_new_sender_falls_back_to_the_winners_account() -> None:
    race = APIError({"message": "duplicate", "code": "23505", "details": None, "hint": None})
    supabase: Any = _Supabase(lookups=[[], [{"user_id": EXISTING}]], insert_error=race)

    user = await accounts.resolve_or_create_user_id_for_subject(supabase, "discord", SUBJECT)

    assert user == EXISTING


async def test_a_failed_insert_that_is_not_a_race_is_raised() -> None:
    broken = APIError({"message": "boom", "code": "XX000", "details": None, "hint": None})
    supabase: Any = _Supabase(lookups=[[]], insert_error=broken)
    with pytest.raises(APIError):
        await accounts.resolve_or_create_user_id_for_subject(supabase, "discord", SUBJECT)


async def test_the_race_winner_that_cannot_be_read_back_is_an_error() -> None:
    race = APIError({"message": "duplicate", "code": "23505", "details": None, "hint": None})
    supabase: Any = _Supabase(lookups=[[], []], insert_error=race)
    with pytest.raises(RuntimeError):
        await accounts.resolve_or_create_user_id_for_subject(supabase, "discord", SUBJECT)


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"bj_provisioned_by": "discord", "bj_discord_subject": SUBJECT}, True),
        ({"bj_provisioned_by": "discord", "bj_discord_subject": "other"}, False),
        ({"bj_provisioned_by": "telegram", "bj_telegram_subject": SUBJECT}, False),
        ({"bj_provisioned_by": "discord", "bj_telegram_subject": SUBJECT}, False),
        ({"bj_provisioned_by": "discord"}, False),
        # The right sender, but the account was not made by this channel's first contact (a web
        # account that has a Discord subject in its metadata would otherwise be unlinked, or
        # have its data wiped, as if it were a bot-only one).
        ({"bj_discord_subject": SUBJECT}, False),
        ({"bj_provisioned_by": "other", "bj_discord_subject": SUBJECT}, False),
        ({"provider": "email"}, False),
        ({}, False),
    ],
)
async def test_only_the_account_made_for_this_sender_on_this_channel_counts(
    metadata: dict[str, str], expected: bool
) -> None:
    supabase: Any = _Supabase(user=SimpleNamespace(app_metadata=metadata))
    assert (
        await accounts.is_auto_provisioned_for_subject(supabase, EXISTING, "discord", SUBJECT)
        is expected
    )
    assert supabase.asked_for == [EXISTING]


async def test_an_account_with_no_metadata_at_all_is_not_bot_only() -> None:
    supabase: Any = _Supabase(user=SimpleNamespace(app_metadata=None))
    assert not await accounts.is_auto_provisioned_for_subject(
        supabase, EXISTING, "discord", SUBJECT
    )


async def test_unlinking_a_discord_sender_removes_exactly_their_identity_row() -> None:
    supabase: Any = _Supabase()
    await accounts.unlink_subject(supabase, "discord", SUBJECT)
    assert supabase.deletes == 1
    assert sorted(supabase.filters) == sorted(
        [("channel", "discord"), ("external_tenant", ""), ("external_subject", SUBJECT)]
    )


async def test_telegram_is_handed_to_the_telegram_module_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, tuple[Any, ...]]] = []

    async def resolve(_s: Any, channel: str, subject: str) -> str:
        seen.append(("resolve", (channel, subject)))
        return "tg-user"

    async def provisioned(_s: Any, user_id: str, channel: str, subject: str) -> bool:
        seen.append(("provisioned", (user_id, channel, subject)))
        return True

    async def unlink(_s: Any, channel: str, subject: str) -> None:
        seen.append(("unlink", (channel, subject)))

    monkeypatch.setattr(telegram_identity, "resolve_or_create_user_id_for_subject", resolve)
    monkeypatch.setattr(telegram_identity, "is_auto_provisioned_for_subject", provisioned)
    monkeypatch.setattr(telegram_identity, "unlink_subject", unlink)
    supabase: Any = _Supabase()

    assert (
        await accounts.resolve_or_create_user_id_for_subject(supabase, "telegram", "5") == "tg-user"
    )
    assert await accounts.is_auto_provisioned_for_subject(supabase, "u", "telegram", "5")
    await accounts.unlink_subject(supabase, "telegram", "5")

    assert seen == [
        ("resolve", ("telegram", "5")),
        ("provisioned", ("u", "telegram", "5")),
        ("unlink", ("telegram", "5")),
    ]
    assert (
        supabase.created == [] and supabase.inserted == []
    )  # nothing here was made by this module


@pytest.mark.parametrize("channel", ["slack", "whatsapp", "extension", "", "DISCORD"])
async def test_a_channel_nobody_wrote_rules_for_gets_nothing(channel: str) -> None:
    supabase: Any = _Supabase()
    for call in (
        accounts.resolve_or_create_user_id_for_subject(supabase, channel, "1"),
        accounts.is_auto_provisioned_for_subject(supabase, "u", channel, "1"),
        accounts.unlink_subject(supabase, channel, "1"),
    ):
        with pytest.raises(ValueError, match="no identity provisioning for channel"):
            await call
    assert supabase.created == [] and supabase.inserted == [] and supabase.deletes == 0
    assert supabase.filters == [] and supabase.asked_for == []
