"""The lease wired into the real app lifespan (launch plan P2.19).

Boots the real FastAPI app with all five workers enabled but their loops replaced
by idle ones, over a strict fake database client that records every RPC and answers
`claim_worker_lease` however the test says. What is pinned:

- scope: exactly the three workers that need a lease claim one -- never the outbox or
  the cache purge -- all with one holder id per process, and /health shows it;
- fail-closed: a refusal is a healthy standby, but a claim that cannot be made (the
  migration missing, a bad grant) turns /health 503 at once, which is what lets a
  platform's first-2xx deploy healthcheck refuse the deploy;
- the switch: WORKER_LEASES is on by default, strictly parsed, and off means no RPC;
- a failed boot leaves no keeper behind.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from between_jobs.api import app as app_module
from between_jobs.api.app import LEASED_WORKERS, app

THE_THREE = {"job_registry_poller", "saved_search_matcher", "gmail_reply_checker"}
OTHERS = {"outbox", "hiring_signal_cache_purge"}
_DISABLE_FLAGS = (
    "DISABLE_OUTBOX_WORKER",
    "DISABLE_JOB_REGISTRY_POLLER",
    "DISABLE_SAVED_SEARCH_MATCHER",
    "DISABLE_GMAIL_REPLY_CHECKER",
    "DISABLE_HIRING_SIGNAL_CACHE_PURGE",
)


class StrictClient:
    """Records every RPC; fails the test on any it was not told to expect."""

    def __init__(self, answer: bool | Exception | object) -> None:
        self.answer = answer
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def rpc(self, name: str, params: dict[str, Any]) -> Any:
        self.calls.append((name, params))
        if name != "claim_worker_lease":
            raise AssertionError(f"unexpected rpc {name!r}")
        answer = self.answer

        class _Call:
            async def execute(self) -> Any:
                if isinstance(answer, Exception):
                    raise answer
                return SimpleNamespace(data=answer)

        return _Call()


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("RAILWAY_DEPLOYMENT_ID", raising=False)
    for flag in _DISABLE_FLAGS:
        monkeypatch.delenv(flag)  # all five workers enabled
    monkeypatch.setenv("WORKER_LEASES", "on")


def _boot_with(
    monkeypatch: pytest.MonkeyPatch, client: StrictClient
) -> Callable[[], Awaitable[tuple[StrictClient, str]]]:
    """Installs the fake database and idle worker loops; returns the fake
    `create_supabase_client` it installed."""

    async def fake_create() -> tuple[StrictClient, str]:
        return client, "https://example.supabase.co"

    async def idle(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(app_module, "create_supabase_client", fake_create)
    for loop in (
        "run_worker_forever",
        "run_poller_forever",
        "run_matcher_forever",
        "run_reply_check_forever",
        "run_hiring_cache_purge_forever",
    ):
        monkeypatch.setattr(app_module, loop, idle)
    return fake_create


def _claimed_workers(client: StrictClient) -> list[str]:
    return sorted(params["p_worker"] for _name, params in client.calls)


# -- scope -------------------------------------------------------------------------


def test_the_leased_set_is_exactly_the_three_that_need_one() -> None:
    assert LEASED_WORKERS == THE_THREE


def test_exactly_the_three_workers_claim_a_lease_and_the_rest_never_do(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StrictClient(True)
    _boot_with(monkeypatch, client)

    with TestClient(app) as test_client:
        body = test_client.get("/health").json()
        states = test_client.app.state.workers.workers  # type: ignore[attr-defined]

    assert _claimed_workers(client) == sorted(THE_THREE)  # one first claim each
    for worker in THE_THREE:
        assert body["workers"][worker]["lease"]["state"] == "held"
        assert states[worker].lease is not None
    for worker in OTHERS:
        assert "lease" not in body["workers"][worker]
        assert states[worker].lease is None


def test_every_claim_carries_the_ttl_and_one_holder_id_per_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StrictClient(True)
    _boot_with(monkeypatch, client)

    with TestClient(app) as test_client:
        text = test_client.get("/health").text

    holders = {params["p_holder"] for _name, params in client.calls}
    assert len(holders) == 1  # one process, one id, shared by its three workers
    assert re.fullmatch(r"local:[0-9a-f]{32}", next(iter(holders)))
    assert {params["p_ttl_seconds"] for _name, params in client.calls} == {60}
    assert next(iter(holders)) not in text  # never in the unauthenticated body


def test_the_holder_id_is_a_fresh_uuid_per_process_even_on_one_railway_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RAILWAY_DEPLOYMENT_ID is shared by every replica of a deployment, so it can
    only be a label: two replicas must still get two different holder ids."""
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "d3adb33f-aaaa-bbbb-cccc-dddddddddddd")
    seen: list[str] = []
    for _ in range(2):
        client = StrictClient(True)
        _boot_with(monkeypatch, client)
        with TestClient(app):
            pass
        seen.append(client.calls[0][1]["p_holder"])

    assert all(h.startswith("d3adb33f:") for h in seen)
    assert seen[0] != seen[1]


def test_a_leased_worker_that_is_switched_off_claims_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISABLE_GMAIL_REPLY_CHECKER", "1")
    client = StrictClient(True)
    _boot_with(monkeypatch, client)

    with TestClient(app) as test_client:
        body = test_client.get("/health").json()

    assert _claimed_workers(client) == ["job_registry_poller", "saved_search_matcher"]
    assert body["workers"]["gmail_reply_checker"]["status"] == "disabled"


# -- fail closed ---------------------------------------------------------------------


def test_a_refused_lease_is_a_healthy_standby(monkeypatch: pytest.MonkeyPatch) -> None:
    """The old container still holds it, as it does for the whole overlap of a deploy."""
    client = StrictClient(False)
    _boot_with(monkeypatch, client)

    with TestClient(app) as test_client:
        response = test_client.get("/health")

    assert response.status_code == 200
    for worker in THE_THREE:
        assert response.json()["workers"][worker]["status"] == "standby"


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (APIError({"message": "no function", "code": "PGRST202"}), "function_missing"),
        (APIError({"message": "permission denied", "code": "42501"}), "database"),
        (RuntimeError("anything"), "unexpected"),
    ],
)
def test_a_lease_that_cannot_be_asked_fails_health_at_once(
    monkeypatch: pytest.MonkeyPatch, error: Exception, kind: str
) -> None:
    """Railway cuts over on the first 2xx. If the migration is missing (or a grant
    is), answering 200 here would hand traffic to three workers that can never tick."""
    client = StrictClient(error)
    _boot_with(monkeypatch, client)

    with TestClient(app) as test_client:
        response = test_client.get("/health")  # the very first request after boot

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "failing"
    for worker in THE_THREE:
        assert body["workers"][worker]["status"] == "lease_unknown"
        assert body["workers"][worker]["lease"]["last_claim_error"] == kind
    for worker in OTHERS:
        assert body["workers"][worker]["status"] != "lease_unknown"


def test_a_malformed_answer_is_not_mistaken_for_a_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StrictClient(None)  # neither true nor false
    _boot_with(monkeypatch, client)

    with TestClient(app) as test_client:
        response = test_client.get("/health")

    assert response.status_code == 503
    assert response.json()["workers"]["job_registry_poller"]["lease"]["last_claim_error"] == (
        "malformed"
    )


# -- the switch ---------------------------------------------------------------------


def test_it_is_on_when_the_variable_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORKER_LEASES")
    client = StrictClient(True)
    _boot_with(monkeypatch, client)

    with TestClient(app):
        pass

    assert _claimed_workers(client) == sorted(THE_THREE)


def test_a_blank_value_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_LEASES", "")
    client = StrictClient(True)
    _boot_with(monkeypatch, client)

    with TestClient(app):
        pass

    assert _claimed_workers(client) == sorted(THE_THREE)


@pytest.mark.parametrize("value", ["off", "OFF", " off "])
def test_off_means_no_lease_and_no_rpc_at_all(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("WORKER_LEASES", value)
    client = StrictClient(True)
    _boot_with(monkeypatch, client)

    with TestClient(app) as test_client:
        body = test_client.get("/health").json()

    assert client.calls == []
    assert body["workers"]["job_registry_poller"]["status"] != "lease_unknown"
    assert "lease" not in body["workers"]["job_registry_poller"]


@pytest.mark.parametrize("value", ["false", "0", "no", "disable", "disabled", "onn", "true"])
def test_anything_but_on_or_off_stops_the_boot_instead_of_silently_disabling_the_guard(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """The repo's DISABLE_* flags read any non-empty value as "yes, disable". Read that
    way, WORKER_LEASES=false would turn the safety guard off. It must be refused."""
    monkeypatch.setenv("WORKER_LEASES", value)
    client = StrictClient(True)
    _boot_with(monkeypatch, client)

    with (
        pytest.raises(RuntimeError, match="WORKER_LEASES must be 'on' or 'off'"),
        TestClient(app),
    ):
        pass
    assert client.calls == []


# -- a failed boot ------------------------------------------------------------------


async def test_a_boot_that_fails_midway_leaves_no_keeper_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StrictClient(True)
    original = _boot_with(monkeypatch, client)
    created = 0

    async def fails_on_the_fourth() -> tuple[Any, str]:
        nonlocal created
        created += 1
        if created == 4:  # the main client, outbox, poller, then the matcher fails
            raise RuntimeError("database credentials missing")
        return await original()

    monkeypatch.setattr(app_module, "create_supabase_client", fails_on_the_fourth)

    with pytest.raises(RuntimeError, match="credentials missing"):
        async with app_module.lifespan(app):
            pass

    leftovers = [t for t in asyncio.all_tasks() if t.get_name().startswith("worker-lease:")]
    assert [t for t in leftovers if not t.done()] == []
