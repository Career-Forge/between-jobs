"""Tests for Hiring Signals P3's shared query cache (`hiring_signal_cache`):
keys, read-side expiry, what is stored, bounded purging, and concurrent
writers. Runs against the in-memory fake in `hiring_signal_fakes`."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from hiring_signal_fakes import FakeSupabase, FakeTable, Query, as_client

from between_jobs.api.hiring_signal_cache import (
    CACHE_TTL,
    EMPTY_TTL,
    MAX_PURGE_BATCHES,
    MAX_PURGE_PER_WRITE,
    cache_key,
    cache_ttl,
    get_cached_hits,
    purge_all_expired,
    purge_expired,
    put_cached_hits,
    run_purge_forever,
)
from between_jobs.api.hiring_signals import Freshness, RawSearchHit

NOW = datetime(2026, 9, 19, 18, 0, 0, tzinfo=UTC)
DAY = date(2026, 9, 19)
HITS = [
    RawSearchHit("https://www.linkedin.com/posts/a-activity-1", "Title one", "Snippet one"),
    RawSearchHit("https://www.linkedin.com/posts/b-activity-2", "Title two", "Snippet two"),
]


def _supabase(rows: list[dict[str, Any]] | None = None) -> tuple[FakeSupabase, FakeTable]:
    table = FakeTable(rows, unique=[lambda r: (r["query_key"],)])
    return FakeSupabase({"hiring_signal_query_cache": table}), table


_ONE_HIT = {"v": 1, "hits": [{"url": "u", "title": "t", "snippet": "s"}]}


def _row(key: str, *, age: timedelta, payload: Any = None) -> dict[str, Any]:
    return {
        "id": f"id-{key}",
        "query_key": key,
        "provider": "firecrawl",
        "response_json": payload if payload is not None else {"v": 1, "hits": []},
        "created_at": (NOW - age).isoformat(),
    }


# ── keys ─────────────────────────────────────────────────────────────────


def test_the_key_is_a_fixed_length_hash_with_no_query_text_in_it() -> None:
    key = cache_key("firecrawl", 'site:linkedin.com/posts "acme"', "qdr:w", DAY)
    assert re.fullmatch(r"[0-9a-f]{64}", key)
    assert "acme" not in key


def test_the_key_is_stable_and_ignores_case_and_whitespace_in_the_query() -> None:
    a = cache_key("firecrawl", 'site:x  ("A" OR "b")', "qdr:w", DAY)
    b = cache_key("firecrawl", ' SITE:X ("a"   OR "B") ', "qdr:w", DAY)
    assert a == b == cache_key("firecrawl", 'site:x  ("A" OR "b")', "qdr:w", DAY)


@pytest.mark.parametrize(
    "different",
    [
        ("you_com", "q", "qdr:w", DAY),
        ("firecrawl", "other", "qdr:w", DAY),
        ("firecrawl", "q", "qdr:d", DAY),
        ("firecrawl", "q", "qdr:w", DAY + timedelta(days=1)),
    ],
)
def test_the_key_changes_with_provider_query_freshness_bucket_and_day(
    different: tuple[str, str, str, date],
) -> None:
    assert cache_key("firecrawl", "q", "qdr:w", DAY) != cache_key(*different)


# ── reading ──────────────────────────────────────────────────────────────


async def test_a_missing_row_is_a_miss() -> None:
    supabase, _ = _supabase()
    assert await get_cached_hits(as_client(supabase), "nope", now=NOW) is None


async def test_a_fresh_row_is_a_hit_that_returns_the_stored_hits() -> None:
    payload = {
        "v": 1,
        "hits": [{"url": h.url, "title": h.title, "snippet": h.snippet} for h in HITS],
    }
    supabase, _ = _supabase([_row("k", age=timedelta(hours=1), payload=payload)])
    assert await get_cached_hits(as_client(supabase), "k", now=NOW) == HITS


async def test_an_empty_answer_is_a_real_hit_not_a_miss() -> None:
    supabase, _ = _supabase([_row("k", age=timedelta(minutes=5))])
    assert await get_cached_hits(as_client(supabase), "k", now=NOW) == []


async def test_the_ttl_boundary_a_row_at_exactly_24_hours_is_expired() -> None:
    assert timedelta(hours=24) == CACHE_TTL
    just_inside, _ = _supabase([_row("k", age=CACHE_TTL - timedelta(seconds=1), payload=_ONE_HIT)])
    at_boundary, _ = _supabase([_row("k", age=CACHE_TTL, payload=_ONE_HIT)])
    past, _ = _supabase([_row("k", age=CACHE_TTL + timedelta(hours=5), payload=_ONE_HIT)])
    assert await get_cached_hits(as_client(just_inside), "k", now=NOW) == [
        RawSearchHit("u", "t", "s")
    ]
    assert await get_cached_hits(as_client(at_boundary), "k", now=NOW) is None
    assert await get_cached_hits(as_client(past), "k", now=NOW) is None


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "text",
        [],
        {},
        {"v": 2, "hits": []},
        {"v": 1},
        {"v": 1, "hits": "not a list"},
        {"v": 1, "hits": ["not a dict"]},
        {"v": 1, "hits": [{"url": "u", "title": "t"}]},
        {"v": 1, "hits": [{"url": "u", "title": 3, "snippet": "s"}]},
        {"v": 1, "hits": [{"url": None, "title": "t", "snippet": "s"}]},
    ],
)
async def test_a_corrupt_or_foreign_payload_is_a_miss_never_an_error(payload: Any) -> None:
    row = _row("k", age=timedelta(hours=1))
    row["response_json"] = payload
    supabase, _ = _supabase([row])
    assert await get_cached_hits(as_client(supabase), "k", now=NOW) is None


@pytest.mark.parametrize("created_at", [None, "yesterday", "2026-09-19T17:00:00", 12345])
async def test_an_unreadable_or_timezone_less_timestamp_is_a_miss(created_at: Any) -> None:
    row = _row("k", age=timedelta(hours=1))
    row["created_at"] = created_at
    supabase, _ = _supabase([row])
    assert await get_cached_hits(as_client(supabase), "k", now=NOW) is None


# ── writing ──────────────────────────────────────────────────────────────


async def test_a_write_stores_only_url_title_and_snippet_per_hit() -> None:
    supabase, table = _supabase()
    await put_cached_hits(as_client(supabase), "k", "firecrawl", HITS, now=NOW)

    (row,) = table.rows
    assert row["query_key"] == "k"
    assert row["provider"] == "firecrawl"
    assert set(row["response_json"]) == {"v", "hits"}
    assert all(set(hit) == {"url", "title", "snippet"} for hit in row["response_json"]["hits"])
    assert row["created_at"] == NOW.isoformat()
    # nothing parsed is stored: no species, author, activity id or age
    assert "activity_id" not in str(row["response_json"]).replace("activity-", "")
    assert await get_cached_hits(as_client(supabase), "k", now=NOW) == HITS


async def test_a_write_overwrites_an_expired_row_in_place() -> None:
    """A row past the TTL the reader asked for, but inside the hard ceiling, is
    a miss that the next write refreshes IN PLACE (same row, same id)."""
    supabase, table = _supabase([_row("k", age=timedelta(hours=5), payload=_ONE_HIT)])
    assert await get_cached_hits(as_client(supabase), "k", now=NOW, ttl=timedelta(hours=3)) is None

    await put_cached_hits(as_client(supabase), "k", "firecrawl", HITS, now=NOW)

    assert len(table.rows) == 1
    assert table.rows[0]["id"] == "id-k"  # the same row, refreshed
    assert await get_cached_hits(as_client(supabase), "k", now=NOW) == HITS


async def test_concurrent_writers_of_one_key_do_not_collide() -> None:
    supabase, table = _supabase()
    await asyncio.gather(
        put_cached_hits(as_client(supabase), "k", "firecrawl", HITS, now=NOW),
        put_cached_hits(as_client(supabase), "k", "firecrawl", HITS[:1], now=NOW),
        put_cached_hits(as_client(supabase), "k", "firecrawl", [], now=NOW),
    )
    assert [r["query_key"] for r in table.rows] == ["k"]
    assert await get_cached_hits(as_client(supabase), "k", now=NOW) is not None


# ── purging ──────────────────────────────────────────────────────────────


async def test_each_write_purges_a_bounded_batch_of_expired_rows_oldest_first() -> None:
    expired = [_row(f"old-{i:03d}", age=timedelta(hours=25, minutes=i)) for i in range(60)]
    fresh = [_row(f"fresh-{i}", age=timedelta(hours=1)) for i in range(5)]
    supabase, table = _supabase(expired + fresh)

    purged = await put_cached_hits(as_client(supabase), "new", "firecrawl", HITS, now=NOW)

    assert purged == MAX_PURGE_PER_WRITE == 25
    remaining = {r["query_key"] for r in table.rows}
    # older rows have larger `i`, so the 25 oldest are old-035 .. old-059
    assert {f"old-{i:03d}" for i in range(35, 60)}.isdisjoint(remaining)
    assert {f"old-{i:03d}" for i in range(35)} <= remaining
    assert {f"fresh-{i}" for i in range(5)} <= remaining
    assert "new" in remaining
    assert len(table.rows) == 60 - 25 + 5 + 1

    # bounded per write, not per table: the next write takes the next batch
    assert await put_cached_hits(as_client(supabase), "new2", "firecrawl", [], now=NOW) == 25
    assert await put_cached_hits(as_client(supabase), "new3", "firecrawl", [], now=NOW) == 10
    assert await put_cached_hits(as_client(supabase), "new4", "firecrawl", [], now=NOW) == 0


async def test_purge_never_touches_a_row_inside_the_ttl() -> None:
    supabase, table = _supabase([_row("edge", age=CACHE_TTL - timedelta(seconds=1))])
    assert await purge_expired(as_client(supabase), now=NOW) == 0
    assert len(table.rows) == 1


async def test_purge_on_an_empty_table_deletes_nothing() -> None:
    supabase, table = _supabase()
    assert await purge_expired(as_client(supabase), now=NOW) == 0
    assert not any(op == "delete" for op, _ in table.calls)


# ── LP-2: expiry is physical, not only logical ───────────────────────────


async def test_a_row_past_the_hard_ceiling_is_deleted_when_it_is_read() -> None:
    """The finding: `get_cached_hits` used to answer "miss" and leave the
    snippet text in the table, so with no later writes it stayed forever."""
    supabase, table = _supabase([_row("k", age=timedelta(days=30), payload=_ONE_HIT)])

    assert await get_cached_hits(as_client(supabase), "k", now=NOW) is None

    assert table.rows == []


async def test_a_row_inside_the_ceiling_but_past_the_asked_ttl_is_a_miss_that_is_kept() -> None:
    """It is refreshed in place by the write that follows, not deleted twice."""
    supabase, table = _supabase([_row("k", age=timedelta(hours=5), payload=_ONE_HIT)])

    assert await get_cached_hits(as_client(supabase), "k", now=NOW, ttl=timedelta(hours=3)) is None

    assert len(table.rows) == 1


async def test_a_quiet_period_still_ends_with_every_expired_row_removed_by_the_sweep() -> None:
    """No writes and no reads for 25 hours; then one sweep. More rows than one
    write's bounded purge (25) can take."""
    old = datetime(2020, 1, 1, tzinfo=UTC)
    rows = [
        {
            "id": f"id-{i}",
            "query_key": f"k{i}",
            "provider": "firecrawl",
            "response_json": _ONE_HIT,
            "created_at": old.isoformat(),
        }
        for i in range(60)
    ]
    supabase, table = _supabase([*rows, _row("fresh", age=timedelta(hours=1), payload=_ONE_HIT)])

    removed = await purge_all_expired(as_client(supabase), now=NOW)

    assert removed == 60
    assert [r["query_key"] for r in table.rows] == ["fresh"]
    assert MAX_PURGE_PER_WRITE == 25  # ... so one write's purge could not have


async def test_a_sweep_is_bounded_work_however_many_rows_have_piled_up() -> None:
    old = datetime(2020, 1, 1, tzinfo=UTC).isoformat()
    total = MAX_PURGE_BATCHES * MAX_PURGE_PER_WRITE + 100
    rows = [
        {"id": f"id-{i}", "query_key": f"k{i}", "response_json": {}, "created_at": old}
        for i in range(total)
    ]
    supabase, table = _supabase(rows)

    removed = await purge_all_expired(as_client(supabase), now=NOW)

    assert removed == MAX_PURGE_BATCHES * MAX_PURGE_PER_WRITE
    assert len(table.rows) == 100  # the next sweep continues


async def test_the_background_worker_sweeps_on_an_interval_without_any_request() -> None:
    old = datetime(2020, 1, 1, tzinfo=UTC).isoformat()
    supabase, table = _supabase(
        [{"id": "id-1", "query_key": "k", "response_json": {}, "created_at": old}]
    )

    task = asyncio.create_task(run_purge_forever(as_client(supabase), interval_seconds=0.01))
    try:
        for _ in range(100):
            if not table.rows:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert table.rows == []


async def test_a_failed_sweep_does_not_end_the_worker() -> None:
    old = datetime(2020, 1, 1, tzinfo=UTC).isoformat()
    supabase, table = _supabase(
        [{"id": "id-1", "query_key": "k", "response_json": {}, "created_at": old}]
    )
    table.fail_with = RuntimeError("database unreachable")

    task = asyncio.create_task(run_purge_forever(as_client(supabase), interval_seconds=0.01))
    try:
        await asyncio.sleep(0.05)  # several failed sweeps
        assert not task.done()
        table.fail_with = None
        for _ in range(100):
            if not table.rows:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert table.rows == []


# ── BC-7: a purge never deletes a row a concurrent request just refreshed ─


class _RefreshingQuery(Query):
    """A select whose result, once produced, is followed by ANOTHER request
    refreshing a row (an upsert overwrites `created_at`) -- before the caller
    gets to act on what it read."""

    def __init__(self, table: _RefreshAfterSelect) -> None:
        super().__init__(table, "select")
        self._refresh_table = table

    async def execute(self) -> Any:
        result = await super().execute()
        table = self._refresh_table
        if not table.refreshed and result.data:
            table.refreshed = True
            table.rows[0]["created_at"] = NOW.isoformat()
        return result


class _RefreshAfterSelect(FakeTable):
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        super().__init__(rows, unique=[lambda r: (r["query_key"],)])
        self.refreshed = False

    def select(self, *_columns: Any) -> Query:
        return _RefreshingQuery(self)


async def test_a_row_refreshed_between_the_select_and_the_delete_is_not_deleted() -> None:
    table = _RefreshAfterSelect([_row("k", age=timedelta(hours=30), payload=_ONE_HIT)])
    supabase = FakeSupabase({"hiring_signal_query_cache": table})

    await purge_expired(as_client(supabase), now=NOW)

    assert len(table.rows) == 1  # the fresh row survived
    assert await get_cached_hits(as_client(supabase), "k", now=NOW) == [RawSearchHit("u", "t", "s")]


# ── YH-10: how long an answer is served ──────────────────────────────────


def test_the_ttl_shrinks_with_the_window() -> None:
    assert cache_ttl(Freshness.DAY) == timedelta(hours=3)
    assert cache_ttl(Freshness.THREE_DAYS) == timedelta(hours=12)
    assert cache_ttl(Freshness.WEEK) == CACHE_TTL == timedelta(hours=24)
    assert all(cache_ttl(f) <= CACHE_TTL for f in Freshness)


async def test_an_answer_older_than_its_windows_ttl_is_a_miss() -> None:
    """A 24-hour search served a 23-hour-old answer would miss nearly the whole
    window it asked about."""
    supabase, _ = _supabase([_row("k", age=timedelta(hours=4), payload=_ONE_HIT)])
    day = cache_ttl(Freshness.DAY)

    assert await get_cached_hits(as_client(supabase), "k", now=NOW, ttl=day) is None
    assert await get_cached_hits(as_client(supabase), "k", now=NOW, ttl=cache_ttl(Freshness.WEEK))


async def test_an_empty_answer_is_served_only_for_the_empty_ttl() -> None:
    assert timedelta(hours=1) == EMPTY_TTL
    fresh, _ = _supabase([_row("k", age=timedelta(minutes=30))])
    stale, _ = _supabase([_row("k", age=timedelta(hours=2))])

    assert await get_cached_hits(as_client(fresh), "k", now=NOW) == []
    assert await get_cached_hits(as_client(stale), "k", now=NOW) is None
    # ... while a non-empty answer of the same age is still served
    kept, _ = _supabase([_row("k", age=timedelta(hours=2), payload=_ONE_HIT)])
    assert await get_cached_hits(as_client(kept), "k", now=NOW) == [RawSearchHit("u", "t", "s")]
