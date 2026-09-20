"""Shared in-memory stand-ins for the Hiring Signals tests -- a Supabase
client whose tables behave like the real ones for the query shapes this
feature uses (select/eq/lt/in_/ilike/order/limit, insert, upsert, delete),
including unique-constraint violations with the real SQLSTATE, plus a
recording HTTP transport.

Not a general Supabase fake: it implements exactly the calls the Hiring
Signals modules make, and raises on anything else, so a new query shape shows
up as a loud failure here instead of a silently different fake behavior.

(P4 added `is_` -- `IS NULL`, what separates a standalone save from a
per-application one -- and `or_` of `<column>.ilike.<pattern>` clauses, the one
shape the registry's batch read uses, and a `hiring_signal_searches` table.)
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
from postgrest.exceptions import APIError

from between_jobs.api.hiring_signal_search import refuse_non_provider_hosts
from between_jobs.api.hiring_signal_service import search_application, search_tab
from between_jobs.api.hiring_signals import Freshness
from supabase import AsyncClient

FIXTURE_DIR = Path(__file__).parent / "golden" / "hiring_signals" / "provider_responses"
FIXTURE_NOW = datetime(2026, 9, 19, 18, 0, 0, tzinfo=UTC)
"""The moment the composed provider fixtures are set at: every activity id in
them encodes a post time relative to this instant, so a test that passes it
as `now` gets the same recency verdicts forever."""


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


Row = dict[str, Any]
UniqueKey = Callable[[Row], tuple[Any, ...] | None]
"""Returns the row's key under one unique index, or `None` when the row is
outside a partial index (so it is not constrained by it)."""


def unique_violation() -> APIError:
    return APIError(
        {
            "message": 'duplicate key value violates unique constraint "fake_key"',
            "code": "23505",
            "details": None,
            "hint": None,
        }
    )


def _comparable(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value
    return value


def _ilike(actual: Any, pattern: str) -> bool:
    regex = "^" + ".*".join(re.escape(part) for part in pattern.split("%")) + "$"
    return isinstance(actual, str) and re.match(regex, actual, re.IGNORECASE) is not None


class Query:
    def __init__(
        self, table: FakeTable, op: str, payload: Any = None, on_conflict: str = ""
    ) -> None:
        self._table = table
        self._op = op
        self._payload = payload
        self._on_conflict = on_conflict
        self._filters: list[tuple[str, str, Any]] = []
        self._order: list[tuple[str, bool]] = []
        self._limit: int | None = None

    # filters
    def eq(self, column: str, value: Any) -> Query:
        self._filters.append(("eq", column, value))
        return self

    def lt(self, column: str, value: Any) -> Query:
        self._filters.append(("lt", column, value))
        return self

    def in_(self, column: str, values: list[Any]) -> Query:
        self._filters.append(("in", column, list(values)))
        return self

    def ilike(self, column: str, pattern: str) -> Query:
        self._filters.append(("ilike", column, pattern))
        return self

    def is_(self, column: str, value: Any) -> Query:
        """`IS NULL` only (`value` is `None`) -- the one `is` this feature uses."""
        assert value is None, "the fake supports is_(column, None) only"
        self._filters.append(("is", column, None))
        return self

    def or_(self, filters: str) -> Query:
        """`or=(a.ilike.%x%,b.ilike.%y%)` -- only `ilike` clauses of the form
        `<column>.ilike.<pattern>`, and any other clause is a loud failure."""
        clauses: list[tuple[str, str]] = []
        for clause in filters.split(","):
            column, operator, pattern = clause.split(".", 2)
            assert operator == "ilike", f"the fake supports or_ of ilike clauses only: {clause!r}"
            clauses.append((column, pattern))
        self._filters.append(("or_ilike", "", clauses))
        return self

    def order(self, column: str, *, desc: bool = False) -> Query:
        self._order.append((column, desc))
        return self

    def limit(self, count: int) -> Query:
        self._limit = count
        return self

    def _matches(self, row: Row) -> bool:
        for op, column, value in self._filters:
            actual = row.get(column)
            if op == "eq" and actual != value:
                return False
            if op == "lt" and not (actual is not None and _comparable(actual) < _comparable(value)):
                return False
            if op == "in" and actual not in value:
                return False
            if op == "ilike" and not _ilike(actual, value):
                return False
            if op == "is" and actual is not None:
                return False
            if op == "or_ilike" and not any(_ilike(row.get(c), pat) for c, pat in value):
                return False
        return True

    async def execute(self) -> Any:
        table = self._table
        table.calls.append((self._op, list(self._filters)))
        if table.fail_with is not None:
            raise table.fail_with
        if self._op == "select":
            rows = [dict(r) for r in table.rows if self._matches(r)]
            for column, desc in reversed(self._order):
                rows.sort(key=lambda r, c=column: _comparable(r.get(c)), reverse=desc)  # type: ignore[misc]
            if self._limit is not None:
                rows = rows[: self._limit]
            return _Result(rows)
        if self._op == "insert":
            return _Result([table.insert_row(self._payload)])
        if self._op == "upsert":
            keys = [k.strip() for k in self._on_conflict.split(",") if k.strip()]
            existing = next(
                (r for r in table.rows if all(r.get(k) == self._payload.get(k) for k in keys)),
                None,
            )
            if existing is not None and keys:
                existing.update(self._payload)
                return _Result([dict(existing)])
            return _Result([table.insert_row(self._payload)])
        if self._op == "delete":
            doomed = [r for r in table.rows if self._matches(r)]
            for row in doomed:
                table.rows.remove(row)
            return _Result([dict(r) for r in doomed])
        raise AssertionError(f"unsupported fake op {self._op!r}")


class _Result:
    def __init__(self, data: list[Row]) -> None:
        self.data = data


class FakeTable:
    def __init__(
        self,
        rows: list[Row] | None = None,
        *,
        unique: list[UniqueKey] | None = None,
        fail_with: BaseException | None = None,
    ) -> None:
        self.rows: list[Row] = [dict(r) for r in (rows or [])]
        self.unique = unique or []
        self.fail_with = fail_with
        self.calls: list[tuple[str, list[tuple[str, str, Any]]]] = []
        self.insert_attempts = 0

    def insert_row(self, payload: Row) -> Row:
        self.insert_attempts += 1
        row = {"id": str(uuid.uuid4()), "created_at": datetime.now(UTC).isoformat(), **payload}
        for key_of in self.unique:
            key = key_of(row)
            if key is not None and any(key_of(existing) == key for existing in self.rows):
                raise unique_violation()
        self.rows.append(row)
        return dict(row)

    def select(self, *_columns: Any) -> Query:
        return Query(self, "select")

    def insert(self, payload: Row) -> Query:
        return Query(self, "insert", payload)

    def upsert(self, payload: Row, *, on_conflict: str = "") -> Query:
        return Query(self, "upsert", payload, on_conflict)

    def delete(self) -> Query:
        return Query(self, "delete")


def blind_first_look(table: FakeTable) -> None:
    """Makes the FIRST `select` on `table` answer with no rows (the query is still
    made and recorded), then behave normally. This is what a lost race looks like
    from the loser's side: it looked, saw nothing, and by the time it inserted,
    somebody else had -- so the insert hits the unique violation and the re-read
    finds the winner."""
    real_select = table.select
    state = {"blind": True}

    def select(*columns: Any) -> Query:
        query = real_select(*columns)
        if state["blind"]:
            state["blind"] = False
            real_execute = query.execute

            async def execute() -> Any:
                await real_execute()
                return _Result([])

            query.execute = execute  # type: ignore[method-assign]
        return query

    table.select = select  # type: ignore[method-assign]


class _Rpc:
    def __init__(self, data: Any) -> None:
        self._data = data

    async def execute(self) -> Any:
        return _Result(self._data)


class FakeSupabase:
    """`tables` maps a table name to its `FakeTable`. `decrypt_secret` (the
    only rpc the credential lookup needs) strips an `enc:` prefix, so a test
    stores `secret_encrypted="enc:my-key"` and the code under test receives
    `my-key`."""

    def __init__(self, tables: dict[str, FakeTable]) -> None:
        self.tables = tables

    def table(self, name: str) -> FakeTable:
        return self.tables[name]

    def rpc(self, fn: str, params: dict[str, Any]) -> _Rpc:
        if fn == "decrypt_secret":
            return _Rpc(str(params["p_ciphertext"]).removeprefix("enc:"))
        raise AssertionError(f"unexpected rpc {fn!r}")


def as_client(fake: FakeSupabase) -> AsyncClient:
    return cast(AsyncClient, fake)


def credential_row(provider: str, key: str, *, user_id: str) -> Row:
    return {
        "user_id": user_id,
        "service": "search",
        "provider": provider,
        "model": None,
        "base_url": None,
        "scope": None,
        "secret_encrypted": f"enc:{key}",
        "secret_2_encrypted": None,
    }


class CredentialTable(FakeTable):
    """`provider_credentials` needs the real query's select-then-eq chain to
    filter on user/service/provider, which `Query.eq` already does."""


class RecordingTransport(httpx.AsyncBaseTransport):
    """An httpx transport that records every request and answers from a
    handler `(request) -> httpx.Response`. With `allowed_hosts`, a request to
    any other host is an `AssertionError` -- a loud test failure, never a
    handler's quiet 404 -- and is not recorded as having succeeded."""

    def __init__(
        self,
        handler: Callable[[httpx.Request], httpx.Response],
        *,
        allowed_hosts: frozenset[str] | None = None,
    ) -> None:
        self._handler = handler
        self._allowed_hosts = allowed_hosts
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._allowed_hosts is not None and str(request.url.host) not in self._allowed_hosts:
            raise AssertionError(f"request to an unexpected host: {request.url.host}")
        self.requests.append(request)
        return self._handler(request)

    @property
    def hosts(self) -> list[str]:
        return [str(r.url.host) for r in self.requests]

    def json_bodies(self) -> list[Any]:
        return [json.loads(r.content) if r.content else None for r in self.requests]


USER = "00000000-0000-0000-0000-000000000001"
OTHER_USER = "00000000-0000-0000-0000-000000000002"
APP = "30000000-0000-0000-0000-000000000001"
SNAPSHOT = "20000000-0000-0000-0000-000000000002"

HOSTS = {
    "you_com": "ydc-index.io",
    "brave": "api.search.brave.com",
    "serper": "google.serper.dev",
    "firecrawl": "api.firecrawl.dev",
}
FIXTURE = load_fixture("firecrawl_company_posts.json")


# ── worlds ───────────────────────────────────────────────────────────────


class World:
    """A fake project: one user with one tracked application, some search
    keys, and a transport that answers per provider host."""

    def __init__(
        self,
        *,
        keys: dict[str, str] | None = None,
        company: str = "Stripe",
        title: str = "Software Engineer",
        location: str | None = "Remote",
        responses: dict[str, Any] | None = None,
        saves: list[dict[str, Any]] | None = None,
        registry_companies: list[dict[str, Any]] | None = None,
        registry_postings: list[dict[str, Any]] | None = None,
        cache_rows: list[dict[str, Any]] | None = None,
        searches: list[dict[str, Any]] | None = None,
    ) -> None:
        keys = {"firecrawl": "fc-key"} if keys is None else keys
        self.tables: dict[str, FakeTable] = {
            "applications": FakeTable(
                [
                    {
                        "id": APP,
                        "user_id": USER,
                        "job_id": "job-1",
                        "active_job_snapshot_id": SNAPSHOT,
                    }
                ]
            ),
            "job_snapshots": FakeTable(
                [
                    {
                        "id": SNAPSHOT,
                        "title": title,
                        "company_name": company,
                        "location_text": location,
                    }
                ]
            ),
            "provider_credentials": FakeTable(
                [credential_row(p, k, user_id=USER) for p, k in keys.items()]
            ),
            "hiring_signal_query_cache": FakeTable(
                cache_rows, unique=[lambda r: (r["query_key"],)]
            ),
            "hiring_signal_saves": FakeTable(saves),
            "job_registry_companies": FakeTable(registry_companies),
            "job_registry_postings": FakeTable(registry_postings),
            # the unique index is over (user, lower(query), coalesce(lower(location), ''))
            "hiring_signal_searches": FakeTable(
                searches,
                unique=[
                    lambda r: (r["user_id"], r["query"].lower(), (r.get("location") or "").lower())
                ],
            ),
        }
        self.supabase = FakeSupabase(self.tables)
        self.responses: dict[str, Any] = {
            "firecrawl": FIXTURE,
            "you_com": load_fixture("you_com_empty.json"),
            "brave": {"web": {"results": []}},
            "serper": {"organic": []},
            **(responses or {}),
        }
        self.transport = RecordingTransport(self._answer, allowed_hosts=frozenset(HOSTS.values()))
        # The client is built the way the app builds the one the routes are
        # handed: with the request hook that refuses any non-provider host.
        self.http = httpx.AsyncClient(
            transport=self.transport, event_hooks={"request": [refuse_non_provider_hosts]}
        )

    def _answer(self, request: httpx.Request) -> httpx.Response:
        provider = next(p for p, host in HOSTS.items() if host == request.url.host)
        planned = self.responses[provider]
        if isinstance(planned, Exception):
            raise planned
        if callable(planned):
            return planned(request)  # type: ignore[no-any-return]
        if isinstance(planned, httpx.Response):
            return planned
        return httpx.Response(200, json=planned, request=request)

    @property
    def providers_called(self) -> list[str]:
        by_host = {host: provider for provider, host in HOSTS.items()}
        return [by_host[host] for host in self.transport.hosts]

    async def search(self, freshness: Freshness = Freshness.WEEK, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("now", FIXTURE_NOW)
        return await search_application(
            as_client(self.supabase), self.http, USER, APP, freshness=freshness, **kwargs
        )

    async def tab_search(
        self,
        query: str = "software engineer",
        location: str | None = None,
        freshness: Freshness = Freshness.WEEK,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """The standalone tab's search (P4), as `USER`."""
        kwargs.setdefault("now", FIXTURE_NOW)
        return await search_tab(
            as_client(self.supabase),
            self.http,
            USER,
            query=query,
            location=location,
            freshness=freshness,
            **kwargs,
        )


def registry_kwargs(*postings: tuple[str, str | None]) -> dict[str, Any]:
    """Kwargs for a `World` whose registry tracks Stripe with these
    `(title, location)` postings."""
    return {
        "registry_companies": [{"id": "co-1", "name": "Stripe, Inc."}],
        "registry_postings": [
            {
                "id": f"post-{i}",
                "company_id": "co-1",
                "title": title,
                "location": location,
                "status": "active",
                "first_seen": f"2026-09-1{i}T00:00:00+00:00",
            }
            for i, (title, location) in enumerate(postings)
        ],
    }


# ── composing provider results ───────────────────────────────────────────


def activity_id_at(age_hours: float, *, sequence: int = 1234567) -> str:
    """A synthetic LinkedIn activity id whose encoded post time is
    `age_hours` before `FIXTURE_NOW` (id = `(milliseconds << 22) | sequence`,
    the layout the parser decodes). A negative age is a time in the future."""
    millis = int((FIXTURE_NOW - timedelta(hours=age_hours)).timestamp() * 1000)
    return str((millis << 22) | sequence)


def post_entry(
    slug: str, title: str, description: str, *, age_hours: float = 30, sequence: int = 1234567
) -> dict[str, Any]:
    """One Firecrawl `web` entry for a `/posts/` result. `slug` is the
    `<handle>_<topic>` part of the url; the caller supplies the person and
    company names, so every one is synthetic by construction."""
    activity = activity_id_at(age_hours, sequence=sequence)
    return {
        "url": f"https://www.linkedin.com/posts/{slug}-activity-{activity}-AbCd",
        "title": title,
        "description": description,
    }


def firecrawl_body(*entries: dict[str, Any]) -> dict[str, Any]:
    """A Firecrawl v2 `search` response body holding these entries."""
    return {"success": True, "data": {"web": list(entries)}, "creditsUsed": 4, "id": "test"}
