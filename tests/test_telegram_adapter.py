"""The Telegram adapter: the only module that reads a raw update or writes a request body.

Three halves: `parse_update` (an update's JSON -> an `InboundMessage`), the renderer
(intents -> `TelegramClient` calls, with the HTML escaping and keyboard JSON that go with
them) and the notifier (a user id -> the chat they linked)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

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
    code,
    italic,
    pre,
    rich,
)
from between_jobs.api.telegram_adapter import (
    TelegramNotifier,
    TelegramRenderer,
    build_notifier,
    inline_keyboard,
    parse_update,
    render_html,
    update_id_of,
)
from between_jobs.api.telegram_client import DownloadTooLarge, Html, TelegramClient

_USER = 987654321
_CHAT = 987654321


def _message(**fields: Any) -> dict[str, Any]:
    message: dict[str, Any] = {
        "message_id": 11,
        "from": {"id": _USER, "is_bot": False},
        "chat": {"id": _CHAT, "type": "private"},
    }
    message.update(fields)
    return {"update_id": 5, "message": message}


def _callback(data: str | None = "x") -> dict[str, Any]:
    callback_query: dict[str, Any] = {
        "id": "cbq-1",
        "from": {"id": _USER},
        "message": {"message_id": 12, "chat": {"id": _CHAT, "type": "private"}},
    }
    if data is not None:
        callback_query["data"] = data
    return {"update_id": 6, "callback_query": callback_query}


# -- update ids ----------------------------------------------------------------------------


def test_the_update_id_is_the_int_telegram_sent() -> None:
    assert update_id_of({"update_id": 123}) == 123


@pytest.mark.parametrize("bad", [None, True, False, "123", 1.5, [1]])
def test_anything_that_is_not_an_int_is_no_update_id(bad: object) -> None:
    assert update_id_of({"update_id": bad}) is None
    assert update_id_of({}) is None


# -- parsing: messages ---------------------------------------------------------------------


def test_a_text_message_becomes_an_inbound_message() -> None:
    inbound = parse_update(_message(text="hello"))

    assert inbound is not None
    assert inbound.channel == "telegram"
    assert inbound.subject == str(_USER)
    assert inbound.chat_ref == str(_CHAT)
    assert inbound.message_id == "11"
    assert inbound.update_id == "5"
    assert inbound.text == "hello"
    assert inbound.attachment is None
    assert inbound.callback is None
    assert inbound.is_private


def test_the_sender_and_the_conversation_are_separate_things() -> None:
    update = _message(text="hi")
    update["message"]["chat"] = {"id": -100123, "type": "supergroup"}

    inbound = parse_update(update)

    assert inbound is not None
    assert inbound.subject == str(_USER)  # who spoke
    assert inbound.chat_ref == "-100123"  # where a reply goes
    assert not inbound.is_private


def test_a_caption_stands_in_for_text() -> None:
    inbound = parse_update(_message(document={"file_id": "f"}, caption="list"))
    assert inbound is not None
    assert inbound.text == "list"
    # text wins when both are there
    both = parse_update(_message(text="a", caption="b", document={"file_id": "f"}))
    assert both is not None
    assert both.text == "a"


def test_a_document_becomes_an_attachment_with_an_opaque_handle() -> None:
    document = {
        "file_id": "file-123",
        "file_name": "resume.json",
        "mime_type": "application/json",
        "file_size": 2000,
    }

    inbound = parse_update(_message(document=document))

    assert inbound is not None
    assert inbound.text == ""
    assert inbound.attachment == Attachment(
        kind="document",
        filename="resume.json",
        mime_type="application/json",
        declared_size=2000,
        handle="file-123",
    )


def test_a_documents_missing_or_odd_fields_get_neutral_values() -> None:
    bare = parse_update(_message(document={"file_id": "f"}))
    assert bare is not None and bare.attachment is not None
    assert (bare.attachment.filename, bare.attachment.mime_type) == ("", "")
    assert bare.attachment.declared_size is None

    for size in ("huge", True, 1.5, None):
        odd = parse_update(_message(document={"file_id": "f", "file_size": size}))
        assert odd is not None and odd.attachment is not None
        assert odd.attachment.declared_size is None, size


@pytest.mark.parametrize(
    "update",
    [
        {"update_id": 1, "edited_message": {"text": "oops"}},
        {"update_id": 2, "channel_post": {"text": "oops"}},
        {"update_id": 3},
        {"update_id": 4, "message": "not a dict"},
        {"update_id": 5, "message": {"message_id": 1, "photo": [], "caption": "x"}},
    ],
)
def test_shapes_the_bot_never_acted_on_are_none(update: dict[str, Any]) -> None:
    assert parse_update(update) is None


def test_a_message_without_a_sender_or_chat_raises_as_it_always_did() -> None:
    """Telegram never sends one; a 500 is how a bug would be noticed."""
    with pytest.raises(KeyError):
        parse_update({"update_id": 1, "message": {"text": "hi", "chat": {"id": 1}}})
    with pytest.raises(KeyError):
        parse_update({"update_id": 1, "message": {"text": "hi", "from": {"id": 1}}})


def test_a_message_without_an_update_id_still_parses() -> None:
    update = _message(text="hi")
    del update["update_id"]
    inbound = parse_update(update)
    assert inbound is not None
    assert inbound.update_id is None


# -- parsing: callbacks --------------------------------------------------------------------


def test_a_button_tap_becomes_a_callback() -> None:
    inbound = parse_update(_callback("app:prepare:abc"))

    assert inbound is not None
    assert inbound.subject == str(_USER)
    assert inbound.chat_ref == str(_CHAT)
    assert inbound.text == ""
    assert inbound.attachment is None
    assert inbound.update_id == "6"
    assert inbound.callback is not None
    assert inbound.callback.id == "cbq-1"
    assert inbound.callback.data == "app:prepare:abc"
    assert inbound.callback.message_ref == MessageRef(str(_CHAT), "12")


def test_a_tap_in_a_group_is_the_senders_but_is_answered_in_the_group() -> None:
    update = _callback("app:prepare:abc")
    update["callback_query"]["from"] = {"id": 555000111}
    update["callback_query"]["message"]["chat"] = {"id": -100222333, "type": "supergroup"}

    inbound = parse_update(update)

    assert inbound is not None and inbound.callback is not None
    assert inbound.subject == "555000111"  # who tapped: the identity key
    assert inbound.chat_ref == "-100222333"  # where the reply goes
    assert inbound.callback.message_ref == MessageRef("-100222333", "12")


def test_a_callback_without_data_has_empty_data() -> None:
    inbound = parse_update(_callback(None))
    assert inbound is not None and inbound.callback is not None
    assert inbound.callback.data == ""


def test_a_callback_wins_over_a_message_in_the_same_update() -> None:
    update = {**_callback("x"), "message": _message(text="hi")["message"]}
    inbound = parse_update(update)
    assert inbound is not None and inbound.callback is not None


def test_a_callback_without_its_message_raises() -> None:
    update = _callback("x")
    del update["callback_query"]["message"]
    with pytest.raises(KeyError):
        parse_update(update)


# -- rendering text ------------------------------------------------------------------------


def test_plain_text_is_escaped() -> None:
    assert render_html(rich("a & b <c> d")) == "a &amp; b &lt;c&gt; d"


def test_quotes_and_apostrophes_are_left_alone() -> None:
    assert render_html(rich('it\'s "fine"')) == 'it\'s "fine"'


def test_each_style_is_its_telegram_tag() -> None:
    text = rich(bold("b"), italic("i"), code("c"), pre("p"))
    assert render_html(text) == "<b>b</b><i>i</i><code>c</code><pre>p</pre>"


def test_text_inside_a_style_is_escaped_too() -> None:
    assert render_html(rich(bold("<i>x</i> & y"))) == "<b>&lt;i&gt;x&lt;/i&gt; &amp; y</b>"
    assert render_html(rich(pre('{"a": "<b>"}'))) == '<pre>{"a": "&lt;b&gt;"}</pre>'


def test_rendered_text_is_checked_markup_whatever_went_in() -> None:
    nasty = rich("<", ">", "&amp;", bold("</b>"), "</i>")
    out = render_html(nasty)
    assert isinstance(out, Html)  # building an Html validates the tags and entities
    assert out == "&lt;&gt;&amp;amp;<b>&lt;/b&gt;</b>&lt;/i&gt;"


def test_no_text_renders_to_nothing() -> None:
    assert render_html(rich()) == ""


def test_the_keyboard_is_telegrams_inline_keyboard_json() -> None:
    rows = (
        (Button("Yes", "yes:1"), Button("No", "no:1")),
        (Button("Later", "later:1"),),
    )
    assert inline_keyboard(rows) == {
        "inline_keyboard": [
            [
                {"text": "Yes", "callback_data": "yes:1"},
                {"text": "No", "callback_data": "no:1"},
            ],
            [{"text": "Later", "callback_data": "later:1"}],
        ]
    }


# -- the renderer, against a recording client ---------------------------------------------


class _RecordingClient:
    def __init__(self, *, message_id: int | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.message_id = message_id
        self.download_error: Exception | None = None

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> int | None:
        self.calls.append(("send_message", (chat_id, text), kwargs))
        return self.message_id

    async def send_document(
        self, chat_id: int, filename: str, content: bytes, **kwargs: Any
    ) -> int | None:
        self.calls.append(("send_document", (chat_id, filename, content), kwargs))
        return self.message_id

    async def edit_message_text(
        self, chat_id: int, message_id: int, text: str, **kwargs: Any
    ) -> int | None:
        self.calls.append(("edit_message_text", (chat_id, message_id, text), kwargs))
        return self.message_id

    async def answer_callback_query(self, callback_query_id: str, **kwargs: Any) -> None:
        self.calls.append(("answer_callback_query", (callback_query_id,), kwargs))

    async def download_document(self, file_id: str, **kwargs: Any) -> bytes:
        self.calls.append(("download_document", (file_id,), kwargs))
        if self.download_error is not None:
            raise self.download_error
        return b"file bytes"


def _renderer(client: _RecordingClient) -> TelegramRenderer:
    return TelegramRenderer(client)  # type: ignore[arg-type]


async def test_say_sends_the_rendered_text_to_the_chat_as_an_int() -> None:
    client = _RecordingClient()

    ref = await _renderer(client).send("987654321", Say(rich("Hi ", bold("<x>"))))

    assert client.calls == [("send_message", (987654321, "Hi <b>&lt;x&gt;</b>"), {})]
    assert ref is None  # the client said nothing about a message id


async def test_say_with_buttons_sends_the_keyboard() -> None:
    client = _RecordingClient()
    buttons = ((Button("Go", "go:1"),),)

    await _renderer(client).send("-100123", Say(rich("pick"), buttons))

    assert client.calls[0][1][0] == -100123
    assert client.calls[0][2] == {"reply_markup": inline_keyboard(buttons)}


async def test_send_gives_back_the_message_ref_when_telegram_does() -> None:
    client = _RecordingClient(message_id=77)
    renderer = _renderer(client)

    assert await renderer.send("5", Say(rich("x"))) == MessageRef("5", "77")
    assert await renderer.send_document("5", SendDocument("a.pdf", b"x")) == MessageRef("5", "77")


async def test_send_document_passes_the_file_and_a_plain_caption_through() -> None:
    client = _RecordingClient()

    await _renderer(client).send_document(
        "987654321", SendDocument("resume.pdf", b"%PDF", caption="ATS <72> & more")
    )

    assert client.calls == [
        (
            "send_document",
            (987654321, "resume.pdf", b"%PDF"),
            {"caption": "ATS <72> & more"},  # a caption is not markup: never escaped
        )
    ]


async def test_edit_replaces_the_text_of_the_referenced_message() -> None:
    client = _RecordingClient(message_id=12)
    ref = MessageRef("987654321", "12")

    edited = await _renderer(client).edit(EditMessage(ref, rich("new ", italic("text"))))

    assert client.calls == [
        ("edit_message_text", (987654321, 12, "new <i>text</i>"), {"reply_markup": None})
    ]
    assert edited == ref


async def test_edit_can_give_the_message_new_buttons() -> None:
    client = _RecordingClient()
    buttons = ((Button("Undo", "undo:1"),),)

    await _renderer(client).edit(EditMessage(MessageRef("5", "6"), rich("x"), buttons))

    assert client.calls[0][2] == {"reply_markup": inline_keyboard(buttons)}


async def test_ack_without_text_is_just_the_id() -> None:
    client = _RecordingClient()

    await _renderer(client).ack_callback(AckCallback("cbq-1"))

    assert client.calls == [("answer_callback_query", ("cbq-1",), {})]


async def test_ack_with_text_shows_it_to_the_person_who_tapped() -> None:
    client = _RecordingClient()

    await _renderer(client).ack_callback(AckCallback("cbq-1", "Done"))

    assert client.calls == [("answer_callback_query", ("cbq-1",), {"text": "Done"})]


def _attachment(handle: str = "file-123") -> Attachment:
    return Attachment("document", "resume.json", "application/json", None, handle)


async def test_fetching_an_attachment_downloads_it_by_its_handle_under_the_cap() -> None:
    client = _RecordingClient()

    data = await _renderer(client).fetch_attachment(_attachment(), max_bytes=2000)

    assert data == b"file bytes"
    assert client.calls == [("download_document", ("file-123",), {"max_bytes": 2000})]


async def test_an_attachment_over_the_cap_is_the_neutral_too_large_error() -> None:
    client = _RecordingClient()
    client.download_error = DownloadTooLarge(2000)

    with pytest.raises(AttachmentTooLarge) as raised:
        await _renderer(client).fetch_attachment(_attachment(), max_bytes=2000)

    assert raised.value.max_bytes == 2000


async def test_an_attachment_with_no_handle_is_refused_before_any_request() -> None:
    client = _RecordingClient()

    with pytest.raises(ValueError, match="no file id"):
        await _renderer(client).fetch_attachment(_attachment(""), max_bytes=2000)

    assert client.calls == []


# -- the renderer, against the real client's wire format -----------------------------------


async def test_the_renderer_and_the_real_client_put_the_expected_json_on_the_wire() -> None:
    bodies: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append((request.url.path.rsplit("/", 1)[-1], json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 31}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    renderer = TelegramRenderer(TelegramClient(http, "test-token-not-real"))

    ref = await renderer.send(
        "987654321",
        Say(rich("✅ ", bold("a & b")), ((Button("Go", "go:1"),),)),
    )
    await renderer.edit(EditMessage(ref or MessageRef("0", "0"), rich("edited")))
    await renderer.ack_callback(AckCallback("cbq-1", "ok"))

    assert ref == MessageRef("987654321", "31")
    assert bodies == [
        (
            "sendMessage",
            {
                "chat_id": 987654321,
                "text": "✅ <b>a &amp; b</b>",
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": [[{"text": "Go", "callback_data": "go:1"}]]},
            },
        ),
        (
            "editMessageText",
            {"chat_id": 987654321, "message_id": 31, "text": "edited", "parse_mode": "HTML"},
        ),
        ("answerCallbackQuery", {"callback_query_id": "cbq-1", "text": "ok"}),
    ]


# -- the notifier --------------------------------------------------------------------------


class _Identities:
    def __init__(self, subject: str | None) -> None:
        self.rows = [{"external_subject": subject}] if subject is not None else []
        self.filters: list[tuple[str, Any]] = []

    def select(self, *_: Any, **__: Any) -> _Identities:
        return self

    def eq(self, column: str, value: Any) -> _Identities:
        self.filters.append((column, value))
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self.rows)


class _Supabase:
    def __init__(self, subject: str | None) -> None:
        self.identities = _Identities(subject)

    def table(self, name: str) -> _Identities:
        assert name == "channel_identities"
        return self.identities


async def test_the_notifier_says_it_in_the_chat_the_user_linked() -> None:
    client = _RecordingClient()
    supabase = _Supabase("555")
    notifier = TelegramNotifier(supabase, _renderer(client))  # type: ignore[arg-type]

    sent = await notifier.notify("user-1", rich("🎯 ", bold("New match <x>")))

    assert sent is True
    assert client.calls == [("send_message", (555, "🎯 <b>New match &lt;x&gt;</b>"), {})]
    assert ("user_id", "user-1") in supabase.identities.filters
    assert ("channel", "telegram") in supabase.identities.filters


async def test_the_notifier_sends_nothing_to_a_user_with_no_linked_chat() -> None:
    client = _RecordingClient()
    notifier = TelegramNotifier(_Supabase(None), _renderer(client))  # type: ignore[arg-type]

    assert await notifier.notify("user-1", rich("hi")) is False
    assert client.calls == []


async def test_a_refused_message_is_the_callers_to_handle() -> None:
    class _Refusing(_RecordingClient):
        async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> int | None:
            raise RuntimeError("the bot was blocked")

    notifier = TelegramNotifier(_Supabase("555"), _renderer(_Refusing()))  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="blocked"):
        await notifier.notify("user-1", rich("hi"))


def test_a_server_without_a_bot_has_no_notifier() -> None:
    assert build_notifier(_Supabase(None), None) is None  # type: ignore[arg-type]
    client = TelegramClient(httpx.AsyncClient(), "test-token-not-real")
    assert isinstance(build_notifier(_Supabase(None), client), TelegramNotifier)  # type: ignore[arg-type]
