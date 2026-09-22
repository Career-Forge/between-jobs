"""Tests for the browser-extension HTTP endpoints (browser-extension.md
E1, plus E3b's /draft-answer, plus E6's D6 gate/rate limit/sign-out).
Exercises the real FastAPI routes via TestClient, same convention as
test_saved_searches_routes.py.

/draft-answer's LLM calls are faked by monkeypatching
`between_jobs.api.extension_routes.llm_generate` -- the route imports its
OWN `generate` reference and passes it explicitly to
`generate_answer`/`verify_answer_claims` (the same R3-bug-informed seam
test_interview_practice_routes.py's own docstring already explains), so
patching application_answer_generator.py's internal default would have
no effect here.

Every route in extension_routes.py depends on
`require_active_extension_user_id` (extension_auth.py), not plain
`require_user_id` (E6 continuation) -- `_client()` below overrides that
dependency directly, same pattern as overriding `get_supabase`.
`tests/test_extension_auth.py` covers the dependency's own real
signature/iat/sign-out logic end to end; these tests stay focused on
each route's business behavior with auth already granted."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from between_jobs.api.app import app
from between_jobs.api.app_state import get_http_client, get_supabase
from between_jobs.api.artifact_versions_store import artifact_id_for
from between_jobs.api.auth import require_user_id
from between_jobs.api.extension_auth import require_active_extension_user_id
from between_jobs.api.llm_client import LLMResponse

_USER_ID = "00000000-0000-0000-0000-000000000001"
_OTHER_USER_ID = "00000000-0000-0000-0000-000000000002"
_APPLICATION_ID = "30000000-0000-0000-0000-000000000001"

_PREFERENCE_ROW = {
    "user_id": _USER_ID,
    "capability": "default",
    "execution_mode": "byok_first_party",
    "provider": "openrouter",
    "model": "anthropic/claude-sonnet-4-6",
}
_CREDENTIAL_ROW = {
    "user_id": _USER_ID,
    "service": "llm",
    "provider": "openrouter",
    "model": "anthropic/claude-sonnet-4-6",
    "base_url": None,
    "scope": None,
    "secret_encrypted": "ciphertext-abc",
    "secret_2_encrypted": None,
}
_PROFILE_ROW = {
    "user_id": _USER_ID,
    "activated_at": "2026-08-01T00:00:00Z",
    "canonical_json": {"personal": {"name": "Jane Doe"}},
}


def _apply_or(rows: list[dict[str, Any]], expr: str) -> list[dict[str, Any]]:
    clauses = expr.split(",")

    def matches(row: dict[str, Any]) -> bool:
        for clause in clauses:
            column, op, value = clause.split(".", 2)
            if op == "is" and value == "null" and row.get(column) is None:
                return True
            if op == "gt":
                current = row.get(column)
                if current is not None and current > value:
                    return True
        return False

    return [r for r in rows if matches(r)]


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, column: str, value: Any) -> _ChainBuilder:
        return _ChainBuilder([r for r in self._rows if r.get(column) == value])

    def in_(self, column: str, values: list[Any]) -> _ChainBuilder:
        return _ChainBuilder([r for r in self._rows if r.get(column) in values])

    def or_(self, expr: str) -> _ChainBuilder:
        return _ChainBuilder(_apply_or(self._rows, expr))

    @property
    def not_(self) -> _ChainBuilder:
        return self

    def is_(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def order(self, *_: Any, **__: Any) -> _ChainBuilder:
        rows = sorted(self._rows, key=lambda r: r.get("updated_at", ""), reverse=True)
        return _ChainBuilder(rows)

    def limit(self, _n: int) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTable:
    def __init__(self, *, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows if rows is not None else []
        self._next_id = 1

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.rows)

    def upsert(self, data: dict[str, Any], *, on_conflict: str) -> _ChainBuilder:
        conflict_cols = on_conflict.split(",")
        existing = next(
            (r for r in self.rows if all(r.get(c) == data.get(c) for c in conflict_cols)),
            None,
        )
        if existing is not None:
            existing.update(data)
            return _ChainBuilder([existing])
        row = {"id": f"row-{self._next_id}", "times_used": 0, **data}
        self._next_id += 1
        self.rows.append(row)
        return _ChainBuilder([row])


class _FakeBucket:
    def __init__(self, *, download_bytes: bytes) -> None:
        self._download_bytes = download_bytes

    async def download(self, _path: str) -> bytes:
        return self._download_bytes


class _FakeStorage:
    def __init__(self, bucket: _FakeBucket) -> None:
        self._bucket = bucket

    def from_(self, _bucket_id: str) -> _FakeBucket:
        return self._bucket


class _FakeSupabase:
    def __init__(
        self,
        *,
        applications: list[dict[str, Any]] | None = None,
        jobs: list[dict[str, Any]] | None = None,
        job_snapshots: list[dict[str, Any]] | None = None,
        approved_answers: list[dict[str, Any]] | None = None,
        profile_versions: list[dict[str, Any]] | None = None,
        capability_preferences: list[dict[str, Any]] | None = None,
        provider_credentials: list[dict[str, Any]] | None = None,
        ats_field_maps: list[dict[str, Any]] | None = None,
        extension_sign_outs: list[dict[str, Any]] | None = None,
        # E6 continuation, part 2 -- for the extension's own resume.pdf/
        # cover-letter.pdf mirror routes, same shapes as
        # test_resume_export_routes.py's own fake (that file already
        # covers latest_resume_pdf/latest_cover_letter_pdf's own compile
        # logic in full; these tests only need enough of a fixture to
        # prove the extension-scoped routes call through to it).
        artifact_versions: list[dict[str, Any]] | None = None,
        storage_bytes: bytes = b"\\begin{document}hello\\end{document}",
        rate_limit_allows: bool = True,
    ) -> None:
        self._tables = {
            "applications": _FakeTable(rows=applications),
            "jobs": _FakeTable(rows=jobs),
            "job_snapshots": _FakeTable(rows=job_snapshots),
            "approved_answers": _FakeTable(rows=approved_answers),
            "profile_versions": _FakeTable(rows=profile_versions),
            "capability_preferences": _FakeTable(rows=capability_preferences),
            "provider_credentials": _FakeTable(rows=provider_credentials),
            "ats_field_maps": _FakeTable(rows=ats_field_maps),
            "extension_sign_outs": _FakeTable(rows=extension_sign_outs),
            # No fabricated default row: artifact_id is derived deterministically
            # via artifact_id_for(application_id, document_kind), which differs
            # between "resume" and "cover_letter" -- a single made-up default
            # row could never legitimately match both, so each test below
            # builds its own row with the real derivation.
            "artifact_versions": _FakeTable(
                rows=artifact_versions if artifact_versions is not None else []
            ),
        }
        self.storage = _FakeStorage(_FakeBucket(download_bytes=storage_bytes))
        # extension_rate_limit.claim_draft_answer_slot's own RPC -- True by
        # default (never blocks a test that isn't specifically about rate
        # limiting), overridable per-test via `rate_limit_allows=False`.
        self.rpc_calls: list[tuple[str, dict[str, Any]]] = []
        self._rate_limit_allows = rate_limit_allows

    def table(self, name: str) -> _FakeTable:
        return self._tables[name]

    async def _rpc_execute(self, fn: str, params: dict[str, Any]) -> SimpleNamespace:
        self.rpc_calls.append((fn, params))
        if fn == "decrypt_secret":
            return SimpleNamespace(data="sk-real-secret-not-real")
        if fn == "claim_extension_draft_answer_slot":
            return SimpleNamespace(data=self._rate_limit_allows)
        raise AssertionError(f"unexpected rpc: {fn}")

    def rpc(self, fn: str, params: dict[str, Any]) -> Any:
        return SimpleNamespace(execute=lambda: self._rpc_execute(fn, params))


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")


@pytest.fixture(autouse=True)
def _clear_overrides() -> Any:
    yield
    app.dependency_overrides.clear()


def _client(supabase: _FakeSupabase) -> TestClient:
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[require_active_extension_user_id] = lambda: _USER_ID
    return TestClient(app)


class _FakeHttpClient:
    """The latex-service call `latest_resume_pdf`/`latest_cover_letter_pdf`
    make -- same shape as test_resume_export_routes.py's own fake, which
    already covers the compile logic in full; only needed here so the
    extension-scoped mirror routes have something to call through to."""

    def __init__(self, *, pdf_bytes: bytes | None = None) -> None:
        self.pdf_bytes = pdf_bytes if pdf_bytes is not None else _one_page_pdf_bytes()

    async def post(self, url: str, **_: Any) -> httpx.Response:
        return httpx.Response(
            status_code=200, content=self.pdf_bytes, request=httpx.Request("POST", url)
        )


def _one_page_pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _client_with_http(supabase: _FakeSupabase, http: _FakeHttpClient) -> TestClient:
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[require_active_extension_user_id] = lambda: _USER_ID
    app.dependency_overrides[get_http_client] = lambda: http
    return TestClient(app)


def test_lookup_finds_a_tracked_application_by_url() -> None:
    supabase = _FakeSupabase(
        applications=[
            {
                "id": "app-1",
                "user_id": _USER_ID,
                "job_id": "job-1",
                "active_job_snapshot_id": "snap-1",
            }
        ],
        jobs=[{"id": "job-1", "canonical_url": None}],
        job_snapshots=[{"id": "snap-1", "source_url": "https://jobs.lever.co/acme/1"}],
    )
    client = _client(supabase)

    response = client.get("/extension/lookup", params={"url": "https://jobs.lever.co/acme/1"})

    assert response.status_code == 200
    assert response.json() == {"application_id": "app-1"}


def test_lookup_returns_null_when_no_application_matches() -> None:
    supabase = _FakeSupabase(applications=[])
    client = _client(supabase)

    response = client.get("/extension/lookup", params={"url": "https://jobs.lever.co/acme/1"})

    assert response.status_code == 200
    assert response.json() == {"application_id": None}


def test_lookup_422s_on_an_empty_url() -> None:
    """Defense-in-depth companion to the store-level empty-url guard --
    rejected at the request boundary before it ever reaches matching
    logic."""
    supabase = _FakeSupabase()
    client = _client(supabase)

    response = client.get("/extension/lookup", params={"url": ""})

    assert response.status_code == 422


def test_lookup_never_matches_another_user_s_application() -> None:
    supabase = _FakeSupabase(
        applications=[
            {
                "id": "app-1",
                "user_id": _OTHER_USER_ID,
                "job_id": "job-1",
                "active_job_snapshot_id": "snap-1",
            }
        ],
        jobs=[{"id": "job-1", "canonical_url": None}],
        job_snapshots=[{"id": "snap-1", "source_url": "https://jobs.lever.co/acme/1"}],
    )
    client = _client(supabase)

    response = client.get("/extension/lookup", params={"url": "https://jobs.lever.co/acme/1"})

    assert response.json() == {"application_id": None}


def test_match_answer_returns_an_exact_match() -> None:
    supabase = _FakeSupabase(
        approved_answers=[
            {
                "id": "ans-1",
                "user_id": _USER_ID,
                "normalized_question": "are you willing to relocate",
                "canonical_intent": "willing_to_relocate",
                "answer_text": "Yes",
                "expires_at": None,
                "updated_at": "2026-09-01T00:00:00Z",
            }
        ]
    )
    client = _client(supabase)

    response = client.post(
        "/extension/match-answer", json={"normalized_question": "are you willing to relocate"}
    )

    assert response.status_code == 200
    assert response.json()["answer"]["answer_text"] == "Yes"


def test_match_answer_returns_null_when_nothing_matches() -> None:
    supabase = _FakeSupabase(approved_answers=[])
    client = _client(supabase)

    response = client.post(
        "/extension/match-answer", json={"normalized_question": "are you willing to relocate"}
    )

    assert response.status_code == 200
    assert response.json()["answer"] is None


def test_match_answer_accepts_a_normalized_question_at_exactly_the_max_length() -> None:
    supabase = _FakeSupabase(approved_answers=[])
    client = _client(supabase)

    response = client.post("/extension/match-answer", json={"normalized_question": "x" * 500})

    assert response.status_code == 200


def test_match_answer_422s_one_character_over_the_max_length() -> None:
    """The new `max_length` request caps reject over-length input rather
    than silently truncating it -- proven at the boundary, not just
    trusted from reading the Field(...) declaration."""
    supabase = _FakeSupabase()
    client = _client(supabase)

    response = client.post("/extension/match-answer", json={"normalized_question": "x" * 501})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_save_answer_creates_a_new_row() -> None:
    supabase = _FakeSupabase(approved_answers=[])
    client = _client(supabase)

    response = client.post(
        "/extension/approved-answers",
        json={
            "normalized_question": "are you willing to relocate",
            "answer_text": "Yes",
            "canonical_intent": "willing_to_relocate",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["answer_text"] == "Yes"
    assert body["user_id"] == _USER_ID


def test_save_answer_upserts_on_repeat_question() -> None:
    supabase = _FakeSupabase(
        approved_answers=[
            {
                "id": "ans-1",
                "user_id": _USER_ID,
                "normalized_question": "are you willing to relocate",
                "answer_text": "No",
            }
        ]
    )
    client = _client(supabase)

    response = client.post(
        "/extension/approved-answers",
        json={"normalized_question": "are you willing to relocate", "answer_text": "Yes"},
    )

    assert response.status_code == 201
    assert response.json()["answer_text"] == "Yes"
    assert len(supabase.table("approved_answers").rows) == 1


def test_save_answer_accepts_an_answer_text_at_exactly_the_max_length() -> None:
    supabase = _FakeSupabase(approved_answers=[])
    client = _client(supabase)

    response = client.post(
        "/extension/approved-answers",
        json={"normalized_question": "are you willing to relocate", "answer_text": "x" * 2000},
    )

    assert response.status_code == 201


def test_save_answer_422s_one_character_over_the_max_length() -> None:
    supabase = _FakeSupabase(approved_answers=[])
    client = _client(supabase)

    response = client.post(
        "/extension/approved-answers",
        json={"normalized_question": "are you willing to relocate", "answer_text": "x" * 2001},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def _draft_answer_supabase(**overrides: Any) -> _FakeSupabase:
    defaults: dict[str, Any] = {
        "applications": [
            {
                "id": "app-1",
                "user_id": _USER_ID,
                "job_id": "job-1",
                "active_job_snapshot_id": "snap-1",
            }
        ],
        "job_snapshots": [{"id": "snap-1", "description_text": "We build ML systems in Python."}],
        "profile_versions": [_PROFILE_ROW],
        "capability_preferences": [_PREFERENCE_ROW],
        "provider_credentials": [_CREDENTIAL_ROW],
    }
    defaults.update(overrides)
    return _FakeSupabase(**defaults)


def _fake_generate_and_verify(
    *,
    generated_answer: str | None,
    declined_reason: str | None = None,
    claims: list[dict[str, Any]] | None = None,
) -> Any:
    async def fake_generate(*, system_prompt: str, **_kwargs: Any) -> LLMResponse:
        if "claim-verification judge" in system_prompt:
            return LLMResponse(content=json.dumps({"claims": claims or []}))
        return LLMResponse(
            content=json.dumps(
                {"answer_text": generated_answer, "declined_reason": declined_reason}
            )
        )

    return fake_generate


def test_draft_answer_skips_the_llm_entirely_for_a_disclaimer_length_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_if_called(**_kwargs: Any) -> LLMResponse:
        raise AssertionError("the LLM must never be called for an ineligible question")

    monkeypatch.setattr("between_jobs.api.extension_routes.llm_generate", fail_if_called)
    supabase = _draft_answer_supabase()
    client = _client(supabase)

    response = client.post(
        "/extension/draft-answer",
        json={"application_id": "app-1", "question_text": "x" * 500},
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {"eligible": False, "answer_text": None, "declined_reason": None, "warnings": []}


def test_draft_answer_accepts_a_question_text_at_exactly_the_max_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`question_text`'s `max_length` (2000) is well above the 400-char
    `is_generation_eligible` threshold on purpose (this route's own
    docstring) -- a 2000-char question reaches that deterministic
    eligibility check and gets a clean `eligible: False`, not a 422, so
    this is a genuinely different boundary than the disclaimer-length test
    right above it."""

    async def fail_if_called(**_kwargs: Any) -> LLMResponse:
        raise AssertionError("the LLM must never be called for an ineligible question")

    monkeypatch.setattr("between_jobs.api.extension_routes.llm_generate", fail_if_called)
    supabase = _draft_answer_supabase()
    client = _client(supabase)

    response = client.post(
        "/extension/draft-answer",
        json={"application_id": "app-1", "question_text": "x" * 2000},
    )

    assert response.status_code == 200
    assert response.json()["eligible"] is False


def test_draft_answer_422s_one_character_over_the_max_length() -> None:
    supabase = _draft_answer_supabase()
    client = _client(supabase)

    response = client.post(
        "/extension/draft-answer",
        json={"application_id": "app-1", "question_text": "x" * 2001},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_draft_answer_generates_and_flags_only_non_grounded_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "between_jobs.api.extension_routes.llm_generate",
        _fake_generate_and_verify(
            generated_answer="I led a team of 5 and invented a new framework.",
            claims=[
                {"claim": "led a team of 5", "verdict": "grounded", "reason": "matches"},
                {
                    "claim": "invented a new framework",
                    "verdict": "contradicted",
                    "reason": "not in facts",
                },
            ],
        ),
    )
    supabase = _draft_answer_supabase()
    client = _client(supabase)

    response = client.post(
        "/extension/draft-answer",
        json={"application_id": "app-1", "question_text": "Tell me about your experience."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["eligible"] is True
    assert body["answer_text"] == "I led a team of 5 and invented a new framework."
    assert body["declined_reason"] is None
    assert len(body["warnings"]) == 1
    assert "invented a new framework" in body["warnings"][0]


def test_draft_answer_returns_a_decline_without_a_verify_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_generate(*, system_prompt: str, **_kwargs: Any) -> LLMResponse:
        assert "claim-verification judge" not in system_prompt, "must not verify a declined answer"
        return LLMResponse(
            content=json.dumps(
                {
                    "answer_text": None,
                    "declined_reason": "This is a consent statement, not a question.",
                }
            )
        )

    monkeypatch.setattr("between_jobs.api.extension_routes.llm_generate", fake_generate)
    supabase = _draft_answer_supabase()
    client = _client(supabase)

    response = client.post(
        "/extension/draft-answer",
        json={"application_id": "app-1", "question_text": "Please acknowledge the policy above."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["eligible"] is True
    assert body["answer_text"] is None
    assert body["declined_reason"] == "This is a consent statement, not a question."


def test_draft_answer_404s_for_a_missing_application(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_if_called(**_kwargs: Any) -> LLMResponse:
        raise AssertionError("the LLM must never be called when the application isn't found")

    monkeypatch.setattr("between_jobs.api.extension_routes.llm_generate", fail_if_called)
    supabase = _draft_answer_supabase(applications=[])
    client = _client(supabase)

    response = client.post(
        "/extension/draft-answer",
        json={"application_id": "app-1", "question_text": "Why do you want to work here?"},
    )

    assert response.status_code == 404


def test_draft_answer_requires_an_active_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_if_called(**_kwargs: Any) -> LLMResponse:
        raise AssertionError(
            "the LLM must never be called with no active profile to ground against"
        )

    monkeypatch.setattr("between_jobs.api.extension_routes.llm_generate", fail_if_called)
    supabase = _draft_answer_supabase(profile_versions=[])
    client = _client(supabase)

    response = client.post(
        "/extension/draft-answer",
        json={"application_id": "app-1", "question_text": "Why do you want to work here?"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SETUP_REQUIRED"


def test_draft_answer_errors_cleanly_when_the_job_snapshot_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_if_called(**_kwargs: Any) -> LLMResponse:
        raise AssertionError("the LLM must never be called when the job snapshot can't be read")

    monkeypatch.setattr("between_jobs.api.extension_routes.llm_generate", fail_if_called)
    supabase = _draft_answer_supabase(job_snapshots=[])
    client = _client(supabase)

    response = client.post(
        "/extension/draft-answer",
        json={"application_id": "app-1", "question_text": "Why do you want to work here?"},
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"


def test_draft_answer_fails_open_when_the_verify_call_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_generate(*, system_prompt: str, **_kwargs: Any) -> LLMResponse:
        if "claim-verification judge" in system_prompt:
            raise RuntimeError("provider outage")
        return LLMResponse(
            content=json.dumps({"answer_text": "I led a team of 5.", "declined_reason": None})
        )

    monkeypatch.setattr("between_jobs.api.extension_routes.llm_generate", fake_generate)
    supabase = _draft_answer_supabase()
    client = _client(supabase)

    response = client.post(
        "/extension/draft-answer",
        json={"application_id": "app-1", "question_text": "Tell me about your experience."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["eligible"] is True
    assert body["answer_text"] == "I led a team of 5."
    assert body["warnings"] == []


_LEVER_FIELD_MAP_ROW = {
    "ats_type": "lever",
    "version": 1,
    "schema": "ats-field-map/v1",
    "payload_canonical": '{"ats_type":"lever","schema":"ats-field-map/v1","version":1}',
    "signature_b64": "c2ln",
    "signing_key_id": "test-key-2026-09",
}


def test_get_field_map_returns_the_published_map() -> None:
    supabase = _FakeSupabase(ats_field_maps=[_LEVER_FIELD_MAP_ROW])
    client = _client(supabase)

    response = client.get("/extension/field-maps/lever")

    assert response.status_code == 200
    assert response.json() == {
        "ats_type": "lever",
        "version": 1,
        "schema": "ats-field-map/v1",
        "payload_canonical": _LEVER_FIELD_MAP_ROW["payload_canonical"],
        "signature_b64": "c2ln",
        "signing_key_id": "test-key-2026-09",
    }


def test_get_field_map_404s_when_nothing_is_published_for_that_ats_type() -> None:
    supabase = _FakeSupabase(ats_field_maps=[])
    client = _client(supabase)

    response = client.get("/extension/field-maps/lever")

    assert response.status_code == 404


def test_get_field_map_only_returns_the_requested_ats_type() -> None:
    supabase = _FakeSupabase(ats_field_maps=[{**_LEVER_FIELD_MAP_ROW, "ats_type": "greenhouse"}])
    client = _client(supabase)

    response = client.get("/extension/field-maps/lever")

    assert response.status_code == 404


# --- E6: the server-side D6 gate on /draft-answer ---------------------------


def test_draft_answer_declines_a_d6_question_without_calling_the_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A modified client (or a direct API call bypassing the extension's
    own client-side check entirely) sends a self-ID question's raw label
    text as `question_text` -- the server-side gate must decline it the
    same way an ineligible question is declined, with no LLM call at
    all, not a 422."""

    async def fail_if_called(**_kwargs: Any) -> LLMResponse:
        raise AssertionError("the LLM must never be called for a D6-class question")

    monkeypatch.setattr("between_jobs.api.extension_routes.llm_generate", fail_if_called)
    supabase = _draft_answer_supabase()
    client = _client(supabase)

    response = client.post(
        "/extension/draft-answer",
        json={"application_id": "app-1", "question_text": "What is your gender identity?"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {"eligible": False, "answer_text": None, "declined_reason": None, "warnings": []}


def test_draft_answer_still_drafts_a_work_authorization_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The maintainer's own pinned boundary (questionSafety.ts's
    MUST_STAY_VISIBLE cases): work-authorization/visa/sponsorship
    questions are NOT D6 self-ID and must still reach the LLM and be
    drafted normally."""
    monkeypatch.setattr(
        "between_jobs.api.extension_routes.llm_generate",
        _fake_generate_and_verify(generated_answer="I am authorized to work in the US."),
    )
    supabase = _draft_answer_supabase()
    client = _client(supabase)

    response = client.post(
        "/extension/draft-answer",
        json={
            "application_id": "app-1",
            "question_text": "Will you now or in the future require visa sponsorship?",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["eligible"] is True
    assert body["answer_text"] == "I am authorized to work in the US."


# --- E6: per-user rate limiting on /draft-answer ----------------------------


def test_draft_answer_429s_when_the_rate_limit_is_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_if_called(**_kwargs: Any) -> LLMResponse:
        raise AssertionError("the LLM must never be called once the caller is rate-limited")

    monkeypatch.setattr("between_jobs.api.extension_routes.llm_generate", fail_if_called)
    supabase = _draft_answer_supabase(rate_limit_allows=False)
    client = _client(supabase)

    response = client.post(
        "/extension/draft-answer",
        json={"application_id": "app-1", "question_text": "Tell me about your experience."},
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "PROVIDER_RATE_LIMITED"
    assert response.json()["error"]["retryable"] is True


def test_draft_answer_never_claims_a_rate_limit_slot_for_a_declined_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A question that never reaches the LLM (ineligible or D6) must not
    count against the caller's own rate-limit budget -- no cost was
    incurred, so nothing should be spent."""

    async def fail_if_called(**_kwargs: Any) -> LLMResponse:
        raise AssertionError("the LLM must never be called for a D6-class question")

    monkeypatch.setattr("between_jobs.api.extension_routes.llm_generate", fail_if_called)
    supabase = _draft_answer_supabase()
    client = _client(supabase)

    response = client.post(
        "/extension/draft-answer",
        json={"application_id": "app-1", "question_text": "What is your race or ethnicity?"},
    )

    assert response.status_code == 200
    assert not any(fn == "claim_extension_draft_answer_slot" for fn, _params in supabase.rpc_calls)


# --- E6: POST /extension/sign-out --------------------------------------------


def test_sign_out_records_a_signed_out_at_row() -> None:
    supabase = _FakeSupabase(extension_sign_outs=[])
    client = _client(supabase)

    response = client.post("/extension/sign-out")

    assert response.status_code == 204
    rows = supabase.table("extension_sign_outs").rows
    assert len(rows) == 1
    assert rows[0]["user_id"] == _USER_ID
    assert rows[0]["signed_out_at"] is not None


def test_sign_out_upserts_rather_than_duplicating() -> None:
    supabase = _FakeSupabase(
        extension_sign_outs=[{"user_id": _USER_ID, "signed_out_at": "2026-01-01T00:00:00+00:00"}]
    )
    client = _client(supabase)

    response = client.post("/extension/sign-out")

    assert response.status_code == 204
    rows = supabase.table("extension_sign_outs").rows
    assert len(rows) == 1
    assert rows[0]["signed_out_at"] != "2026-01-01T00:00:00+00:00"


# E6 continuation, part 2 -- the extension's own resume.pdf/cover-letter.pdf
# mirror routes (live-verification found the original web-app routes stayed
# on require_user_id, so a signed-out-of-the-extension token could still read
# them; these two routes close that gap). The underlying compile logic
# (latest_resume_pdf/latest_cover_letter_pdf) is already fully covered by
# test_resume_export_routes.py -- these tests only prove the extension-scoped
# route calls through to it AND is gated by the right dependency.


def _application_row() -> dict[str, Any]:
    return {"id": _APPLICATION_ID, "user_id": _USER_ID, "job_id": "job-1"}


def _artifact_version_row(document_kind: str) -> dict[str, Any]:
    return {
        "id": "version-row-1",
        "user_id": _USER_ID,
        "artifact_id": artifact_id_for(_APPLICATION_ID, document_kind),
        "version": 1,
        "storage_key": f"{_USER_ID}/{document_kind}/1",
        "warnings": [],
    }


def test_extension_resume_pdf_returns_the_compiled_pdf() -> None:
    supabase = _FakeSupabase(
        applications=[_application_row()], artifact_versions=[_artifact_version_row("resume")]
    )
    http = _FakeHttpClient()

    with _client_with_http(supabase, http) as client:
        response = client.get(f"/extension/{_APPLICATION_ID}/resume.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content == http.pdf_bytes


def test_extension_cover_letter_pdf_returns_the_compiled_pdf() -> None:
    supabase = _FakeSupabase(
        applications=[_application_row()],
        artifact_versions=[_artifact_version_row("cover_letter")],
    )
    http = _FakeHttpClient()

    with _client_with_http(supabase, http) as client:
        response = client.get(f"/extension/{_APPLICATION_ID}/cover-letter.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "cover-letter.pdf" in response.headers["content-disposition"]


def test_extension_resume_pdf_404s_when_no_application() -> None:
    supabase = _FakeSupabase(applications=[])
    http = _FakeHttpClient()

    with _client_with_http(supabase, http) as client:
        response = client.get(f"/extension/{_APPLICATION_ID}/resume.pdf")

    assert response.status_code == 404


def test_extension_pdf_routes_are_gated_by_the_extensions_own_sign_out_aware_dependency() -> None:
    """The whole point of moving these routes: only `require_user_id` is
    overridden here (not `require_active_extension_user_id`), so a request
    with no Authorization header must hit the real extension dependency's
    own rejection -- if either route were still on `require_user_id` (the
    bug this fix closes), the override below would make it succeed and this
    test would catch the regression immediately, the same way
    test_extension_payload_is_gated_by_the_extensions_own_sign_out_aware_
    dependency does in test_applications_routes.py for the sibling route."""
    supabase = _FakeSupabase()
    http = _FakeHttpClient()
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    app.dependency_overrides[get_http_client] = lambda: http

    with TestClient(app) as client:
        resume_response = client.get(f"/extension/{_APPLICATION_ID}/resume.pdf")
        cover_letter_response = client.get(f"/extension/{_APPLICATION_ID}/cover-letter.pdf")

    for response in (resume_response, cover_letter_response):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_REQUIRED"
