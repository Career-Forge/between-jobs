"""Tests for the two shared Supabase/Postgres resilience helpers
(code-review-fixes.md Phase 4a/4b) -- factored out after 4 near-identical
pagination loops and 2 near-identical retry loops were found duplicated
across this codebase."""

from __future__ import annotations

import asyncio

import pytest
from postgrest.exceptions import APIError

from between_jobs.api.supabase_helpers import fetch_all_pages, retry_on_statement_timeout


async def _no_sleep(_seconds: float) -> None:
    return


# ── fetch_all_pages ──────────────────────────────────────────────────────


async def test_fetch_all_pages_returns_everything_from_a_single_short_page() -> None:
    async def _page(start: int, end: int) -> list[int]:
        assert (start, end) == (0, 9)
        return [1, 2, 3]

    result = await fetch_all_pages(_page, page_size=10)
    assert result == [1, 2, 3]


async def test_fetch_all_pages_stops_on_a_short_page_after_full_pages() -> None:
    calls: list[tuple[int, int]] = []
    all_rows = list(range(25))

    async def _page(start: int, end: int) -> list[int]:
        calls.append((start, end))
        return all_rows[start : end + 1]

    result = await fetch_all_pages(_page, page_size=10)

    assert result == all_rows
    assert calls == [(0, 9), (10, 19), (20, 29)]


async def test_fetch_all_pages_handles_an_empty_result() -> None:
    async def _page(start: int, end: int) -> list[int]:
        return []

    assert await fetch_all_pages(_page, page_size=1000) == []


async def test_fetch_all_pages_stops_exactly_on_a_page_that_is_a_full_multiple() -> None:
    """A page that comes back exactly `page_size` long must trigger one
    more fetch (it might not be the last page) -- confirmed via a fixture
    where the true data is exactly one page long, so the second call
    correctly returns empty and the loop stops there."""
    calls = 0

    async def _page(start: int, end: int) -> list[int]:
        nonlocal calls
        calls += 1
        if start == 0:
            return list(range(10))
        return []

    result = await fetch_all_pages(_page, page_size=10)

    assert result == list(range(10))
    assert calls == 2


# ── retry_on_statement_timeout ───────────────────────────────────────────


def _timeout_error() -> APIError:
    return APIError({"message": "canceling statement due to statement timeout", "code": "57014"})


def _other_error() -> APIError:
    return APIError({"message": "relation does not exist", "code": "42P01"})


async def test_retry_on_statement_timeout_returns_the_result_on_first_success() -> None:
    async def op() -> str:
        return "ok"

    assert await retry_on_statement_timeout(op) == "ok"


async def test_retry_on_statement_timeout_retries_only_on_57014_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    attempts = 0

    async def op() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise _timeout_error()
        return "ok"

    result = await retry_on_statement_timeout(op, max_attempts=4)

    assert result == "ok"
    assert attempts == 3


async def test_retry_on_statement_timeout_raises_immediately_on_a_different_sqlstate() -> None:
    attempts = 0

    async def op() -> str:
        nonlocal attempts
        attempts += 1
        raise _other_error()

    try:
        await retry_on_statement_timeout(op, max_attempts=4)
        raise AssertionError("expected APIError")
    except APIError as e:
        assert e.code == "42P01"
    assert attempts == 1


async def test_retry_on_statement_timeout_raises_after_max_attempts_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    attempts = 0

    async def op() -> str:
        nonlocal attempts
        attempts += 1
        raise _timeout_error()

    try:
        await retry_on_statement_timeout(op, max_attempts=3)
        raise AssertionError("expected APIError")
    except APIError as e:
        assert e.code == "57014"
    assert attempts == 3
