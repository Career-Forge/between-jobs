"""Module-scoped fixtures shared by the cross-tenant suites: a service-role world, two real
users, and the real app behind an ASGI client. Import them into a test module; they are not
in a conftest so the other integration tests keep their function-scoped `world`."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from urllib.parse import urlparse

import asyncpg
import httpx
import pytest
import pytest_asyncio

from supabase import acreate_client

from ..conftest import World
from .harness import Pair, loopback_only, make_tenant


@pytest.fixture(scope="module")
def tenant_env(local_stack: dict[str, str]) -> Iterator[None]:
    """Points the app at the local stack with every background worker off (the global conftest
    does this per test; a module-scoped fixture needs it once, here)."""
    patch = pytest.MonkeyPatch()
    patch.setenv("SUPABASE_URL", local_stack["API_URL"])
    patch.setenv("SUPABASE_SERVICE_ROLE_KEY", local_stack["SERVICE_ROLE_KEY"])
    for flag in (
        "DISABLE_OUTBOX_WORKER",
        "DISABLE_JOB_REGISTRY_POLLER",
        "DISABLE_SAVED_SEARCH_MATCHER",
        "DISABLE_GMAIL_REPLY_CHECKER",
        "DISABLE_HIRING_SIGNAL_CACHE_PURGE",
        "DISABLE_HEALTH_DEPENDENCY_CHECKS",
    ):
        patch.setenv(flag, "1")
    patch.setenv("WORKER_LEASES", "off")
    try:
        yield
    finally:
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def tenant_world(local_stack: dict[str, str]) -> AsyncIterator[World]:
    sb = await acreate_client(local_stack["API_URL"], local_stack["SERVICE_ROLE_KEY"])
    pg = await asyncpg.connect(local_stack["DB_URL"])
    world = World(sb, pg, local_stack)
    try:
        yield world
    finally:
        await world.cleanup()
        await pg.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def tenant_pair(tenant_world: World) -> Pair:
    return Pair(await make_tenant(tenant_world, "A"), await make_tenant(tenant_world, "B"))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def tenant_client(
    local_stack: dict[str, str], tenant_env: None
) -> AsyncIterator[httpx.AsyncClient]:
    """The real app, lifespan and all, reachable only from this process, and allowed to open
    nothing but the local stack's own sockets."""
    from between_jobs.api.app import app, lifespan

    ports = {urlparse(local_stack[key]).port for key in ("API_URL", "DB_URL")}
    with loopback_only({p for p in ports if p is not None}):
        async with lifespan(app):
            host = urlparse(app.state.supabase_url).hostname
            assert host in {"127.0.0.1", "localhost", "::1"}, f"not the local stack: {host}"
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
                yield http
