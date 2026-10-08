"""The in-memory interaction ledger gives the shared scenario the answers the SQL must give too
(tests/integration/test_local_discord_interaction_dedup.py runs the same scenario against the real
`claim_discord_interaction` and its companions), and the store that wraps the three RPCs behaves
the way `telegram_updates_store` does: dedup is a safety net, never a gate."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest
from discord_fakes import FakeInteractionLedger
from update_ledger_fakes import SCENARIO_EXPECTED, run_scenario

from between_jobs.api.discord_interactions_store import (
    claim_interaction,
    complete_interaction,
    release_interaction,
)


async def test_the_fake_ledger_gives_the_scenarios_expected_answers() -> None:
    assert await run_scenario(FakeInteractionLedger()) == SCENARIO_EXPECTED


async def test_the_default_lease_of_the_fake_is_the_real_one() -> None:
    ledger = FakeInteractionLedger()
    assert await ledger.claim("1") == "claimed"
    await ledger.age("1", 899)
    assert await ledger.claim("1") == "in_progress"
    await ledger.age("1", 2)
    assert await ledger.claim("1") == "claimed"


class _Rpc:
    def __init__(self, answer: Any = None, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._answer = answer
        self._error = error

    def rpc(self, name: str, params: dict[str, Any]) -> _Rpc:
        self.calls.append((name, params))
        return self

    async def execute(self) -> SimpleNamespace:
        if self._error is not None:
            raise self._error
        return SimpleNamespace(data=self._answer)


async def test_the_store_calls_the_three_functions_with_the_id_as_text() -> None:
    rpc = _Rpc("claimed")
    assert await claim_interaction(rpc, "1100000000000000001", lease_seconds=900) == "claimed"  # type: ignore[arg-type]
    await complete_interaction(rpc, "1100000000000000001")  # type: ignore[arg-type]
    await release_interaction(rpc, "1100000000000000001")  # type: ignore[arg-type]
    assert rpc.calls == [
        (
            "claim_discord_interaction",
            {"p_interaction_id": "1100000000000000001", "p_lease_seconds": 900},
        ),
        ("complete_discord_interaction", {"p_interaction_id": "1100000000000000001"}),
        ("release_discord_interaction", {"p_interaction_id": "1100000000000000001"}),
    ]


@pytest.mark.parametrize("answer", ["claimed", "done", "in_progress"])
async def test_the_three_answers_are_passed_on(answer: str) -> None:
    assert await claim_interaction(_Rpc(answer), "1", lease_seconds=5) == answer  # type: ignore[arg-type]


@pytest.mark.parametrize("answer", [None, True, 1, "maybe", "", ["claimed"]])
async def test_an_answer_that_is_not_one_of_the_three_means_process_it(
    answer: Any, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        assert await claim_interaction(_Rpc(answer), "1", lease_seconds=5) is None  # type: ignore[arg-type]
    assert "unrecognised" in caplog.text


async def test_a_failing_claim_means_process_it_and_the_other_two_never_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    broken = _Rpc(error=RuntimeError("function claim_discord_interaction does not exist"))
    with caplog.at_level(logging.WARNING):
        assert await claim_interaction(broken, "1", lease_seconds=5) is None  # type: ignore[arg-type]
        await complete_interaction(broken, "1")  # type: ignore[arg-type]
        await release_interaction(broken, "1")  # type: ignore[arg-type]
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 3
