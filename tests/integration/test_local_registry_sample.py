"""The registry sampler's real reads and writes (launch plan P2.8), against a local stack.

The unit tests use a PostgREST-shaped fake, which cannot show that the column lists exist, that
an interval or a numeric survives the round trip, that `count=exact` / `order` / `limit` mean
what the code assumes, or that the upserts satisfy the real constraints. This does.

One local stack stands in for both projects: the test reads a sample, deletes the rows it
seeded (so the "target" is empty of them), and writes the sample back. The guards in
`copy_sample` are not under test here, they are covered by the unit tests; this goes through
`read_sample` and `write_sample`, which is everything that touches a database.

Rows are committed (PostgREST cannot see an open transaction), carry a per-run prefix, and are
removed in a `finally`.

Run with `pytest -m local_supabase` after `supabase start` and `supabase db reset --local`."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from scripts.copy_registry_sample import (
    SampledRegistry,
    _count_companies,
    read_sample,
    write_sample,
)

from .conftest import World  # noqa: F401  (keeps the shared fixtures importable here)

pytestmark = pytest.mark.local_supabase

_NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)
_BIG = 5
_SMALL = 2
_CAP = 3
"""A "big" company has more active postings than the cap, a "small" one has fewer: the cap must
bind on the first, and on the second a closed posting would show up if the read forgot to
filter on status (the index sorts `active` before `closed`, so a closed row only reaches a
capped read when the cap is not already full of active ones)."""


@pytest.fixture
async def seeded(pg: Any) -> AsyncIterator[str]:
    """Seeds companies of three ATS types plus a failing and an inactive one; yields the
    prefix their slugs share, and deletes everything with that prefix afterward."""
    prefix = f"it-sample-{uuid.uuid4().hex[:8]}"
    try:
        for ats_type, count in (("greenhouse", 4), ("lever", 2), ("workday", 1)):
            for i in range(count):
                big = ats_type == "workday" or (ats_type == "greenhouse" and i == 0)
                await _insert_company(
                    pg,
                    prefix,
                    ats_type,
                    f"{prefix}-{ats_type}-{i}",
                    actives=_BIG if big else _SMALL,
                )
        await _insert_company(pg, prefix, "greenhouse", f"{prefix}-failing", failures=3)
        await _insert_company(pg, prefix, "greenhouse", f"{prefix}-dormant", active=False)
        yield prefix
    finally:
        await pg.execute(
            "delete from public.job_registry_companies where slug like $1", f"{prefix}%"
        )


async def _insert_company(
    pg: Any,
    prefix: str,
    ats_type: str,
    slug: str,
    *,
    failures: int = 0,
    active: bool = True,
    actives: int = _SMALL,
    api_base: str = "",
) -> None:
    company_id = await pg.fetchval(
        """
        insert into public.job_registry_companies
          (name, ats_type, slug, api_base, is_active, poll_interval, next_poll_at,
           last_polled_at, etag, last_modified, consecutive_failures, tier, relevant_yield,
           tier_weight, sweep_started_at, sweep_posting_count)
        values ($1, $2, $3, $6, $4, interval '1 day 06:00:00', now() - interval '3 days',
                now() - interval '8 days', 'W/"abc"', 'Sun, 27 Sep 2026 03:00:00 GMT', $5,
                'hot', 7, 0.75, now() - interval '9 days', 120)
        returning id
        """,
        f"Company {slug}",
        ats_type,
        slug,
        active,
        failures,
        api_base,
    )
    board = f"{ats_type}:{slug}:{api_base}"
    await _insert_posting(pg, company_id, board, f"{slug}-closed", days_ago=0, status="closed")
    for j in range(actives):
        await _insert_posting(pg, company_id, board, f"{slug}-job-{j}", days_ago=j + 1)


async def _insert_posting(
    pg: Any, company_id: Any, board: str, external_id: str, *, days_ago: int, status: str = "active"
) -> None:
    await pg.execute(
        """
        insert into public.job_registry_postings
          (company_id, board, external_id, title, jd_text, location, remote, apply_url,
           posted_at, status, first_seen, last_seen, salary_min, salary_max, salary_currency,
           salary_period, sponsorship_signal)
        values ($1, $2, $3, $4, 'Build distributed systems.', 'Remote', true, $5,
                now() - ($6::int * interval '1 day'), $7, now() - interval '10 days',
                now() - ($6::int * interval '1 day'), 120000, 180000.50, 'USD', 'year',
                'explicit_yes')
        """,
        company_id,
        board,
        external_id,
        f"Engineer {external_id}",
        f"https://example.test/{external_id}",
        days_ago,
        status,
    )


def _only(sampled: SampledRegistry, prefix: str) -> SampledRegistry:
    """The sample restricted to this run's own rows, so other rows in a shared local database
    are never rewritten."""
    keep = [i for i, c in enumerate(sampled.companies) if c["slug"].startswith(prefix)]
    return SampledRegistry(
        eligible_companies=sampled.eligible_companies,
        companies=[sampled.companies[i] for i in keep],
        postings=[sampled.postings[i] for i in keep],
    )


async def test_a_sample_read_from_postgrest_is_written_back_with_polling_reset(
    sb: Any, pg: Any, seeded: str
) -> None:
    assert await _count_companies(sb) >= 7

    sampled = _only(
        await read_sample(sb, companies=2000, postings_per_company=_CAP, min_per_type=2, seed=1),
        seeded,
    )

    slugs = {c["slug"] for c in sampled.companies}
    assert slugs == {s for s in slugs if not s.endswith(("-failing", "-dormant"))}
    assert len(slugs) == 7
    assert {c["ats_type"] for c in sampled.companies} == {"greenhouse", "lever", "workday"}
    for company, rows in zip(sampled.companies, sampled.postings, strict=True):
        big = company["slug"].endswith(("-workday-0", "-greenhouse-0"))
        assert len(rows) == (_CAP if big else _SMALL), company["slug"]
    assert not any(p["external_id"].endswith("-closed") for rows in sampled.postings for p in rows)

    # the "target" is empty of this run's rows
    await pg.execute("delete from public.job_registry_companies where slug like $1", f"{seeded}%")
    assert (
        await pg.fetchval(
            "select count(*) from public.job_registry_postings where board like $1",
            f"%{seeded}%",
        )
        == 0
    )

    assert await write_sample(sb, sampled, now=_NOW) == 0

    companies = await pg.fetch(
        "select * from public.job_registry_companies where slug like $1", f"{seeded}%"
    )
    assert len(companies) == 7
    for company in companies:
        assert company["etag"] is None
        assert company["last_modified"] is None
        assert company["last_polled_at"] is None
        assert company["consecutive_failures"] == 0
        assert company["sweep_started_at"] is None
        assert company["sweep_posting_count"] == 0
        assert company["next_poll_at"] == _NOW
        assert company["is_active"] is True
        assert company["tier"] == "hot"  # scheduling weight survives
        assert company["tier_weight"] == Decimal("0.75")
        assert company["poll_interval"] == timedelta(days=1, hours=6)  # a multi-day interval

    postings = await pg.fetch(
        """
        select p.*, c.slug, c.ats_type, c.api_base
        from public.job_registry_postings p
        join public.job_registry_companies c on c.id = p.company_id
        where c.slug like $1
        """,
        f"{seeded}%",
    )
    assert len(postings) == 2 * _CAP + 5 * _SMALL
    for posting in postings:
        assert posting["board"] == f"{posting['ats_type']}:{posting['slug']}:{posting['api_base']}"
        assert posting["status"] == "active"
        assert posting["closed_at"] is None
        assert posting["salary_max"] == Decimal("180000.50")
        assert posting["sponsorship_signal"] == "explicit_yes"
        assert posting["jd_tsv"] is not None  # the generated column filled itself in
        assert not posting["external_id"].endswith("-closed")


async def test_writing_the_same_sample_twice_adds_nothing(sb: Any, pg: Any, seeded: str) -> None:
    sampled = _only(
        await read_sample(sb, companies=2000, postings_per_company=_CAP, min_per_type=2, seed=1),
        seeded,
    )
    await pg.execute("delete from public.job_registry_companies where slug like $1", f"{seeded}%")

    await write_sample(sb, sampled, now=_NOW)
    first = (
        await pg.fetchval(
            "select count(*) from public.job_registry_companies where slug like $1", f"{seeded}%"
        ),
        await pg.fetchval(
            "select count(*) from public.job_registry_postings where board like $1", f"%{seeded}%"
        ),
    )
    await write_sample(sb, sampled, now=_NOW)
    second = (
        await pg.fetchval(
            "select count(*) from public.job_registry_companies where slug like $1", f"{seeded}%"
        ),
        await pg.fetchval(
            "select count(*) from public.job_registry_postings where board like $1", f"%{seeded}%"
        ),
    )

    assert first == second == (7, 2 * _CAP + 5 * _SMALL)
