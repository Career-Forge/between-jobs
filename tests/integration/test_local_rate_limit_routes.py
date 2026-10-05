"""The rate limit on real routes, against a real local stack.

The real FastAPI app (its real lifespan, authentication and limiter) runs against a real local
Supabase stack, with two real users. The requests used all fail fast and locally -- an
application that does not exist, a profile that was never imported -- so nothing here calls a
model, a provider or the LaTeX service (and `loopback_only` would stop it if anything tried), but
every one of them is a request that passed authentication and so counts.

Run with `pytest -m local_supabase` after `supabase start` and `supabase db reset --local`."""

from __future__ import annotations

import uuid

import httpx
import pytest

from between_jobs.api import credentials_routes

from .conftest import World
from .cross_tenant.harness import Pair, make_tenant

pytestmark = [pytest.mark.local_supabase, pytest.mark.asyncio(loop_scope="module")]

_IDEMPOTENCY_KEY = "k" * 20


def _prepare_url() -> str:
    return f"/applications/{uuid.uuid4()}/prepare"


async def _counts(world: World, user_id: str) -> dict[str, int]:
    rows = await world.pg.fetch(
        "select bucket, request_count from public.api_rate_limits where user_id = $1",
        uuid.UUID(user_id),
    )
    return {r["bucket"]: r["request_count"] for r in rows}


async def _reset(world: World, user_id: str) -> None:
    await world.pg.execute(
        "delete from public.api_rate_limits where user_id = $1", uuid.UUID(user_id)
    )


async def test_the_eleventh_prepare_in_an_hour_is_a_429_and_nobody_elses_budget_is_touched(
    tenant_client: httpx.AsyncClient, tenant_world: World, tenant_pair: Pair
) -> None:
    a, b = tenant_pair.a, tenant_pair.b
    await _reset(tenant_world, a.user_id)
    await _reset(tenant_world, b.user_id)
    body = {"idempotency_key": _IDEMPOTENCY_KEY}

    # Ten attempts, all of which pass authentication and fail at "no such application" (404):
    # a failed attempt still counts.
    for attempt in range(10):
        response = await tenant_client.post(_prepare_url(), json=body, headers=a.headers)
        assert response.status_code == 404, (attempt, response.text)
    assert await _counts(tenant_world, a.user_id) == {"prepare": 10}

    refused = await tenant_client.post(
        _prepare_url(), json=body, headers={**a.headers, "X-Request-ID": "rate-limit-test-1"}
    )

    assert refused.status_code == 429
    assert refused.headers["x-request-id"] == "rate-limit-test-1"
    retry_after = int(refused.headers["retry-after"])
    assert 1 <= retry_after <= 3600
    error = refused.json()["error"]
    assert error["code"] == "RATE_LIMITED"
    assert error["retryable"] is True
    assert error["details"] == {"retry_after_seconds": retry_after, "bucket": "prepare"}
    assert "10 per hour" in error["message"]
    # A refusal is not counted again.
    assert await _counts(tenant_world, a.user_id) == {"prepare": 10}

    # Another user has their own budget ...
    assert (
        await tenant_client.post(_prepare_url(), json=body, headers=b.headers)
    ).status_code == 404
    assert await _counts(tenant_world, b.user_id) == {"prepare": 1}
    # ... and the first user's other actions are not affected.
    other = await tenant_client.get("/discover", headers=a.headers)
    assert other.status_code == 409  # SETUP_REQUIRED: no profile -- reached the route
    assert other.json()["error"]["code"] == "SETUP_REQUIRED"

    await _reset(tenant_world, a.user_id)
    await _reset(tenant_world, b.user_id)


async def test_a_request_that_fails_authentication_is_not_counted_and_never_touches_the_counter(
    tenant_client: httpx.AsyncClient, tenant_world: World, tenant_pair: Pair
) -> None:
    a = tenant_pair.a
    await _reset(tenant_world, a.user_id)
    body = {"idempotency_key": _IDEMPOTENCY_KEY}

    none = await tenant_client.post(_prepare_url(), json=body)
    forged = await tenant_client.post(
        _prepare_url(), json=body, headers={"Authorization": "Bearer not.a.token"}
    )

    assert (none.status_code, forged.status_code) == (401, 401)
    assert await _counts(tenant_world, a.user_id) == {}


async def test_a_request_with_an_invalid_body_still_counts(
    tenant_client: httpx.AsyncClient, tenant_world: World, tenant_pair: Pair
) -> None:
    """The counting policy: every attempt that has passed authentication counts."""
    a = tenant_pair.a
    await _reset(tenant_world, a.user_id)

    response = await tenant_client.post(_prepare_url(), json={"nope": 1}, headers=a.headers)

    assert response.status_code == 422
    assert await _counts(tenant_world, a.user_id) == {"prepare": 1}
    await _reset(tenant_world, a.user_id)


async def test_the_web_and_extension_pdf_routes_draw_on_one_budget(
    tenant_client: httpx.AsyncClient, tenant_world: World, tenant_pair: Pair
) -> None:
    """Both compile the same LaTeX in the same service, so the extension's scoped-token routes
    count against the same 'pdf_compile' bucket as the web app's -- by the same user id."""
    a = tenant_pair.a
    await _reset(tenant_world, a.user_id)
    application = uuid.uuid4()

    web = await tenant_client.get(f"/applications/{application}/resume.pdf", headers=a.headers)
    cover = await tenant_client.get(
        f"/applications/{application}/cover-letter.pdf", headers=a.headers
    )
    extension = await tenant_client.get(f"/extension/{application}/resume.pdf", headers=a.headers)

    assert (web.status_code, cover.status_code, extension.status_code) == (404, 404, 404)
    assert await _counts(tenant_world, a.user_id) == {"pdf_compile": 3}
    await _reset(tenant_world, a.user_id)


async def test_a_limiter_that_cannot_reach_its_function_lets_the_request_through(
    tenant_client: httpx.AsyncClient,
    tenant_world: World,
    tenant_pair: Pair,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fail open, against the real app: the RPC is made to fail the way a missing migration
    would, and the request still gets its real answer."""
    from between_jobs.api.app import app

    a = tenant_pair.a
    await _reset(tenant_world, a.user_id)

    class _BrokenRpc:
        async def execute(self) -> None:
            raise RuntimeError("function public.claim_rate_limit_slot does not exist")

    real_rpc = app.state.supabase.rpc

    def rpc(name: str, params: dict[str, object]) -> object:
        if name == "claim_rate_limit_slot":
            return _BrokenRpc()
        return real_rpc(name, params)

    monkeypatch.setattr(app.state.supabase, "rpc", rpc)

    with caplog.at_level("ERROR", logger="between_jobs.api.rate_limits"):
        response = await tenant_client.post(
            _prepare_url(), json={"idempotency_key": _IDEMPOTENCY_KEY}, headers=a.headers
        )

    assert response.status_code == 404  # the route's own answer, not a 429 and not a 500
    assert any("rate limiter unavailable" in r.getMessage() for r in caplog.records)
    assert await _counts(tenant_world, a.user_id) == {}  # and nothing was counted


async def test_a_deleted_accounts_still_valid_token_gets_no_free_pass_on_the_key_check(
    tenant_client: httpx.AsyncClient,
    tenant_world: World,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Authentication checks a token's signature and expiry, never that the user still exists,
    so the access token of a deleted account keeps working until it expires. Saving a credential
    calls the provider to check the key, which is the call 'credential_save' exists to bound;
    if the limiter let this caller through (its first counter insert fails on the missing user)
    it would be an unlimited oracle for other people's keys. It must be refused instead, and
    before the provider is called."""
    ghost = await make_tenant(tenant_world, "B")
    checked: list[str] = []

    async def counting_validator(_http: object, secret: str) -> None:
        checked.append(secret)

    monkeypatch.setitem(credentials_routes._VALIDATORS, ("search", "serper"), counting_validator)
    body = {"service": "search", "provider": "serper", "secret": "not-a-real-key"}

    # Control: while the account exists the same request reaches the provider check.
    before = await tenant_client.post("/credentials", json=body, headers=ghost.headers)
    assert before.status_code == 201, before.text
    assert checked == ["not-a-real-key"]

    await tenant_world.sb.auth.admin.delete_user(ghost.user_id)
    checked.clear()

    with caplog.at_level("WARNING", logger="between_jobs.api.rate_limits"):
        for _ in range(3):
            response = await tenant_client.post("/credentials", json=body, headers=ghost.headers)
            assert response.status_code == 401, response.text
            assert response.json()["error"]["code"] == "AUTH_REQUIRED"

    assert checked == []  # the provider was never asked
    assert not any(r.levelno >= 40 for r in caplog.records if r.name.endswith("rate_limits"))
