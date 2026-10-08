"""Discord's side of the channel boundary: the one module that reads a raw Discord interaction or
writes a Discord request body.

Discord is the bot's second channel, over HTTP interactions only (no Gateway connection): Discord
POSTs a signed JSON document to our endpoint when a person runs a slash command or taps a button,
and everything we say back goes through the interaction's own webhook (`discord_client`). Plain
text a person types in a direct message with the app is never delivered to us, which is why every
command the Telegram bot understands as a sentence is a slash command here
(`discord_commands`).

Everything the business logic (`channel_core`) knows about Discord goes through this module, in
two directions:

- `parse_interaction` turns an interaction into ONE `InboundMessage` whose text is the sentence
  the Telegram bot would have been sent for the same thing (`/link ABCD2345`, `list`,
  `apply to 3`, a pasted job), so the intent parser stays the single place that decides what a
  text means. A button tap becomes a `Callback` carrying the button's `custom_id`, which is the
  `Button.data` it was made from. Anything that is not a command or a button (a ping, an
  autocomplete, a modal) is None. A payload that is not shaped like an interaction raises
  `MalformedInteraction`.
- `DiscordInteractionRenderer` turns the logic's intents (`Say`, `SendDocument`, `EditMessage`,
  `AckCallback`) into calls on `DiscordClient`, for ONE interaction, and fetches an
  `Attachment`'s bytes from Discord's CDN.

How one interaction is answered (the webhook, `discord_webhook`, answers the HTTP request; this
module says what with):

- A slash command is answered at once with a DEFERRED response (type 5): Discord shows "the app is
  thinking" and gives the app 15 minutes. The renderer's FIRST message edits that placeholder (the
  `@original` message), every later one is a follow-up. A command that ends without saying anything
  would leave the placeholder up forever, so `settle` ends it.
- A button tap is answered at once with a DEFERRED UPDATE (type 6): Discord shows no placeholder and
  the message the button was on is left as it is. Everything the renderer says to a tap is a NEW
  message (a follow-up), never an edit of the message with the buttons.
- A follow-up shares the first message's visibility, so what is sent in anything but the direct
  message with the app (a server channel, a group chat) is EPHEMERAL (flag 64): visible to the
  person who ran the command and to nobody else. The commands are registered for the direct message
  only and the webhook refuses anything else, so this is a second lock on the same door.

Every request carries `allowed_mentions: {"parse": []}`, so nothing a person or a job posting wrote
can ping a user, a role or everyone. A message is cut into parts that fit Discord's 2000-character
limit (`discord_markdown`), and a button row is held to Discord's limits: five buttons to a row,
five rows, labels of at most 80 characters and `custom_id`s of at most 100.

The interaction's token is a credential for 15 minutes. It lives in the renderer and in the URLs
`discord_client` builds, and nowhere else: not in a log line, not in an exception message, not in
the renderer's repr. When it stops working (it expired, or never worked) the renderer says so
once, and from then on sends what it still has to say to the person's direct message through the
bot token when the server has one, or logs that the message could not be delivered when it does
not.

`DiscordNotifier` is the proactive side: it finds the Discord account a user linked, opens a direct
message with it through the bot token, and says something there.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from enum import Enum
from typing import Any

from supabase import AsyncClient

from . import discord_commands as commands
from .channel_envelope import (
    AckCallback,
    Attachment,
    AttachmentTooLarge,
    Button,
    ButtonRows,
    Callback,
    EditMessage,
    InboundMessage,
    MessageRef,
    RichText,
    Say,
    SendDocument,
)
from .channel_identity import get_chat_ref
from .discord_client import (
    DiscordClient,
    DiscordTokenExpired,
    DownloadTooLarge,
)
from .discord_copy import localize
from .discord_markdown import MESSAGE_LIMIT, escape_markdown, scrub, split_markdown, utf16_length

logger = logging.getLogger(__name__)

CHANNEL = "discord"

# -- Discord's numbers -----------------------------------------------------------------------

_INTERACTION_PING = 1
_INTERACTION_COMMAND = 2
_INTERACTION_COMPONENT = 3

_RESPONSE_PONG = 1
_RESPONSE_MESSAGE = 4
_RESPONSE_DEFERRED_MESSAGE = 5
_RESPONSE_DEFERRED_UPDATE = 6

FLAG_EPHEMERAL = 1 << 6

_COMPONENT_ACTION_ROW = 1
_COMPONENT_BUTTON = 2
_BUTTON_PRIMARY = 1
_BUTTON_SECONDARY = 2

MAX_BUTTONS_PER_ROW = 5
MAX_BUTTON_ROWS = 5
MAX_BUTTON_LABEL = 80
MAX_CUSTOM_ID = 100

_SNOWFLAKE = re.compile(r"[0-9]{1,25}")
_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


class MalformedInteraction(ValueError):
    """A document that claims to be a command or a button tap but lacks what one always has (an
    id, a token, a sender). Discord never sends one; the message names the missing part and
    never quotes the document."""


class InteractionKind(Enum):
    PING = "ping"
    COMMAND = "command"
    COMPONENT = "component"
    OTHER = "other"


# -- inbound ---------------------------------------------------------------------------------


def _snowflake(value: object, what: str) -> str:
    if isinstance(value, str) and _SNOWFLAKE.fullmatch(value):
        return value
    raise MalformedInteraction(f"the interaction has no valid {what}")


def interaction_kind(payload: Mapping[str, Any]) -> InteractionKind:
    kind = payload.get("type")
    if kind == _INTERACTION_PING:
        return InteractionKind.PING
    if kind == _INTERACTION_COMMAND:
        return InteractionKind.COMMAND
    if kind == _INTERACTION_COMPONENT:
        return InteractionKind.COMPONENT
    return InteractionKind.OTHER


def interaction_id_of(payload: Mapping[str, Any]) -> str | None:
    """Discord's id for this interaction (its delivery id), or None when it has none Discord
    would recognise."""
    value = payload.get("id")
    return value if isinstance(value, str) and _SNOWFLAKE.fullmatch(value) else None


def _sender(payload: Mapping[str, Any]) -> str:
    """The person who ran the command: `member.user` when it ran in a server, `user` in a direct
    message. Never anything in the options or the message text."""
    member = payload.get("member")
    user = member.get("user") if isinstance(member, dict) else payload.get("user")
    if not isinstance(user, dict):
        raise MalformedInteraction("the interaction names no sender")
    return _snowflake(user.get("id"), "sender id")


def _channel_of(payload: Mapping[str, Any]) -> str:
    channel_id = payload.get("channel_id")
    if isinstance(channel_id, str) and _SNOWFLAKE.fullmatch(channel_id):
        return channel_id
    channel = payload.get("channel")
    if isinstance(channel, dict):
        nested = channel.get("id")
        if isinstance(nested, str) and _SNOWFLAKE.fullmatch(nested):
            return nested
    return ""


def is_bot_dm(payload: Mapping[str, Any]) -> bool:
    """Whether this ran in the direct message between the person and the app: not in a server,
    and, when Discord says which kind of place it was, not a group chat or someone else's DM."""
    if payload.get("guild_id") is not None:
        return False
    context = payload.get("context")
    return context is None or context == commands.INTERACTION_CONTEXT_BOT_DM


def _option_values(data: Mapping[str, Any]) -> dict[str, Any]:
    options = data.get("options")
    values: dict[str, Any] = {}
    if isinstance(options, list):
        for option in options:
            if isinstance(option, dict) and isinstance(option.get("name"), str):
                values[option["name"]] = option.get("value")
    return values


def _one_line(value: object) -> str:
    """A single-line option value: every run of whitespace, line breaks included, is one space.
    A line break in a title would otherwise let a person start a new field of the job text."""
    return " ".join(value.split()) if isinstance(value, str) else ""


def _job_text(values: Mapping[str, Any]) -> str:
    title = _one_line(values.get(commands.OPT_TITLE))
    company = _one_line(values.get(commands.OPT_COMPANY))
    location = _one_line(values.get(commands.OPT_LOCATION))
    url = _one_line(values.get(commands.OPT_URL))
    description = values.get(commands.OPT_DESCRIPTION)
    lines = [f"Title: {title}", f"Company: {company}"]
    if location:
        lines.append(f"Location: {location}")
    if url:
        lines.append(f"URL: {url}")
    body = description.strip() if isinstance(description, str) else ""
    return "\n".join(lines) + "\n\n" + body


def _command_text(name: str, values: Mapping[str, Any]) -> str | None:
    """The sentence the Telegram bot would have been sent for this command, or None for a
    command this app does not define."""
    if name == commands.LINK:
        code = "".join(str(values.get(commands.OPT_CODE) or "").split())
        return f"/link {code}" if code else "/link"
    if name == commands.UNLINK:
        return "/unlink"
    if name == commands.LIST:
        return "list"
    if name == commands.APPLY:
        number = values.get(commands.OPT_NUMBER)
        if isinstance(number, int) and not isinstance(number, bool) and number >= 1:
            return f"apply to {number}"
        return "apply"
    if name == commands.JOB:
        return _job_text(values)
    if name == commands.IMPORT or name == commands.SETUP:
        # A file that is not a resume JSON is left alone by the logic, and "/setup" is then
        # what answers it: the template and how to fill it in.
        return "/setup"
    if name == commands.RESUME:
        return "check my resume"
    if name == commands.PRIVACY:
        return "/privacy"
    if name == commands.LEARN:
        return "/learn"
    return None


def _attachment_of(data: Mapping[str, Any], values: Mapping[str, Any]) -> Attachment | None:
    attachment_id = values.get(commands.OPT_FILE)
    resolved = data.get("resolved")
    files = resolved.get("attachments") if isinstance(resolved, dict) else None
    if not isinstance(attachment_id, str) or not isinstance(files, dict):
        return None
    file = files.get(attachment_id)
    if not isinstance(file, dict):
        return None
    size = file.get("size")
    return Attachment(
        kind="document",
        filename=str(file.get("filename") or ""),
        mime_type=str(file.get("content_type") or ""),
        declared_size=size if isinstance(size, int) and not isinstance(size, bool) else None,
        handle=str(file.get("url") or ""),
    )


def parse_interaction(payload: Mapping[str, Any]) -> InboundMessage | None:
    """The interaction as the business logic sees it, or None if it is not something the app acts
    on. Raises `MalformedInteraction` for a command or a button tap that lacks an id, a sender or
    its data."""
    kind = interaction_kind(payload)
    if kind in (InteractionKind.PING, InteractionKind.OTHER):
        return None

    interaction_id = _snowflake(payload.get("id"), "id")
    subject = _sender(payload)
    chat_ref = _channel_of(payload)
    private = is_bot_dm(payload)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise MalformedInteraction("the interaction has no data")

    if kind is InteractionKind.COMPONENT:
        if data.get("component_type") != _COMPONENT_BUTTON:
            return None
        message = payload.get("message")
        message_id = message.get("id") if isinstance(message, dict) else None
        message_ref = (
            MessageRef(chat_ref, message_id)
            if isinstance(message_id, str) and _SNOWFLAKE.fullmatch(message_id)
            else None
        )
        custom_id = data.get("custom_id")
        return InboundMessage(
            channel=CHANNEL,
            subject=subject,
            chat_ref=chat_ref,
            message_id=None,
            update_id=interaction_id,
            text="",
            callback=Callback(
                id=interaction_id,
                data=custom_id if isinstance(custom_id, str) else "",
                message_ref=message_ref,
            ),
            is_private=private,
        )

    name = data.get("name")
    if not isinstance(name, str):
        raise MalformedInteraction("the command has no name")
    values = _option_values(data)
    text = _command_text(name, values)
    if text is None:
        return None
    return InboundMessage(
        channel=CHANNEL,
        subject=subject,
        chat_ref=chat_ref,
        message_id=None,
        update_id=interaction_id,
        text=text,
        attachment=_attachment_of(data, values) if name == commands.IMPORT else None,
        is_private=private,
    )


# -- the HTTP response to Discord (the part the webhook sends back at once) ---------------------


def pong_response() -> dict[str, Any]:
    return {"type": _RESPONSE_PONG}


def deferred_response(kind: InteractionKind, *, ephemeral: bool) -> dict[str, Any]:
    """The answer that buys time: "thinking" for a command (ephemeral when asked, which is
    fixed by this first answer and cannot be changed later), a silent deferral for a button."""
    if kind is InteractionKind.COMPONENT:
        return {"type": _RESPONSE_DEFERRED_UPDATE}
    if ephemeral:
        return {"type": _RESPONSE_DEFERRED_MESSAGE, "data": {"flags": FLAG_EPHEMERAL}}
    return {"type": _RESPONSE_DEFERRED_MESSAGE}


def immediate_response(text: str, *, ephemeral: bool = True) -> dict[str, Any]:
    """A complete answer given in the HTTP response itself, for what needs no work: a refusal.
    `text` is plain text, shown as written."""
    data: dict[str, Any] = {
        "content": escape_markdown(text)[:MESSAGE_LIMIT],
        "allowed_mentions": _no_mentions(),
    }
    if ephemeral:
        data["flags"] = FLAG_EPHEMERAL
    return {"type": _RESPONSE_MESSAGE, "data": data}


# -- outbound: message bodies ---------------------------------------------------------------------


def _no_mentions() -> dict[str, Any]:
    return {"parse": []}


def components_for(rows: ButtonRows) -> list[dict[str, Any]]:
    """The buttons as Discord action rows, held to Discord's limits. A row of more than five
    buttons becomes several rows; more than five rows, a label that is empty, or a `custom_id`
    that is empty or longer than 100 characters is a bug in whatever made the buttons, and raises
    (cutting a `custom_id` would hand back data nobody wrote). A label longer than 80 characters
    is cut, since it is only a caption."""
    action_rows: list[dict[str, Any]] = []
    for row in rows:
        for start in range(0, len(row), MAX_BUTTONS_PER_ROW):
            chunk = row[start : start + MAX_BUTTONS_PER_ROW]
            action_rows.append(
                {
                    "type": _COMPONENT_ACTION_ROW,
                    "components": [
                        _button(button, first=index == 0) for index, button in enumerate(chunk)
                    ],
                }
            )
    if len(action_rows) > MAX_BUTTON_ROWS:
        raise ValueError(f"a message holds at most {MAX_BUTTON_ROWS} rows of buttons")
    return action_rows


def _cut_utf16(text: str, units: int) -> str:
    """The longest start of `text` that is at most `units` UTF-16 code units: Discord counts a
    character outside the Basic Multilingual Plane (an emoji) as two, which slicing by character
    does not."""
    used = 0
    for index, char in enumerate(text):
        used += 2 if ord(char) > 0xFFFF else 1
        if used > units:
            return text[:index]
    return text


def _button(button: Button, *, first: bool) -> dict[str, Any]:
    label = scrub(button.label).strip()
    if not label:
        raise ValueError("a button needs a label")
    if not button.data or len(button.data) > MAX_CUSTOM_ID:
        raise ValueError(f"a button's data must be 1 to {MAX_CUSTOM_ID} characters")
    if utf16_length(label) > MAX_BUTTON_LABEL:
        label = _cut_utf16(label, MAX_BUTTON_LABEL - 1) + "…"
    return {
        "type": _COMPONENT_BUTTON,
        "style": _BUTTON_PRIMARY if first else _BUTTON_SECONDARY,
        "label": label,
        "custom_id": button.data,
    }


def message_bodies(
    text: RichText, buttons: ButtonRows = (), *, edit: bool = False
) -> list[dict[str, Any]]:
    """The request bodies that carry one message: one per part, the buttons on the last. For an
    EDIT the first body always states its components, an empty list when there are none, because
    that is how Discord is told to remove the buttons the message had."""
    contents = split_markdown(localize(text))
    action_rows = components_for(buttons)
    bodies: list[dict[str, Any]] = []
    for index, content in enumerate(contents):
        body: dict[str, Any] = {"content": content, "allowed_mentions": _no_mentions()}
        if index == len(contents) - 1 and action_rows:
            body["components"] = action_rows
        bodies.append(body)
    if edit and "components" not in bodies[0]:
        bodies[0]["components"] = []
    return bodies


def _document_body(intent: SendDocument, filename: str) -> dict[str, Any]:
    caption = scrub(intent.caption) if intent.caption else ""
    content = escape_markdown(caption)
    while utf16_length(content) > MESSAGE_LIMIT:
        caption = caption[: max(1, len(caption) - max(1, utf16_length(content) - MESSAGE_LIMIT))]
        content = escape_markdown(caption)
    body: dict[str, Any] = {
        "allowed_mentions": _no_mentions(),
        "attachments": [{"id": 0, "filename": filename}],
    }
    if content:
        body["content"] = content
    return body


def safe_filename(name: str) -> str:
    cleaned = _FILENAME_UNSAFE.sub("_", name.rsplit("/", 1)[-1])[:100].lstrip(".")
    return cleaned or "file"


# -- outbound: the renderer ---------------------------------------------------------------------


class DiscordInteractionRenderer:
    """`Renderer` for ONE interaction. See the module docstring for how it uses the interaction's
    first message, its follow-ups and its token."""

    def __init__(
        self,
        client: DiscordClient,
        *,
        token: str,
        ephemeral: bool,
        replaces_original: bool,
        recipient_id: str,
    ) -> None:
        self._client = client
        self._token = token
        self._ephemeral = ephemeral
        self._original_pending = replaces_original
        self._recipient_id = recipient_id
        self._token_dead = False

    def __repr__(self) -> str:
        # Never the token.
        return f"DiscordInteractionRenderer(ephemeral={self._ephemeral})"

    @property
    def answered(self) -> bool:
        """Whether anything has been shown for this interaction yet. A command's placeholder
        ("thinking") is not an answer."""
        return not self._original_pending

    # -- delivery ------------------------------------------------------------------------

    def _followup_body(self, body: dict[str, Any]) -> dict[str, Any]:
        return {**body, "flags": FLAG_EPHEMERAL} if self._ephemeral else body

    async def _bot_channel(self, chat_ref: str) -> str | None:
        """The channel to send to with the bot token when the interaction's token cannot be
        used: the conversation itself, in a direct message. Nothing that was ephemeral is ever
        sent this way (it would stop being private), and nothing at all when there is no bot
        token."""
        if self._ephemeral or not self._client.has_bot_token:
            return None
        if chat_ref and _SNOWFLAKE.fullmatch(chat_ref):
            return chat_ref
        return await self._client.open_dm(self._recipient_id)

    def _mark_token_dead(self) -> None:
        if not self._token_dead:
            self._token_dead = True
            logger.warning(
                "the interaction token no longer works",
                extra={"ctx": {"bot_fallback": self._client.has_bot_token and not self._ephemeral}},
            )

    async def _new_message(
        self,
        chat_ref: str,
        body: dict[str, Any],
        *,
        files: list[tuple[str, bytes]] | None = None,
    ) -> MessageRef | None:
        if not self._token_dead:
            try:
                if self._original_pending:
                    await self._client.edit_interaction_message(
                        self._token, "@original", body, files=files
                    )
                    self._original_pending = False
                    return MessageRef(chat_ref, "@original")
                message_id = await self._client.create_followup(
                    self._token, self._followup_body(body), files=files
                )
                return MessageRef(chat_ref, message_id) if message_id else None
            except DiscordTokenExpired:
                self._mark_token_dead()
        channel = await self._bot_channel(chat_ref)
        if channel is None:
            logger.warning(
                "a reply could not be delivered: the interaction token expired and there is no "
                "bot token to send it another way"
            )
            raise DiscordTokenExpired("send", 404, None)
        message_id = await self._client.create_channel_message(
            channel, {k: v for k, v in body.items() if k != "flags"}, files=files
        )
        self._original_pending = False
        return MessageRef(channel, message_id) if message_id else None

    async def send(self, chat_ref: str, intent: Say) -> MessageRef | None:
        first: MessageRef | None = None
        for index, body in enumerate(message_bodies(intent.text, intent.buttons)):
            ref = await self._new_message(chat_ref, body)
            if index == 0:
                first = ref
        return first

    async def send_document(self, chat_ref: str, intent: SendDocument) -> MessageRef | None:
        filename = safe_filename(intent.filename)
        return await self._new_message(
            chat_ref,
            _document_body(intent, filename),
            files=[(filename, intent.content)],
        )

    async def edit(self, intent: EditMessage) -> MessageRef | None:
        ref = intent.message_ref
        bodies = message_bodies(intent.text, intent.buttons, edit=True)
        if self._token_dead:
            await self._edit_without_token(ref, bodies[0])
        else:
            try:
                await self._client.edit_interaction_message(self._token, ref.message_id, bodies[0])
                if ref.message_id == "@original":
                    self._original_pending = False
            except DiscordTokenExpired:
                self._mark_token_dead()
                await self._edit_without_token(ref, bodies[0])
        for body in bodies[1:]:
            await self._new_message(ref.chat_ref, body)
        return ref

    async def _edit_without_token(self, ref: MessageRef, body: dict[str, Any]) -> None:
        """What an edit can still do once the interaction token is gone: edit a message the bot
        sent with the bot token. The interaction's own first message (`@original`) has no id to
        name it by, so that one cannot be edited, and the caller (a progress message) falls back
        to sending a new one."""
        channel = await self._bot_channel(ref.chat_ref)
        if channel is None or ref.message_id == "@original":
            raise DiscordTokenExpired("edit_message", 404, None)
        await self._client.edit_channel_message(channel, ref.message_id, body)

    async def ack_callback(self, intent: AckCallback) -> None:
        # The HTTP response to the tap already acknowledged it (a deferred update), and Discord
        # has no toast to attach a note to. A note, when there is one, is a message only the
        # person who tapped can see.
        if intent.text is None or self._token_dead:
            return
        body = {
            "content": escape_markdown(intent.text)[:MESSAGE_LIMIT],
            "allowed_mentions": _no_mentions(),
            "flags": FLAG_EPHEMERAL,
        }
        try:
            await self._client.create_followup(self._token, body)
        except DiscordTokenExpired:
            self._mark_token_dead()

    async def fetch_attachment(self, attachment: Attachment, *, max_bytes: int) -> bytes:
        if not attachment.handle:
            raise ValueError("the attachment has no address to fetch it by")
        try:
            return await self._client.download_attachment(attachment.handle, max_bytes=max_bytes)
        except DownloadTooLarge as e:
            raise AttachmentTooLarge(max_bytes) from e

    async def settle(self) -> None:
        """Ends the "thinking" placeholder of a command that finished without saying anything,
        so a person is never left looking at it."""
        if self._original_pending:
            await self._new_message("", _plain_body("✅ Done."))


def _plain_body(text: str) -> dict[str, Any]:
    return {"content": escape_markdown(text), "allowed_mentions": _no_mentions()}


def build_renderer(
    client: DiscordClient, payload: Mapping[str, Any], message: InboundMessage
) -> DiscordInteractionRenderer:
    """The renderer for this interaction, from its token. Raises `MalformedInteraction` when the
    interaction carries none."""
    token = payload.get("token")
    if not isinstance(token, str) or not token:
        raise MalformedInteraction("the interaction has no token")
    return DiscordInteractionRenderer(
        client,
        token=token,
        ephemeral=not message.is_private,
        replaces_original=message.callback is None,
        recipient_id=message.subject,
    )


# -- proactive --------------------------------------------------------------------------------


class DiscordNotifier:
    """`Notifier` for Discord: finds the Discord account the user linked, opens a direct message
    with it using the bot token, and says it there.

    A Discord user id is not a chat: a conversation with the bot has its own channel id, which is
    why this opens one (`DiscordClient.open_dm` returns the existing one when there is one)
    instead of sending to `channel_identity.get_chat_ref`'s value.

    Whether the bot may do that is Discord's decision, made from the person's own privacy
    settings and not ours to override. Discord's support pages say what stops one account messaging
    another: no server in common, direct messages turned off for the shared server, the person only
    accepting messages from friends, or a block. The developer documentation names the API's answer
    to a refused message, error 50007 ("Cannot send messages to this user"), and 40003 ("You are
    opening direct messages too fast") when too many conversations are opened too quickly. A refusal
    raises here with that code in the error (`DiscordApiError.code`), which `FanOutNotifier` logs
    against the channel and the user and counts as one channel failing: nothing is sent on Discord,
    the person's other channels still get the message, and nothing is retried. Discord's
    documentation does not say how those rules apply to an app a person installed to their own
    account only (without adding the bot to any server they are in), so nothing here assumes it
    can or cannot message them: the attempt is made and the answer decides. Nothing here is a way
    round a person's settings; a person who wants these messages needs, at least, to allow direct
    messages from the app's bot."""

    def __init__(self, supabase: AsyncClient, client: DiscordClient) -> None:
        self._supabase = supabase
        self._client = client

    async def notify(self, user_id: str, text: RichText) -> bool:
        subject = await get_chat_ref(self._supabase, user_id, CHANNEL)
        if subject is None:
            return False
        channel_id = await self._client.open_dm(subject)
        for body in message_bodies(text):
            await self._client.create_channel_message(channel_id, body)
        return True


def build_notifier(supabase: AsyncClient, client: DiscordClient | None) -> DiscordNotifier | None:
    """The notifier for a server's Discord app, or None on a server with no bot token (nothing
    can be pushed without one)."""
    if client is None or not client.has_bot_token:
        return None
    return DiscordNotifier(supabase, client)
