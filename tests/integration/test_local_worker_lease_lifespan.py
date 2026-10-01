"""Two real app lifespans over one local Supabase stack, and the real
`claim_worker_lease` function between them (launch plan P2.19).

Each lifespan is a separate "process" as far as the lease can tell: its own
holder id, its own keepers, its own database clients. Their worker loops are idle
(this is about who holds the lease, not about what the workers do).

Run with `pytest -m local_supabase` after `supabase start` and
`supabase db reset --local`."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI

from between_jobs.api import app as app_module

from .conftest import World

pytestmark = pytest.mark.local_supabase

THE_THREE = ("job_registry_poller", "saved_search_matcher", "gmail_reply_checker")


@pytest.fixture
async def stack_env(world: World, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("SUPABASE_URL", world.stack["API_URL"])
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", world.stack["SERVICE_ROLE_KEY"])
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("WORKER_LEASES", "on")
    for flag in (
        "DISABLE_OUTBOX_WORKER",
        "DISABLE_JOB_REGISTRY_POLLER",
        "DISABLE_SAVED_SEARCH_MATCHER",
        "DISABLE_GMAIL_REPLY_CHECKER",
        "DISABLE_HIRING_SIGNAL_CACHE_PURGE",
    ):
        monkeypatch.delenv(flag)

    async def idle(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.Event().wait()

    for loop in (
        "run_worker_forever",
        "run_poller_forever",
        "run_matcher_forever",
        "run_reply_check_forever",
        "run_hiring_cache_purge_forever",
    ):
        monkeypatch.setattr(app_module, loop, idle)

    await world.pg.execute(
        "delete from public.worker_leases where worker = any($1)", list(THE_THREE)
    )
    yield
    await world.pg.execute(
        "delete from public.worker_leases where worker = any($1)", list(THE_THREE)
    )


def _states(app: FastAPI) -> dict[str, str | None]:
    workers = app.state.workers.workers
    return {
        name: (state.lease.status() if state.lease is not None else None)
        for name, state in workers.items()
    }


async def test_the_second_process_stands_by_for_all_three_while_the_first_holds_them(
    world: World, stack_env: Any
) -> None:
    first, second = FastAPI(), FastAPI()

    async with app_module.lifespan(first), app_module.lifespan(second):
        assert {n: _states(first)[n] for n in THE_THREE} == dict.fromkeys(THE_THREE, "held")
        assert {n: _states(second)[n] for n in THE_THREE} == dict.fromkeys(THE_THREE, "standby")
        # The outbox and the cache purge are not leased in v1.
        assert _states(first)["outbox"] is None
        assert _states(second)["hiring_signal_cache_purge"] is None
        # Both report healthy: a standby is alive on purpose.
        assert first.state.workers.healthy() and second.state.workers.healthy()

        rows = await world.pg.fetch(
            "select worker, holder, expires_at > now() as live from public.worker_leases"
        )
        assert {r["worker"] for r in rows} == set(THE_THREE)
        assert len({r["holder"] for r in rows}) == 1  # one process holds all three
        assert all(r["live"] for r in rows)
