"""One message that tells a person how long-running work is going, edited as it advances.

A resume generation takes tens of seconds. Rather than one message per stage and another for the
result, the person gets ONE message: it starts as "working on it", is edited as the work moves on
("compiling the PDF"), and ends as the work's final state -- done, or why it did not finish --
while the document itself arrives as the file it is. `ProgressMessage` is that message, in terms
of the envelope's `Say` and `EditMessage`, so it works on any channel whose renderer can edit.

A channel can refuse an edit for reasons that have nothing to do with this message: the person
deleted it, the channel rate limits edits, the channel cannot edit at all, or it is briefly down.
What happens then depends on what the update says:

- A STAGE update (`update`) is a courtesy. If it cannot be edited in, it is dropped and logged:
  the person already has a message saying work is under way, and a stray new message for each
  stage would be exactly the clutter this exists to avoid.
- The FINAL state (`finish`) is information: the error that explains why there is no resume, or
  the prompt that carries the "mark as applied" button. If it cannot be edited in, it is sent as
  a new message, so the person never loses it. A channel that cannot edit therefore ends up with
  the opening message and the final one, and nothing in between.

Neither ever raises: this runs in a background task after the answer to the request was given,
and a progress message failing must not make a person who already holds their resume be told it
failed. When even the new message cannot be sent the failure is logged. `finish` answers whether
the final state reached the person at all, so a caller whose person has NOTHING (no resume) can
try once more with something the channel will take, instead of leaving them on "this can take a
minute". (`start` is the one call that can raise, because it runs inside the request, before any
work has been promised.)

A message the renderer reports no reference for cannot be edited, since there is nothing to name
it by; it is treated like a channel that cannot edit.

What a restart does to a message in flight is the deferred work's own story (see
`deferred_reply`): the task is lost, and this message stays at the last state it showed.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from .channel_envelope import (
    ButtonRows,
    EditMessage,
    MessageRef,
    Renderer,
    RichText,
    Say,
    rich,
)

logger = logging.getLogger(__name__)


class ProgressMessage:
    def __init__(
        self,
        renderer: Renderer,
        chat_ref: str,
        *,
        log_context: Mapping[str, Any] | None = None,
    ) -> None:
        self._renderer = renderer
        self._chat_ref = chat_ref
        self._ctx = dict(log_context or {})
        self._ref: MessageRef | None = None
        self._finished = False

    @property
    def message_ref(self) -> MessageRef | None:
        """The message as the renderer named it, or None before `start` or when it named none."""
        return self._ref

    async def start(self, text: str | RichText) -> None:
        """Sends the opening message. Raises whatever the renderer raises: nothing has been
        promised to the person yet, so the caller handles it as it would any failed reply."""
        self._ref = await self._renderer.send(self._chat_ref, Say(_as_rich(text)))

    async def update(self, text: str | RichText) -> None:
        """Shows a new stage by editing the message. Best effort: when the edit cannot be made
        the stage is simply not shown. Does nothing once the message has been finished."""
        if self._finished:
            return
        if self._ref is None:
            logger.info("a progress update was skipped: the message has no reference to edit by")
            return
        try:
            await self._renderer.edit(EditMessage(self._ref, _as_rich(text)))
        except Exception:
            logger.warning(
                "could not edit the progress message to show a new stage; it is left as it was",
                exc_info=True,
                extra={"ctx": self._ctx},
            )

    async def finish(self, text: str | RichText, buttons: ButtonRows = ()) -> bool:
        """Ends the message with its final state, `buttons` under it if given. Edited into the
        progress message when it can be, else sent as a new message. Never raises.

        True when the person has the final state, by the edit or by the new message; False when
        both were refused, so nothing was shown. Either way the message has ended: a second
        call is sent as a message of its own, which is how a caller whose first text was refused
        gets a shorter one through."""
        body = _as_rich(text)
        if self._ref is not None and not self._finished:
            try:
                await self._renderer.edit(EditMessage(self._ref, body, buttons))
            except Exception:
                logger.warning(
                    "could not edit the progress message into its final state; "
                    "sending it as a new message",
                    exc_info=True,
                    extra={"ctx": self._ctx},
                )
            else:
                self._finished = True
                return True
        self._finished = True
        try:
            await self._renderer.send(self._chat_ref, Say(body, buttons))
        except Exception:
            logger.warning(
                "could not tell the person how their request ended",
                exc_info=True,
                extra={"ctx": self._ctx},
            )
            return False
        return True


def _as_rich(text: str | RichText) -> RichText:
    return text if isinstance(text, RichText) else rich(text)
