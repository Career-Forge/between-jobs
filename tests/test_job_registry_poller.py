"""Tests for the registry poller's own orchestration (Job Finder P2,
job-finder-port.md D2/D8) -- select-due -> fetch -> upsert -> close/advance/
penalize. Real ATS adapters are swapped for fakes here (their own field-
mapping/HTTP behavior is covered by test_job_registry_adapters.py); these
tests exercise the tick's batching, the new-posting-count computation that
drives tier promotion, and the ok/not_modified/gone/failed fan-out."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from postgrest.exceptions import APIError

from between_jobs.api import job_registry_poller
from between_jobs.api.job_registry_adapters import (
    ADAPTERS,
    AdapterResult,
    DueCompany,
    ParsedPosting,
)
from between_jobs.api.job_registry_poller import (
    _MAX_UPSERT_ATTEMPTS,
    _POSTING_UPSERT_BATCH_SIZE,
    _dedupe_postings,
    _extract_if_eligible,
    _upsert_postings_in_batches,
    run_eightfold_jd_backfill,
    run_poll_tick,
)

# ADAPTERS (imported directly from job_registry_adapters, not via
# job_registry_poller) is the same dict object job_registry_poller.py's
# own ADAPTERS name is bound to -- mutating entries on it here is visible
# there too, since dicts are shared by reference, without reaching into
# job_registry_poller's own re-exported name (which mypy's strict
# no-implicit-reexport checking flags).


def _due_row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "company_id": "company-uuid-1",
        "name": "Acme",
        "ats_type": "greenhouse",
        "slug": "acme",
        "api_base": "",
        "board": "greenhouse:acme",
        "etag": "",
        "sweep_started_at": None,
        "sweep_posting_count": 0,
    }
    base.update(overrides)
    return base


def _posting(board: str, external_id: str, **overrides: Any) -> ParsedPosting:
    base: dict[str, Any] = {
        "company_id": "company-uuid-1",
        "board": board,
        "external_id": external_id,
        "title": "Engineer",
        "jd_text": "",
        "location": "Remote",
        "remote": True,
        "apply_url": "https://example.com",
        "posted_at": None,
    }
    base.update(overrides)
    return ParsedPosting(**base)


class _FakeRpcCall:
    def __init__(self, data: Any) -> None:
        self._data = data

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._data)


class _FakeSupabase:
    """Tracks (board, external_id) keys already upserted across calls to
    faithfully reproduce Postgres's own `xmax = 0` semantics -- a second
    tick's upsert of a previously-seen posting must NOT count as new
    again, which is exactly what drives the tier-promotion signal."""

    def __init__(self, due_rows: list[dict[str, Any]] | None = None) -> None:
        self.due_rows = due_rows or []
        self.rpc_calls: list[tuple[str, dict[str, Any]]] = []
        self._seen_keys: set[tuple[str, str]] = set()

    def rpc(self, name: str, params: dict[str, Any]) -> _FakeRpcCall:
        self.rpc_calls.append((name, params))
        if name == "select_due_job_registry_companies":
            return _FakeRpcCall(self.due_rows)
        if name == "upsert_job_registry_postings":
            data = []
            for p in params["postings"]:
                key = (p["board"], p["external_id"])
                is_new = key not in self._seen_keys
                self._seen_keys.add(key)
                data.append({"board": p["board"], "is_new": is_new})
            return _FakeRpcCall(data)
        return _FakeRpcCall(None)

    def calls_named(self, name: str) -> list[dict[str, Any]]:
        return [params for called, params in self.rpc_calls if called == name]


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))


# ── _dedupe_postings ─────────────────────────────────────────────────────
# Regression coverage for a real bug: a real P3a tick hit Postgres error
# 21000 ("ON CONFLICT DO UPDATE command cannot affect row a second
# time") because SmartRecruiters' own pagination returned the same
# posting on two sequential pages (the board's ordering shifted between
# fetches) -- two rows in one upsert batch shared the same (board,
# external_id) key, which Postgres's upsert cannot apply twice in one
# statement.


def test_dedupe_postings_drops_duplicate_board_and_external_id() -> None:
    rows = [
        {"board": "smartrecruiters:acme:", "external_id": "1", "title": "First seen"},
        {"board": "smartrecruiters:acme:", "external_id": "2", "title": "Unique"},
        {"board": "smartrecruiters:acme:", "external_id": "1", "title": "Second seen (wins)"},
    ]

    result = _dedupe_postings(rows)

    assert len(result) == 2
    by_id = {r["external_id"]: r for r in result}
    assert by_id["1"]["title"] == "Second seen (wins)"
    assert by_id["2"]["title"] == "Unique"


def test_dedupe_postings_keeps_same_external_id_on_different_boards() -> None:
    rows = [
        {"board": "smartrecruiters:acme:", "external_id": "1"},
        {"board": "smartrecruiters:other:", "external_id": "1"},
    ]

    result = _dedupe_postings(rows)

    assert len(result) == 2


# ── _extract_if_eligible ────────────────────────────────────────────────


def test_extract_if_eligible_runs_extraction_when_jd_text_long_enough() -> None:
    posting = _posting(
        "greenhouse:acme",
        "1",
        title="Engineer",
        jd_text="Salary $120,000 - $150,000 USD. " * 3,
    )
    row = _extract_if_eligible(posting)
    assert row["salary_min"] == 120000
    assert row["extraction_version"] == 1
    assert row["extracted_at"] is not None


def test_extract_if_eligible_skips_extraction_when_jd_text_too_short() -> None:
    posting = _posting("greenhouse:acme", "1", jd_text="short")
    row = _extract_if_eligible(posting)
    assert row["salary_min"] is None
    assert row["sponsorship_signal"] == "unknown"
    assert row["extraction_version"] is None
    assert row["extracted_at"] is None


# ── run_poll_tick ────────────────────────────────────────────────────────


async def test_run_poll_tick_returns_zero_and_makes_no_other_calls_when_nothing_due() -> None:
    supabase = _FakeSupabase(due_rows=[])
    count = await run_poll_tick(_http(), supabase)  # type: ignore[arg-type]
    assert count == 0
    # Only the due-companies lookup itself -- no upsert/close/advance/
    # penalize call when there was nothing to process.
    assert supabase.rpc_calls == [("select_due_job_registry_companies", {})]


async def test_run_poll_tick_upserts_and_advances_with_new_posting_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_greenhouse(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
        return AdapterResult(
            status="ok",
            postings=[
                _posting(company.board, "1"),
                _posting(company.board, "2"),
            ],
            new_etag="new-etag",
        )

    monkeypatch.setitem(ADAPTERS, "greenhouse", fake_greenhouse)
    supabase = _FakeSupabase(due_rows=[_due_row()])

    count = await run_poll_tick(_http(), supabase)  # type: ignore[arg-type]

    assert count == 1
    upsert_calls = supabase.calls_named("upsert_job_registry_postings")
    assert len(upsert_calls) == 1
    assert len(upsert_calls[0]["postings"]) == 2

    close_calls = supabase.calls_named("close_stale_job_registry_postings")
    assert close_calls[0]["close_targets"] == [
        {"board": "greenhouse:acme", "cutoff": _any_timestamp(close_calls[0])}
    ]

    advance_calls = supabase.calls_named("advance_job_registry_poll_state")
    assert advance_calls[0]["results"] == [
        {
            "board": "greenhouse:acme",
            "etag": "new-etag",
            "relevant": 2,
            "sweep_started_at": None,
            "sweep_posting_count": 0,
        }
    ]

    assert supabase.calls_named("penalize_failed_job_registry_boards") == []


async def test_run_poll_tick_partial_status_upserts_but_skips_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mid-pagination failure that still collected real data (Bug 3a)
    must upsert what it got, but must NEVER close_stale_job_registry_
    postings off an admittedly incomplete fetch -- that would wrongly
    mark still-open postings on unfetched later pages closed."""

    async def fake_greenhouse(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
        return AdapterResult(
            status="partial",
            postings=[_posting(company.board, "1")],
            new_etag="etag-from-page0",
        )

    monkeypatch.setitem(ADAPTERS, "greenhouse", fake_greenhouse)
    supabase = _FakeSupabase(due_rows=[_due_row()])

    await run_poll_tick(_http(), supabase)  # type: ignore[arg-type]

    assert len(supabase.calls_named("upsert_job_registry_postings")[0]["postings"]) == 1
    assert supabase.calls_named("close_stale_job_registry_postings") == []
    advance = supabase.calls_named("advance_job_registry_poll_state")[0]["results"][0]
    assert advance["etag"] == "etag-from-page0"
    assert advance["relevant"] == 1


def _any_timestamp(close_call: dict[str, Any]) -> str:
    return str(close_call["close_targets"][0]["cutoff"])


async def test_run_poll_tick_second_tick_only_counts_genuinely_new_postings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tick = {"n": 0}

    async def fake_greenhouse(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
        tick["n"] += 1
        postings = [_posting(company.board, "1")]
        if tick["n"] == 2:
            postings.append(_posting(company.board, "2"))  # one genuinely new posting
        return AdapterResult(status="ok", postings=postings, new_etag=None)

    monkeypatch.setitem(ADAPTERS, "greenhouse", fake_greenhouse)
    supabase = _FakeSupabase(due_rows=[_due_row()])

    await run_poll_tick(_http(), supabase)  # type: ignore[arg-type]
    await run_poll_tick(_http(), supabase)  # type: ignore[arg-type]

    advance_calls = supabase.calls_named("advance_job_registry_poll_state")
    assert advance_calls[0]["results"][0]["relevant"] == 1  # first tick: posting "1" is new
    assert advance_calls[1]["results"][0]["relevant"] == 1  # second tick: only "2" is new


async def test_run_poll_tick_not_modified_uses_sentinel_and_skips_close_and_upsert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_greenhouse(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
        return AdapterResult(status="not_modified")

    monkeypatch.setitem(ADAPTERS, "greenhouse", fake_greenhouse)
    supabase = _FakeSupabase(due_rows=[_due_row()])

    await run_poll_tick(_http(), supabase)  # type: ignore[arg-type]

    assert supabase.calls_named("upsert_job_registry_postings") == []
    assert supabase.calls_named("close_stale_job_registry_postings") == []
    advance_calls = supabase.calls_named("advance_job_registry_poll_state")
    assert advance_calls[0]["results"] == [
        {
            "board": "greenhouse:acme",
            "etag": None,
            "relevant": -1,
            "sweep_started_at": None,
            "sweep_posting_count": 0,
        }
    ]


async def test_run_poll_tick_routes_gone_and_failed_boards_to_penalize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_greenhouse(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
        return (
            AdapterResult(status="gone")
            if company.slug == "gone-co"
            else AdapterResult(status="failed")
        )

    monkeypatch.setitem(ADAPTERS, "greenhouse", fake_greenhouse)
    supabase = _FakeSupabase(
        due_rows=[
            _due_row(company_id="c1", slug="gone-co", board="greenhouse:gone-co"),
            _due_row(company_id="c2", slug="failed-co", board="greenhouse:failed-co"),
        ]
    )

    await run_poll_tick(_http(), supabase)  # type: ignore[arg-type]

    penalize_calls = supabase.calls_named("penalize_failed_job_registry_boards")
    assert len(penalize_calls) == 1
    assert penalize_calls[0]["gone_boards"] == ["greenhouse:gone-co"]
    assert penalize_calls[0]["failed_boards"] == ["greenhouse:failed-co"]
    assert supabase.calls_named("advance_job_registry_poll_state") == []


async def test_run_poll_tick_skips_penalize_call_when_nothing_failed_or_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_greenhouse(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
        return AdapterResult(status="ok", postings=[])

    monkeypatch.setitem(ADAPTERS, "greenhouse", fake_greenhouse)
    supabase = _FakeSupabase(due_rows=[_due_row()])

    await run_poll_tick(_http(), supabase)  # type: ignore[arg-type]

    assert supabase.calls_named("penalize_failed_job_registry_boards") == []
    # A genuinely empty board still gets a close-target and a relevant=0
    # advance -- an empty listing is real, meaningful signal, not a failure.
    assert supabase.calls_named("close_stale_job_registry_postings")
    assert supabase.calls_named("advance_job_registry_poll_state")[0]["results"][0]["relevant"] == 0


async def test_run_poll_tick_processes_multiple_companies_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_greenhouse(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
        return AdapterResult(status="ok", postings=[_posting(company.board, "1")])

    monkeypatch.setitem(ADAPTERS, "greenhouse", fake_greenhouse)
    supabase = _FakeSupabase(
        due_rows=[
            _due_row(company_id="c1", slug="a", board="greenhouse:a"),
            _due_row(company_id="c2", slug="b", board="greenhouse:b"),
        ]
    )

    count = await run_poll_tick(_http(), supabase)  # type: ignore[arg-type]

    assert count == 2
    upsert_calls = supabase.calls_named("upsert_job_registry_postings")
    assert len(upsert_calls[0]["postings"]) == 2
    boards_advanced = {
        r["board"] for r in supabase.calls_named("advance_job_registry_poll_state")[0]["results"]
    }
    assert boards_advanced == {"greenhouse:a", "greenhouse:b"}


async def test_run_poll_tick_unknown_ats_type_is_treated_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(ADAPTERS, "greenhouse", raising=False)
    supabase = _FakeSupabase(due_rows=[_due_row()])

    await run_poll_tick(_http(), supabase)  # type: ignore[arg-type]

    penalize_calls = supabase.calls_named("penalize_failed_job_registry_boards")
    assert penalize_calls[0]["failed_boards"] == ["greenhouse:acme"]


async def test_run_poll_tick_per_company_timeout_is_treated_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def slow_adapter(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
        await asyncio.sleep(10)
        return AdapterResult(status="ok")

    monkeypatch.setattr(job_registry_poller, "_PER_COMPANY_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setitem(ADAPTERS, "greenhouse", slow_adapter)
    supabase = _FakeSupabase(due_rows=[_due_row()])

    await run_poll_tick(_http(), supabase)  # type: ignore[arg-type]

    penalize_calls = supabase.calls_named("penalize_failed_job_registry_boards")
    assert penalize_calls[0]["failed_boards"] == ["greenhouse:acme"]


# ── run_poll_tick: Google's sweep-start-cutoff bookkeeping (P3e) ────────
# Google's own board is too large to fully re-walk in one tick (confirmed
# live: 85+ pages), so `_PAGINATED_ATS_TYPES` routes it through cross-tick
# sweep bookkeeping instead of the plain run_start cutoff every other
# adapter uses. These tests exercise that bookkeeping directly against
# job_registry_poller.py's own orchestration -- fetch_google's own
# per-tick page-walking is covered separately in
# test_job_registry_adapters_p3e.py.


def _google_row(**overrides: Any) -> dict[str, Any]:
    base = _due_row(
        ats_type="google",
        slug="google",
        board="google:google:https://careers.google.com/",
    )
    base.update(overrides)
    return base


async def test_run_poll_tick_paginated_type_starts_new_sweep_when_not_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_google(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
        return AdapterResult(
            status="ok",
            postings=[_posting(company.board, "1"), _posting(company.board, "2")],
            new_etag="5",
            hit_end=False,
        )

    monkeypatch.setitem(ADAPTERS, "google", fake_google)
    supabase = _FakeSupabase(due_rows=[_google_row(sweep_started_at=None, sweep_posting_count=0)])

    await run_poll_tick(_http(), supabase)  # type: ignore[arg-type]

    # Mid-sweep: never close, since the board hasn't been fully re-walked.
    assert supabase.calls_named("close_stale_job_registry_postings") == []
    result = supabase.calls_named("advance_job_registry_poll_state")[0]["results"][0]
    assert result["sweep_started_at"] is not None  # a fresh sweep started this tick
    assert result["sweep_posting_count"] == 2


async def test_run_poll_tick_paginated_type_continues_existing_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_google(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
        return AdapterResult(
            status="ok",
            postings=[_posting(company.board, "3"), _posting(company.board, "4")],
            new_etag="9",
            hit_end=False,
        )

    monkeypatch.setitem(ADAPTERS, "google", fake_google)
    supabase = _FakeSupabase(
        due_rows=[_google_row(sweep_started_at="2026-08-30T12:00:00+00:00", sweep_posting_count=40)]
    )

    await run_poll_tick(_http(), supabase)  # type: ignore[arg-type]

    assert supabase.calls_named("close_stale_job_registry_postings") == []
    result = supabase.calls_named("advance_job_registry_poll_state")[0]["results"][0]
    # Carries the ORIGINAL sweep start forward, not this tick's own start.
    assert result["sweep_started_at"] == "2026-08-30T12:00:00+00:00"
    assert result["sweep_posting_count"] == 42


async def test_run_poll_tick_paginated_type_completes_sweep_closes_using_sweep_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_google(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
        return AdapterResult(
            status="ok",
            postings=[_posting(company.board, "5")],
            new_etag="1",
            hit_end=True,
        )

    monkeypatch.setitem(ADAPTERS, "google", fake_google)
    supabase = _FakeSupabase(
        due_rows=[
            _google_row(sweep_started_at="2026-08-30T12:00:00+00:00", sweep_posting_count=118)
        ]
    )

    await run_poll_tick(_http(), supabase)  # type: ignore[arg-type]

    close_calls = supabase.calls_named("close_stale_job_registry_postings")
    assert close_calls == [
        {
            "close_targets": [
                {
                    "board": "google:google:https://careers.google.com/",
                    "cutoff": "2026-08-30T12:00:00+00:00",
                }
            ]
        }
    ]
    result = supabase.calls_named("advance_job_registry_poll_state")[0]["results"][0]
    # Reset, ready for the next sweep to start fresh.
    assert result["sweep_started_at"] is None
    assert result["sweep_posting_count"] == 0


async def test_run_poll_tick_paginated_type_zero_sweep_guard_skips_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirrors n8n's own real s151 fix: a sweep that completes having seen
    ZERO postings across every one of its ticks must never be read as "the
    board is now empty" -- that's exactly the shape of a broken scraper,
    not a real signal, and closing on it would silently wipe every
    still-live posting on the board."""

    async def fake_google(http: httpx.AsyncClient, company: DueCompany) -> AdapterResult:
        return AdapterResult(status="ok", postings=[], new_etag="1", hit_end=True)

    monkeypatch.setitem(ADAPTERS, "google", fake_google)
    supabase = _FakeSupabase(
        due_rows=[_google_row(sweep_started_at="2026-08-30T12:00:00+00:00", sweep_posting_count=0)]
    )

    await run_poll_tick(_http(), supabase)  # type: ignore[arg-type]

    assert supabase.calls_named("close_stale_job_registry_postings") == []
    result = supabase.calls_named("advance_job_registry_poll_state")[0]["results"][0]
    assert result["sweep_started_at"] is None
    assert result["sweep_posting_count"] == 0


# ── _upsert_postings_in_batches / retry ──────────────────────────────────
# Regression coverage for a real bug: a real 72-company tick against the
# live registry batched every posting from every company into ONE upsert
# call and hit Postgres's own statement timeout (57014) -- the same
# failure P1's seed-import script hit against this table. Fixed with
# bounded batches plus a capped retry-with-backoff on that SQLSTATE.


class _CountingUpsertSupabase:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def rpc(self, name: str, params: dict[str, Any]) -> _FakeRpcCall:
        self.calls.append(params)
        data = [{"board": p["board"], "is_new": True} for p in params["postings"]]
        return _FakeRpcCall(data)


async def test_upsert_postings_in_batches_splits_large_ticks_into_bounded_batches() -> None:
    supabase = _CountingUpsertSupabase()
    postings = [
        {"board": "greenhouse:acme", "external_id": str(i)}
        for i in range(_POSTING_UPSERT_BATCH_SIZE + 50)
    ]

    counts = await _upsert_postings_in_batches(supabase, postings)  # type: ignore[arg-type]

    assert len(supabase.calls) == 2
    assert len(supabase.calls[0]["postings"]) == _POSTING_UPSERT_BATCH_SIZE
    assert len(supabase.calls[1]["postings"]) == 50
    assert counts == {"greenhouse:acme": _POSTING_UPSERT_BATCH_SIZE + 50}


class _FlakyUpsertRpcCall:
    def __init__(self, state: dict[str, int], params: dict[str, Any]) -> None:
        self._state = state
        self._params = params

    async def execute(self) -> SimpleNamespace:
        self._state["attempts"] += 1
        if self._state["attempts"] <= self._state["fail_times"]:
            raise APIError(
                {
                    "message": "canceling statement due to statement timeout",
                    "code": "57014",
                    "hint": None,
                    "details": None,
                }
            )
        data = [{"board": p["board"], "is_new": True} for p in self._params["postings"]]
        return SimpleNamespace(data=data)


class _FlakyUpsertSupabase:
    def __init__(self, fail_times: int) -> None:
        self.state = {"attempts": 0, "fail_times": fail_times}

    def rpc(self, name: str, params: dict[str, Any]) -> _FlakyUpsertRpcCall:
        return _FlakyUpsertRpcCall(self.state, params)


async def _no_sleep(_seconds: float) -> None:
    return


async def test_upsert_batch_retries_transient_statement_timeouts_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    supabase = _FlakyUpsertSupabase(fail_times=2)

    counts = await _upsert_postings_in_batches(
        supabase,  # type: ignore[arg-type]
        [{"board": "greenhouse:acme", "external_id": "1"}],
    )

    assert counts == {"greenhouse:acme": 1}
    assert supabase.state["attempts"] == 3


async def test_upsert_batch_gives_up_after_max_attempts_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    supabase = _FlakyUpsertSupabase(fail_times=99)

    with pytest.raises(APIError):
        await _upsert_postings_in_batches(
            supabase,  # type: ignore[arg-type]
            [{"board": "greenhouse:acme", "external_id": "1"}],
        )

    assert supabase.state["attempts"] == _MAX_UPSERT_ATTEMPTS


# ── run_eightfold_jd_backfill ────────────────────────────────────────────
# Eightfold's own list endpoint never returns real description text on
# either tier (confirmed live, P3c research) -- this lane is the only
# path by which an Eightfold row's jd_text/salary/sponsorship data ever
# gets populated.


class _BackfillFakeSupabase:
    def __init__(self, candidates: list[dict[str, Any]]) -> None:
        self.candidates = candidates
        self.rpc_calls: list[tuple[str, dict[str, Any]]] = []

    def rpc(self, name: str, params: dict[str, Any]) -> _FakeRpcCall:
        self.rpc_calls.append((name, params))
        if name == "select_eightfold_jd_backfill_candidates":
            return _FakeRpcCall(self.candidates)
        return _FakeRpcCall(None)

    def calls_named(self, name: str) -> list[dict[str, Any]]:
        return [params for called, params in self.rpc_calls if called == name]


def _backfill_candidate(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "posting-uuid-1",
        "apply_url": "https://paypal.eightfold.ai/careers/job/274922097063",
        "api_base": "paypal.eightfold.ai",
        "slug": "paypal.com",
    }
    base.update(overrides)
    return base


async def test_run_eightfold_jd_backfill_returns_zero_when_no_candidates() -> None:
    supabase = _BackfillFakeSupabase(candidates=[])
    count = await run_eightfold_jd_backfill(_http(), supabase)  # type: ignore[arg-type]
    assert count == 0
    assert supabase.calls_named("backfill_job_registry_posting_descriptions") == []


async def test_run_eightfold_jd_backfill_skips_candidate_with_unextractable_apply_url() -> None:
    supabase = _BackfillFakeSupabase(
        candidates=[_backfill_candidate(apply_url="https://example.com/no-job-id-here")]
    )

    count = await run_eightfold_jd_backfill(_http(), supabase)  # type: ignore[arg-type]

    assert count == 0
    assert supabase.calls_named("backfill_job_registry_posting_descriptions") == []


async def test_run_eightfold_jd_backfill_writes_back_detail_with_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from between_jobs.api.job_registry_adapters import EightfoldDetail

    async def fake_detail(
        http: httpx.AsyncClient, api_base: str, slug: str, position_id: str
    ) -> Any:
        assert api_base == "paypal.eightfold.ai"
        assert slug == "paypal.com"
        assert position_id == "274922097063"
        return EightfoldDetail(
            jd_text="Salary $150,000 - $200,000 USD. " * 3,
            apply_url="https://paypal.eightfold.ai/careers/job/274922097063",
        )

    monkeypatch.setattr(job_registry_poller, "fetch_eightfold_detail", fake_detail)
    supabase = _BackfillFakeSupabase(candidates=[_backfill_candidate()])

    count = await run_eightfold_jd_backfill(_http(), supabase)  # type: ignore[arg-type]

    assert count == 1
    backfill_calls = supabase.calls_named("backfill_job_registry_posting_descriptions")
    assert len(backfill_calls) == 1
    update = backfill_calls[0]["updates"][0]
    assert update["job_id"] == "posting-uuid-1"
    assert update["salary_min"] == 150000
    assert update["extraction_version"] == 1
    assert update["extracted_at"] is not None


async def test_run_eightfold_jd_backfill_skips_when_detail_too_short(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from between_jobs.api.job_registry_adapters import EightfoldDetail

    async def fake_detail(
        http: httpx.AsyncClient, api_base: str, slug: str, position_id: str
    ) -> Any:
        return EightfoldDetail(jd_text="short", apply_url="https://x/careers/job/1")

    monkeypatch.setattr(job_registry_poller, "fetch_eightfold_detail", fake_detail)
    supabase = _BackfillFakeSupabase(candidates=[_backfill_candidate()])

    count = await run_eightfold_jd_backfill(_http(), supabase)  # type: ignore[arg-type]

    assert count == 0
    assert supabase.calls_named("backfill_job_registry_posting_descriptions") == []


async def test_run_eightfold_jd_backfill_skips_when_detail_fetch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_detail(
        http: httpx.AsyncClient, api_base: str, slug: str, position_id: str
    ) -> Any:
        return None

    monkeypatch.setattr(job_registry_poller, "fetch_eightfold_detail", fake_detail)
    supabase = _BackfillFakeSupabase(candidates=[_backfill_candidate()])

    count = await run_eightfold_jd_backfill(_http(), supabase)  # type: ignore[arg-type]

    assert count == 0
