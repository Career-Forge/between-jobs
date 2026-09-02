"""Tests for the saved-search matcher (Job Finder P9b, today-feed-job-
matching.md) -- the background job producing "high-fit new job" events.
The LLM call is faked via the module's own explicit `generate=llm_
generate` injection point, same R3-bug-informed convention every other
multi-stage pipeline in this codebase already uses.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from postgrest.exceptions import APIError

from between_jobs.api.llm_client import LLMResponse
from between_jobs.api.saved_search_matcher import (
    _select_active_saved_searches,
    run_match_tick,
)

_USER_ID = "00000000-0000-0000-0000-000000000001"
_SEARCH_ID = "40000000-0000-0000-0000-000000000001"

_PROFILE_ROW = {"user_id": _USER_ID, "canonical_json": {"personal": {"name": "Jane Doe"}}}
_PREFERENCE_ROW = {
    "user_id": _USER_ID,
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
_POSTING_ROW = {
    "title": "Backend Engineer",
    "company_name": "Acme",
    "location": "Remote",
    "remote": True,
    "apply_url": "https://boards.greenhouse.io/acme/jobs/1",
    "posted_at": "2026-09-01T00:00:00Z",
    "salary_min": None,
    "salary_max": None,
    "salary_currency": None,
    "sponsorship_signal": "unknown",
    "snippet": "Build backend systems.",
}


def _saved_search(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": _SEARCH_ID,
        "user_id": _USER_ID,
        "query": "",
        "location": None,
        "companies": [],
        "remote_only": False,
        "is_active": True,
        "last_matched_at": "2026-08-01T00:00:00Z",
    }
    base.update(overrides)
    return base


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, column: str, value: Any) -> _ChainBuilder:
        return _ChainBuilder([r for r in self._rows if r.get(column) == value])

    def range(self, start: int, end: int) -> _ChainBuilder:
        return _ChainBuilder(self._rows[start : end + 1])

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


class _UpdateBuilder:
    def __init__(self, table: _FakeSavedSearchesTable, data: dict[str, Any]) -> None:
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


class _FakeSavedSearchesTable:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.rows)

    def update(self, data: dict[str, Any]) -> _UpdateBuilder:
        return _UpdateBuilder(self, data)


class _CredentialTable:
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


class _EventOutboxTable:
    def __init__(self, *, raise_on_idempotency_key: set[str] | None = None) -> None:
        self.insert_calls: list[dict[str, Any]] = []
        self._raise_on = raise_on_idempotency_key or set()

    def insert(self, data: dict[str, Any]) -> _EventInsertBuilder:
        return _EventInsertBuilder(self, data)


class _EventInsertBuilder:
    def __init__(self, table: _EventOutboxTable, data: dict[str, Any]) -> None:
        self._table = table
        self._data = data

    async def execute(self) -> SimpleNamespace:
        if self._data["idempotency_key"] in self._table._raise_on:
            raise APIError(
                {"message": "duplicate key", "code": "23505", "details": None, "hint": None}
            )
        self._table.insert_calls.append(self._data)
        return SimpleNamespace(data=[{"id": "evt-1", **self._data}])


class _FakeRpcBuilder:
    def __init__(self, data: Any) -> None:
        self._data = data

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._data)


class _FakeSupabase:
    def __init__(
        self,
        *,
        saved_searches: list[dict[str, Any]] | None = None,
        registry_postings: list[dict[str, Any]] | None = None,
        profile_versions: list[dict[str, Any]] | None = None,
        capability_preferences: list[dict[str, Any]] | None = None,
        provider_credentials: dict[tuple[str, str], dict[str, Any]] | None = None,
        event_outbox: _EventOutboxTable | None = None,
    ) -> None:
        self.saved_searches = _FakeSavedSearchesTable(
            saved_searches if saved_searches is not None else [_saved_search()]
        )
        self._registry_postings = registry_postings if registry_postings is not None else []
        self.profile_versions = _FakeSavedSearchesTable(
            profile_versions if profile_versions is not None else [_PROFILE_ROW]
        )
        self.capability_preferences = _FakeSavedSearchesTable(
            capability_preferences if capability_preferences is not None else [_PREFERENCE_ROW]
        )
        self._credential_table = _CredentialTable(
            provider_credentials
            if provider_credentials is not None
            else {("llm", "openrouter"): _LLM_CREDENTIAL_ROW}
        )
        self.event_outbox = event_outbox or _EventOutboxTable()
        self._company_tiers = _FakeSavedSearchesTable([])

    def table(self, name: str) -> Any:
        return {
            "saved_searches": self.saved_searches,
            "profile_versions": self.profile_versions,
            "capability_preferences": self.capability_preferences,
            "provider_credentials": self._credential_table,
            "event_outbox": self.event_outbox,
            "company_tiers": self._company_tiers,
        }[name]

    def rpc(self, fn: str, params: dict[str, Any]) -> _FakeRpcBuilder:
        if fn == "search_new_job_registry_postings":
            return _FakeRpcBuilder(self._registry_postings)
        if fn == "decrypt_secret":
            return _FakeRpcBuilder(params["p_ciphertext"])
        raise AssertionError(f"unexpected rpc: {fn}")


def _patch_llm(monkeypatch: pytest.MonkeyPatch, content: str) -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=content)

    monkeypatch.setattr("between_jobs.api.saved_search_matcher.llm_generate", fake_generate)


def _score_response(job_id: str, score: int, bin_: str = "Strong") -> str:
    return json.dumps(
        {
            "scored": [
                {
                    "job_id": job_id,
                    "fit_score": round(score / 10),
                    "one_liner": "A real fit reason",
                    "skills_score": score,
                    "experience_score": score,
                    "workauth_score": score,
                }
            ]
        }
    )


@pytest.fixture(autouse=True)
def _reset_caches() -> Any:
    import between_jobs.api.company_tiers as tiers_module

    tiers_module._cached_index = None
    yield
    tiers_module._cached_index = None


async def test_run_match_tick_returns_zero_with_no_active_searches() -> None:
    supabase = _FakeSupabase(saved_searches=[])

    processed = await run_match_tick(supabase)  # type: ignore[arg-type]

    assert processed == 0


async def test_strong_match_publishes_an_event_and_advances_the_watermark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_llm(monkeypatch, _score_response(str(_POSTING_ROW["apply_url"]), 80))
    supabase = _FakeSupabase(registry_postings=[_POSTING_ROW])

    processed = await run_match_tick(supabase)  # type: ignore[arg-type]

    assert processed == 1
    assert len(supabase.event_outbox.insert_calls) == 1
    event = supabase.event_outbox.insert_calls[0]
    assert event["aggregate_type"] == "saved_search"
    assert event["aggregate_id"] == _SEARCH_ID
    assert event["event_type"] == "job_registry.match_found.v1"
    assert event["payload"]["apply_url"] == _POSTING_ROW["apply_url"]
    assert event["payload"]["score100"] >= 70
    # Watermark always advances, whether or not a match was found.
    assert supabase.saved_searches.rows[0]["last_matched_at"] != "2026-08-01T00:00:00Z"


async def test_weak_match_does_not_publish_but_still_advances_watermark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_llm(monkeypatch, _score_response(str(_POSTING_ROW["apply_url"]), 30, "Poor"))
    supabase = _FakeSupabase(registry_postings=[_POSTING_ROW])

    processed = await run_match_tick(supabase)  # type: ignore[arg-type]

    assert processed == 1
    assert supabase.event_outbox.insert_calls == []
    assert supabase.saved_searches.rows[0]["last_matched_at"] != "2026-08-01T00:00:00Z"


async def test_no_candidates_skips_scoring_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_generate(**_kwargs: Any) -> LLMResponse:
        raise AssertionError("should never score with zero candidates")

    monkeypatch.setattr("between_jobs.api.saved_search_matcher.llm_generate", fail_generate)
    supabase = _FakeSupabase(registry_postings=[])

    processed = await run_match_tick(supabase)  # type: ignore[arg-type]

    assert processed == 1
    assert supabase.event_outbox.insert_calls == []


async def test_missing_profile_skips_scoring_but_advances_watermark() -> None:
    supabase = _FakeSupabase(registry_postings=[_POSTING_ROW], profile_versions=[])

    processed = await run_match_tick(supabase)  # type: ignore[arg-type]

    assert processed == 1
    assert supabase.event_outbox.insert_calls == []
    assert supabase.saved_searches.rows[0]["last_matched_at"] != "2026-08-01T00:00:00Z"


async def test_missing_job_scoring_credential_skips_scoring_but_advances_watermark() -> None:
    supabase = _FakeSupabase(
        registry_postings=[_POSTING_ROW],
        capability_preferences=[],
        provider_credentials={},
    )

    processed = await run_match_tick(supabase)  # type: ignore[arg-type]

    assert processed == 1
    assert supabase.event_outbox.insert_calls == []
    assert supabase.saved_searches.rows[0]["last_matched_at"] != "2026-08-01T00:00:00Z"


async def test_role_filter_excludes_a_non_matching_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unrelated_posting = {**_POSTING_ROW, "title": "Sales Manager", "apply_url": "https://x/2"}

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        raise AssertionError("role filter should have excluded the only candidate")

    monkeypatch.setattr("between_jobs.api.saved_search_matcher.llm_generate", fake_generate)
    supabase = _FakeSupabase(
        saved_searches=[_saved_search(query="backend engineer")],
        registry_postings=[unrelated_posting],
    )

    processed = await run_match_tick(supabase)  # type: ignore[arg-type]

    assert processed == 1
    assert supabase.event_outbox.insert_calls == []


async def test_company_filter_excludes_a_non_matching_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        raise AssertionError("company filter should have excluded the only candidate")

    monkeypatch.setattr("between_jobs.api.saved_search_matcher.llm_generate", fake_generate)
    supabase = _FakeSupabase(
        saved_searches=[_saved_search(companies=["Anthropic"])],
        registry_postings=[_POSTING_ROW],  # company_name = "Acme"
    )

    processed = await run_match_tick(supabase)  # type: ignore[arg-type]

    assert processed == 1
    assert supabase.event_outbox.insert_calls == []


async def test_caps_at_five_strong_matches_per_search_per_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postings = [
        {**_POSTING_ROW, "apply_url": f"https://x/{i}", "title": f"Backend Engineer {i}"}
        for i in range(7)
    ]
    scored = {
        "scored": [
            {
                "job_id": p["apply_url"],
                "fit_score": 8,
                "one_liner": "Strong fit",
                "skills_score": 80,
                "experience_score": 80,
                "workauth_score": 80,
            }
            for p in postings
        ]
    }

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps(scored))

    monkeypatch.setattr("between_jobs.api.saved_search_matcher.llm_generate", fake_generate)
    supabase = _FakeSupabase(registry_postings=postings)

    await run_match_tick(supabase)  # type: ignore[arg-type]

    assert len(supabase.event_outbox.insert_calls) == 5


async def test_duplicate_match_is_idempotently_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    idempotency_key = f"job_registry.match_found:{_SEARCH_ID}:{_POSTING_ROW['apply_url']}"
    _patch_llm(monkeypatch, _score_response(str(_POSTING_ROW["apply_url"]), 80))
    supabase = _FakeSupabase(
        registry_postings=[_POSTING_ROW],
        event_outbox=_EventOutboxTable(raise_on_idempotency_key={idempotency_key}),
    )

    processed = await run_match_tick(supabase)  # type: ignore[arg-type]

    assert processed == 1
    assert supabase.event_outbox.insert_calls == []


async def test_select_active_saved_searches_pages_past_the_first_1000_rows() -> None:
    big_rows = [_saved_search(id=f"search-{i}") for i in range(1500)]
    supabase = _FakeSupabase(saved_searches=big_rows)

    rows = await _select_active_saved_searches(supabase)  # type: ignore[arg-type]

    assert len(rows) == 1500


async def test_run_match_tick_processes_searches_concurrently_but_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """code-review-fixes.md 4d: different saved searches must run
    concurrently (not the old fully-sequential one-at-a-time loop), but
    bounded -- never more than _MAX_CONCURRENT_SEARCHES real LLM calls
    in flight at once."""
    searches = [_saved_search(id=f"search-{i}") for i in range(12)]
    supabase = _FakeSupabase(saved_searches=searches, registry_postings=[_POSTING_ROW])

    in_flight = 0
    max_in_flight = 0

    async def tracking_generate(**_kwargs: Any) -> LLMResponse:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return LLMResponse(content=_score_response(str(_POSTING_ROW["apply_url"]), 80))

    monkeypatch.setattr("between_jobs.api.saved_search_matcher.llm_generate", tracking_generate)

    processed = await run_match_tick(supabase)  # type: ignore[arg-type]

    assert processed == 12
    assert 1 < max_in_flight <= 5  # genuinely concurrent, but never past the bound
