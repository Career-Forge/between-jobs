"""The channel-neutral language between the business logic and a chat channel.

A chat channel (Telegram today) is two things: a wire format, and a person on the other
end. The business logic -- link codes, resume import, tracking a job, generating a resume --
should know the person and not the wire format, so a second channel needs a second adapter
and no change to the logic. This module is the vocabulary they share. It is pure: no I/O, no
imports from the rest of the package, nothing channel-specific.

Inbound, an adapter turns a provider's update into one `InboundMessage`: who sent it
(`channel` + `subject`, the provider's own user id as text), where a reply goes (`chat_ref`),
and what they said (`text`, plus an `Attachment` they uploaded or the `Callback` of a button
they tapped).

Outbound, the logic says what it wants to happen with intents -- `Say`, `SendDocument`,
`EditMessage`, `AckCallback` -- and a `Renderer` makes it happen on its channel. Text is a
`RichText`: a flat list of segments, each plain or one of bold, italic, code and pre. That is
the set Telegram's HTML mode needs, and the set most chat channels can draw; the renderer
decides how (Telegram writes tags and escapes the text, a channel with no markup could print
the plain text). Because a segment's text is never markup, a name or an error message put
into a `RichText` can never be read as formatting: escaping is the renderer's job, once.

Identity is not here. `subject` is whatever the provider calls the sender; mapping it to a
user is `telegram_identity` for now, keyed by the verified channel link and never by anything
in the message text.

Neither is redelivery. A provider that delivers an update again when it gets no answer in
time needs its channel side to drop the repeat before the logic sees it, or a resume
generation would be paid for twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

Style = Literal["bold", "italic", "code", "pre"]


@dataclass(frozen=True)
class Segment:
    """A run of text with one style, or none (plain). `text` is text, never markup."""

    text: str
    style: Style | None = None


@dataclass(frozen=True)
class RichText:
    segments: tuple[Segment, ...] = ()

    def __add__(self, other: RichText) -> RichText:
        return rich(self, other)

    def plain_text(self) -> str:
        """The words with no formatting: what a channel with no markup would show."""
        return "".join(segment.text for segment in self.segments)

    def is_styled(self) -> bool:
        return any(segment.style is not None for segment in self.segments)


def bold(text: str) -> Segment:
    return Segment(text, "bold")


def italic(text: str) -> Segment:
    return Segment(text, "italic")


def code(text: str) -> Segment:
    return Segment(text, "code")


def pre(text: str) -> Segment:
    return Segment(text, "pre")


def rich(*parts: str | Segment | RichText) -> RichText:
    """Builds a `RichText` from strings (plain), styled segments and other `RichText`s.

    The result is normalized so equal text is equal data: empty plain runs are dropped and
    neighbouring plain runs are merged. An empty STYLED segment is kept -- it is what the
    caller asked for, and a channel may draw it (`<b></b>`) -- and styled runs are never
    merged."""
    segments: list[Segment] = []
    for part in parts:
        if isinstance(part, str):
            incoming: tuple[Segment, ...] = (Segment(part),)
        elif isinstance(part, Segment):
            incoming = (part,)
        else:
            incoming = part.segments
        for segment in incoming:
            if segment.style is None:
                if segment.text == "":
                    continue
                if segments and segments[-1].style is None:
                    segments[-1] = Segment(segments[-1].text + segment.text)
                    continue
            segments.append(segment)
    return RichText(tuple(segments))


# -- addressing --------------------------------------------------------------------------


@dataclass(frozen=True)
class MessageRef:
    """A message that already exists: the chat it is in and the channel's id for it.
    Both are the channel's own values, as text; only that channel's renderer reads them."""

    chat_ref: str
    message_id: str


# -- inbound -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Attachment:
    """A file the sender attached. `handle` is opaque to the business logic: only the
    channel's own renderer can turn it into bytes (`Renderer.fetch_attachment`). The size
    is what the sender's client claims, so it is advisory -- never the only limit."""

    kind: Literal["document"]
    filename: str
    mime_type: str
    declared_size: int | None
    handle: str


@dataclass(frozen=True)
class Callback:
    """A tap on a button. `data` is exactly what the `Button` carried; `id` is what
    `AckCallback` needs to acknowledge the tap."""

    id: str
    data: str
    message_ref: MessageRef | None


@dataclass(frozen=True)
class InboundMessage:
    channel: str
    subject: str
    """The provider's id for the sender, as text. The only thing identity is derived from."""
    chat_ref: str
    """Where a reply goes: the provider's id for the conversation, as text."""
    message_id: str | None
    update_id: str | None
    """The provider's delivery id, as text, for tracing a log line to a delivery. Nothing here
    dedups on it: protection against a redelivery is the channel's own job, done before the
    message reaches the business logic (Telegram's webhook claims the raw integer id in
    `telegram_processed_updates`, a table keyed on that id alone, so another channel needs a
    claim path of its own)."""
    text: str
    """What they typed (or the caption of what they sent); empty for a button tap."""
    attachment: Attachment | None = None
    callback: Callback | None = None
    is_private: bool = True
    """A one-to-one conversation with the bot. A /link code must never be accepted from
    anything else: everyone in a group could have seen it."""


# -- outbound ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Button:
    label: str
    data: str
    """Comes back as `Callback.data` when the button is tapped. Kept short: channels cap it."""


ButtonRows = tuple[tuple[Button, ...], ...]


@dataclass(frozen=True)
class Say:
    text: RichText
    buttons: ButtonRows = ()


@dataclass(frozen=True)
class SendDocument:
    filename: str
    content: bytes
    caption: str | None = None
    """Plain text, shown as written: a caption is not markup on any channel we send to."""


@dataclass(frozen=True)
class EditMessage:
    message_ref: MessageRef
    text: RichText
    buttons: ButtonRows = ()
    """What the edited message shows afterwards; empty means no buttons."""


@dataclass(frozen=True)
class AckCallback:
    callback_id: str
    text: str | None = None
    """A short note shown to the person who tapped, where the channel has such a thing."""


class AttachmentTooLarge(Exception):
    """An attachment is larger than the caller said it would accept."""

    def __init__(self, max_bytes: int) -> None:
        super().__init__(f"the attachment is larger than {max_bytes} bytes")
        self.max_bytes = max_bytes


class Renderer(Protocol):
    """One channel's side of a conversation: what the business logic can ask for.

    Every method returns when the channel has accepted the request, and raises when it has
    not; nothing is retried or swallowed here. The two that create a message answer with its
    `MessageRef` when the channel gives one back, else None."""

    async def send(self, chat_ref: str, intent: Say) -> MessageRef | None: ...

    async def send_document(self, chat_ref: str, intent: SendDocument) -> MessageRef | None: ...

    async def edit(self, intent: EditMessage) -> MessageRef | None: ...

    async def ack_callback(self, intent: AckCallback) -> None: ...

    async def fetch_attachment(self, attachment: Attachment, *, max_bytes: int) -> bytes:
        """The attachment's bytes. Raises `AttachmentTooLarge` as soon as it is known to be
        (or turns out to be) larger than `max_bytes`, without holding the rest in memory."""


class Notifier(Protocol):
    """Tells a user something unprompted, on whichever channel they have linked.

    Returns whether anything was sent: False for a user with no linked channel, which is a
    normal answer. Raises when the channel refuses; callers that treat a nudge as a bonus
    catch that themselves."""

    async def notify(self, user_id: str, text: RichText) -> bool: ...
