"""Repo-wide test fixtures.

Every route test in this repo boots the real FastAPI app (and thus its
`lifespan`) via TestClient/ASGI against a stubbed, unreachable
SUPABASE_URL. `app.py`'s outbox worker (Horizon Sprint 4.0), job
registry poller worker (Job Finder P2), saved-search matcher worker
(Job Finder P9b), Gmail reply checker worker (Gmail reply/status
parsing R3) and Hiring Signals cache-purge worker all poll Supabase
immediately on startup -- without
disabling them here, that first poll would throw an unhandled connection
error inside its own background task on every single test run. This is
the one thing every test file needs regardless of what it's actually
testing, so it lives here rather than being copy-pasted into each file's
own `_stub_env` fixture.
"""

from __future__ import annotations

from typing import Any

import pytest

from between_jobs.api import product_events, rate_limits


@pytest.fixture(autouse=True)
def _disable_outbox_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISABLE_OUTBOX_WORKER", "1")
    monkeypatch.setenv("DISABLE_JOB_REGISTRY_POLLER", "1")
    monkeypatch.setenv("DISABLE_SAVED_SEARCH_MATCHER", "1")
    monkeypatch.setenv("DISABLE_GMAIL_REPLY_CHECKER", "1")
    monkeypatch.setenv("DISABLE_HIRING_SIGNAL_CACHE_PURGE", "1")
    # The leased workers would claim a lease over the stubbed, unreachable database
    # at startup (tests/test_worker_lease_lifespan.py turns it on where it matters).
    monkeypatch.setenv("WORKER_LEASES", "off")
    # /health would otherwise call Supabase, forge-engines and the LaTeX
    # service for real on every hit; it reports them as not_checked instead.
    monkeypatch.setenv("DISABLE_HEALTH_DEPENDENCY_CHECKS", "1")


async def _allow_every_request(*_: object) -> rate_limits.RateLimitDecision:
    return rate_limits.RateLimitDecision(allowed=True, retry_after_seconds=0)


@pytest.fixture(autouse=True)
def _no_rate_limit_database(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """The per-user limiter (api/rate_limits.py) claims a slot through a Postgres RPC, which
    the route tests' hand-made Supabase fakes do not have and must not need. So by default
    every request is allowed without asking anyone; the limiter's own tests
    (tests/test_rate_limits.py) put the real function back, and the tests that run against a
    real local stack (marked `local_supabase`) never have it replaced at all.

    This is a test double, not a switch: nothing in the running application can turn the
    limits off."""
    if request.node.get_closest_marker("local_supabase") is None:
        monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", _allow_every_request)


async def _discard_event(*_: object) -> bool:
    return True


@pytest.fixture(autouse=True)
def _no_product_event_database(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Product events (api/product_events.py) are written with the service-role client, which the
    route tests' hand-made Supabase fakes do not have a `product_events` table for and must not
    need. So by default the write is a no-op: the instrumented routes still schedule it, and it
    simply stores nothing. The emitter's own tests and the tests of each emission point put a
    recording or failing writer back; the tests that run against a real local stack (marked
    `local_supabase`) never have it replaced at all."""
    if request.node.get_closest_marker("local_supabase") is None:
        monkeypatch.setattr(product_events, "write_event", _discard_event)


@pytest.fixture(autouse=True)
def _fresh_setup_required_counts() -> None:
    """The per-user hourly cap on `setup_required` events (product_events._setup_window) is
    process-wide state. Tests share a handful of user ids, so without this a late test would find
    its user already at the cap, and its event silently not recorded."""
    product_events._setup_window.clear()


@pytest.fixture
def recorded_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The product-event rows the code under test tried to write, in order.

    The writer is replaced by a plain function that records the row the moment the event is
    scheduled and hands back an already-finished coroutine for the background task to run. So a
    test sees every event as soon as the request that caused it has returned, whichever event
    loop that request ran on and whether or not the background task ever got a turn."""
    rows: list[dict[str, Any]] = []

    def record(_supabase: object, row: dict[str, Any]) -> Any:
        rows.append(dict(row))

        async def stored() -> bool:
            return True

        return stored()

    monkeypatch.setattr(product_events, "write_event", record)
    return rows
