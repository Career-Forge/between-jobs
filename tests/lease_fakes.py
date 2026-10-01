"""An in-memory stand-in for the `claim_worker_lease` SQL function, and the one
scenario both it and the real function must satisfy.

The stand-in has a clock the test advances (the real function uses the database
clock), so lease expiry is a method call, not a sleep. `tests/test_worker_lease_contract.py`
runs SCENARIO against it in the fast suite; `tests/integration/test_local_worker_leases.py`
runs the same SCENARIO against the real function on a local Supabase stack and
requires the same answers -- that equality is what stops the fake drifting from the
SQL it stands in for."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class LeaseUnderTest(Protocol):
    async def claim(self, worker: str, holder: str, ttl_seconds: int) -> bool: ...

    async def expire(self, worker: str) -> None:
        """Make the current holder's lease run out, now."""


class FakeLeaseStore:
    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        """`clock` lets the store follow someone else's time (a test's virtual
        clock); without one it has its own, moved by `advance`."""
        self._clock = clock
        self._own_now = 0.0
        self.rows: dict[str, tuple[str, float]] = {}
        self.claims = 0

    @property
    def now(self) -> float:
        return self._clock() if self._clock is not None else self._own_now

    def advance(self, seconds: float) -> None:
        self._own_now += seconds

    async def claim(self, worker: str, holder: str, ttl_seconds: int) -> bool:
        self.claims += 1
        if not 1 <= ttl_seconds <= 3600:
            raise ValueError("p_ttl_seconds must be between 1 and 3600")
        current = self.rows.get(worker)
        if current is None or current[0] == holder or current[1] <= self.now:
            self.rows[worker] = (holder, self.now + ttl_seconds)
            return True
        return False

    async def expire(self, worker: str) -> None:
        holder, _ = self.rows[worker]
        self.rows[worker] = (holder, self.now)


# (what is being done, who claims, what the function must answer). Expiry is
# `expires_at <= now()`, so a lease expired "now" is takeable immediately.
SCENARIO: list[tuple[str, str, bool]] = [
    ("A claims a lease nobody holds", "A", True),
    ("A renews its own live lease", "A", True),
    ("B is refused while A's lease is live", "B", False),
    ("a refusal changed nothing: A renews again", "A", True),
    ("(A's lease expires)", "-", True),
    ("B takes the expired lease", "B", True),
    ("A is now refused: B's lease is live", "A", False),
    ("B renews", "B", True),
]


async def run_scenario(lease: LeaseUnderTest, worker: str) -> list[tuple[str, str, bool]]:
    """Plays SCENARIO against `lease`; returns it with the real answers filled in."""
    observed: list[tuple[str, str, bool]] = []
    for label, holder, _expected in SCENARIO:
        if holder == "-":
            await lease.expire(worker)
            observed.append((label, holder, True))
            continue
        observed.append((label, holder, await lease.claim(worker, holder, 60)))
    return observed
