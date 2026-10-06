"""`ProgressMessage` on its own: the one message that is edited as long work advances.

How the bot uses it for a resume generation is in test_channel_core.py; here is what the class
promises whatever uses it -- a stage is a courtesy and is dropped when it cannot be edited in, the
final state always reaches the person, nothing after `start` raises, and one message is never
edited after it has ended."""

from __future__ import annotations

import logging

import pytest
from channel_fakes import CHAT_REF, FakeRenderer

from between_jobs.api.channel_envelope import (
    Button,
    EditMessage,
    MessageRef,
    Say,
    bold,
    rich,
)
from between_jobs.api.progress_message import ProgressMessage

_BUTTONS = ((Button("Mark", "m:1"),),)


class _Refusing(FakeRenderer):
    def __init__(self, *, edits: bool = False, sends_after_the_first: bool = False) -> None:
        super().__init__()
        self._edits = edits
        self._sends = sends_after_the_first

    async def edit(self, intent: EditMessage) -> MessageRef | None:
        if self._edits:
            raise RuntimeError("edit refused")
        return await super().edit(intent)

    async def send(self, chat_ref: str, intent: Say) -> MessageRef | None:
        if self._sends and self.sent:
            raise RuntimeError("send refused")
        return await super().send(chat_ref, intent)


class _NamesNothing(FakeRenderer):
    """A channel that gives no reference for a message it sent, so there is nothing to edit by."""

    async def send(self, chat_ref: str, intent: Say) -> MessageRef | None:
        await super().send(chat_ref, intent)
        return None


async def test_it_opens_with_one_message_and_edits_that_message() -> None:
    renderer = FakeRenderer()
    progress = ProgressMessage(renderer, CHAT_REF)

    await progress.start("starting")
    await progress.update("halfway")
    await progress.finish("done", _BUTTONS)

    assert renderer.texts == ["starting"]
    opened = progress.message_ref
    assert opened == MessageRef(CHAT_REF, "1")
    assert [(e.message_ref, e.text.plain_text(), e.buttons) for e in renderer.edits] == [
        (opened, "halfway", ()),
        (opened, "done", _BUTTONS),
    ]
    assert renderer.shown_texts == ["done"]


async def test_text_may_be_rich_and_a_plain_string_is_text_never_markup() -> None:
    renderer = FakeRenderer()
    progress = ProgressMessage(renderer, CHAT_REF)

    await progress.start(rich("working on ", bold("<b>x</b>")))
    await progress.finish("<i>done</i>")

    assert renderer.sent[0][1].text == rich("working on ", bold("<b>x</b>"))
    assert renderer.edits[0].text == rich("<i>done</i>")
    assert not renderer.edits[0].text.is_styled()


async def test_start_raises_what_the_renderer_raises() -> None:
    class _Down(FakeRenderer):
        async def send(self, chat_ref: str, intent: Say) -> MessageRef | None:
            raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        await ProgressMessage(_Down(), CHAT_REF).start("starting")


async def test_a_stage_that_cannot_be_edited_in_is_dropped_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    renderer = _Refusing(edits=True)
    progress = ProgressMessage(renderer, CHAT_REF, log_context={"update_id": "9"})
    await progress.start("starting")

    with caplog.at_level(logging.WARNING, logger="between_jobs.api.progress_message"):
        await progress.update("halfway")

    assert renderer.texts == ["starting"]  # no message of its own for the stage
    (record,) = caplog.records
    assert record.ctx == {"update_id": "9"}  # type: ignore[attr-defined]
    assert record.exc_info is not None


async def test_the_final_state_is_sent_new_when_it_cannot_be_edited_in() -> None:
    renderer = _Refusing(edits=True)
    progress = ProgressMessage(renderer, CHAT_REF)
    await progress.start("starting")

    await progress.finish("the reason", _BUTTONS)

    assert renderer.texts == ["starting", "the reason"]
    assert renderer.sent[1][1].buttons == _BUTTONS  # and it keeps its buttons
    assert renderer.sent[1][0] == CHAT_REF


async def test_finish_never_raises_even_when_nothing_gets_through(
    caplog: pytest.LogCaptureFixture,
) -> None:
    renderer = _Refusing(edits=True, sends_after_the_first=True)
    progress = ProgressMessage(renderer, CHAT_REF)
    await progress.start("starting")

    with caplog.at_level(logging.WARNING, logger="between_jobs.api.progress_message"):
        shown = await progress.finish("the reason")

    assert shown is False  # the person has not been told
    assert renderer.texts == ["starting"]
    assert any("how their request ended" in r.getMessage() for r in caplog.records)


async def test_finish_says_whether_the_final_state_reached_the_person() -> None:
    edited = FakeRenderer()
    first = ProgressMessage(edited, CHAT_REF)
    await first.start("starting")
    assert await first.finish("done") is True  # by the edit

    sent_new = _Refusing(edits=True)
    second = ProgressMessage(sent_new, CHAT_REF)
    await second.start("starting")
    assert await second.finish("done") is True  # the edit was refused; the new message was not

    nothing_to_edit_by = _NamesNothing()
    third = ProgressMessage(nothing_to_edit_by, CHAT_REF)
    await third.start("starting")
    assert await third.finish("done") is True  # sent as a new message

    nothing_gets_through = _Refusing(edits=True, sends_after_the_first=True)
    fourth = ProgressMessage(nothing_gets_through, CHAT_REF)
    await fourth.start("starting")
    assert await fourth.finish("done") is False


async def test_a_second_finish_after_a_refused_first_gets_a_shorter_text_through() -> None:
    """What the bot does for a person with no resume: the reason was refused, so the generic
    words are sent instead, as a message of their own."""

    class _RefusesLongText(FakeRenderer):
        async def edit(self, intent: EditMessage) -> MessageRef | None:
            if len(intent.text.plain_text()) > 20:
                raise RuntimeError("message is too long")
            return await super().edit(intent)

        async def send(self, chat_ref: str, intent: Say) -> MessageRef | None:
            if len(intent.text.plain_text()) > 20:
                raise RuntimeError("message is too long")
            return await super().send(chat_ref, intent)

    renderer = _RefusesLongText()
    progress = ProgressMessage(renderer, CHAT_REF)
    await progress.start("starting")

    assert await progress.finish("x" * 50) is False
    assert await progress.finish("short") is True

    assert renderer.texts == ["starting", "short"]


async def test_a_message_the_renderer_gave_no_reference_for_is_never_edited() -> None:
    renderer = _NamesNothing()
    progress = ProgressMessage(renderer, CHAT_REF)
    await progress.start("starting")

    await progress.update("halfway")
    await progress.finish("done")

    assert renderer.edits == []
    assert renderer.texts == ["starting", "done"]  # the stage is dropped, the end is not


async def test_nothing_is_edited_once_the_message_has_ended() -> None:
    renderer = FakeRenderer()
    progress = ProgressMessage(renderer, CHAT_REF)
    await progress.start("starting")
    await progress.finish("done")

    await progress.update("too late")

    assert [e.text.plain_text() for e in renderer.edits] == ["done"]


async def test_a_message_ended_by_a_new_message_is_not_edited_afterwards() -> None:
    """The ending went out as a message of its own because the edit was refused. The progress
    message must stay untouched from then on: a stage that arrives late would be written over
    the stale message the person has already moved past, and a repeated ending must not try the
    edit again."""

    class _RefusesTheFirstEditOnly(FakeRenderer):
        def __init__(self) -> None:
            super().__init__()
            self.edit_attempts = 0

        async def edit(self, intent: EditMessage) -> MessageRef | None:
            self.edit_attempts += 1
            if self.edit_attempts == 1:
                raise RuntimeError("edit refused")
            return await super().edit(intent)

    renderer = _RefusesTheFirstEditOnly()
    progress = ProgressMessage(renderer, CHAT_REF)
    await progress.start("starting")
    await progress.finish("the reason")  # the edit is refused, so it goes out as a new message

    await progress.update("too late")
    await progress.finish("and again")

    assert renderer.edit_attempts == 1  # nothing after the ending touched the old message
    assert renderer.edits == []
    assert renderer.texts == ["starting", "the reason", "and again"]


async def test_a_second_ending_is_a_message_of_its_own() -> None:
    """Two final states is a bug in the caller, but what the second says may be something the
    person needs (an error), so it is sent, not silently dropped or written over the first."""
    renderer = FakeRenderer()
    progress = ProgressMessage(renderer, CHAT_REF)
    await progress.start("starting")
    await progress.finish("done")

    await progress.finish("but then this happened")

    assert renderer.texts == ["starting", "but then this happened"]
    assert renderer.shown_texts == ["done", "but then this happened"]
