"""An in-memory stand-in for `claim_telegram_update` / `complete_telegram_update` /
`release_telegram_update`, and the one scenario both it and the real functions must satisfy.

The stand-in has a clock the test moves (the real functions use the database clock), so a
lease running out is a method call, not a sleep. `tests/test_telegram_update_ledger_contract.py`
runs SCENARIO against it in the fast suite; `tests/integration/test_local_telegram_update_dedup.py`
runs the same SCENARIO against the real functions on a local Supabase stack and requires the
same answers -- that equality is what stops the fake drifting from the SQL it stands in for."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Protocol

UPDATE_RPCS = frozenset(
    {"claim_telegram_update", "complete_telegram_update", "release_telegram_update"}
)
_DAY = 86400.0


class LedgerUnderTest(Protocol):
    async def claim(self, update_id: int, lease_seconds: int = 600) -> str:
        """'claimed', 'done' or 'in_progress'."""

    async def complete(self, update_id: int) -> None: ...

    async def release(self, update_id: int) -> None: ...

    async def age(self, update_id: int, seconds: float) -> None:
        """Make the update's claim `seconds` older."""

    async def known(self, update_id: int) -> bool:
        """Is there a row for it at all?"""


class FakeUpdateLedger:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {}
        self.now = 0.0
        self.claim_calls: list[int] = []
        self.leases: list[int] = []

    async def claim(self, update_id: int, lease_seconds: int = 600) -> str:
        if not 1 <= lease_seconds <= 86400:
            raise ValueError("p_lease_seconds must be between 1 and 86400")
        self.claim_calls.append(update_id)
        self.leases.append(lease_seconds)
        for stale in [
            uid for uid, row in self.rows.items() if row["claimed_at"] < self.now - 7 * _DAY
        ][:200]:
            del self.rows[stale]
        row = self.rows.get(update_id)
        if row is None:
            self.rows[update_id] = {"claimed_at": self.now, "completed": False}
            return "claimed"
        if not row["completed"] and row["claimed_at"] <= self.now - lease_seconds:
            row["claimed_at"] = self.now
            return "claimed"
        return "done" if row["completed"] else "in_progress"

    async def complete(self, update_id: int) -> None:
        if update_id in self.rows:
            self.rows[update_id]["completed"] = True

    async def release(self, update_id: int) -> None:
        row = self.rows.get(update_id)
        if row is not None and not row["completed"]:
            del self.rows[update_id]

    async def age(self, update_id: int, seconds: float) -> None:
        self.rows[update_id]["claimed_at"] -= seconds

    async def known(self, update_id: int) -> bool:
        return update_id in self.rows

    # -- the supabase `.rpc(name, params).execute()` surface ---------------------------

    def rpc(self, name: str, params: dict[str, Any]) -> Any:
        ledger = self

        class _Call:
            async def execute(self) -> SimpleNamespace:
                update_id = params["p_update_id"]
                if name == "claim_telegram_update":
                    return SimpleNamespace(
                        data=await ledger.claim(update_id, params.get("p_lease_seconds", 600))
                    )
                if name == "complete_telegram_update":
                    await ledger.complete(update_id)
                else:
                    await ledger.release(update_id)
                return SimpleNamespace(data=None)

        return _Call()


async def run_scenario(ledger: LedgerUnderTest) -> list[tuple[str, int, str | bool]]:
    """Drives `ledger` through every rule; returns what it answered to each claim as
    (step, update id, answer) for the caller to compare with SCENARIO_EXPECTED."""
    seen: list[tuple[str, int, str | bool]] = []

    async def claim(step: str, uid: int, lease: int = 600) -> None:
        seen.append((step, uid, await ledger.claim(uid, lease)))

    # A claim is exclusive, and a completed update stays claimed.
    await claim("first delivery", 1)
    await claim("redelivery while running", 1)
    await ledger.complete(1)
    await claim("redelivery after completion", 1)

    # A released claim can be taken again (the 500-retry path).
    await claim("before release", 2)
    await ledger.release(2)
    await claim("after release", 2)

    # A claim nobody finishes expires after its lease; before that it holds.
    await claim("crashed delivery", 3)
    await ledger.age(3, 100)
    await claim("inside the lease", 3)
    await ledger.age(3, 600)
    await claim("lease run out", 3)

    # A short lease is honoured.
    await claim("short lease", 4, lease=5)
    await ledger.age(4, 6)
    await claim("short lease run out", 4, lease=5)

    # Completing or releasing what was never claimed does nothing.
    await ledger.complete(5)
    await ledger.release(5)
    await claim("never claimed", 5)

    # A completed update cannot be released away.
    await claim("to complete", 6)
    await ledger.complete(6)
    await ledger.release(6)
    await claim("release after completion", 6)

    # Old rows are cleaned up by the next claim, after seven days.
    await claim("old row", 7)
    await ledger.complete(7)
    await ledger.age(7, 8 * _DAY)
    await claim("triggers the purge", 8)
    seen.append(("old row is gone", 7, await ledger.known(7)))
    return seen


SCENARIO_EXPECTED: list[tuple[str, int, str | bool]] = [
    ("first delivery", 1, "claimed"),
    ("redelivery while running", 1, "in_progress"),
    ("redelivery after completion", 1, "done"),
    ("before release", 2, "claimed"),
    ("after release", 2, "claimed"),
    ("crashed delivery", 3, "claimed"),
    ("inside the lease", 3, "in_progress"),
    ("lease run out", 3, "claimed"),
    ("short lease", 4, "claimed"),
    ("short lease run out", 4, "claimed"),
    ("never claimed", 5, "claimed"),
    ("to complete", 6, "claimed"),
    ("release after completion", 6, "done"),
    ("old row", 7, "claimed"),
    ("triggers the purge", 8, "claimed"),
    ("old row is gone", 7, False),
]
