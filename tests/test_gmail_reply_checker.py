"""Tests for the Gmail reply checker (Gmail reply/status parsing R3,
gmail-reply-status-parsing.md) -- the poller finally wiring R1 (`get_
thread`) and R2 (`classify_reply`) into something that runs. The LLM
call is faked via the module's own explicit `generate=llm_generate`
injection point (the R3-bug-informed convention every multi-stage
pipeline in this codebase already uses), letting the real `classify_
reply`/`_parse_status_proposal` grounding logic run for real rather than
mocking the classifier itself.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from postgrest.exceptions import APIError

from between_jobs.api.errors import ApiError
from between_jobs.api.gmail_client import GmailOauthConfig
from between_jobs.api.gmail_reply_checker import (
    _check_one_draft,
    _select_due_drafts,
    run_reply_check_once,
)
from between_jobs.api.llm_client import LLMResponse

_USER_ID = "00000000-0000-0000-0000-000000000001"
_APPLICATION_ID = "50000000-0000-0000-0000-000000000001"
_DRAFT_ID = "60000000-0000-0000-0000-000000000001"
_THREAD_ID = "18cabc1234def567"
_REPLY_TEXT = "Thanks for reaching out! We'd love to schedule a call this week to discuss the role."

_GMAIL_CREDENTIAL_ROW = {
    "provider": "gmail",
    "model": None,
    "base_url": None,
    "scope": "https://www.googleapis.com/auth/gmail.compose "
    "https://www.googleapis.com/auth/gmail.readonly",
    "secret_encrypted": "refresh-token-cipher",
    "secret_2_encrypted": None,
}
_LLM_CREDENTIAL_ROW = {
    "provider": "openrouter",
    "model": "anthropic/claude-sonnet-4-6",
    "base_url": None,
    "scope": None,
    "secret_encrypted": "llm-cipher",
    "secret_2_encrypted": None,
}
_PREFERENCE_ROW = {
    "user_id": _USER_ID,
    "capability": "default",
    "execution_mode": "byok_first_party",
    "provider": "openrouter",
    "model": "anthropic/claude-sonnet-4-6",
}


def _due_draft(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "draft_id": _DRAFT_ID,
        "user_id": _USER_ID,
        "application_id": _APPLICATION_ID,
        "gmail_thread_id": _THREAD_ID,
        "sent_confirmed_at": None,
        "last_reply_checked_at": None,
        "company_name": "Acme",
        "title": "Staff Engineer",
    }
    base.update(overrides)
    return base


def _gmail_message(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "msg-sent-1",
        "label_ids": ["SENT"],
        "internal_date_ms": 1_000_000,
        "snippet": "",
        "body_text": "",
    }
    base.update(overrides)
    return base


def _sent_and_reply_thread(
    *, reply_body_text: str = _REPLY_TEXT, reply_id: str = "msg-reply-1"
) -> dict[str, Any]:
    return {
        "id": _THREAD_ID,
        "messages": [
            _gmail_message(id="msg-sent-1", label_ids=["SENT"], internal_date_ms=1_000_000),
            _gmail_message(
                id=reply_id,
                label_ids=["INBOX"],
                internal_date_ms=2_000_000,
                body_text=reply_body_text,
            ),
        ],
    }


def _classifier_response(payload: dict[str, Any]) -> LLMResponse:
    return LLMResponse(content=json.dumps(payload))


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, column: str, value: Any) -> _ChainBuilder:
        return _ChainBuilder([r for r in self._rows if r.get(column) == value])

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _UpdateBuilder:
    def __init__(self, table: Any, data: dict[str, Any]) -> None:
        self._table = table
        self._data = data
        self._filters: dict[str, Any] = {}

    def eq(self, column: str, value: Any) -> _UpdateBuilder:
        self._filters[column] = value
        return self

    async def execute(self) -> SimpleNamespace:
        matched = [
            r for r in self._table.rows if all(r.get(k) == v for k, v in self._filters.items())
        ]
        for row in matched:
            row.update(self._data)
        return SimpleNamespace(data=matched)


class _FakeTable:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.rows)

    def update(self, data: dict[str, Any]) -> _UpdateBuilder:
        return _UpdateBuilder(self, data)


class _CredentialTable:
    """Keyed on (user_id, service, provider), not just (service,
    provider) -- an adversarial review found the original fake silently
    ignored `.eq("user_id", ...)` entirely, so a regression that dropped
    the real code's own `user_id` filter (the only thing standing
    between a user's stored Gmail/LLM credential and any OTHER user's)
    would have gone completely undetected by this test suite."""

    def __init__(self, rows: dict[tuple[str, str, str], dict[str, Any]]) -> None:
        self._rows = rows
        self._user_id = ""
        self._service = ""
        self._provider = ""

    def select(self, *_: Any, **__: Any) -> _CredentialTable:
        return self

    def eq(self, column: str, value: Any) -> _CredentialTable:
        if column == "user_id":
            self._user_id = value
        if column == "service":
            self._service = value
        if column == "provider":
            self._provider = value
        return self

    async def execute(self) -> SimpleNamespace:
        row = self._rows.get((self._user_id, self._service, self._provider))
        return SimpleNamespace(data=[row] if row else [])


class _EventOutboxTable:
    def __init__(self) -> None:
        self.insert_calls: list[dict[str, Any]] = []

    def insert(self, data: dict[str, Any]) -> _EventInsertBuilder:
        return _EventInsertBuilder(self, data)


class _EventInsertBuilder:
    def __init__(self, table: _EventOutboxTable, data: dict[str, Any]) -> None:
        self._table = table
        self._data = data

    async def execute(self) -> SimpleNamespace:
        self._table.insert_calls.append(self._data)
        return SimpleNamespace(data=[{"id": "evt-1", **self._data}])


class _ProposalsInsertBuilder:
    def __init__(self, table: _ProposalsTable, data: dict[str, Any]) -> None:
        self._table = table
        self._data = data

    async def execute(self) -> SimpleNamespace:
        if self._data["source_gmail_message_id"] in self._table.raise_on_message_id:
            raise APIError(
                {"message": "duplicate key", "code": "23505", "details": None, "hint": None}
            )
        row = {
            "id": f"proposal-{len(self._table.rows) + 1}",
            "status": "pending",
            "resolved_at": None,
            **self._data,
        }
        self._table.rows.append(row)
        self._table.insert_calls.append(row)
        return SimpleNamespace(data=[row])


class _ProposalsTable:
    def __init__(self, *, raise_on_message_id: set[str] | None = None) -> None:
        self.rows: list[dict[str, Any]] = []
        self.insert_calls: list[dict[str, Any]] = []
        self.raise_on_message_id = raise_on_message_id or set()

    def insert(self, data: dict[str, Any]) -> _ProposalsInsertBuilder:
        return _ProposalsInsertBuilder(self, data)

    def update(self, data: dict[str, Any]) -> _UpdateBuilder:
        return _UpdateBuilder(self, data)


class _FakeRpcBuilder:
    def __init__(self, data: Any) -> None:
        self._data = data

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._data)


class _FakeSupabase:
    def __init__(
        self,
        *,
        due_drafts: list[dict[str, Any]] | None = None,
        outreach_drafts: list[dict[str, Any]] | None = None,
        provider_credentials: dict[tuple[str, str, str], dict[str, Any]] | None = None,
        capability_preferences: list[dict[str, Any]] | None = None,
        event_outbox: _EventOutboxTable | None = None,
        application_status_proposals: _ProposalsTable | None = None,
        change_stage_error: APIError | None = None,
    ) -> None:
        self._due_drafts = due_drafts if due_drafts is not None else [_due_draft()]
        self.outreach_drafts = _FakeTable(
            outreach_drafts if outreach_drafts is not None else [dict(_due_draft(id=_DRAFT_ID))]
        )
        self._credential_table = _CredentialTable(
            provider_credentials
            if provider_credentials is not None
            else {
                (_USER_ID, "oauth", "gmail"): _GMAIL_CREDENTIAL_ROW,
                (_USER_ID, "llm", "openrouter"): _LLM_CREDENTIAL_ROW,
            }
        )
        self.capability_preferences = _FakeTable(
            capability_preferences if capability_preferences is not None else [_PREFERENCE_ROW]
        )
        self.event_outbox = event_outbox or _EventOutboxTable()
        self.application_status_proposals = application_status_proposals or _ProposalsTable()
        self._change_stage_error = change_stage_error
        self.change_stage_calls: list[dict[str, Any]] = []

    def table(self, name: str) -> Any:
        return {
            "outreach_drafts": self.outreach_drafts,
            "provider_credentials": self._credential_table,
            "capability_preferences": self.capability_preferences,
            "event_outbox": self.event_outbox,
            "application_status_proposals": self.application_status_proposals,
        }[name]

    def rpc(self, fn: str, params: dict[str, Any]) -> _FakeRpcBuilder:
        if fn == "list_drafts_due_for_reply_check":
            return _FakeRpcBuilder(self._due_drafts)
        if fn == "decrypt_secret":
            return _FakeRpcBuilder(params["p_ciphertext"])
        if fn == "change_application_stage":
            self.change_stage_calls.append(params)
            if self._change_stage_error is not None:
                raise self._change_stage_error
            return _FakeRpcBuilder(
                {"id": params["p_application_id"], "status": params["p_new_status"]}
            )
        raise AssertionError(f"unexpected rpc: {fn}")


@pytest.fixture(autouse=True)
def _gmail_oauth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", "https://example.test/callback")


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(500)))


def _patch_get_thread(monkeypatch: pytest.MonkeyPatch, thread: dict[str, Any]) -> None:
    async def fake_get_thread(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return thread

    monkeypatch.setattr("between_jobs.api.gmail_reply_checker.get_thread", fake_get_thread)


def _patch_refresh(
    monkeypatch: pytest.MonkeyPatch, *, token: str = "access-token", calls: list[Any] | None = None
) -> None:
    async def fake_refresh(*_args: Any, **kwargs: Any) -> str:
        if calls is not None:
            calls.append(kwargs)
        return token

    monkeypatch.setattr("between_jobs.api.gmail_reply_checker.refresh_access_token", fake_refresh)


def _patch_llm(monkeypatch: pytest.MonkeyPatch, content: str) -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=content)

    monkeypatch.setattr("between_jobs.api.gmail_reply_checker.llm_generate", fake_generate)


async def test_run_reply_check_once_returns_zero_with_no_due_drafts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_refresh(monkeypatch)
    supabase = _FakeSupabase(due_drafts=[])

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 0


async def test_run_reply_check_once_fails_open_when_gmail_oauth_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)

    def fail_due_drafts(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("should never even look for due drafts")

    supabase = _FakeSupabase()
    supabase.rpc = fail_due_drafts  # type: ignore[method-assign]

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 0


async def test_check_one_draft_fails_open_when_no_gmail_connection_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_get_thread(monkeypatch, _sent_and_reply_thread())
    supabase = _FakeSupabase(
        provider_credentials={(_USER_ID, "llm", "openrouter"): _LLM_CREDENTIAL_ROW}
    )
    config = GmailOauthConfig(client_id="c", client_secret="s", redirect_uri="r")

    await _check_one_draft(_http(), supabase, _due_draft(), config, {})  # type: ignore[arg-type]

    assert supabase.event_outbox.insert_calls == []
    assert supabase.application_status_proposals.insert_calls == []


async def test_check_one_draft_never_uses_another_users_stored_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: `_CredentialTable` (and the real `get_decrypted_
    credential` it stands in for) is keyed on user_id too, not just
    (service, provider) -- a due draft for a user with NO Gmail
    credential of their own must fail open even when some OTHER user's
    Gmail credential exists in the same table."""
    _patch_get_thread(monkeypatch, _sent_and_reply_thread())
    supabase = _FakeSupabase(
        provider_credentials={
            ("someone-else", "oauth", "gmail"): _GMAIL_CREDENTIAL_ROW,
            ("someone-else", "llm", "openrouter"): _LLM_CREDENTIAL_ROW,
        }
    )
    config = GmailOauthConfig(client_id="c", client_secret="s", redirect_uri="r")

    await _check_one_draft(_http(), supabase, _due_draft(), config, {})  # type: ignore[arg-type]

    assert supabase.application_status_proposals.insert_calls == []


async def test_check_one_draft_fails_open_when_stored_scope_lacks_readonly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_get_thread(monkeypatch, _sent_and_reply_thread())
    compose_only_row = {
        **_GMAIL_CREDENTIAL_ROW,
        "scope": "https://www.googleapis.com/auth/gmail.compose",
    }
    supabase = _FakeSupabase(
        provider_credentials={
            (_USER_ID, "oauth", "gmail"): compose_only_row,
            (_USER_ID, "llm", "openrouter"): _LLM_CREDENTIAL_ROW,
        }
    )
    config = GmailOauthConfig(client_id="c", client_secret="s", redirect_uri="r")

    await _check_one_draft(_http(), supabase, _due_draft(), config, {})  # type: ignore[arg-type]

    assert supabase.application_status_proposals.insert_calls == []


async def test_never_sent_draft_is_marked_checked_but_not_confirmed_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_get_thread(
        monkeypatch, {"id": _THREAD_ID, "messages": [_gmail_message(label_ids=["DRAFT"])]}
    )
    _patch_refresh(monkeypatch)
    supabase = _FakeSupabase()

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 1
    row = supabase.outreach_drafts.rows[0]
    assert row["last_reply_checked_at"] is not None
    assert row["sent_confirmed_at"] is None
    assert supabase.application_status_proposals.insert_calls == []


async def test_sent_with_no_reply_confirms_sent_and_records_no_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_get_thread(monkeypatch, {"id": _THREAD_ID, "messages": [_gmail_message()]})
    _patch_refresh(monkeypatch)
    supabase = _FakeSupabase()

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 1
    row = supabase.outreach_drafts.rows[0]
    assert row["sent_confirmed_at"] is not None
    assert row["last_reply_checked_at"] is not None
    assert supabase.application_status_proposals.insert_calls == []


async def test_sent_confirmation_anchors_to_the_earliest_sent_message_on_first_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard for a real, adversarially-confirmed bug: a real
    reply that arrived BETWEEN two SENT-labeled messages (the original
    outreach send, then a second message the user later sent from
    within Gmail directly -- not through this platform) must still be
    found. Anchoring to the MOST RECENT SENT message (the original code)
    would put the watermark past the real reply, losing it permanently
    since the watermark only ever advances."""
    thread = {
        "id": _THREAD_ID,
        "messages": [
            _gmail_message(id="msg-sent-original", label_ids=["SENT"], internal_date_ms=1_000_000),
            _gmail_message(
                id="msg-reply-between",
                label_ids=["INBOX"],
                internal_date_ms=1_500_000,
                body_text=_REPLY_TEXT,
            ),
            _gmail_message(id="msg-sent-later", label_ids=["SENT"], internal_date_ms=2_000_000),
        ],
    }
    _patch_get_thread(monkeypatch, thread)
    _patch_refresh(monkeypatch)
    _patch_llm(
        monkeypatch,
        json.dumps(
            {
                "proposed_type": "interview.requested",
                "confidence": 0.5,
                "evidence_spans": ["We'd love to schedule a call this week"],
            }
        ),
    )
    supabase = _FakeSupabase()

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 1
    row = supabase.outreach_drafts.rows[0]
    assert row["sent_confirmed_at"] is not None
    assert len(supabase.application_status_proposals.insert_calls) == 1
    assert supabase.application_status_proposals.insert_calls[0]["source_gmail_message_id"] == (
        "msg-reply-between"
    )


async def test_sent_confirmed_at_is_never_recomputed_on_a_later_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once confirmed, the stored `sent_confirmed_at` is the permanent
    anchor -- a later tick must never re-derive it from the thread's own
    SENT messages again, even if the thread has since picked up a second,
    later SENT message."""
    thread = {
        "id": _THREAD_ID,
        "messages": [
            # Real 2026 epoch-ms values, not tiny placeholders -- the
            # earlier version of this test used internal_date_ms values
            # so small (~1970) that a real sent_confirmed_at ISO date
            # converted to a FAR larger epoch-ms watermark, masking the
            # very behavior this test exists to prove.
            _gmail_message(
                id="msg-sent-original", label_ids=["SENT"], internal_date_ms=1_767_225_600_000
            ),  # 2026-01-01
            _gmail_message(
                id="msg-sent-later", label_ids=["SENT"], internal_date_ms=1_780_272_000_000
            ),  # 2026-06-01
            _gmail_message(
                id="msg-reply-new",
                label_ids=["INBOX"],
                internal_date_ms=1_767_312_000_000,  # 2026-01-02
                body_text=_REPLY_TEXT,
            ),
        ],
    }
    _patch_get_thread(monkeypatch, thread)
    _patch_refresh(monkeypatch)
    _patch_llm(
        monkeypatch,
        json.dumps(
            {
                "proposed_type": "interview.requested",
                "confidence": 0.5,
                "evidence_spans": ["We'd love to schedule a call this week"],
            }
        ),
    )
    already_confirmed_sent_at = "2026-01-01T00:00:00Z"  # matches msg-sent-original exactly
    supabase = _FakeSupabase(
        due_drafts=[
            _due_draft(sent_confirmed_at=already_confirmed_sent_at, last_reply_checked_at=None)
        ],
        outreach_drafts=[
            {
                "id": _DRAFT_ID,
                "last_reply_checked_at": None,
                "sent_confirmed_at": already_confirmed_sent_at,
            }
        ],
    )

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 1
    # sent_confirmed_at is untouched -- no second _mark_sent_confirmed call
    assert supabase.outreach_drafts.rows[0]["sent_confirmed_at"] == already_confirmed_sent_at
    assert len(supabase.application_status_proposals.insert_calls) == 1
    assert supabase.application_status_proposals.insert_calls[0]["source_gmail_message_id"] == (
        "msg-reply-new"
    )


async def test_a_new_reply_below_threshold_is_recorded_as_pending_and_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_get_thread(monkeypatch, _sent_and_reply_thread())
    _patch_refresh(monkeypatch)
    _patch_llm(
        monkeypatch,
        json.dumps(
            {
                "proposed_type": "interview.requested",
                "confidence": 0.5,
                "evidence_spans": ["We'd love to schedule a call this week"],
            }
        ),
    )
    supabase = _FakeSupabase()

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 1
    assert len(supabase.application_status_proposals.insert_calls) == 1
    proposal = supabase.application_status_proposals.insert_calls[0]
    assert proposal["status"] == "pending"
    assert proposal["proposed_type"] == "interview.requested"
    assert proposal["application_id"] == _APPLICATION_ID
    assert supabase.change_stage_calls == []
    assert len(supabase.event_outbox.insert_calls) == 1
    event = supabase.event_outbox.insert_calls[0]
    assert event["event_type"] == "gmail_reply.status_proposed.v1"
    assert event["aggregate_type"] == "application_status_proposal"
    assert event["aggregate_id"] == proposal["id"]
    assert event["payload"]["application_id"] == _APPLICATION_ID


async def test_above_threshold_mappable_proposal_auto_applies_the_stage_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_get_thread(monkeypatch, _sent_and_reply_thread())
    _patch_refresh(monkeypatch)
    _patch_llm(
        monkeypatch,
        json.dumps(
            {
                "proposed_type": "interview.requested",
                "confidence": 0.9,
                "evidence_spans": ["We'd love to schedule a call this week"],
            }
        ),
    )
    supabase = _FakeSupabase()

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 1
    assert len(supabase.change_stage_calls) == 1
    call = supabase.change_stage_calls[0]
    assert call["p_application_id"] == _APPLICATION_ID
    assert call["p_new_status"] == "interviewing"
    assert call["p_actor_type"] == "email_monitor"
    proposal = supabase.application_status_proposals.rows[0]
    assert proposal["status"] == "accepted"
    assert proposal["resolved_at"] is not None
    assert (
        supabase.event_outbox.insert_calls == []
    )  # auto-applied -- no separate review-queue event


async def test_auto_apply_falls_through_to_the_review_queue_when_the_application_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard for a real, adversarially-confirmed bug: the
    application this proposal refers to can be deleted between the
    poller resolving it and the auto-apply RPC firing. The original code
    just returned on ApplicationNotFound, permanently stranding the
    proposal at status='pending' with NO event ever published -- never
    visible to a human anywhere. It must now fall through to the same
    pending-review event every other non-auto-applied proposal gets."""
    _patch_get_thread(monkeypatch, _sent_and_reply_thread())
    _patch_refresh(monkeypatch)
    _patch_llm(
        monkeypatch,
        json.dumps(
            {
                "proposed_type": "interview.requested",
                "confidence": 0.9,
                "evidence_spans": ["We'd love to schedule a call this week"],
            }
        ),
    )
    application_not_found = APIError(
        {"message": "application not found", "code": "P0001", "details": None, "hint": None}
    )
    supabase = _FakeSupabase(change_stage_error=application_not_found)

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 1
    assert len(supabase.change_stage_calls) == 1
    proposal = supabase.application_status_proposals.rows[0]
    assert proposal["status"] == "pending"
    assert len(supabase.event_outbox.insert_calls) == 1
    assert supabase.event_outbox.insert_calls[0]["event_type"] == "gmail_reply.status_proposed.v1"


async def test_auto_apply_falls_through_to_the_review_queue_on_a_transient_rpc_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_get_thread(monkeypatch, _sent_and_reply_thread())
    _patch_refresh(monkeypatch)
    _patch_llm(
        monkeypatch,
        json.dumps(
            {
                "proposed_type": "offer.received",
                "confidence": 0.95,
                "evidence_spans": ["We'd love to schedule a call this week"],
            }
        ),
    )
    transient_failure = APIError(
        {"message": "server error", "code": "57014", "details": None, "hint": None}
    )
    supabase = _FakeSupabase(change_stage_error=transient_failure)

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 1
    proposal = supabase.application_status_proposals.rows[0]
    assert proposal["status"] == "pending"
    assert len(supabase.event_outbox.insert_calls) == 1


async def test_above_threshold_unmappable_proposal_is_queued_for_review_instead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`recruiter.replied` has no corresponding Kanban stage -- even at
    high confidence, auto-apply is structurally impossible, so this must
    still go to the pending review queue, never silently dropped."""
    _patch_get_thread(monkeypatch, _sent_and_reply_thread())
    _patch_refresh(monkeypatch)
    _patch_llm(
        monkeypatch,
        json.dumps(
            {
                "proposed_type": "recruiter.replied",
                "confidence": 0.95,
                "evidence_spans": ["We'd love to schedule a call this week"],
            }
        ),
    )
    supabase = _FakeSupabase()

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 1
    assert supabase.change_stage_calls == []
    assert len(supabase.event_outbox.insert_calls) == 1
    proposal = supabase.application_status_proposals.rows[0]
    assert proposal["status"] == "pending"


async def test_a_reply_older_than_the_watermark_is_not_reclassified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread = _sent_and_reply_thread()
    _patch_get_thread(monkeypatch, thread)
    _patch_refresh(monkeypatch)

    async def fail_generate(**_kwargs: Any) -> LLMResponse:
        raise AssertionError("an already-seen reply should never be reclassified")

    monkeypatch.setattr("between_jobs.api.gmail_reply_checker.llm_generate", fail_generate)

    reply_ms = thread["messages"][1]["internal_date_ms"]
    already_checked_at = "2026-09-06T00:00:10Z"  # after the reply's own internal_date_ms
    supabase = _FakeSupabase(
        due_drafts=[
            _due_draft(sent_confirmed_at="2026-09-06T00:00:00Z", last_reply_checked_at=None)
        ],
        outreach_drafts=[
            {
                "id": _DRAFT_ID,
                "last_reply_checked_at": already_checked_at,
                "sent_confirmed_at": "2026-09-06T00:00:00Z",
            }
        ],
    )
    # The watermark check reads the DUE-DRAFT row's own last_reply_checked_at
    # (from the RPC), not the outreach_drafts table row -- set both so the
    # RPC-returned value is what actually gates reclassification.
    supabase._due_drafts[0]["last_reply_checked_at"] = already_checked_at
    assert reply_ms < 2_000_000_000_000  # sanity: the reply is "in the past" relative to 2026

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 1
    assert supabase.application_status_proposals.insert_calls == []


async def test_duplicate_reply_message_is_idempotently_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_get_thread(monkeypatch, _sent_and_reply_thread(reply_id="msg-reply-dup"))
    _patch_refresh(monkeypatch)
    _patch_llm(
        monkeypatch,
        json.dumps(
            {
                "proposed_type": "interview.requested",
                "confidence": 0.5,
                "evidence_spans": ["We'd love to schedule a call this week"],
            }
        ),
    )
    supabase = _FakeSupabase(
        application_status_proposals=_ProposalsTable(raise_on_message_id={"msg-reply-dup"})
    )

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 1
    assert supabase.application_status_proposals.rows == []
    assert supabase.event_outbox.insert_calls == []
    assert supabase.change_stage_calls == []


async def test_provider_rejected_on_refresh_marks_checked_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_refresh(*_args: Any, **_kwargs: Any) -> str:
        raise ApiError("PROVIDER_REJECTED", "Google rejected the stored Gmail connection.")

    monkeypatch.setattr("between_jobs.api.gmail_reply_checker.refresh_access_token", fake_refresh)
    supabase = _FakeSupabase()

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 1
    assert supabase.outreach_drafts.rows[0]["last_reply_checked_at"] is not None


async def test_provider_rejected_on_get_thread_marks_checked_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_thread(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise ApiError("PROVIDER_REJECTED", "Gmail rejected that request.")

    monkeypatch.setattr("between_jobs.api.gmail_reply_checker.get_thread", fake_get_thread)
    _patch_refresh(monkeypatch)
    supabase = _FakeSupabase()

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 1
    assert supabase.outreach_drafts.rows[0]["last_reply_checked_at"] is not None


async def test_provider_unavailable_on_refresh_does_not_crash_the_whole_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: an adversarially-confirmed bug let a transient
    Gmail/Google network blip (PROVIDER_UNAVAILABLE, raised by gmail_
    client.py on any httpx.HTTPError) propagate out of _check_one_draft,
    through asyncio.gather, and permanently kill run_reply_check_forever's
    while-loop task for every user -- not just fail this one draft. Only
    PROVIDER_REJECTED was special-cased originally; this proves ANY
    ApiError from the Gmail call sites is now handled the same way."""

    async def fake_refresh(*_args: Any, **_kwargs: Any) -> str:
        raise ApiError("PROVIDER_UNAVAILABLE", "Couldn't reach Google.", retryable=True)

    monkeypatch.setattr("between_jobs.api.gmail_reply_checker.refresh_access_token", fake_refresh)
    supabase = _FakeSupabase()

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 1
    assert supabase.outreach_drafts.rows[0]["last_reply_checked_at"] is not None


async def test_provider_unavailable_on_get_thread_does_not_crash_the_whole_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_thread(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise ApiError("PROVIDER_UNAVAILABLE", "Gmail couldn't fetch that thread.", retryable=True)

    monkeypatch.setattr("between_jobs.api.gmail_reply_checker.get_thread", fake_get_thread)
    _patch_refresh(monkeypatch)
    supabase = _FakeSupabase()

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 1
    assert supabase.outreach_drafts.rows[0]["last_reply_checked_at"] is not None


async def test_one_drafts_provider_unavailable_failure_does_not_stop_other_drafts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real-world shape of the bug: one due draft hitting a transient
    Gmail error must not prevent a completely different draft (a
    different user, a different thread) from still being checked and
    classified in the very same tick."""
    failing_draft_id = "60000000-0000-0000-0000-000000000003"
    ok_draft_id = "60000000-0000-0000-0000-000000000004"

    async def fake_get_thread(*_args: Any, thread_id: str, **_kwargs: Any) -> dict[str, Any]:
        if thread_id == "18cabc1234def569":
            raise ApiError("PROVIDER_UNAVAILABLE", "Couldn't reach Gmail.", retryable=True)
        return _sent_and_reply_thread()

    monkeypatch.setattr("between_jobs.api.gmail_reply_checker.get_thread", fake_get_thread)
    _patch_refresh(monkeypatch)
    _patch_llm(
        monkeypatch,
        json.dumps(
            {
                "proposed_type": "interview.requested",
                "confidence": 0.5,
                "evidence_spans": ["We'd love to schedule a call this week"],
            }
        ),
    )
    supabase = _FakeSupabase(
        due_drafts=[
            _due_draft(draft_id=failing_draft_id, gmail_thread_id="18cabc1234def569"),
            _due_draft(draft_id=ok_draft_id, gmail_thread_id=_THREAD_ID),
        ],
        outreach_drafts=[
            {"id": failing_draft_id, "last_reply_checked_at": None, "sent_confirmed_at": None},
            {"id": ok_draft_id, "last_reply_checked_at": None, "sent_confirmed_at": None},
        ],
    )

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 2
    assert len(supabase.application_status_proposals.insert_calls) == 1
    assert supabase.application_status_proposals.insert_calls[0]["outreach_draft_id"] == ok_draft_id


async def test_missing_llm_credential_skips_classification_but_watermark_still_advances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_get_thread(monkeypatch, _sent_and_reply_thread())
    _patch_refresh(monkeypatch)
    supabase = _FakeSupabase(
        capability_preferences=[],
        provider_credentials={(_USER_ID, "oauth", "gmail"): _GMAIL_CREDENTIAL_ROW},
    )

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 1
    assert supabase.application_status_proposals.insert_calls == []
    assert supabase.outreach_drafts.rows[0]["last_reply_checked_at"] is not None
    assert supabase.outreach_drafts.rows[0]["sent_confirmed_at"] is not None


async def test_multiple_new_replies_only_classifies_the_newest_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread = {
        "id": _THREAD_ID,
        "messages": [
            _gmail_message(id="msg-sent-1", label_ids=["SENT"], internal_date_ms=1_000_000),
            _gmail_message(
                id="msg-reply-older",
                label_ids=["INBOX"],
                internal_date_ms=1_500_000,
                body_text="An older reply.",
            ),
            _gmail_message(
                id="msg-reply-newer",
                label_ids=["INBOX"],
                internal_date_ms=2_000_000,
                body_text=_REPLY_TEXT,
            ),
        ],
    }
    _patch_get_thread(monkeypatch, thread)
    _patch_refresh(monkeypatch)
    calls = []

    async def counting_generate(**kwargs: Any) -> LLMResponse:
        calls.append(kwargs)
        return _classifier_response(
            {
                "proposed_type": "interview.requested",
                "confidence": 0.5,
                "evidence_spans": ["We'd love to schedule a call this week"],
            }
        )

    monkeypatch.setattr("between_jobs.api.gmail_reply_checker.llm_generate", counting_generate)
    supabase = _FakeSupabase()

    await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert len(calls) == 1
    assert len(supabase.application_status_proposals.insert_calls) == 1
    assert supabase.application_status_proposals.insert_calls[0]["source_gmail_message_id"] == (
        "msg-reply-newer"
    )


async def test_access_token_is_refreshed_once_per_user_across_multiple_drafts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refresh call includes a real `await asyncio.sleep(...)` --
    forcing a genuine yield point -- so both drafts' `_check_one_draft`
    calls actually interleave here. An adversarial review found the
    original plain dict-of-strings cache had a check-then-await-then-set
    race: without the single-flight `asyncio.Task` fix, both coroutines
    would observe a cache miss before either write happened, and this
    test (with a fake that doesn't force a real yield) would have passed
    even with that bug still present -- a genuinely concurrency-proving
    test needs a real suspension point, not just two sequential calls."""
    other_draft_id = "60000000-0000-0000-0000-000000000002"
    other_thread_id = "18cabc1234def568"
    _patch_get_thread(monkeypatch, {"id": _THREAD_ID, "messages": [_gmail_message()]})
    refresh_calls: list[Any] = []

    async def fake_refresh(*_args: Any, **kwargs: Any) -> str:
        refresh_calls.append(kwargs)
        await asyncio.sleep(0.01)
        return "access-token"

    monkeypatch.setattr("between_jobs.api.gmail_reply_checker.refresh_access_token", fake_refresh)
    supabase = _FakeSupabase(
        due_drafts=[
            _due_draft(),
            _due_draft(draft_id=other_draft_id, gmail_thread_id=other_thread_id),
        ],
        outreach_drafts=[
            {"id": _DRAFT_ID, "last_reply_checked_at": None, "sent_confirmed_at": None},
            {"id": other_draft_id, "last_reply_checked_at": None, "sent_confirmed_at": None},
        ],
    )

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 2
    assert len(refresh_calls) == 1


async def test_run_reply_check_once_processes_drafts_concurrently_but_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """code-review-fixes.md's own precedent (4d, saved_search_matcher) --
    different drafts must run concurrently, but bounded -- never more
    than _MAX_CONCURRENT_CHECKS real LLM calls in flight at once. Each of
    the 12 drafts here gets its own genuinely new reply so the LLM is
    actually reached for every one of them, not just processed-and-
    skipped."""
    drafts = [_due_draft(draft_id=f"draft-{i}", gmail_thread_id=f"{i:016x}") for i in range(12)]
    outreach_rows = [
        {"id": d["draft_id"], "last_reply_checked_at": None, "sent_confirmed_at": None}
        for d in drafts
    ]

    async def fake_get_thread(*_args: Any, thread_id: str, **_kwargs: Any) -> dict[str, Any]:
        return _sent_and_reply_thread(reply_id=f"reply-{thread_id}")

    monkeypatch.setattr("between_jobs.api.gmail_reply_checker.get_thread", fake_get_thread)
    _patch_refresh(monkeypatch)

    in_flight = 0
    max_in_flight = 0

    async def tracking_generate(**_kwargs: Any) -> LLMResponse:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return _classifier_response(
            {"proposed_type": "unknown", "confidence": 0.0, "evidence_spans": []}
        )

    monkeypatch.setattr("between_jobs.api.gmail_reply_checker.llm_generate", tracking_generate)
    supabase = _FakeSupabase(due_drafts=drafts, outreach_drafts=outreach_rows)

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 12
    assert len(supabase.application_status_proposals.insert_calls) == 12
    assert 1 < max_in_flight <= 5


async def test_select_due_drafts_returns_the_rpc_rows_as_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_due_draft(), _due_draft(draft_id="60000000-0000-0000-0000-000000000002")]
    supabase = _FakeSupabase(due_drafts=rows)

    result = await _select_due_drafts(supabase)  # type: ignore[arg-type]

    assert result == rows
