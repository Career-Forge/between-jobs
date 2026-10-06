"""Tests for GET /applications/{id}/resume.pdf and .../export-checklist
(Sprint 3.3f).

Same TestClient + dependency-override convention as
test_prepare_application_route.py. The outbound call to latex-service is
faked at the httpx client boundary -- never a real pdflatex invocation in
a unit test; that's covered by latex-service's own test suite and by this
sprint's live verification.
"""

from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from between_jobs.api import product_events
from between_jobs.api.app import app
from between_jobs.api.app_state import get_http_client, get_supabase
from between_jobs.api.auth import require_user_id

_USER_ID = "00000000-0000-0000-0000-000000000001"
_APPLICATION_ID = "30000000-0000-0000-0000-000000000001"

_APPLICATION_ROW = {"id": _APPLICATION_ID, "user_id": _USER_ID}
_LATEX_SOURCE = r"\begin{document}hello\end{document}"


def _one_page_pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def order(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def limit(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTable:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self._rows)


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


class _FakeSupabaseClient:
    def __init__(
        self,
        *,
        applications: list[dict[str, Any]] | None = None,
        artifact_versions: list[dict[str, Any]] | None = None,
        storage_bytes: bytes = _LATEX_SOURCE.encode("utf-8"),
    ) -> None:
        self._applications = _FakeTable(
            applications if applications is not None else [_APPLICATION_ROW]
        )
        self._artifact_versions = _FakeTable(
            artifact_versions
            if artifact_versions is not None
            else [
                {
                    "id": "version-row-1",
                    "version": 1,
                    "storage_key": f"{_USER_ID}/artifact-1/1",
                    "warnings": [],
                }
            ]
        )
        self.storage = _FakeStorage(_FakeBucket(download_bytes=storage_bytes))

    def table(self, name: str) -> Any:
        return {"applications": self._applications, "artifact_versions": self._artifact_versions}[
            name
        ]


class _FakeHttpClient:
    def __init__(self, *, status_code: int = 200, pdf_bytes: bytes | None = None) -> None:
        self.status_code = status_code
        self.pdf_bytes = pdf_bytes if pdf_bytes is not None else _one_page_pdf_bytes()
        self.post_calls: list[tuple[str, dict[str, Any]]] = []

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.post_calls.append((url, kwargs))
        if self.status_code == 200:
            return httpx.Response(
                status_code=200, content=self.pdf_bytes, request=httpx.Request("POST", url)
            )
        return httpx.Response(
            status_code=self.status_code,
            json={"error": "CompileError", "message": "boom"},
            request=httpx.Request("POST", url),
        )


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


def _client(supabase: _FakeSupabaseClient, http: _FakeHttpClient) -> TestClient:
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    app.dependency_overrides[get_http_client] = lambda: http
    return TestClient(app)


def test_download_resume_pdf_returns_the_compiled_pdf() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}/resume.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert response.content == http.pdf_bytes
    assert len(http.post_calls) == 1
    assert http.post_calls[0][1]["json"] == {"latex": _LATEX_SOURCE}


def test_download_resume_pdf_404s_when_no_application() -> None:
    supabase = _FakeSupabaseClient(applications=[])
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}/resume.pdf")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_download_resume_pdf_404s_when_nothing_generated_yet() -> None:
    supabase = _FakeSupabaseClient(artifact_versions=[])
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}/resume.pdf")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert http.post_calls == []


def test_download_resume_pdf_maps_a_compile_failure_to_run_failed() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient(status_code=422)

    with _client(supabase, http) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}/resume.pdf")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "RUN_FAILED"


def test_download_cover_letter_pdf_returns_the_compiled_pdf() -> None:
    # C1/C2 (coverforge-port.md): same shape as resume.pdf, own route.
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}/cover-letter.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert "cover-letter.pdf" in response.headers["content-disposition"]
    assert response.content == http.pdf_bytes


def test_download_cover_letter_pdf_404s_when_nothing_generated_yet() -> None:
    supabase = _FakeSupabaseClient(artifact_versions=[])
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}/cover-letter.pdf")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert http.post_calls == []


def test_export_checklist_reports_pass_for_a_clean_one_page_resume() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}/export-checklist")

    assert response.status_code == 200
    items = {item["key"]: item for item in response.json()["items"]}
    assert items["one_page"]["status"] == "pass"
    assert items["no_engine_warnings"]["status"] == "pass"
    assert items["links_valid"]["status"] == "not_checked"


def test_export_checklist_surfaces_stored_warnings_as_a_failed_check() -> None:
    supabase = _FakeSupabaseClient(
        artifact_versions=[
            {
                "id": "version-row-1",
                "version": 1,
                "storage_key": f"{_USER_ID}/artifact-1/1",
                "warnings": ["Borderline seniority match."],
            }
        ]
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}/export-checklist")

    items = {item["key"]: item for item in response.json()["items"]}
    assert items["no_engine_warnings"]["status"] == "fail"
    assert "Borderline seniority match." in items["no_engine_warnings"]["detail"]


def test_export_checklist_404s_when_nothing_generated_yet() -> None:
    supabase = _FakeSupabaseClient(artifact_versions=[])
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}/export-checklist")

    assert response.status_code == 404


# -- product events: one `artifact_downloaded` per successful download ------------------------


def test_downloading_a_resume_is_one_event_naming_the_application_and_the_document(
    recorded_events: list[dict[str, Any]],
) -> None:
    with _client(_FakeSupabaseClient(), _FakeHttpClient()) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}/resume.pdf")

    assert response.status_code == 200
    assert recorded_events == [
        {
            "user_id": _USER_ID,
            "event": "artifact_downloaded",
            "application_id": _APPLICATION_ID,
            "outcome": "ok",
            "n_a": 1,  # 1 = resume
        }
    ]


def test_downloading_a_cover_letter_is_recorded_as_the_other_document(
    recorded_events: list[dict[str, Any]],
) -> None:
    with _client(_FakeSupabaseClient(), _FakeHttpClient()) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}/cover-letter.pdf")

    assert response.status_code == 200
    assert [(r["event"], r["n_a"]) for r in recorded_events] == [("artifact_downloaded", 2)]


@pytest.mark.parametrize(
    "supabase",
    [
        _FakeSupabaseClient(applications=[]),  # not the caller's application
        _FakeSupabaseClient(artifact_versions=[]),  # nothing generated yet
    ],
    ids=["no application", "nothing generated"],
)
def test_a_download_that_does_not_happen_records_nothing(
    recorded_events: list[dict[str, Any]], supabase: _FakeSupabaseClient
) -> None:
    with _client(supabase, _FakeHttpClient()) as client:
        resume = client.get(f"/applications/{_APPLICATION_ID}/resume.pdf")
        cover_letter = client.get(f"/applications/{_APPLICATION_ID}/cover-letter.pdf")

    assert (resume.status_code, cover_letter.status_code) == (404, 404)
    assert recorded_events == []


def test_a_failed_compile_records_nothing(recorded_events: list[dict[str, Any]]) -> None:
    with _client(_FakeSupabaseClient(), _FakeHttpClient(status_code=422)) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}/resume.pdf")

    assert response.status_code == 500
    assert recorded_events == []


def test_the_checklist_is_not_a_download(recorded_events: list[dict[str, Any]]) -> None:
    """It compiles the same PDF, but nothing leaves the server."""
    with _client(_FakeSupabaseClient(), _FakeHttpClient()) as client:
        assert client.get(f"/applications/{_APPLICATION_ID}/export-checklist").status_code == 200

    assert recorded_events == []


def test_a_download_is_the_same_whether_or_not_the_event_can_be_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*_: object) -> None:
        raise RuntimeError("the writer itself is broken")

    monkeypatch.setattr(product_events, "write_event", explode)
    http = _FakeHttpClient()

    with _client(_FakeSupabaseClient(), http) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}/resume.pdf")

    assert response.status_code == 200
    assert response.content == http.pdf_bytes
    assert response.headers["content-type"] == "application/pdf"
