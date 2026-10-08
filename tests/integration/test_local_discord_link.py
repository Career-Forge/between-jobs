"""Linking a Discord account to a web account against a LOCAL Supabase stack: the real SQL.

`consume_link_code` used to refuse every channel but Telegram, and the hijack guard inside
`merge_user_data` (reached again by `finish_link_merge` on a resumed link) read Telegram's
`app_metadata` keys, so a Discord bot-only account could not have been merged even if the first
refusal were lifted. These tests pin what the Discord migration made true, with the same weight as
the Telegram link tests (test_local_link_merge.py): the link itself, that a code only ever works on
the channel it was minted for, that the protections against taking over somebody else's account
are as strong, and that the merge treats every row the same whichever channel it came through.

Run with `pytest -m local_supabase` after `supabase start` and `supabase db reset --local`."""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg
import pytest
from postgrest.exceptions import APIError

from supabase import acreate_client

from .conftest import World

pytestmark = pytest.mark.local_supabase


async def _owner_of(world: World, channel: str, subject: str) -> str | None:
    rows = (
        await world.sb.table("channel_identities")
        .select("user_id")
        .eq("channel", channel)
        .eq("external_subject", subject)
        .execute()
    ).data
    return str(rows[0]["user_id"]) if rows else None


async def _code_row(world: World, user_id: str, channel: str) -> dict[str, Any]:
    rows = (
        await world.sb.table("link_codes")
        .select("*")
        .eq("user_id", user_id)
        .eq("channel", channel)
        .execute()
    ).data
    assert len(rows) == 1
    return dict(rows[0])


async def _failed_attempts(world: World, channel: str, subject: str) -> int:
    rows = (
        await world.sb.table("link_code_attempts")
        .select("failed_count")
        .eq("channel", channel)
        .eq("external_subject", subject)
        .execute()
    ).data
    return int(rows[0]["failed_count"]) if rows else 0


async def _with_data(world: World, user_id: str) -> None:
    """The kinds of row a person accumulates before linking: a profile with a fact, a credential,
    a saved search, a tracked job."""
    pv = await world.profile_version(user_id)
    await world.fact(user_id, pv["id"], pointer="skills.0")
    await (
        world.sb.table("provider_credentials")
        .insert(
            {
                "user_id": user_id,
                "service": "llm",
                "provider": "openrouter",
                "secret_encrypted": "e",
            }
        )
        .execute()
    )
    await world.sb.table("saved_searches").insert({"user_id": user_id, "query": "ml"}).execute()
    await world.application(user_id, await world.job())


# -- the link ----------------------------------------------------------------------------------


async def test_a_discord_code_links_a_discord_account_and_the_source_is_retired(
    world: World,
) -> None:
    src = await world.discord_user()
    target = await world.web_user()
    await _with_data(world, src.id)
    code = await world.mint(target, channel="discord")

    result = await world.link(src.subject, code, src.id, channel="discord")

    assert result["ok"] is True
    assert (result["target_user_id"], result["source_user_id"]) == (target, src.id)
    summary = result["summary"]
    assert summary["profile_versions"] == 1 and summary["career_facts"] == 1
    assert summary["provider_credentials"] == 1 and summary["saved_searches"] == 1
    assert summary["applications"] == 1 and summary["channel_identities"] == 1
    assert await _owner_of(world, "discord", src.subject) == target
    consumed = await _code_row(world, target, "discord")
    assert consumed["consumed_at"] is not None
    assert (consumed["consumed_by_user_id"], consumed["consumed_by_subject"]) == (
        src.id,
        src.subject,
    )

    completion = await world.finish(src.id, target, src.subject, channel="discord")

    assert completion.retired is True and completion.leftover == {}
    assert not await world.user_exists(src.id)  # the emptied bot-only account is gone
    assert await world.user_exists(target)


async def test_the_merge_does_the_same_to_every_row_whichever_channel_the_link_came_through(
    world: World,
) -> None:
    """Proof that the merge itself is channel-blind: one set of data, linked once as a Telegram
    account and once as a Discord account, and the answers are identical."""
    summaries: dict[str, dict[str, Any]] = {}
    drained: dict[str, dict[str, int]] = {}
    for channel in ("telegram", "discord"):
        src = await (world.telegram_user() if channel == "telegram" else world.discord_user())
        target = await world.web_user()
        await _with_data(world, src.id)
        await _with_data(world, target)  # collisions to resolve: credential, profile, saved search
        code = await world.mint(target, channel=channel)

        result = await world.link(src.subject, code, src.id, channel=channel)

        assert result["ok"] is True
        summaries[channel] = result["summary"]
        counts = await world.counts(src.id)
        counts.pop("artifact_versions.storage_key", None)
        counts.pop("storage.objects", None)
        drained[channel] = counts
    assert summaries["telegram"] == summaries["discord"]
    assert drained["telegram"] == drained["discord"] == {}


async def test_the_same_code_sent_again_after_the_link_committed_resumes_it(world: World) -> None:
    src = await world.discord_user()
    target = await world.web_user()
    code = await world.mint(target, channel="discord")
    first = await world.link(src.subject, code, src.id, channel="discord")
    assert first["ok"] is True

    again = await world.link(src.subject, code, target, channel="discord")  # now the web user's

    assert again["ok"] is True and again.get("resumed") is True
    assert (again["target_user_id"], again["source_user_id"]) == (target, src.id)


async def test_a_web_account_may_link_one_account_on_each_channel(world: World) -> None:
    target = await world.web_user()
    tg = await world.telegram_user()
    dc = await world.discord_user()

    one = await world.link(tg.subject, await world.mint(target), tg.id)
    two = await world.link(
        dc.subject, await world.mint(target, channel="discord"), dc.id, channel="discord"
    )

    assert one["ok"] is True and two["ok"] is True
    assert await _owner_of(world, "telegram", tg.subject) == target
    assert await _owner_of(world, "discord", dc.subject) == target


async def test_a_web_account_with_a_discord_account_already_refuses_a_second(world: World) -> None:
    src = await world.discord_user()
    target = await world.web_user()
    await world.identity(target, "discord", f"7{uuid.uuid4().int % 10**17:017d}")

    result = await world.link(
        src.subject, await world.mint(target, channel="discord"), src.id, channel="discord"
    )

    assert result == {"ok": False, "reason": "target_linked_elsewhere"}
    assert await _owner_of(world, "discord", src.subject) == src.id  # nothing moved


async def test_a_channel_with_no_link_support_is_still_refused(world: World) -> None:
    src = await world.discord_user()
    with pytest.raises(APIError) as refused:
        await world.link(src.subject, "ABCD2345", src.id, channel="slack")
    assert refused.value.code == "BJ005"


# -- a code only ever works on the channel it was minted for ----------------------------------


async def test_a_telegram_code_presented_from_discord_is_refused_as_a_wrong_code_is(
    world: World,
) -> None:
    src = await world.discord_user()
    target = await world.web_user()
    telegram_code = await world.mint(target, channel="telegram")

    cross = await world.link(src.subject, telegram_code, src.id, channel="discord")
    wrong = await world.link(src.subject, "ZZZZ9999", src.id, channel="discord")

    assert cross == wrong == {"ok": False, "reason": "invalid_code"}
    # Nothing was consumed or moved, and the failed attempt was counted against the Discord sender.
    assert (await _code_row(world, target, "telegram"))["consumed_at"] is None
    assert await _owner_of(world, "discord", src.subject) == src.id
    assert await _failed_attempts(world, "discord", src.subject) == 2
    assert await _failed_attempts(world, "telegram", src.subject) == 0


async def test_a_discord_code_presented_from_telegram_is_refused_as_a_wrong_code_is(
    world: World,
) -> None:
    src = await world.telegram_user()
    target = await world.web_user()
    discord_code = await world.mint(target, channel="discord")

    cross = await world.link(src.subject, discord_code, src.id, channel="telegram")
    wrong = await world.link(src.subject, "ZZZZ9999", src.id, channel="telegram")

    assert cross == wrong == {"ok": False, "reason": "invalid_code"}
    assert (await _code_row(world, target, "discord"))["consumed_at"] is None
    assert await _owner_of(world, "telegram", src.subject) == src.id


async def test_a_refused_cross_channel_code_still_works_on_its_own_channel(world: World) -> None:
    dc = await world.discord_user()
    tg = await world.telegram_user()
    target = await world.web_user()
    code = await world.mint(target, channel="discord")

    assert (await world.link(tg.subject, code, tg.id, channel="telegram"))["ok"] is False
    assert (await world.link(dc.subject, code, dc.id, channel="discord"))["ok"] is True


async def test_the_same_code_text_on_two_channels_redeems_only_the_senders_own(
    world: World,
) -> None:
    """Code texts are random, but nothing in the database forbids two channels' codes being equal
    (the index is on the hash alone). Each is redeemed on its own channel and never the other."""
    dc = await world.discord_user()
    web_for_discord = await world.web_user()
    web_for_telegram = await world.web_user()
    shared = "SHARED77"
    import hashlib
    from datetime import UTC, datetime, timedelta

    for user_id, channel in ((web_for_discord, "discord"), (web_for_telegram, "telegram")):
        await (
            world.sb.table("link_codes")
            .insert(
                {
                    "user_id": user_id,
                    "channel": channel,
                    "code_hash": hashlib.sha256(shared.encode()).hexdigest(),
                    "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                }
            )
            .execute()
        )

    result = await world.link(dc.subject, shared, dc.id, channel="discord")

    assert result["ok"] is True and result["target_user_id"] == web_for_discord
    assert (await _code_row(world, web_for_telegram, "telegram"))["consumed_at"] is None


async def test_the_lockout_is_per_channel_and_per_sender(world: World) -> None:
    dc = await world.discord_user()
    target = await world.web_user()
    for _ in range(5):
        assert (await world.link(dc.subject, "WRONG000", dc.id, channel="discord"))["reason"] in {
            "invalid_code",
            "rate_limited",
        }

    locked = await world.link(
        dc.subject, await world.mint(target, channel="discord"), dc.id, channel="discord"
    )

    assert locked["ok"] is False and locked["reason"] == "rate_limited"
    # The lock is on (discord, this sender): another Discord sender is unaffected.
    other = await world.discord_user()
    ok = await world.link(
        other.subject, await world.mint(target, channel="discord"), other.id, channel="discord"
    )
    assert ok["ok"] is True


async def test_an_expired_discord_code_is_expired_and_a_code_is_single_use(world: World) -> None:
    from datetime import timedelta

    src = await world.discord_user()
    target = await world.web_user()
    expired = await world.mint(target, expires_in=timedelta(minutes=-1), channel="discord")
    assert await world.link(src.subject, expired, src.id, channel="discord") == {
        "ok": False,
        "reason": "expired_code",
    }
    fresh = await world.mint(target, channel="discord")
    assert (await world.link(src.subject, fresh, src.id, channel="discord"))["ok"] is True
    other = await world.discord_user()
    assert await world.link(other.subject, fresh, other.id, channel="discord") == {
        "ok": False,
        "reason": "invalid_code",
    }


# -- the protections against taking over somebody else's account ----------------------------------


async def test_a_linked_discord_account_cannot_be_merged_away_and_the_code_is_burned(
    world: World,
) -> None:
    victim = await world.web_user()  # a real web account that has its own Discord identity
    subject = f"7{uuid.uuid4().int % 10**17:017d}"
    world.subjects.append(subject)
    await world.identity(victim, "discord", subject)
    await world.sb.table("saved_searches").insert({"user_id": victim, "query": "mine"}).execute()
    attacker_target = await world.web_user()
    code = await world.mint(attacker_target, channel="discord")

    result = await world.link(subject, code, victim, channel="discord")

    assert result == {"ok": False, "reason": "source_already_linked"}
    assert (await _code_row(world, attacker_target, "discord"))["consumed_at"] is not None
    assert await world.user_exists(victim)
    assert (await world.counts(victim)).get("public.saved_searches") == 1


async def test_markers_a_web_user_can_write_do_not_make_it_a_discord_account(world: World) -> None:
    """Only app_metadata, which the service role alone writes, counts. user_metadata is editable
    by the user."""
    subject = f"7{uuid.uuid4().int % 10**17:017d}"
    world.subjects.append(subject)
    created = await world.sb.auth.admin.create_user(
        {
            "email": f"spoof-{uuid.uuid4().hex}@example.com",
            "password": uuid.uuid4().hex,
            "email_confirm": True,
            "user_metadata": {"bj_provisioned_by": "discord", "bj_discord_subject": subject},
        }
    )
    spoofer = created.user.id
    world.users.append(spoofer)
    await world.identity(spoofer, "discord", subject)
    target = await world.web_user()

    result = await world.link(
        subject, await world.mint(target, channel="discord"), spoofer, channel="discord"
    )

    assert result == {"ok": False, "reason": "source_already_linked"}


async def _provisioned(world: World, metadata: dict[str, str]) -> str:
    created = await world.sb.auth.admin.create_user(
        {
            "email": f"made-{uuid.uuid4().hex}@users.between-jobs.tech",
            "email_confirm": True,
            "app_metadata": metadata,
        }
    )
    user_id = str(created.user.id)
    world.users.append(user_id)
    return user_id


@pytest.mark.parametrize(
    ("kind", "metadata"),
    [
        (
            "another channel's account",
            {"bj_provisioned_by": "telegram", "bj_telegram_subject": "S"},
        ),
        (
            "another sender's account",
            {"bj_provisioned_by": "discord", "bj_discord_subject": "OTHER"},
        ),
        ("no subject recorded", {"bj_provisioned_by": "discord"}),
        (
            "the wrong key for its channel",
            {"bj_provisioned_by": "discord", "bj_telegram_subject": "S"},
        ),
        ("a made-up channel", {"bj_provisioned_by": "slack", "bj_slack_subject": "S"}),
    ],
)
async def test_only_an_account_made_for_this_exact_sender_on_this_exact_channel_may_be_merged(
    world: World, kind: str, metadata: dict[str, str]
) -> None:
    subject = f"7{uuid.uuid4().int % 10**17:017d}"
    world.subjects.append(subject)
    metadata = {k: (subject if v == "S" else v) for k, v in metadata.items()}
    source = await _provisioned(world, metadata)
    await world.identity(source, "discord", subject)
    target = await world.web_user()

    result = await world.link(
        subject, await world.mint(target, channel="discord"), source, channel="discord"
    )

    assert result == {"ok": False, "reason": "source_already_linked"}, kind
    assert await _owner_of(world, "discord", subject) == source


async def test_a_discord_account_cannot_be_merged_through_a_telegram_code(world: World) -> None:
    """The guard names the channel: a bot-only DISCORD account that somehow also holds a Telegram
    identity row is not a Telegram bot-only account, so a Telegram link cannot merge it away."""
    src = await world.discord_user()
    tg_subject = f"9{uuid.uuid4().int % 10**11:011d}"
    world.subjects.append(tg_subject)
    await world.identity(src.id, "telegram", tg_subject)
    target = await world.web_user()

    result = await world.link(tg_subject, await world.mint(target), src.id, channel="telegram")

    assert result == {"ok": False, "reason": "source_already_linked"}


async def test_a_sender_who_is_not_the_identity_owner_is_refused(world: World) -> None:
    src = await world.discord_user()
    stranger = await world.discord_user()
    target = await world.web_user()

    result = await world.link(
        src.subject, await world.mint(target, channel="discord"), stranger.id, channel="discord"
    )

    assert result == {"ok": False, "reason": "source_mismatch"}


async def test_merge_user_data_itself_keeps_the_guard_and_names_the_channel(world: World) -> None:
    """The copy of the check inside the merge function, which a resumed link reaches through
    finish_link_merge and which no caller can skip."""
    src = await world.discord_user()
    target = await world.web_user()
    pg: Any = world.pg

    async def merge(channel: str, subject: str) -> Any:
        return await pg.fetchval(
            "select public.merge_user_data($1::uuid, $2::uuid, $3, $4)",
            uuid.UUID(src.id),
            uuid.UUID(target),
            channel,
            subject,
        )

    for channel, subject in (
        ("telegram", src.subject),  # the right sender, the wrong channel
        ("discord", "somebody-else"),  # the right channel, the wrong sender
    ):
        with pytest.raises(asyncpg.PostgresError) as refused:
            await merge(channel, subject)
        assert refused.value.sqlstate == "BJ004"

    summary = await merge("discord", src.subject)
    assert isinstance(summary, str) and "channel_identities" in summary


async def test_finish_link_names_the_channel_it_finishes(world: World) -> None:
    src = await world.discord_user()
    target = await world.web_user()
    code = await world.mint(target, channel="discord")
    await world.link(src.subject, code, src.id, channel="discord")

    with pytest.raises(APIError) as wrong_channel:
        await world.sb.rpc(
            "finish_link_merge",
            {
                "p_channel": "telegram",
                "p_subject": src.subject,
                "p_source": src.id,
                "p_target": target,
            },
        ).execute()
    assert wrong_channel.value.code == "BJ006"  # not the confirmed target of a Telegram link

    merged = await world.sb.rpc(
        "finish_link_merge",
        {
            "p_channel": "discord",
            "p_subject": src.subject,
            "p_source": src.id,
            "p_target": target,
        },
    ).execute()
    assert merged.data["ok"] is True


# -- what is gone and who can call what ---------------------------------------------------------


async def test_the_telegram_only_functions_are_gone(world: World) -> None:
    pg: Any = world.pg
    assert not await pg.fetchval(
        "select exists (select 1 from pg_proc where proname = 'is_auto_provisioned_telegram_user')"
    )
    arities = {
        row["pronargs"]
        for row in await pg.fetch("select pronargs from pg_proc where proname = 'merge_user_data'")
    }
    assert arities == {4}


async def test_only_the_backend_can_reach_the_new_link_functions(world: World) -> None:
    stack = world.stack
    anon = await acreate_client(stack["API_URL"], stack["ANON_KEY"])
    password = uuid.uuid4().hex
    email = f"authn-{uuid.uuid4().hex}@example.com"
    await world.web_user(password=password, email=email)
    signed_in = await acreate_client(stack["API_URL"], stack["ANON_KEY"])
    await signed_in.auth.sign_in_with_password({"email": email, "password": password})
    user = str(uuid.uuid4())
    calls: dict[str, dict[str, Any]] = {
        "merge_user_data": {
            "p_source_user_id": user,
            "p_target_user_id": user,
            "p_channel": "discord",
            "p_subject": "x",
        },
        "is_auto_provisioned_channel_user": {
            "p_user_id": user,
            "p_channel": "discord",
            "p_subject": "x",
        },
    }
    for name, params in calls.items():
        for client in (anon, signed_in, world.sb):
            with pytest.raises(APIError) as refused:
                await client.rpc(name, params).execute()
            assert refused.value.code in {"42501", "PGRST202"}, name


# -- account deletion reaches a Discord identity's traces -----------------------------------------


async def test_deleting_an_account_forgets_its_discord_lockout_counter_and_identity(
    world: World,
) -> None:
    """The counters are keyed by (channel, subject), not by the user, so deleting the user does
    not reach them by itself: the deletion's own step must, for Discord as for Telegram, and must
    leave the same id on another channel alone."""
    from between_jobs.api import account_deletion

    user = await world.web_user()
    subject = f"7{uuid.uuid4().int % 10**17:017d}"
    world.subjects.append(subject)
    await world.identity(user, "discord", subject)
    for channel in ("discord", "telegram"):  # the same text on two channels
        await (
            world.sb.table("link_code_attempts")
            .insert({"channel": channel, "external_subject": subject, "failed_count": 2})
            .execute()
        )

    purged = await account_deletion._forget_link_attempts(world.sb, user)

    assert purged == 1
    assert await _failed_attempts(world, "discord", subject) == 0
    assert await _failed_attempts(world, "telegram", subject) == 2  # not this person's identity

    await world.sb.auth.admin.delete_user(user)
    assert await _owner_of(world, "discord", subject) is None  # the identity went with the user
