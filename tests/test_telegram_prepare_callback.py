"""Tests for the Telegram "Generate resume" callback (Sprint 3.4b).

`_handle_prepare_callback` calls the exact same `run_prepare_application`/
`latest_resume_pdf` orchestration the web's `/prepare` and `/resume.pdf`
routes call (test_prepare_application_route.py / test_resume_export_routes.py
already cover that orchestration's own behavior thoroughly). What's new
here is the Telegram-side glue: does the callback answer immediately, call
the orchestration correctly, and turn the result into the right chat
output (a document + caption on success, a plain error message otherwise).
Kept in its own file rather than folded into test_telegram_webhook.py --
this flow's fake Supabase/http surface (capability_preferences,
provider_credentials, artifact_versions, Storage, forge-engines AND
latex-service HTTP calls) is materially different from that file's
resume-import-focused fakes, the same way test_prepare_application_route.py
already stands apart from test_applications_routes.py.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from between_jobs.api.app import app
from between_jobs.api.app_state import get_http_client, get_supabase, get_telegram_client

_TELEGRAM_USER_ID = 987654321
_CHAT_ID = 987654321
_USER_ID = "00000000-0000-0000-0000-000000000001"
_APPLICATION_ID = "30000000-0000-0000-0000-000000000001"
_SNAPSHOT_ID = "20000000-0000-0000-0000-000000000002"
_PROFILE_VERSION_ID = "40000000-0000-0000-0000-000000000001"

_WEBHOOK_SECRET = "test-secret-not-real"

_APPLICATION_ROW = {
    "id": _APPLICATION_ID,
    "user_id": _USER_ID,
    "active_job_snapshot_id": _SNAPSHOT_ID,
}
_SNAPSHOT_ROW = {
    "id": _SNAPSHOT_ID,
    "job_id": "job-1",
    "title": "Staff Engineer",
    "company_name": "Acme",
    "location_text": "Remote",
    "description_text": "Build things.",
    "source_url": "https://example.com/jobs/1",
    "source_kind": "manual_paste",
}
_PROFILE_VERSION_ROW = {
    "id": _PROFILE_VERSION_ID,
    "user_id": _USER_ID,
    "activated_at": "2026-08-01T00:00:00Z",
    "canonical_json": {"personal": {"name": "Jordan Rivera"}},
}
_PREFERENCE_ROW = {
    "capability": "default",
    "execution_mode": "byok_first_party",
    "provider": "openrouter",
    "model": "anthropic/claude-sonnet-4-6",
}
_CREDENTIAL_ROW = {
    "provider": "openrouter",
    "model": "anthropic/claude-sonnet-4-6",
    "base_url": None,
    "secret_encrypted": "ciphertext-abc",
}
_ARTIFACT_VERSION_ROW = {
    "id": "version-row-1",
    "artifact_id": "artifact-1",
    "version": 1,
    "media_type": "application/x-tex",
    "sha256": "deadbeef",
    "storage_key": f"{_USER_ID}/artifact-1/1",
    "warnings": ["Borderline seniority match."],
}

_LATEX_SOURCE = r"\begin{document}hello\end{document}"
_PDF_BYTES = b"%PDF-1.5 fake pdf bytes"

_FORGE_APPLY_RESPONSE_BODY = {
    "job": {"title": "Staff Engineer"},
    "personal": {"name": "Jordan Rivera"},
    "seniority": {"mode": "mid"},
    "fit": {"overall_score": 8.0},
    "gate": {"outcome": "proceed", "reason": "", "cautions": ["Borderline seniority match."]},
    "resume": {"latex": _LATEX_SOURCE, "resumePlainText": "hello"},
    "ats_attempts": [
        {
            "overall_score": 72,
            "breakdown": {
                "semanticCoverage": 80,
                "experienceQuality": 70,
                "hardReqScore": 90,
                "quantification": 60,
                "companyAlignment": 50,
                "structure": 100,
            },
            "confidence": "high",
            "rating": "strong",
            "gaps": [],
        }
    ],
    "regenerated": False,
    "pass1": {},
    "step0": {},
}


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def order(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def limit(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    @property
    def not_(self) -> _ChainBuilder:
        return self

    def is_(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTable:
    def __init__(
        self, *, select_rows: list[dict[str, Any]], insert_row: dict[str, Any] | None = None
    ) -> None:
        self.select_rows = select_rows
        self.insert_row = insert_row
        self.insert_calls: list[dict[str, Any]] = []

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def insert(self, data: dict[str, Any]) -> _ChainBuilder:
        self.insert_calls.append(data)
        rows = [self.insert_row] if self.insert_row is not None else [{**data, "id": "row-1"}]
        return _ChainBuilder(rows)


class _FakeRpcBuilder:
    def __init__(self, data: Any) -> None:
        self._data = data

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._data)


class _FakeChannelIdentitiesTable:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self._rows)

    def insert(self, data: dict[str, Any]) -> _ChainBuilder:
        return _ChainBuilder([{}])


class _FakeBucket:
    def __init__(self, *, download_bytes: bytes = _LATEX_SOURCE.encode("utf-8")) -> None:
        self.uploads: list[tuple[str, bytes, dict[str, Any]]] = []
        self._download_bytes = download_bytes

    async def upload(self, path: str, file: bytes, file_options: dict[str, Any]) -> None:
        self.uploads.append((path, file, file_options))

    async def download(self, _path: str) -> bytes:
        return self._download_bytes


class _FakeStorage:
    def __init__(self, bucket: _FakeBucket) -> None:
        self._bucket = bucket

    def from_(self, _bucket_id: str) -> _FakeBucket:
        return self._bucket


class _FakeSupabaseClient:
    def __init__(
        self,
        *,
        channel_identities_rows: list[dict[str, Any]],
        applications: _FakeTable | None = None,
        profile_versions: _FakeTable | None = None,
        capability_preferences: _FakeTable | None = None,
        artifact_versions: _FakeTable | None = None,
    ) -> None:
        self.auth = SimpleNamespace(admin=SimpleNamespace())
        self.channel_identities = _FakeChannelIdentitiesTable(channel_identities_rows)
        self.applications = applications or _FakeTable(select_rows=[_APPLICATION_ROW])
        self.job_snapshots = _FakeTable(select_rows=[_SNAPSHOT_ROW])
        self.profile_versions = profile_versions or _FakeTable(select_rows=[_PROFILE_VERSION_ROW])
        self.capability_preferences = capability_preferences or _FakeTable(
            select_rows=[_PREFERENCE_ROW]
        )
        self.provider_credentials = _FakeTable(select_rows=[_CREDENTIAL_ROW])
        self.application_events = _FakeTable(select_rows=[], insert_row={"id": "event-1"})
        self.artifact_versions = artifact_versions or _FakeTable(
            select_rows=[_ARTIFACT_VERSION_ROW], insert_row=_ARTIFACT_VERSION_ROW
        )
        self.event_outbox = _FakeTable(select_rows=[])
        # R6: run_prepare_application looks up both the master and the
        # per-application resume_documents row for shape_overrides -- empty
        # by default (get_document_for returns None), matching every
        # existing test here, which predates shape_overrides and doesn't
        # exercise it.
        self.resume_documents = _FakeTable(select_rows=[])
        self.bucket = _FakeBucket()
        self.storage = _FakeStorage(self.bucket)

    def table(self, name: str) -> Any:
        return {
            "channel_identities": self.channel_identities,
            "applications": self.applications,
            "job_snapshots": self.job_snapshots,
            "profile_versions": self.profile_versions,
            "capability_preferences": self.capability_preferences,
            "provider_credentials": self.provider_credentials,
            "application_events": self.application_events,
            "artifact_versions": self.artifact_versions,
            "event_outbox": self.event_outbox,
            "resume_documents": self.resume_documents,
        }[name]

    def rpc(self, fn: str, _params: dict[str, Any]) -> _FakeRpcBuilder:
        if fn == "decrypt_secret":
            return _FakeRpcBuilder("sk-or-v1-real-secret")
        raise AssertionError(f"unexpected rpc: {fn}")


class _FakeHttpClient:
    """Branches on URL suffix: `/apply` (forge-engines) vs `/compile`
    (latex-service) -- the callback calls both in sequence."""

    def __init__(
        self,
        *,
        apply_status_code: int = 200,
        apply_body: Any = None,
        compile_status_code: int = 200,
    ) -> None:
        self.apply_status_code = apply_status_code
        self.apply_body = apply_body if apply_body is not None else _FORGE_APPLY_RESPONSE_BODY
        self.compile_status_code = compile_status_code
        self.post_calls: list[str] = []

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.post_calls.append(url)
        if url.endswith("/apply"):
            return httpx.Response(
                status_code=self.apply_status_code,
                json=self.apply_body,
                request=httpx.Request("POST", url),
            )
        if url.endswith("/compile"):
            if self.compile_status_code == 200:
                return httpx.Response(
                    status_code=200, content=_PDF_BYTES, request=httpx.Request("POST", url)
                )
            return httpx.Response(
                status_code=self.compile_status_code,
                json={"error": "CompileError", "message": "boom"},
                request=httpx.Request("POST", url),
            )
        raise AssertionError(f"unexpected POST: {url}")


class _FakeTelegramClient:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, dict[str, Any] | None]] = []
        self.documents_sent: list[tuple[int, str, bytes, str | None]] = []
        self.answered_callback_ids: list[str] = []

    async def send_message(
        self, chat_id: int, text: str, *, reply_markup: dict[str, Any] | None = None
    ) -> None:
        self.sent.append((chat_id, text, reply_markup))

    async def send_document(
        self, chat_id: int, filename: str, content: bytes, *, caption: str | None = None
    ) -> None:
        self.documents_sent.append((chat_id, filename, content, caption))

    async def answer_callback_query(self, callback_query_id: str) -> None:
        self.answered_callback_ids.append(callback_query_id)


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
    supabase: _FakeSupabaseClient,
    telegram: _FakeTelegramClient,
    http: _FakeHttpClient,
    update: dict[str, Any],
) -> Any:
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[get_telegram_client] = lambda: telegram
    app.dependency_overrides[get_http_client] = lambda: http
    try:
        with TestClient(app) as client:
            return client.post(
                "/telegram/webhook",
                json=update,
                headers={"X-Telegram-Bot-Api-Secret-Token": _WEBHOOK_SECRET},
            )
    finally:
        app.dependency_overrides.clear()


def test_prepare_callback_success_sends_document_with_score_caption() -> None:
    supabase = _FakeSupabaseClient(channel_identities_rows=[{"user_id": _USER_ID}])
    telegram = _FakeTelegramClient()
    http = _FakeHttpClient()

    response = _post(supabase, telegram, http, _callback_update(f"app:prepare:{_APPLICATION_ID}"))

    assert response.status_code == 200
    assert telegram.answered_callback_ids == ["cbq-1"]
    assert any("Generating" in text for _cid, text, _rm in telegram.sent)
    assert len(telegram.documents_sent) == 1
    chat_id, filename, content, caption = telegram.documents_sent[0]
    assert chat_id == _CHAT_ID
    assert filename == "resume.pdf"
    assert content == _PDF_BYTES
    assert caption is not None
    assert "72" in caption
    assert "Borderline seniority match." in caption
    assert http.post_calls[0].endswith("/apply")
    assert http.post_calls[1].endswith("/compile")

    # Sprint 3.4d -- a follow-up "Mark as applied" prompt with the right
    # application_id baked into its callback_data.
    prompt = next(rm for _c, _t, rm in telegram.sent if rm is not None)
    assert (
        prompt["inline_keyboard"][0][0]["callback_data"] == f"app:stage:{_APPLICATION_ID}:applied"
    )


def test_prepare_callback_answers_the_callback_before_running() -> None:
    # The spinner-stopping answer must not wait on the (potentially
    # minute-long) prepare run -- verified indirectly here by confirming
    # it's answered even though the fake http client always responds
    # synchronously; a real slow run would still hit this line first.
    supabase = _FakeSupabaseClient(channel_identities_rows=[{"user_id": _USER_ID}])
    telegram = _FakeTelegramClient()
    http = _FakeHttpClient()

    _post(supabase, telegram, http, _callback_update(f"app:prepare:{_APPLICATION_ID}"))

    assert "cbq-1" in telegram.answered_callback_ids


def test_prepare_callback_setup_required_sends_honest_error_no_document() -> None:
    supabase = _FakeSupabaseClient(
        channel_identities_rows=[{"user_id": _USER_ID}],
        capability_preferences=_FakeTable(select_rows=[]),
    )
    telegram = _FakeTelegramClient()
    http = _FakeHttpClient()

    response = _post(supabase, telegram, http, _callback_update(f"app:prepare:{_APPLICATION_ID}"))

    assert response.status_code == 200
    assert telegram.documents_sent == []
    assert http.post_calls == []
    error_texts = [text for _cid, text, _rm in telegram.sent if text.startswith("❌")]
    assert len(error_texts) == 1


def test_prepare_callback_declined_gate_sends_warnings_no_document() -> None:
    declined_body = {
        **_FORGE_APPLY_RESPONSE_BODY,
        "resume": None,
        "ats_attempts": [],
        "gate": {"outcome": "skip_low_score", "reason": "Fit score too low.", "cautions": []},
    }
    supabase = _FakeSupabaseClient(channel_identities_rows=[{"user_id": _USER_ID}])
    telegram = _FakeTelegramClient()
    http = _FakeHttpClient(apply_body=declined_body)

    response = _post(supabase, telegram, http, _callback_update(f"app:prepare:{_APPLICATION_ID}"))

    assert response.status_code == 200
    assert telegram.documents_sent == []
    declined_texts = [text for _cid, text, _rm in telegram.sent if "Fit score too low." in text]
    assert len(declined_texts) == 1


def test_prepare_callback_compile_failure_sends_honest_error() -> None:
    supabase = _FakeSupabaseClient(channel_identities_rows=[{"user_id": _USER_ID}])
    telegram = _FakeTelegramClient()
    http = _FakeHttpClient(compile_status_code=422)

    response = _post(supabase, telegram, http, _callback_update(f"app:prepare:{_APPLICATION_ID}"))

    assert response.status_code == 200
    assert telegram.documents_sent == []
    error_texts = [text for _cid, text, _rm in telegram.sent if text.startswith("❌")]
    assert len(error_texts) == 1
