"""Two small Supabase/Postgres resilience patterns this codebase kept
re-deriving independently, factored out after a code-review pass found
four near-identical copies of one and two of the other:

`fetch_all_pages` -- the PostgREST-1000-row-default pagination guard.
This exact bug (an unranged `.select()` silently capping at PostgREST's
own default row limit) has bitten this codebase three separate times
(P1's seed import, P5d's gazetteer load, Job Finder P9's saved-search
matcher), each fixed locally with its own private loop. A fifth "fetch
everything" caller has nothing forcing it to remember the guard unless
it's centralized.

`retry_on_statement_timeout` -- the bounded, exponential-backoff retry
around a real Postgres statement timeout (57014), first hit importing
the ~90k-row job registry (P1) and again on the poller's own per-tick
upserts (P2). Retry is safe here specifically because every wrapped
write is an idempotent upsert on a natural-key/on-conflict target --
callers must only use this for operations with that property.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from postgrest.exceptions import APIError

_STATEMENT_TIMEOUT_SQLSTATE = "57014"


async def fetch_all_pages[T](
    fetch_page: Callable[[int, int], Awaitable[list[T]]],
    *,
    page_size: int = 1000,
) -> list[T]:
    """`fetch_page(start, end)` must return the `.range(start, end)` page
    of a caller-owned query -- this owns only the accumulate-until-a-
    short-page-confirms-nothing's-left loop, not the query itself, so
    each caller keeps its own `.select()`/`.order()`/`.eq()` chain
    unchanged."""
    rows: list[T] = []
    start = 0
    while True:
        page = await fetch_page(start, start + page_size - 1)
        rows.extend(page)
        if len(page) < page_size:
            return rows
        start += page_size


async def retry_on_statement_timeout[T](
    op: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 4,
) -> T:
    """Retries `op()` with exponential backoff (`2**attempt` seconds)
    only on a real Postgres statement timeout (57014) -- every other
    `APIError`, or a `57014` on the final attempt, still raises. Hard-
    capped at `max_attempts`, matching this project's own "every retry
    has a hard cap" rule -- callers must only wrap an idempotent
    operation (an upsert on a natural-key/on-conflict target), since a
    retried call can otherwise double-apply."""
    for attempt in range(1, max_attempts + 1):
        try:
            return await op()
        except APIError as e:
            if e.code != _STATEMENT_TIMEOUT_SQLSTATE or attempt == max_attempts:
                raise
            await asyncio.sleep(2**attempt)
    raise AssertionError("unreachable")  # loop always returns or raises
