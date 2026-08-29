"""Tests for POST /applications/{id}/prepare (Sprint 3.0e).

Exercises the real FastAPI route via TestClient, same shape as
test_applications_routes.py/test_credentials_routes.py -- the outbound
call to forge-engines is faked at the httpx client boundary
(`get_http_client`'s dependency override), never a real network call.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from between_jobs.api.app import app
from between_jobs.api.app_state import get_http_client, get_supabase
from between_jobs.api.auth import require_user_id

_USER_ID = "00000000-0000-0000-0000-000000000001"
_APPLICATION_ID = "30000000-0000-0000-0000-000000000001"
_SNAPSHOT_ID = "20000000-0000-0000-0000-000000000002"
_PROFILE_VERSION_ID = "40000000-0000-0000-0000-000000000001"

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

_FORGE_APPLY_RESPONSE_BODY = {
    "job": {"title": "Staff Engineer"},
    "personal": {"name": "Jordan Rivera"},
    "seniority": {"mode": "mid"},
    "fit": {"overall_score": 8.0},
    "gate": {"outcome": "proceed", "reason": "", "cautions": ["Borderline seniority match."]},
    "resume": {"latex": r"\begin{document}hello\end{document}", "resumePlainText": "hello"},
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
        # Real postgrest-py exposes `.not_` as a property returning a
        # filter-builder proxy (`.eq(...).not_.is_(...)`), not a method --
        # matching that shape here rather than the more common `def not_(self)`
        # is what makes `get_active_version`'s real query chain work.
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


class _FakeBucket:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, bytes, dict[str, Any]]] = []

    async def upload(self, path: str, file: bytes, file_options: dict[str, Any]) -> None:
        self.uploads.append((path, file, file_options))


class _FakeStorage:
    def __init__(self, bucket: _FakeBucket) -> None:
        self._bucket = bucket

    def from_(self, bucket_id: str) -> _FakeBucket:
        return self._bucket


class _FakeSupabaseClient:
    def __init__(
        self,
        *,
        applications: _FakeTable | None = None,
        job_snapshots: _FakeTable | None = None,
        profile_versions: _FakeTable | None = None,
        capability_preferences: _FakeTable | None = None,
        provider_credentials: _FakeTable | None = None,
        application_events: _FakeTable | None = None,
        artifact_versions: _FakeTable | None = None,
        event_outbox: _FakeTable | None = None,
        resume_documents: _FakeTable | None = None,
    ) -> None:
        self.applications = applications or _FakeTable(select_rows=[_APPLICATION_ROW])
        self.job_snapshots = job_snapshots or _FakeTable(select_rows=[_SNAPSHOT_ROW])
        self.profile_versions = profile_versions or _FakeTable(select_rows=[_PROFILE_VERSION_ROW])
        self.capability_preferences = capability_preferences or _FakeTable(
            select_rows=[_PREFERENCE_ROW]
        )
        self.provider_credentials = provider_credentials or _FakeTable(
            select_rows=[_CREDENTIAL_ROW]
        )
        self.application_events = application_events or _FakeTable(
            select_rows=[], insert_row={"id": "event-1"}
        )
        self.artifact_versions = artifact_versions or _FakeTable(
            select_rows=[],
            insert_row={
                "id": "version-row-1",
                "artifact_id": "artifact-1",
                "version": 1,
                "media_type": "application/x-tex",
                "sha256": "deadbeef",
            },
        )
        self.event_outbox = event_outbox or _FakeTable(select_rows=[])
        # R6: run_prepare_application looks up both the master and the
        # per-application resume_documents row for shape_overrides -- empty
        # by default (get_document_for returns None), matching every
        # existing test here, which predates shape_overrides and doesn't
        # exercise it.
        self.resume_documents = resume_documents or _FakeTable(select_rows=[])
        self.bucket = _FakeBucket()
        self.storage = _FakeStorage(self.bucket)

    def table(self, name: str) -> Any:
        return {
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

    def rpc(self, fn: str, params: dict[str, Any]) -> _FakeRpcBuilder:
        if fn == "decrypt_secret":
            return _FakeRpcBuilder("sk-or-v1-real-secret")
        raise AssertionError(f"unexpected rpc: {fn}")


class _FakeHttpClient:
    def __init__(self, *, status_code: int = 200, body: Any = None) -> None:
        self.status_code = status_code
        self.body = body if body is not None else _FORGE_APPLY_RESPONSE_BODY
        self.post_calls: list[tuple[str, dict[str, Any]]] = []

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.post_calls.append((url, kwargs))
        return httpx.Response(
            status_code=self.status_code, json=self.body, request=httpx.Request("POST", url)
        )


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")


def _client(supabase: _FakeSupabaseClient, http: _FakeHttpClient) -> TestClient:
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    app.dependency_overrides[get_http_client] = lambda: http
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Any:
    yield
    app.dependency_overrides.clear()


def _prepare_body(**overrides: Any) -> dict[str, Any]:
    body = {"idempotency_key": "prepare-" + "a" * 16}
    body.update(overrides)
    return body


def test_prepare_application_success_stores_artifact_and_returns_result() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/prepare", json=_prepare_body())

    assert response.status_code == 201
    body = response.json()
    assert body["profile_version_id"] == _PROFILE_VERSION_ID
    assert body["job_snapshot_id"] == _SNAPSHOT_ID
    assert body["resume"]["artifact_id"] == "artifact-1"
    assert body["final_score"] == 72.0
    assert len(body["ats_attempts"]) == 1
    assert body["ats_attempts"][0]["breakdown"]["semantic_coverage"] == 80
    assert body["warnings"] == ["Borderline seniority match."]
    assert body["cover_letter"] is None
    assert body["application_answers_id"] is None
    assert body["evidence_fact_ids"] == []
    assert body["fit"]["overall_score"] == 8.0

    assert len(supabase.bucket.uploads) == 1
    assert len(supabase.artifact_versions.insert_calls) == 1
    assert supabase.artifact_versions.insert_calls[0]["document_kind"] == "resume"
    assert len(supabase.application_events.insert_calls) == 1
    assert supabase.application_events.insert_calls[0]["event_type"] == "application.prepared"
    assert len(supabase.event_outbox.insert_calls) == 1
    assert supabase.event_outbox.insert_calls[0]["event_type"] == "artifact.generated.v1"


def test_prepare_application_no_active_profile_returns_setup_required() -> None:
    supabase = _FakeSupabaseClient(profile_versions=_FakeTable(select_rows=[]))
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/prepare", json=_prepare_body())

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SETUP_REQUIRED"
    assert http.post_calls == []


def test_prepare_application_not_found_returns_404() -> None:
    supabase = _FakeSupabaseClient(applications=_FakeTable(select_rows=[]))
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/prepare", json=_prepare_body())

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_prepare_application_declined_gate_stores_no_artifact() -> None:
    declined_body = {
        **_FORGE_APPLY_RESPONSE_BODY,
        "resume": None,
        "ats_attempts": [],
        "gate": {"outcome": "skip_low_score", "reason": "Fit score too low.", "cautions": []},
    }
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(body=declined_body)

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/prepare", json=_prepare_body())

    assert response.status_code == 201
    body = response.json()
    assert body["resume"] is None
    assert body["final_score"] is None
    assert body["warnings"] == ["Fit score too low."]
    # S4c: the Honest Floor read is still there on a decline -- it's the
    # whole reason to show one instead of just the bare reason string.
    assert body["fit"]["overall_score"] == 8.0
    assert supabase.bucket.uploads == []
    assert supabase.artifact_versions.insert_calls == []
    assert len(supabase.event_outbox.insert_calls) == 1
    assert supabase.event_outbox.insert_calls[0]["event_type"] == "artifact.generation_failed.v1"


def test_prepare_application_sends_force_generate_to_forge_engines() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/prepare",
            json=_prepare_body(force_generate=True),
        )

    assert response.status_code == 201
    apply_call = next(kwargs for url, kwargs in http.post_calls if url.endswith("/apply"))
    assert apply_call["json"]["force_generate"] is True


def test_prepare_application_defaults_force_generate_to_false() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        client.post(f"/applications/{_APPLICATION_ID}/prepare", json=_prepare_body())

    apply_call = next(kwargs for url, kwargs in http.post_calls if url.endswith("/apply"))
    assert apply_call["json"]["force_generate"] is False


def test_prepare_application_sends_generate_cover_letter_to_forge_engines() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/prepare",
            json=_prepare_body(generate_cover_letter=True),
        )

    assert response.status_code == 201
    apply_call = next(kwargs for url, kwargs in http.post_calls if url.endswith("/apply"))
    assert apply_call["json"]["generate_cover_letter"] is True


def test_prepare_application_defaults_generate_cover_letter_to_false() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        client.post(f"/applications/{_APPLICATION_ID}/prepare", json=_prepare_body())

    apply_call = next(kwargs for url, kwargs in http.post_calls if url.endswith("/apply"))
    assert apply_call["json"]["generate_cover_letter"] is False


def test_prepare_application_stores_cover_letter_artifact_when_present() -> None:
    # C1/C2 (coverforge-port.md): forge-engines only ever returns cover_letter
    # when the caller opted in -- this fixture matches that real shape.
    body_with_cover_letter = {
        **_FORGE_APPLY_RESPONSE_BODY,
        "cover_letter": {"latex": r"\documentclass{article}hello\end{document}", "word_count": 42},
    }
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(body=body_with_cover_letter)

    with _client(supabase, http) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/prepare",
            json=_prepare_body(generate_cover_letter=True),
        )

    assert response.status_code == 201
    body = response.json()
    assert body["cover_letter"] is not None
    assert body["cover_letter"]["artifact_id"] == "artifact-1"
    assert body["resume"]["artifact_id"] == "artifact-1"

    # Two separate artifact_versions inserts -- resume and cover_letter,
    # own document_kind each, both under the same application/profile
    # version/job snapshot per Proposal §7.3's "one shared transaction".
    assert len(supabase.artifact_versions.insert_calls) == 2
    document_kinds = {c["document_kind"] for c in supabase.artifact_versions.insert_calls}
    assert document_kinds == {"resume", "cover_letter"}
    assert len(supabase.bucket.uploads) == 2


def test_prepare_application_no_cover_letter_when_not_generated() -> None:
    # generate_cover_letter=True but forge-engines' gate declined (or the
    # caller simply didn't opt in) -- cover_letter stays None, no second
    # artifact write happens.
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/prepare",
            json=_prepare_body(generate_cover_letter=True),
        )

    assert response.status_code == 201
    assert response.json()["cover_letter"] is None
    assert len(supabase.artifact_versions.insert_calls) == 1
    assert supabase.artifact_versions.insert_calls[0]["document_kind"] == "resume"


def test_prepare_application_folds_claim_warnings_into_the_shared_warnings_list() -> None:
    """C4 (coverforge-port.md): forge-engines' `claim_warnings` rides the
    SAME `warnings` channel gate cautions/shape warnings/violations already
    use -- no separate response field, no separate stored column."""
    body_with_claim_warning = {
        **_FORGE_APPLY_RESPONSE_BODY,
        "claim_warnings": ['unsupported claim (contradicted): "Led 20 engineers" -- no match'],
    }
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(body=body_with_claim_warning)

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/prepare", json=_prepare_body())

    assert response.status_code == 201
    body = response.json()
    assert body["warnings"] == [
        "Borderline seniority match.",
        'unsupported claim (contradicted): "Led 20 engineers" -- no match',
    ]
    # Stored on the resume artifact_versions row too -- the export
    # checklist reads it back from there later, not from this response.
    assert (
        'unsupported claim (contradicted): "Led 20 engineers" -- no match'
        in supabase.artifact_versions.insert_calls[0]["warnings"]
    )


def test_prepare_application_no_claim_warnings_when_nothing_flagged() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()  # _FORGE_APPLY_RESPONSE_BODY has no claim_warnings key at all

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/prepare", json=_prepare_body())

    assert response.json()["warnings"] == ["Borderline seniority match."]


def test_prepare_application_is_idempotent_on_retry() -> None:
    already_prepared = {
        "id": "event-1",
        "idempotency_key": _prepare_body()["idempotency_key"],
        "payload": {"run_id": "run-1", "resume": None, "final_score": None},
    }
    supabase = _FakeSupabaseClient(
        application_events=_FakeTable(select_rows=[already_prepared]),
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/prepare", json=_prepare_body())

    # Same status_code=201 as every other path this route returns --
    # matches create_application_from_paste's own idempotent-hit precedent
    # (still 201 even when the row already existed).
    assert response.status_code == 201
    assert response.json() == already_prepared["payload"]
    assert http.post_calls == []
