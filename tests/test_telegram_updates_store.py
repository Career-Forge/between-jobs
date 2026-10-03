"""telegram_updates_store: the claim is exclusive, and it never takes a message down with it."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from update_ledger_fakes import FakeUpdateLedger

from between_jobs.api.telegram_updates_store import claim_update, complete_update, release_update


class _Supabase:
    def __init__(self, ledger: Any) -> None:
        self._ledger = ledger

    def rpc(self, name: str, params: dict[str, Any]) -> Any:
        return self._ledger.rpc(name, params)


class _Answers:
    """A function that answers with whatever it is told to, or raises."""

    def __init__(self, data: Any = None, error: Exception | None = None) -> None:
        self.data, self.error = data, error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def rpc(self, name: str, params: dict[str, Any]) -> Any:
        self.calls.append((name, params))
        outer = self

        class _Call:
            async def execute(self) -> SimpleNamespace:
                if outer.error is not None:
                    raise outer.error
                return SimpleNamespace(data=outer.data)

        return _Call()


async def test_the_first_claim_wins_and_a_second_is_a_duplicate() -> None:
    supabase = _Supabase(FakeUpdateLedger())

    assert await claim_update(supabase, 1) is True  # type: ignore[arg-type]
    assert await claim_update(supabase, 1) is False  # type: ignore[arg-type]


async def test_complete_and_release_use_the_update_id() -> None:
    answers = _Answers()
    supabase = answers

    await complete_update(supabase, 7)  # type: ignore[arg-type]
    await release_update(supabase, 7)  # type: ignore[arg-type]

    assert answers.calls == [
        ("complete_telegram_update", {"p_update_id": 7}),
        ("release_telegram_update", {"p_update_id": 7}),
    ]


@pytest.mark.parametrize("data", [None, [], "true", 1, 0, {"claimed": True}])
async def test_an_answer_that_is_not_a_boolean_means_could_not_tell(data: object) -> None:
    """None must not read as "duplicate": that would drop a message because the bookkeeping
    answered oddly."""
    assert await claim_update(_Answers(data=data), 1) is None  # type: ignore[arg-type]


async def test_a_failing_claim_means_could_not_tell_not_an_exception() -> None:
    answers = _Answers(error=RuntimeError("function does not exist"))

    assert await claim_update(answers, 1) is None  # type: ignore[arg-type]


async def test_a_failing_complete_or_release_is_swallowed() -> None:
    answers = _Answers(error=RuntimeError("timeout"))

    await complete_update(answers, 1)  # type: ignore[arg-type]
    await release_update(answers, 1)  # type: ignore[arg-type]
