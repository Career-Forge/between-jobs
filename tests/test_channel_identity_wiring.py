"""The core uses the right one of three values that look alike.

A message carries who sent it (`subject`) and where the conversation is (`chat_ref`); the
user those resolve to is a third value. All three are text, so a type checker cannot tell
them apart, and a slip is easy to make and invisible in a test whose values happen to be
equal. Here they all differ, and every flow the bot has is driven while the calls that take
a user id are watched:

- identity is resolved from exactly the channel and the sender, and from nothing else;
- every store, limit and generation call is made for the user that identity resolved to --
  never for the sender's own id, never for the chat's;
- every reply and every document goes to the conversation, never to the sender's id.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import test_telegram_webhook as webhook_fakes
from channel_fakes import (
    APPLICATION_ID,
    CHAT_REF,
    SUBJECT,
    USER_ID,
    ComposedSupabase,
    inbound,
)
from test_channel_core import _PREVIEW_ROW, _VERSION_ID, _Engine, _handle

from between_jobs.api import channel_core
from between_jobs.api.channel_envelope import InboundMessage
from between_jobs.api.telegram_identity import resolve_or_create_user_id_for_subject

# Where the user id is among the positional arguments of each function the core calls with it.
_USER_ID_ARGUMENT = {
    "activate_version": 1,
    "delete_pending_version": 1,
    "create_pending_version": 1,
    "get_active_version": 1,
    "create_application": 1,
    "list_applications": 1,
    "get_active_working_set": 1,
    "create_working_set": 1,
    "resolve_reference": 1,
    "change_stage": 1,
    "rate_limit_error_or_none": 1,
    "is_auto_provisioned_for_subject": 1,
    "load_first_run_facts": 1,
    "run_prepare_application": 2,
    "latest_resume_pdf": 2,
}

_RESUME_CALLS = {"run_prepare_application", "latest_resume_pdf", "rate_limit_error_or_none"}


def _flows() -> dict[str, tuple[InboundMessage, dict[str, Any], set[str]]]:
    """Each flow: the message, how to build its fake database, and the user-taking functions
    it must reach (so a flow that stops short of the code under test fails, not passes)."""
    paste_supabase = {
        "applications": webhook_fakes._FakeSimpleTable(select_rows=[]),
    }
    import_supabase = {
        "profile_versions": webhook_fakes._FakeProfileVersionsTable(
            select_rows=[], insert_row=_PREVIEW_ROW
        ),
    }
    return {
        "check the resume": (inbound("check my resume"), {}, {"get_active_version"}),
        "import a resume": (
            inbound(webhook_fakes._VALID_RESUME_JSON),
            import_supabase,
            {"create_pending_version"},
        ),
        "confirm a preview": (
            inbound(callback_data=f"profile:activate:{_VERSION_ID}"),
            {},
            {"activate_version"},
        ),
        "cancel a preview": (
            inbound(callback_data=f"profile:cancel:{_VERSION_ID}"),
            {},
            {"delete_pending_version"},
        ),
        "track a job": (
            inbound("Title: Engineer\nCompany: Acme\n\nA description."),
            paste_supabase,
            {"create_application"},
        ),
        "list applications": (
            inbound("list"),
            {},
            {"list_applications", "create_working_set"},
        ),
        "apply to a number": (
            inbound("apply to #1"),
            {},
            {"get_active_working_set", "resolve_reference"} | _RESUME_CALLS,
        ),
        "tap generate": (
            inbound(callback_data=f"app:prepare:{APPLICATION_ID}"),
            {},
            _RESUME_CALLS,
        ),
        "tap mark as applied": (
            inbound(callback_data=f"app:stage:{APPLICATION_ID}:applied"),
            {"rpc_data": {"id": APPLICATION_ID, "status": "applied"}},
            {"change_stage"},
        ),
        "unlink": (inbound("/unlink"), {}, {"is_auto_provisioned_for_subject"}),
        "privacy": (inbound("/privacy"), {}, {"is_auto_provisioned_for_subject"}),
        "learn": (
            inbound("/learn"),
            {},
            {"is_auto_provisioned_for_subject", "load_first_run_facts"},
        ),
    }


_FLOWS = _flows()


def test_the_three_values_are_different() -> None:
    assert len({SUBJECT, CHAT_REF, USER_ID}) == 3
    message = inbound("hi")
    assert (message.subject, message.chat_ref) == (SUBJECT, CHAT_REF)


@pytest.mark.parametrize("flow", sorted(_FLOWS))
async def test_every_call_is_for_the_resolved_user_and_every_reply_is_for_the_conversation(
    flow: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    message, supabase_kwargs, expected_calls = _FLOWS[flow]
    user_arguments: list[tuple[str, Any]] = []

    def spy_on(name: str, index: int) -> Callable[..., Any]:
        original = getattr(channel_core, name)

        async def spy(*args: Any, **kwargs: Any) -> Any:
            user_arguments.append((name, args[index]))
            if name == "is_auto_provisioned_for_subject":
                return False  # a linked account, so /unlink, /privacy and /learn go through
            return await original(*args, **kwargs)

        return spy

    for name, index in _USER_ID_ARGUMENT.items():
        monkeypatch.setattr(channel_core, name, spy_on(name, index))

    identity_lookups: list[tuple[str, str]] = []

    async def resolve(supabase: Any, channel: str, subject: str) -> str:
        identity_lookups.append((channel, subject))
        return await resolve_or_create_user_id_for_subject(supabase, channel, subject)

    monkeypatch.setattr(channel_core, "resolve_or_create_user_id_for_subject", resolve)

    unlinked: list[tuple[str, str]] = []

    async def unlink(_supabase: Any, channel: str, subject: str) -> None:
        unlinked.append((channel, subject))

    monkeypatch.setattr(channel_core, "unlink_subject", unlink)

    renderer, _supabase, registry = await _handle(
        message, supabase=ComposedSupabase(**supabase_kwargs), http=_Engine()
    )
    await registry.shutdown(grace_seconds=5)

    assert identity_lookups == [("telegram", SUBJECT)]
    assert {name for name, _argument in user_arguments} >= expected_calls
    for name, argument in user_arguments:
        assert argument == USER_ID, f"{name} was given {argument!r}, not the resolved user"
    if flow == "unlink":
        assert unlinked == [("telegram", SUBJECT)]  # the sender's own identity, not the chat's
    assert renderer.sent or renderer.documents or renderer.acks, "the flow said nothing"
    for chat, _intent in renderer.sent:
        assert chat == CHAT_REF
    for chat, _document in renderer.documents:
        assert chat == CHAT_REF
