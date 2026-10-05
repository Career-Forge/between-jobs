"""`claim_and_publish_outbox_batch` with more than one caller, on a local stack.

The outbox worker is deliberately NOT given a lease (launch plan P2.19): its claim
is one `UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED) RETURNING *`
statement, so two workers -- an old and a new container overlapping in a deploy --
are meant to get disjoint rows. That was established by reading the SQL and its
comment; no test ever ran two callers. This is that test, and the reason leaving
the outbox out of the lease is a measured decision rather than an assumption.

Run with `pytest -m local_supabase` after `supabase start` and
`supabase db reset --local`."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import asyncpg
import pytest

from .conftest import World

pytestmark = pytest.mark.local_supabase

_CLAIM = "select id from public.claim_and_publish_outbox_batch($1)"


async def _seed(world: World, count: int) -> tuple[str, set[str]]:
    """`count` unpublished rows for one throwaway user, oldest first. Every other
    unpublished row on this (throwaway, loopback-only) stack is marked published
    first, so the claim can only ever see these."""
    await world.pg.execute(
        "update public.event_outbox set published_at = now() where published_at is null"
    )
    user = await world.web_user()
    tag = uuid.uuid4().hex[:8]
    rows = await world.pg.fetch(
        """
        insert into public.event_outbox
          (user_id, aggregate_type, aggregate_id, event_type, event_version, payload,
           idempotency_key, created_at)
        select $1::uuid, 'test', gen_random_uuid(), 'test.event', 1, '{}'::jsonb,
               'claim-test-' || $2 || '-' || n,
               now() - (($3 - n) || ' seconds')::interval
        from generate_series(1, $3) as n
        returning id
        """,
        user,
        tag,
        count,
    )
    return user, {str(r["id"]) for r in rows}


# The claim is `UPDATE ... WHERE id IN (SELECT ... LIMIT n FOR UPDATE SKIP LOCKED)`. Which join the
# planner uses for that IN depends on the table's statistics, and a nested-loop semi join re-runs
# the limited subquery, so a call can return more rows than its limit (33 for a limit of 20 was
# seen). The test therefore runs under the default planner and with the other join strategies
# switched off, which forces the nested loop whatever the statistics happen to be.
_PLANNER_SETTINGS = {
    "default planner": "",
    "forced nested loop": "set enable_hashjoin = off; set enable_mergejoin = off; "
    "set enable_hashagg = off",
}


@pytest.mark.parametrize("settings", list(_PLANNER_SETTINGS), ids=list(_PLANNER_SETTINGS))
async def test_two_workers_claiming_at_the_same_moment_get_disjoint_batches(
    world: World, settings: str
) -> None:
    """Worker A claims and holds its transaction open -- its row locks are live --
    while worker B claims. B must neither block on A's rows nor take any of them."""
    user, seeded = await _seed(world, 40)
    a: Any = await asyncpg.connect(world.stack["DB_URL"])
    b: Any = await asyncpg.connect(world.stack["DB_URL"])
    try:
        for connection in (a, b):
            if _PLANNER_SETTINGS[settings]:
                await connection.execute(_PLANNER_SETTINGS[settings])
        ta, tb = a.transaction(), b.transaction()
        await ta.start()
        claimed_by_a = {str(r["id"]) for r in await a.fetch(_CLAIM, 20)}

        await tb.start()
        claimed_by_b = {
            str(r["id"]) for r in await asyncio.wait_for(b.fetch(_CLAIM, 20), timeout=10)
        }

        assert len(claimed_by_a) == 20 and len(claimed_by_b) == 20
        assert claimed_by_a.isdisjoint(claimed_by_b)
        assert claimed_by_a | claimed_by_b == seeded
        await ta.commit()
        await tb.commit()
    finally:
        await a.close()
        await b.close()

    assert await world.pg.fetch(_CLAIM, 20) == []  # nothing left for a third caller
    unpublished = await world.pg.fetchval(
        "select count(*) from public.event_outbox where user_id = $1 and published_at is null",
        user,
    )
    assert unpublished == 0


async def test_four_workers_draining_the_outbox_together_handle_every_row_exactly_once(
    world: World,
) -> None:
    _user, seeded = await _seed(world, 40)
    connections: list[Any] = [await asyncpg.connect(world.stack["DB_URL"]) for _ in range(4)]

    async def drain(connection: Any) -> list[str]:
        mine: list[str] = []
        while True:
            batch = await connection.fetch(_CLAIM, 3)
            if not batch:
                return mine
            mine.extend(str(r["id"]) for r in batch)
            await asyncio.sleep(0)  # let the others in between batches

    try:
        per_worker = await asyncio.gather(*(drain(c) for c in connections))
    finally:
        for connection in connections:
            await connection.close()

    everything = [row_id for mine in per_worker for row_id in mine]
    assert len(everything) == len(set(everything)) == 40  # no row twice, none dropped
    assert set(everything) == seeded
    assert sum(1 for mine in per_worker if mine) >= 2  # it really was shared
