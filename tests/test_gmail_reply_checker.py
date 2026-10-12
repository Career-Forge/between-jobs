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
import logging
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from postgrest.exceptions import APIError

from between_jobs.api.errors import ApiError
from between_jobs.api.gmail_access import flag_reconnect_required
from between_jobs.api.gmail_client import GmailOauthConfig, reconnect_required_error
from between_jobs.api.gmail_reply_checker import (
    _MAX_CONCURRENT_CHECKS,
    _MAX_DRAFTS_PER_TICK,
    _check_one_draft,
    _select_due_drafts,
    run_reply_check_once,
)
from between_jobs.api.llm_client import LLMResponse
from between_jobs.api.provider_credentials_store import save_credential

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
        self._not_filters: dict[str, Any] = {}

    def eq(self, column: str, value: Any) -> _UpdateBuilder:
        self._filters[column] = value
        return self

    def neq(self, column: str, value: Any) -> _UpdateBuilder:
        self._not_filters[column] = value
        return self

    async def execute(self) -> SimpleNamespace:
        matched = [
            r
            for r in self._table.rows
            if all(r.get(k) == v for k, v in self._filters.items())
            and all(r.get(k) != v for k, v in self._not_filters.items())
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
    """Rows keyed on (user_id, service, provider), not just (service,
    provider) -- an adversarial review found the original fake silently
    ignored `.eq("user_id", ...)` entirely, so a regression that dropped
    the real code's own `user_id` filter (the only thing standing
    between a user's stored Gmail/LLM credential and any OTHER user's)
    would have gone completely undetected by this test suite.

    Every `.eq` is a real filter now, so the same fake answers a lookup of one credential, the
    "who is waiting to reconnect" listing (`select("user_id")`), the conditional update that
    flags a connection (it changes a row only while its `is_validated` still matches the
    filter, like Postgres) and the upsert a reconnect makes; `update_calls` records each
    attempt to flag. A select answers with only the columns it asked for, as PostgREST does, so
    code that reads a column it never selected sees it missing here too."""

    def __init__(self, rows: dict[tuple[str, str, str], dict[str, Any]]) -> None:
        self._rows = rows
        self.update_calls: list[dict[str, Any]] = []
        self._reset()

    def _reset(self) -> None:
        self._filters: dict[str, Any] = {}
        self._columns = "*"
        self._update: dict[str, Any] | None = None
        self._upsert: dict[str, Any] | None = None

    def select(self, columns: str = "*", **_: Any) -> _CredentialTable:
        self._reset()
        self._columns = columns
        return self

    def update(self, data: dict[str, Any]) -> _CredentialTable:
        self._reset()
        self._update = data
        return self

    def upsert(self, data: dict[str, Any], on_conflict: str = "") -> _CredentialTable:
        assert on_conflict == "user_id,service,provider"
        self._reset()
        self._upsert = data
        return self

    def eq(self, column: str, value: Any) -> _CredentialTable:
        self._filters[column] = value
        return self

    def _matching(self) -> list[tuple[tuple[str, str, str], dict[str, Any]]]:
        by_key = {"user_id": 0, "service": 1, "provider": 2}
        return [
            (key, row)
            for key, row in self._rows.items()
            if all(
                key[by_key[column]] == value if column in by_key else row.get(column, True) == value
                for column, value in self._filters.items()
            )
        ]

    async def execute(self) -> SimpleNamespace:
        if self._upsert is not None:
            key = (self._upsert["user_id"], self._upsert["service"], self._upsert["provider"])
            row = {
                column: value
                for column, value in self._upsert.items()
                if column not in ("user_id", "service")
            }
            self._rows[key] = row  # the whole row is replaced, as an upsert on the key does
            return SimpleNamespace(data=[row])
        matching = self._matching()
        if self._update is not None:
            self.update_calls.append({"set": self._update, "filters": dict(self._filters)})
            for _, row in matching:
                row.update(self._update)
            return SimpleNamespace(data=[row for _, row in matching])
        if self._columns.strip() == "*":
            return SimpleNamespace(data=[row for _, row in matching])
        wanted = {column.strip() for column in self._columns.split(",")}
        return SimpleNamespace(
            data=[
                {
                    k: v
                    for k, v in {"user_id": key[0], "service": key[1], **row}.items()
                    if k in wanted
                }
                for key, row in matching
            ]
        )


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
    def __init__(
        self,
        *,
        raise_on_message_id: set[str] | None = None,
        initial_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.rows: list[dict[str, Any]] = list(initial_rows or [])
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
                (_USER_ID, "oauth", "gmail"): dict(_GMAIL_CREDENTIAL_ROW),
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
        self.due_limits: list[int] = []

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
            self.due_limits.append(params["result_limit"])
            return _FakeRpcBuilder(self._due_drafts[: params["result_limit"]])
        if fn == "encrypt_secret":
            return _FakeRpcBuilder(params["p_plaintext"])  # the fake database keeps text as is
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
    monkeypatch.delenv("GMAIL_ALLOWED_USER_IDS", raising=False)


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


@pytest.mark.parametrize("unset", [["CLIENT_ID", "CLIENT_SECRET", "REDIRECT_URI"], ["CLIENT_ID"]])
async def test_a_server_without_gmail_checks_nothing_and_logs_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, unset: list[str]
) -> None:
    """Gmail is optional: the worker's tick is a quiet no-op, every tick, not a CRITICAL "the
    API cannot start" line or a crash loop. (A half-set Gmail never gets this far: the boot
    refuses it. This is the tick's own answer to whatever the environment says.)"""
    for name in unset:
        monkeypatch.delenv(f"GOOGLE_OAUTH_{name}")

    def fail_due_drafts(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("should never even look for due drafts")

    supabase = _FakeSupabase()
    supabase.rpc = fail_due_drafts  # type: ignore[method-assign]

    with caplog.at_level(logging.DEBUG):
        for _ in range(3):  # tick after tick
            assert await run_reply_check_once(_http(), supabase) == 0  # type: ignore[arg-type]

    assert [r for r in caplog.records if r.levelno >= logging.INFO] == []


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


async def test_auto_apply_dismisses_other_pending_proposals_for_the_same_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard, same reasoning as today_routes.py's own human
    Accept path: an older sibling proposal left 'pending' after this one
    auto-applies could later be accepted (by a human, via the Today
    feed) and silently regress a stage that already moved forward."""
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
    sibling = {
        "id": "sibling-proposal-1",
        "user_id": _USER_ID,
        "application_id": _APPLICATION_ID,
        "outreach_draft_id": "60000000-0000-0000-0000-000000000099",
        "proposed_type": "assessment.received",
        "confidence": 0.3,
        "status": "pending",
        "resolved_at": None,
    }
    supabase = _FakeSupabase(application_status_proposals=_ProposalsTable(initial_rows=[sibling]))

    await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    sibling_row = next(
        r for r in supabase.application_status_proposals.rows if r["id"] == "sibling-proposal-1"
    )
    assert sibling_row["status"] == "dismissed"
    assert sibling_row["resolved_at"] is not None


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


# -- a model provider that refuses the owner's key is expected, not a bug -------------------------

_OTHER_DRAFT_ID = "60000000-0000-0000-0000-000000000005"
_OTHER_THREAD_ID = "18cabc1234def570"


def _two_drafts_one_reply_each() -> _FakeSupabase:
    return _FakeSupabase(
        due_drafts=[
            _due_draft(),
            _due_draft(draft_id=_OTHER_DRAFT_ID, gmail_thread_id=_OTHER_THREAD_ID),
        ],
        outreach_drafts=[
            {"id": _DRAFT_ID, "last_reply_checked_at": None, "sent_confirmed_at": None},
            {"id": _OTHER_DRAFT_ID, "last_reply_checked_at": None, "sent_confirmed_at": None},
        ],
    )


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("PROVIDER_REJECTED", "key_rejected"),
        ("PROVIDER_REJECTED", "out_of_credit"),
        ("PROVIDER_RATE_LIMITED", "rate_limited"),
        ("PROVIDER_UNAVAILABLE", "timeout"),
    ],
)
async def test_a_draft_whose_owners_model_key_fails_is_one_warning_and_the_other_is_classified(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, code: Any, reason: str
) -> None:
    _patch_get_thread(monkeypatch, _sent_and_reply_thread())
    _patch_refresh(monkeypatch)
    calls = 0

    async def generate(**_kwargs: Any) -> LLMResponse:
        nonlocal calls
        calls += 1
        if calls == 1:  # whichever draft asks first belongs to the person with the bad key
            raise ApiError(code, "The provider said no.", details={"reason": reason})
        return _classifier_response(
            {
                "proposed_type": "interview.requested",
                "confidence": 0.5,
                "evidence_spans": ["We'd love to schedule a call this week"],
            }
        )

    monkeypatch.setattr("between_jobs.api.gmail_reply_checker.llm_generate", generate)
    supabase = _two_drafts_one_reply_each()

    with caplog.at_level(logging.DEBUG, logger="between_jobs.api.gmail_reply_checker"):
        processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 2
    assert calls == 2  # one question each: a refusal is not asked again
    [proposal] = supabase.application_status_proposals.insert_calls
    failed_draft = _OTHER_DRAFT_ID if proposal["outreach_draft_id"] == _DRAFT_ID else _DRAFT_ID
    [record] = [r for r in caplog.records if r.levelno >= logging.INFO]
    assert record.levelno == logging.WARNING
    assert record.getMessage() == f"api error {code}"
    assert record.exc_info is None
    assert record.ctx == {  # type: ignore[attr-defined]
        "code": code,
        "status": ApiError(code, "x").status_code,
        "draft_id": failed_draft,
        "task": "reply_check",
        "reason": reason,
    }


async def test_any_other_failure_of_one_draft_keeps_its_traceback_at_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _patch_get_thread(monkeypatch, _sent_and_reply_thread())
    _patch_refresh(monkeypatch)

    async def generate(**_kwargs: Any) -> LLMResponse:
        raise RuntimeError("a bug")

    monkeypatch.setattr("between_jobs.api.gmail_reply_checker.llm_generate", generate)

    with caplog.at_level(logging.DEBUG, logger="between_jobs.api.gmail_reply_checker"):
        processed = await run_reply_check_once(_http(), _FakeSupabase())  # type: ignore[arg-type]

    assert processed == 1
    [record] = [r for r in caplog.records if r.levelno >= logging.INFO]
    assert record.levelno == logging.ERROR
    assert record.getMessage() == "reply check failed for a draft; retried next tick"
    assert record.exc_info is not None and isinstance(record.exc_info[1], RuntimeError)
    assert record.ctx == {"draft_id": _DRAFT_ID}  # type: ignore[attr-defined]


# -- Gmail for invited people only, and a connection Google stopped honouring -------------------

_OTHER_USER_ID = "00000000-0000-0000-0000-000000000002"
_OTHER_DRAFT_2 = "60000000-0000-0000-0000-000000000007"
_OTHER_THREAD_2 = "18cabc1234def571"
_REFRESH_TOKEN_TEXT = "refresh-token-cipher"  # what the fake database "decrypts" the token to
_OTHER_PERSON_CREDENTIALS: dict[tuple[str, str, str], dict[str, Any]] = {
    (_OTHER_USER_ID, "oauth", "gmail"): dict(_GMAIL_CREDENTIAL_ROW),
    (_OTHER_USER_ID, "llm", "openrouter"): dict(_LLM_CREDENTIAL_ROW),
}
_NEW_REPLY_PROPOSAL = {
    "proposed_type": "interview.requested",
    "confidence": 0.5,
    "evidence_spans": ["We'd love to schedule a call this week"],
}


def _two_people(*, flagged: tuple[str, ...] = ()) -> _FakeSupabase:
    """One due draft each for `_USER_ID` and `_OTHER_USER_ID`, both with a connection."""
    credentials: dict[tuple[str, str, str], dict[str, Any]] = {
        (_USER_ID, "oauth", "gmail"): dict(_GMAIL_CREDENTIAL_ROW),
        (_USER_ID, "llm", "openrouter"): dict(_LLM_CREDENTIAL_ROW),
        **{key: dict(row) for key, row in _OTHER_PERSON_CREDENTIALS.items()},
    }
    for user in flagged:
        credentials[(user, "oauth", "gmail")]["is_validated"] = False
    return _FakeSupabase(
        due_drafts=[
            _due_draft(),
            _due_draft(
                user_id=_OTHER_USER_ID, draft_id=_OTHER_DRAFT_ID, gmail_thread_id=_OTHER_THREAD_ID
            ),
        ],
        outreach_drafts=[
            {"id": _DRAFT_ID, "last_reply_checked_at": None, "sent_confirmed_at": None},
            {"id": _OTHER_DRAFT_ID, "last_reply_checked_at": None, "sent_confirmed_at": None},
        ],
        provider_credentials=credentials,
    )


def _watch_google(
    monkeypatch: pytest.MonkeyPatch,
    thread: dict[str, Any],
    *,
    refresh_error: ApiError | None = None,
    thread_error: ApiError | None = None,
) -> tuple[list[str], list[str]]:
    """Fakes the two calls to Google and returns what they were asked: the refresh tokens
    sent, and the thread ids fetched."""
    refreshed: list[str] = []
    fetched: list[str] = []

    async def fake_refresh(*_args: Any, **kwargs: Any) -> str:
        refreshed.append(kwargs["refresh_token"])
        if refresh_error is not None:
            raise refresh_error
        return "access-token"

    async def fake_get_thread(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        fetched.append(kwargs["thread_id"])
        if thread_error is not None:
            raise thread_error
        return thread

    monkeypatch.setattr("between_jobs.api.gmail_reply_checker.refresh_access_token", fake_refresh)
    monkeypatch.setattr("between_jobs.api.gmail_reply_checker.get_thread", fake_get_thread)
    return refreshed, fetched


def _our_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.INFO]


def _draft_row(supabase: _FakeSupabase, draft_id: str) -> dict[str, Any]:
    return next(r for r in supabase.outreach_drafts.rows if r["id"] == draft_id)


async def test_a_person_the_gmail_list_does_not_name_is_left_alone_entirely(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """No call to Google, nothing logged, and nothing written -- not even the watermark, so
    adding them back later still catches a reply that arrived in between."""
    monkeypatch.setenv("GMAIL_ALLOWED_USER_IDS", _OTHER_USER_ID)
    refreshed, fetched = _watch_google(monkeypatch, {"id": _THREAD_ID, "messages": []})
    supabase = _two_people()

    with caplog.at_level(logging.DEBUG):
        processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 1
    assert fetched == [_OTHER_THREAD_ID]  # only the listed person's thread was read
    assert len(refreshed) == 1
    unlisted = _draft_row(supabase, _DRAFT_ID)
    assert unlisted == {"id": _DRAFT_ID, "last_reply_checked_at": None, "sent_confirmed_at": None}
    assert supabase._credential_table.update_calls == []
    assert _draft_row(supabase, _OTHER_DRAFT_ID)["last_reply_checked_at"] is not None
    assert _our_records(caplog) == []


async def test_with_no_list_every_connected_person_is_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, fetched = _watch_google(monkeypatch, {"id": _THREAD_ID, "messages": []})

    processed = await run_reply_check_once(_http(), _two_people())  # type: ignore[arg-type]

    assert processed == 2
    assert sorted(fetched) == sorted([_THREAD_ID, _OTHER_THREAD_ID])


async def test_a_list_that_names_nobody_with_a_draft_checks_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GMAIL_ALLOWED_USER_IDS", "00000000-0000-0000-0000-0000000000ff")
    refreshed, fetched = _watch_google(monkeypatch, {"id": _THREAD_ID, "messages": []})

    processed = await run_reply_check_once(_http(), _two_people())  # type: ignore[arg-type]

    assert processed == 0
    assert refreshed == [] and fetched == []


async def test_an_expired_connection_is_flagged_once_then_left_alone_until_the_person_reconnects(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    thread = _sent_and_reply_thread()
    refreshed, fetched = _watch_google(
        monkeypatch, thread, refresh_error=reconnect_required_error()
    )
    _patch_llm(monkeypatch, json.dumps(_NEW_REPLY_PROPOSAL))
    supabase = _FakeSupabase()
    row = supabase._credential_table._rows[(_USER_ID, "oauth", "gmail")]

    # tick 1: Google refuses the refresh token. The connection is flagged, and kept.
    with caplog.at_level(logging.DEBUG):
        assert await run_reply_check_once(_http(), supabase) == 1  # type: ignore[arg-type]
    assert row["is_validated"] is False
    assert row["secret_encrypted"] == "refresh-token-cipher"
    # the draft is not marked checked, so a reply that arrives while access is gone is not lost
    assert supabase.outreach_drafts.rows[0]["last_reply_checked_at"] is None
    [warning] = _our_records(caplog)
    assert warning.levelno == logging.WARNING
    assert warning.name == "between_jobs.api.gmail_access"
    assert warning.ctx == {"user_id": _USER_ID, "reason": "gmail_reconnect_required"}  # type: ignore[attr-defined]
    assert len(refreshed) == 1 and fetched == []

    # ticks 2 and 3: nothing is asked of Google, nothing is logged, nothing is written
    caplog.clear()
    writes_before = list(supabase._credential_table.update_calls)
    with caplog.at_level(logging.DEBUG):
        assert await run_reply_check_once(_http(), supabase) == 0  # type: ignore[arg-type]
        assert await run_reply_check_once(_http(), supabase) == 0  # type: ignore[arg-type]
    assert len(refreshed) == 1 and fetched == []
    assert supabase._credential_table.update_calls == writes_before
    assert supabase.outreach_drafts.rows[0]["last_reply_checked_at"] is None
    assert _our_records(caplog) == []

    # the person reconnects: the callback saves the credential again with is_validated true
    row["is_validated"] = True
    _, fetched_after_reconnect = _watch_google(monkeypatch, thread)
    with caplog.at_level(logging.DEBUG):
        assert await run_reply_check_once(_http(), supabase) == 1  # type: ignore[arg-type]
    assert fetched_after_reconnect == [_THREAD_ID]
    # and the reply that came in meanwhile is classified, not lost
    [proposal] = supabase.application_status_proposals.insert_calls
    assert proposal["source_gmail_message_id"] == "msg-reply-1"
    assert supabase.outreach_drafts.rows[0]["last_reply_checked_at"] is not None
    # in all of this, neither the token nor anything of Google's was ever written to a log
    assert _REFRESH_TOKEN_TEXT not in caplog.text and "client-secret" not in caplog.text


async def test_two_drafts_of_one_person_flag_their_connection_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    refreshed: list[str] = []

    async def fake_refresh(*_args: Any, **kwargs: Any) -> str:
        refreshed.append(kwargs["refresh_token"])
        await asyncio.sleep(0.01)  # both drafts are waiting on the same refresh
        raise reconnect_required_error()

    monkeypatch.setattr("between_jobs.api.gmail_reply_checker.refresh_access_token", fake_refresh)
    supabase = _two_drafts_one_reply_each()

    with caplog.at_level(logging.DEBUG):
        processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 2 and len(refreshed) == 1
    assert supabase._credential_table._rows[(_USER_ID, "oauth", "gmail")]["is_validated"] is False
    [warning] = _our_records(caplog)
    assert warning.getMessage() == "a Gmail connection needs to be reconnected"
    for draft_id in (_DRAFT_ID, _OTHER_DRAFT_ID):
        assert _draft_row(supabase, draft_id)["last_reply_checked_at"] is None


async def test_a_reconnect_mid_tick_is_not_undone_by_the_old_tokens_cached_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A person's first draft finds out Google refuses their old refresh token, and they
    reconnect at once. Their second draft was still waiting behind other people's work, so it
    reads the NEW connection. It must be refreshed with the new token: reusing the old token's
    cached failure would flag the fresh connection a second time, and Google would never see the
    new token. (The refresh is shared only between drafts that hold the same token.)"""
    old_token, new_token = "old-refresh-token", "new-refresh-token"
    late_draft_id, late_thread_id = "60000000-0000-0000-0000-0000000000aa", "18cabc1234def5aa"
    reconnected = asyncio.Event()
    asked_google: list[str] = []
    threads_read: list[tuple[str, str]] = []

    async def token_endpoint(request: httpx.Request) -> httpx.Response:
        token = parse_qs(request.content.decode())["refresh_token"][0]
        asked_google.append(token)
        if token == old_token:
            return httpx.Response(400, json={"error": "invalid_grant"})
        if token == new_token:
            return httpx.Response(200, json={"access_token": "access-for-new"})
        # The other person's work holds its slots until the reconnect has landed, so the late
        # draft can only start after it, as it would behind slow work in a real tick.
        await reconnected.wait()
        return httpx.Response(200, json={"access_token": "access-for-other"})

    async def fake_get_thread(*_args: Any, access_token: str, thread_id: str) -> dict[str, Any]:
        threads_read.append((access_token, thread_id))
        return {"id": thread_id, "messages": []}

    async def flag_then_reconnect(supabase: Any, user_id: str) -> None:
        await flag_reconnect_required(supabase, user_id)
        if not reconnected.is_set():
            # what the OAuth callback does when the person reconnects: save the credential again
            await save_credential(
                supabase,
                user_id,
                service="oauth",
                provider="gmail",
                secret=new_token,
                scope=_GMAIL_CREDENTIAL_ROW["scope"],
                is_validated=True,
            )
            reconnected.set()

    monkeypatch.setattr("between_jobs.api.gmail_reply_checker.get_thread", fake_get_thread)
    monkeypatch.setattr(
        "between_jobs.api.gmail_reply_checker.flag_reconnect_required", flag_then_reconnect
    )
    first = _due_draft()
    others = [
        _due_draft(user_id=_OTHER_USER_ID, draft_id=f"other-{i}", gmail_thread_id=f"{i:016x}")
        for i in range(_MAX_CONCURRENT_CHECKS - 1)
    ]
    late = _due_draft(draft_id=late_draft_id, gmail_thread_id=late_thread_id)
    drafts = [first, *others, late]  # the first and the others fill every slot; `late` waits
    supabase = _FakeSupabase(
        due_drafts=drafts,
        outreach_drafts=[
            {"id": d["draft_id"], "last_reply_checked_at": None, "sent_confirmed_at": None}
            for d in drafts
        ],
        provider_credentials={
            (_USER_ID, "oauth", "gmail"): {**_GMAIL_CREDENTIAL_ROW, "secret_encrypted": old_token},
            **{key: dict(row) for key, row in _OTHER_PERSON_CREDENTIALS.items()},
        },
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(token_endpoint)) as http:
        with caplog.at_level(logging.DEBUG):
            processed = await asyncio.wait_for(run_reply_check_once(http, supabase), timeout=10)  # type: ignore[arg-type]

    assert processed == len(drafts)
    row = supabase._credential_table._rows[(_USER_ID, "oauth", "gmail")]
    assert row["is_validated"] is True  # the reconnect stands
    assert row["secret_encrypted"] == new_token
    transitions = [r for r in caplog.records if r.name == "between_jobs.api.gmail_access"]
    assert len(transitions) == 1 and transitions[0].levelno == logging.WARNING
    assert len(supabase._credential_table.update_calls) == 1  # flagged once, not again
    assert asked_google.count(old_token) == 1
    assert asked_google.count(new_token) == 1  # Google did see the new token
    assert ("access-for-new", late_thread_id) in threads_read  # and the late draft was checked
    assert _draft_row(supabase, late_draft_id)["last_reply_checked_at"] is not None
    assert _draft_row(supabase, _DRAFT_ID)["last_reply_checked_at"] is None  # the first was not
    assert old_token not in caplog.text and new_token not in caplog.text


async def test_gmail_refusing_the_access_token_while_reading_a_thread_flags_the_connection_too(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A 401 from the Gmail API itself (or a revoked scope), after a refresh that worked."""
    refreshed, fetched = _watch_google(
        monkeypatch, {"id": _THREAD_ID}, thread_error=reconnect_required_error()
    )
    supabase = _FakeSupabase()

    with caplog.at_level(logging.DEBUG):
        await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]
        await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert supabase._credential_table._rows[(_USER_ID, "oauth", "gmail")]["is_validated"] is False
    assert len(refreshed) == 1 and fetched == [_THREAD_ID]  # the second tick asked nothing
    assert supabase.outreach_drafts.rows[0]["last_reply_checked_at"] is None
    assert len(_our_records(caplog)) == 1


@pytest.mark.parametrize(
    "error",
    [
        ApiError("PROVIDER_UNAVAILABLE", "Couldn't reach Google.", retryable=True),
        ApiError("PROVIDER_RATE_LIMITED", "Gmail is limiting requests.", retryable=True),
        ApiError("PROVIDER_REJECTED", "Gmail refused to fetch that thread."),
        ApiError("PROVIDER_REJECTED", "Google wouldn't refresh.", details={"reason": "other"}),
    ],
    ids=["unavailable", "rate-limited", "rejected", "rejected-for-another-reason"],
)
@pytest.mark.parametrize("step", ["refresh", "thread"])
async def test_transient_failures_never_flag_a_connection_and_are_tried_again_next_tick(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, error: ApiError, step: str
) -> None:
    refreshed, fetched = _watch_google(
        monkeypatch,
        {"id": _THREAD_ID, "messages": []},
        refresh_error=error if step == "refresh" else None,
        thread_error=error if step == "thread" else None,
    )
    supabase = _FakeSupabase()

    with caplog.at_level(logging.DEBUG):
        for _ in range(3):
            assert await run_reply_check_once(_http(), supabase) == 1  # type: ignore[arg-type]

    row = supabase._credential_table._rows[(_USER_ID, "oauth", "gmail")]
    assert row.get("is_validated", True) is True
    assert supabase._credential_table.update_calls == []
    assert len(refreshed) == 3  # asked again on every tick, not given up on
    assert len(fetched) == (3 if step == "thread" else 0)
    assert not any(r.name == "between_jobs.api.gmail_access" for r in caplog.records)


async def test_a_connection_flagged_after_the_tick_began_is_not_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The listing of people to skip is read at the start of a tick; the credential itself is
    read again for each draft, and a flagged one stops there."""
    refreshed, fetched = _watch_google(monkeypatch, {"id": _THREAD_ID, "messages": []})
    supabase = _FakeSupabase(
        provider_credentials={
            (_USER_ID, "oauth", "gmail"): {**_GMAIL_CREDENTIAL_ROW, "is_validated": False},
            (_USER_ID, "llm", "openrouter"): _LLM_CREDENTIAL_ROW,
        }
    )
    config = GmailOauthConfig(client_id="c", client_secret="s", redirect_uri="r")

    await _check_one_draft(_http(), supabase, _due_draft(), config, {})  # type: ignore[arg-type]

    assert refreshed == [] and fetched == []
    assert supabase.outreach_drafts.rows[0].get("last_reply_checked_at") is None


async def test_a_flagged_person_does_not_stop_the_next_person_being_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, fetched = _watch_google(monkeypatch, {"id": _THREAD_ID, "messages": []})
    supabase = _two_people(flagged=(_USER_ID,))

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 1 and fetched == [_OTHER_THREAD_ID]


async def test_people_who_are_skipped_do_not_crowd_the_others_out_of_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The query returns the least recently checked drafts first and a skipped draft is never
    marked checked, so 250 drafts of a flagged person sit at the front of every tick."""
    flagged_drafts = [
        _due_draft(draft_id=f"flagged-{i}", gmail_thread_id=f"{i:016x}") for i in range(250)
    ]
    others = [
        _due_draft(user_id=_OTHER_USER_ID, draft_id=f"other-{i}", gmail_thread_id=f"{i:016x}")
        for i in range(_MAX_DRAFTS_PER_TICK + 5)
    ]
    _, fetched = _watch_google(monkeypatch, {"id": _THREAD_ID, "messages": []})
    supabase = _two_people(flagged=(_USER_ID,))
    supabase._due_drafts = [*flagged_drafts, *others]

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == _MAX_DRAFTS_PER_TICK  # a full budget of the others', not 0 and not 205
    assert len(fetched) == _MAX_DRAFTS_PER_TICK
    assert supabase.due_limits == [_MAX_DRAFTS_PER_TICK * 3]  # read wider, a bounded multiple


async def test_with_nobody_to_skip_the_query_asks_for_exactly_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _watch_google(monkeypatch, {"id": _THREAD_ID, "messages": []})
    supabase = _two_people()

    await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert supabase.due_limits == [_MAX_DRAFTS_PER_TICK]


async def test_a_list_is_applied_before_the_budget_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GMAIL_ALLOWED_USER_IDS", _OTHER_USER_ID)
    unlisted = [
        _due_draft(draft_id=f"unlisted-{i}", gmail_thread_id=f"{i:016x}") for i in range(250)
    ]
    listed = [
        _due_draft(user_id=_OTHER_USER_ID, draft_id=f"listed-{i}", gmail_thread_id=f"{i:016x}")
        for i in range(3)
    ]
    _, fetched = _watch_google(monkeypatch, {"id": _THREAD_ID, "messages": []})
    supabase = _two_people()
    supabase._due_drafts = [*unlisted, *listed]

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    assert processed == 3 and len(fetched) == 3


async def test_ids_are_compared_in_lower_case_whatever_case_a_row_writes_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upper = _OTHER_USER_ID.upper().replace("0000", "AAAA")
    lower = upper.lower()
    monkeypatch.setenv("GMAIL_ALLOWED_USER_IDS", lower)
    _, fetched = _watch_google(monkeypatch, {"id": _THREAD_ID, "messages": []})
    supabase = _two_people()
    supabase._due_drafts = [
        _due_draft(user_id=upper, draft_id="listed-in-capitals", gmail_thread_id="1" * 16),
        _due_draft(draft_id="unlisted", gmail_thread_id="2" * 16),
    ]
    supabase._credential_table._rows[(upper, "oauth", "gmail")] = dict(_GMAIL_CREDENTIAL_ROW)
    supabase._credential_table._rows[(lower, "oauth", "gmail")] = {
        **_GMAIL_CREDENTIAL_ROW,
        "is_validated": False,
    }

    processed = await run_reply_check_once(_http(), supabase)  # type: ignore[arg-type]

    # The draft's id is in capitals, the flagged connection and the list say it in lower case:
    # it is on the list, but its owner is waiting to reconnect, so nothing is checked.
    assert processed == 0 and fetched == []
