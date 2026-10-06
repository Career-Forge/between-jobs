"""The chat bot's business logic with no Telegram in sight (`channel_core`).

Every test here hands `handle_inbound` an `InboundMessage` built directly and reads what came
back as intents on a recording `Renderer` -- `Say` with a `RichText` and buttons,
`SendDocument`, `AckCallback`. That is the whole point of the extraction: a second channel
needs an adapter and a renderer, not a copy of this logic. What each message actually says on
Telegram, byte for byte, is pinned through the real endpoint in test_telegram_webhook.py and
test_telegram_golden_parity.py; the deferred resume generation through the endpoint is in
test_telegram_deferred_prepare.py."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
import pytest
import test_telegram_prepare_callback as prepare_fakes
import test_telegram_webhook as webhook_fakes
from channel_fakes import (
    APPLICATION_ID,
    CHAT_REF,
    USER_ID,
    ComposedSupabase,
    FakeRenderer,
    inbound,
    user_id_for,
)

from between_jobs.api import channel_core, rate_limits
from between_jobs.api import channel_messages as messages
from between_jobs.api.applications_store import change_stage
from between_jobs.api.channel_core import handle_inbound, is_slow_request
from between_jobs.api.channel_envelope import (
    AckCallback,
    Attachment,
    Button,
    MessageRef,
    RichText,
    Say,
    Segment,
    SendDocument,
)
from between_jobs.api.deferred_reply import DeferredReplies
from between_jobs.api.link_completion import LinkCompletion
from between_jobs.api.prepare_orchestrator import run_prepare_application

_VERSION_ID = webhook_fakes._VERSION_ID
_PREVIEW_ROW = {
    "id": _VERSION_ID,
    "canonical_json": json.loads(webhook_fakes._VALID_RESUME_JSON),
    "activated_at": None,
}


class _Engine(prepare_fakes._FakeHttpClient):
    def __init__(
        self,
        *,
        gate: asyncio.Event | None = None,
        fail: bool = False,
        apply_body: Any = None,
    ) -> None:
        super().__init__(apply_body=apply_body)
        self.gate = gate
        self.fail = fail

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        if url.endswith("/apply"):
            if self.gate is not None:
                await asyncio.wait_for(self.gate.wait(), timeout=10)
            if self.fail:
                raise RuntimeError("the engine fell over")
        return await super().post(url, **kwargs)


async def _handle(
    message: Any,
    *,
    supabase: ComposedSupabase | None = None,
    renderer: FakeRenderer | None = None,
    http: _Engine | None = None,
    registry: DeferredReplies | None = None,
) -> tuple[FakeRenderer, ComposedSupabase, DeferredReplies]:
    supabase = supabase or ComposedSupabase()
    renderer = renderer or FakeRenderer()
    registry = registry or DeferredReplies()
    await handle_inbound(
        supabase,  # type: ignore[arg-type]
        http or _Engine(),  # type: ignore[arg-type]
        renderer,
        message,
        deferred=registry,
    )
    return renderer, supabase, registry


@pytest.fixture(autouse=True)
def _stub_link_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ready(_supabase: Any) -> bool:
        return True

    monkeypatch.setattr("between_jobs.api.channel_core.link_schema_ready", ready)


# -- replies are envelopes -----------------------------------------------------------------


async def test_a_reply_goes_to_the_conversation_the_message_came_from() -> None:
    renderer, _supabase, _registry = await _handle(inbound("hi there"))

    assert renderer.sent == [(CHAT_REF, Say(RichText((Segment(messages.FALLBACK_TEXT),))))]


async def test_every_reply_is_a_say_carrying_rich_text() -> None:
    for text in ("hi", "set up my resume", "track a job", "list", "check my resume"):
        renderer, _supabase, _registry = await _handle(inbound(text))
        assert renderer.sent, text
        for _chat, intent in renderer.sent:
            assert isinstance(intent, Say)
            assert isinstance(intent.text, RichText)


async def test_the_setup_help_is_formatted_text_with_the_template_as_a_pre_block() -> None:
    renderer, _supabase, _registry = await _handle(inbound("set up my resume"))

    text = renderer.sent[0][1].text
    assert text == messages.SETUP_HELP_TEXT
    assert text.is_styled()
    pre_blocks = [s for s in text.segments if s.style == "pre"]
    assert pre_blocks == [Segment(messages.RESUME_TEMPLATE_JSON, "pre")]
    # the placeholders in the template are text, not markup, at this level
    assert "<Your Name>" in pre_blocks[0].text


async def test_a_tracked_job_names_the_job_in_bold_and_offers_a_generate_button() -> None:
    message = inbound("Title: <b>Boss</b>\nCompany: Smith & Co\n\nA description.")
    supabase = ComposedSupabase(applications=webhook_fakes._FakeSimpleTable(select_rows=[]))

    renderer, supabase, _registry = await _handle(message, supabase=supabase)

    _chat, say = renderer.sent[0]
    assert say.text == messages.job_tracked_text("<b>Boss</b>", "Smith & Co")
    # the title is a bold segment holding exactly what was typed: escaping is the renderer's
    assert Segment("<b>Boss</b>", "bold") in say.text.segments
    assert say.buttons == ((Button("📄 Generate resume", "app:prepare:generated-1"),),)
    assert supabase.applications.insert_calls[0]["source_channel"] == "telegram"


async def test_a_resume_preview_offers_confirm_and_cancel_in_one_row() -> None:
    profile_versions = webhook_fakes._FakeProfileVersionsTable(
        select_rows=[], insert_row=_PREVIEW_ROW
    )
    supabase = ComposedSupabase(profile_versions=profile_versions)

    renderer, _supabase, _registry = await _handle(
        inbound(webhook_fakes._VALID_RESUME_JSON), supabase=supabase
    )

    _chat, say = renderer.sent[0]
    assert "Jane Doe" in say.text.plain_text()
    assert say.buttons == (
        (
            Button("✅ Looks good -- save it", f"profile:activate:{_VERSION_ID}"),
            Button("❌ Cancel", f"profile:cancel:{_VERSION_ID}"),
        ),
    )
    assert profile_versions.insert_calls[0]["source_kind"] == "telegram_json_paste"


# -- attachments ---------------------------------------------------------------------------


def _json_attachment(size: int | None = 2000) -> Attachment:
    return Attachment("document", "resume.json", "application/json", size, "file-123")


async def test_a_json_upload_is_fetched_through_the_renderer_under_the_size_cap() -> None:
    profile_versions = webhook_fakes._FakeProfileVersionsTable(
        select_rows=[], insert_row=_PREVIEW_ROW
    )
    supabase = ComposedSupabase(profile_versions=profile_versions)
    renderer = FakeRenderer(attachment_bytes=webhook_fakes._VALID_RESUME_JSON.encode())
    attachment = _json_attachment()

    await _handle(inbound(attachment=attachment), supabase=supabase, renderer=renderer)

    assert renderer.fetches == [(attachment, 1_048_576)]  # the same cap a pasted profile gets
    assert profile_versions.insert_calls[0]["source_kind"] == "telegram_json_upload"
    assert "Jane Doe" in renderer.texts[0]


async def test_an_upload_that_says_it_is_over_the_cap_is_never_fetched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "4096")
    renderer = FakeRenderer(attachment_bytes=b"{}")

    await _handle(inbound(attachment=_json_attachment(4097)), renderer=renderer)

    assert renderer.fetches == []
    assert "too large to import" in renderer.texts[0]
    assert "(the limit is 4 KiB)" in renderer.texts[0]


async def test_an_upload_whose_declared_size_equals_the_cap_is_fetched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "4096")
    profile_versions = webhook_fakes._FakeProfileVersionsTable(
        select_rows=[], insert_row=_PREVIEW_ROW
    )
    supabase = ComposedSupabase(profile_versions=profile_versions)
    renderer = FakeRenderer(attachment_bytes=webhook_fakes._VALID_RESUME_JSON.encode())
    attachment = _json_attachment(4096)

    await _handle(inbound(attachment=attachment), supabase=supabase, renderer=renderer)

    assert renderer.fetches == [(attachment, 4096)]  # exactly the cap is allowed, as on the web
    assert "too large to import" not in renderer.texts[0]


async def test_an_upload_that_lies_about_its_size_is_refused_when_the_fetch_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "2000")
    renderer = FakeRenderer(attachment_too_large=True)

    await _handle(inbound(attachment=_json_attachment(None)), renderer=renderer)

    assert [limit for _a, limit in renderer.fetches] == [2000]
    assert "too large to import" in renderer.texts[0]


async def test_an_attachment_that_is_not_json_is_ignored_and_its_caption_is_the_message() -> None:
    renderer = FakeRenderer()
    picture = Attachment("document", "photo.png", "image/png", 10, "file-9")
    supabase = ComposedSupabase(applications=webhook_fakes._FakeSimpleTable(select_rows=[]))

    await _handle(inbound("list", attachment=picture), renderer=renderer, supabase=supabase)

    assert renderer.fetches == []
    assert renderer.texts == [messages.NO_APPLICATIONS_TEXT]


# -- identity, and what comes from where ---------------------------------------------------


async def test_a_link_code_from_a_group_is_refused_before_it_is_ever_submitted() -> None:
    renderer, supabase, _registry = await _handle(
        inbound("/link ABCD2345", is_private=False), supabase=ComposedSupabase()
    )

    assert renderer.texts == [messages.LINK_PRIVATE_ONLY_TEXT]
    assert supabase.rpc_calls == []


async def test_a_link_code_is_consumed_for_the_sender_not_for_the_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def finish(_supabase: Any, **_: Any) -> LinkCompletion:
        return LinkCompletion(already_complete=False, retired=True)

    monkeypatch.setattr("between_jobs.api.channel_core.finish_link", finish)
    supabase = ComposedSupabase(
        rpc_data={
            "ok": True,
            "target_user_id": "00000000-0000-0000-0000-0000000000aa",
            "source_user_id": USER_ID,
            "summary": {},
        }
    )

    await _handle(inbound("/link ABCD2345", subject="42"), supabase=supabase)

    ((name, params),) = supabase.rpc_calls
    assert name == "consume_link_code"
    assert params["p_channel"] == "telegram"
    assert params["p_external_subject"] == "42"  # who sent it; the chat id is another value
    assert params["p_source_user_id"] == USER_ID


async def test_a_channel_with_no_identity_provisioning_is_refused_before_anything_happens() -> None:
    """Identity is implemented for Telegram only. A message from another channel must not be
    filed under a Telegram identity, so it stops at the identity step -- nothing is said or
    written."""
    renderer = FakeRenderer()
    supabase = ComposedSupabase()

    with pytest.raises(ValueError, match="no identity provisioning for channel 'discord'"):
        await _handle(inbound("hi", channel="discord"), supabase=supabase, renderer=renderer)

    assert renderer.calls == []
    assert supabase.applications.insert_calls == []


# -- button taps ---------------------------------------------------------------------------


async def test_confirming_a_preview_replies_then_acknowledges_the_tap() -> None:
    profile_versions = webhook_fakes._FakeProfileVersionsTable(
        select_rows=[], update_row={"id": _VERSION_ID, "activated_at": "2026-08-06T00:00:00Z"}
    )
    supabase = ComposedSupabase(profile_versions=profile_versions)

    renderer, _supabase, _registry = await _handle(
        inbound(callback_data=f"profile:activate:{_VERSION_ID}"), supabase=supabase
    )

    assert renderer.texts == [messages.SAVED_TEXT]
    assert renderer.acks == [AckCallback("cbq-1")]
    assert renderer.calls == ["say", "ack"]


async def test_cancelling_a_preview_that_is_already_gone_only_acknowledges() -> None:
    supabase = ComposedSupabase(
        profile_versions=webhook_fakes._FakeProfileVersionsTable(select_rows=[])
    )

    renderer, _supabase, _registry = await _handle(
        inbound(callback_data=f"profile:cancel:{_VERSION_ID}"), supabase=supabase
    )

    assert renderer.calls == ["ack"]


async def test_cancelling_a_preview_that_was_already_confirmed_only_acknowledges() -> None:
    """A cancel tap that lost a race with the confirm: nothing is left to cancel, and the
    handler must not raise, or the channel would redeliver the tap for ever."""
    row = {"id": _VERSION_ID, "user_id": USER_ID, "activated_at": "2026-08-06T00:00:00Z"}
    table = webhook_fakes._FakeProfileVersionsTable(select_rows=[row])
    supabase = ComposedSupabase(profile_versions=table)

    renderer, _supabase, _registry = await _handle(
        inbound(callback_data=f"profile:cancel:{_VERSION_ID}"), supabase=supabase
    )

    assert renderer.calls == ["ack"]  # no reply, and handle_inbound did not raise
    assert table.delete_calls == 0  # the confirmed version is left alone


async def test_the_stage_on_the_button_is_the_stage_sent_to_the_database() -> None:
    supabase = ComposedSupabase(rpc_data={"id": APPLICATION_ID, "status": "interviewing"})

    renderer, supabase, _registry = await _handle(
        inbound(callback_data=f"app:stage:{APPLICATION_ID}:interviewing"), supabase=supabase
    )

    assert supabase.rpc_calls[0][1]["p_application_id"] == APPLICATION_ID
    assert supabase.rpc_calls[0][1]["p_new_status"] == "interviewing"
    assert renderer.sent[0][1].text == messages.stage_changed_text("interviewing")


async def test_marking_an_application_as_applied_confirms_with_the_status_in_bold() -> None:
    supabase = ComposedSupabase(rpc_data={"id": APPLICATION_ID, "status": "applied"})

    renderer, supabase, _registry = await _handle(
        inbound(callback_data=f"app:stage:{APPLICATION_ID}:applied"), supabase=supabase
    )

    assert renderer.sent[0][1].text == messages.stage_changed_text("applied")
    assert supabase.rpc_calls[0][1]["p_new_status"] == "applied"
    assert renderer.acks == [AckCallback("cbq-1")]


async def test_a_tap_with_data_nobody_recognizes_is_acknowledged_and_nothing_else() -> None:
    renderer, _supabase, _registry = await _handle(inbound(callback_data="something:else"))

    assert renderer.calls == ["ack"]


async def test_a_tap_with_no_data_is_acknowledged_and_nothing_else() -> None:
    # Telegram can send a tap with no data at all (the adapter makes that ""). It is still a
    # tap: acknowledged, and never handed to the text handler, which would answer with help.
    renderer, _supabase, _registry = await _handle(inbound(callback_data=""))

    assert renderer.calls == ["ack"]
    assert is_slow_request(inbound(callback_data="")) is False


# -- listing applications ------------------------------------------------------------------


class _SnapshotsById(webhook_fakes._FakeSimpleTable):
    """`job_snapshots` that honours the ids a lookup asks for, and keeps them."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        super().__init__(select_rows=rows)
        self.asked_for: list[list[str]] = []

    def select(self, *_: Any, **__: Any) -> webhook_fakes._ChainBuilder:
        table = self

        class _Lookup(webhook_fakes._ChainBuilder):
            def in_(self, *args: Any, **kwargs: Any) -> webhook_fakes._ChainBuilder:
                _column, ids = args
                table.asked_for.append(list(ids))
                self._rows = [row for row in table.select_rows if row["id"] in ids]
                return self

        return _Lookup(self.select_rows)


async def test_the_list_names_every_application_from_one_lookup_of_all_their_snapshots() -> None:
    snapshots = _SnapshotsById([webhook_fakes._SNAP_1, webhook_fakes._SNAP_2])
    supabase = ComposedSupabase(
        applications=webhook_fakes._FakeSimpleTable(
            select_rows=[webhook_fakes._APP_1, webhook_fakes._APP_2]
        ),
        job_snapshots=snapshots,
    )

    renderer, _supabase, _registry = await _handle(inbound("list"), supabase=supabase)

    assert [sorted(ids) for ids in snapshots.asked_for] == [["snap-1", "snap-2"]]
    assert "1. Staff AI Engineer @ Acme -- saved" in renderer.texts[0]
    assert "2. Backend Engineer @ Globex -- applied" in renderer.texts[0]


async def test_an_application_whose_snapshot_is_missing_is_listed_as_untitled() -> None:
    supabase = ComposedSupabase(
        applications=webhook_fakes._FakeSimpleTable(
            select_rows=[webhook_fakes._APP_1, webhook_fakes._APP_2]
        ),
        job_snapshots=_SnapshotsById([webhook_fakes._SNAP_1]),
    )

    renderer, _supabase, _registry = await _handle(inbound("list"), supabase=supabase)

    assert "1. Staff AI Engineer @ Acme -- saved" in renderer.texts[0]
    assert "2. Untitled @  -- applied" in renderer.texts[0]


# -- the deferred resume generation, one layer below the endpoint --------------------------


async def test_generating_a_resume_acknowledges_the_tap_first_and_never_twice() -> None:
    renderer, _supabase, registry = await _handle(
        inbound(callback_data=f"app:prepare:{APPLICATION_ID}")
    )
    await registry.shutdown(grace_seconds=5)

    assert renderer.calls[:2] == ["ack", "say"]  # the spinner stops, then "this can take a minute"
    assert renderer.texts[0] == messages.GENERATING_TEXT
    assert renderer.acks == [AckCallback("cbq-1")]  # not acknowledged again afterwards


async def test_the_resume_is_sent_as_a_document_after_the_handler_has_returned() -> None:
    gate = asyncio.Event()
    renderer, _supabase, registry = await _handle(
        inbound(callback_data=f"app:prepare:{APPLICATION_ID}"), http=_Engine(gate=gate)
    )

    # handle_inbound has returned: the reply the person saw is out, the resume is not
    assert renderer.texts == [messages.GENERATING_TEXT]
    assert renderer.documents == []
    assert registry.running == 1

    gate.set()
    await registry.shutdown(grace_seconds=5)

    ((chat, document),) = renderer.documents
    assert chat == CHAT_REF
    assert (document.filename, document.content) == ("resume.pdf", prepare_fakes._PDF_BYTES)
    assert document.caption is not None and "ATS score 72/100" in document.caption
    assert "Borderline seniority match." in document.caption
    # then the prompt with the stage button
    last = renderer.sent[-1][1]
    assert last.text.plain_text() == messages.MARK_APPLIED_PROMPT_TEXT
    assert last.buttons == ((Button("✅ Mark as applied", f"app:stage:{APPLICATION_ID}:applied"),),)


async def test_apply_to_a_number_runs_the_same_deferred_flow() -> None:
    renderer, _supabase, registry = await _handle(inbound("apply to #1"))
    await registry.shutdown(grace_seconds=5)

    assert renderer.texts[0] == messages.GENERATING_TEXT
    assert len(renderer.documents) == 1


_PERSON_A = "100000001"
_PERSON_B = "100000002"
_PERSON_C = "100000003"


def _tap_generate(subject: str) -> Any:
    return inbound(callback_data=f"app:prepare:{APPLICATION_ID}", subject=subject)


async def test_when_all_places_are_taken_the_user_hears_busy_and_nothing_else_starts() -> None:
    registry = DeferredReplies(max_concurrent=1)
    gate = asyncio.Event()
    engine = _Engine(gate=gate)
    supabase = ComposedSupabase(distinct_users=True)
    first, _s1, _r1 = await _handle(
        _tap_generate(_PERSON_A), supabase=supabase, http=engine, registry=registry
    )

    second, _s2, _r2 = await _handle(
        _tap_generate(_PERSON_B), supabase=supabase, http=engine, registry=registry
    )
    gate.set()
    await registry.shutdown(grace_seconds=5)

    assert second.texts == [messages.BUSY_TEXT]  # someone else holds the one place
    assert len(first.documents) == 1
    assert second.documents == []
    assert len([u for u in engine.post_calls if u.endswith("/apply")]) == 1


async def test_one_person_cannot_hold_more_than_one_place_and_others_still_get_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims: list[tuple[str, str]] = []

    async def count(_supabase: Any, user_id: str, bucket: str) -> rate_limits.RateLimitDecision:
        claims.append((user_id, bucket))
        return rate_limits.RateLimitDecision(True, 0)

    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", count)
    registry = DeferredReplies()  # the default cap of three: person A could take every place
    gate = asyncio.Event()
    engine = _Engine(gate=gate)
    supabase = ComposedSupabase(distinct_users=True)

    a_taps: list[FakeRenderer] = []
    for _ in range(3):  # three taps, as three deliveries: the update ledger lets them all in
        renderer, _s, _r = await _handle(
            _tap_generate(_PERSON_A), supabase=supabase, http=engine, registry=registry
        )
        a_taps.append(renderer)
    b_tap, _s, _r = await _handle(
        _tap_generate(_PERSON_B), supabase=supabase, http=engine, registry=registry
    )
    gate.set()
    await registry.shutdown(grace_seconds=5)

    assert [r.texts for r in a_taps] == [
        [messages.GENERATING_TEXT, messages.MARK_APPLIED_PROMPT_TEXT],  # and then its resume
        [messages.ALREADY_GENERATING_TEXT],
        [messages.ALREADY_GENERATING_TEXT],
    ]
    assert b_tap.texts[0] == messages.GENERATING_TEXT  # not "busy": A holds one place of three
    assert messages.BUSY_TEXT not in b_tap.texts
    assert len([u for u in engine.post_calls if u.endswith("/apply")]) == 2  # one each
    assert len(a_taps[0].documents) == 1 and len(b_tap.documents) == 1
    # The two refused taps spent nothing of A's hourly limit.
    assert claims == [(user_id_for(_PERSON_A), "prepare"), (user_id_for(_PERSON_B), "prepare")]


async def test_a_person_can_generate_again_once_their_generation_has_ended() -> None:
    registry = DeferredReplies()
    supabase = ComposedSupabase(distinct_users=True)

    first, _s1, _r1 = await _handle(_tap_generate(_PERSON_A), supabase=supabase, registry=registry)
    await registry.shutdown(grace_seconds=5)
    second, _s2, _r2 = await _handle(_tap_generate(_PERSON_A), supabase=supabase, registry=registry)
    await registry.shutdown(grace_seconds=5)

    assert messages.ALREADY_GENERATING_TEXT not in first.texts + second.texts
    assert len(first.documents) == 1 and len(second.documents) == 1


async def test_a_place_held_while_the_limit_is_asked_is_not_held_while_the_refusal_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing will run for a refused request, so its place goes back before the reply that
    says so is sent -- however slow that send is -- instead of leaving other people's taps
    answered "busy" while no generation exists."""

    async def deny(_supabase: Any, _user_id: str, _bucket: str) -> rate_limits.RateLimitDecision:
        return rate_limits.RateLimitDecision(False, 725)

    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", deny)

    class _SlowRefusal(FakeRenderer):
        def __init__(self) -> None:
            super().__init__()
            self.sending = asyncio.Event()
            self.finish = asyncio.Event()

        async def send(self, chat_ref: str, intent: Say) -> MessageRef | None:
            self.sending.set()
            await asyncio.wait_for(self.finish.wait(), timeout=10)
            return await super().send(chat_ref, intent)

    registry = DeferredReplies(max_concurrent=1)
    renderer = _SlowRefusal()
    refused = asyncio.create_task(
        _handle(_tap_generate(_PERSON_A), renderer=renderer, registry=registry)
    )
    try:
        await asyncio.wait_for(renderer.sending.wait(), timeout=5)  # the refusal is in flight

        place = await registry.try_reserve(user_id_for(_PERSON_B))  # so the place is free
        assert place is not None
        assert not registry.holds(user_id_for(_PERSON_A))
        place.release()
    finally:
        renderer.finish.set()
        await refused

    assert renderer.texts[0].startswith("❌ You've reached the limit")


async def test_a_failure_inside_the_generation_is_told_to_the_user() -> None:
    renderer, _supabase, registry = await _handle(
        inbound(callback_data=f"app:prepare:{APPLICATION_ID}"), http=_Engine(fail=True)
    )
    await registry.shutdown(grace_seconds=5)

    assert renderer.texts[-1] == messages.PREPARE_FAILED_TEXT
    assert renderer.documents == []


def _telegram_refusal() -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError(
        "429 Too Many Requests",
        request=httpx.Request("POST", "https://api.telegram.org/bot/sendMessage"),
        response=httpx.Response(429),
    )


class _RefusesThePrompt(FakeRenderer):
    """A channel that takes the resume and then refuses the message that follows it."""

    async def send(self, chat_ref: str, intent: Say) -> MessageRef | None:
        if intent.text.plain_text() == messages.MARK_APPLIED_PROMPT_TEXT:
            raise _telegram_refusal()
        return await super().send(chat_ref, intent)


class _RefusesTheFile(FakeRenderer):
    async def send_document(self, chat_ref: str, intent: SendDocument) -> MessageRef | None:
        raise _telegram_refusal()


async def test_a_refused_follow_up_prompt_does_not_tell_the_person_the_generation_failed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The resume is already in the chat. A failure to offer the "mark as applied" button is
    logged and nothing more: told as "something went wrong generating", it would send the
    person off to pay for a second generation."""
    renderer = _RefusesThePrompt()

    with (
        caplog.at_level(logging.INFO, logger="between_jobs.api.channel_core"),
        caplog.at_level(logging.INFO, logger="between_jobs.api.deferred_reply"),
    ):
        _r, _s, registry = await _handle(
            inbound(callback_data=f"app:prepare:{APPLICATION_ID}"), renderer=renderer
        )
        await registry.shutdown(grace_seconds=5)

    assert len(renderer.documents) == 1
    assert messages.PREPARE_FAILED_TEXT not in renderer.texts
    assert registry.running == 0
    assert await registry.try_reserve(USER_ID) is not None  # its place and key came back
    logged = [r.getMessage() for r in caplog.records]
    assert "deferred work failed" not in logged  # not a failure of the task
    (record,) = [r for r in caplog.records if "mark-as-applied prompt" in r.getMessage()]
    assert record.levelno == logging.WARNING
    assert record.exc_info is not None
    assert record.ctx == {"update_id": "1", "channel": "telegram"}  # type: ignore[attr-defined]


async def test_a_refused_file_is_still_told_as_a_failure_because_the_person_has_nothing() -> None:
    renderer = _RefusesTheFile()

    _r, _s, registry = await _handle(
        inbound(callback_data=f"app:prepare:{APPLICATION_ID}"), renderer=renderer
    )
    await registry.shutdown(grace_seconds=5)

    assert renderer.documents == []
    assert renderer.texts[-1] == messages.PREPARE_FAILED_TEXT


async def test_a_declined_generation_with_no_reason_to_give_still_says_something() -> None:
    declined = {
        **prepare_fakes._FORGE_APPLY_RESPONSE_BODY,
        "resume": None,
        "ats_attempts": [],
        "gate": {"outcome": "proceed", "reason": "", "cautions": []},
    }

    renderer, _supabase, registry = await _handle(
        inbound(callback_data=f"app:prepare:{APPLICATION_ID}"), http=_Engine(apply_body=declined)
    )
    await registry.shutdown(grace_seconds=5)

    assert renderer.documents == []
    assert renderer.texts[-1] == messages.PREPARE_DECLINED_TEXT.format(warnings="No details given.")


async def test_every_generation_and_every_stage_change_gets_a_fresh_idempotency_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key that repeated would make the second request replay the first one's result
    without doing anything: no resume, no stage change, and no sign of it in the chat."""
    prepare_keys: list[str] = []

    async def record_prepare(
        supabase: Any, http: Any, user_id: str, application_id: str, *, idempotency_key: str
    ) -> Any:
        prepare_keys.append(idempotency_key)
        return await run_prepare_application(
            supabase, http, user_id, application_id, idempotency_key=idempotency_key
        )

    stage_keys: list[str] = []

    async def record_stage(
        supabase: Any,
        user_id: str,
        application_id: str,
        *,
        new_status: str,
        idempotency_key: str,
    ) -> Any:
        stage_keys.append(idempotency_key)
        return await change_stage(
            supabase,
            user_id,
            application_id,
            new_status=new_status,
            idempotency_key=idempotency_key,
        )

    monkeypatch.setattr(channel_core, "run_prepare_application", record_prepare)
    monkeypatch.setattr(channel_core, "change_stage", record_stage)
    supabase = ComposedSupabase(rpc_data={"id": APPLICATION_ID, "status": "applied"})
    registry = DeferredReplies()
    channel = inbound().channel

    for _ in range(2):
        await _handle(
            inbound(callback_data=f"app:prepare:{APPLICATION_ID}"),
            supabase=supabase,
            registry=registry,
        )
        await registry.shutdown(grace_seconds=5)
        await _handle(
            inbound(callback_data=f"app:stage:{APPLICATION_ID}:applied"),
            supabase=supabase,
            registry=registry,
        )

    for keys, prefix in ((prepare_keys, f"{channel}:"), (stage_keys, f"{channel}-stage:")):
        assert len(keys) == 2
        assert keys[0] != keys[1]
        for key in keys:
            assert key.startswith(prefix)
            assert 16 <= len(key) <= 128  # the bounds the web's prepare route holds its key to


async def test_the_generation_still_claims_the_users_prepare_slot_before_starting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims: list[tuple[str, str]] = []

    async def deny(_supabase: Any, user_id: str, bucket: str) -> rate_limits.RateLimitDecision:
        claims.append((user_id, bucket))
        return rate_limits.RateLimitDecision(False, 725)

    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", deny)
    engine = _Engine()

    renderer, _supabase, registry = await _handle(
        inbound(callback_data=f"app:prepare:{APPLICATION_ID}"), http=engine
    )

    assert claims == [(USER_ID, "prepare")]
    assert renderer.texts == [
        "❌ You've reached the limit for this action (10 per hour). Try again in about 13 minutes."
    ]
    assert engine.post_calls == []
    assert registry.running == 0


# -- which requests are slow ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("message_kwargs", "slow"),
    [
        ({"callback_data": "app:prepare:abc"}, True),
        ({"callback_data": "app:stage:abc:applied"}, False),
        ({"callback_data": "profile:activate:abc"}, False),
        ({"text": "apply to #3"}, True),
        ({"text": "generate #1"}, True),
        ({"text": "list"}, False),
        ({"text": "hi"}, False),
        ({"text": "apply to #3", "attachment": _json_attachment()}, True),  # caption still counts
    ],
)
def test_only_a_generate_tap_or_an_apply_reference_is_a_slow_request(
    message_kwargs: dict[str, Any], slow: bool
) -> None:
    assert is_slow_request(inbound(**message_kwargs)) is slow
