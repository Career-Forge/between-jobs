"""Concurrent links against a local Supabase stack: real transactions and row
locks, two callers actually running at once (asyncio.gather), so what's
serialized is serialized by the database and not by the test.

Run with `pytest -m local_supabase` after `supabase start` and
`supabase db reset --local`."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg
import pytest

from .conftest import World

pytestmark = pytest.mark.local_supabase


async def _owners(world: World, subject: str) -> list[str]:
    rows = (
        await world.sb.table("channel_identities")
        .select("user_id")
        .eq("external_subject", subject)
        .execute()
    ).data
    return [r["user_id"] for r in rows]


async def test_two_codes_racing_for_one_telegram_account_link_it_exactly_once(world: World) -> None:
    src = await world.telegram_user()
    first_target = await world.web_user()
    second_target = await world.web_user()
    await world.sb.table("saved_searches").insert({"user_id": src.id, "query": "mine"}).execute()
    first_code = await world.mint(first_target)
    second_code = await world.mint(second_target)

    results = await asyncio.gather(
        world.link(src.subject, first_code, src.id),
        world.link(src.subject, second_code, src.id),
    )

    winners = [r for r in results if r.get("ok")]
    losers = [r for r in results if not r.get("ok")]
    assert len(winners) == 1
    assert losers == [{"ok": False, "reason": "source_mismatch"}]
    winner = winners[0]["target_user_id"]
    assert await _owners(world, src.subject) == [winner]
    searches = (
        await world.sb.table("saved_searches").select("user_id").eq("query", "mine").execute()
    ).data
    assert searches == [{"user_id": winner}]  # moved once, to the one who won


async def test_two_telegram_accounts_racing_for_one_web_account_link_only_one(world: World) -> None:
    first = await world.telegram_user()
    second = await world.telegram_user()
    target = await world.web_user()
    for user, query in ((first, "first's"), (second, "second's")):
        await (
            world.sb.table("saved_searches").insert({"user_id": user.id, "query": query}).execute()
        )
    first_code = await world.mint(target)
    second_code = await world.mint(target)

    results: list[Any] = await asyncio.gather(
        world.link(first.subject, first_code, first.id),
        world.link(second.subject, second_code, second.id),
        return_exceptions=True,
    )

    committed = [r for r in results if isinstance(r, dict) and r.get("ok")]
    assert len(committed) == 1
    refused = [r for r in results if r not in committed]
    assert len(refused) == 1
    loser = refused[0]
    # The loser waits on the target's lock, then sees the winner's identity.
    # Reaching the unique index instead (a raw 23505) would mean the lock
    # stopped serializing them.
    assert loser == {"ok": False, "reason": "target_linked_elsewhere"}
    telegram_rows = (
        await world.sb.table("channel_identities")
        .select("external_subject")
        .eq("user_id", target)
        .eq("channel", "telegram")
        .execute()
    ).data
    assert len(telegram_rows) == 1
    winner_user, loser_user = (
        (first, second) if committed[0]["source_user_id"] == first.id else (second, first)
    )
    assert telegram_rows == [{"external_subject": winner_user.subject}]
    assert await _owners(world, loser_user.subject) == [loser_user.id]
    kept = (
        await world.sb.table("saved_searches")
        .select("user_id")
        .eq("user_id", loser_user.id)
        .execute()
    ).data
    assert len(kept) == 1  # the loser's data never moved


async def test_two_finishers_racing_over_one_link_lose_nothing(world: World) -> None:
    """Telegram redelivering a /link while the first delivery is still finishing
    it: both run finish_link at once."""
    src = await world.telegram_user()
    target = await world.web_user()
    app = await world.application(src.id, await world.job())
    pv = await world.profile_version(src.id)
    payloads = {b"one", b"two", b"three"}
    for version, body in enumerate(sorted(payloads), start=1):
        await world.artifact_version(src.id, app["id"], pv["id"], version=version, body=body)
    await world.link(src.subject, await world.mint(target), src.id)

    first, second = await asyncio.gather(
        world.finish(src.id, target, src.subject),
        world.finish(src.id, target, src.subject),
    )

    assert first.retired and second.retired
    assert not await world.user_exists(src.id)
    rows = (
        await world.sb.table("artifact_versions")
        .select("storage_key")
        .eq("user_id", target)
        .execute()
    ).data
    assert len(rows) == 3
    existing = set(await world.storage_keys(target))
    assert all(r["storage_key"] in existing for r in rows)
    downloaded = {
        bytes(await world.sb.storage.from_("artifacts").download(r["storage_key"])) for r in rows
    }
    assert downloaded == payloads


async def test_a_finisher_waiting_on_the_one_retiring_the_source_finds_it_gone(
    world: World, pg: Any
) -> None:
    """Telegram redelivering a /link while the first delivery is deleting the
    source: the second checks the source exists, blocks on the identity-row
    lock, and wakes after the delete commits. It must answer "gone", not fail
    inside the merge about an account that no longer exists."""
    src = await world.telegram_user()
    target = await world.web_user()
    await world.link(src.subject, await world.mint(target), src.id)
    retiring = await asyncpg.connect(world.stack["DB_URL"])
    try:
        transaction = retiring.transaction()
        await transaction.start()
        deleted = await retiring.fetchval(
            "select public.finish_link_delete_source('telegram', $1, $2::uuid, $3::uuid)",
            src.subject,
            src.id,
            target,
        )
        assert json.loads(deleted)["source_gone"] is True  # not committed yet
        waiting = asyncio.create_task(
            pg.fetchval(
                "select public.finish_link_merge('telegram', $1, $2::uuid, $3::uuid)",
                src.subject,
                src.id,
                target,
            )
        )
        await asyncio.sleep(0.5)
        assert not waiting.done()  # parked on the identity-row lock
        await transaction.commit()
        result = json.loads(await asyncio.wait_for(waiting, timeout=10))
    finally:
        await retiring.close()

    assert result == {"ok": True, "source_gone": True}
