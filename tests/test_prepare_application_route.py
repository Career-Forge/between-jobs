"""Tests for POST /applications/{id}/prepare (Sprint 3.0e).

Exercises the real FastAPI route via TestClient, same shape as
test_applications_routes.py/test_credentials_routes.py -- the outbound
call to forge-engines is faked at the httpx client boundary
(`get_http_client`'s dependency override), never a real network call.
"""

from __future__ import annotations

import asyncio
import io
import json
import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from generic_engine_fakes import ScriptedModel, echo_body
from generic_engine_fakes import profile as sample_profile
from generic_engine_fakes import snapshot as sample_snapshot

from between_jobs.api import engine_gateway, product_events
from between_jobs.api.app import app
from between_jobs.api.app_state import get_http_client, get_supabase
from between_jobs.api.auth import require_user_id
from between_jobs.api.engine_contract import PrepareApplicationResult
from between_jobs.api.errors import ApiError
from between_jobs.api.forge_engines_client import ForgeApplyResult
from between_jobs.engines import CAPABILITIES_BY_KIND, GenericBackend

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
        career_facts: _FakeTable | None = None,
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
        # Only an engine that reports which profile entries it drew on (the built-in one) makes
        # the prepare flow look their facts up.
        self.career_facts = career_facts or _FakeTable(select_rows=[])
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
            "career_facts": self.career_facts,
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
    assert body["gate_outcome"] == "proceed"
    assert body["gate_cautions"] == ["Borderline seniority match."]

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
    # Phase I: the literal gate verdict now survives too, not just its
    # reason folded into plain warning text.
    assert body["gate_outcome"] == "skip_low_score"
    assert body["gate_reason"] == "Fit score too low."
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


def test_prepare_application_sends_the_saved_header_layout_to_forge_engines() -> None:
    """P0.12: the Header Composer layout a candidate saved has to reach the engine on the real
    /prepare path. Before this nothing sent it and every generation used the default header."""
    layout = {"chips": [{"field": "github"}, {"field": "email"}], "separator": "dot"}
    supabase = _FakeSupabaseClient(
        resume_documents=_FakeTable(select_rows=[{"id": "doc-1", "header_layout": layout}])
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/prepare", json=_prepare_body())

    assert response.status_code == 201
    apply_call = next(kwargs for url, kwargs in http.post_calls if url.endswith("/apply"))
    assert apply_call["json"]["header_layout"] == layout


@pytest.mark.parametrize(
    "documents",
    [[], [{"id": "doc-1", "header_layout": {}}], [{"id": "doc-1"}]],
)
def test_prepare_application_sends_no_layout_when_none_was_saved(
    documents: list[dict[str, Any]],
) -> None:
    supabase = _FakeSupabaseClient(resume_documents=_FakeTable(select_rows=documents))
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        client.post(f"/applications/{_APPLICATION_ID}/prepare", json=_prepare_body())

    apply_call = next(kwargs for url, kwargs in http.post_calls if url.endswith("/apply"))
    assert apply_call["json"]["header_layout"] is None


class _DocumentsByApplication:
    """resume_documents rows keyed by application_id: `.is_("application_id", None)` is the
    master document, `.eq("application_id", id)` an application's own."""

    def __init__(self, master: Any, own: Any) -> None:
        self._rows = [{"application_id": None, "header_layout": master}]
        if own is not None:
            self._rows.append({"application_id": _APPLICATION_ID, "header_layout": own})
        self._application: Any = "unset"

    def select(self, *_: Any, **__: Any) -> _DocumentsByApplication:
        self._application = "unset"
        return self

    def eq(self, column: str, value: Any) -> _DocumentsByApplication:
        if column == "application_id":
            self._application = value
        return self

    def is_(self, column: str, value: Any) -> _DocumentsByApplication:
        if column == "application_id" and value is None:
            self._application = None
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(
            data=[r for r in self._rows if r["application_id"] == self._application]
        )


_MASTER_LAYOUT = {"chips": [{"field": "github"}], "separator": "dot"}
_OWN_LAYOUT = {"chips": [{"field": "phone"}], "separator": "bullet"}


@pytest.mark.parametrize(
    ("master", "own", "expected"),
    [
        (_MASTER_LAYOUT, _OWN_LAYOUT, _OWN_LAYOUT),  # the application's own layout wins
        (_MASTER_LAYOUT, {}, _MASTER_LAYOUT),  # nothing saved for it: the master is the default
        (_MASTER_LAYOUT, None, _MASTER_LAYOUT),  # no document for it yet at all
        ({}, _OWN_LAYOUT, _OWN_LAYOUT),
        ({}, {}, None),
    ],
)
def test_prepare_application_picks_the_right_document_for_the_header_layout(
    master: Any, own: Any, expected: Any
) -> None:
    supabase = _FakeSupabaseClient(resume_documents=_DocumentsByApplication(master, own))  # type: ignore[arg-type]
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(f"/applications/{_APPLICATION_ID}/prepare", json=_prepare_body())

    assert response.status_code == 201
    apply_call = next(kwargs for url, kwargs in http.post_calls if url.endswith("/apply"))
    assert apply_call["json"]["header_layout"] == expected


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


def test_get_prepare_result_returns_the_latest_stored_payload() -> None:
    prepared_event = {
        "id": "event-1",
        "event_type": "application.prepared",
        "payload": {"run_id": "run-1", "fit": {"overall_score": 8.0}, "gate_outcome": "proceed"},
    }
    supabase = _FakeSupabaseClient(
        application_events=_FakeTable(select_rows=[prepared_event]),
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}/prepare-result")

    assert response.status_code == 200
    assert response.json() == {"result": prepared_event["payload"]}


def test_get_prepare_result_returns_none_when_never_prepared() -> None:
    supabase = _FakeSupabaseClient(application_events=_FakeTable(select_rows=[]))
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}/prepare-result")

    assert response.status_code == 200
    assert response.json() == {"result": None}


# -- product events: one `prepare_finished` per real attempt ----------------------------------


def _prepare(
    supabase: _FakeSupabaseClient, http: _FakeHttpClient, **body: Any
) -> httpx.Response | Any:
    return _prepare_timed(supabase, http, **body)[0]


def _prepare_timed(
    supabase: _FakeSupabaseClient, http: _FakeHttpClient, **body: Any
) -> tuple[Any, float]:
    """The response, and how long the request itself took (not the app's startup and shutdown,
    which sit either side of it inside the `with`)."""
    with _client(supabase, http) as client:
        started = time.monotonic()
        response = client.post(
            f"/applications/{_APPLICATION_ID}/prepare", json=_prepare_body(**body)
        )
        return response, time.monotonic() - started


def _prepare_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r["event"] == "prepare_finished"]


def test_a_generated_resume_is_one_ok_event_naming_the_application(
    recorded_events: list[dict[str, Any]],
) -> None:
    response = _prepare(_FakeSupabaseClient(), _FakeHttpClient())

    assert response.status_code == 201
    (row,) = recorded_events
    assert {k: v for k, v in row.items() if k != "duration_ms"} == {
        "user_id": _USER_ID,
        "event": "prepare_finished",
        "application_id": _APPLICATION_ID,
        "outcome": "ok",
        "n_a": 0,  # no unsupported-claim warnings
    }
    assert isinstance(row["duration_ms"], int) and row["duration_ms"] >= 0
    assert "ats_type" not in row  # example.com is not an applicant-tracking system we know


def test_the_applicant_tracking_system_is_recorded_by_name_never_by_url(
    recorded_events: list[dict[str, Any]],
) -> None:
    snapshot = {**_SNAPSHOT_ROW, "source_url": "https://jobs.lever.co/acme/1234-secret-path?x=1"}

    _prepare(
        _FakeSupabaseClient(job_snapshots=_FakeTable(select_rows=[snapshot])), _FakeHttpClient()
    )

    (row,) = recorded_events
    assert row["ats_type"] == "lever"
    assert "acme" not in str(row) and "secret-path" not in str(row)


def test_the_number_of_unsupported_claim_warnings_is_recorded(
    recorded_events: list[dict[str, Any]],
) -> None:
    body = {
        **_FORGE_APPLY_RESPONSE_BODY,
        "claim_warnings": [
            'unsupported claim (contradicted): "Led 20 engineers" -- no match',
            'unsupported claim (unverifiable): "Built it in Rust" -- not in the source',
        ],
    }

    response = _prepare(_FakeSupabaseClient(), _FakeHttpClient(body=body))

    assert response.status_code == 201
    # the other warning (the gate's caution) is not a claim warning and is not counted
    assert len(response.json()["warnings"]) == 3
    assert recorded_events[0]["n_a"] == 2


def test_a_run_that_declined_to_write_a_resume_is_recorded_as_partial(
    recorded_events: list[dict[str, Any]],
) -> None:
    declined = {
        **_FORGE_APPLY_RESPONSE_BODY,
        "resume": None,
        "ats_attempts": [],
        "gate": {"outcome": "skip_low_score", "reason": "Fit score too low.", "cautions": []},
    }

    response = _prepare(_FakeSupabaseClient(), _FakeHttpClient(body=declined))

    assert response.status_code == 201
    assert _prepare_events(recorded_events)[0]["outcome"] == "partial"


def test_a_missing_profile_is_recorded_as_a_setup_required_attempt(
    recorded_events: list[dict[str, Any]],
) -> None:
    supabase = _FakeSupabaseClient(profile_versions=_FakeTable(select_rows=[]))

    response = _prepare(supabase, _FakeHttpClient())

    assert response.status_code == 409
    (row,) = recorded_events
    assert (row["event"], row["outcome"], row["capability"]) == (
        "prepare_finished",
        "setup_required",
        "profile",
    )
    assert row["application_id"] == _APPLICATION_ID


def test_an_engine_failure_is_recorded_as_failed_and_still_the_same_error(
    recorded_events: list[dict[str, Any]],
) -> None:
    response = _prepare(_FakeSupabaseClient(), _FakeHttpClient(status_code=503, body={}))

    assert response.status_code >= 500
    (row,) = recorded_events
    assert (row["event"], row["outcome"]) == ("prepare_finished", "failed")
    assert row["application_id"] == _APPLICATION_ID


def test_an_application_that_is_not_the_callers_records_nothing_at_all(
    recorded_events: list[dict[str, Any]],
) -> None:
    """A stranger's application id must not leave a row that names it, and a typo'd id is not
    an attempt: nothing is recorded until the application is known to be the caller's."""
    response = _prepare(
        _FakeSupabaseClient(applications=_FakeTable(select_rows=[])), _FakeHttpClient()
    )

    assert response.status_code == 404
    assert recorded_events == []


def test_a_replayed_request_records_nothing_a_second_time(
    recorded_events: list[dict[str, Any]],
) -> None:
    already_prepared = {
        "id": "event-1",
        "idempotency_key": _prepare_body()["idempotency_key"],
        "payload": {"run_id": "run-1", "resume": None, "final_score": None},
    }
    supabase = _FakeSupabaseClient(application_events=_FakeTable(select_rows=[already_prepared]))

    response = _prepare(supabase, _FakeHttpClient())

    assert response.status_code == 201
    assert recorded_events == []


def test_generating_answers_the_same_whether_or_not_the_event_can_be_recorded(
    monkeypatch: pytest.MonkeyPatch, recorded_events: list[dict[str, Any]]
) -> None:
    normal = _prepare(_FakeSupabaseClient(), _FakeHttpClient())

    def explode(*_: object) -> None:
        raise RuntimeError("the writer itself is broken")

    async def slow_database(*_: object) -> bool:
        await asyncio.sleep(1.0)
        return True

    for broken in (explode, slow_database):
        monkeypatch.setattr(product_events, "write_event", broken)
        response, seconds = _prepare_timed(_FakeSupabaseClient(), _FakeHttpClient())
        assert seconds < 0.5, "the request waited for the event to be written"
        assert response.status_code == normal.status_code == 201
        # the run id is fresh every time; everything else is the same
        assert {**response.json(), "run_id": None} == {**normal.json(), "run_id": None}


def test_a_failing_run_still_raises_its_own_error_when_the_event_cannot_be_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*_: object) -> None:
        raise RuntimeError("the writer itself is broken")

    monkeypatch.setattr(product_events, "write_event", explode)

    response = _prepare(
        _FakeSupabaseClient(profile_versions=_FakeTable(select_rows=[])), _FakeHttpClient()
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SETUP_REQUIRED"


# -- which engine wrote the document ----------------------------------------------------------


def test_the_stored_document_names_the_separate_engine_that_wrote_it() -> None:
    supabase = _FakeSupabaseClient()
    with_cover_letter = {
        **_FORGE_APPLY_RESPONSE_BODY,
        "cover_letter": {"latex": r"\documentclass{article}", "word_count": 42},
    }

    response = _prepare(
        supabase, _FakeHttpClient(body=with_cover_letter), generate_cover_letter=True
    )

    assert response.status_code == 201
    rows = supabase.artifact_versions.insert_calls
    assert [row["document_kind"] for row in rows] == ["resume", "cover_letter"]
    # Byte for byte what every document written through the separate engine has always carried.
    assert {(row["generator"], row["generator_version"]) for row in rows} == {
        ("forge-engines", "0.0.1")
    }


class _ScorelessEngine:
    """An engine that writes the resume and computes nothing else: no fit read, no ATS passes, no
    Honest Floor gate. Takes the built-in engine's place; its capabilities (and so the name it is
    stamped with) are the built-in engine's own."""

    capabilities = CAPABILITIES_BY_KIND["generic"]

    def __init__(self) -> None:
        self.calls = 0

    async def apply(self, http: Any, **kwargs: Any) -> ForgeApplyResult:
        self.calls += 1
        return ForgeApplyResult(
            resume={"latex": r"\begin{document}hi\end{document}"}, regenerated=False
        )


def test_a_resume_from_an_engine_that_scores_nothing_carries_no_score_gate_or_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing is made up where the engine said nothing: no fit read, no score, no verdict, and
    no caution that was never raised. The document is stamped with the engine that wrote it."""
    import between_jobs

    monkeypatch.delenv("FORGE_ENGINES_BASE_URL")
    engine = _ScorelessEngine()
    monkeypatch.setattr(engine_gateway, "_GENERIC", engine)
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()

    response = _prepare(supabase, http)

    assert response.status_code == 201
    body = response.json()
    assert body["resume"]["artifact_id"] == "artifact-1"
    assert body["fit"] is None
    assert body["ats_attempts"] == []
    assert body["final_score"] is None
    assert body["gate_outcome"] is None
    assert body["gate_reason"] == ""
    assert body["gate_cautions"] == []
    assert body["warnings"] == []
    assert engine.calls == 1
    assert http.post_calls == []  # the separate service was never asked
    (row,) = supabase.artifact_versions.insert_calls
    assert (row["generator"], row["generator_version"]) == (
        "between-jobs-builtin",
        between_jobs.__version__,
    )
    # the resume was delivered, so it is announced as one
    assert supabase.event_outbox.insert_calls[0]["event_type"] == "artifact.generated.v1"
    # and what was stored for the page that restores it after a reload says the same
    stored = supabase.application_events.insert_calls[0]["payload"]
    assert stored["fit"] is None and stored["final_score"] is None
    assert stored["gate_outcome"] is None


# -- the built-in engine, end to end through the real route ------------------------------------


def _use_the_built_in_engine(monkeypatch: pytest.MonkeyPatch, model: ScriptedModel) -> None:
    monkeypatch.delenv("FORGE_ENGINES_BASE_URL")
    monkeypatch.setattr(engine_gateway, "_GENERIC", GenericBackend(generate=model))


_FACTS = _FakeTable(
    select_rows=[
        {"id": "fact-exp-0", "source_pointer": "/experience/0"},
        {"id": "fact-exp-1", "source_pointer": "/experience/1"},
        {"id": "fact-exp-2", "source_pointer": "/experience/2"},
        {"id": "fact-proj-0", "source_pointer": "/projects/0"},
        {"id": "fact-proj-1", "source_pointer": "/projects/1"},
        {"id": "fact-edu-0", "source_pointer": "/education/0"},
        # facts the profile version has but the resume was not drawn from: the built-in engine
        # prints no publication or patent for this profile, so neither is evidence for it
        {"id": "fact-pub-0", "source_pointer": "/publications/0"},
        {"id": "fact-pat-0", "source_pointer": "/patents/0"},
    ]
)


def _supabase_with_a_real_profile() -> _FakeSupabaseClient:
    row = {**_PROFILE_VERSION_ROW, "canonical_json": sample_profile()}
    posting = {**_SNAPSHOT_ROW, **sample_snapshot()}
    return _FakeSupabaseClient(
        profile_versions=_FakeTable(select_rows=[row]),
        job_snapshots=_FakeTable(select_rows=[{**posting, "id": _SNAPSHOT_ID}]),
        career_facts=_FACTS,
    )


def test_a_server_without_the_hosted_engine_writes_a_resume_and_a_cover_letter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the built-in engine: no separate service, no private engine, only a profile,
    a job and a model key, and the result is a stored resume and cover letter that the rest of
    the app (the PDF endpoints, the checklist, the web app) already knows how to use."""
    import between_jobs

    model = ScriptedModel.faithful()
    _use_the_built_in_engine(monkeypatch, model)
    supabase = _supabase_with_a_real_profile()
    http = _FakeHttpClient()

    response = _prepare(supabase, http, generate_cover_letter=True)

    assert response.status_code == 201
    body = response.json()
    result = PrepareApplicationResult.model_validate(body)  # the public result contract
    assert result.resume is not None and result.cover_letter is not None
    # nothing was scored and nothing is made up in its place
    assert result.final_score is None and result.fit is None and result.ats_attempts == []
    assert result.gate_outcome is None and result.gate_cautions == []
    assert body["warnings"] == []
    # the documents were stored under the built-in engine's name, with LaTeX the PDF renderer takes
    rows = supabase.artifact_versions.insert_calls
    assert [row["document_kind"] for row in rows] == ["resume", "cover_letter"]
    assert {(row["generator"], row["generator_version"]) for row in rows} == {
        ("between-jobs-builtin", between_jobs.__version__)
    }
    resume_tex, cover_tex = (upload[1].decode() for upload in supabase.bucket.uploads)
    assert resume_tex.startswith(r"\documentclass") and "Northwind Labs" in resume_tex
    assert cover_tex.startswith(r"\documentclass") and "Globex Corporation" in cover_tex
    # nothing went to a separate service, and the model was asked exactly as often as planned
    # (the summary is off unless the person turned it on)
    assert http.post_calls == []
    assert sorted(call.stage for call in model.calls) == ["body", "cover", "step0"]
    assert supabase.event_outbox.insert_calls[0]["event_type"] == "artifact.generated.v1"


def test_the_stored_resume_says_which_profile_entries_it_was_drawn_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_the_built_in_engine(monkeypatch, ScriptedModel.faithful())
    supabase = _supabase_with_a_real_profile()

    response = _prepare(supabase, _FakeHttpClient(), generate_cover_letter=True)

    expected = [
        "fact-edu-0",
        "fact-exp-0",
        "fact-exp-1",
        "fact-exp-2",
        "fact-proj-0",
        "fact-proj-1",
    ]
    resume_row, cover_row = supabase.artifact_versions.insert_calls
    assert sorted(resume_row["evidence_fact_ids"]) == expected
    assert sorted(response.json()["evidence_fact_ids"]) == expected
    # only the entries the engine reported: a fact of the same profile version it did not use is
    # not recorded as provenance the document does not have
    for unused in ("fact-pub-0", "fact-pat-0"):
        assert unused not in resume_row["evidence_fact_ids"]
        assert unused not in response.json()["evidence_fact_ids"]
    # the letter is checked against the whole profile, not drawn from entries: nothing claimed
    assert cover_row["evidence_fact_ids"] == []


def test_what_the_built_in_engine_replaced_reaches_the_checklist_as_an_unsupported_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pypdf import PdfWriter

    from between_jobs.api.export_checklist import build_checklist
    from between_jobs.engines import claims_were_verified_by

    def invent(user: str) -> str:
        entries = json.loads(echo_body(user))
        entries["entries"][0]["bullets"][0]["text"] = "Cut p95 API latency 45% across 12 services"
        return json.dumps(entries)

    _use_the_built_in_engine(monkeypatch, ScriptedModel.faithful(body=[invent, invent]))
    supabase = _supabase_with_a_real_profile()

    response = _prepare(supabase, _FakeHttpClient())

    assert response.status_code == 201
    flagged = [w for w in response.json()["warnings"] if w.startswith("unsupported claim")]
    assert len(flagged) == 2 and all('"45%"' in w for w in flagged)
    (row,) = supabase.artifact_versions.insert_calls
    assert row["warnings"] == response.json()["warnings"]
    # ... and the checklist, reading the stored warnings, fails the item that names them
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    buffer = io.BytesIO()
    writer.write(buffer)
    checklist = build_checklist(
        buffer.getvalue(),
        row["warnings"],
        row["shape_report"],
        claims_verified=claims_were_verified_by(row["generator"]),
    )
    item = next(i for i in checklist if i["key"] == "no_unsupported_claims")
    assert item["status"] == "fail" and "45%" in item["detail"]


def test_the_built_in_engines_shape_report_is_stored_for_the_checklist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_the_built_in_engine(monkeypatch, ScriptedModel.faithful())
    supabase = _supabase_with_a_real_profile()

    _prepare(supabase, _FakeHttpClient())

    (row,) = supabase.artifact_versions.insert_calls
    assert row["shape_report"] == {"target_pages": 1, "pins_honored": True, "warnings": []}


def test_a_provider_failure_while_the_built_in_engine_writes_stores_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    down = ApiError("PROVIDER_UNAVAILABLE", "down", retryable=True)
    _use_the_built_in_engine(monkeypatch, ScriptedModel.faithful(step0=[down]))
    supabase = _supabase_with_a_real_profile()

    response = _prepare(supabase, _FakeHttpClient())

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "PROVIDER_UNAVAILABLE"
    assert supabase.bucket.uploads == [] and supabase.artifact_versions.insert_calls == []
    assert supabase.application_events.insert_calls == []


class _NoLookupTable(_FakeTable):
    """A facts table that fails the test if anything reads it."""

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        raise AssertionError("the profile's facts were looked up for an engine that reported none")


def test_an_engine_that_reports_no_evidence_costs_no_lookup_of_the_profiles_facts() -> None:
    """The separate service reports none: the stored list stays empty, and the facts table is
    never read for it."""
    supabase = _FakeSupabaseClient(career_facts=_NoLookupTable(select_rows=[]))

    response = _prepare(supabase, _FakeHttpClient())

    assert response.status_code == 201
    assert supabase.artifact_versions.insert_calls[0]["evidence_fact_ids"] == []


async def test_the_evidence_is_the_reported_pointers_in_the_engines_order_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from between_jobs.api import prepare_orchestrator

    async def facts(*_args: Any) -> list[dict[str, str]]:
        return [
            {"id": "f-exp-0", "source_pointer": "/experience/0"},
            {"id": "f-exp-1", "source_pointer": "/experience/1"},
            {"id": "f-pub-0", "source_pointer": "/publications/0"},
        ]

    monkeypatch.setattr(prepare_orchestrator, "list_career_facts", facts)

    ids = await prepare_orchestrator._evidence_fact_ids(
        None,  # type: ignore[arg-type]
        "user",
        "version",
        ["/experience/1", "/experience/0", "/experience/9"],
    )

    # the engine's order, the unknown pointer dropped, the publication left out
    assert ids == ["f-exp-1", "f-exp-0"]
    assert await prepare_orchestrator._evidence_fact_ids(None, "u", "v", []) == []  # type: ignore[arg-type]
