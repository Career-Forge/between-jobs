"""The in-memory lease store honours the same contract as the SQL function
(see tests/lease_fakes.py): same scenario, same answers."""

from __future__ import annotations

import pytest
from lease_fakes import SCENARIO, FakeLeaseStore, run_scenario


async def test_the_fake_satisfies_the_lease_scenario() -> None:
    assert await run_scenario(FakeLeaseStore(), "w") == SCENARIO


async def test_the_fake_expires_by_its_clock_not_by_a_call() -> None:
    store = FakeLeaseStore()
    assert await store.claim("w", "A", 60) is True
    store.advance(59)
    assert await store.claim("w", "B", 60) is False
    store.advance(1)  # expires_at <= now: takeable at the exact moment it runs out
    assert await store.claim("w", "B", 60) is True


@pytest.mark.parametrize("ttl", [0, -1, 3601])
async def test_the_fake_refuses_an_absurd_ttl_like_the_function_does(ttl: int) -> None:
    with pytest.raises(ValueError):
        await FakeLeaseStore().claim("w", "A", ttl)
