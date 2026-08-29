"""Tests for the Telegram webhook route (Sprint 2.5 rewrite).

The secret-token check runs for real here (real header comparison against
the real app.state value set from the stubbed env var) rather than being
overridden away -- it's the whole point of this endpoint's security, same
reasoning as leaving require_user_id's missing-header path real in
test_api.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from between_jobs.api.app import app
from between_jobs.api.app_state import get_supabase, get_telegram_client

_WEBHOOK_SECRET = "test-secret-not-real"
_TELEGRAM_USER_ID = 987654321
_CHAT_ID = 987654321
_EXISTING_USER_ID = "00000000-0000-0000-0000-000000000001"
_NEW_USER_ID = "00000000-0000-0000-0000-000000000002"
_VERSION_ID = "10000000-0000-0000-0000-000000000001"

_VALID_RESUME_JSON = json.dumps(
    {
        "personal": {
            "name": "Jane Doe",
            "headline": "Software Engineer",
            "emails": [{"address": "jane@example.com", "primary": True}],
            "phones": [],
            "links": {},
            "location": {},
        },
        "summary_bullets": [],
        "experience": [
            {
                "title": "Engineer",
                "company": "Acme",
                "start_date": "2022-01",
                "end_date": "present",
                "bullets": [],
            }
        ],
        "projects": [],
        "education": [],
        "skills": {},
        "achievements": [],
    }
)


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def is_(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def in_(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def order(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def limit(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    @property
    def not_(self) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeProfileVersionsTable:
    def __init__(
        self,
        *,
        select_rows: list[dict[str, Any]] | None = None,
        insert_row: dict[str, Any] | None = None,
        update_row: dict[str, Any] | None = None,
    ) -> None:
        self.select_rows = select_rows or []
        self.insert_row = insert_row
        self.update_row = update_row
        self.insert_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.delete_calls = 0

    def select(self, columns: str) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def insert(self, data: dict[str, Any]) -> _ChainBuilder:
        self.insert_calls.append(data)
        rows = [self.insert_row] if self.insert_row is not None else []
        return _ChainBuilder(rows)

    def update(self, data: dict[str, Any]) -> _ChainBuilder:
        self.update_calls.append(data)
        rows = [self.update_row] if self.update_row is not None else []
        return _ChainBuilder(rows)

    def delete(self) -> _ChainBuilder:
        self.delete_calls += 1
        return _ChainBuilder([])


class _FakeCareerFactsTable:
    def insert(self, data: list[dict[str, Any]]) -> _ChainBuilder:
        return _ChainBuilder([])


class _FakeChannelIdentitiesTable:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.delete_calls = 0

    def select(self, columns: str) -> _ChainBuilder:
        return _ChainBuilder(self._rows)

    def insert(self, data: dict[str, Any]) -> _ChainBuilder:
        return _ChainBuilder([{}])

    def delete(self) -> _ChainBuilder:
        self.delete_calls += 1
        return _ChainBuilder([])


class _FakeSimpleTable:
    """Covers `jobs`/`job_snapshots`/`applications`/`application_events`
    for the job-paste flow (Sprint 3.4a) -- none of these need anything
    beyond select-then-maybe-insert, already covered by `_ChainBuilder`."""

    def __init__(self, *, select_rows: list[dict[str, Any]] | None = None) -> None:
        self.select_rows = select_rows or []
        self.insert_calls: list[dict[str, Any]] = []

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def insert(self, data: dict[str, Any]) -> _ChainBuilder:
        self.insert_calls.append(data)
        row = {**data, "id": f"generated-{len(self.insert_calls)}"}
        return _ChainBuilder([row])


class _FakeAdmin:
    def __init__(self, new_user_id: str) -> None:
        self._new_user_id = new_user_id
        self.create_user_calls: list[dict[str, Any]] = []
        self.delete_user_calls: list[str] = []

    async def create_user(self, attributes: dict[str, Any]) -> SimpleNamespace:
        self.create_user_calls.append(attributes)
        return SimpleNamespace(user=SimpleNamespace(id=self._new_user_id))

    async def delete_user(self, user_id: str) -> None:
        self.delete_user_calls.append(user_id)


class _FakeRpcBuilder:
    def __init__(self, data: Any, error: Exception | None = None) -> None:
        self._data = data
        self._error = error

    async def execute(self) -> SimpleNamespace:
        if self._error:
            raise self._error
        return SimpleNamespace(data=self._data)


class _FakeSupabaseClient:
    def __init__(
        self,
        *,
        channel_identities_rows: list[dict[str, Any]],
        profile_versions: _FakeProfileVersionsTable | None = None,
        jobs: _FakeSimpleTable | None = None,
        job_snapshots: _FakeSimpleTable | None = None,
        applications: _FakeSimpleTable | None = None,
        application_events: _FakeSimpleTable | None = None,
        working_sets: _FakeSimpleTable | None = None,
        event_outbox: _FakeSimpleTable | None = None,
        new_user_id: str = _NEW_USER_ID,
        rpc_data: Any = None,
        rpc_error: Exception | None = None,
    ) -> None:
        self.auth = SimpleNamespace(admin=_FakeAdmin(new_user_id))
        self.channel_identities = _FakeChannelIdentitiesTable(channel_identities_rows)
        self.profile_versions = profile_versions or _FakeProfileVersionsTable()
        self.career_facts = _FakeCareerFactsTable()
        self.jobs = jobs or _FakeSimpleTable()
        self.job_snapshots = job_snapshots or _FakeSimpleTable()
        self.applications = applications or _FakeSimpleTable()
        self.application_events = application_events or _FakeSimpleTable()
        self.working_sets = working_sets or _FakeSimpleTable()
        self.event_outbox = event_outbox or _FakeSimpleTable()
        self.rpc_data = rpc_data
        self.rpc_error = rpc_error
        self.rpc_calls: list[tuple[str, dict[str, Any]]] = []

    def table(self, name: str) -> Any:
        if name == "channel_identities":
            return self.channel_identities
        if name == "profile_versions":
            return self.profile_versions
        if name == "career_facts":
            return self.career_facts
        if name == "jobs":
            return self.jobs
        if name == "job_snapshots":
            return self.job_snapshots
        if name == "applications":
            return self.applications
        if name == "event_outbox":
            return self.event_outbox
        if name == "application_events":
            return self.application_events
        if name == "working_sets":
            return self.working_sets
        raise AssertionError(f"unexpected table: {name}")

    def rpc(self, fn: str, params: dict[str, Any]) -> _FakeRpcBuilder:
        self.rpc_calls.append((fn, params))
        return _FakeRpcBuilder(self.rpc_data, self.rpc_error)


class _FakeTelegramClient:
    def __init__(self, document_bytes: bytes | None = None) -> None:
        self.sent: list[tuple[int, str, dict[str, Any] | None]] = []
        self.answered_callback_ids: list[str] = []
        self._document_bytes = document_bytes

    async def send_message(
        self, chat_id: int, text: str, *, reply_markup: dict[str, Any] | None = None
    ) -> None:
        self.sent.append((chat_id, text, reply_markup))

    async def answer_callback_query(self, callback_query_id: str) -> None:
        self.answered_callback_ids.append(callback_query_id)

    async def download_document(self, file_id: str) -> bytes:
        assert self._document_bytes is not None, "no document configured for this test"
        return self._document_bytes


def _message_update(
    text: str | None = None, document: dict[str, Any] | None = None
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "message_id": 1,
        "from": {"id": _TELEGRAM_USER_ID, "is_bot": False, "first_name": "Test"},
        "chat": {"id": _CHAT_ID, "type": "private"},
        "date": 1785000000,
    }
    if text is not None:
        message["text"] = text
    if document is not None:
        message["document"] = document
    return {"update_id": 1, "message": message}


def _callback_update(data: str) -> dict[str, Any]:
    return {
        "update_id": 2,
        "callback_query": {
            "id": "cbq-1",
            "from": {"id": _TELEGRAM_USER_ID, "is_bot": False, "first_name": "Test"},
            "message": {"message_id": 2, "chat": {"id": _CHAT_ID, "type": "private"}},
            "data": data,
        },
    }


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", _WEBHOOK_SECRET)


def _post(
    supabase: _FakeSupabaseClient, telegram: _FakeTelegramClient, update: dict[str, Any]
) -> Any:
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[get_telegram_client] = lambda: telegram
    try:
        with TestClient(app) as client:
            return client.post(
                "/telegram/webhook",
                json=update,
                headers={"X-Telegram-Bot-Api-Secret-Token": _WEBHOOK_SECRET},
            )
    finally:
        app.dependency_overrides.clear()


def test_missing_secret_header_rejected() -> None:
    with TestClient(app) as client:
        response = client.post("/telegram/webhook", json=_message_update("hi"))
    assert response.status_code == 401


def test_wrong_secret_header_rejected() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/telegram/webhook",
            json=_message_update("hi"),
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
        )
    assert response.status_code == 401


def test_unrecognized_message_from_new_user_provisions_and_gets_fallback() -> None:
    fake_supabase = _FakeSupabaseClient(channel_identities_rows=[])
    fake_telegram = _FakeTelegramClient()
    response = _post(fake_supabase, fake_telegram, _message_update("hi there"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert len(fake_supabase.auth.admin.create_user_calls) == 1
    assert len(fake_telegram.sent) == 1
    assert fake_telegram.sent[0][0] == _CHAT_ID
    assert "set up my resume" in fake_telegram.sent[0][1].lower()


def test_unrecognized_message_from_linked_user_skips_provisioning() -> None:
    fake_supabase = _FakeSupabaseClient(channel_identities_rows=[{"user_id": _EXISTING_USER_ID}])
    fake_telegram = _FakeTelegramClient()
    response = _post(fake_supabase, fake_telegram, _message_update("hi again"))

    assert response.status_code == 200
    assert fake_supabase.auth.admin.create_user_calls == []
    assert len(fake_telegram.sent) == 1


def test_setup_help_sends_the_template() -> None:
    fake_supabase = _FakeSupabaseClient(channel_identities_rows=[{"user_id": _EXISTING_USER_ID}])
    fake_telegram = _FakeTelegramClient()
    response = _post(fake_supabase, fake_telegram, _message_update("set up my resume"))

    assert response.status_code == 200
    reply = fake_telegram.sent[0][1]
    assert "personal" in reply
    assert "experience" in reply


def test_json_paste_creates_pending_version_and_sends_preview_with_buttons() -> None:
    inserted_row = {
        "id": _VERSION_ID,
        "canonical_json": json.loads(_VALID_RESUME_JSON),
        "activated_at": None,
    }
    profile_versions = _FakeProfileVersionsTable(select_rows=[], insert_row=inserted_row)
    fake_supabase = _FakeSupabaseClient(
        channel_identities_rows=[{"user_id": _EXISTING_USER_ID}], profile_versions=profile_versions
    )
    fake_telegram = _FakeTelegramClient()

    response = _post(fake_supabase, fake_telegram, _message_update(_VALID_RESUME_JSON))

    assert response.status_code == 200
    assert len(profile_versions.insert_calls) == 1
    assert profile_versions.insert_calls[0]["source_kind"] == "telegram_json_paste"

    _chat_id, text, reply_markup = fake_telegram.sent[0]
    assert "Jane Doe" in text
    assert reply_markup is not None
    buttons = reply_markup["inline_keyboard"][0]
    assert buttons[0]["callback_data"] == f"profile:activate:{_VERSION_ID}"
    assert buttons[1]["callback_data"] == f"profile:cancel:{_VERSION_ID}"


def test_json_paste_invalid_sends_honest_error_and_does_not_insert() -> None:
    profile_versions = _FakeProfileVersionsTable(select_rows=[])
    fake_supabase = _FakeSupabaseClient(
        channel_identities_rows=[{"user_id": _EXISTING_USER_ID}], profile_versions=profile_versions
    )
    fake_telegram = _FakeTelegramClient()

    # No experience/projects/publications/volunteering -- fails the
    # business-rule check in profile.py (schema v1.1).
    bad_json = json.dumps({"personal": {"name": "Jane"}, "experience": [], "projects": []})
    response = _post(fake_supabase, fake_telegram, _message_update(bad_json))

    assert response.status_code == 200
    assert profile_versions.insert_calls == []
    assert "at least one of" in fake_telegram.sent[0][1]


def test_track_job_help_sends_the_template() -> None:
    fake_supabase = _FakeSupabaseClient(channel_identities_rows=[{"user_id": _EXISTING_USER_ID}])
    fake_telegram = _FakeTelegramClient()
    response = _post(fake_supabase, fake_telegram, _message_update("track a job"))

    assert response.status_code == 200
    reply = fake_telegram.sent[0][1]
    assert "Title:" in reply
    assert "Company:" in reply


def test_job_paste_creates_job_snapshot_and_application() -> None:
    fake_supabase = _FakeSupabaseClient(channel_identities_rows=[{"user_id": _EXISTING_USER_ID}])
    fake_telegram = _FakeTelegramClient()
    text = (
        "Title: Staff AI Engineer\n"
        "Company: Acme\n"
        "Location: Remote\n"
        "\n"
        "We need a Python engineer with RAG experience."
    )

    response = _post(fake_supabase, fake_telegram, _message_update(text))

    assert response.status_code == 200
    assert len(fake_supabase.jobs.insert_calls) == 1
    assert fake_supabase.jobs.insert_calls[0]["company_name"] == "Acme"
    assert len(fake_supabase.job_snapshots.insert_calls) == 1
    assert fake_supabase.job_snapshots.insert_calls[0]["title"] == "Staff AI Engineer"
    assert len(fake_supabase.applications.insert_calls) == 1
    assert fake_supabase.applications.insert_calls[0]["source_channel"] == "telegram"

    reply = fake_telegram.sent[0][1]
    assert "Staff AI Engineer" in reply
    assert "Acme" in reply


def test_job_paste_missing_company_sends_honest_error_and_creates_nothing() -> None:
    fake_supabase = _FakeSupabaseClient(channel_identities_rows=[{"user_id": _EXISTING_USER_ID}])
    fake_telegram = _FakeTelegramClient()
    text = "Title: Staff AI Engineer\n\nWe need a Python engineer."

    response = _post(fake_supabase, fake_telegram, _message_update(text))

    assert response.status_code == 200
    assert fake_supabase.jobs.insert_calls == []
    assert fake_supabase.applications.insert_calls == []
    assert "Missing required field: Company" in fake_telegram.sent[0][1]


_APP_1 = {
    "id": "app-1",
    "user_id": _EXISTING_USER_ID,
    "active_job_snapshot_id": "snap-1",
    "status": "saved",
}
_APP_2 = {
    "id": "app-2",
    "user_id": _EXISTING_USER_ID,
    "active_job_snapshot_id": "snap-2",
    "status": "applied",
}
_SNAP_1 = {"id": "snap-1", "title": "Staff AI Engineer", "company_name": "Acme"}
_SNAP_2 = {"id": "snap-2", "title": "Backend Engineer", "company_name": "Globex"}


def test_list_applications_sends_numbered_list_and_mints_working_set() -> None:
    fake_supabase = _FakeSupabaseClient(
        channel_identities_rows=[{"user_id": _EXISTING_USER_ID}],
        applications=_FakeSimpleTable(select_rows=[_APP_1, _APP_2]),
        job_snapshots=_FakeSimpleTable(select_rows=[_SNAP_1, _SNAP_2]),
    )
    fake_telegram = _FakeTelegramClient()

    response = _post(fake_supabase, fake_telegram, _message_update("list"))

    assert response.status_code == 200
    assert len(fake_supabase.working_sets.insert_calls) == 1
    inserted = fake_supabase.working_sets.insert_calls[0]
    assert inserted["kind"] == "applications"
    assert inserted["items"] == ["app-1", "app-2"]

    reply = fake_telegram.sent[0][1]
    assert "1. Staff AI Engineer @ Acme" in reply
    assert "2. Backend Engineer @ Globex" in reply


def test_list_applications_empty_sends_honest_message_no_working_set() -> None:
    fake_supabase = _FakeSupabaseClient(
        channel_identities_rows=[{"user_id": _EXISTING_USER_ID}],
        applications=_FakeSimpleTable(select_rows=[]),
    )
    fake_telegram = _FakeTelegramClient()

    response = _post(fake_supabase, fake_telegram, _message_update("list"))

    assert response.status_code == 200
    assert fake_supabase.working_sets.insert_calls == []
    assert "Nothing tracked yet" in fake_telegram.sent[0][1]


def test_apply_reference_with_no_working_set_sends_honest_message() -> None:
    fake_supabase = _FakeSupabaseClient(
        channel_identities_rows=[{"user_id": _EXISTING_USER_ID}],
        working_sets=_FakeSimpleTable(select_rows=[]),
    )
    fake_telegram = _FakeTelegramClient()

    response = _post(fake_supabase, fake_telegram, _message_update("apply to #1"))

    assert response.status_code == 200
    assert 'Send "list" first' in fake_telegram.sent[0][1]


def test_apply_reference_expired_working_set_sends_honest_message() -> None:
    expired_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    working_set_row = {"id": "ws-1", "items": ["app-1"], "expires_at": expired_at}
    fake_supabase = _FakeSupabaseClient(
        channel_identities_rows=[{"user_id": _EXISTING_USER_ID}],
        working_sets=_FakeSimpleTable(select_rows=[working_set_row]),
    )
    fake_telegram = _FakeTelegramClient()

    response = _post(fake_supabase, fake_telegram, _message_update("apply to #1"))

    assert response.status_code == 200
    assert "expired" in fake_telegram.sent[0][1]


def test_apply_reference_out_of_range_sends_honest_message() -> None:
    active_at = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
    working_set_row = {"id": "ws-1", "items": ["app-1"], "expires_at": active_at}
    fake_supabase = _FakeSupabaseClient(
        channel_identities_rows=[{"user_id": _EXISTING_USER_ID}],
        working_sets=_FakeSimpleTable(select_rows=[working_set_row]),
    )
    fake_telegram = _FakeTelegramClient()

    response = _post(fake_supabase, fake_telegram, _message_update("apply to #5"))

    assert response.status_code == 200
    assert "#5 isn't on your list" in fake_telegram.sent[0][1]


def test_apply_reference_resolves_and_reaches_the_shared_prepare_flow() -> None:
    # Proves reference resolution correctly maps #1 -> the right
    # application_id and hands off to `_run_prepare_and_deliver` -- that
    # shared function's own success/error branches are already covered
    # thoroughly by test_telegram_prepare_callback.py, so this only needs
    # to reach a point that PROVES the right application_id was used, not
    # re-derive the full orchestration. An empty `profile_versions` table
    # makes `run_prepare_application` fail fast with SETUP_REQUIRED right
    # after resolving the application -- a clean, cheap proof point.
    active_at = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
    working_set_row = {"id": "ws-1", "items": ["app-1"], "expires_at": active_at}
    fake_supabase = _FakeSupabaseClient(
        channel_identities_rows=[{"user_id": _EXISTING_USER_ID}],
        working_sets=_FakeSimpleTable(select_rows=[working_set_row]),
        applications=_FakeSimpleTable(select_rows=[_APP_1]),
        profile_versions=_FakeProfileVersionsTable(select_rows=[]),
    )
    fake_telegram = _FakeTelegramClient()

    response = _post(fake_supabase, fake_telegram, _message_update("apply to #1"))

    assert response.status_code == 200
    texts = [t for _c, t, _r in fake_telegram.sent]
    assert any("Generating" in t for t in texts)
    assert any("profile" in t.lower() for t in texts)


def test_json_file_upload_downloads_decodes_and_imports() -> None:
    inserted_row = {
        "id": _VERSION_ID,
        "canonical_json": json.loads(_VALID_RESUME_JSON),
        "activated_at": None,
    }
    profile_versions = _FakeProfileVersionsTable(select_rows=[], insert_row=inserted_row)
    fake_supabase = _FakeSupabaseClient(
        channel_identities_rows=[{"user_id": _EXISTING_USER_ID}], profile_versions=profile_versions
    )
    fake_telegram = _FakeTelegramClient(document_bytes=_VALID_RESUME_JSON.encode("utf-8"))

    document = {"file_id": "file-123", "file_name": "resume.json", "mime_type": "application/json"}
    response = _post(fake_supabase, fake_telegram, _message_update(document=document))

    assert response.status_code == 200
    assert len(profile_versions.insert_calls) == 1
    assert profile_versions.insert_calls[0]["source_kind"] == "telegram_json_upload"


def test_activate_callback_confirms_and_answers() -> None:
    updated_row = {"id": _VERSION_ID, "activated_at": "2026-08-06T00:00:00Z"}
    profile_versions = _FakeProfileVersionsTable(select_rows=[], update_row=updated_row)
    fake_supabase = _FakeSupabaseClient(
        channel_identities_rows=[{"user_id": _EXISTING_USER_ID}], profile_versions=profile_versions
    )
    fake_telegram = _FakeTelegramClient()

    response = _post(
        fake_supabase, fake_telegram, _callback_update(f"profile:activate:{_VERSION_ID}")
    )

    assert response.status_code == 200
    assert len(profile_versions.update_calls) == 1
    assert "Saved" in fake_telegram.sent[0][1]
    assert fake_telegram.answered_callback_ids == ["cbq-1"]


def test_activate_callback_expired_version_sends_honest_message() -> None:
    profile_versions = _FakeProfileVersionsTable(select_rows=[], update_row=None)
    fake_supabase = _FakeSupabaseClient(
        channel_identities_rows=[{"user_id": _EXISTING_USER_ID}], profile_versions=profile_versions
    )
    fake_telegram = _FakeTelegramClient()

    response = _post(
        fake_supabase, fake_telegram, _callback_update(f"profile:activate:{_VERSION_ID}")
    )

    assert response.status_code == 200
    assert "gone" in fake_telegram.sent[0][1].lower()
    assert fake_telegram.answered_callback_ids == ["cbq-1"]


def test_cancel_callback_deletes_and_answers() -> None:
    pending_row = {"id": _VERSION_ID, "user_id": _EXISTING_USER_ID, "activated_at": None}
    profile_versions = _FakeProfileVersionsTable(select_rows=[pending_row])
    fake_supabase = _FakeSupabaseClient(
        channel_identities_rows=[{"user_id": _EXISTING_USER_ID}], profile_versions=profile_versions
    )
    fake_telegram = _FakeTelegramClient()

    response = _post(
        fake_supabase, fake_telegram, _callback_update(f"profile:cancel:{_VERSION_ID}")
    )

    assert response.status_code == 200
    assert profile_versions.delete_calls == 1
    assert "Cancelled" in fake_telegram.sent[0][1]
    assert fake_telegram.answered_callback_ids == ["cbq-1"]


def test_check_resume_when_none_active() -> None:
    fake_supabase = _FakeSupabaseClient(
        channel_identities_rows=[{"user_id": _EXISTING_USER_ID}],
        profile_versions=_FakeProfileVersionsTable(select_rows=[]),
    )
    fake_telegram = _FakeTelegramClient()
    response = _post(fake_supabase, fake_telegram, _message_update("check my resume"))

    assert response.status_code == 200
    assert "don't have a resume" in fake_telegram.sent[0][1].lower()


def test_check_resume_when_active() -> None:
    active_row = {
        "canonical_json": {"personal": {"name": "Jane Doe", "headline": "Engineer"}},
        "activated_at": "2026-08-06T00:00:00Z",
    }
    fake_supabase = _FakeSupabaseClient(
        channel_identities_rows=[{"user_id": _EXISTING_USER_ID}],
        profile_versions=_FakeProfileVersionsTable(select_rows=[active_row]),
    )
    fake_telegram = _FakeTelegramClient()
    response = _post(fake_supabase, fake_telegram, _message_update("check my resume"))

    assert response.status_code == 200
    reply = fake_telegram.sent[0][1]
    assert "Jane Doe" in reply
    assert "2026-08-06" in reply


def test_non_message_update_ignored() -> None:
    fake_supabase = _FakeSupabaseClient(channel_identities_rows=[])
    fake_telegram = _FakeTelegramClient()
    response = _post(
        fake_supabase, fake_telegram, {"update_id": 3, "edited_message": {"text": "oops"}}
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
    assert fake_supabase.auth.admin.create_user_calls == []
    assert fake_telegram.sent == []


def test_link_success_sends_summary_and_cleans_up_orphan() -> None:
    fake_supabase = _FakeSupabaseClient(
        channel_identities_rows=[{"user_id": _EXISTING_USER_ID}],
        rpc_data={
            "ok": True,
            "target_user_id": "target-web-user",
            "summary": {"profile_versions": 1, "applications": 2, "provider_credentials": 0},
        },
    )
    fake_telegram = _FakeTelegramClient()

    response = _post(fake_supabase, fake_telegram, _message_update("/link ABCD2345"))

    assert response.status_code == 200
    assert fake_supabase.rpc_calls == [
        (
            "consume_link_code",
            {
                "p_channel": "telegram",
                "p_external_subject": str(_TELEGRAM_USER_ID),
                "p_code": "ABCD2345",
                "p_source_user_id": _EXISTING_USER_ID,
            },
        )
    ]
    reply = fake_telegram.sent[0][1]
    assert "Linked" in reply
    assert "resume version" in reply
    assert "tracked application" in reply
    # The now-empty source identity's auth user gets cleaned up.
    assert fake_supabase.auth.admin.delete_user_calls == [_EXISTING_USER_ID]


def test_link_already_linked_to_same_account_is_a_friendly_noop() -> None:
    fake_supabase = _FakeSupabaseClient(
        channel_identities_rows=[{"user_id": _EXISTING_USER_ID}],
        rpc_data={"ok": True, "target_user_id": _EXISTING_USER_ID, "summary": {}},
    )
    fake_telegram = _FakeTelegramClient()

    response = _post(fake_supabase, fake_telegram, _message_update("/link ABCD2345"))

    assert response.status_code == 200
    assert "already linked" in fake_telegram.sent[0][1].lower()
    assert fake_supabase.auth.admin.delete_user_calls == []


@pytest.mark.parametrize(
    ("reason", "expected_snippet"),
    [
        ("invalid_code", "isn't valid"),
        ("expired_code", "expired"),
        ("rate_limited", "too many"),
    ],
)
def test_link_soft_failures_send_distinct_messages(reason: str, expected_snippet: str) -> None:
    fake_supabase = _FakeSupabaseClient(
        channel_identities_rows=[{"user_id": _EXISTING_USER_ID}],
        rpc_data={"ok": False, "reason": reason},
    )
    fake_telegram = _FakeTelegramClient()

    response = _post(fake_supabase, fake_telegram, _message_update("/link WRONGONE"))

    assert response.status_code == 200
    assert expected_snippet in fake_telegram.sent[0][1].lower()
    assert fake_supabase.auth.admin.delete_user_calls == []


def test_link_collision_sends_conflict_message_without_leaking_db_details() -> None:
    collision = APIError(
        {
            "message": "duplicate key value violates unique constraint "
            '"provider_credentials_user_id_service_provider_key"',
            "code": "23505",
            "hint": None,
            "details": None,
        }
    )
    fake_supabase = _FakeSupabaseClient(
        channel_identities_rows=[{"user_id": _EXISTING_USER_ID}], rpc_error=collision
    )
    fake_telegram = _FakeTelegramClient()

    response = _post(fake_supabase, fake_telegram, _message_update("/link ABCD2345"))

    assert response.status_code == 200
    reply = fake_telegram.sent[0][1]
    assert "conflicting data" in reply.lower()
    assert "constraint" not in reply
    assert "provider_credentials" not in reply


def test_unlink_deletes_the_channel_identity() -> None:
    fake_supabase = _FakeSupabaseClient(channel_identities_rows=[{"user_id": _EXISTING_USER_ID}])
    fake_telegram = _FakeTelegramClient()

    response = _post(fake_supabase, fake_telegram, _message_update("/unlink"))

    assert response.status_code == 200
    assert fake_supabase.channel_identities.delete_calls == 1
    assert "unlinked" in fake_telegram.sent[0][1].lower()
