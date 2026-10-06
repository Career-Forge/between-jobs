"""Telegram's side of the channel boundary: the one module that reads a raw Telegram update
or writes a Telegram request body.

Everything the business logic (`channel_core`) knows about Telegram goes through here, in two
directions:

- `parse_update` turns an update's JSON into an `InboundMessage` -- or None for the shapes
  the bot has never acted on (edited messages, a message with neither text nor a document,
  channel posts). A malformed message or callback (no `from`, no `chat`) raises, as it
  always did: Telegram never sends one, and a 500 is how a bug here would be noticed.
- `TelegramRenderer` turns the logic's intents (`Say`, `SendDocument`, `EditMessage`,
  `AckCallback`) into calls on `TelegramClient`, and fetches an `Attachment`'s bytes.

What stays Telegram's own, and so lives here: HTML mode and the escaping that goes with it
(a segment's text is escaped, never trusted as markup), the inline-keyboard JSON, the
`file_id` an uploaded document is fetched by and the size cap on that fetch, and the rule
that a chat id is an integer. Telegram's 4096-character cap on a message has never been
enforced by splitting, and still is not: the copy the bot sends is checked against it in the
template tests instead.

`TelegramNotifier` is the proactive side: it finds the chat a user linked and says
something there.
"""

from __future__ import annotations

from typing import Any

from supabase import AsyncClient

from .channel_envelope import (
    AckCallback,
    Attachment,
    AttachmentTooLarge,
    ButtonRows,
    Callback,
    EditMessage,
    InboundMessage,
    MessageRef,
    RichText,
    Say,
    SendDocument,
)
from .telegram_client import DownloadTooLarge, Html, TelegramClient, escape
from .telegram_identity import CHANNEL, get_chat_id

_TAGS = {"bold": "b", "italic": "i", "code": "code", "pre": "pre"}


# -- inbound -----------------------------------------------------------------------------


def update_id_of(update: dict[str, Any]) -> int | None:
    """Telegram's id for this delivery, or None if the update has none Telegram would
    recognise. A bool is an int in Python and is not an id."""
    update_id = update.get("update_id")
    if isinstance(update_id, int) and not isinstance(update_id, bool):
        return update_id
    return None


def _text_of(message: dict[str, Any]) -> str:
    """What the sender typed, or the caption of what they sent."""
    return str(message.get("text") or message.get("caption") or "")


def _attachment_of(document: object) -> Attachment | None:
    if not isinstance(document, dict):
        return None
    declared_size = document.get("file_size")
    return Attachment(
        kind="document",
        filename=str(document.get("file_name", "")),
        mime_type=str(document.get("mime_type", "")),
        declared_size=(
            declared_size
            if isinstance(declared_size, int) and not isinstance(declared_size, bool)
            else None
        ),
        handle=str(document.get("file_id", "")),
    )


def parse_update(update: dict[str, Any]) -> InboundMessage | None:
    """The update as the business logic sees it, or None if it is not something the bot acts
    on."""
    raw_update_id = update_id_of(update)
    update_id = str(raw_update_id) if raw_update_id is not None else None

    callback_query = update.get("callback_query")
    if isinstance(callback_query, dict):
        chat_ref = str(callback_query["message"]["chat"]["id"])
        message_id = callback_query["message"].get("message_id")
        return InboundMessage(
            channel=CHANNEL,
            subject=str(callback_query["from"]["id"]),
            chat_ref=chat_ref,
            message_id=None,
            update_id=update_id,
            text="",
            callback=Callback(
                id=str(callback_query["id"]),
                data=callback_query["data"] if isinstance(callback_query.get("data"), str) else "",
                message_ref=(
                    MessageRef(chat_ref, str(message_id)) if message_id is not None else None
                ),
            ),
        )

    message = update.get("message")
    if not isinstance(message, dict) or ("text" not in message and "document" not in message):
        return None

    raw_message_id = message.get("message_id")
    chat = message["chat"]
    return InboundMessage(
        channel=CHANNEL,
        subject=str(message["from"]["id"]),
        chat_ref=str(chat["id"]),
        message_id=str(raw_message_id) if raw_message_id is not None else None,
        update_id=update_id,
        text=_text_of(message),
        attachment=_attachment_of(message.get("document")),
        is_private=chat.get("type") == "private",
    )


# -- outbound ----------------------------------------------------------------------------


def render_html(text: RichText) -> Html:
    """The text as Telegram HTML. Each segment's text is escaped, so nothing a caller put in
    it can be read as markup; the tags are the only markup there is."""
    parts: list[str] = []
    for segment in text.segments:
        body = escape(segment.text)
        tag = _TAGS[segment.style] if segment.style is not None else None
        parts.append(f"<{tag}>{body}</{tag}>" if tag is not None else body)
    return Html("".join(parts))


def inline_keyboard(rows: ButtonRows) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": button.label, "callback_data": button.data} for button in row] for row in rows
        ]
    }


def _ref(chat_ref: str, message_id: int | None) -> MessageRef | None:
    return MessageRef(chat_ref, str(message_id)) if message_id is not None else None


class TelegramRenderer:
    """`Renderer` on top of `TelegramClient`."""

    def __init__(self, client: TelegramClient) -> None:
        self._client = client

    async def send(self, chat_ref: str, intent: Say) -> MessageRef | None:
        # No keyboard, no `reply_markup` argument at all: a plain message is the same call
        # it always was.
        if intent.buttons:
            message_id = await self._client.send_message(
                int(chat_ref),
                render_html(intent.text),
                reply_markup=inline_keyboard(intent.buttons),
            )
        else:
            message_id = await self._client.send_message(int(chat_ref), render_html(intent.text))
        return _ref(chat_ref, message_id)

    async def send_document(self, chat_ref: str, intent: SendDocument) -> MessageRef | None:
        message_id = await self._client.send_document(
            int(chat_ref), intent.filename, intent.content, caption=intent.caption
        )
        return _ref(chat_ref, message_id)

    async def edit(self, intent: EditMessage) -> MessageRef | None:
        ref = intent.message_ref
        message_id = await self._client.edit_message_text(
            int(ref.chat_ref),
            int(ref.message_id),
            render_html(intent.text),
            reply_markup=inline_keyboard(intent.buttons) if intent.buttons else None,
        )
        return _ref(ref.chat_ref, message_id)

    async def ack_callback(self, intent: AckCallback) -> None:
        if intent.text is None:
            await self._client.answer_callback_query(intent.callback_id)
        else:
            await self._client.answer_callback_query(intent.callback_id, text=intent.text)

    async def fetch_attachment(self, attachment: Attachment, *, max_bytes: int) -> bytes:
        if not attachment.handle:
            raise ValueError("the attachment has no file id to fetch it by")
        try:
            return await self._client.download_document(attachment.handle, max_bytes=max_bytes)
        except DownloadTooLarge as e:
            raise AttachmentTooLarge(max_bytes) from e


class TelegramNotifier:
    """`Notifier` for Telegram: finds the private chat the user linked and says it there.

    Telegram's own rule for a one-to-one conversation is that its chat id is the user's id,
    which is what the identity row holds (`get_chat_id`)."""

    def __init__(self, supabase: AsyncClient, renderer: TelegramRenderer) -> None:
        self._supabase = supabase
        self._renderer = renderer

    async def notify(self, user_id: str, text: RichText) -> bool:
        chat_id = await get_chat_id(self._supabase, user_id)
        if chat_id is None:
            return False
        await self._renderer.send(str(chat_id), Say(text))
        return True


def build_notifier(supabase: AsyncClient, client: TelegramClient | None) -> TelegramNotifier | None:
    """The notifier for a server's bot, or None on a server running without one."""
    if client is None:
        return None
    return TelegramNotifier(supabase, TelegramRenderer(client))
