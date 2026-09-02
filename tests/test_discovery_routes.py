"""Tests for the discovery HTTP endpoints (Job Finder P8, job-finder-
port.md's own build order) -- the full native search pipeline route.

Exercises the real FastAPI routes via TestClient, same convention as
test_company_intel_routes.py: outbound calls are faked at the httpx
client boundary (get_http_client's override) and the Supabase table/RPC
boundary (get_supabase's override); the LLM call is faked via the
route's own explicit `generate=llm_generate` injection point. No real
network call, no real spend, in any test here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from between_jobs.api.app import app
from between_jobs.api.app_state import get_http_client, get_supabase
from between_jobs.api.auth import require_user_id
from between_jobs.api.discovery_routes import _result_to_card
from between_jobs.api.job_fit_scoring import ScoredJob, SubScores
from between_jobs.api.llm_client import LLMResponse
from between_jobs.api.search_providers import SearchResult

_USER_ID = "00000000-0000-0000-0000-000000000001"

_PROFILE_ROW = {
    "canonical_json": {"personal": {"name": "Jane Doe", "headline": "Backend Engineer"}}
}
_PREFERENCE_ROW = {
    "capability": "default",
    "execution_mode": "byok_first_party",
    "provider": "openrouter",
    "model": "anthropic/claude-sonnet-4-6",
}
_LLM_CREDENTIAL_ROW = {
    "provider": "openrouter",
    "model": "anthropic/claude-sonnet-4-6",
    "base_url": None,
    "secret_encrypted": "llm-cipher",
    "secret_2_encrypted": None,
}
_YOU_COM_CREDENTIAL_ROW = {
    "provider": "you_com",
    "model": None,
    "base_url": None,
    "secret_encrypted": "you-com-cipher",
    "secret_2_encrypted": None,
}
_REGISTRY_POSTING_ROW = {
    "title": "Senior Backend Engineer",
    "company_name": "Acme",
    "location": "Remote",
    "remote": True,
    "apply_url": "https://example.com/careers/job-1",
    "snippet": "Build backend systems in Python.",
    "posted_at": "2026-08-01T00:00:00Z",
    "salary_min": None,
    "salary_max": None,
    "salary_currency": None,
    "sponsorship_signal": "unknown",
}


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def order(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    @property
    def not_(self) -> _ChainBuilder:
        return self

    def is_(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def limit(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTable:
    def __init__(
        self, *, select_rows: list[dict[str, Any]], insert_row: dict[str, Any] | None = None
    ) -> None:
        self.select_rows = select_rows
        self.insert_row = insert_row
        self._next_id = 1

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def insert(self, data: Any) -> _ChainBuilder:
        if isinstance(data, list):
            rows = []
            for row in data:
                rows.append({"id": f"row-{self._next_id}", **row})
                self._next_id += 1
        else:
            rows = [self.insert_row] if self.insert_row is not None else [{**data, "id": "row-1"}]
        self.select_rows.extend(rows)
        return _ChainBuilder(rows)


class _CredentialTable:
    """Keyed by (service, provider) -- same reasoning as test_company_
    intel_routes.py's own identically-named class: this route resolves
    MANY distinct credentials in one request (LLM + up to 7 search
    providers)."""

    def __init__(self, rows: dict[tuple[str, str], dict[str, Any]]) -> None:
        self._rows = rows
        self._service = ""
        self._provider = ""

    def select(self, *_: Any, **__: Any) -> _CredentialTable:
        return self

    def eq(self, column: str, value: Any) -> _CredentialTable:
        if column == "service":
            self._service = value
        if column == "provider":
            self._provider = value
        return self

    async def execute(self) -> SimpleNamespace:
        row = self._rows.get((self._service, self._provider))
        return SimpleNamespace(data=[row] if row else [])


class _PaginatedTable:
    """`.select().range().execute()` -- matches `company_tiers.get_
    company_tier_index`'s own real query chain (no `.eq()` at all)."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self._start = 0
        self._end = len(rows)

    def select(self, *_: Any, **__: Any) -> _PaginatedTable:
        return self

    def range(self, start: int, end: int) -> _PaginatedTable:
        self._start = start
        self._end = end
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows[self._start : self._end + 1])


class _FakeRpcBuilder:
    def __init__(self, data: Any) -> None:
        self._data = data

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._data)


class _FakeSupabaseClient:
    def __init__(
        self,
        *,
        profile_versions: list[dict[str, Any]] | None = None,
        capability_preferences: list[dict[str, Any]] | None = None,
        provider_credentials: dict[tuple[str, str], dict[str, Any]] | None = None,
        registry_postings: list[dict[str, Any]] | None = None,
        registry_posting_details: dict[str, dict[str, Any]] | None = None,
        registry_companies: dict[str, str] | None = None,
        jobs: _FakeTable | None = None,
        job_snapshots: _FakeTable | None = None,
        applications: _FakeTable | None = None,
    ) -> None:
        self.profile_versions = _FakeTable(
            select_rows=profile_versions if profile_versions is not None else [_PROFILE_ROW]
        )
        self.capability_preferences = _FakeTable(
            select_rows=capability_preferences
            if capability_preferences is not None
            else [_PREFERENCE_ROW]
        )
        self._credential_table = _CredentialTable(
            provider_credentials
            if provider_credentials is not None
            else {("llm", "openrouter"): _LLM_CREDENTIAL_ROW}
        )
        self._company_tiers = _PaginatedTable([])
        self._registry_postings = registry_postings if registry_postings is not None else []
        self._registry_posting_details = registry_posting_details or {}
        self._registry_companies = registry_companies or {}
        self.jobs = jobs or _FakeTable(select_rows=[])
        self.job_snapshots = job_snapshots or _FakeTable(select_rows=[])
        self.applications = applications or _FakeTable(select_rows=[])
        self.application_events = _FakeTable(select_rows=[])
        self.event_outbox = _FakeTable(select_rows=[])

    def table(self, name: str) -> Any:
        if name == "job_registry_postings":
            return _RegistryPostingsTable(self._registry_posting_details)
        if name == "job_registry_companies":
            return _RegistryCompaniesTable(self._registry_companies)
        return {
            "profile_versions": self.profile_versions,
            "capability_preferences": self.capability_preferences,
            "provider_credentials": self._credential_table,
            "company_tiers": self._company_tiers,
            "jobs": self.jobs,
            "job_snapshots": self.job_snapshots,
            "applications": self.applications,
            "application_events": self.application_events,
            "event_outbox": self.event_outbox,
        }[name]

    def rpc(self, fn: str, params: dict[str, Any]) -> _FakeRpcBuilder:
        if fn == "decrypt_secret":
            return _FakeRpcBuilder("decrypted-secret")
        if fn == "search_job_registry_postings":
            return _FakeRpcBuilder(self._registry_postings)
        raise AssertionError(f"unexpected rpc: {fn}")


class _RegistryPostingsTable:
    """`.select("title, location, jd_text, company_id").eq("apply_url",
    ...).limit(1).execute()` -- the route's own registry posting-details
    lookup at track-time (title/location/jd_text/company, Phase 2g)."""

    def __init__(self, details_by_url: dict[str, dict[str, Any]]) -> None:
        self._details_by_url = details_by_url
        self._apply_url = ""

    def select(self, *_: Any, **__: Any) -> _RegistryPostingsTable:
        return self

    def eq(self, column: str, value: Any) -> _RegistryPostingsTable:
        if column == "apply_url":
            self._apply_url = value
        return self

    def limit(self, *_: Any, **__: Any) -> _RegistryPostingsTable:
        return self

    async def execute(self) -> SimpleNamespace:
        details = self._details_by_url.get(self._apply_url)
        return SimpleNamespace(data=[details] if details is not None else [])


class _RegistryCompaniesTable:
    """`.select("name").eq("id", company_id).limit(1).execute()` -- the
    company-name half of the same track-time lookup."""

    def __init__(self, name_by_company_id: dict[str, str]) -> None:
        self._name_by_company_id = name_by_company_id
        self._company_id = ""

    def select(self, *_: Any, **__: Any) -> _RegistryCompaniesTable:
        return self

    def eq(self, column: str, value: Any) -> _RegistryCompaniesTable:
        if column == "id":
            self._company_id = value
        return self

    def limit(self, *_: Any, **__: Any) -> _RegistryCompaniesTable:
        return self

    async def execute(self) -> SimpleNamespace:
        name = self._name_by_company_id.get(self._company_id)
        return SimpleNamespace(data=[{"name": name}] if name is not None else [])


class _FakeHttpClient:
    """RemoteOK/Arbeitnow (always called by `search_jobs`, no credential
    gating) get graceful, real-shaped empty responses so they never
    produce a warning; a real per-platform liveness probe URL gets
    `liveness_status`, defaulting to 200 (keep). `you_com_body`, when
    given, lets a test produce a real live-search-lane `SearchResult`
    (every other provider is stubbed empty by default, so no discovery-
    route test before Phase 2a's fix ever exercised the live-search
    lane's own liveness check at all)."""

    def __init__(
        self, *, liveness_status: int = 200, you_com_body: dict[str, Any] | None = None
    ) -> None:
        self.liveness_status = liveness_status
        self.you_com_body = you_com_body
        self.get_calls: list[str] = []
        self.request_calls: list[tuple[str, str]] = []

    async def get(self, url: str, **_kwargs: Any) -> httpx.Response:
        self.get_calls.append(url)
        if "remoteok.com" in url:
            return httpx.Response(200, json=[], request=httpx.Request("GET", url))
        if "arbeitnow.com" in url:
            return httpx.Response(200, json={"data": []}, request=httpx.Request("GET", url))
        return httpx.Response(self.liveness_status, request=httpx.Request("GET", url))

    async def request(self, method: str, url: str, **_kwargs: Any) -> httpx.Response:
        self.request_calls.append((method, url))
        return httpx.Response(self.liveness_status, request=httpx.Request(method, url))

    async def post(self, url: str, **_kwargs: Any) -> httpx.Response:
        if self.you_com_body is not None and "ydc-index.io" in url:
            return httpx.Response(200, json=self.you_com_body, request=httpx.Request("POST", url))
        return httpx.Response(200, json={}, request=httpx.Request("POST", url))


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


@pytest.fixture(autouse=True)
def _reset_company_tier_cache() -> Any:
    import between_jobs.api.company_tiers as tiers_module

    tiers_module._cached_index = None
    yield
    tiers_module._cached_index = None


def _patch_llm(monkeypatch: pytest.MonkeyPatch, content: str) -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=content)

    monkeypatch.setattr("between_jobs.api.discovery_routes.llm_generate", fake_generate)


def _client(supabase: _FakeSupabaseClient, http: _FakeHttpClient) -> TestClient:
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[get_http_client] = lambda: http
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    return TestClient(app)


_SCORE_LLM_RESPONSE = (
    '{"scored": [{"job_id": "https://example.com/careers/job-1", "fit_score": 8, '
    '"one_liner": "Strong fit"}]}'
)


# ── _result_to_card (pure, no I/O) ───────────────────────────────────────


def _search_result(**overrides: Any) -> SearchResult:
    base: dict[str, Any] = {
        "provider": "registry",
        "title": "Backend Engineer",
        "company": "Acme",
        "location": "Remote",
        "remote": True,
        "apply_url": "https://example.com/careers/job-1",
        "snippet": "Build things.",
        "posted_at": None,
    }
    base.update(overrides)
    return SearchResult(**base)


def test_result_to_card_without_score_has_none_score() -> None:
    card = _result_to_card(_search_result(), None)
    assert card["title"] == "Backend Engineer"
    assert card["score"] is None


def test_result_to_card_with_score_merges_both() -> None:
    scored = ScoredJob(
        apply_url="https://example.com/careers/job-1",
        fit_score=8,
        one_liner="Strong fit",
        sub_scores=SubScores(
            skills=80, experience=70, workauth=60, location=100, company_health=60, compensation=60
        ),
        score100=75,
        bin="Strong",
        bottleneck="workauth",
        inapplicable_dims=("compensation",),
        detected_location=None,
        location_match="unknown",
    )
    card = _result_to_card(_search_result(), scored)
    assert card["score"]["score100"] == 75
    assert card["score"]["bin"] == "Strong"
    assert card["score"]["inapplicable_dims"] == ["compensation"]


# ── GET /discover ─────────────────────────────────────────────────────────


def test_search_discover_requires_an_active_profile() -> None:
    supabase = _FakeSupabaseClient(profile_versions=[])
    http = _FakeHttpClient()
    client = _client(supabase, http)

    response = client.get("/discover", params={"q": "backend engineer"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SETUP_REQUIRED"


def test_search_discover_requires_a_job_scoring_credential() -> None:
    supabase = _FakeSupabaseClient(capability_preferences=[], provider_credentials={})
    http = _FakeHttpClient()
    client = _client(supabase, http)

    response = client.get("/discover", params={"q": "backend engineer"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SETUP_REQUIRED"


def test_search_discover_happy_path_scores_a_registry_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_llm(monkeypatch, _SCORE_LLM_RESPONSE)
    supabase = _FakeSupabaseClient(registry_postings=[_REGISTRY_POSTING_ROW])
    http = _FakeHttpClient()
    client = _client(supabase, http)

    response = client.get("/discover", params={"q": "backend engineer"})

    assert response.status_code == 200
    body = response.json()
    assert body["dead_removed"] == 0
    assert len(body["scored"]) == 1
    assert body["scored"][0]["apply_url"] == "https://example.com/careers/job-1"
    assert body["scored"][0]["score"]["fit_score"] == 8
    assert body["scored"][0]["link_checked"] is True


def test_search_discover_never_probes_a_registry_result_for_liveness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registry-lane result already carries its liveness guarantee from
    the ATS poller's own absence-based mechanism (P1-P3e) -- verify_liveness
    must never run a real per-platform probe against it, so an http client
    that would fail every such probe has zero effect."""
    _patch_llm(monkeypatch, _SCORE_LLM_RESPONSE)
    supabase = _FakeSupabaseClient(registry_postings=[_REGISTRY_POSTING_ROW])
    http = _FakeHttpClient(liveness_status=404)
    client = _client(supabase, http)

    response = client.get("/discover", params={"q": "backend engineer"})

    assert response.status_code == 200
    body = response.json()
    assert body["dead_removed"] == 0
    assert len(body["scored"]) == 1
    assert body["scored"][0]["link_checked"] is True
    registry_url = str(_REGISTRY_POSTING_ROW["apply_url"])
    assert not any(registry_url in call for call in http.get_calls)
    assert not any(registry_url in url for _method, url in http.request_calls)


_LIVE_LANE_YOU_COM_BODY = {
    "results": {
        "web": [
            {
                "url": "https://example.com/careers/live-1",
                "title": "Backend Engineer",
                "snippets": ["A live-search-lane result."],
            }
        ]
    }
}


def test_search_discover_still_drops_a_dead_live_search_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lane split (2a) must not accidentally disable liveness
    checking for the lane it actually exists for."""
    _patch_llm(monkeypatch, _SCORE_LLM_RESPONSE)
    supabase = _FakeSupabaseClient(
        registry_postings=[],
        provider_credentials={
            ("llm", "openrouter"): _LLM_CREDENTIAL_ROW,
            ("search", "you_com"): _YOU_COM_CREDENTIAL_ROW,
        },
    )
    http = _FakeHttpClient(liveness_status=404, you_com_body=_LIVE_LANE_YOU_COM_BODY)
    client = _client(supabase, http)

    response = client.get("/discover", params={"q": "backend engineer"})

    assert response.status_code == 200
    assert response.json()["dead_removed"] == 1


def test_search_discover_registry_volume_does_not_starve_live_lane_liveness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registry-lane results must never eat into the live-search-lane's
    own liveness-check budget, even when they fill (or overflow) the cap
    in the combined, tier-sorted list."""
    monkeypatch.setattr("between_jobs.api.discovery_routes._LIVENESS_CANDIDATE_CAP", 1)
    _patch_llm(monkeypatch, _SCORE_LLM_RESPONSE)
    registry_rows = [
        {**_REGISTRY_POSTING_ROW, "apply_url": f"https://example.com/careers/registry-{i}"}
        for i in range(3)
    ]
    supabase = _FakeSupabaseClient(
        registry_postings=registry_rows,
        provider_credentials={
            ("llm", "openrouter"): _LLM_CREDENTIAL_ROW,
            ("search", "you_com"): _YOU_COM_CREDENTIAL_ROW,
        },
    )
    http = _FakeHttpClient(liveness_status=404, you_com_body=_LIVE_LANE_YOU_COM_BODY)
    client = _client(supabase, http)

    response = client.get("/discover", params={"q": "backend engineer"})

    assert response.status_code == 200
    # Registry rows (tier 1.0) sort ahead of the tier-2.5 live-lane
    # result in `combined`. With the cap monkeypatched to 1, the OLD
    # code's single slot would go to a registry row and the live-lane
    # result -- despite being genuinely dead -- would never get probed
    # at all. The fix must still check it regardless of how many
    # registry rows precede it.
    assert response.json()["dead_removed"] == 1


def test_search_discover_alive_results_past_the_scoring_batch_still_appear_in_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for the `alive[30:50]`-vanishes bug: the route
    used to pre-slice to `alive[:30]` before calling `score_jobs`, so
    `score_jobs`' own `unscored = results[30:]` was always empty -- real,
    liveness-verified-ALIVE jobs beyond the 30th never reached `scored`
    OR `more`. 35 alive registry results (past `score_jobs`' own 30-job
    batch, still under this route's 50-job liveness cap) with `dead_
    removed == 0` proves every one of the 5 beyond the batch boundary
    (job-30..job-34) now surfaces in `more` -- none silently dropped."""
    _patch_llm(monkeypatch, _SCORE_LLM_RESPONSE)
    base = datetime(2026, 8, 31, tzinfo=UTC)
    rows = [
        {
            **_REGISTRY_POSTING_ROW,
            "apply_url": f"https://example.com/careers/job-{i}",
            "title": f"Backend Engineer {i}",
            # Strictly decreasing posted_at -> aggregate_jobs' recency
            # sort deterministically places job-0 first, job-34 last,
            # so positions 30-34 are unambiguous.
            "posted_at": (base - timedelta(hours=i)).isoformat(),
        }
        for i in range(35)
    ]
    supabase = _FakeSupabaseClient(registry_postings=rows)
    http = _FakeHttpClient()
    client = _client(supabase, http)

    response = client.get("/discover", params={"q": "backend engineer"})

    assert response.status_code == 200
    body = response.json()
    assert body["dead_removed"] == 0

    # _SCORE_LLM_RESPONSE only scores job-1 (within the first 30 sent to
    # the LLM) -- the bug this test guards is about job-30..job-34, which
    # never even reach the LLM and must come back through `unscored`.
    assert [c["apply_url"] for c in body["scored"]] == ["https://example.com/careers/job-1"]

    more_urls = [c["apply_url"] for c in body["more"]]
    expected_unscored = [f"https://example.com/careers/job-{i}" for i in range(30, 35)]
    for url in expected_unscored:
        assert url in more_urls, f"{url} vanished from both scored and more"
    assert more_urls == expected_unscored


def test_search_discover_with_no_registry_or_live_results_returns_empty() -> None:
    supabase = _FakeSupabaseClient(registry_postings=[])
    http = _FakeHttpClient()
    client = _client(supabase, http)

    response = client.get("/discover", params={"q": "backend engineer"})

    assert response.status_code == 200
    body = response.json()
    assert body["scored"] == []
    assert body["more"] == []
    assert body["dead_removed"] == 0


def test_search_discover_location_param_is_a_no_op_against_an_empty_gazetteer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`filter_by_location` fails open (no-op) when the gazetteer is
    empty (P5d's own contract) -- confirms the route wires location
    filtering in without needing real city fixture data here, since
    that filter's own real behavior is already covered by
    test_search_aggregation.py/test_geo_gazetteer.py."""
    import between_jobs.api.geo_gazetteer as gazetteer_module

    gazetteer_module._cached_gazetteer = None
    _patch_llm(monkeypatch, _SCORE_LLM_RESPONSE)
    supabase = _FakeSupabaseClient(registry_postings=[_REGISTRY_POSTING_ROW])
    http = _FakeHttpClient()
    client = _client(supabase, http)

    response = client.get("/discover", params={"q": "backend engineer", "location": "New York"})

    assert response.status_code == 200
    assert len(response.json()["scored"]) == 1
    gazetteer_module._cached_gazetteer = None


# ── POST /discover/track ─────────────────────────────────────────────────


def test_track_registry_result_uses_the_real_full_jd_text() -> None:
    supabase = _FakeSupabaseClient(
        registry_posting_details={
            "https://example.com/careers/job-1": {
                "title": None,
                "location": None,
                "jd_text": "The real, full job description.",
                "company_id": None,
            }
        }
    )
    http = _FakeHttpClient()
    client = _client(supabase, http)

    response = client.post(
        "/discover/track",
        json={
            "apply_url": "https://example.com/careers/job-1",
            "title": "Senior Backend Engineer",
            "company": "Acme",
            "location": "Remote",
            "snippet": "Build backend systems in Python.",
            "provider": "registry",
        },
    )

    assert response.status_code == 201
    inserted_snapshot = supabase.job_snapshots.select_rows[-1]
    assert inserted_snapshot["description_text"] == "The real, full job description."


def test_track_registry_result_also_overrides_title_location_and_company() -> None:
    """Phase 2g -- previously only jd_text was re-derived server-side for
    the registry lane, leaving title/location/company client-trusted even
    though ground truth was one query away."""
    supabase = _FakeSupabaseClient(
        registry_posting_details={
            "https://example.com/careers/job-1": {
                "title": "Staff Backend Engineer",
                "location": "New York, NY",
                "jd_text": "The real, full job description.",
                "company_id": "company-1",
            }
        },
        registry_companies={"company-1": "Acme Corp"},
    )
    http = _FakeHttpClient()
    client = _client(supabase, http)

    response = client.post(
        "/discover/track",
        json={
            "apply_url": "https://example.com/careers/job-1",
            "title": "A stale client-supplied title",
            "company": "A stale client-supplied company",
            "location": "A stale client-supplied location",
            "snippet": "Build backend systems in Python.",
            "provider": "registry",
        },
    )

    assert response.status_code == 201
    inserted_snapshot = supabase.job_snapshots.select_rows[-1]
    assert inserted_snapshot["title"] == "Staff Backend Engineer"
    assert inserted_snapshot["location_text"] == "New York, NY"
    inserted_job = supabase.jobs.select_rows[-1]
    assert inserted_job["company_name"] == "Acme Corp"


def test_track_live_search_result_falls_back_to_the_snippet() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()
    client = _client(supabase, http)

    response = client.post(
        "/discover/track",
        json={
            "apply_url": "https://boards.greenhouse.io/acme/jobs/1",
            "title": "Backend Engineer",
            "company": "Acme",
            "location": "Remote",
            "snippet": "A short preview of the role.",
            "provider": "serper",
        },
    )

    assert response.status_code == 201
    inserted_snapshot = supabase.job_snapshots.select_rows[-1]
    assert inserted_snapshot["description_text"] == "A short preview of the role."


def test_track_result_defaults_missing_company_and_description() -> None:
    supabase = _FakeSupabaseClient()
    http = _FakeHttpClient()
    client = _client(supabase, http)

    response = client.post(
        "/discover/track",
        json={
            "apply_url": "https://example.com/careers/job-2",
            "title": "Some Role",
            "snippet": "",
            "provider": "you_com",
        },
    )

    assert response.status_code == 201
    inserted_job = supabase.jobs.select_rows[-1]
    assert inserted_job["company_name"] == "Unknown Company"
    inserted_snapshot = supabase.job_snapshots.select_rows[-1]
    assert inserted_snapshot["description_text"] == "(no description available)"
