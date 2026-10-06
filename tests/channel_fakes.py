"""Fakes for exercising the channel-neutral core (`channel_core`) without Telegram.

`FakeRenderer` is a `Renderer` that records the intents it is given, so a test reads what the
business logic asked for in the vocabulary of `channel_envelope` -- never a request body.
`ComposedSupabase` is the webhook tests' fake Supabase client plus the tables and storage a
resume generation reads and writes (so one fake serves both the quick commands and the
prepare flow)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import test_telegram_prepare_callback as prepare_fakes
import test_telegram_webhook as webhook_fakes

from between_jobs.api.channel_envelope import (
    AckCallback,
    Attachment,
    AttachmentTooLarge,
    Callback,
    EditMessage,
    InboundMessage,
    MessageRef,
    Say,
    SendDocument,
)

USER_ID = prepare_fakes._USER_ID
APPLICATION_ID = prepare_fakes._APPLICATION_ID
# The sender, the conversation and the user they resolve to are three different values on
# purpose: all of them are text (or a uuid string), so a mix-up between them is invisible to
# the type checker, and the only thing that can catch one is a test whose values differ.
CHAT_REF = "222333444"
SUBJECT = "555000111"


def user_id_for(subject: str) -> str:
    """The user id `ComposedSupabase(distinct_users=True)` resolves `subject` to: stable, and
    different for every subject."""
    return str(uuid.uuid5(uuid.NAMESPACE_OID, f"telegram:{subject}"))


class _PerSubjectLookup:
    def __init__(self) -> None:
        self._subject: str | None = None

    def eq(self, column: str, value: Any) -> _PerSubjectLookup:
        if column == "external_subject":
            self._subject = str(value)
        return self

    async def execute(self) -> SimpleNamespace:
        assert self._subject is not None, "the identity lookup never filtered on a subject"
        return SimpleNamespace(data=[{"user_id": user_id_for(self._subject)}])


class _PerSubjectIdentities(webhook_fakes._FakeChannelIdentitiesTable):
    """`channel_identities` in which every sender is a different user, found by the subject
    the lookup filters on -- for tests where "another person" has to be another user."""

    def __init__(self) -> None:
        super().__init__([])

    def select(self, columns: str) -> Any:
        return _PerSubjectLookup()


class FakeRenderer:
    """Records every intent; returns a `MessageRef` for each message it "sends".

    `shown` is what the person would see in the chat once everything has been applied: each
    message sent, in order, with an edit replacing the text (and buttons) of the message it
    names. It is how a test says "the progress message ended as ..." without caring which
    calls got it there."""

    def __init__(
        self,
        *,
        attachment_bytes: bytes | None = None,
        attachment_too_large: bool = False,
    ) -> None:
        self.sent: list[tuple[str, Say]] = []
        self.documents: list[tuple[str, SendDocument]] = []
        self.edits: list[EditMessage] = []
        self.acks: list[AckCallback] = []
        self.fetches: list[tuple[Attachment, int]] = []
        self._attachment_bytes = attachment_bytes
        self._attachment_too_large = attachment_too_large
        self._shown: dict[tuple[str, str], Say] = {}
        # Every call in order, across kinds: ("say" | "document" | "edit" | "ack" | "fetch").
        self.calls: list[str] = []

    @property
    def texts(self) -> list[str]:
        return [intent.text.plain_text() for _chat, intent in self.sent]

    @property
    def shown(self) -> list[Say]:
        """The messages as the person sees them now: sends in order, edits applied."""
        return list(self._shown.values())

    @property
    def shown_texts(self) -> list[str]:
        return [say.text.plain_text() for say in self.shown]

    async def send(self, chat_ref: str, intent: Say) -> MessageRef | None:
        self.calls.append("say")
        self.sent.append((chat_ref, intent))
        ref = MessageRef(chat_ref, str(len(self.sent)))
        self._shown[(ref.chat_ref, ref.message_id)] = intent
        return ref

    async def send_document(self, chat_ref: str, intent: SendDocument) -> MessageRef | None:
        self.calls.append("document")
        self.documents.append((chat_ref, intent))
        return MessageRef(chat_ref, f"doc{len(self.documents)}")

    async def edit(self, intent: EditMessage) -> MessageRef | None:
        self.calls.append("edit")
        self.edits.append(intent)
        ref = intent.message_ref
        self._shown[(ref.chat_ref, ref.message_id)] = Say(intent.text, intent.buttons)
        return ref

    async def ack_callback(self, intent: AckCallback) -> None:
        self.calls.append("ack")
        self.acks.append(intent)

    async def fetch_attachment(self, attachment: Attachment, *, max_bytes: int) -> bytes:
        self.calls.append("fetch")
        self.fetches.append((attachment, max_bytes))
        if self._attachment_too_large:
            raise AttachmentTooLarge(max_bytes)
        assert self._attachment_bytes is not None, "no attachment configured for this test"
        return self._attachment_bytes


def inbound(
    text: str = "",
    *,
    attachment: Attachment | None = None,
    callback_data: str | None = None,
    is_private: bool = True,
    update_id: str | None = "1",
    subject: str = SUBJECT,
    channel: str = "telegram",
) -> InboundMessage:
    """An `InboundMessage` made directly: the core never sees a provider's JSON."""
    return InboundMessage(
        channel=channel,
        subject=subject,
        chat_ref=CHAT_REF,
        message_id="7",
        update_id=update_id,
        text=text,
        attachment=attachment,
        callback=(
            Callback("cbq-1", callback_data, MessageRef(CHAT_REF, "6"))
            if callback_data is not None
            else None
        ),
        is_private=is_private,
    )


class ComposedSupabase(webhook_fakes._FakeSupabaseClient):
    """The webhook tests' fake, plus what a resume generation reads and writes.

    Every sender is the one user `USER_ID` unless `distinct_users` is set, which makes each
    subject a different user (`user_id_for`)."""

    def __init__(self, *, distinct_users: bool = False, **overrides: Any) -> None:
        application = {**webhook_fakes._APP_1, "id": APPLICATION_ID}
        kwargs: dict[str, Any] = {
            "channel_identities_rows": [{"user_id": USER_ID}],
            "applications": webhook_fakes._FakeSimpleTable(select_rows=[application]),
            "job_snapshots": webhook_fakes._FakeSimpleTable(
                select_rows=[prepare_fakes._SNAPSHOT_ROW]
            ),
            "profile_versions": webhook_fakes._FakeProfileVersionsTable(
                select_rows=[prepare_fakes._PROFILE_VERSION_ROW]
            ),
            "working_sets": webhook_fakes._FakeSimpleTable(
                select_rows=[
                    {
                        "id": "ws-1",
                        "items": [APPLICATION_ID],
                        "expires_at": "2999-01-01T00:00:00+00:00",
                    }
                ]
            ),
        }
        kwargs.update(overrides)
        super().__init__(**kwargs)
        if distinct_users:
            self.channel_identities = _PerSubjectIdentities()
        self._extra: dict[str, Any] = {
            "capability_preferences": prepare_fakes._FakeTable(
                select_rows=[prepare_fakes._PREFERENCE_ROW]
            ),
            "provider_credentials": prepare_fakes._FakeTable(
                select_rows=[prepare_fakes._CREDENTIAL_ROW]
            ),
            "artifact_versions": prepare_fakes._FakeTable(
                select_rows=[prepare_fakes._ARTIFACT_VERSION_ROW],
                insert_row=prepare_fakes._ARTIFACT_VERSION_ROW,
            ),
            "resume_documents": prepare_fakes._FakeTable(select_rows=[]),
            "application_events": prepare_fakes._FakeTable(
                select_rows=[], insert_row={"id": "event-1"}
            ),
            "event_outbox": prepare_fakes._FakeTable(select_rows=[]),
            "saved_searches": prepare_fakes._FakeTable(select_rows=[]),
        }
        self.storage = prepare_fakes._FakeStorage(prepare_fakes._FakeBucket())

    def table(self, name: str) -> Any:
        return self._extra.get(name) or super().table(name)

    def rpc(self, fn: str, params: dict[str, Any]) -> Any:
        if fn == "decrypt_secret":
            return prepare_fakes._FakeRpcBuilder("sk-or-v1-real-secret")
        return super().rpc(fn, params)
