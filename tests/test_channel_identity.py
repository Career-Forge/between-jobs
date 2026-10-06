"""The identity lookups that belong to no channel in particular (`channel_identity`).

They read `channel_identities` from either end, so what is pinned here is WHICH rows each one
asks for -- the filters are the ownership boundary, because the backend's client bypasses
row-level security -- and what each answers for a row that is not there."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from between_jobs.api.channel_identity import (
    get_chat_ref,
    get_user_id_for_subject,
    list_linked_channels,
)
from between_jobs.api.telegram_identity import get_chat_id


class _Query:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.columns: str | None = None
        self.filters: list[tuple[str, Any]] = []

    def select(self, columns: str) -> _Query:
        self.columns = columns
        return self

    def eq(self, column: str, value: Any) -> _Query:
        self.filters.append((column, value))
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _Supabase:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.query = _Query(rows)

    def table(self, name: str) -> _Query:
        assert name == "channel_identities"
        return self.query


async def test_the_user_of_a_sender_is_looked_up_by_channel_tenant_and_subject() -> None:
    supabase = _Supabase([{"user_id": "user-1"}])

    user_id = await get_user_id_for_subject(supabase, "telegram", "555")  # type: ignore[arg-type]

    assert user_id == "user-1"
    assert supabase.query.filters == [
        ("channel", "telegram"),
        ("external_tenant", ""),
        ("external_subject", "555"),
    ]


async def test_a_channel_with_tenants_passes_its_own() -> None:
    supabase = _Supabase([{"user_id": "user-1"}])

    await get_user_id_for_subject(supabase, "slack", "U1", tenant="T9")  # type: ignore[arg-type]

    assert ("external_tenant", "T9") in supabase.query.filters


async def test_a_sender_nobody_has_met_has_no_user() -> None:
    assert await get_user_id_for_subject(_Supabase([]), "discord", "1") is None  # type: ignore[arg-type]


async def test_the_chat_of_a_user_is_asked_for_on_the_one_channel() -> None:
    supabase = _Supabase([{"external_subject": "777"}])

    chat_ref = await get_chat_ref(supabase, "user-1", "discord")  # type: ignore[arg-type]

    assert chat_ref == "777"
    assert ("user_id", "user-1") in supabase.query.filters
    assert ("channel", "discord") in supabase.query.filters


async def test_a_user_who_has_not_linked_a_channel_has_no_chat_there() -> None:
    assert await get_chat_ref(_Supabase([]), "user-1", "discord") is None  # type: ignore[arg-type]


async def test_the_telegram_chat_id_is_the_generic_lookup_as_an_integer() -> None:
    supabase = _Supabase([{"external_subject": "555"}])

    assert await get_chat_id(supabase, "user-1") == 555  # type: ignore[arg-type]
    assert ("channel", "telegram") in supabase.query.filters
    assert await get_chat_id(_Supabase([]), "user-1") is None  # type: ignore[arg-type]


async def test_the_linked_channels_of_a_user_are_each_listed_once_in_a_stable_order() -> None:
    supabase = _Supabase([{"channel": "telegram"}, {"channel": "discord"}, {"channel": "telegram"}])

    channels = await list_linked_channels(supabase, "user-1")  # type: ignore[arg-type]

    assert channels == ["discord", "telegram"]
    assert supabase.query.columns == "channel"
    assert supabase.query.filters == [("user_id", "user-1")]  # only this user's rows


async def test_a_user_with_nothing_linked_has_no_channels() -> None:
    assert await list_linked_channels(_Supabase([]), "user-1") == []  # type: ignore[arg-type]
