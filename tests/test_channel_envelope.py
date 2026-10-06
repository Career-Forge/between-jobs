"""The channel-neutral vocabulary (`channel_envelope`): rich text that cannot be mistaken for
markup, normalized so equal text is equal data, and immutable envelopes."""

from __future__ import annotations

import dataclasses

import pytest

from between_jobs.api.channel_envelope import (
    AckCallback,
    Attachment,
    AttachmentTooLarge,
    Button,
    Callback,
    EditMessage,
    InboundMessage,
    MessageRef,
    RichText,
    Say,
    Segment,
    SendDocument,
    bold,
    code,
    italic,
    pre,
    rich,
)


def test_a_plain_string_is_one_plain_segment() -> None:
    assert rich("hello") == RichText((Segment("hello"),))


def test_the_style_helpers_make_one_styled_segment_each() -> None:
    assert bold("a") == Segment("a", "bold")
    assert italic("a") == Segment("a", "italic")
    assert code("a") == Segment("a", "code")
    assert pre("a") == Segment("a", "pre")


def test_text_that_looks_like_markup_stays_text() -> None:
    """A segment's text is never read as formatting -- escaping is the renderer's job."""
    out = rich("<b>", bold("</b> & <i>"))
    assert out.segments == (Segment("<b>"), Segment("</b> & <i>", "bold"))
    assert out.plain_text() == "<b></b> & <i>"


def test_neighbouring_plain_runs_are_merged_and_empty_ones_dropped() -> None:
    assert rich("a", "", "b", RichText((Segment("c"),))) == rich("abc")
    assert rich("a", bold("b"), "c", "d") == RichText(
        (Segment("a"), Segment("b", "bold"), Segment("cd"))
    )


def test_an_empty_plain_run_alone_or_at_either_end_is_dropped_too() -> None:
    """Equal text is equal data: the same words give the same value however they were built."""
    assert rich("") == RichText(())
    assert rich(bold("x"), "") == rich(bold("x"))
    assert rich("", bold("x")) == rich(bold("x"))
    assert rich("", bold("x"), "").segments == (Segment("x", "bold"),)


def test_styled_runs_are_never_merged_and_an_empty_one_is_kept() -> None:
    assert len(rich(bold("a"), bold("b")).segments) == 2
    assert rich("x", bold(""), "y").segments == (
        Segment("x"),
        Segment("", "bold"),
        Segment("y"),
    )


def test_rich_text_composes_with_plus_and_inside_rich() -> None:
    left = rich("a ", bold("b"))
    right = rich(" c")
    assert left + right == rich(left, right) == rich("a ", bold("b"), " c")


def test_plain_text_and_is_styled() -> None:
    text = rich("Hi ", bold("there"), ", ", code("x"))
    assert text.plain_text() == "Hi there, x"
    assert text.is_styled()
    assert not rich("just words").is_styled()
    assert not RichText().is_styled()
    assert RichText().plain_text() == ""


def test_every_envelope_is_immutable() -> None:
    envelopes = [
        Segment("a"),
        RichText(),
        MessageRef("1", "2"),
        Attachment("document", "a.json", "application/json", 3, "h"),
        Callback("id", "data", None),
        InboundMessage("telegram", "1", "1", None, None, ""),
        Button("l", "d"),
        Say(RichText()),
        SendDocument("a.pdf", b"x"),
        EditMessage(MessageRef("1", "2"), RichText()),
        AckCallback("id"),
    ]
    for envelope in envelopes:
        with pytest.raises(dataclasses.FrozenInstanceError):
            envelope.anything = 1  # type: ignore[attr-defined]


def test_an_inbound_message_defaults_to_a_private_chat_with_no_attachment_or_callback() -> None:
    message = InboundMessage("telegram", "42", "42", "7", "9", "hello")
    assert message.is_private
    assert message.attachment is None
    assert message.callback is None


def test_outbound_intents_default_to_no_buttons_and_no_caption() -> None:
    assert Say(rich("x")).buttons == ()
    assert EditMessage(MessageRef("1", "2"), rich("x")).buttons == ()
    assert SendDocument("a.pdf", b"x").caption is None
    assert AckCallback("id").text is None


def test_attachment_too_large_remembers_the_limit() -> None:
    error = AttachmentTooLarge(2000)
    assert error.max_bytes == 2000
    assert "2000" in str(error)
