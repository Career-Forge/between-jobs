"""Tests for the jobs/applications HTTP endpoints (Sprint 2.6f).

Exercises the real FastAPI routes via TestClient, same shape as
test_profile_routes.py -- this is what proves request parsing, dependency
wiring, and error-code mapping actually work end to end, not just the
underlying store functions in isolation (covered in test_jobs_store.py /
test_applications_store.py).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from between_jobs.api.app import app
from between_jobs.api.app_state import get_http_client, get_supabase
from between_jobs.api.artifact_versions_store import artifact_id_for
from between_jobs.api.auth import require_user_id

_USER_ID = "00000000-0000-0000-0000-000000000001"
_JOB_ID = "20000000-0000-0000-0000-000000000001"
_SNAPSHOT_ID = "20000000-0000-0000-0000-000000000002"
_APPLICATION_ID = "30000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def order(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def limit(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def in_(self, *_: Any, **__: Any) -> _ChainBuilder:
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
        rows = [self.insert_row] if self.insert_row is not None else []
        return _ChainBuilder(rows)


class _RealFilterQuery:
    """Unlike _ChainBuilder above (whose .eq() is a no-op -- several
    existing tests in this file rely on that, seeding fixture rows that
    deliberately omit fields they're not testing), this one genuinely
    filters. Used only for tests proving a real .eq() column/value
    actually matters -- an adversarial review caught that the shared
    no-op fake gave false confidence that lookup_registry_posting's own
    apply_url filter was being exercised at all."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, column: str, value: Any) -> _RealFilterQuery:
        return _RealFilterQuery([row for row in self._rows if row.get(column) == value])

    def order(self, *_: Any, **__: Any) -> _RealFilterQuery:
        return self

    def limit(self, *_: Any, **__: Any) -> _RealFilterQuery:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _RealFilterTable:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def select(self, *_: Any, **__: Any) -> _RealFilterQuery:
        return _RealFilterQuery(self._rows)


class _FakeRpcBuilder:
    def __init__(self, data: Any, error: APIError | None) -> None:
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
        jobs: _FakeTable | None = None,
        job_snapshots: _FakeTable | None = None,
        applications: _FakeTable | None = None,
        application_events: _FakeTable | None = None,
        artifact_versions: _FakeTable | None = None,
        event_outbox: _FakeTable | None = None,
        job_registry_postings: _FakeTable | None = None,
        job_registry_companies: _FakeTable | None = None,
        provider_credentials: _FakeTable | None = None,
        rpc_data: Any = None,
        rpc_error: APIError | None = None,
    ) -> None:
        self.jobs = jobs or _FakeTable(select_rows=[])
        self.job_snapshots = job_snapshots or _FakeTable(select_rows=[])
        self.applications = applications or _FakeTable(select_rows=[])
        self.application_events = application_events or _FakeTable(select_rows=[])
        self.artifact_versions = artifact_versions or _FakeTable(select_rows=[])
        self.event_outbox = event_outbox or _FakeTable(select_rows=[])
        self.job_registry_postings = job_registry_postings or _FakeTable(select_rows=[])
        self.job_registry_companies = job_registry_companies or _FakeTable(select_rows=[])
        self.provider_credentials = provider_credentials or _FakeTable(select_rows=[])
        self.rpc_data = rpc_data
        self.rpc_error = rpc_error

    def table(self, name: str) -> Any:
        return {
            "jobs": self.jobs,
            "job_snapshots": self.job_snapshots,
            "applications": self.applications,
            "application_events": self.application_events,
            "artifact_versions": self.artifact_versions,
            "event_outbox": self.event_outbox,
            "job_registry_postings": self.job_registry_postings,
            "job_registry_companies": self.job_registry_companies,
            "provider_credentials": self.provider_credentials,
        }[name]

    def rpc(self, fn: str, params: dict[str, Any]) -> _FakeRpcBuilder:
        if fn == "decrypt_secret":
            return _FakeRpcBuilder("sk-firecrawl-real-secret", None)
        return _FakeRpcBuilder(self.rpc_data, self.rpc_error)


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")


class _FakeHttpClient:
    def __init__(self, *, status_code: int = 200, body: Any = None) -> None:
        self.status_code = status_code
        self.body = body if body is not None else {"data": {"markdown": "", "metadata": {}}}
        self.post_calls: list[tuple[str, dict[str, Any]]] = []

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.post_calls.append((url, kwargs))
        return httpx.Response(
            status_code=self.status_code, json=self.body, request=httpx.Request("POST", url)
        )


def _client(supabase: _FakeSupabaseClient, http: _FakeHttpClient | None = None) -> TestClient:
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    if http is not None:
        app.dependency_overrides[get_http_client] = lambda: http
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Any:
    yield
    app.dependency_overrides.clear()


def _paste_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "title": "Staff Engineer",
        "company_name": "Acme",
        "description_text": "Build things.",
        "canonical_url": "https://acme.example/jobs/1",
        "location_text": "Remote",
    }
    body.update(overrides)
    return body


def test_create_application_from_paste_success() -> None:
    new_job = {"id": _JOB_ID, "canonical_url": "https://acme.example/jobs/1"}
    new_snapshot = {"id": _SNAPSHOT_ID, "job_id": _JOB_ID, "title": "Staff Engineer"}
    new_app = {"id": _APPLICATION_ID, "user_id": _USER_ID, "status": "saved"}
    supabase = _FakeSupabaseClient(
        jobs=_FakeTable(select_rows=[], insert_row=new_job),
        job_snapshots=_FakeTable(select_rows=[], insert_row=new_snapshot),
        applications=_FakeTable(select_rows=[], insert_row=new_app),
        application_events=_FakeTable(
            select_rows=[], insert_row={"id": "event-1", "event_type": "application.created"}
        ),
    )
    with _client(supabase) as client:
        response = client.post("/applications", json=_paste_body())

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == _APPLICATION_ID
    assert body["snapshot"]["id"] == _SNAPSHOT_ID


def test_create_application_from_paste_missing_field_returns_structured_422() -> None:
    supabase = _FakeSupabaseClient()
    with _client(supabase) as client:
        response = client.post("/applications", json=_paste_body(company_name=""))

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "INVALID_INPUT"
    assert body["details"]["errors"]


def test_create_application_from_url_registry_hit_uses_real_jd_text() -> None:
    """outreach-v2-search-first.md Phase J -- a posting already in this
    platform's own job registry needs zero scrape calls. Uses the real-
    filter double specifically so this test can't pass on a broken
    apply_url filter (a decoy row for a DIFFERENT url is seeded
    alongside the real match -- the no-op shared fake would return both
    regardless of which URL was actually posted)."""
    posting_row = {
        "apply_url": "https://acme.example/jobs/1",
        "title": "Software Engineer",
        "location": "Remote",
        "jd_text": "A real, full job description from the registry.",
        "company_id": "company-1",
    }
    decoy_row = {
        "apply_url": "https://other.example/jobs/999",
        "title": "Wrong Job",
        "location": "Nowhere",
        "jd_text": "This must never be returned for the URL under test.",
        "company_id": "company-2",
    }
    company_row = {"name": "Acme"}
    new_job = {"id": _JOB_ID, "canonical_url": "https://acme.example/jobs/1"}
    new_snapshot = {"id": _SNAPSHOT_ID, "job_id": _JOB_ID, "title": "Software Engineer"}
    new_app = {"id": _APPLICATION_ID, "user_id": _USER_ID, "status": "saved"}
    supabase = _FakeSupabaseClient(
        job_registry_postings=_RealFilterTable([posting_row, decoy_row]),  # type: ignore[arg-type]
        job_registry_companies=_FakeTable(select_rows=[company_row]),
        jobs=_FakeTable(select_rows=[], insert_row=new_job),
        job_snapshots=_FakeTable(select_rows=[], insert_row=new_snapshot),
        applications=_FakeTable(select_rows=[], insert_row=new_app),
        application_events=_FakeTable(select_rows=[], insert_row={"id": "event-1"}),
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(
            "/applications/from-url", json={"url": "https://acme.example/jobs/1"}
        )

    assert response.status_code == 201
    assert response.json()["snapshot"]["id"] == _SNAPSHOT_ID
    inserted_snapshot = supabase.job_snapshots.insert_calls[0]
    assert inserted_snapshot["description_text"] == (
        "A real, full job description from the registry."
    )
    assert inserted_snapshot["company_name"] == "Acme"
    assert inserted_snapshot["source_kind"] == "url_ingest"
    # Zero scrape calls -- the registry already had it.
    assert http.post_calls == []


def test_create_application_from_url_falls_back_to_scrape_on_an_empty_registry_row() -> None:
    """A real, disclosed registry data-quality state (P1's own seed-
    import history): a row exists for this URL but carries no usable
    jd_text. Must fall through to a real scrape rather than persist a
    permanent placeholder when the identical URL would have gotten real
    content had it simply missed the registry."""
    empty_posting_row = {
        "apply_url": "https://acme.example/jobs/1",
        "title": None,
        "location": None,
        "jd_text": "",
        "company_id": None,
    }
    new_job = {"id": _JOB_ID, "canonical_url": "https://acme.example/jobs/1"}
    new_snapshot = {"id": _SNAPSHOT_ID, "job_id": _JOB_ID}
    new_app = {"id": _APPLICATION_ID, "user_id": _USER_ID, "status": "saved"}
    supabase = _FakeSupabaseClient(
        job_registry_postings=_FakeTable(select_rows=[empty_posting_row]),
        provider_credentials=_FakeTable(
            select_rows=[
                {
                    "provider": "firecrawl",
                    "model": None,
                    "base_url": None,
                    "secret_encrypted": "cipher",
                    "secret_2_encrypted": None,
                }
            ]
        ),
        jobs=_FakeTable(select_rows=[], insert_row=new_job),
        job_snapshots=_FakeTable(select_rows=[], insert_row=new_snapshot),
        applications=_FakeTable(select_rows=[], insert_row=new_app),
        application_events=_FakeTable(select_rows=[], insert_row={"id": "event-1"}),
    )
    http = _FakeHttpClient(body={"data": {"markdown": "Real scraped content.", "metadata": {}}})

    with _client(supabase, http) as client:
        response = client.post(
            "/applications/from-url", json={"url": "https://acme.example/jobs/1"}
        )

    assert response.status_code == 201
    assert len(http.post_calls) == 1
    inserted_snapshot = supabase.job_snapshots.insert_calls[0]
    assert inserted_snapshot["description_text"] == "Real scraped content."
    assert inserted_snapshot["title"] == "Untitled Position"
    assert inserted_snapshot["company_name"] == "Acme"


def test_create_application_from_url_scrapes_when_not_in_the_registry() -> None:
    new_job = {"id": _JOB_ID, "canonical_url": "https://boards.greenhouse.io/acme/jobs/1"}
    new_snapshot = {"id": _SNAPSHOT_ID, "job_id": _JOB_ID}
    new_app = {"id": _APPLICATION_ID, "user_id": _USER_ID, "status": "saved"}
    supabase = _FakeSupabaseClient(
        job_registry_postings=_FakeTable(select_rows=[]),
        provider_credentials=_FakeTable(
            select_rows=[
                {
                    "provider": "firecrawl",
                    "model": None,
                    "base_url": None,
                    "secret_encrypted": "cipher",
                    "secret_2_encrypted": None,
                }
            ]
        ),
        jobs=_FakeTable(select_rows=[], insert_row=new_job),
        job_snapshots=_FakeTable(select_rows=[], insert_row=new_snapshot),
        applications=_FakeTable(select_rows=[], insert_row=new_app),
        application_events=_FakeTable(select_rows=[], insert_row={"id": "event-1"}),
    )
    http = _FakeHttpClient(
        body={
            "data": {
                "markdown": "# Software Engineer\n\nReal scraped content.",
                "metadata": {"title": "Software Engineer - Acme"},
            }
        }
    )

    with _client(supabase, http) as client:
        response = client.post(
            "/applications/from-url",
            json={"url": "https://job-boards.greenhouse.io/acme/jobs/1"},
        )

    assert response.status_code == 201
    assert len(http.post_calls) == 1
    scrape_url, scrape_kwargs = http.post_calls[0]
    assert scrape_url == "https://api.firecrawl.dev/v2/scrape"
    # Proves the REAL posted url reached Firecrawl, not a stale/wrong
    # variable from a different code path.
    assert scrape_kwargs["json"]["url"] == "https://job-boards.greenhouse.io/acme/jobs/1"
    inserted_snapshot = supabase.job_snapshots.insert_calls[0]
    assert inserted_snapshot["description_text"] == "# Software Engineer\n\nReal scraped content."
    assert inserted_snapshot["title"] == "Software Engineer - Acme"
    assert inserted_snapshot["company_name"] == "Acme"  # guessed from the path slug
    assert inserted_snapshot["source_kind"] == "url_ingest"


def test_create_application_from_url_falls_back_to_untitled_when_scrape_has_no_title() -> None:
    new_job = {"id": _JOB_ID, "canonical_url": "https://job-boards.greenhouse.io/acme/jobs/1"}
    new_snapshot = {"id": _SNAPSHOT_ID, "job_id": _JOB_ID}
    new_app = {"id": _APPLICATION_ID, "user_id": _USER_ID, "status": "saved"}
    supabase = _FakeSupabaseClient(
        job_registry_postings=_FakeTable(select_rows=[]),
        provider_credentials=_FakeTable(
            select_rows=[
                {
                    "provider": "firecrawl",
                    "model": None,
                    "base_url": None,
                    "secret_encrypted": "cipher",
                    "secret_2_encrypted": None,
                }
            ]
        ),
        jobs=_FakeTable(select_rows=[], insert_row=new_job),
        job_snapshots=_FakeTable(select_rows=[], insert_row=new_snapshot),
        applications=_FakeTable(select_rows=[], insert_row=new_app),
        application_events=_FakeTable(select_rows=[], insert_row={"id": "event-1"}),
    )
    http = _FakeHttpClient(body={"data": {"markdown": "Real content.", "metadata": {}}})

    with _client(supabase, http) as client:
        response = client.post(
            "/applications/from-url",
            json={"url": "https://job-boards.greenhouse.io/acme/jobs/1"},
        )

    assert response.status_code == 201
    assert supabase.job_snapshots.insert_calls[0]["title"] == "Untitled Position"


def test_create_application_from_url_empty_url_returns_structured_422() -> None:
    supabase = _FakeSupabaseClient()
    with _client(supabase) as client:
        response = client.post("/applications/from-url", json={"url": ""})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_create_application_from_url_resolves_the_firecrawl_credential_specifically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for the same "no-op .eq()" false-confidence class:
    proves the route resolves service="search"/provider="firecrawl"
    specifically, by spying on try_get_secret directly rather than
    trusting the fake table's own filtering (which doesn't filter)."""
    new_job = {"id": _JOB_ID, "canonical_url": "https://job-boards.greenhouse.io/acme/jobs/1"}
    new_snapshot = {"id": _SNAPSHOT_ID, "job_id": _JOB_ID}
    new_app = {"id": _APPLICATION_ID, "user_id": _USER_ID, "status": "saved"}
    supabase = _FakeSupabaseClient(
        job_registry_postings=_FakeTable(select_rows=[]),
        jobs=_FakeTable(select_rows=[], insert_row=new_job),
        job_snapshots=_FakeTable(select_rows=[], insert_row=new_snapshot),
        applications=_FakeTable(select_rows=[], insert_row=new_app),
        application_events=_FakeTable(select_rows=[], insert_row={"id": "event-1"}),
    )
    http = _FakeHttpClient(body={"data": {"markdown": "Real content.", "metadata": {}}})

    calls: list[tuple[str, str, str]] = []

    async def fake_try_get_secret(
        _supabase_arg: Any, user_id_arg: str, *, service: str, provider: str
    ) -> str | None:
        calls.append((user_id_arg, service, provider))
        return "fc-real-key"

    monkeypatch.setattr("between_jobs.api.applications_routes.try_get_secret", fake_try_get_secret)

    with _client(supabase, http) as client:
        response = client.post(
            "/applications/from-url",
            json={"url": "https://job-boards.greenhouse.io/acme/jobs/1"},
        )

    assert response.status_code == 201
    assert calls == [(_USER_ID, "search", "firecrawl")]


def test_create_application_from_url_refuses_a_denied_host() -> None:
    supabase = _FakeSupabaseClient(job_registry_postings=_FakeTable(select_rows=[]))
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(
            "/applications/from-url",
            json={"url": "https://www.linkedin.com/jobs/view/12345"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"
    assert http.post_calls == []


def test_create_application_from_url_no_firecrawl_key_returns_setup_required() -> None:
    supabase = _FakeSupabaseClient(
        job_registry_postings=_FakeTable(select_rows=[]),
        provider_credentials=_FakeTable(select_rows=[]),
    )
    http = _FakeHttpClient()

    with _client(supabase, http) as client:
        response = client.post(
            "/applications/from-url",
            json={"url": "https://job-boards.greenhouse.io/acme/jobs/1"},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SETUP_REQUIRED"
    assert http.post_calls == []


def test_create_application_from_url_empty_scrape_returns_invalid_input() -> None:
    supabase = _FakeSupabaseClient(
        job_registry_postings=_FakeTable(select_rows=[]),
        provider_credentials=_FakeTable(
            select_rows=[
                {
                    "provider": "firecrawl",
                    "model": None,
                    "base_url": None,
                    "secret_encrypted": "cipher",
                    "secret_2_encrypted": None,
                }
            ]
        ),
    )
    http = _FakeHttpClient(body={"data": {"markdown": "   ", "metadata": {}}})

    with _client(supabase, http) as client:
        response = client.post(
            "/applications/from-url",
            json={"url": "https://job-boards.greenhouse.io/acme/jobs/1"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_list_my_applications_embeds_snapshot() -> None:
    app_row = {"id": _APPLICATION_ID, "active_job_snapshot_id": _SNAPSHOT_ID}
    snapshot_row = {"id": _SNAPSHOT_ID, "title": "Staff Engineer"}
    supabase = _FakeSupabaseClient(
        applications=_FakeTable(select_rows=[app_row]),
        job_snapshots=_FakeTable(select_rows=[snapshot_row]),
    )
    with _client(supabase) as client:
        response = client.get("/applications")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["snapshot"]["title"] == "Staff Engineer"


def test_list_my_applications_includes_resume_exists_true_when_generated() -> None:
    """K1 (applications-kanban.md D5) -- the real "Resume ✓" badge, batch-
    computed for the whole list."""
    app_row = {"id": _APPLICATION_ID, "active_job_snapshot_id": _SNAPSHOT_ID}
    snapshot_row = {"id": _SNAPSHOT_ID, "title": "Staff Engineer"}
    resume_artifact_id = artifact_id_for(_APPLICATION_ID, "resume")
    supabase = _FakeSupabaseClient(
        applications=_FakeTable(select_rows=[app_row]),
        job_snapshots=_FakeTable(select_rows=[snapshot_row]),
        artifact_versions=_FakeTable(select_rows=[{"artifact_id": resume_artifact_id}]),
    )
    with _client(supabase) as client:
        response = client.get("/applications")

    assert response.json()[0]["resume_exists"] is True


def test_list_my_applications_includes_resume_exists_false_when_none_generated() -> None:
    app_row = {"id": _APPLICATION_ID, "active_job_snapshot_id": _SNAPSHOT_ID}
    snapshot_row = {"id": _SNAPSHOT_ID, "title": "Staff Engineer"}
    supabase = _FakeSupabaseClient(
        applications=_FakeTable(select_rows=[app_row]),
        job_snapshots=_FakeTable(select_rows=[snapshot_row]),
        artifact_versions=_FakeTable(select_rows=[]),
    )
    with _client(supabase) as client:
        response = client.get("/applications")

    assert response.json()[0]["resume_exists"] is False


def test_list_my_applications_empty() -> None:
    supabase = _FakeSupabaseClient(applications=_FakeTable(select_rows=[]))
    with _client(supabase) as client:
        response = client.get("/applications")

    assert response.status_code == 200
    assert response.json() == []


def test_get_my_application_embeds_snapshot() -> None:
    app_row = {"id": _APPLICATION_ID, "active_job_snapshot_id": _SNAPSHOT_ID}
    snapshot_row = {"id": _SNAPSHOT_ID, "title": "Staff Engineer"}
    supabase = _FakeSupabaseClient(
        applications=_FakeTable(select_rows=[app_row]),
        job_snapshots=_FakeTable(select_rows=[snapshot_row]),
    )
    with _client(supabase) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == _APPLICATION_ID
    assert body["snapshot"]["title"] == "Staff Engineer"
    assert body["resume_exists"] is False


def test_get_my_application_reports_resume_exists_when_an_artifact_was_generated() -> None:
    app_row = {"id": _APPLICATION_ID, "active_job_snapshot_id": _SNAPSHOT_ID}
    snapshot_row = {"id": _SNAPSHOT_ID, "title": "Staff Engineer"}
    supabase = _FakeSupabaseClient(
        applications=_FakeTable(select_rows=[app_row]),
        job_snapshots=_FakeTable(select_rows=[snapshot_row]),
        artifact_versions=_FakeTable(select_rows=[{"id": "version-row-1", "version": 1}]),
    )
    with _client(supabase) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}")

    assert response.json()["resume_exists"] is True


def test_get_my_application_not_found_returns_404() -> None:
    supabase = _FakeSupabaseClient(applications=_FakeTable(select_rows=[]))
    with _client(supabase) as client:
        response = client.get(f"/applications/{_APPLICATION_ID}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_change_application_stage_success() -> None:
    updated = {"id": _APPLICATION_ID, "status": "applied"}
    supabase = _FakeSupabaseClient(rpc_data=updated)
    with _client(supabase) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/stage",
            json={"new_status": "applied", "idempotency_key": "change-1"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "applied"


def test_change_application_stage_invalid_status_returns_422() -> None:
    """K1 (applications-kanban.md D1/D2) -- `new_status` is now a Literal
    of the 7 enforced values; an unrecognized string 422s via FastAPI's
    own Pydantic validation before the route body ever runs."""
    supabase = _FakeSupabaseClient()
    with _client(supabase) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/stage",
            json={"new_status": "bogus", "idempotency_key": "change-1"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_change_application_stage_not_found_returns_404() -> None:
    error = APIError({"message": "application x not found for user y", "code": "P0001"})
    supabase = _FakeSupabaseClient(rpc_error=error)
    with _client(supabase) as client:
        response = client.post(
            f"/applications/{_APPLICATION_ID}/stage",
            json={"new_status": "applied", "idempotency_key": "change-1"},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
