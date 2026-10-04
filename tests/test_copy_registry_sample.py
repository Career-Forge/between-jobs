"""Tests for the registry sampler (launch plan P2.8): `scripts/copy_registry_sample.py`.

The fake below behaves like PostgREST where it matters here: an unranged select is capped at
1,000 rows (the bug that bit three earlier importers), `range`/`limit`/`eq`/`order` work, a
select returns only the columns it asked for, and an upsert merges on its natural key.
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from postgrest.exceptions import APIError
from postgrest.types import ReturnMethod

from scripts import copy_registry_sample as sampler
from scripts.copy_registry_sample import (
    SampleRefusal,
    allocate_per_type,
    board_for,
    check_direction,
    check_guards,
    company_to_target_row,
    copy_sample,
    pick_sample,
    project_ref,
)

_NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)
_SOURCE_URL = "https://srcprojectref.supabase.co"
_TARGET_URL = "https://dstprojectref.supabase.co"
_TARGET_REF = "dstprojectref"

_KEY_COLUMNS = {
    "job_registry_companies": ("ats_type", "slug", "api_base"),
    "job_registry_postings": ("board", "external_id"),
}


# -- a PostgREST-shaped in-memory fake ------------------------------------------------------


class _Query:
    def __init__(self, table: _Table) -> None:
        self._table = table
        self._columns: list[str] | None = None
        self._count = False
        self._head = False
        self._filters: list[tuple[str, Any]] = []
        self._order: tuple[str, bool] | None = None
        self._limit: int | None = None
        self._range: tuple[int, int] | None = None
        self._upsert: tuple[list[dict[str, Any]], str] | None = None

    def select(self, columns: str = "*", count: Any = None, head: bool = False) -> _Query:
        self._columns = None if columns.strip() == "*" else [c.strip() for c in columns.split(",")]
        self._count = count is not None
        self._head = head
        return self

    def eq(self, column: str, value: Any) -> _Query:
        self._filters.append((column, value))
        return self

    def order(self, column: str, desc: bool = False) -> _Query:
        self._order = (column, desc)
        return self

    def limit(self, n: int) -> _Query:
        self._limit = n
        return self

    def range(self, start: int, end: int) -> _Query:
        self._range = (start, end)
        return self

    def upsert(self, rows: list[dict[str, Any]], on_conflict: str, returning: Any = None) -> _Query:
        self._upsert = (rows, on_conflict)
        self._table.client.returnings.append(returning)
        return self

    async def execute(self) -> Any:
        client = self._table.client
        client.calls.append((self._table.name, "upsert" if self._upsert else "select"))
        if self._upsert is not None:
            rows, on_conflict = self._upsert
            client.upsert_calls.append((self._table.name, rows))
            self._table.apply_upsert(rows, on_conflict)
            return SimpleNamespace(data=rows, count=None)

        matched = [r for r in self._table.rows if all(r.get(c) == v for c, v in self._filters)]
        if self._order is not None:
            column, desc = self._order
            matched.sort(key=lambda r: (r.get(column) is None, r.get(column)), reverse=desc)
        total = len(matched)
        if self._range is not None:
            matched = matched[self._range[0] : self._range[1] + 1]
        elif self._limit is not None:
            matched = matched[: self._limit]
        else:
            matched = matched[:1000]  # PostgREST's default cap on an unranged select
        if self._columns is not None:
            matched = [{c: r[c] for c in self._columns} for r in matched]
        count = total if self._count else None
        return SimpleNamespace(data=[] if self._head else matched, count=count)


class _Table:
    def __init__(self, client: _FakeClient, name: str) -> None:
        self.client = client
        self.name = name
        self.rows: list[dict[str, Any]] = []
        self._next_id = 1

    def apply_upsert(self, incoming: list[dict[str, Any]], on_conflict: str) -> None:
        keys = tuple(on_conflict.split(","))
        assert keys == _KEY_COLUMNS[self.name], f"unexpected on_conflict {on_conflict!r}"
        batch_keys = [tuple(row[k] for k in keys) for row in incoming]
        if len(set(batch_keys)) != len(batch_keys):
            raise APIError(
                {
                    "message": "ON CONFLICT DO UPDATE command cannot affect row a second time",
                    "code": "21000",
                    "hint": None,
                    "details": None,
                }
            )
        for row in incoming:
            key = tuple(row[k] for k in keys)
            existing = next((r for r in self.rows if tuple(r.get(k) for k in keys) == key), None)
            if existing is not None:
                existing.update(row)
            else:
                self.rows.append({"id": f"{self.name}-{self._next_id}", **row})
                self._next_id += 1


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.upsert_calls: list[tuple[str, list[dict[str, Any]]]] = []
        self.returnings: list[Any] = []
        self._tables = {
            name: _Table(self, name) for name in ("job_registry_companies", "job_registry_postings")
        }

    def table(self, name: str) -> _Query:
        return _Query(self._tables[name])

    def rows(self, name: str) -> list[dict[str, Any]]:
        return self._tables[name].rows

    def add_company(self, ats_type: str, slug: str, **overrides: Any) -> dict[str, Any]:
        row: dict[str, Any] = {
            "name": slug.title(),
            "ats_type": ats_type,
            "slug": slug,
            "api_base": "",
            "is_active": True,
            "poll_interval": "06:00:00",
            "next_poll_at": "2026-09-01T00:00:00+00:00",
            "last_polled_at": "2026-09-27T03:00:00+00:00",
            "etag": 'W/"abc"',
            "last_modified": "Sun, 27 Sep 2026 03:00:00 GMT",
            "consecutive_failures": 0,
            "tier": "hot",
            "relevant_yield": 7,
            "tier_weight": 0.5,
            "sweep_started_at": "2026-09-26T00:00:00+00:00",
            "sweep_posting_count": 120,
        }
        row.update(overrides)
        self._tables["job_registry_companies"].apply_upsert([row], "ats_type,slug,api_base")
        return self._tables["job_registry_companies"].rows[-1]

    def add_posting(self, company: dict[str, Any], external_id: str, **overrides: Any) -> None:
        row: dict[str, Any] = {
            "company_id": company["id"],
            "board": board_for(company),
            "external_id": external_id,
            "title": f"Engineer {external_id}",
            "jd_text": "Build things.",
            "location": "Remote",
            "remote": True,
            "apply_url": f"https://example.test/{external_id}",
            "posted_at": "2026-09-20T00:00:00+00:00",
            "status": "active",
            "first_seen": "2026-09-20T00:00:00+00:00",
            "last_seen": "2026-09-27T00:00:00+00:00",
            "closed_at": None,
            "salary_min": None,
            "salary_max": None,
            "salary_currency": None,
            "salary_period": None,
            "sponsorship_signal": "unknown",
            "extracted_at": None,
            "extraction_version": None,
        }
        row.update(overrides)
        self._tables["job_registry_postings"].apply_upsert([row], "board,external_id")


def _registry(sizes: dict[str, int], postings_each: int = 3) -> _FakeClient:
    client = _FakeClient()
    for ats_type, count in sizes.items():
        for i in range(count):
            company = client.add_company(ats_type, f"{ats_type}-{i}")
            for j in range(postings_each):
                client.add_posting(company, f"{company['slug']}-job-{j}")
    return client


async def _copy(source: _FakeClient, target: _FakeClient, **kwargs: Any) -> sampler.SampleSummary:
    params: dict[str, Any] = {
        "source_url": _SOURCE_URL,
        "target_url": _TARGET_URL,
        "target_ref": _TARGET_REF,
        "now": _NOW,
    }
    params.update(kwargs)
    return await copy_sample(source, target, **params)  # type: ignore[arg-type]


# -- guards ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "ref"),
    [
        ("https://abcdefghij.supabase.co", "abcdefghij"),
        ("https://ABCDEFGHIJ.supabase.co/", "abcdefghij"),
        ("https://abcdefghij.supabase.co/rest/v1", "abcdefghij"),
        ("http://127.0.0.1:54321", "local"),
        ("http://localhost:54321", "local"),
    ],
)
def test_project_ref_names_the_project(url: str, ref: str) -> None:
    assert project_ref(url) == ref


@pytest.mark.parametrize(
    "url",
    [
        "http://abcdefghij.supabase.co",  # hosted projects are https only
        "https://abcdefghij.supabase.co.evil.test",
        "https://abcdefghij.evil.test",  # three labels, but not a supabase.co project
        "https://abcdefghij.supabase.com",  # the wrong suffix
        "https://evil.test/abcdefghij.supabase.co",
        "https://db.abcdefghij.supabase.co",  # not the API host: can't be named as a ref
        "https://\uff41bcdefghij.supabase.co",  # a fullwidth 'a': the client normalizes it away
        "https://a_cdefghij.supabase.co",  # not a ref label
        "https://example.com",
        "not a url",
        "",
    ],
)
def test_project_ref_refuses_a_destination_it_cannot_name(url: str) -> None:
    with pytest.raises(SampleRefusal):
        project_ref(url)


def test_the_target_may_not_be_the_source() -> None:
    with pytest.raises(SampleRefusal, match="the target is the source"):
        check_guards(_SOURCE_URL, _SOURCE_URL, "srcprojectref")


def test_the_target_may_not_be_the_source_under_another_spelling() -> None:
    with pytest.raises(SampleRefusal, match="the target is the source"):
        check_guards("https://srcprojectref.supabase.co", "https://SRCPROJECTREF.supabase.co/", "x")


def test_the_target_ref_must_be_typed_out_and_match() -> None:
    with pytest.raises(SampleRefusal, match="does not match"):
        check_guards(_SOURCE_URL, _TARGET_URL, "someotherref")
    check_guards(_SOURCE_URL, _TARGET_URL, _TARGET_REF)  # the matching ref passes


def test_a_local_target_is_named_local() -> None:
    check_guards(_SOURCE_URL, "http://127.0.0.1:54321", "local")
    with pytest.raises(SampleRefusal, match="does not match"):
        check_guards(_SOURCE_URL, "http://127.0.0.1:54321", _TARGET_REF)


def test_a_sample_never_flows_into_a_bigger_registry() -> None:
    check_direction(16_000, 500)
    check_direction(500, 500)
    with pytest.raises(SampleRefusal, match="swapped"):
        check_direction(500, 16_000)


# -- sampling -------------------------------------------------------------------------------


def test_every_type_gets_its_floor_before_the_big_ones_take_the_rest() -> None:
    alloc = allocate_per_type({"greenhouse": 5000, "lever": 2000, "smartrecruiters": 33}, 100, 5)

    assert alloc["smartrecruiters"] >= 5
    assert alloc["lever"] >= 5
    assert sum(alloc.values()) == 100
    assert alloc["greenhouse"] > alloc["lever"] > alloc["smartrecruiters"]


def test_a_type_smaller_than_the_floor_gives_everything_it_has() -> None:
    alloc = allocate_per_type({"amazon": 1, "apple": 1, "greenhouse": 50}, 20, 5)

    assert alloc["amazon"] == 1
    assert alloc["apple"] == 1
    assert alloc["greenhouse"] == 18


def test_a_total_smaller_than_the_number_of_types_still_spreads_across_them() -> None:
    sizes = {"a": 10, "b": 10, "c": 10, "d": 10}

    alloc = allocate_per_type(sizes, 3, 5)

    assert sum(alloc.values()) == 3
    assert alloc == {"a": 1, "b": 1, "c": 1, "d": 0}  # name order, and the same every time


def test_the_allocation_never_exceeds_a_type_or_the_total() -> None:
    rng = random.Random(7)
    for _ in range(300):
        sizes = {f"t{i}": rng.randint(0, 400) for i in range(rng.randint(0, 15))}
        total = rng.randint(0, 600)
        floor = rng.randint(0, 8)

        alloc = allocate_per_type(sizes, total, floor)

        assert all(0 <= n <= sizes[t] for t, n in alloc.items())
        assert sum(alloc.values()) <= total
        assert sum(alloc.values()) == min(total, sum(sizes.values()))
        # the same input always gives the same answer
        assert alloc == allocate_per_type(sizes, total, floor)


def test_the_same_seed_picks_the_same_companies_and_another_seed_picks_others() -> None:
    companies = [{"ats_type": "greenhouse", "slug": f"c{i}", "api_base": ""} for i in range(200)]

    first = pick_sample(companies, total=20, min_per_type=5, seed=1)
    again = pick_sample(companies, total=20, min_per_type=5, seed=1)
    other = pick_sample(companies, total=20, min_per_type=5, seed=2)

    assert [c["slug"] for c in first] == [c["slug"] for c in again]
    assert [c["slug"] for c in first] != [c["slug"] for c in other]


# -- row mapping ----------------------------------------------------------------------------


def test_a_copied_company_keeps_its_identity_and_starts_polling_over() -> None:
    source = _FakeClient()
    company = source.add_company("greenhouse", "acme", tier="hot", tier_weight=0.9)

    row = company_to_target_row(company, now=_NOW)

    assert (row["name"], row["ats_type"], row["slug"], row["api_base"]) == (
        "Acme",
        "greenhouse",
        "acme",
        "",
    )
    assert (row["tier"], row["tier_weight"], row["poll_interval"]) == ("hot", 0.9, "06:00:00")
    assert row["etag"] is None
    assert row["last_modified"] is None
    assert row["last_polled_at"] is None
    assert row["next_poll_at"] == _NOW.isoformat()
    assert row["consecutive_failures"] == 0
    assert row["sweep_started_at"] is None
    assert row["sweep_posting_count"] == 0
    assert "id" not in row
    assert "created_at" not in row


# -- copying --------------------------------------------------------------------------------


async def test_a_sample_covers_every_type_and_caps_the_postings_per_company() -> None:
    source = _registry({"greenhouse": 30, "lever": 12, "amazon": 1}, postings_each=6)
    target = _FakeClient()

    summary = await _copy(source, target, companies=20, postings_per_company=4, min_per_type=2)

    copied = target.rows("job_registry_companies")
    assert len(copied) == 20
    assert {c["ats_type"] for c in copied} == {"greenhouse", "lever", "amazon"}
    assert summary.companies == 20
    assert summary.companies_per_type["amazon"] == 1
    per_board: dict[str, int] = {}
    for posting in target.rows("job_registry_postings"):
        per_board[posting["board"]] = per_board.get(posting["board"], 0) + 1
    assert per_board and max(per_board.values()) == 4
    assert summary.postings == len(target.rows("job_registry_postings")) == 20 * 4
    assert "greenhouse" in str(summary)


async def test_polling_state_is_reset_not_copied() -> None:
    source = _registry({"greenhouse": 3})
    target = _FakeClient()

    await _copy(source, target)

    for company in target.rows("job_registry_companies"):
        assert company["etag"] is None
        assert company["last_polled_at"] is None
        assert company["consecutive_failures"] == 0
        assert company["next_poll_at"] == _NOW.isoformat()
        assert company["tier"] == "hot"  # scheduling weight is kept
        assert company["is_active"] is True


async def test_inactive_and_failing_companies_are_not_sampled() -> None:
    source = _registry({"greenhouse": 3})
    source.add_company("greenhouse", "dormant", is_active=False)
    source.add_company("greenhouse", "failing", consecutive_failures=4)
    target = _FakeClient()

    summary = await _copy(source, target)

    slugs = {c["slug"] for c in target.rows("job_registry_companies")}
    assert "dormant" not in slugs
    assert "failing" not in slugs
    assert summary.eligible_companies == 3


async def test_only_active_postings_of_the_sampled_companies_are_copied() -> None:
    source = _FakeClient()
    acme = source.add_company("greenhouse", "acme")
    other = source.add_company("greenhouse", "other")
    # the closed posting comes first, so a read that forgot the status filter would return it
    # inside the cap
    source.add_posting(acme, "acme-closed", status="closed")
    for i in range(5):
        source.add_posting(acme, f"acme-{i}")
    source.add_posting(other, "other-1")
    target = _FakeClient()

    await _copy(source, target, companies=2, postings_per_company=2, min_per_type=1)

    postings = target.rows("job_registry_postings")
    external_ids = {p["external_id"] for p in postings}
    assert len(postings) == 3  # two of acme's five, and other's one
    assert "acme-closed" not in external_ids
    assert external_ids <= {f"acme-{i}" for i in range(5)} | {"other-1"}
    assert all(p["status"] == "active" and p["closed_at"] is None for p in postings)


async def test_the_postings_read_never_sorts() -> None:
    """`order by last_seen` over a board's active rows reads all of them first: 16,372 rows and
    3.9 s for the Amazon board on prod. The read must stay a bare filter plus limit."""
    source = _FakeClient()
    acme = source.add_company("greenhouse", "acme")
    for i in range(5):
        source.add_posting(acme, f"acme-{i}")
    target = _FakeClient()
    orders: list[str] = []
    real_order = _Query.order

    def spy(self: _Query, column: str, desc: bool = False) -> _Query:
        if self._table.name == "job_registry_postings":
            orders.append(column)
        return real_order(self, column, desc)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(_Query, "order", spy)
        await _copy(source, target, companies=1, postings_per_company=2, min_per_type=1)

    assert orders == []
    assert len(target.rows("job_registry_postings")) == 2


async def test_postings_point_at_the_targets_own_company_ids() -> None:
    source = _registry({"greenhouse": 4, "workday": 2})
    # a company in the target that the sample does not touch, so its ids are not 1..n
    target = _FakeClient()
    target.add_company("lever", "preexisting")
    await _copy(source, target)

    company_by_id = {c["id"]: c for c in target.rows("job_registry_companies")}
    postings = target.rows("job_registry_postings")
    assert postings
    for posting in postings:
        company = company_by_id[posting["company_id"]]
        assert posting["board"] == board_for(company)
        assert posting["company_id"] != "job_registry_companies-1"  # not the preexisting row


async def test_a_second_run_changes_nothing_and_duplicates_nothing() -> None:
    source = _registry({"greenhouse": 8, "lever": 4})
    target = _FakeClient()

    await _copy(source, target, companies=10, min_per_type=2)
    first_companies = [dict(r) for r in target.rows("job_registry_companies")]
    first_postings = [dict(r) for r in target.rows("job_registry_postings")]
    await _copy(source, target, companies=10, min_per_type=2)

    assert target.rows("job_registry_companies") == first_companies
    assert target.rows("job_registry_postings") == first_postings


async def test_the_source_is_never_written() -> None:
    source = _registry({"greenhouse": 5})
    target = _FakeClient()

    await _copy(source, target)

    assert source.upsert_calls == []


async def test_a_dry_run_reads_and_reports_but_writes_nothing() -> None:
    source = _registry({"greenhouse": 5, "lever": 3})
    target = _FakeClient()

    summary = await _copy(source, target, dry_run=True)

    assert target.upsert_calls == []
    assert target.rows("job_registry_companies") == []
    assert summary.dry_run is True
    assert summary.companies == 8
    assert str(summary).startswith("[DRY RUN]")


async def test_companies_only_when_postings_per_company_is_zero() -> None:
    source = _registry({"greenhouse": 4})
    target = _FakeClient()

    summary = await _copy(source, target, postings_per_company=0)

    assert len(target.rows("job_registry_companies")) == 4
    assert target.rows("job_registry_postings") == []
    assert ("job_registry_postings", "select") not in source.calls
    assert summary.companies_without_postings == 4


async def test_a_registry_bigger_than_one_page_is_read_in_full() -> None:
    """Regression for the bug three earlier importers hit: an unranged select silently stops at
    PostgREST's 1,000-row default, so a bigger registry would be sampled from its first page."""
    source = _FakeClient()
    for i in range(1300):
        source.add_company("greenhouse", f"company-{i:04d}")
    target = _FakeClient()

    summary = await _copy(source, target, companies=1300, postings_per_company=0)

    assert summary.eligible_companies == 1300
    assert len(target.rows("job_registry_companies")) == 1300


async def test_the_target_registry_is_read_in_full_when_resolving_ids() -> None:
    source = _registry({"greenhouse": 5}, postings_each=1)
    target = _FakeClient()
    for i in range(1100):  # more rows than one unranged select returns
        target.add_company("lever", f"existing-{i:04d}")
    # keep the sample smaller than the target by making the source bigger
    for i in range(1100):
        source.add_company("lever", f"src-{i:04d}", is_active=False)

    await _copy(source, target, companies=5, postings_per_company=1, min_per_type=5)

    assert len(target.rows("job_registry_postings")) == 5  # every posting found its company


# -- refusals happen before anything is touched ---------------------------------------------


async def test_a_refused_pair_touches_neither_database() -> None:
    source = _registry({"greenhouse": 3})
    target = _FakeClient()

    with pytest.raises(SampleRefusal):
        await _copy(source, target, target_url=_SOURCE_URL, target_ref="srcprojectref")
    with pytest.raises(SampleRefusal):
        await _copy(source, target, target_ref="wrongref")

    assert source.calls == []
    assert target.calls == []


async def test_a_swapped_pair_is_refused_before_any_write() -> None:
    big = _registry({"greenhouse": 50})
    small = _FakeClient()
    small.add_company("greenhouse", "tiny")

    with pytest.raises(SampleRefusal, match="swapped"):
        await _copy(small, big)  # source is the small registry, target the big one

    assert big.upsert_calls == []


@pytest.mark.parametrize("companies", [0, -1, 2001])
async def test_the_company_count_is_bounded(companies: int) -> None:
    with pytest.raises(SampleRefusal, match="--companies"):
        await _copy(_FakeClient(), _FakeClient(), companies=companies)


@pytest.mark.parametrize("postings", [-1, 201])
async def test_the_postings_per_company_count_is_bounded(postings: int) -> None:
    with pytest.raises(SampleRefusal, match="--postings-per-company"):
        await _copy(_FakeClient(), _FakeClient(), postings_per_company=postings)


# -- a slow target ---------------------------------------------------------------------------


async def test_a_statement_timeout_on_a_batch_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_wait(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    source = _registry({"greenhouse": 3}, postings_each=2)
    target = _FakeClient()
    real_execute = _Query.execute
    failures = {"left": 1}

    async def flaky(self: _Query) -> Any:
        is_posting_write = self._upsert is not None and self._table.name == "job_registry_postings"
        if is_posting_write and failures["left"] > 0:
            failures["left"] -= 1
            raise APIError({"message": "timeout", "code": "57014", "hint": None, "details": None})
        return await real_execute(self)

    monkeypatch.setattr(_Query, "execute", flaky)

    await _copy(source, target)

    assert failures["left"] == 0
    assert len(target.rows("job_registry_postings")) == 6


# -- review follow-ups: spellings, batching, api_base, re-runs, concurrency, counters -----------


def test_a_fullwidth_spelling_of_the_source_host_cannot_dodge_the_same_project_guard() -> None:
    """The HTTP client normalizes a fullwidth host back to the real one, so comparing refs as
    typed would let a target that IS the source through."""
    with pytest.raises(SampleRefusal):
        check_guards("https://abc.supabase.co", "https://\uff41bc.supabase.co", "\uff41bc")


async def test_upserts_do_not_ask_for_the_rows_back() -> None:
    """The default echoes every written row, job description and tsvector included."""
    source = _registry({"greenhouse": 3})
    target = _FakeClient()

    await _copy(source, target)

    assert target.returnings
    assert all(r == ReturnMethod.minimal for r in target.returnings)


async def test_a_company_the_paging_returns_twice_is_written_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Offset paging over a registry being written to can repeat a row at a page boundary;
    two identical rows in one upsert batch make Postgres reject the whole batch (21000)."""
    source = _FakeClient()
    for i in range(1300):
        source.add_company("greenhouse", f"company-{i:04d}")
    real = _Query.execute

    async def repeat_the_boundary_row(self: _Query) -> Any:
        if self._table.name == "job_registry_companies" and self._range and self._range[0] > 0:
            self._range = (self._range[0] - 1, self._range[1])  # the previous page's last row again
        return await real(self)

    monkeypatch.setattr(_Query, "execute", repeat_the_boundary_row)
    target = _FakeClient()

    summary = await _copy(source, target, companies=1300, postings_per_company=0)

    assert summary.eligible_companies == 1300
    assert len(target.rows("job_registry_companies")) == 1300


async def test_companies_sharing_a_slug_are_told_apart_by_their_api_base() -> None:
    """Workday and Oracle tenants reuse generic slugs ("External") and differ only by api_base.
    The expected boards are spelled out, not built with the sampler's own helper."""
    api_a = "https://a.example.test/wday/cxs/a/External"
    api_b = "https://b.example.test/wday/cxs/b/External"
    source = _FakeClient()
    company_a = source.add_company("workday", "external", api_base=api_a)
    company_b = source.add_company("workday", "external", api_base=api_b)
    source.add_posting(company_a, "a-1", board=f"workday:external:{api_a}")
    source.add_posting(company_b, "b-1", board=f"workday:external:{api_b}")
    target = _FakeClient()

    await _copy(source, target, companies=2, min_per_type=2)

    ids = {c["api_base"]: c["id"] for c in target.rows("job_registry_companies")}
    boards = {p["board"]: p["company_id"] for p in target.rows("job_registry_postings")}
    assert boards == {
        f"workday:external:{api_a}": ids[api_a],
        f"workday:external:{api_b}": ids[api_b],
    }


def test_the_rank_tells_two_companies_with_one_slug_apart() -> None:
    a = {"ats_type": "workday", "slug": "external", "api_base": "https://a.example.test"}
    b = {"ats_type": "workday", "slug": "external", "api_base": "https://b.example.test"}

    chosen = {
        pick_sample([a, b], total=1, min_per_type=1, seed=seed)[0]["api_base"] for seed in range(40)
    }

    assert chosen == {a["api_base"], b["api_base"]}


async def test_big_writes_go_out_in_batches_of_the_documented_size() -> None:
    source = _FakeClient()
    for i in range(450):
        company = source.add_company("greenhouse", f"company-{i:03d}")
        source.add_posting(company, f"job-{i:03d}")
    target = _FakeClient()

    await _copy(source, target, companies=450, postings_per_company=1, min_per_type=5)

    company_batches = [
        rows for name, rows in target.upsert_calls if name == "job_registry_companies"
    ]
    posting_batches = [
        rows for name, rows in target.upsert_calls if name == "job_registry_postings"
    ]
    assert sum(map(len, company_batches)) == 450
    assert len(company_batches) >= 3
    assert max(map(len, company_batches)) <= 200
    assert sum(map(len, posting_batches)) == 450
    assert len(posting_batches) >= 5
    assert max(map(len, posting_batches)) <= 100


async def test_a_rerun_starts_the_polling_state_over_again() -> None:
    """The docstring promises this; with the same clock and an untouched target a re-run that
    left the rows alone would look identical, so the target's poller 'runs' in between."""
    source = _registry({"greenhouse": 3})
    target = _FakeClient()
    await _copy(source, target)
    for company in target.rows("job_registry_companies"):
        company.update(
            etag='W/"dev"',
            last_polled_at="2026-10-05T01:00:00+00:00",
            consecutive_failures=3,
            tier="cold",
            next_poll_at="2026-10-06T00:00:00+00:00",
        )
    later = _NOW + timedelta(hours=2)

    await _copy(source, target, now=later)

    for company in target.rows("job_registry_companies"):
        assert company["etag"] is None
        assert company["last_polled_at"] is None
        assert company["consecutive_failures"] == 0
        assert company["tier"] == "hot"  # the source's weight, not the target's drifted one
        assert company["next_poll_at"] == later.isoformat()


@pytest.mark.parametrize(("companies", "postings"), [(1, 0), (2000, 200)])
async def test_the_bounds_themselves_are_accepted(companies: int, postings: int) -> None:
    summary = await _copy(
        _registry({"greenhouse": 2}),
        _FakeClient(),
        companies=companies,
        postings_per_company=postings,
    )

    assert summary.companies == min(companies, 2)


async def test_reads_against_the_source_run_a_few_at_a_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _registry({"greenhouse": 12}, postings_each=1)
    in_flight = 0
    peak = 0
    real = _Query.execute

    async def slow(self: _Query) -> Any:
        nonlocal in_flight, peak
        if self._table.name == "job_registry_postings" and self._upsert is None:
            in_flight += 1
            peak = max(peak, in_flight)
            try:
                await asyncio.sleep(0.005)
                return await real(self)
            finally:
                in_flight -= 1
        return await real(self)

    monkeypatch.setattr(_Query, "execute", slow)

    await _copy(source, _FakeClient(), companies=12, min_per_type=5)

    assert 1 < peak <= 4


async def test_a_statement_timeout_on_a_source_read_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_wait(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    source = _registry({"greenhouse": 2}, postings_each=2)
    real = _Query.execute
    failures = {"left": 1}

    async def flaky(self: _Query) -> Any:
        is_posting_read = self._table.name == "job_registry_postings" and self._upsert is None
        if is_posting_read and failures["left"] > 0:
            failures["left"] -= 1
            raise APIError({"message": "timeout", "code": "57014", "hint": None, "details": None})
        return await real(self)

    monkeypatch.setattr(_Query, "execute", flaky)
    target = _FakeClient()

    await _copy(source, target)

    assert failures["left"] == 0
    assert len(target.rows("job_registry_postings")) == 4


async def test_companies_without_postings_are_counted() -> None:
    source = _registry({"greenhouse": 3}, postings_each=2)
    source.add_company("greenhouse", "empty-1")
    source.add_company("greenhouse", "empty-2")

    summary = await _copy(source, _FakeClient(), companies=5, min_per_type=5)

    assert summary.companies == 5
    assert summary.companies_without_postings == 2


async def test_postings_whose_company_never_reached_the_target_are_counted_not_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _registry({"greenhouse": 3}, postings_each=2)
    target = _FakeClient()
    real_apply = _Table.apply_upsert

    def lossy(self: _Table, incoming: list[dict[str, Any]], on_conflict: str) -> None:
        if self.name == "job_registry_companies":
            incoming = [row for row in incoming if row["slug"] != "greenhouse-0"]
        real_apply(self, incoming, on_conflict)

    monkeypatch.setattr(_Table, "apply_upsert", lossy)

    summary = await _copy(source, target)

    assert summary.postings_skipped_unresolved_company == 2
    assert len(target.rows("job_registry_postings")) == 4
    assert "skipped" in str(summary)
