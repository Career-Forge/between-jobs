"""The Discord adapter (api/discord_adapter.py): interactions in, requests out, the proactive side.

Synthetic fixtures throughout (tests/discord_fakes.py explains why there is nothing to capture)."""

from __future__ import annotations

import logging
import re
from types import SimpleNamespace
from typing import Any

import pytest
from discord_fakes import (
    BOT_TOKEN,
    DM_CHANNEL,
    GUILD_CHANNEL,
    GUILD_ID,
    SUBJECT,
    FakeDiscord,
    attachment_resolved,
    button,
    command,
    discord_client,
    fresh_token,
    ping,
)

from between_jobs.api import channel_messages as messages
from between_jobs.api.channel_envelope import (
    AckCallback,
    Attachment,
    AttachmentTooLarge,
    Button,
    EditMessage,
    MessageRef,
    Say,
    SendDocument,
    bold,
    rich,
)
from between_jobs.api.discord_adapter import (
    DiscordInteractionRenderer,
    DiscordNotifier,
    InteractionKind,
    MalformedInteraction,
    build_notifier,
    build_renderer,
    components_for,
    deferred_response,
    immediate_response,
    interaction_id_of,
    interaction_kind,
    is_bot_dm,
    message_bodies,
    parse_interaction,
    pong_response,
    safe_filename,
)
from between_jobs.api.discord_client import (
    AttachmentRefused,
    DiscordApiError,
    DiscordTokenExpired,
)
from between_jobs.api.discord_markdown import utf16_length
from between_jobs.api.intents import (
    Intent,
    classify,
    is_unlink_command,
    looks_like_job_paste,
    looks_like_json_payload,
    parse_apply_reference,
    parse_job_paste,
    parse_link_code,
)

# -- parsing: what a command becomes -----------------------------------------------------------


def _text(name: str, options: dict[str, Any] | None = None, **kwargs: Any) -> str:
    message = parse_interaction(command(name, options, **kwargs))
    assert message is not None
    return message.text


def test_link_becomes_the_sentence_the_telegram_bot_gets_and_the_real_parser_reads_it() -> None:
    text = _text("link", {"code": "ABCD2345"})
    assert text == "/link ABCD2345"
    assert parse_link_code(text) == "ABCD2345"


def test_a_code_is_kept_exactly_as_typed_apart_from_stray_spaces() -> None:
    assert _text("link", {"code": " AB CD\n2345 "}) == "/link ABCD2345"
    assert _text("link", {"code": "abcd2345"}) == "/link abcd2345"  # case is the person's
    assert _text("link", {"code": "   "}) == "/link"
    assert parse_link_code(_text("link", {"code": "   "})) is None


def test_the_other_simple_commands_become_the_texts_the_intent_parser_knows() -> None:
    assert is_unlink_command(_text("unlink"))
    assert classify(_text("list")) is Intent.LIST_APPLICATIONS
    assert classify(_text("setup")) is Intent.SETUP_HELP
    assert classify(_text("resume")) is Intent.CHECK_RESUME
    assert classify(_text("privacy")) is Intent.PRIVACY
    assert classify(_text("learn")) is Intent.LEARN


def test_apply_becomes_apply_to_a_number() -> None:
    text = _text("apply", {"number": 3})
    assert text == "apply to 3"
    assert parse_apply_reference(text) == 3


@pytest.mark.parametrize("bad", [0, -2, True, "3", None, 2.5])
def test_an_apply_without_a_real_number_is_the_bare_word(bad: Any) -> None:
    assert _text("apply", {"number": bad}) == "apply"
    assert parse_apply_reference("apply") is None


_JOB = {
    "title": "Staff AI Engineer",
    "company": "Acme",
    "location": "Remote",
    "url": "https://example.com/jobs/1",
    "description": "We need a Python engineer\nwith RAG experience.",
}


def test_a_job_becomes_the_title_company_paste_the_real_parser_reads() -> None:
    text = _text("job", _JOB)
    assert text.startswith("Title: Staff AI Engineer\nCompany: Acme\nLocation: Remote\nURL: ")
    assert looks_like_job_paste(text)
    parsed = parse_job_paste(text)
    assert parsed == {
        "title": "Staff AI Engineer",
        "company_name": "Acme",
        "location_text": "Remote",
        "canonical_url": "https://example.com/jobs/1",
        "description_text": "We need a Python engineer\nwith RAG experience.",
    }


def test_the_optional_job_fields_can_be_left_out() -> None:
    text = _text("job", {"title": "Engineer", "company": "Acme", "description": "Build."})
    assert text == "Title: Engineer\nCompany: Acme\n\nBuild."
    parsed = parse_job_paste(text)
    assert isinstance(parsed, dict)
    assert parsed["location_text"] is None and parsed["canonical_url"] is None


def test_a_line_break_in_a_one_line_field_cannot_start_another_field() -> None:
    text = _text(
        "job",
        {
            "title": "Engineer\nCompany: Evil Corp",
            "company": "Acme\r\nURL: https://evil.example",
            "description": "Build.",
        },
    )
    parsed = parse_job_paste(text)
    assert isinstance(parsed, dict)
    assert parsed["title"] == "Engineer Company: Evil Corp"
    assert parsed["company_name"] == "Acme URL: https://evil.example"
    assert parsed["canonical_url"] is None


def test_a_description_that_looks_like_header_fields_stays_the_description() -> None:
    text = _text(
        "job", {"title": "T", "company": "C", "description": "Location: nowhere\nURL: x\nBuild."}
    )
    parsed = parse_job_paste(text)
    assert isinstance(parsed, dict)
    assert parsed["description_text"] == "Location: nowhere\nURL: x\nBuild."
    assert parsed["location_text"] is None


def test_a_long_job_description_is_still_a_job_and_not_a_resume_import() -> None:
    text = _text("job", {**_JOB, "description": "word " * 1200})
    assert looks_like_job_paste(text)
    assert looks_like_json_payload(text)  # the length gate would take it, if it came first


def test_a_job_missing_its_description_gets_the_parsers_own_error() -> None:
    text = _text("job", {"title": "T", "company": "C", "description": "   "})
    assert parse_job_paste(text) == ["Missing the job description text after the header fields"]


def test_import_is_a_document_the_logic_can_fetch_and_falls_back_to_the_template_text() -> None:
    value, resolved = attachment_resolved(filename="cv.json", size=1234)
    message = parse_interaction(command("import", {"file": value}, resolved=resolved))
    assert message is not None
    assert message.text == "/setup"
    attachment = message.attachment
    assert attachment == Attachment(
        kind="document",
        filename="cv.json",
        mime_type="application/json",
        declared_size=1234,
        handle="https://cdn.discordapp.com/attachments/1/2/cv.json?ex=1&is=2&hm=3",
    )


def test_an_import_with_odd_attachment_data_never_raises() -> None:
    value, resolved = attachment_resolved(content_type=None)
    resolved["attachments"][value]["size"] = "big"
    message = parse_interaction(command("import", {"file": value}, resolved=resolved))
    assert message is not None and message.attachment is not None
    assert message.attachment.mime_type == "" and message.attachment.declared_size is None

    for options, resolved_block in (
        ({"file": value}, None),
        ({"file": "999"}, resolved),
        ({}, resolved),
        ({"file": 5}, {"attachments": "nope"}),
    ):
        message = parse_interaction(command("import", options, resolved=resolved_block))
        assert message is not None and message.attachment is None and message.text == "/setup"


def test_a_command_the_app_does_not_define_is_not_acted_on() -> None:
    assert parse_interaction(command("somethingelse")) is None
    assert parse_interaction(command("LINK")) is None  # names are lower case


def test_options_of_other_commands_are_ignored() -> None:
    assert _text("list", {"code": "XXXX"}) == "list"


# -- parsing: who, where, which delivery --------------------------------------------------------


def test_the_sender_the_conversation_and_the_delivery_are_three_different_values() -> None:
    message = parse_interaction(command("list", interaction_id="1100000000000000042"))
    assert message is not None
    assert message.channel == "discord"
    assert message.subject == SUBJECT
    assert message.chat_ref == DM_CHANNEL
    assert message.update_id == "1100000000000000042"
    assert len({message.subject, message.chat_ref, message.update_id}) == 3
    assert message.message_id is None and message.callback is None and message.attachment is None


def test_in_a_server_the_sender_is_the_member_s_user() -> None:
    payload = command("list", dm=False, user_id="777000999888777666")
    message = parse_interaction(payload)
    assert message is not None
    assert message.subject == "777000999888777666"
    assert message.is_private is False
    assert message.chat_ref == GUILD_CHANNEL  # the channel it came from, not the server's id


def test_the_member_wins_over_a_top_level_user() -> None:
    payload = command("list", dm=False, user_id="111")
    payload["user"] = {"id": "222"}
    message = parse_interaction(payload)
    assert message is not None and message.subject == "111"


@pytest.mark.parametrize(
    ("guild", "context", "private"),
    [
        (None, None, True),  # a direct message
        (None, 1, True),  # BOT_DM
        (None, 2, False),  # a group chat or someone else's DM
        (None, 0, False),
        (GUILD_ID, None, False),
        (GUILD_ID, 1, False),
    ],
)
def test_only_the_direct_message_with_the_app_is_private(
    guild: str | None, context: int | None, private: bool
) -> None:
    payload = command("list")
    payload.pop("context")
    if context is not None:
        payload["context"] = context
    if guild is not None:
        payload["guild_id"] = guild
    assert is_bot_dm(payload) is private
    message = parse_interaction(payload)
    assert message is not None and message.is_private is private


def test_the_conversation_falls_back_to_the_channel_object_and_then_to_nothing() -> None:
    payload = command("list")
    del payload["channel_id"]
    message = parse_interaction(payload)
    assert message is not None and message.chat_ref == DM_CHANNEL
    del payload["channel"]
    message = parse_interaction(payload)
    assert message is not None and message.chat_ref == ""


# -- parsing: buttons --------------------------------------------------------------------------


def test_a_button_tap_is_a_callback_carrying_the_buttons_own_data() -> None:
    data = "app:prepare:30000000-0000-0000-0000-000000000001"
    message = parse_interaction(
        button(data, interaction_id="1100000000000000077", message_id="1200000000000000009")
    )
    assert message is not None
    assert message.text == "" and message.attachment is None
    assert message.callback is not None
    assert message.callback.id == "1100000000000000077"
    assert message.callback.data == data
    assert message.callback.message_ref == MessageRef(DM_CHANNEL, "1200000000000000009")
    assert message.subject == SUBJECT and message.is_private


def test_a_component_that_is_not_a_button_is_not_acted_on() -> None:
    payload = button("x")
    payload["data"]["component_type"] = 3  # a select menu
    assert parse_interaction(payload) is None


def test_a_tap_without_data_or_message_still_parses_with_empty_data() -> None:
    payload = button("x")
    del payload["data"]["custom_id"]
    del payload["message"]
    message = parse_interaction(payload)
    assert message is not None and message.callback is not None
    assert message.callback.data == "" and message.callback.message_ref is None


# -- parsing: what is not ours, and what is broken ----------------------------------------------


@pytest.mark.parametrize("kind", [1, 4, 5, 6, 99, None, "2"])
def test_pings_autocompletes_modals_and_unknown_types_are_not_acted_on(kind: Any) -> None:
    payload = command("list")
    payload["type"] = kind
    assert parse_interaction(payload) is None
    assert interaction_kind({"type": 1}) is InteractionKind.PING
    assert interaction_kind({"type": kind}) in (InteractionKind.PING, InteractionKind.OTHER)


def test_the_kinds_the_webhook_dispatches_on() -> None:
    assert interaction_kind(ping()) is InteractionKind.PING
    assert interaction_kind(command("list")) is InteractionKind.COMMAND
    assert interaction_kind(button("x")) is InteractionKind.COMPONENT
    assert interaction_kind({"type": 4}) is InteractionKind.OTHER


def test_the_interaction_id_is_only_a_snowflake_string() -> None:
    assert interaction_id_of({"id": "1100000000000000001"}) == "1100000000000000001"
    for bad in (None, 5, "", "abc", "1 2", "1" * 26):
        assert interaction_id_of({"id": bad}) is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("id"),
        lambda p: p.__setitem__("id", "not-a-number"),
        lambda p: p.__setitem__("id", 12345),
        lambda p: (p.pop("user"), None),
        lambda p: p.__setitem__("user", "someone"),
        lambda p: p.__setitem__("user", {"id": "x"}),
        lambda p: p.__setitem__("user", {}),
        lambda p: p.pop("data"),
        lambda p: p.__setitem__("data", "list"),
        lambda p: p["data"].pop("name"),
        lambda p: p["data"].__setitem__("name", 7),
    ],
)
def test_a_command_that_lacks_what_every_command_has_raises_without_quoting_it(
    mutate: Any,
) -> None:
    payload = command("list", token="SECRETTOKEN" * 4)
    mutate(payload)
    with pytest.raises(MalformedInteraction) as caught:
        parse_interaction(payload)
    assert "SECRETTOKEN" not in str(caught.value) and "someone" not in str(caught.value)


def test_options_that_are_not_well_formed_are_skipped_not_fatal() -> None:
    payload = command("link", {"code": "ABCD2345"})
    payload["data"]["options"] = ["junk", {"name": 5}, {"name": "code", "value": "ABCD2345"}]
    message = parse_interaction(payload)
    assert message is not None and message.text == "/link ABCD2345"


# -- what is sent back at once --------------------------------------------------------------------


def test_the_http_answers_that_buy_time() -> None:
    assert pong_response() == {"type": 1}
    assert deferred_response(InteractionKind.COMMAND, ephemeral=False) == {"type": 5}
    assert deferred_response(InteractionKind.COMMAND, ephemeral=True) == {
        "type": 5,
        "data": {"flags": 64},
    }
    # A button tap is a silent deferral of an update; its visibility is the message's own.
    assert deferred_response(InteractionKind.COMPONENT, ephemeral=True) == {"type": 6}
    assert deferred_response(InteractionKind.COMPONENT, ephemeral=False) == {"type": 6}


def test_an_immediate_answer_pings_nobody_and_is_ephemeral_by_default() -> None:
    answer = immediate_response("<@123> @everyone I can't do that.")
    assert answer["type"] == 4
    data = answer["data"]
    assert data["flags"] == 64 and data["allowed_mentions"] == {"parse": []}
    assert "<@" not in data["content"].replace("\\<@", "")
    assert "flags" not in immediate_response("ok", ephemeral=False)["data"]


# -- buttons -----------------------------------------------------------------------------------


def test_the_bots_own_keyboards_become_valid_action_rows_with_the_same_data() -> None:
    for rows in (
        messages.preview_keyboard("11111111-2222-3333-4444-555555555555"),
        messages.prepare_keyboard("11111111-2222-3333-4444-555555555555"),
        messages.mark_applied_keyboard("11111111-2222-3333-4444-555555555555"),
    ):
        rendered = components_for(rows)
        assert [b["custom_id"] for row in rendered for b in row["components"]] == [
            button.data for row in rows for button in row
        ]
        for row in rendered:
            assert row["type"] == 1 and 1 <= len(row["components"]) <= 5
            for item in row["components"]:
                assert item["type"] == 2 and 1 <= utf16_length(item["label"]) <= 80
                assert 1 <= len(item["custom_id"]) <= 100


def test_the_first_button_of_a_row_is_primary_and_the_rest_secondary() -> None:
    row = components_for(((Button("a", "1"), Button("b", "2")),))[0]["components"]
    assert [item["style"] for item in row] == [1, 2]


def test_a_row_of_more_than_five_buttons_becomes_more_rows() -> None:
    rows = components_for((tuple(Button(f"b{i}", f"d{i}") for i in range(7)),))
    assert [len(row["components"]) for row in rows] == [5, 2]


def _label(text: str) -> str:
    label = components_for(((Button(text, "d"),),))[0]["components"][0]["label"]
    assert isinstance(label, str)
    return label


def test_a_label_is_cleaned_like_any_other_text() -> None:
    """A label can carry a job or company name. A direction override is dropped, and a lone
    surrogate (which a JSON body cannot hold: encoding it raises) becomes the replacement mark."""
    label = _label("ab\u202ecd\ud800 ")
    assert label == "abcd\ufffd"
    label.encode("utf-8")  # does not raise


def test_a_label_is_held_to_80_utf16_units_not_80_characters() -> None:
    assert _label("😀" * 40) == "😀" * 40  # exactly 80 units: untouched
    for count in (41, 80, 200):
        label = _label("😀" * count)
        assert utf16_length(label) <= 80 and label.endswith("…")
        assert label.removesuffix("…") == "😀" * (len(label) - 1)  # whole characters only
    mixed = _label("a" + "😀" * 100)
    assert utf16_length(mixed) <= 80 and mixed.startswith("a😀")


def test_limits_are_held() -> None:
    assert components_for(()) == []
    long_label = components_for(((Button("x" * 300, "d"),),))[0]["components"][0]["label"]
    assert utf16_length(long_label) == 80 and long_label.endswith("…")
    for bad in (
        ((Button("", "d"),),),
        ((Button("  ", "d"),),),
        ((Button("ok", ""),),),
        ((Button("ok", "d" * 101),),),
        tuple((Button("ok", f"d{i}"),) for i in range(6)),  # six rows
        (tuple(Button("ok", f"d{i}") for i in range(26)),),  # 26 buttons -> six rows
    ):
        with pytest.raises(ValueError):
            components_for(bad)
    # exactly at the limits is fine
    assert components_for(((Button("ok", "d" * 100),),))
    assert len(components_for(tuple((Button("ok", f"d{i}"),) for i in range(5)))) == 5


# -- message bodies ----------------------------------------------------------------------------


def test_every_body_pings_nobody() -> None:
    for body in message_bodies(rich("@everyone <@1> ", bold("<@&2>")), (), edit=True):
        assert body["allowed_mentions"] == {"parse": []}


def test_buttons_ride_on_the_last_part_and_an_edit_clears_the_first() -> None:
    text = rich(("para " * 80 + "\n\n") * 8)
    buttons = messages.prepare_keyboard("a" * 36)
    sent = message_bodies(text, buttons)
    assert len(sent) >= 2
    assert all("components" not in body for body in sent[:-1])
    assert sent[-1]["components"][0]["components"][0]["custom_id"] == "app:prepare:" + "a" * 36
    edited = message_bodies(text, buttons, edit=True)
    assert edited[0]["components"] == [] and "components" in edited[-1]
    single = message_bodies(rich("short"), (), edit=True)
    assert single[0]["components"] == []  # how an edit removes the buttons a message had
    assert "components" not in message_bodies(rich("short"))[0]


def test_the_message_is_localized_into_discords_words() -> None:
    (body,) = message_bodies(rich(messages.NO_APPLICATIONS_TEXT))
    assert "/job" in body["content"] and "Send" not in body["content"]


# -- the renderer --------------------------------------------------------------------------------


def _renderer(
    fake: FakeDiscord,
    *,
    replaces_original: bool = True,
    ephemeral: bool = False,
    bot_token: str | None = BOT_TOKEN,
    token: str | None = None,
) -> DiscordInteractionRenderer:
    return DiscordInteractionRenderer(
        discord_client(fake, bot_token=bot_token),
        token=token or fresh_token(),
        ephemeral=ephemeral,
        replaces_original=replaces_original,
        recipient_id=SUBJECT,
    )


async def test_a_commands_first_message_replaces_the_thinking_placeholder_and_the_rest_follow() -> (
    None
):
    fake = FakeDiscord()
    renderer = _renderer(fake)

    first = await renderer.send(DM_CHANNEL, Say(rich("one")))
    second = await renderer.send(DM_CHANNEL, Say(rich("two")))

    assert [r.kind for r in fake.requests] == ["edit_original", "followup"]
    assert first == MessageRef(DM_CHANNEL, "@original")
    assert second is not None and second.message_id.isdigit()
    assert fake.contents == ["one", "two"]
    assert renderer.answered


async def test_a_button_taps_messages_are_all_new_messages() -> None:
    fake = FakeDiscord()
    renderer = _renderer(fake, replaces_original=False)

    await renderer.send(DM_CHANNEL, Say(rich("one")))
    await renderer.send(DM_CHANNEL, Say(rich("two")))

    assert [r.kind for r in fake.requests] == ["followup", "followup"]
    assert renderer.answered  # nothing is waiting to be replaced


async def test_an_edit_patches_the_named_message() -> None:
    fake = FakeDiscord()
    renderer = _renderer(fake)
    first = await renderer.send(DM_CHANNEL, Say(rich("working")))
    second = await renderer.send(DM_CHANNEL, Say(rich("other")))
    assert first is not None and second is not None

    await renderer.edit(EditMessage(first, rich("compiling")))
    await renderer.edit(EditMessage(second, rich("done"), messages.mark_applied_keyboard("a" * 36)))

    patch_original, patch_followup = fake.requests[-2:]
    assert patch_original.kind == "edit_original"
    assert patch_original.json_body == {
        "content": "compiling",
        "allowed_mentions": {"parse": []},
        "components": [],
    }
    assert patch_followup.kind == "edit_followup"
    assert patch_followup.path.endswith(f"/messages/{second.message_id}")
    assert patch_followup.json_body is not None
    assert patch_followup.json_body["components"][0]["components"][0]["custom_id"].startswith(
        "app:stage:"
    )


async def test_an_edit_that_is_too_long_edits_the_first_part_and_follows_up_with_the_rest() -> None:
    fake = FakeDiscord()
    renderer = _renderer(fake)
    ref = await renderer.send(DM_CHANNEL, Say(rich("working")))
    assert ref is not None
    text = rich(("para " * 80 + "\n\n") * 8)

    await renderer.edit(EditMessage(ref, text, messages.prepare_keyboard("a" * 36)))

    edit, *followups = fake.requests[1:]
    assert edit.kind == "edit_original" and followups
    assert all(r.kind == "followup" for r in followups)
    assert edit.json_body is not None and edit.json_body["components"] == []
    assert followups[-1].json_body is not None and "components" in followups[-1].json_body
    for request in (edit, *followups):
        assert request.json_body is not None
        assert len(request.json_body["content"]) <= 2000


async def test_a_long_first_message_edits_the_placeholder_then_follows_up() -> None:
    fake = FakeDiscord()
    renderer = _renderer(fake)

    await renderer.send(DM_CHANNEL, Say(rich(("para " * 80 + "\n\n") * 8)))

    kinds = [r.kind for r in fake.requests]
    assert kinds[0] == "edit_original" and set(kinds[1:]) == {"followup"} and len(kinds) >= 2


async def test_every_request_pings_nobody_and_a_hostile_name_is_text() -> None:
    fake = FakeDiscord()
    renderer = _renderer(fake)
    hostile = "@everyone <@123> **boom** `x` ||spoiler|| [a](https://evil.example)"

    await renderer.send(DM_CHANNEL, Say(messages.job_tracked_text(hostile, hostile)))
    await renderer.send(DM_CHANNEL, Say(rich(hostile)))
    await renderer.send_document(DM_CHANNEL, SendDocument("resume.pdf", b"%PDF", caption=hostile))
    ref = await renderer.send(DM_CHANNEL, Say(rich("progress")))
    assert ref is not None
    await renderer.edit(EditMessage(ref, rich(hostile)))

    assert len(fake.requests) == 5
    for request in fake.requests:
        body = request.json_body
        assert body is not None and body["allowed_mentions"] == {"parse": []}
        content = body.get("content", "")
        left = re.sub(r"\\.", "", content, flags=re.DOTALL)  # what is not escaped
        assert "@everyone" not in content and "<" not in left and "[" not in left
        assert "**boom**" not in content and "||" not in left


async def test_a_follow_up_is_ephemeral_outside_the_direct_message_and_a_patch_never_says_so() -> (
    None
):
    fake = FakeDiscord()
    renderer = _renderer(fake, ephemeral=True)

    ref = await renderer.send(DM_CHANNEL, Say(rich("a")))
    await renderer.send(DM_CHANNEL, Say(rich("b")))
    await renderer.send_document(DM_CHANNEL, SendDocument("resume.pdf", b"%PDF"))
    assert ref is not None
    await renderer.edit(EditMessage(ref, rich("c")))

    patches = fake.of("edit_original")
    followups = fake.of("followup")
    assert len(patches) == 2 and len(followups) == 2
    assert all(r.json_body is not None and "flags" not in r.json_body for r in patches)
    assert all(r.json_body is not None and r.json_body["flags"] == 64 for r in followups)


async def test_in_the_direct_message_nothing_is_ephemeral() -> None:
    fake = FakeDiscord()
    renderer = _renderer(fake, ephemeral=False)
    await renderer.send(DM_CHANNEL, Say(rich("a")))
    await renderer.send(DM_CHANNEL, Say(rich("b")))
    assert all("flags" not in (r.json_body or {}) for r in fake.requests)


async def test_a_document_is_a_multipart_follow_up_with_its_caption_as_text() -> None:
    fake = FakeDiscord()
    renderer = _renderer(fake, replaces_original=False)
    caption = "📄 Resume -- ATS score 72/100\n• a *starred* warning"

    ref = await renderer.send_document(
        DM_CHANNEL, SendDocument("../my resume?.pdf", b"%PDF-1.5 bytes", caption=caption)
    )

    (request,) = fake.requests
    assert request.kind == "followup" and ref is not None
    assert request.files == {"my_resume_.pdf": b"%PDF-1.5 bytes"}
    assert request.json_body is not None
    assert request.json_body["attachments"] == [{"id": 0, "filename": "my_resume_.pdf"}]
    assert (
        request.json_body["content"] == "📄 Resume -- ATS score 72/100\n• a \\*starred\\* warning"
    )


async def test_a_document_that_is_the_first_thing_said_takes_the_placeholders_place() -> None:
    fake = FakeDiscord()
    renderer = _renderer(fake)

    await renderer.send_document(DM_CHANNEL, SendDocument("resume.pdf", b"%PDF"))

    (request,) = fake.requests
    assert request.kind == "edit_original" and request.files == {"resume.pdf": b"%PDF"}
    assert renderer.answered


async def test_a_caption_of_exactly_the_limit_is_kept_whole_and_one_over_is_cut_by_one() -> None:
    fake = FakeDiscord()
    renderer = _renderer(fake, replaces_original=False)

    await renderer.send_document(DM_CHANNEL, SendDocument("a.pdf", b"x", caption="a" * 2000))
    await renderer.send_document(DM_CHANNEL, SendDocument("b.pdf", b"x", caption="a" * 2500))

    exact, over = fake.requests
    assert exact.json_body is not None and exact.json_body["content"] == "a" * 2000
    assert over.json_body is not None and over.json_body["content"] == "a" * 2000


async def test_an_overlong_caption_is_cut_to_the_limit() -> None:
    fake = FakeDiscord()
    renderer = _renderer(fake, replaces_original=False)

    await renderer.send_document(DM_CHANNEL, SendDocument("a.pdf", b"x", caption="*" * 3000))

    (request,) = fake.requests
    assert request.json_body is not None and len(request.json_body["content"]) <= 2000


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("resume.pdf", "resume.pdf"),
        ("../../etc/passwd", "passwd"),
        ("my resume (1).pdf", "my_resume__1_.pdf"),
        ("", "file"),
        ("...", "file"),
        ("é.pdf", "_.pdf"),
        ("a" * 300, "a" * 100),
    ],
)
def test_a_file_name_is_made_safe(name: str, expected: str) -> None:
    assert safe_filename(name) == expected


async def test_acknowledging_a_tap_sends_nothing_because_the_http_answer_already_did() -> None:
    fake = FakeDiscord()
    renderer = _renderer(fake, replaces_original=False)

    await renderer.ack_callback(AckCallback("1100000000000000001"))

    assert fake.requests == []


async def test_a_note_with_an_acknowledgement_is_shown_to_the_person_who_tapped_only() -> None:
    fake = FakeDiscord()
    renderer = _renderer(fake, replaces_original=False)

    await renderer.ack_callback(AckCallback("1100000000000000001", text="Got *it*"))

    (request,) = fake.requests
    assert request.kind == "followup"
    assert request.json_body == {
        "content": "Got \\*it\\*",
        "allowed_mentions": {"parse": []},
        "flags": 64,
    }


async def test_a_command_that_said_nothing_is_ended_not_left_thinking() -> None:
    fake = FakeDiscord()
    renderer = _renderer(fake)
    assert not renderer.answered

    await renderer.settle()
    await renderer.settle()

    (request,) = fake.requests  # the second call has nothing left to end
    assert request.kind == "edit_original"
    assert request.json_body is not None and request.json_body["content"] == "✅ Done."


async def test_a_command_that_answered_is_left_alone_by_settle() -> None:
    fake = FakeDiscord()
    renderer = _renderer(fake)
    await renderer.send(DM_CHANNEL, Say(rich("hi")))
    await renderer.settle()
    assert len(fake.requests) == 1


# -- attachments ---------------------------------------------------------------------------------


def _attachment(
    url: str = "https://cdn.discordapp.com/attachments/1/2/resume.json?ex=1",
) -> Attachment:
    return Attachment("document", "resume.json", "application/json", 12, url)


async def test_an_attachment_is_fetched_from_the_cdn() -> None:
    fake = FakeDiscord(cdn_files={"resume.json": b'{"a": 1}'})
    data = await _renderer(fake).fetch_attachment(_attachment(), max_bytes=100)
    assert data == b'{"a": 1}'


async def test_an_attachment_over_the_cap_is_the_neutral_too_large_error() -> None:
    fake = FakeDiscord(cdn_files={"resume.json": b"x" * 500})
    with pytest.raises(AttachmentTooLarge) as caught:
        await _renderer(fake).fetch_attachment(_attachment(), max_bytes=100)
    assert caught.value.max_bytes == 100


async def test_an_attachment_with_no_address_or_a_foreign_one_is_never_fetched() -> None:
    fake = FakeDiscord()
    renderer = _renderer(fake)
    with pytest.raises(ValueError):
        await renderer.fetch_attachment(_attachment(""), max_bytes=100)
    with pytest.raises(AttachmentRefused):
        await renderer.fetch_attachment(
            _attachment("https://example.com/resume.json"), max_bytes=100
        )
    assert fake.requests == []


# -- the token stops working -----------------------------------------------------------------------


async def test_when_the_token_has_expired_the_reply_goes_to_the_direct_message_with_the_bot() -> (
    None
):
    fake = FakeDiscord()
    fake.expire_token()
    renderer = _renderer(fake)

    ref = await renderer.send(
        DM_CHANNEL, Say(rich("here you go"), messages.prepare_keyboard("a" * 36))
    )
    await renderer.send(DM_CHANNEL, Say(rich("and more")))

    kinds = [r.kind for r in fake.requests]
    assert kinds == ["edit_original", "channel_message", "channel_message"]
    # The token was tried once; after that it is known dead and not tried again.
    fallback = fake.requests[1]
    assert fallback.path == f"/channels/{DM_CHANNEL}/messages"
    assert fallback.authorized_as_bot
    assert fallback.json_body is not None and "flags" not in fallback.json_body
    assert fallback.json_body["allowed_mentions"] == {"parse": []}
    assert fallback.json_body["components"]  # the buttons still go with it
    assert ref == MessageRef(DM_CHANNEL, ref.message_id if ref else "")
    assert renderer.answered


async def test_an_expired_token_with_no_chat_known_opens_the_direct_message_first() -> None:
    fake = FakeDiscord()
    fake.expire_token()
    renderer = _renderer(fake, replaces_original=False)

    await renderer.send("", Say(rich("hi")))

    assert [r.kind for r in fake.requests] == ["followup", "open_dm", "channel_message"]
    assert fake.requests[2].path == f"/channels/{fake.dm_channel}/messages"


async def test_a_document_is_delivered_through_the_bot_when_the_token_has_expired() -> None:
    fake = FakeDiscord()
    fake.expire_token()
    renderer = _renderer(fake, replaces_original=False)

    await renderer.send_document(DM_CHANNEL, SendDocument("resume.pdf", b"%PDF", caption="c"))

    sent = fake.requests[-1]
    assert sent.kind == "channel_message" and sent.files == {"resume.pdf": b"%PDF"}
    assert sent.authorized_as_bot


async def test_with_no_bot_token_an_expired_reply_is_logged_without_the_token_and_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeDiscord()
    fake.expire_token()
    token = fresh_token()
    renderer = _renderer(fake, bot_token=None, token=token)

    with caplog.at_level(logging.DEBUG), pytest.raises(DiscordTokenExpired) as caught:
        await renderer.send(DM_CHANNEL, Say(rich("hi")))

    assert [r.kind for r in fake.requests] == ["edit_original"]  # nothing else is tried
    assert token not in str(caught.value) and token not in repr(renderer)
    assert any("could not be delivered" in r.getMessage() for r in caplog.records)
    for record in caplog.records:
        assert token not in record.getMessage() and token not in str(getattr(record, "ctx", ""))


async def test_nothing_ephemeral_is_ever_re_sent_publicly_when_the_token_expires() -> None:
    fake = FakeDiscord()
    fake.expire_token()
    renderer = _renderer(fake, ephemeral=True)

    with pytest.raises(DiscordTokenExpired):
        await renderer.send(DM_CHANNEL, Say(rich("private")))

    assert [r.kind for r in fake.requests] == ["edit_original"]


async def test_an_edit_after_expiry_edits_a_bot_message_and_cannot_edit_the_original() -> None:
    fake = FakeDiscord()
    renderer = _renderer(fake, replaces_original=False)
    ref = await renderer.send(DM_CHANNEL, Say(rich("working")))
    assert ref is not None
    fake.expire_token()

    await renderer.edit(EditMessage(ref, rich("compiling")))

    last = fake.requests[-1]
    assert last.kind == "edit_channel_message" and last.authorized_as_bot
    assert last.path == f"/channels/{DM_CHANNEL}/messages/{ref.message_id}"

    with pytest.raises(DiscordTokenExpired):
        await renderer.edit(EditMessage(MessageRef(DM_CHANNEL, "@original"), rich("x")))


async def test_the_renderer_never_shows_its_token() -> None:
    token = fresh_token()
    renderer = _renderer(FakeDiscord(), token=token)
    assert token not in repr(renderer)
    assert BOT_TOKEN not in repr(renderer)


async def test_build_renderer_takes_the_token_and_the_mode_from_the_interaction() -> None:
    fake = FakeDiscord()
    client = discord_client(fake)
    token = fresh_token()

    payload = command("list", token=token)
    message = parse_interaction(payload)
    assert message is not None
    renderer = build_renderer(client, payload, message)
    await renderer.send(DM_CHANNEL, Say(rich("x")))
    assert fake.requests[0].token == token and fake.requests[0].kind == "edit_original"

    tap = button("x", token=token)
    tapped = parse_interaction(tap)
    assert tapped is not None
    renderer = build_renderer(client, tap, tapped)
    await renderer.send(DM_CHANNEL, Say(rich("y")))
    assert fake.requests[1].kind == "followup"

    payload = command("list")
    del payload["token"]
    message = parse_interaction(payload)
    assert message is not None
    with pytest.raises(MalformedInteraction):
        build_renderer(client, payload, message)


async def test_a_server_interaction_gets_ephemeral_follow_ups_from_build_renderer() -> None:
    """The second lock on the same door: the webhook refuses anything that did not come from the
    direct message with the app before any renderer exists, and if that ever regressed, what a
    renderer built for a server interaction sends is still private to the person who ran it."""
    fake = FakeDiscord()
    client = discord_client(fake)

    payload = command("list", dm=False)
    message = parse_interaction(payload)
    assert message is not None and message.is_private is False
    renderer = build_renderer(client, payload, message)
    await renderer.send(message.chat_ref, Say(rich("a")))
    await renderer.send(message.chat_ref, Say(rich("b")))
    first, second = fake.requests
    assert first.kind == "edit_original" and "flags" not in (first.json_body or {})
    assert second.kind == "followup" and (second.json_body or {})["flags"] == 64

    # In the direct message the same follow-up carries no such flag.
    dm_fake = FakeDiscord()
    dm_payload = command("list")
    dm_message = parse_interaction(dm_payload)
    assert dm_message is not None and dm_message.is_private is True
    dm_renderer = build_renderer(discord_client(dm_fake), dm_payload, dm_message)
    await dm_renderer.send(dm_message.chat_ref, Say(rich("a")))
    await dm_renderer.send(dm_message.chat_ref, Say(rich("b")))
    assert all("flags" not in (r.json_body or {}) for r in dm_fake.requests)


async def test_a_note_for_a_tap_makes_no_call_with_a_token_known_to_be_dead() -> None:
    """Discord counts requests it refuses against an invalid-request limit, so a token that has
    failed once is not tried again, whichever call comes next."""
    fake = FakeDiscord()
    fake.expire_token()
    renderer = _renderer(fake)
    await renderer.send(DM_CHANNEL, Say(rich("x")))  # fails once, then goes to the bot channel
    made = len(fake.requests)

    await renderer.ack_callback(AckCallback("1100000000000000001", text="a note"))

    assert len(fake.requests) == made


# -- the notifier --------------------------------------------------------------------------------


class _Identities:
    """`channel_identities` for `get_chat_ref`: one row per (user, channel)."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.asked: list[tuple[str, Any]] = []
        self._filters: dict[str, Any] = {}

    def table(self, name: str) -> _Identities:
        assert name == "channel_identities"
        self._filters = {}
        return self

    def select(self, _columns: str) -> _Identities:
        return self

    def eq(self, column: str, value: Any) -> _Identities:
        self._filters[column] = value
        self.asked.append((column, value))
        return self

    async def execute(self) -> SimpleNamespace:
        wanted = [
            {"external_subject": r["subject"]}
            for r in self.rows
            if r["user_id"] == self._filters.get("user_id")
            and r["channel"] == self._filters.get("channel")
        ]
        return SimpleNamespace(data=wanted)


_LINKED = [{"user_id": "user-1", "channel": "discord", "subject": SUBJECT}]


async def test_the_notifier_opens_the_direct_message_and_says_it_there() -> None:
    fake = FakeDiscord()
    notifier = DiscordNotifier(_Identities(_LINKED), discord_client(fake))  # type: ignore[arg-type]

    sent = await notifier.notify("user-1", rich("🎯 ", bold("Strong match"), " at <@1> Acme"))

    assert sent is True
    opened, posted = fake.requests
    assert opened.kind == "open_dm" and opened.json_body == {"recipient_id": SUBJECT}
    assert posted.path == f"/channels/{fake.dm_channel}/messages"
    assert posted.authorized_as_bot
    assert posted.json_body is not None
    assert posted.json_body["allowed_mentions"] == {"parse": []}
    assert posted.json_body["content"] == "🎯 **Strong match** at \\<@1> Acme"


async def test_a_user_who_has_not_linked_discord_has_nothing_to_send() -> None:
    fake = FakeDiscord()
    identities = _Identities([{"user_id": "user-1", "channel": "telegram", "subject": "5"}])
    notifier = DiscordNotifier(identities, discord_client(fake))  # type: ignore[arg-type]

    assert await notifier.notify("user-1", rich("hi")) is False
    assert await notifier.notify("someone-else", rich("hi")) is False
    assert fake.requests == []
    assert ("channel", "discord") in identities.asked and ("user_id", "user-1") in identities.asked


async def test_a_long_push_is_several_messages() -> None:
    fake = FakeDiscord()
    notifier = DiscordNotifier(_Identities(_LINKED), discord_client(fake))  # type: ignore[arg-type]

    await notifier.notify("user-1", rich(("para " * 80 + "\n\n") * 8))

    assert len(fake.of("channel_message")) >= 2


async def test_a_refusal_to_open_the_conversation_is_raised_with_discords_code() -> None:
    fake = FakeDiscord()
    fake.fail("open_dm", status=403, code=50007)  # "Cannot send messages to this user"
    notifier = DiscordNotifier(_Identities(_LINKED), discord_client(fake))  # type: ignore[arg-type]

    with pytest.raises(DiscordApiError) as caught:
        await notifier.notify("user-1", rich("hi"))

    assert (caught.value.status_code, caught.value.code) == (403, 50007)
    assert fake.of("channel_message") == []  # nothing was sent
    assert BOT_TOKEN not in str(caught.value)


async def test_a_refusal_to_post_is_raised_too() -> None:
    fake = FakeDiscord()
    fake.fail("channel_message", status=403, code=50007)
    notifier = DiscordNotifier(_Identities(_LINKED), discord_client(fake))  # type: ignore[arg-type]
    with pytest.raises(DiscordApiError):
        await notifier.notify("user-1", rich("hi"))


def test_a_notifier_exists_only_where_there_is_a_bot_token() -> None:
    fake = FakeDiscord()
    identities = _Identities([])
    assert build_notifier(identities, None) is None  # type: ignore[arg-type]
    assert build_notifier(identities, discord_client(fake, bot_token=None)) is None  # type: ignore[arg-type]
    assert isinstance(build_notifier(identities, discord_client(fake)), DiscordNotifier)  # type: ignore[arg-type]


def test_nothing_in_these_fixtures_is_a_real_token() -> None:
    """A guard on the fixtures themselves: the bot token is the obviously fake one."""
    assert "not-a-real-bot-token" in BOT_TOKEN
