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
from pathlib import Path
from typing import Any, ClassVar

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
    EditMessage,
    MessageRef,
    RichText,
    Say,
    Segment,
    SendDocument,
)
from between_jobs.api.deferred_reply import DeferredReplies
from between_jobs.api.first_run import ApplicationFacts, FirstRunFacts, derive_first_run
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


async def test_a_long_job_paste_is_tracked_not_read_as_a_resume_file() -> None:
    # Real job descriptions run to thousands of characters, well past the length at which a
    # message is otherwise taken for a pasted resume.
    description = "Build and run data pipelines for the analytics team. " * 40
    assert len(description) > 800
    message = inbound(f"Title: Data Engineer\nCompany: Acme\n\n{description}")
    supabase = ComposedSupabase(applications=webhook_fakes._FakeSimpleTable(select_rows=[]))

    renderer, supabase, _registry = await _handle(message, supabase=supabase)

    _chat, say = renderer.sent[0]
    assert say.text == messages.job_tracked_text("Data Engineer", "Acme")
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
    """Identity is implemented for Telegram and Discord. A message from a channel nobody wrote
    the account rules for must not be filed under either, so it stops at the identity step --
    nothing is said or written."""
    renderer = FakeRenderer()
    supabase = ComposedSupabase()

    with pytest.raises(ValueError, match="no identity provisioning for channel 'slack'"):
        await _handle(inbound("hi", channel="slack"), supabase=supabase, renderer=renderer)

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
    # The one message the person has been watching ends as the final state, with the stage
    # button under it: no second message after the document.
    final = renderer.edits[-1]
    assert final.text.plain_text() == messages.PREPARE_DONE_TEXT
    assert final.buttons == (
        (Button("✅ Mark as applied", f"app:stage:{APPLICATION_ID}:applied"),),
    )
    assert renderer.texts == [messages.GENERATING_TEXT]


async def test_the_caption_of_a_resume_nobody_scored_says_nothing_about_a_score() -> None:
    """An engine that does not score its resumes sends no ATS attempts. The caption is then the
    same message without the score -- not "ATS score --/100", a number's shape with no number in
    it. (The scored caption is pinned just above and, byte for byte, by the Telegram golden.)"""
    unscored = {**prepare_fakes._FORGE_APPLY_RESPONSE_BODY, "ats_attempts": []}
    renderer, _supabase, registry = await _handle(
        inbound(callback_data=f"app:prepare:{APPLICATION_ID}"), http=_Engine(apply_body=unscored)
    )
    await registry.shutdown(grace_seconds=5)

    ((_, document),) = renderer.documents
    assert document.caption == "📄 Resume\n• Borderline seniority match."
    assert "score" not in document.caption.lower() and "/100" not in document.caption
    # the resume was delivered all the same, so the chat ends as it does for a scored one
    assert renderer.edits[-1].text.plain_text() == messages.PREPARE_DONE_TEXT


async def test_a_resume_that_scored_zero_is_captioned_with_its_zero() -> None:
    """Zero is a score. Only an engine that sent none leaves the caption without one."""
    body: dict[str, Any] = prepare_fakes._FORGE_APPLY_RESPONSE_BODY
    zero = {**body, "ats_attempts": [{**body["ats_attempts"][0], "overall_score": 0}]}
    renderer, _supabase, registry = await _handle(
        inbound(callback_data=f"app:prepare:{APPLICATION_ID}"), http=_Engine(apply_body=zero)
    )
    await registry.shutdown(grace_seconds=5)

    ((_, document),) = renderer.documents
    assert document.caption is not None and document.caption.startswith(
        "\U0001f4c4 Resume -- ATS score 0/100"
    )


async def test_a_long_list_of_warnings_never_makes_a_caption_telegram_would_refuse() -> None:
    """Telegram refuses a document whose caption is over 1024 characters, and a resume that was
    already written must not be lost to its own warnings. Whole bullets are kept while they fit
    and the rest are counted."""
    many = [f'unsupported claim (removed): "{"x" * 150}" -- note {n}' for n in range(30)]
    body = {
        **prepare_fakes._FORGE_APPLY_RESPONSE_BODY,
        "ats_attempts": [],
        "gate": {"outcome": "proceed", "reason": "", "cautions": many},
    }
    renderer, _supabase, registry = await _handle(
        inbound(callback_data=f"app:prepare:{APPLICATION_ID}"), http=_Engine(apply_body=body)
    )
    await registry.shutdown(grace_seconds=5)

    ((_, document),) = renderer.documents
    caption = document.caption
    assert caption is not None and len(caption.encode("utf-16-le")) // 2 <= 1024
    assert caption.startswith("📄 Resume\n• unsupported claim (removed)")
    assert "more" in caption.splitlines()[-1]  # the bullets that did not fit are counted
    assert renderer.edits[-1].text.plain_text() == messages.PREPARE_DONE_TEXT  # and it delivered


def test_the_unscored_caption_is_the_scored_one_without_its_score() -> None:
    scored = messages.PREPARE_SUCCESS_CAPTION.format(score=72, warnings="\n• a")
    unscored = messages.PREPARE_SUCCESS_CAPTION_UNSCORED.format(warnings="\n• a")

    assert scored == "📄 Resume -- ATS score 72/100\n• a"
    assert unscored == "📄 Resume\n• a"
    assert messages.PREPARE_SUCCESS_CAPTION_UNSCORED.format(warnings="") == "📄 Resume"


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
        [messages.GENERATING_TEXT],  # edited in place as the resume was made and delivered
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

    # the progress message the person was watching ends as the failure
    assert renderer.texts == [messages.GENERATING_TEXT]
    assert renderer.shown_texts == [messages.PREPARE_FAILED_TEXT]
    assert renderer.documents == []


def _telegram_refusal() -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError(
        "429 Too Many Requests",
        request=httpx.Request("POST", "https://api.telegram.org/bot/sendMessage"),
        response=httpx.Response(429),
    )


class _RefusesTheFinalState(FakeRenderer):
    """A channel that takes the resume and then refuses everything that would show the final
    state: the edit of the progress message and the new message that is the fallback."""

    async def edit(self, intent: EditMessage) -> MessageRef | None:
        if intent.text.plain_text() == messages.PREPARE_DONE_TEXT:
            raise _telegram_refusal()
        return await super().edit(intent)

    async def send(self, chat_ref: str, intent: Say) -> MessageRef | None:
        if intent.text.plain_text() == messages.PREPARE_DONE_TEXT:
            raise _telegram_refusal()
        return await super().send(chat_ref, intent)


class _RefusesTheFile(FakeRenderer):
    async def send_document(self, chat_ref: str, intent: SendDocument) -> MessageRef | None:
        raise _telegram_refusal()


async def test_a_refused_final_state_does_not_tell_the_person_the_generation_failed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The resume is already in the chat. A failure to show the final state (and the "mark as
    applied" button with it) is logged and nothing more: told as "something went wrong
    generating", it would send the person off to pay for a second generation."""
    renderer = _RefusesTheFinalState()

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
    (record,) = [r for r in caplog.records if "how their request ended" in r.getMessage()]
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
    assert renderer.shown_texts == [messages.PREPARE_FAILED_TEXT]


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
    assert renderer.shown_texts == [
        messages.PREPARE_DECLINED_TEXT.format(warnings="No details given.")
    ]


# -- an ending the channel refuses, with no resume to show for it ---------------------------

_TELEGRAMS_LIMIT = 4096


class _TakesOnlyTheCannedMessages(FakeRenderer):
    """A channel that refuses, on edit and on send alike, every text but the bot's own fixed
    ones: what a person whose "reason" text it will not take looks like from here."""

    _TAKEN: ClassVar[frozenset[str]] = frozenset(
        {
            messages.GENERATING_TEXT,
            messages.PREPARE_COMPILING_TEXT,
            messages.PREPARE_DONE_TEXT,
            messages.PREPARE_FAILED_TEXT,
        }
    )

    async def edit(self, intent: EditMessage) -> MessageRef | None:
        if intent.text.plain_text() not in self._TAKEN:
            raise _bad_request("Bad Request: message is too long")
        return await super().edit(intent)

    async def send(self, chat_ref: str, intent: Say) -> MessageRef | None:
        if intent.text.plain_text() not in self._TAKEN:
            raise _bad_request("Bad Request: message is too long")
        return await super().send(chat_ref, intent)


class _TakesNothingOver(FakeRenderer):
    """A channel with a size limit, as Telegram has: no text over `limit` characters, on edit or
    on send."""

    def __init__(self, limit: int) -> None:
        super().__init__()
        self._limit = limit

    async def edit(self, intent: EditMessage) -> MessageRef | None:
        if len(intent.text.plain_text()) > self._limit:
            raise _bad_request("Bad Request: message is too long")
        return await super().edit(intent)

    async def send(self, chat_ref: str, intent: Say) -> MessageRef | None:
        if len(intent.text.plain_text()) > self._limit:
            raise _bad_request("Bad Request: message is too long")
        return await super().send(chat_ref, intent)


def _declined_for(*reasons: str) -> dict[str, Any]:
    return {
        **prepare_fakes._FORGE_APPLY_RESPONSE_BODY,
        "resume": None,
        "ats_attempts": [],
        "gate": {"outcome": "reject_mismatch", "reason": reasons[0], "cautions": list(reasons[1:])},
    }


async def test_a_declined_resume_whose_reason_the_channel_refuses_is_told_as_a_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The person has no resume, so they must not be left on "this can take a minute": the reason
    was refused, so the generic failure is what they are told (as before the progress message
    existed, when the refusal reached the registry)."""
    renderer = _TakesNothingOver(1000)  # the engine's reason below is longer than this
    long_reason = "The posting asks for a clearance. " * 150

    with caplog.at_level(logging.INFO, logger="between_jobs.api.progress_message"):
        await _generate(renderer=renderer, http=_Engine(apply_body=_declined_for(long_reason)))

    assert renderer.documents == []
    assert renderer.shown_texts == [messages.GENERATING_TEXT, messages.PREPARE_FAILED_TEXT]
    assert renderer.texts[-1] == messages.PREPARE_FAILED_TEXT  # a message of its own
    assert any("how their request ended" in r.getMessage() for r in caplog.records)


async def test_a_known_error_the_channel_refuses_is_told_as_a_failure() -> None:
    supabase = ComposedSupabase()
    supabase.profile_versions.select_rows = []  # no resume on file: an error with its own words

    renderer = await _generate(renderer=_TakesOnlyTheCannedMessages(), supabase=supabase)

    assert renderer.documents == []
    assert renderer.shown_texts == [messages.GENERATING_TEXT, messages.PREPARE_FAILED_TEXT]


async def test_a_pdf_error_the_channel_refuses_is_told_as_a_failure() -> None:
    http = _Engine()
    http.compile_status_code = 422

    renderer = await _generate(renderer=_TakesOnlyTheCannedMessages(), http=http)

    assert renderer.documents == []
    assert renderer.shown_texts[-1] == messages.PREPARE_FAILED_TEXT


async def test_a_declined_resume_with_a_huge_reason_is_bounded_so_the_channel_takes_it() -> None:
    """Telegram's own limit applies. The engine's reasons are not text this code writes, so they
    are cut to fit and say how many more there were, rather than refused whole."""
    renderer = _TakesNothingOver(_TELEGRAMS_LIMIT)
    reasons = [f"Reason {i}: " + "x" * 400 for i in range(40)]

    await _generate(renderer=renderer, http=_Engine(apply_body=_declined_for(*reasons)))

    (ending,) = renderer.shown_texts
    assert ending.startswith(messages.PREPARE_DECLINED_TEXT.split("{")[0])
    assert "Reason 0: " in ending and "• ... and " in ending and ending.endswith(" more")
    assert len(ending) <= _TELEGRAMS_LIMIT
    assert messages.PREPARE_FAILED_TEXT not in renderer.texts


async def test_a_channel_that_takes_nothing_after_the_opening_still_ends_quietly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both tries are refused: nothing is raised (the registry would call it a failed task and
    log it), the refusals are logged, and the place is given back."""

    class _TakesNothingAfterTheOpening(FakeRenderer):
        async def edit(self, intent: EditMessage) -> MessageRef | None:
            raise _telegram_refusal()

        async def send(self, chat_ref: str, intent: Say) -> MessageRef | None:
            if self.sent:
                raise _telegram_refusal()
            return await super().send(chat_ref, intent)

    renderer = _TakesNothingAfterTheOpening()
    declined = _declined_for("Fit score too low.")

    with caplog.at_level(logging.INFO):
        _r, _s, registry = await _handle(
            inbound(callback_data=_TAP), renderer=renderer, http=_Engine(apply_body=declined)
        )
        await registry.shutdown(grace_seconds=5)

    assert renderer.texts == [messages.GENERATING_TEXT]
    assert registry.running == 0
    assert await registry.try_reserve(USER_ID) is not None
    logged = [r.getMessage() for r in caplog.records]
    assert "deferred work failed" not in logged
    assert len([m for m in logged if "how their request ended" in m]) == 2  # each try is logged


def test_the_declined_text_of_a_normal_reason_is_unchanged_by_the_bound() -> None:
    assert messages.declined_warnings_text([]) == "No details given."
    assert messages.declined_warnings_text(["Fit score too low."]) == "• Fit score too low."
    assert messages.declined_warnings_text(["a", "b"]) == "• a\n• b"


def test_the_declined_text_is_cut_to_its_limit_and_counts_what_it_leaves_out() -> None:
    reasons = [f"reason number {i}" for i in range(100)]

    text = messages.declined_warnings_text(reasons, limit=200)

    assert len(text) <= 200
    lines = text.splitlines()
    assert lines[0] == "• reason number 0"
    assert lines[-1] == f"• ... and {100 - (len(lines) - 1)} more"
    assert all(line.startswith("• ") for line in lines)  # whole bullets, never a half one


def test_one_reason_that_is_by_itself_too_long_is_cut_with_an_ellipsis() -> None:
    text = messages.declined_warnings_text(["y" * 5000], limit=300)

    assert len(text) <= 300 and text.startswith("• yyy") and text.endswith("…")
    assert "more" not in text  # nothing else was left out

    text_with_others = messages.declined_warnings_text(["y" * 5000, "z"], limit=300)
    assert len(text_with_others) <= 300 and text_with_others.endswith("• ... and 1 more")


# -- one progress message ------------------------------------------------------------------

_TAP = f"app:prepare:{APPLICATION_ID}"


async def _generate(
    *, renderer: FakeRenderer | None = None, http: _Engine | None = None, supabase: Any = None
) -> FakeRenderer:
    renderer = renderer or FakeRenderer()
    _r, _s, registry = await _handle(
        inbound(callback_data=_TAP), renderer=renderer, http=http, supabase=supabase
    )
    await registry.shutdown(grace_seconds=5)
    return renderer


async def test_a_resume_is_one_progress_message_edited_twice_and_then_the_document() -> None:
    renderer = await _generate()

    # exactly one message sent; the rest of what the person was told is edits of it
    assert renderer.texts == [messages.GENERATING_TEXT]
    progress = MessageRef(CHAT_REF, "1")
    assert [(e.message_ref, e.text.plain_text()) for e in renderer.edits] == [
        (progress, messages.PREPARE_COMPILING_TEXT),
        (progress, messages.PREPARE_DONE_TEXT),
    ]
    # in this order: opened, "compiling" while the PDF is made, the file, then the final state
    assert renderer.calls == ["ack", "say", "edit", "document", "edit"]
    assert renderer.shown_texts == [messages.PREPARE_DONE_TEXT]
    # only the final state carries the button, and it is for this application
    assert renderer.edits[0].buttons == ()
    assert renderer.edits[1].buttons == (
        (Button("✅ Mark as applied", f"app:stage:{APPLICATION_ID}:applied"),),
    )


async def test_a_resume_the_engine_declines_ends_the_progress_message_with_the_reason() -> None:
    declined = {
        **prepare_fakes._FORGE_APPLY_RESPONSE_BODY,
        "resume": None,
        "ats_attempts": [],
        "gate": {"outcome": "skip_low_score", "reason": "Fit score too low.", "cautions": []},
    }

    renderer = await _generate(http=_Engine(apply_body=declined))

    assert renderer.texts == [messages.GENERATING_TEXT]
    assert [e.text.plain_text() for e in renderer.edits] == [
        messages.PREPARE_DECLINED_TEXT.format(warnings="• Fit score too low.")
    ]
    assert renderer.documents == []
    assert renderer.edits[0].buttons == ()  # nothing to mark as applied


async def test_a_known_error_ends_the_progress_message_as_the_error() -> None:
    supabase = ComposedSupabase()
    supabase.profile_versions.select_rows = []  # no resume on file

    renderer = await _generate(supabase=supabase)

    assert renderer.texts == [messages.GENERATING_TEXT]
    ((edit),) = renderer.edits
    assert edit.text.plain_text().startswith("❌")
    assert renderer.documents == []


async def test_a_pdf_that_will_not_compile_is_a_stage_and_then_the_error() -> None:
    http = _Engine()
    http.compile_status_code = 422

    renderer = await _generate(http=http)

    assert renderer.texts == [messages.GENERATING_TEXT]
    texts = [e.text.plain_text() for e in renderer.edits]
    assert texts[0] == messages.PREPARE_COMPILING_TEXT
    assert len(texts) == 2 and texts[1].startswith("❌")
    assert renderer.documents == []


class _CannotEdit(FakeRenderer):
    """A channel that cannot edit a message at all, or refuses to for any reason (the message
    was deleted, an edit rate limit, an outage): every edit raises."""

    def __init__(self, error: Exception | None = None) -> None:
        super().__init__()
        self._error = error or NotImplementedError("this channel cannot edit messages")
        self.refused_edits: list[EditMessage] = []

    async def edit(self, intent: EditMessage) -> MessageRef | None:
        self.refused_edits.append(intent)
        raise self._error


def _bad_request(description: str) -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError(
        "400 Bad Request",
        request=httpx.Request("POST", "https://api.telegram.org/bot/editMessageText"),
        response=httpx.Response(400, json={"ok": False, "description": description}),
    )


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(None, id="a channel that cannot edit"),
        pytest.param(_bad_request("Bad Request: message to edit not found"), id="deleted"),
        pytest.param(_telegram_refusal(), id="rate limited"),
        pytest.param(ConnectionError("the channel is unreachable"), id="unreachable"),
    ],
)
async def test_when_nothing_can_be_edited_the_final_state_is_sent_as_a_new_message(
    error: Exception | None,
) -> None:
    """The person never loses what the final state says. The stage in between is a courtesy
    and is dropped rather than sent as a message of its own."""
    renderer = _CannotEdit(error)

    await _generate(renderer=renderer)

    assert len(renderer.refused_edits) == 2  # the stage, and the final state: both tried
    assert renderer.texts == [messages.GENERATING_TEXT, messages.PREPARE_DONE_TEXT]
    final = renderer.sent[-1][1]
    assert final.buttons == (
        (Button("✅ Mark as applied", f"app:stage:{APPLICATION_ID}:applied"),),
    )
    assert len(renderer.documents) == 1
    assert renderer.calls == ["ack", "say", "document", "say"]


async def test_an_error_is_sent_as_a_new_message_when_it_cannot_be_edited_in() -> None:
    supabase = ComposedSupabase()
    supabase.profile_versions.select_rows = []  # no resume on file
    renderer = _CannotEdit()

    await _generate(renderer=renderer, supabase=supabase)

    assert len(renderer.texts) == 2
    assert renderer.texts[1].startswith("❌")  # what went wrong still reaches the person


async def test_a_stage_that_cannot_be_edited_in_does_not_stop_the_final_edit() -> None:
    class _FailsTheStage(FakeRenderer):
        async def edit(self, intent: EditMessage) -> MessageRef | None:
            if intent.text.plain_text() == messages.PREPARE_COMPILING_TEXT:
                raise _telegram_refusal()
            return await super().edit(intent)

    renderer = _FailsTheStage()

    await _generate(renderer=renderer)

    assert renderer.texts == [messages.GENERATING_TEXT]  # no extra message
    assert renderer.shown_texts == [messages.PREPARE_DONE_TEXT]  # the final edit still landed


async def test_a_renderer_that_names_no_message_gets_the_final_state_as_a_new_message() -> None:
    class _NamesNothing(FakeRenderer):
        async def send(self, chat_ref: str, intent: Say) -> MessageRef | None:
            await super().send(chat_ref, intent)
            return None

    renderer = _NamesNothing()

    await _generate(renderer=renderer)

    assert renderer.edits == []  # nothing to edit by
    assert renderer.texts == [messages.GENERATING_TEXT, messages.PREPARE_DONE_TEXT]


async def test_nothing_the_progress_message_does_can_make_the_task_fail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every edit and every send after the opening one is refused, and the generation still ends
    quietly with the resume delivered: a task that raised would be told to the person as a
    failure and logged as one."""

    class _RefusesEverythingAfterTheOpening(FakeRenderer):
        async def edit(self, intent: EditMessage) -> MessageRef | None:
            raise _telegram_refusal()

        async def send(self, chat_ref: str, intent: Say) -> MessageRef | None:
            if self.sent:
                raise _telegram_refusal()
            return await super().send(chat_ref, intent)

    renderer = _RefusesEverythingAfterTheOpening()

    with caplog.at_level(logging.INFO, logger="between_jobs.api.deferred_reply"):
        await _generate(renderer=renderer)

    assert len(renderer.documents) == 1
    assert renderer.texts == [messages.GENERATING_TEXT]
    assert "deferred work failed" not in [r.getMessage() for r in caplog.records]


async def test_the_opening_message_failing_is_the_requests_own_failure() -> None:
    """Nothing has been promised yet, so this is a failed reply like any other, not a quiet
    one: the webhook answers an error and the channel redelivers."""

    class _CannotOpen(FakeRenderer):
        async def send(self, chat_ref: str, intent: Say) -> MessageRef | None:
            raise _telegram_refusal()

    registry = DeferredReplies()

    with pytest.raises(httpx.HTTPStatusError):
        await _handle(inbound(callback_data=_TAP), renderer=_CannotOpen(), registry=registry)

    assert registry.running == 0
    assert await registry.try_reserve(USER_ID) is not None  # the place was given back


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


# -- /privacy and /learn -------------------------------------------------------------------

_WEB = "https://app.between-jobs.example"


def _linked(**overrides: Any) -> ComposedSupabase:
    """A chat linked to a web account: the user it resolves to is a web user, which carries no
    mark of having been made by the bot."""
    return ComposedSupabase(existing_user=webhook_fakes._WEB_USER, **overrides)


def _unlinked(**overrides: Any) -> ComposedSupabase:
    """The account the bot made for this sender on first contact."""
    bot_made = webhook_fakes._auth_user(
        "telegram-x@users.between-jobs.tech",
        provider="telegram",
        bj_provisioned_by="telegram",
        bj_telegram_subject=inbound().subject,
    )
    return ComposedSupabase(existing_user=bot_made, **overrides)


@pytest.fixture
def web_url(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("WEB_APP_URL", _WEB)
    return _WEB


@pytest.fixture
def no_web_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_APP_URL", raising=False)


@pytest.mark.parametrize("text", ["/privacy", "privacy"])
async def test_privacy_is_one_short_message_that_links_the_policy(text: str, web_url: str) -> None:
    renderer, supabase, _registry = await _handle(inbound(text), supabase=_linked())

    assert len(renderer.sent) == 1 and renderer.documents == [] and renderer.edits == []
    say = renderer.sent[0][1]
    assert say.buttons == ()
    shown = say.text.plain_text()
    assert shown.endswith(f"Full policy: {_WEB}/privacy")
    assert len(shown) < 1200  # a summary, not the policy
    for fact in ("encrypted", "Telegram id", "OpenRouter", "Supabase", "/unlink", "Delete my"):
        assert fact in shown
    assert supabase.auth.admin.get_user_by_id_calls == [USER_ID]  # the only thing it looked up


async def test_privacy_without_a_web_address_says_where_the_policy_is_and_links_nothing(
    no_web_url: None,
) -> None:
    renderer, _supabase, _registry = await _handle(inbound("/privacy"), supabase=_linked())

    shown = renderer.texts[0]
    assert shown.endswith(messages.PRIVACY_NO_LINK_TEXT)
    assert "http" not in shown


@pytest.mark.parametrize("linked", [True, False], ids=["linked", "bot-only"])
@pytest.mark.parametrize("address", [None, _WEB, "https://between-jobs.tech"])
def test_the_privacy_summary_stays_a_summary_whoever_reads_it(
    linked: bool, address: str | None
) -> None:
    shown = messages.privacy_text(address, linked=linked).plain_text()

    assert len(shown) < 1200


# What the bot's summary says, and the words of the web policy (`web/src/content/legal.ts`) that
# say the same. The policy is the authority: if it changes, this fails until the summary is
# checked against it again. Each pair is (words in the bot's message, words in the policy).
_PRIVACY_CLAIMS = [
    ("stored encrypted", "stored encrypted"),
    ("numeric Telegram id", "numeric Telegram user id"),
    ("no name, no username", "no name or username"),
    ("failed link-code attempts", "failed link-code attempts"),
    ("a short record each time you use a main feature", "A short record each time you use a main"),
    ("no resume or job text", "your resume or any job text"),
    ("numbered lists", "numbered lists of applications"),
    ("Supabase", "Supabase"),
    ("Railway", "Railway"),
    ("OpenRouter", "OpenRouter"),
    ("resume engine", "Resume engine"),
    # Where the person's AI key goes: the summary must say what the policy says, not less.
    ("the AI key and model you chose", "the AI key and model you chose"),
    ("that one request", "for that one request"),
    ("does not store it", "does not store your key"),
    ("PDF renderer", "PDF renderer"),
    ("submit an application or send an email for you", "never submits an application for you"),
    ("no third-party analytics", "no third-party analytics"),
]
# Only the version for a chat linked to a web account. The deletion promise carries the
# qualifier the policy attaches to it, and names the exceptions as the policy lists them.
_PRIVACY_CLAIMS_LINKED = [
    ("/unlink detaches", "'/unlink' detaches it"),
    ("Delete my account", "'Delete my account' on the Profile page"),
    ("with exceptions", "with the exceptions listed under 'Keeping and deleting your data'"),
    ("backups until they expire", "keeps backups, deleted data stays in them until they expire"),
    ("drafts in your Gmail", "Drafts we already created in your Gmail"),
    ("what your AI provider kept", "Anything your AI and search providers kept"),
]
# Only the version for a chat the bot made an account for: no Profile page to delete from.
_PRIVACY_CLAIMS_BOT_ONLY = [
    ("link this chat to a website account with a code", "link it to a web account with a code"),
    ("Integrations page", "Integrations page"),
    ("email the privacy address", "delete your account by email at"),
    ("within 7 days", "We will do it within 7 days"),
    ("what the policy lists as not removed", "What is not removed when you delete your account"),
]


@pytest.mark.parametrize(
    ("linked", "claims"),
    [
        pytest.param(True, _PRIVACY_CLAIMS + _PRIVACY_CLAIMS_LINKED, id="linked"),
        pytest.param(False, _PRIVACY_CLAIMS + _PRIVACY_CLAIMS_BOT_ONLY, id="bot-only"),
    ],
)
def test_every_claim_in_the_privacy_summary_is_one_the_web_policy_makes(
    linked: bool, claims: list[tuple[str, str]], no_web_url: None
) -> None:
    legal = (Path(__file__).parent.parent / "web" / "src" / "content" / "legal.ts").read_text()
    summary = messages.privacy_text(None, linked=linked).plain_text()

    for in_the_bot, in_the_policy in claims:
        assert in_the_bot.lower() in summary.lower(), in_the_bot
        assert in_the_policy.lower() in legal.lower(), in_the_policy


@pytest.mark.parametrize("linked", [True, False], ids=["linked", "bot-only"])
def test_what_is_kept_is_not_offered_as_a_complete_list(linked: bool, no_web_url: None) -> None:
    """The policy lists more than the summary can (every table, and more services). The line
    about what is kept says "mainly" so it does not read as the whole list, and the message ends
    by pointing at the full policy for the rest."""
    summary = messages.privacy_text(None, linked=linked).plain_text()

    kept = next(line for line in summary.splitlines() if line.startswith("• What I keep"))
    assert "mainly" in kept
    assert summary.endswith(messages.PRIVACY_NO_LINK_TEXT)
    assert "Full policy" in messages.privacy_text(_WEB, linked=linked).plain_text()


def test_the_deletion_promise_is_never_stated_without_its_qualifier(no_web_url: None) -> None:
    """The policy promises deletion "with the exceptions" it lists. A sentence that says an
    account "and its data" are removed and stops there promises more than it does."""
    summary = messages.privacy_text(None, linked=True).plain_text()

    assert "removes your account and its data." not in summary
    promise = summary.split('"Delete my account"')[1].split("\n")[0]
    assert "exceptions" in promise


async def test_a_chat_the_bot_made_an_account_for_gets_the_summary_too(
    web_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bot already holds what this person sent it, so /privacy is not withheld: it reads no
    data. Only the last line differs, since there is no web account to delete from the Profile
    page and nothing to /unlink."""

    async def never(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("/privacy must not read the person's data")

    monkeypatch.setattr(channel_core, "load_first_run_facts", never)
    supabase = _unlinked()

    renderer, _s, _r = await _handle(inbound("/privacy"), supabase=supabase)

    assert len(renderer.sent) == 1 and renderer.sent[0][1].buttons == ()
    shown = renderer.texts[0]
    assert renderer.texts == [messages.privacy_text(_WEB, linked=False).plain_text()]
    assert shown != messages.privacy_text(_WEB, linked=True).plain_text()
    assert shown.endswith(f"Full policy: {_WEB}/privacy")
    assert "link this chat to a website account" in shown and "/link CODE" in shown
    assert "/unlink" not in shown and "Profile page" not in shown
    assert messages.LINK_FIRST_TEXT not in shown
    assert supabase.auth.admin.get_user_by_id_calls == [USER_ID]  # who they are, and nothing else
    assert supabase.rpc_calls == []


async def test_a_chat_the_bot_made_an_account_for_gets_the_summary_without_a_web_address(
    no_web_url: None,
) -> None:
    renderer, _s, _r = await _handle(inbound("/privacy"), supabase=_unlinked())

    assert renderer.texts[0].endswith(messages.PRIVACY_NO_LINK_TEXT)
    assert "http" not in renderer.texts[0]


async def test_an_unlinked_chat_is_told_how_to_link_and_nothing_is_read(
    web_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def never(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("an unlinked chat's data must not be read")

    monkeypatch.setattr(channel_core, "load_first_run_facts", never)

    renderer, _supabase, _registry = await _handle(inbound("/learn"), supabase=_unlinked())

    assert renderer.texts == [messages.link_first_text(_WEB)]
    assert "/link CODE" in renderer.texts[0]
    assert renderer.texts[0].endswith(f"{_WEB}/profile/integrations")


async def test_the_link_prompt_without_a_web_address_carries_no_link(no_web_url: None) -> None:
    renderer, _supabase, _registry = await _handle(inbound("/learn"), supabase=_unlinked())

    assert renderer.texts == [messages.LINK_FIRST_TEXT]


class _Down:
    """A table that cannot be read: every query against it fails."""

    def select(self, *_a: Any, **_k: Any) -> Any:
        raise ConnectionError("down")


def _learn_supabase(
    *,
    profile: bool = False,
    key_validated: bool | None = None,
    saved_searches: int = 0,
    applications: list[dict[str, Any]] | None = None,
    resume_for: list[str] | None = None,
) -> ComposedSupabase:
    from between_jobs.api.artifact_versions_store import artifact_id_for

    supabase = _linked(
        profile_versions=webhook_fakes._FakeProfileVersionsTable(
            select_rows=[{"id": "v1", "activated_at": "2026-10-01T00:00:00Z"}] if profile else []
        ),
        applications=webhook_fakes._FakeSimpleTable(select_rows=applications or []),
    )
    supabase._extra["provider_credentials"] = prepare_fakes._FakeTable(
        select_rows=[]
        if key_validated is None
        else [{"service": "llm", "provider": "openrouter", "is_validated": key_validated}]
    )
    supabase._extra["saved_searches"] = prepare_fakes._FakeTable(
        select_rows=[{"id": f"s{i}"} for i in range(saved_searches)]
    )
    supabase._extra["artifact_versions"] = prepare_fakes._FakeTable(
        select_rows=[{"artifact_id": artifact_id_for(a, "resume")} for a in resume_for or []]
    )
    return supabase


async def test_learn_for_a_new_account_shows_five_steps_and_points_at_the_first(
    web_url: str,
) -> None:
    renderer, _supabase, _registry = await _handle(inbound("/learn"), supabase=_learn_supabase())

    assert len(renderer.sent) == 1
    shown = renderer.texts[0]
    assert shown.splitlines()[0] == "📋 Getting started -- 0 of 5 done"
    assert shown.splitlines()[2:7] == [
        "⬜ Add your profile",
        "⬜ Add a model key",
        "❓ Run a first search -- I can't see searches you run in your browser",
        "⬜ Track a job",
        "⬜ Generate a resume",
    ]
    assert "Next: Add your profile" in shown
    assert shown.endswith(f"On the website: {_WEB}/profile")
    assert renderer.sent[0][1].buttons == ()


async def test_learn_ticks_what_the_account_holds_and_names_the_next_step_to_take(
    web_url: str,
) -> None:
    supabase = _learn_supabase(
        profile=True,
        key_validated=True,
        saved_searches=1,
        applications=[{**webhook_fakes._APP_1, "id": "app-1", "source_channel": "telegram"}],
    )

    renderer, _s, _r = await _handle(inbound("/learn"), supabase=supabase)

    lines = renderer.texts[0].splitlines()
    assert lines[0] == "📋 Getting started -- 4 of 5 done"
    assert lines[2:7] == [
        "✅ Add your profile",
        "✅ Add a model key",
        "✅ Run a first search",
        "✅ Track a job",
        "⬜ Generate a resume",
    ]
    assert "Next: Generate a resume" in renderer.texts[0]
    assert renderer.texts[0].endswith(f"{_WEB}/applications")


async def test_learn_when_everything_is_done_says_so(no_web_url: None) -> None:
    supabase = _learn_supabase(
        profile=True,
        key_validated=True,
        saved_searches=2,
        applications=[{**webhook_fakes._APP_1, "id": "app-1", "source_channel": "web"}],
        resume_for=["app-1"],
    )

    renderer, _s, _r = await _handle(inbound("/learn"), supabase=supabase)

    assert renderer.texts[0].splitlines()[0] == "📋 Getting started -- 5 of 5 done"
    assert renderer.texts[0].endswith("All five are done -- you're set up.")
    assert "Next:" not in renderer.texts[0]


async def test_learn_without_a_web_address_says_which_website_page(no_web_url: None) -> None:
    renderer, _s, _r = await _handle(inbound("learn"), supabase=_learn_supabase())

    assert renderer.texts[0].endswith("On the website: the Profile page.")
    assert "http" not in renderer.texts[0]


# Where the chat points a person when it has no website address to link: the hint for each step
# it can name as next, then the page. Pinned for every one of them, so a step cannot be sent to
# another step's page or told another step's instruction.
_PROFILE_TAIL = (
    'Send "set up my resume" here for the template, or import your resume on the website.\n'
    "On the website: the Profile page."
)
_KEY_TAIL = (
    "Paste your own model key on the website. This app never runs on a shared key.\n"
    "On the website: the Integrations page (under Profile)."
)
_TRACK_TAIL = (
    'Send me a job here ("track a job" shows the format), or paste one on the website.\n'
    "On the website: the Applications page."
)
_GENERATE_TAIL = (
    'Tap "Generate resume" under a tracked job, or send "list" and then "apply to #N".\n'
    "On the website: the Applications page."
)


def _a_tracked_job(channel: str) -> list[dict[str, Any]]:
    return [{**webhook_fakes._APP_1, "id": "app-1", "source_channel": channel}]


@pytest.mark.parametrize(
    ("account", "next_step", "tail"),
    [
        pytest.param({}, "Add your profile", _PROFILE_TAIL, id="profile"),
        pytest.param({"profile": True}, "Add a model key", _KEY_TAIL, id="model_key"),
        pytest.param(
            {"profile": True, "key_validated": True, "saved_searches": 1},
            "Track a job",
            _TRACK_TAIL,
            id="track_job",
        ),
        pytest.param(
            {
                "profile": True,
                "key_validated": True,
                "saved_searches": 1,
                "applications": _a_tracked_job("telegram"),
            },
            "Generate a resume",
            _GENERATE_TAIL,
            id="generate_resume",
        ),
    ],
)
async def test_learn_without_a_web_address_names_the_right_hint_and_page_for_every_step(
    account: dict[str, Any], next_step: str, tail: str, no_web_url: None
) -> None:
    renderer, _s, _r = await _handle(inbound("/learn"), supabase=_learn_supabase(**account))

    shown = renderer.texts[0]
    assert f"Next: {next_step}\n" in shown
    assert shown.endswith(tail)
    assert "http" not in shown


def test_the_first_search_step_has_its_own_hint_and_page_even_though_a_chat_never_names_it() -> (
    None
):
    """A chat cannot know the first search was NOT done, so it never names it as next. The
    wording exists for the view that can (the web's, which remembers a search in the browser):
    it is pinned here so it stays right."""
    facts = FirstRunFacts(
        profile=True,
        model_key=True,
        saved_searches=0,
        applications=ApplicationFacts(count=0, from_discover=0, with_resume=0),
    )
    view = derive_first_run(facts, False)  # the browser remembers no search: it is todo
    assert view.next_step is not None and view.next_step.id == "first_search"

    assert (
        messages.learn_text(view, facts, None)
        .plain_text()
        .endswith(
            "Search for a role on the website, or leave the filters empty to browse the latest "
            "postings.\nOn the website: the Discover page."
        )
    )
    assert (
        messages.learn_text(view, facts, _WEB)
        .plain_text()
        .endswith(f"On the website: {_WEB}/discover")
    )


async def test_an_application_tracked_in_the_chat_does_not_show_a_first_search_was_run(
    no_web_url: None,
) -> None:
    """The bot itself tracks applications (`source_channel` "telegram"). One of those, and no
    saved search, says nothing about a search: the step stays unknown, never done."""
    supabase = _learn_supabase(
        profile=True, key_validated=True, saved_searches=0, applications=_a_tracked_job("telegram")
    )

    renderer, _s, _r = await _handle(inbound("/learn"), supabase=supabase)

    lines = renderer.texts[0].splitlines()
    assert lines[0] == "📋 Getting started -- 3 of 5 done"
    assert lines[2:7] == [
        "✅ Add your profile",
        "✅ Add a model key",
        "❓ Run a first search -- I can't see searches you run in your browser",
        "✅ Track a job",
        "⬜ Generate a resume",
    ]


async def test_an_application_tracked_from_discover_shows_a_first_search_was_run(
    no_web_url: None,
) -> None:
    supabase = _learn_supabase(
        profile=True, key_validated=True, saved_searches=0, applications=_a_tracked_job("discover")
    )

    renderer, _s, _r = await _handle(inbound("/learn"), supabase=supabase)

    lines = renderer.texts[0].splitlines()
    assert lines[0] == "📋 Getting started -- 4 of 5 done"
    assert "✅ Run a first search" in lines


async def test_a_validated_key_is_what_counts_not_a_saved_one(no_web_url: None) -> None:
    supabase = _learn_supabase(profile=True, key_validated=False)

    renderer, _s, _r = await _handle(inbound("/learn"), supabase=supabase)

    assert "⬜ Add a model key" in renderer.texts[0]
    assert "Next: Add a model key" in renderer.texts[0]


async def test_learn_says_unknown_for_what_it_could_not_read_and_never_names_it_next(
    no_web_url: None, caplog: pytest.LogCaptureFixture
) -> None:
    supabase = _learn_supabase(profile=True, key_validated=True)
    supabase._extra["applications"] = _Down()
    supabase._extra["saved_searches"] = _Down()

    with caplog.at_level(logging.WARNING, logger="between_jobs.api.first_run"):
        renderer, _s, _r = await _handle(inbound("/learn"), supabase=supabase)

    shown = renderer.texts[0]
    assert "✅ Add your profile" in shown and "✅ Add a model key" in shown
    assert "❓ Run a first search -- couldn't check just now" in shown
    assert "❓ Track a job -- couldn't check just now" in shown
    assert "❓ Generate a resume -- couldn't check just now" in shown
    assert "Next:" not in shown  # nothing KNOWN to be todo
    assert shown.endswith(
        "Everything I could check is done. "
        "I couldn't confirm: Run a first search, Track a job, Generate a resume."
    )
    assert {r.ctx["fact"] for r in caplog.records} == {"saved_searches", "applications"}  # type: ignore[attr-defined]


@pytest.mark.parametrize("broken", ["applications", "saved_searches"])
async def test_learn_blames_the_browser_only_when_everything_that_could_show_a_search_was_read(
    broken: str, no_web_url: None
) -> None:
    """The note "I can't see searches you run in your browser" is for a first search that could
    not be told from the account. When one of the two things that could have shown it failed to
    be read, the honest reason is that the read failed, not a limit of the chat."""
    supabase = _learn_supabase(profile=True, key_validated=True)
    supabase._extra[broken] = _Down()

    renderer, _s, _r = await _handle(inbound("/learn"), supabase=supabase)

    shown = renderer.texts[0]
    assert "❓ Run a first search -- couldn't check just now" in shown
    assert "I can't see searches you run in your browser" not in shown
    if broken == "applications":
        assert "❓ Track a job -- couldn't check just now" in shown
        assert "❓ Generate a resume -- couldn't check just now" in shown


async def test_learn_is_all_our_own_words_so_nothing_a_person_stored_can_reach_it(
    web_url: str,
) -> None:
    """A job title, a company or a profile field is attacker-controlled text elsewhere in the
    bot. /learn reads counts and flags and prints constants, so none of it is in the message."""
    hostile = "<b>Boss</b> & <script>"
    supabase = _learn_supabase(
        profile=True,
        applications=[
            {**webhook_fakes._APP_1, "id": "app-1", "source_channel": "discover", "title": hostile}
        ],
    )

    renderer, _s, _r = await _handle(inbound("/learn"), supabase=supabase)

    assert "Boss" not in renderer.texts[0] and "script" not in renderer.texts[0]
    assert all(seg.style in {None, "bold"} for seg in renderer.sent[0][1].text.segments)


async def test_learn_reads_nothing_it_is_not_entitled_to_and_writes_nothing() -> None:
    supabase = _learn_supabase(profile=True)

    await _handle(inbound("/learn"), supabase=supabase)

    assert supabase.rpc_calls == []  # no write goes through an RPC
    for table in ("applications", "profile_versions"):
        assert getattr(supabase, table).insert_calls == []


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
