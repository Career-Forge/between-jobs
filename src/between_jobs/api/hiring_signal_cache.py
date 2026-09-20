"""Hiring Signals P3 -- the shared, short-lived query cache
(`hiring_signal_query_cache`), a cost optimization and nothing else.

A search call spends credits on the USER'S OWN provider key. A second click
on the same application (or a second user's click on the same company) a few
hours later should not spend them again, so the provider's answer to a given
query is kept for a while. This is the ONLY place in the feature where
third-party snippet text is allowed to persist: nothing user-facing ever reads
it back as text, and no row outlives `CACHE_TTL` by more than one purge
interval (see "Lifetime"). `hiring_signal_saves` (the durable table) holds a
numeric post id and nothing else.

**What is stored.** The minimal normalized hit list -- `{url, title,
snippet}` per hit, each already clipped by `hiring_signal_search` -- not the
provider's whole response. **What is not stored: a parse.** The hits are
parsed AFTER they are read back, so a row can never carry an old parser's
opinion of a post (species, author, company match), and the recency window is
always enforced against the clock of the request that reads it, not of the one
that wrote it.

**Key.** A hash of `(provider, normalized provider query, freshness bucket,
UTC day)`. The provider is in the key because providers answer differently;
the freshness bucket is the provider-native parameter actually sent (two
windows that map to the same parameter are the same call); the UTC day keeps a
row from serving a whole rolling window's worth of stale "last day" answers
across midnight. The query is whitespace-collapsed and case-folded first.
Only a user who holds a key for a provider ever reads that provider's rows:
the cache never makes a provider available to someone who has not configured
it.

**What sharing tells a caller.** The key holds no user, so `cached: true` in a
response says that SOME user with a key for the same provider ran the same
normalized query inside the TTL. That is the price of a shared cache (the saving
is the point), and it is a real, disclosed side channel: on the standalone tab the
query is free typed text, so a caller can probe whether a given role and metro
was searched recently. The tab response's `cached` field is part of the API
contract, so dropping it -- or keying the tab's rows per user, which would give up
the sharing -- is recorded as an open contract decision, not made here.

**Lifetime.** Expiry is PHYSICAL, not only logical -- a row is not merely
ignored once it is too old, it is deleted, because the snippet text in it is
what the short TTL exists to bound:

- `cache_ttl(freshness)` is how long an answer is served: a day for the
  7-day window, but only a few hours for the 24-hour one (an answer half a day
  old is missing about half of what that window is for), and never more than
  `EMPTY_TTL` for an EMPTY answer (a provider that returned nothing once should
  be asked again soon, not trusted for a day). A row past it is a miss, and the
  write that follows overwrites it in place (an upsert on the unique key, so
  two concurrent writers cannot collide).
- `CACHE_TTL` (24 hours) is the hard ceiling. A row at or past it is deleted
  the moment it is READ, deleted by any write's bounded purge, and deleted by
  `run_purge_forever`, a background worker that sweeps every
  `PURGE_INTERVAL_SECONDS` whether or not anyone searches -- so a quiet period, a
  burst of more than `MAX_PURGE_PER_WRITE` expired rows, or the feature being
  switched off cannot leave third-party text at rest past the ceiling plus one
  interval.
- Every delete repeats the expiry test in its own `WHERE`, so a row that a
  concurrent request refreshed between the select and the delete is not
  deleted.

Service-role client only: the table has row-level security enabled and no
policies, so a client-side key can neither read nor write it.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from supabase import AsyncClient

from .hiring_signals import Freshness, RawSearchHit

CACHE_TTL = timedelta(hours=24)
"""The hard ceiling: no row is served, or kept, at or past this age."""
EMPTY_TTL = timedelta(hours=1)
"""How long an EMPTY answer is served."""
_WINDOW_TTL = {
    Freshness.DAY: timedelta(hours=3),
    Freshness.THREE_DAYS: timedelta(hours=12),
    Freshness.WEEK: CACHE_TTL,
}
MAX_PURGE_PER_WRITE = 25
MAX_PURGE_BATCHES = 40
"""At most this many `MAX_PURGE_PER_WRITE`-row batches in one sweep, so one
sweep is bounded work however many rows have piled up (the next sweep
continues)."""
PURGE_INTERVAL_SECONDS = 3600.0

_TABLE = "hiring_signal_query_cache"
_PAYLOAD_VERSION = 1
_KEY_SEPARATOR = "\x1f"


def cache_key(provider: str, query: str, freshness_param: str, day: date) -> str:
    """See the module docstring. A hex SHA-256: fixed length, no query text
    in the key column."""
    normalized_query = " ".join(query.split()).casefold()
    material = _KEY_SEPARATOR.join(
        (f"v{_PAYLOAD_VERSION}", provider, normalized_query, freshness_param, day.isoformat())
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def cache_ttl(freshness: Freshness) -> timedelta:
    """How long an answer for this window may be served (see "Lifetime")."""
    return _WINDOW_TTL[freshness]


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _hits_from_payload(payload: object) -> list[RawSearchHit] | None:
    """`None` for anything that is not a version-1 payload of well-formed
    hits: a corrupt or foreign row is a miss, never an error and never a
    partial result."""
    if not isinstance(payload, dict) or payload.get("v") != _PAYLOAD_VERSION:
        return None
    raw_hits = payload.get("hits")
    if not isinstance(raw_hits, list):
        return None
    hits: list[RawSearchHit] = []
    for raw in raw_hits:
        if not isinstance(raw, dict):
            return None
        url, title, snippet = raw.get("url"), raw.get("title"), raw.get("snippet")
        if not (isinstance(url, str) and isinstance(title, str) and isinstance(snippet, str)):
            return None
        hits.append(RawSearchHit(url=url, title=title, snippet=snippet))
    return hits


async def get_cached_hits(
    supabase: AsyncClient, key: str, *, now: datetime, ttl: timedelta = CACHE_TTL
) -> list[RawSearchHit] | None:
    """The cached hits for `key`, or `None` on a miss (no row, a row older
    than `ttl` -- or than `EMPTY_TTL`, for an empty answer -- or a row that is
    not a valid payload). An empty list is a real cached answer -- "the
    provider had nothing" is worth not re-buying for a little while. A row at
    or past the hard ceiling `CACHE_TTL` is DELETED here, not just ignored."""
    result = (
        await supabase.table(_TABLE)
        .select("id, response_json, created_at")
        .eq("query_key", key)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    row = cast(dict[str, Any], result.data[0])
    created_at = _parse_timestamp(row.get("created_at"))
    if created_at is None:
        return None
    age = now - created_at
    if age >= CACHE_TTL:
        await _delete_expired(supabase, [row["id"]], now=now)
        return None
    hits = _hits_from_payload(row.get("response_json"))
    if hits is None:
        return None
    if age >= (ttl if hits else min(ttl, EMPTY_TTL)):
        return None
    return hits


async def _delete_expired(supabase: AsyncClient, ids: Sequence[Any], *, now: datetime) -> None:
    """Deletes these rows only if they are STILL expired: a row another
    request refreshed since `ids` were chosen is left alone."""
    await (
        supabase.table(_TABLE)
        .delete()
        .in_("id", list(ids))
        .lt("created_at", (now - CACHE_TTL).isoformat())
        .execute()
    )


async def put_cached_hits(
    supabase: AsyncClient,
    key: str,
    provider: str,
    hits: Sequence[RawSearchHit],
    *,
    now: datetime,
) -> int:
    """Stores `hits` under `key` (overwriting an expired or concurrent row in
    place) and purges up to `MAX_PURGE_PER_WRITE` expired rows. Returns how
    many rows the purge deleted."""
    await (
        supabase.table(_TABLE)
        .upsert(
            {
                "query_key": key,
                "provider": provider,
                "response_json": {
                    "v": _PAYLOAD_VERSION,
                    "hits": [
                        {"url": hit.url, "title": hit.title, "snippet": hit.snippet} for hit in hits
                    ],
                },
                "created_at": now.isoformat(),
            },
            on_conflict="query_key",
        )
        .execute()
    )
    return await purge_expired(supabase, now=now)


async def purge_expired(supabase: AsyncClient, *, now: datetime) -> int:
    """Deletes at most `MAX_PURGE_PER_WRITE` rows older than `CACHE_TTL`,
    oldest first. PostgREST has no `DELETE ... LIMIT`, so the ids are selected
    first; a row that another request deletes in between simply is not there
    to delete, and one it refreshes in between is not deleted (the delete
    repeats the expiry test). Returns how many rows were selected."""
    expired = (
        await supabase.table(_TABLE)
        .select("id")
        .lt("created_at", (now - CACHE_TTL).isoformat())
        .order("created_at")
        .limit(MAX_PURGE_PER_WRITE)
        .execute()
    )
    ids = [cast(dict[str, Any], row)["id"] for row in expired.data]
    if not ids:
        return 0
    await _delete_expired(supabase, ids, now=now)
    return len(ids)


async def purge_all_expired(supabase: AsyncClient, *, now: datetime) -> int:
    """Sweeps expired rows in `MAX_PURGE_PER_WRITE`-row batches until none is
    left or `MAX_PURGE_BATCHES` batches have run (the hard cap of this loop;
    the next sweep continues). Returns how many rows were selected."""
    total = 0
    for _ in range(MAX_PURGE_BATCHES):
        batch = await purge_expired(supabase, now=now)
        total += batch
        if batch < MAX_PURGE_PER_WRITE:
            break
    return total


async def run_purge_forever(
    supabase: AsyncClient, *, interval_seconds: float = PURGE_INTERVAL_SECONDS
) -> None:
    """The background sweep behind "Lifetime": a plain sleep loop, the shape
    of `saved_search_matcher.run_matcher_forever` -- shutdown is
    `asyncio.CancelledError` out of the sleep. A sweep that fails (the
    database was unreachable) is skipped, never fatal: the next one runs a full
    interval later, and nothing here is on a request's path."""
    while True:
        with contextlib.suppress(Exception):  # a failed sweep must not end the worker
            await purge_all_expired(supabase, now=datetime.now(UTC))
        await asyncio.sleep(interval_seconds)
