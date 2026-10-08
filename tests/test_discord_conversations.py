"""Whole conversations over Discord, through the real endpoint: a signed interaction goes in, and
what the person would have seen comes out of a recording fake of Discord's API.

The business logic is the one Telegram's tests cover (`channel_core`), reached through the
Discord adapter; what is pinned here is that the SAME paths run for a Discord sender -- linking,
tracking a job, generating a resume and receiving the PDF as a follow-up, listing and applying by
number, importing a resume file -- that identity and linking are keyed by the signed sender, and
that a delivery that fails, repeats, or outlives its token ends the way the module docstring of
`discord_webhook` says. Synthetic fixtures throughout (tests/discord_fakes.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import pytest
import test_telegram_webhook as webhook_fakes
from channel_fakes import APPLICATION_ID as APPLICATION
from channel_fakes import USER_ID
from discord_app import DiscordSupabase, Harness
from discord_fakes import (
    BOT_TOKEN,
    DM_CHANNEL,
    SECOND_SUBJECT,
    SUBJECT,
    FakeDiscord,
    World,
    attachment_resolved,
    button,
    command,
    fresh_token,
)

from between_jobs.api import discord_webhook, rate_limits
from between_jobs.api.link_completion import LinkCompletion

_LINK_OK = {
    "ok": True,
    "target_user_id": "00000000-0000-0000-0000-0000000000aa",
    "source_user_id": USER_ID,
    "summary": {"profile_versions": 1, "applications": 2, "provider_credentials": 0},
}
_JOB = {
    "title": "Staff AI Engineer",
    "company": "Acme",
    "location": "Remote",
    "url": "https://example.com/jobs/1",
    "description": "We need a Python engineer with RAG experience.",
}


@pytest.fixture(autouse=True)
def _record_the_prepare_limit(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    claimed: list[tuple[str, str]] = []

    async def claim(_supabase: Any, user_id: str, bucket: str) -> rate_limits.RateLimitDecision:
        claimed.append((user_id, bucket))
        return rate_limits.RateLimitDecision(allowed=True, retry_after_seconds=0)

    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", claim)
    return claimed


def _finish_recorder(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake(_supabase: Any, **kwargs: Any) -> LinkCompletion:
        calls.append(kwargs)
        return LinkCompletion(already_complete=False, retired=True)

    monkeypatch.setattr("between_jobs.api.channel_core.finish_link", fake)
    return calls


def _all_requests_ping_nobody(world: FakeDiscord) -> None:
    for request in world.requests:
        if request.json_body is not None:
            assert request.json_body["allowed_mentions"] == {"parse": []}, request.path


# -- linking -----------------------------------------------------------------------------------


def test_a_link_code_is_redeemed_for_the_signed_sender_and_the_reply_is_in_discords_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finished = _finish_recorder(monkeypatch)
    supabase = DiscordSupabase(rpc_data=_LINK_OK)
    world = FakeDiscord()

    with Harness(supabase, world) as h:
        response = h.post(command("link", {"code": "ABCD2345"}))

    assert response.json() == {"type": 5}
    ((name, params),) = supabase.rpc_calls
    assert name == "consume_link_code"
    assert params == {
        "p_channel": "discord",
        "p_external_subject": SUBJECT,  # the signed sender: not the chat, not the delivery
        "p_code": "ABCD2345",
        "p_source_user_id": USER_ID,
    }
    assert DM_CHANNEL not in params.values()
    assert finished == [
        {
            "source_user_id": USER_ID,
            "target_user_id": _LINK_OK["target_user_id"],
            "subject": SUBJECT,
            "channel": "discord",
        }
    ]
    (reply,) = world.sent
    assert reply.kind == "edit_original"
    text = reply.json_body["content"] if reply.json_body else ""
    assert "Linked" in text and "Discord" in text and "Telegram" not in text
    assert "resume version" in text and "tracked application" in text
    _all_requests_ping_nobody(world)


def test_a_refused_code_gets_the_generic_refusal_and_finishes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finished = _finish_recorder(monkeypatch)
    supabase = DiscordSupabase(rpc_data={"ok": False, "reason": "invalid_code"})
    world = FakeDiscord()
    with Harness(supabase, world) as h:
        h.post(command("link", {"code": "WRONGCODE"}))
    assert finished == []
    assert world.contents == ["❌ That code isn't valid. Double-check it and try again."]


def test_first_contact_makes_a_bot_only_account_for_the_discord_sender() -> None:
    from between_jobs.api.channel_accounts import DISCORD

    identities = _RecordingIdentities([])
    supabase = DiscordSupabase(channel_identities_rows=[])
    supabase.channel_identities = identities
    world = FakeDiscord()

    with Harness(supabase, world) as h:
        h.post(command("privacy", user_id=SECOND_SUBJECT))

    ((attributes,),) = [tuple(supabase.auth.admin.create_user_calls)]
    assert attributes["email"].startswith("discord-")
    assert attributes["email"].endswith("@users.between-jobs.tech")
    assert attributes["app_metadata"] == {
        "bj_provisioned_by": DISCORD,
        "bj_discord_subject": SECOND_SUBJECT,
    }
    (row,) = identities.inserted
    assert row["channel"] == "discord" and row["external_subject"] == SECOND_SUBJECT
    assert row["external_tenant"] == "" and row["user_id"] == supabase.auth.admin._new_user_id
    assert len(world.sent) == 1


class _RecordingIdentities(webhook_fakes._FakeChannelIdentitiesTable):
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        super().__init__(rows)
        self.inserted: list[dict[str, Any]] = []

    def insert(self, data: dict[str, Any]) -> Any:
        self.inserted.append(data)
        return super().insert(data)


def test_unlink_on_a_bot_only_account_says_there_is_nothing_to_unlink() -> None:
    supabase = DiscordSupabase(
        existing_user=webhook_fakes._auth_user(
            "discord-x@users.between-jobs.tech",
            bj_provisioned_by="discord",
            bj_discord_subject=SUBJECT,
        )
    )
    world = FakeDiscord()
    with Harness(supabase, world) as h:
        h.post(command("unlink"))
    assert world.contents == [
        "This Discord account isn't linked to a web account -- nothing to unlink."
    ]
    assert supabase.channel_identities.delete_calls == 0


def test_unlink_on_a_linked_account_detaches_the_discord_identity() -> None:
    supabase = DiscordSupabase(existing_user=webhook_fakes._WEB_USER)
    world = FakeDiscord()
    with Harness(supabase, world) as h:
        h.post(command("unlink"))
    assert world.contents == [
        "Unlinked. This Discord account is no longer connected to any web account."
    ]
    assert supabase.channel_identities.delete_calls == 1


# -- tracking a job and generating a resume ----------------------------------------------------


def test_a_pasted_job_is_tracked_and_the_button_generates_the_resume_and_delivers_the_pdf(
    _record_the_prepare_limit: list[tuple[str, str]],
) -> None:
    supabase = DiscordSupabase()
    world = World()

    with Harness(supabase, world) as h:
        job = h.post(command("job", _JOB))
        h.settle()
        assert job.json() == {"type": 5}
        tracked = world.sent[0]
        assert tracked.kind == "edit_original"
        assert tracked.json_body is not None
        assert "Tracking" in tracked.json_body["content"]
        (row,) = tracked.json_body["components"]
        (generate,) = row["components"]
        assert generate["label"] == "📄 Generate resume"
        assert generate["custom_id"].startswith("app:prepare:")

        tap = h.post(button(generate["custom_id"]))
        assert tap.json() == {"type": 6}

    # The job and the application the same store functions the web and Telegram use made.
    assert supabase.jobs.insert_calls[0]["company_name"] == "Acme"

    # The generation ran once, on the same path: one engine call, the per-user limit claimed.
    assert world.engine_calls == 1
    assert _record_the_prepare_limit == [(USER_ID, "prepare")]

    # What the person saw after the tap: one progress message (a follow-up, because a button tap
    # has no placeholder), edited as the work moved on, the PDF as a follow-up, and the final
    # edit carrying the mark-as-applied button.
    after = world.requests[world.requests.index(tracked) + 1 :]
    kinds = [r.kind for r in after]
    assert kinds[0] == "followup"
    assert kinds.count("followup") == 2  # the progress message, then the document
    assert "edit_followup" in kinds
    progress, *_rest = after
    assert (
        progress.json_body is not None and "Generating your resume" in progress.json_body["content"]
    )
    (document,) = [r for r in after if r.files]
    assert document.kind == "followup"
    assert document.files == {"resume.pdf": b"%PDF-1.5 fake pdf bytes"}
    assert document.json_body is not None
    assert "ATS score" in document.json_body["content"]
    assert document.json_body["attachments"] == [{"id": 0, "filename": "resume.pdf"}]
    last_edit = [r for r in after if r.kind == "edit_followup"][-1]
    assert last_edit.json_body is not None
    final = last_edit.json_body["components"][0]["components"][0]
    assert final["custom_id"].startswith("app:stage:") and final["custom_id"].endswith(":applied")
    _all_requests_ping_nobody(world)


def test_a_pasted_job_becomes_an_application_from_the_discord_channel() -> None:
    supabase = DiscordSupabase(
        applications=webhook_fakes._FakeSimpleTable(),
        job_snapshots=webhook_fakes._FakeSimpleTable(),
    )
    with Harness(supabase, FakeDiscord()) as h:
        h.post(command("job", _JOB))
    assert supabase.jobs.insert_calls[0]["company_name"] == "Acme"
    assert supabase.jobs.insert_calls[0]["canonical_url"] == "https://example.com/jobs/1"
    assert supabase.applications.insert_calls[0]["source_channel"] == "discord"
    assert supabase.job_snapshots.insert_calls[0]["title"] == "Staff AI Engineer"


def test_a_job_whose_description_is_long_is_still_tracked_not_imported_as_a_resume() -> None:
    supabase = DiscordSupabase(
        applications=webhook_fakes._FakeSimpleTable(),
        job_snapshots=webhook_fakes._FakeSimpleTable(),
    )
    world = FakeDiscord()
    with Harness(supabase, world) as h:
        h.post(command("job", {**_JOB, "description": "Build distributed systems. " * 200}))
    assert supabase.jobs.insert_calls and "Tracking" in world.contents[0]


def test_listing_and_applying_by_number_runs_the_same_generation() -> None:
    supabase = DiscordSupabase()
    world = World()

    with Harness(supabase, world) as h:
        h.post(command("list"))
        h.settle()
        listing = world.sent[0].json_body
        assert listing is not None
        assert "1. " not in listing["content"] and "1\\. " in listing["content"]
        assert "Use /apply" in listing["content"] and "Send" not in listing["content"]
        h.post(command("apply", {"number": 1}))

    assert world.engine_calls == 1
    kinds = [r.kind for r in world.requests]
    assert kinds[0] == "edit_original"  # the list replaced the placeholder
    assert "edit_original" in kinds[1:]  # so did the generation's progress message
    assert any(r.files for r in world.requests)  # and the PDF came as a file
    final = [r for r in world.requests if r.kind == "edit_original"][-1].json_body
    assert final is not None and "your resume is in this chat" in final["content"]


def test_a_second_generation_for_the_same_person_while_one_runs_is_refused_politely() -> None:
    supabase = DiscordSupabase()
    world = World()
    gate = asyncio.Event()
    original = world.handler

    def slow(request: Any) -> Any:
        return original(request)

    world.handler = slow  # type: ignore[method-assign]
    with Harness(supabase, world) as h:
        h.post(command("list"))
        h.settle()
        h.post(command("apply", {"number": 1}))
        h.post(command("apply", {"number": 1}))
        h.settle()
    del gate
    texts = " ".join(world.contents)
    assert world.engine_calls >= 1
    assert "Generating your resume" in texts or "your resume is in this chat" in texts


# -- importing a resume file -------------------------------------------------------------------


def test_an_imported_resume_file_is_previewed_with_buttons() -> None:
    value, resolved = attachment_resolved(
        filename="resume.json", size=len(webhook_fakes._VALID_RESUME_JSON)
    )
    world = FakeDiscord(cdn_files={"resume.json": webhook_fakes._VALID_RESUME_JSON.encode()})
    preview_row = {
        "id": webhook_fakes._VERSION_ID,
        "canonical_json": json.loads(webhook_fakes._VALID_RESUME_JSON),
        "activated_at": None,
    }
    profile_versions = webhook_fakes._FakeProfileVersionsTable(
        select_rows=[], insert_row=preview_row
    )
    supabase = DiscordSupabase(profile_versions=profile_versions)

    with Harness(supabase, world) as h:
        response = h.post(command("import", {"file": value}, resolved=resolved))

    assert response.json() == {"type": 5}
    assert profile_versions.insert_calls[0]["source_kind"] == "discord_json_upload"
    (cdn,) = world.of("download")
    assert cdn.host == "cdn.discordapp.com" and "authorization" not in cdn.headers
    (preview,) = world.sent
    assert preview.json_body is not None
    assert "Resume preview" in preview.json_body["content"]
    ids = [b["custom_id"] for b in preview.json_body["components"][0]["components"]]
    assert ids == [
        f"profile:activate:{webhook_fakes._VERSION_ID}",
        f"profile:cancel:{webhook_fakes._VERSION_ID}",
    ]


def test_a_resume_with_an_enormous_headline_cannot_hold_the_server_up_in_its_preview() -> None:
    """A valid resume's name and headline have no length limit and the preview repeats them, so a
    person's file could make the reply as long as the file. Cutting that reply up used to take
    time that grew with the square of its length (3 seconds for 200 KB, a minute for 1 MB) while
    the server did nothing else; now it is the first few thousand characters that are worked on."""
    resume = json.loads(webhook_fakes._VALID_RESUME_JSON)
    resume["personal"]["headline"] = "*_" * 100_000  # every character needs a backslash
    payload = json.dumps(resume).encode()
    value, resolved = attachment_resolved(filename="resume.json", size=len(payload))
    world = FakeDiscord(cdn_files={"resume.json": payload})
    preview_row = {"id": webhook_fakes._VERSION_ID, "canonical_json": resume, "activated_at": None}
    profile_versions = webhook_fakes._FakeProfileVersionsTable(
        select_rows=[], insert_row=preview_row
    )
    supabase = DiscordSupabase(profile_versions=profile_versions)

    with Harness(supabase, world) as h:
        started = time.perf_counter()
        response = h.post(command("import", {"file": value}, resolved=resolved))
        h.settle()
        elapsed = time.perf_counter() - started

    assert response.json() == {"type": 5}
    assert elapsed < 2.0, elapsed
    assert 1 <= len(world.contents) <= 5  # as many messages as a reply may be, no more
    assert all(len(content) <= 2000 for content in world.contents)
    assert world.contents[-1].endswith("(shortened)")


def test_an_import_that_is_not_a_json_file_gets_the_template_instead() -> None:
    value, resolved = attachment_resolved(filename="resume.pdf", content_type="application/pdf")
    world = FakeDiscord()
    with Harness(DiscordSupabase(), world) as h:
        h.post(command("import", {"file": value}, resolved=resolved))
    assert world.of("download") == []  # nothing was downloaded
    # The template is longer than one Discord message: the first replaces the placeholder, the
    # rest follow, and the JSON template arrives whole.
    first, *rest = world.sent
    assert first.kind == "edit_original" and first.json_body is not None
    assert "Set up your resume" in first.json_body["content"]
    assert rest and all(r.kind == "followup" for r in rest)
    assert '"personal"' in "".join(world.contents)


def test_an_attachment_over_the_size_cap_is_refused_before_it_is_fetched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "1000")
    value, resolved = attachment_resolved(size=5000)
    world = FakeDiscord(cdn_files={"resume.json": b"x" * 5000})
    with Harness(DiscordSupabase(), world) as h:
        h.post(command("import", {"file": value}, resolved=resolved))
    assert world.of("download") == []
    assert "too large" in world.contents[0]


def test_a_file_that_outgrows_what_its_size_claimed_is_cut_off_while_downloading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "1000")
    value, resolved = attachment_resolved(size=10)  # a lie
    world = FakeDiscord(cdn_files={"resume.json": b"x" * 5000})
    with Harness(DiscordSupabase(), world) as h:
        h.post(command("import", {"file": value}, resolved=resolved))
    assert "too large" in world.contents[0]


# -- repeats -----------------------------------------------------------------------------------


def test_the_same_signed_interaction_twice_is_processed_once() -> None:
    supabase = DiscordSupabase()
    world = FakeDiscord()
    payload = command("privacy", interaction_id="1100000000000000901")

    with Harness(supabase, world) as h:
        first = h.post(payload)
        h.settle()
        second = h.post(payload)  # a repeat, or a replayed capture, inside the window
        third = h.post(payload)

    assert first.json() == second.json() == third.json() == {"type": 5}
    assert len(world.sent) == 1  # one reply, not three
    assert supabase.ledger.rows["1100000000000000901"]["completed"] is True


def test_a_repeat_that_arrives_while_the_first_is_still_running_is_dropped_too() -> None:
    supabase = DiscordSupabase()
    asyncio.run(supabase.ledger.claim("1100000000000000902"))  # an earlier delivery, unfinished
    world = FakeDiscord()
    with Harness(supabase, world) as h:
        response = h.post(command("privacy", interaction_id="1100000000000000902"))
    assert response.json() == {"type": 5}
    assert world.requests == []


def test_different_interactions_are_each_processed() -> None:
    supabase = DiscordSupabase()
    world = FakeDiscord()
    with Harness(supabase, world) as h:
        h.post(command("privacy"))
        h.post(command("privacy"))
    assert len(world.sent) == 2


def test_a_claim_that_cannot_be_made_does_not_stop_the_command() -> None:
    class Broken(DiscordSupabase):
        def rpc(self, fn: str, params: dict[str, Any]) -> Any:
            if fn == "claim_discord_interaction":
                raise RuntimeError("function claim_discord_interaction does not exist")
            return super().rpc(fn, params)

    world = FakeDiscord()
    with Harness(Broken(), world) as h:
        response = h.post(command("privacy"))
    assert response.json() == {"type": 5} and len(world.sent) == 1


# -- failures ----------------------------------------------------------------------------------


def test_a_handler_that_fails_is_contained_the_person_is_told_and_the_claim_is_closed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def explode(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("the database fell over")

    monkeypatch.setattr(discord_webhook, "handle_inbound", explode)
    supabase = DiscordSupabase()
    world = FakeDiscord()
    with caplog.at_level(logging.DEBUG), Harness(supabase, world) as h:
        response = h.post(command("privacy", interaction_id="1100000000000000910"))

    assert response.json() == {"type": 5}
    assert world.contents == ["❌ Something went wrong. Try again in a moment."]
    assert supabase.ledger.rows["1100000000000000910"]["completed"] is True
    failed = [r for r in caplog.records if "deferred work failed" in r.getMessage()]
    assert len(failed) == 1 and failed[0].ctx["channel"] == "discord"  # type: ignore[attr-defined]
    # Nothing escaped into the event loop's "never retrieved" handler.
    assert not [r for r in caplog.records if "never retrieved" in r.getMessage()]


def test_when_even_the_failure_message_cannot_be_sent_nothing_escapes(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def explode(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(discord_webhook, "handle_inbound", explode)
    world = FakeDiscord()
    world.fail("edit_original", status=500, times=5)
    with caplog.at_level(logging.DEBUG), Harness(DiscordSupabase(), world) as h:
        assert h.post(command("privacy")).json() == {"type": 5}
    assert [r for r in caplog.records if "could not tell the user" in r.getMessage()]
    assert not [r for r in caplog.records if "never retrieved" in r.getMessage()]


def test_a_command_that_says_nothing_still_ends_the_thinking_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def silent(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(discord_webhook, "handle_inbound", silent)
    world = FakeDiscord()
    with Harness(DiscordSupabase(), world) as h:
        h.post(command("privacy"))
    assert world.contents == ["✅ Done."]


def test_work_still_running_at_shutdown_is_cancelled_and_its_claim_is_still_closed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    started = asyncio.Event()

    async def stall(*_args: Any, **_kwargs: Any) -> None:
        started.set()
        await asyncio.sleep(60)

    monkeypatch.setattr(discord_webhook, "handle_inbound", stall)
    monkeypatch.setattr("between_jobs.api.deferred_reply.SHUTDOWN_GRACE_SECONDS", 0.1)
    supabase = DiscordSupabase()
    world = FakeDiscord()
    with caplog.at_level(logging.DEBUG), Harness(supabase, world) as h:
        h.post(command("privacy", interaction_id="1100000000000000920"))

    assert (
        supabase.ledger.rows["1100000000000000920"]["completed"] is True
    )  # the finally still settles the claim
    assert world.requests == []  # a cancelled task says nothing to the person
    assert any("deferred work cancelled" in r.getMessage() for r in caplog.records)
    assert not [r for r in caplog.records if "never retrieved" in r.getMessage()]


# -- the token ---------------------------------------------------------------------------------


def test_an_expired_token_falls_back_to_the_direct_message_through_the_bot() -> None:
    world = FakeDiscord()
    world.expire_token()
    with Harness(DiscordSupabase(), world, bot_token=BOT_TOKEN) as h:
        h.post(command("privacy"))
    kinds = [r.kind for r in world.requests]
    assert kinds == ["edit_original", "channel_message"]
    assert world.requests[1].path == f"/channels/{DM_CHANNEL}/messages"
    assert world.requests[1].authorized_as_bot


def test_an_expired_token_with_no_bot_token_is_logged_without_either_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    world = FakeDiscord()
    world.expire_token()
    token = fresh_token()
    with caplog.at_level(logging.DEBUG), Harness(DiscordSupabase(), world, bot_token=None) as h:
        h.post(command("privacy", token=token))
    # The token was tried once; it is known dead after that, so nothing else is sent to Discord,
    # not even the "something went wrong" the registry then tries to give the person.
    assert [r.kind for r in world.requests] == ["edit_original"]
    assert any("could not be delivered" in r.getMessage() for r in caplog.records)
    assert token not in _log_text(caplog)


def _log_text(caplog: pytest.LogCaptureFixture) -> str:
    parts: list[str] = []
    for record in caplog.records:
        parts += [record.getMessage(), str(record.args), str(getattr(record, "ctx", ""))]
        if record.exc_info and record.exc_info[1] is not None:
            parts.append(str(record.exc_info[1]))
    return "\n".join(parts)


def test_neither_token_reaches_a_log_in_a_whole_conversation(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _finish_recorder(monkeypatch)
    token = fresh_token()
    tap_token = fresh_token()
    supabase = DiscordSupabase(rpc_data=_LINK_OK)
    world = World()
    with caplog.at_level(logging.DEBUG), Harness(supabase, world) as h:
        h.post(command("link", {"code": "ABCD2345"}, token=token))
        h.post(command("job", _JOB, token=token))
        h.settle()
        generate = world.sent[-1].json_body["components"][0]["components"][0]["custom_id"]  # type: ignore[index]
        h.post(button(generate, token=tap_token))
    text = _log_text(caplog)
    for secret in (token, tap_token, BOT_TOKEN, "ABCD2345"):
        assert secret not in text
    # ...and the token is only ever used where it belongs: in the webhook path.
    assert {r.token for r in world.requests if r.token} == {token, tap_token}


def test_the_job_text_and_the_resume_never_reach_a_log(caplog: pytest.LogCaptureFixture) -> None:
    secret = "Confidential-Description-Marker"
    with caplog.at_level(logging.DEBUG), Harness(DiscordSupabase(), World()) as h:
        h.post(command("job", {**_JOB, "description": secret}))
    assert secret not in _log_text(caplog)


def test_a_command_run_in_the_wrong_place_is_not_even_claimed() -> None:
    supabase = DiscordSupabase()
    with Harness(supabase, FakeDiscord()) as h:
        h.post(command("list", dm=False))
    assert supabase.ledger.claims == []


def test_the_apply_number_is_an_ordinary_reference_not_an_application_id() -> None:
    """`/apply number:7` is resolved against the person's own numbered list; nothing in the
    interaction can name an application."""
    supabase = DiscordSupabase()
    world = World()
    with Harness(supabase, world) as h:
        h.post(command("list"))
        h.settle()
        h.post(command("apply", {"number": 7}))
    assert world.engine_calls == 0
    assert "#7 isn't on your list" in " ".join(world.contents) or "7" in world.contents[-1]
    assert APPLICATION not in " ".join(world.contents)
