"""claim_worker_lease's wrapper (launch plan P2.19): exactly true or false, else it
raises -- because "I was told no" (standby, healthy) and "I could not ask" (unknown,
unhealthy) must never be confused."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from between_jobs.api.worker_leases_store import LeaseAnswerError, claim_worker_lease


class _Client:
    def __init__(self, data: Any) -> None:
        self.data = data
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def rpc(self, name: str, params: dict[str, Any]) -> Any:
        self.calls.append((name, params))
        data = self.data

        class _Call:
            async def execute(self) -> Any:
                return SimpleNamespace(data=data)

        return _Call()


async def test_it_calls_the_function_with_the_worker_the_holder_and_the_ttl() -> None:
    client = _Client(True)

    assert await claim_worker_lease(client, "job_registry_poller", "dep1:abc", 60) is True  # type: ignore[arg-type]

    assert client.calls == [
        (
            "claim_worker_lease",
            {"p_worker": "job_registry_poller", "p_holder": "dep1:abc", "p_ttl_seconds": 60},
        )
    ]


async def test_false_is_a_definite_refusal_not_an_error() -> None:
    assert await claim_worker_lease(_Client(False), "w", "h", 60) is False  # type: ignore[arg-type]


@pytest.mark.parametrize("data", [None, [], {}, "true", "false", 1, 0, [True], {"claim": True}])
async def test_anything_that_is_not_exactly_a_boolean_raises_instead_of_guessing(
    data: Any,
) -> None:
    """bool(None), bool([]) and bool(0) are all False: reading them as "someone else
    holds it" would put a process on a healthy standby that no one is covering."""
    with pytest.raises(LeaseAnswerError):
        await claim_worker_lease(_Client(data), "w", "h", 60)  # type: ignore[arg-type]
