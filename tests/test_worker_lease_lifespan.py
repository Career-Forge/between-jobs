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
import time
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest
from discord_fakes import APPLICATION_ID, BOT_TOKEN, PUBLIC_KEY_HEX
from fastapi import FastAPI
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from between_jobs.api import app as app_module
from between_jobs.api import product_events
from between_jobs.api.app import LEASED_WORKERS, app
from between_jobs.api.channel_push import FanOutNotifier
from between_jobs.api.digest_listener import handle_batch as handle_digest_batch
from between_jobs.api.discord_adapter import DiscordNotifier
from between_jobs.api.telegram_adapter import TelegramNotifier

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

    def __init__(self, answer: bool | Exception | object, *, delay: float = 0.0) -> None:
        self.answer = answer
        self.delay = delay  # real seconds each claim takes: a database round trip
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def rpc(self, name: str, params: dict[str, Any]) -> Any:
        self.calls.append((name, params))
        if name != "claim_worker_lease":
            raise AssertionError(f"unexpected rpc {name!r}")
        answer, delay = self.answer, self.delay

        class _Call:
            async def execute(self) -> Any:
                if delay:
                    await asyncio.sleep(delay)
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
    for name in ("DISCORD_APPLICATION_ID", "DISCORD_PUBLIC_KEY", "DISCORD_BOT_TOKEN"):
        monkeypatch.delenv(name, raising=False)
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


def _outbox_listener(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The digest listener the lifespan hands the outbox worker, as the worker receives it."""
    _boot_with(monkeypatch, StrictClient(True))
    seen: dict[str, Any] = {}

    async def record(*_args: Any, **kwargs: Any) -> None:
        seen["listeners"] = kwargs.get("listeners")
        await asyncio.Event().wait()

    monkeypatch.setattr(app_module, "run_worker_forever", record)
    with TestClient(app):
        deadline = time.monotonic() + 5
        while "listeners" not in seen and time.monotonic() < deadline:
            time.sleep(0.01)
    (listener,) = seen["listeners"]
    return listener


def test_the_outbox_worker_gets_a_notifier_for_the_bot_when_there_is_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A listener holding no notifier would silently stop the real-time job pushes."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")

    listener = _outbox_listener(monkeypatch)

    assert listener.func is handle_digest_batch
    # One notifier that fans out over every channel with an adapter here: Telegram's is the only
    # entry today, and it is the very notifier a bot-only deployment used to be handed.
    notifier = listener.keywords["notifier"]
    assert isinstance(notifier, FanOutNotifier)
    assert notifier.channels == {"telegram"}
    assert isinstance(notifier._notifiers["telegram"], TelegramNotifier)


def _configure_discord(monkeypatch: pytest.MonkeyPatch, *, bot_token: bool) -> None:
    monkeypatch.setenv("DISCORD_APPLICATION_ID", APPLICATION_ID)
    monkeypatch.setenv("DISCORD_PUBLIC_KEY", PUBLIC_KEY_HEX)
    if bot_token:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", BOT_TOKEN)


def test_the_outbox_worker_gets_a_notifier_for_discord_when_the_server_has_a_bot_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The client the lifespan builds has to reach the push notifier: dropped on the way, a server
    with a bot token would never push to Discord and nothing else would notice."""
    _configure_discord(monkeypatch, bot_token=True)

    listener = _outbox_listener(monkeypatch)

    notifier = listener.keywords["notifier"]
    assert isinstance(notifier, FanOutNotifier)
    assert notifier.channels == {"discord"}
    assert isinstance(notifier._notifiers["discord"], DiscordNotifier)


def test_discord_without_a_bot_token_has_no_push_notifier(monkeypatch: pytest.MonkeyPatch) -> None:
    """No token, no push: nothing can be sent to a person unprompted without one."""
    _configure_discord(monkeypatch, bot_token=False)

    listener = _outbox_listener(monkeypatch)

    assert listener.keywords["notifier"] is None


def test_both_channels_push_when_both_are_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")
    _configure_discord(monkeypatch, bot_token=True)

    notifier = _outbox_listener(monkeypatch).keywords["notifier"]

    assert isinstance(notifier, FanOutNotifier)
    assert notifier.channels == {"telegram", "discord"}


def test_the_outbox_worker_gets_no_notifier_on_a_server_without_a_bot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listener = _outbox_listener(monkeypatch)

    assert listener.func is handle_digest_batch
    assert listener.keywords["notifier"] is None


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


def test_the_first_health_request_waits_for_a_claim_that_takes_real_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With an instant fake the keeper's first claim is over before any request, so
    deleting the lifespan's wait for it changes nothing. A claim that takes a moment
    -- as every real database round trip does -- is what the wait is for: the first
    /health after boot must report the answer, not a state nobody has asked about."""
    client = StrictClient(False, delay=0.3)
    _boot_with(monkeypatch, client)

    started = time.monotonic()
    with TestClient(app) as test_client:
        booted_after = time.monotonic() - started
        response = test_client.get("/health")  # the very first request

    assert booted_after >= 0.3  # startup really did wait for the claims
    assert response.status_code == 200
    for worker in THE_THREE:
        assert response.json()["workers"][worker]["status"] == "standby"


async def test_a_normal_shutdown_stops_every_worker_and_every_keeper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StrictClient(True)
    _boot_with(monkeypatch, client)
    fresh = FastAPI()

    async with app_module.lifespan(fresh):
        registry = fresh.state.workers
        assert any(
            not t.done() for t in asyncio.all_tasks() if t.get_name().startswith("worker-lease:")
        )

    assert all(state.task is None or state.task.done() for state in registry.workers.values())
    assert [
        t for t in asyncio.all_tasks() if t.get_name().startswith("worker-lease:") and not t.done()
    ] == []


async def test_shutdown_waits_for_product_events_still_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An event whose insert is still running when the app shuts down (a deploy) is written, not
    lost: the lifespan flushes before it closes the clients. The writer takes real time (0.2 s),
    so a flush that is missing, or has a zero timeout, returns before the insert finishes."""
    client = StrictClient(True)
    _boot_with(monkeypatch, client)
    written: list[dict[str, Any]] = []

    async def slow_write(_supabase: object, row: dict[str, Any]) -> bool:
        await asyncio.sleep(0.2)
        written.append(row)
        return True

    # after the suite's autouse no-op writer, so this one is the one the emitter schedules
    monkeypatch.setattr(product_events, "write_event", slow_write)

    async with app_module.lifespan(FastAPI()):
        product_events.emit_event(
            client,  # type: ignore[arg-type]
            "00000000-0000-0000-0000-0000000000a1",
            "discover_search",
            outcome="ok",
        )
        assert written == []  # still in flight while the app is running

    assert [row["event"] for row in written] == ["discover_search"]


def test_each_leased_loop_is_given_the_state_that_carries_its_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The loops are idle in these tests, so nothing else would notice one of them
    losing `state=state`: that worker would build its own lease-less state, tick on
    every replica unconditionally -- the exact failure the lease exists to prevent --
    while the keeper went on claiming and /health went on saying `held`."""
    client = StrictClient(True)
    _boot_with(monkeypatch, client)
    seen: dict[str, Any] = {}

    def recorder(name: str) -> Any:
        async def loop(*_args: Any, **kwargs: Any) -> None:
            seen[name] = kwargs.get("state")
            await asyncio.Event().wait()

        return loop

    for loop_name, worker in (
        ("run_worker_forever", "outbox"),
        ("run_poller_forever", "job_registry_poller"),
        ("run_matcher_forever", "saved_search_matcher"),
        ("run_reply_check_forever", "gmail_reply_checker"),
        ("run_hiring_cache_purge_forever", "hiring_signal_cache_purge"),
    ):
        monkeypatch.setattr(app_module, loop_name, recorder(worker))

    with TestClient(app) as test_client:
        registry = test_client.app.state.workers.workers  # type: ignore[attr-defined]
        deadline = time.monotonic() + 5
        while len(seen) < 5 and time.monotonic() < deadline:
            time.sleep(0.01)

    assert set(seen) == THE_THREE | OTHERS
    for worker in THE_THREE:
        assert seen[worker] is registry[worker] and seen[worker].lease is not None
    for worker in OTHERS:
        assert seen[worker] is registry[worker] and seen[worker].lease is None


def test_the_keeper_stops_renewing_when_that_workers_tick_has_outrun_its_stale_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wedge gate as app.py wires it (`wants_lease`), for each leased worker: a
    tick that has been running longer than the worker's own stale window must make the
    keeper stop renewing, or a stuck holder blocks every standby forever; a tick well
    inside the window must not."""
    client = StrictClient(True)
    _boot_with(monkeypatch, client)

    with TestClient(app) as test_client:
        registry = test_client.app.state.workers.workers  # type: ignore[attr-defined]
        for worker in THE_THREE:
            state = registry[worker]
            window = state.stale_after.total_seconds()
            assert state.lease._wants()  # idle: nothing running
            state.current_tick_started = time.monotonic() - 5
            assert state.lease._wants()  # a short tick
            state.current_tick_started = time.monotonic() - window - 1
            assert not state.lease._wants()  # one that has outrun the window
            state.current_tick_started = None


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
