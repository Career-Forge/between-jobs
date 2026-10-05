"""The machinery behind the cross-tenant suite.

Two real users (A and B) exist in a local Supabase stack. Each case seeds rows owned by A,
then sends the request that would read or change them as B, with B's real access token, to
the real FastAPI app. B must get the answer a stranger gets (404 for a resource id, an
unrelated 200 for a list), nothing of A's may appear in the body, and A's data must be
unchanged. The same request as A is the control: it proves the request was valid, so a 404
for B is not a typo in the path.

Nothing here may reach a third party: `loopback_only` refuses every socket that is not the
local Supabase stack, so a route that would call an LLM, Google or a job board fails loudly
instead of spending anyone's money.
"""

from __future__ import annotations

import contextlib
import importlib
import pkgutil
import socket
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from supabase import AsyncClient

from ..conftest import World


class ExternalCallBlocked(RuntimeError):
    """A test tried to open a connection to something that is not the local stack."""


@contextlib.contextmanager
def loopback_only(allowed_ports: set[int]) -> Iterator[None]:
    """Refuses every network connection except to the local stack's own ports."""
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def check(sock: socket.socket, address: Any) -> None:
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            host, port = address[0], address[1]
            if host not in {"127.0.0.1", "::1", "localhost"} or port not in allowed_ports:
                raise ExternalCallBlocked(
                    f"the cross-tenant suite may only talk to the local stack, not {host}:{port}"
                )

    def connect(self: socket.socket, address: Any) -> Any:
        check(self, address)
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> Any:
        check(self, address)
        return real_connect_ex(self, address)

    socket.socket.connect = connect  # type: ignore[method-assign,assignment]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign,assignment]
    try:
        yield
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = real_connect_ex  # type: ignore[method-assign]


# -- the two tenants --------------------------------------------------------------------------


@dataclass(frozen=True)
class Tenant:
    label: Literal["A", "B"]
    user_id: str
    email: str
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


@dataclass
class Pair:
    """User A (the owner) and user B (the stranger), plus every string seeded for each, so a
    leak is caught no matter which case seeded it."""

    a: Tenant
    b: Tenant
    marks: dict[str, list[str]] = field(default_factory=lambda: {"A": [], "B": []})


# -- the per-case context and the seeders ------------------------------------------------------


@dataclass(frozen=True)
class Seeder:
    kind: str
    fn: Callable[[Ctx, Tenant], Awaitable[dict[str, Any]]]
    tables: tuple[str, ...]


SEEDERS: dict[str, Seeder] = {}


def seeder(kind: str, *, tables: tuple[str, ...]) -> Callable[[Any], Any]:
    """Registers `async def seed(ctx, tenant) -> dict` as the way to make a `kind` of row for a
    tenant. `tables` names every table the seeder writes a user's row into: the RLS suite
    needs to know which tables it has a real row for. A seeder may `await ctx.need(...)` for
    what it builds on, and registers every user-visible string with `ctx.mark`."""

    def register(fn: Callable[[Ctx, Tenant], Awaitable[dict[str, Any]]]) -> Any:
        if kind in SEEDERS:
            raise RuntimeError(f"two seeders for {kind!r}")
        SEEDERS[kind] = Seeder(kind, fn, tables)
        return fn

    return register


class Ctx:
    """What one case gets: the two tenants, the service-role client, and `need` to seed rows."""

    def __init__(self, world: World, pair: Pair) -> None:
        self.world = world
        self.sb: Any = world.sb
        self.pair = pair
        self.a = pair.a
        self.b = pair.b
        self._seeded: dict[tuple[str, str], dict[str, Any]] = {}
        # Scratch space shared by one case's request builder and its `unchanged` hook (a value
        # the request generated, which the hook then looks for).
        self.state: dict[str, Any] = {}

    async def need(self, kind: str, tenant: Tenant | None = None) -> dict[str, Any]:
        tenant = tenant or self.a
        key = (tenant.label, kind)
        if key not in self._seeded:
            self._seeded[key] = await SEEDERS[kind].fn(self, tenant)
        return self._seeded[key]

    def tag(self, prefix: str) -> str:
        """A unique string, for a name or a title that must never show up for another user."""
        return f"{prefix}-{uuid.uuid4().hex[:10]}"

    def mark(self, tenant: Tenant, text: str) -> str:
        self.pair.marks[tenant.label].append(text)
        return text


# -- the cases ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Req:
    method: str
    url: str
    json: Any = None
    params: dict[str, str] | None = None
    data: dict[str, str] | None = None
    files: dict[str, tuple[str, bytes, str]] | None = None
    headers: dict[str, str] | None = None

    def send_kwargs(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in ("json", "params", "data", "files"):
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        return out


@dataclass(frozen=True)
class Case:
    """One operation, tried as a stranger.

    `operation` is exactly the OpenAPI key, e.g. "GET /applications/{application_id}".
    `request` builds the request that targets A's rows (it seeds what it needs through
    `ctx.need`). `kind="by_id"` expects `foreign_status` (404) for B; `kind="list"` expects a
    200 whose body holds B's own rows (seeded through `b_seeds`) and none of A's.
    `owner_status` is what A gets for the same request: the control. `unchanged` is awaited
    after B's attempt and must show A's data was not touched.
    """

    operation: str
    request: Callable[[Ctx], Awaitable[Req]]
    kind: Literal["by_id", "list"] = "by_id"
    foreign_status: int = 404
    owner_status: frozenset[int] = frozenset({200})
    b_seeds: tuple[str, ...] = ()
    unchanged: Callable[[Ctx], Awaitable[None]] | None = None
    note: str = ""


@dataclass(frozen=True)
class StoreCase:
    """One store function that filters rows by the caller, called as a stranger.

    `target` is "<module>.<function>" (a method is "<module>.<Class>.<method>"), as
    `ownership_scan` names it. `run` seeds A's rows through `ctx.need`, calls the function as B
    with A's ids and asserts what a stranger must get (not found, an empty result, `None`, or an
    unchanged row afterward), then reads A's data back with the service-role client to prove
    nothing moved. It must also call the function as A once, so an empty answer for B cannot be
    an empty answer for everyone.
    """

    target: str
    run: Callable[[Ctx], Awaitable[None]]
    note: str = ""


# Why an operation has no cross-tenant case. `category` is checked by the meta-test: only
# these kinds of operation may skip the case, and only "shared" and "secret" ones may take a
# path id.
Category = Literal["public", "secret", "shared", "own-identity"]


@dataclass(frozen=True)
class NotUserScoped:
    category: Category
    reason: str


def _modules(prefix: str) -> list[Any]:
    package = importlib.import_module(__package__ or "tests.integration.cross_tenant")
    names = sorted(
        m.name for m in pkgutil.iter_modules(package.__path__) if m.name.startswith(prefix)
    )
    return [importlib.import_module(f"{package.__name__}.{name}") for name in names]


def load_cases() -> list[Case]:
    """Every `CASES` list in every `cases_*` module (importing them registers their seeders)."""
    importlib.import_module(f"{__package__}.seeds_core")
    importlib.import_module(f"{__package__}.seeds_rls")
    cases: list[Case] = []
    for module in _modules("cases_"):
        cases.extend(getattr(module, "CASES", []))
    return cases


def load_store_cases() -> list[StoreCase]:
    """Every `STORE_CASES` list in every `stores_*` module."""
    importlib.import_module(f"{__package__}.seeds_core")
    importlib.import_module(f"{__package__}.seeds_rls")
    cases: list[StoreCase] = []
    for module in _modules("stores_"):
        cases.extend(getattr(module, "STORE_CASES", []))
    return cases


def load_not_user_scoped() -> dict[str, NotUserScoped]:
    excused: dict[str, NotUserScoped] = {}
    for module in _modules("cases_"):
        for operation, why in getattr(module, "NOT_USER_SCOPED", {}).items():
            if operation in excused:
                raise RuntimeError(f"{operation!r} is excused twice")
            excused[operation] = why
    return excused


def operations(app: Any) -> set[str]:
    """Every "METHOD /path" the app serves, from its own OpenAPI document."""
    found: set[str] = set()
    for path, ops in app.openapi()["paths"].items():
        for method in ops:
            if method.upper() != "HEAD":
                found.add(f"{method.upper()} {path}")
    return found


# -- running a case ----------------------------------------------------------------------------


async def run_case(case: Case, ctx: Ctx, client: httpx.AsyncClient) -> None:
    for kind in case.b_seeds:
        await ctx.need(kind, ctx.b)
    req = await case.request(ctx)

    foreign = await client.request(
        req.method, req.url, headers={**ctx.b.headers, **(req.headers or {})}, **req.send_kwargs()
    )
    where = f"{case.operation} as B -> {foreign.status_code} {foreign.text[:300]!r}"
    assert foreign.status_code == case.foreign_status, where
    leaked = [m for m in ctx.pair.marks["A"] if m in foreign.text]
    assert not leaked, f"{case.operation} showed B something of A's: {leaked[:3]} ({where})"
    if case.kind == "list":
        assert any(m in foreign.text for m in ctx.pair.marks["B"]), (
            f"{case.operation}: B's own rows are missing from B's list, "
            "so 'none of A's' proves nothing"
        )
    if case.unchanged is not None:
        await case.unchanged(ctx)

    owner = await client.request(
        req.method, req.url, headers={**ctx.a.headers, **(req.headers or {})}, **req.send_kwargs()
    )
    assert owner.status_code in case.owner_status, (
        f"{case.operation} as A (the control) -> {owner.status_code} {owner.text[:300]!r}, "
        f"expected one of {sorted(case.owner_status)}: the request itself is not valid, so B's "
        "404 proves nothing"
    )


# -- the two real users ------------------------------------------------------------------------


async def make_tenant(world: World, label: Literal["A", "B"]) -> Tenant:
    """A real user with a real access token, removed (with everything it owns) when the world
    is cleaned up."""
    from supabase import acreate_client

    sb: AsyncClient = world.sb
    stack = world.stack

    email = f"tenant-{label.lower()}-{uuid.uuid4().hex[:10]}@example.com"
    password = uuid.uuid4().hex
    created = await sb.auth.admin.create_user(
        {"email": email, "password": password, "email_confirm": True}
    )
    user_id = str(created.user.id)
    world.users.append(user_id)
    anon = await acreate_client(stack["API_URL"], stack["ANON_KEY"])
    session = await anon.auth.sign_in_with_password({"email": email, "password": password})
    assert session.session is not None
    return Tenant(label, user_id, email, session.session.access_token)


async def aclose_quietly(*closeables: Any) -> None:
    for closeable in closeables:
        with contextlib.suppress(Exception):
            await closeable.aclose()


__all__ = [
    "SEEDERS",
    "Case",
    "Ctx",
    "ExternalCallBlocked",
    "NotUserScoped",
    "Pair",
    "Req",
    "StoreCase",
    "Tenant",
    "load_cases",
    "load_not_user_scoped",
    "load_store_cases",
    "loopback_only",
    "make_tenant",
    "operations",
    "run_case",
    "seeder",
]
