"""The in-memory update ledger gives the scenario the answers the SQL must give too
(tests/integration/test_local_telegram_update_dedup.py runs the same scenario against the
real functions)."""

from __future__ import annotations

from update_ledger_fakes import SCENARIO_EXPECTED, FakeUpdateLedger, run_scenario


async def test_the_fake_ledger_gives_the_scenarios_expected_answers() -> None:
    assert await run_scenario(FakeUpdateLedger()) == SCENARIO_EXPECTED
