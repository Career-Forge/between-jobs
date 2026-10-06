"""Which routes carry a per-user rate limit, and a guard that a new expensive one does not slip
in without one (api/rate_limits.py).

Two guards, from two angles, because either alone has a hole:

1. THE INVENTORY. Every HTTP route the app serves is in exactly one of two dicts below: LIMITED
   (route -> bucket) or UNLIMITED (route -> why it is safe to leave unlimited). The test walks
   the app's real routes and their real dependency trees and requires both dicts to match what
   is actually wired, so a route cannot gain or lose its limiter without this file changing, and
   a NEW route fails until someone has decided which dict it belongs in. The walk fails loudly
   on anything it cannot inventory (a mounted sub-app, a websocket) rather than skipping it.
   The hole: whoever adds an expensive route can file it under UNLIMITED with a
   plausible-sounding reason.

2. THE SCAN. A static look (ast) at every module that serves a route, which fails when a route
   handler -- or any helper in the same module it calls -- reaches for something that spends
   money or heavy compute (an LLM call, a search or scrape provider, the forge-engines client,
   the LaTeX compiler) and the handler neither carries a limiter nor is named in SCAN_EXEMPT
   with a reason. It does not care what the inventory says, so it catches the expensive route
   filed under UNLIMITED. What it can see is bounded: names reached in the handler's own
   module and its same-module helpers. A pipeline function in another module whose model call
   is a DEFAULT ARGUMENT is invisible unless it is listed in EXPENSIVE_NAMES, or the handler
   passes `generate=llm_generate` explicitly (every handler does today, which is the
   convention the scan leans on). Adding to EXPENSIVE_NAMES is how a new kind of expensive
   call gets covered.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, WebSocket
from fastapi.routing import APIRoute

from between_jobs.api.app import app
from between_jobs.api.auth import require_user_id
from between_jobs.api.extension_auth import require_active_extension_user_id
from between_jobs.api.rate_limits import RATE_LIMITS, limiter_bucket

_SRC = Path(__file__).resolve().parents[1] / "src" / "between_jobs" / "api"

# -- 1. the inventory ----------------------------------------------------------------------

LIMITED: dict[str, str] = {
    # The four most expensive actions, each with its own set limit.
    "GET /discover": "discover",
    "POST /applications/{application_id}/prepare": "prepare",
    "POST /applications/{application_id}/company-intel": "company_intel",
    "POST /applications/{application_id}/interview-practice/sessions": "interview_practice_session",
    # Everything else that calls an LLM, a search or scrape provider, the forge-engines
    # service or the LaTeX compiler.
    "POST /applications/{application_id}/interview-practice/sessions/{session_id}/answers": (
        "interview_practice_answer"
    ),
    "POST /applications/{application_id}/contacts": "contact_research",
    "POST /applications/{application_id}/contacts/{candidate_id}/enrich": "contact_lookup",
    "POST /applications/{application_id}/contacts/{candidate_id}/find-linkedin": "contact_lookup",
    "POST /applications/{application_id}/contacts/{candidate_id}/draft-outreach": "outreach_draft",
    "POST /applications/{application_id}/positioning-brief": "positioning_brief",
    "POST /applications/{application_id}/warm-path-events": "warm_path_events",
    "POST /applications/{application_id}/hiring-signals/search": "hiring_signal_search",
    "POST /hiring-signals/search": "hiring_signal_search",
    "POST /resume-documents/{document_id}/coverage": "tailor_coverage",
    "POST /resume-documents/{document_id}/gap-interview": "gap_interview",
    "POST /profile/gap-interview/draft": "gap_interview",
    "POST /resume-documents/{document_id}/header/preview": "header_preview",
    "GET /applications/{application_id}/resume.pdf": "pdf_compile",
    "GET /applications/{application_id}/cover-letter.pdf": "pdf_compile",
    "GET /applications/{application_id}/export-checklist": "pdf_compile",
    "GET /extension/{application_id}/resume.pdf": "pdf_compile",
    "GET /extension/{application_id}/cover-letter.pdf": "pdf_compile",
    # A scrape on the user's own key (or a registry hit), and the same act from Discover.
    "POST /applications/from-url": "job_ingest",
    "POST /discover/track": "job_ingest",
    # Saving a key makes a live call to the provider to check it.
    "POST /credentials": "credential_save",
    # One small insert per finished autofill, bounded so a stuck client cannot grow the log.
    "POST /extension/fill-outcome": "fill_outcome",
}
"""Route -> bucket. Routes authenticated with the extension's scoped token are listed too; the
limiter on them reads the user from that token (see
`test_limiters_authenticate_the_way_their_route_does`)."""

_DB_ONLY = "reads or writes the caller's own rows only: no model, provider, engine or compile call"

UNLIMITED: dict[str, str] = {
    # -- not a signed-in user's request at all
    "GET /health": "unauthenticated liveness probe; its dependency checks are cached for 30 s",
    "POST /telegram/webhook": (
        "authenticated by Telegram's shared secret, not a user session, so it cannot carry a "
        "per-user route dependency; its one expensive action, generating a resume, claims the "
        "'prepare' bucket itself (channel_core._start_prepare); an uploaded .json "
        "resume is held to the same size cap as a pasted one (body_limit.py), checked against "
        "Telegram's declared size and again while downloading"
    ),
    "GET /oauth/gmail/callback": (
        "authenticated by the single-use state minted for a signed-in user; the Google code "
        "exchange only happens for a valid state, so a stranger cannot reach it"
    ),
    # -- one cheap call or a database write
    "GET /capabilities": "returns two process-wide flags; reads nothing of the user's",
    "GET /hiring-signals/status": "returns one process-wide flag; reads nothing of the user's",
    "POST /account/delete": (
        "deletes the caller's own account once, behind a typed confirmation, and is idempotent; "
        "not something a loop can repeat to cost anything"
    ),
    "POST /link/code": "mints one short-lived code row for the caller; one small insert",
    "GET /profile/integrations/gmail/connect": "mints one single-use OAuth state row",
    "POST /applications/{application_id}/contacts/{candidate_id}/push-to-gmail": (
        "one call to the caller's own Gmail on their own grant, and a no-op once pushed "
        "(it returns the existing draft); no model or paid provider"
    ),
    "POST /profile/versions": (
        "deterministic local parse of the pasted JSON and a few inserts, no model call; the body "
        "is capped by the request size limit (body_limit.py), and so is the same import sent "
        "as a Telegram upload"
    ),
    # -- the extension's own cheap routes, and the one with its own limiter
    "POST /extension/draft-answer": (
        "has its own dedicated limiter, extension_rate_limit.claim_draft_answer_slot (20 per 10 "
        "minutes), which predates this one and is deliberately left as it is"
    ),
    "GET /extension/lookup": _DB_ONLY,
    "POST /extension/sign-out": _DB_ONLY,
    "GET /extension/field-maps/{ats_type}": "one read of a stored, signed document; " + _DB_ONLY,
    "POST /extension/match-answer": _DB_ONLY,
    "POST /extension/approved-answers": _DB_ONLY,
    "GET /applications/{application_id}/extension-payload": _DB_ONLY,
    # -- plain reads and writes of the caller's own rows
    "GET /applications": _DB_ONLY,
    "POST /applications": _DB_ONLY,
    "GET /applications/{application_id}": _DB_ONLY,
    "POST /applications/{application_id}/stage": _DB_ONLY,
    "GET /applications/{application_id}/prepare-result": _DB_ONLY,
    "GET /applications/{application_id}/company-intel": _DB_ONLY,
    "GET /applications/{application_id}/contacts": _DB_ONLY,
    "GET /applications/{application_id}/contacts/{candidate_id}/draft-outreach": _DB_ONLY,
    "GET /applications/{application_id}/positioning-brief": _DB_ONLY,
    "GET /applications/{application_id}/warm-path-events": _DB_ONLY,
    "GET /applications/{application_id}/interview-practice/sessions": _DB_ONLY,
    "GET /applications/{application_id}/interview-practice/sessions/{session_id}": _DB_ONLY,
    "GET /applications/{application_id}/hiring-signals/saves": _DB_ONLY,
    "POST /applications/{application_id}/hiring-signals/saves": _DB_ONLY,
    "GET /hiring-signals/saves": _DB_ONLY,
    "POST /hiring-signals/saves": _DB_ONLY,
    "DELETE /hiring-signals/saves/{save_id}": _DB_ONLY,
    "GET /hiring-signals/searches": _DB_ONLY,
    "POST /hiring-signals/searches": _DB_ONLY,
    "DELETE /hiring-signals/searches/{search_id}": _DB_ONLY,
    "GET /credentials": _DB_ONLY,
    "DELETE /credentials/{service}/{provider}": _DB_ONLY,
    "GET /profile/current": _DB_ONLY,
    "GET /profile/versions/{version_id}": _DB_ONLY,
    "GET /profile/versions/{version_id}/career-facts": _DB_ONLY,
    "POST /profile/versions/{version_id}/activate": _DB_ONLY,
    "DELETE /profile/versions/{version_id}": _DB_ONLY,
    "POST /profile/gap-interview/approve": (
        "the deterministic apply step of the gap interview: no model call, one new pending "
        "profile version"
    ),
    "GET /resume-documents/mine": _DB_ONLY,
    "PATCH /resume-documents/{document_id}/header": _DB_ONLY,
    "PATCH /resume-documents/{document_id}/evidence": _DB_ONLY,
    "PATCH /resume-documents/{document_id}/assertions": _DB_ONLY,
    "PATCH /resume-documents/{document_id}/shape": _DB_ONLY,
    "PATCH /resume-documents/{document_id}/sections": _DB_ONLY,
    "GET /saved-searches": _DB_ONLY,
    "POST /saved-searches": (
        "one small insert, no model, provider or engine call; bounded in size by the request "
        "model and the table's CHECK constraints, and in number by the per-user cap that a "
        "trigger enforces (see test_saved_searches_routes.py); the background matcher that "
        "scores what it stores reads at most a fixed number per tick"
    ),
    "PATCH /saved-searches/{search_id}": _DB_ONLY,
    "DELETE /saved-searches/{search_id}": _DB_ONLY,
    "GET /today": _DB_ONLY,
    "POST /today/{item_id}/dismiss": _DB_ONLY,
    "POST /today/{item_id}/accept-proposal": _DB_ONLY,
    "POST /today/{item_id}/dismiss-proposal": _DB_ONLY,
}
"""Route -> why leaving it unlimited is safe. Every route the app serves that is not in LIMITED."""


@dataclass(frozen=True)
class _Served:
    method: str
    path: str
    dependant: Any
    endpoint: Callable[..., Any]

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


_FRAMEWORK_PATHS = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})
"""The four plain (non-API) routes FastAPI adds on its own: the schema and its two doc pages."""


def _walk(target: Any) -> tuple[list[_Served], list[str]]:
    """(every HTTP operation `target` serves, with its full dependency tree -- the router's and
    the route's own; a description of everything else it serves that the walk cannot inventory).
    FastAPI at 0.141 wraps an included router in a lazy object whose effective routes carry the
    same `path`, `methods`, `dependant` and `endpoint` an `APIRoute` does; older versions put the
    `APIRoute`s straight into `routes`. Both are read here. Anything that is neither an HTTP
    operation nor one of FastAPI's own four doc routes (a mounted sub-app, a websocket, a plain
    Starlette route) comes back in the second list: it would carry no limiter and no inventory
    entry, so it must fail loudly instead of being skipped."""
    found: list[_Served] = []
    unseen: list[str] = []

    def describe(item: Any) -> tuple[str, str]:
        original = getattr(item, "original_route", item)
        path = getattr(item, "path", None) or getattr(original, "path", "") or ""
        return type(original).__name__, str(path)

    def visit(item: Any) -> None:
        if isinstance(item, APIRoute) or (
            hasattr(item, "original_route") and getattr(item, "dependant", None) is not None
        ):
            for method in sorted(item.methods - {"HEAD", "OPTIONS"}):
                found.append(_Served(method, item.path, item.dependant, item.endpoint))
        elif hasattr(item, "effective_candidates"):
            for candidate in item.effective_candidates():
                visit(candidate)
        else:
            kind, path = describe(item)
            if not (kind == "Route" and path in _FRAMEWORK_PATHS):
                unseen.append(f"{kind} {path}")

    for route in target.routes:
        visit(route)
    return found, unseen


def _served_routes() -> list[_Served]:
    return _walk(app)[0]


def _dependencies(dependant: Any) -> Iterator[Any]:
    for sub in dependant.dependencies:
        yield sub
        yield from _dependencies(sub)


def _limiters(served: _Served) -> list[tuple[Any, str]]:
    """(the limiter's Dependant, its bucket) for each limiter in the route's dependency tree."""
    return [
        (sub, bucket)
        for sub in _dependencies(served.dependant)
        if (bucket := limiter_bucket(sub.call)) is not None
    ]


def _openapi_operations() -> set[str]:
    operations = set()
    for path, ops in app.openapi()["paths"].items():
        for method in ops:
            if method.upper() != "HEAD":
                operations.add(f"{method.upper()} {path}")
    return operations


def test_the_walk_sees_every_route_the_app_serves() -> None:
    """Guards the guard: the walk above reads FastAPI internals, so check it against the
    OpenAPI document, which lists every operation from a different code path."""
    walked = {served.key for served in _served_routes()}
    assert len(walked) > 60
    assert walked == _openapi_operations()


def test_the_app_serves_nothing_the_walk_cannot_see() -> None:
    """A mounted sub-app or a websocket would carry no limiter and need no inventory entry to
    keep the rest of this file green, so the walk refuses to skip them."""
    unseen = _walk(app)[1]
    assert unseen == [], (
        "the app serves routes the rate-limit inventory cannot see (mounts and websockets are "
        "not covered): extend the walk in this file to cover them, or serve them as plain HTTP "
        f"routes: {unseen}"
    )


def test_the_walk_reports_mounts_and_websockets_but_not_the_frameworks_own_routes() -> None:
    """The self-check for the test above: build the shapes it exists to catch."""
    sub = FastAPI()

    @sub.post("/spend")
    async def spend() -> None: ...

    async def socket_endpoint(websocket: WebSocket) -> None: ...

    router = APIRouter()
    router.add_api_websocket_route("/ws/on-a-router", socket_endpoint)

    @router.get("/fine")
    async def fine() -> None: ...

    tiny = FastAPI()
    tiny.mount("/sub", sub)
    tiny.add_api_websocket_route("/ws/spend", socket_endpoint)
    tiny.include_router(router)

    served, unseen = _walk(tiny)

    assert [s.key for s in served] == ["GET /fine"]
    assert sorted(unseen) == sorted(
        ["Mount /sub", "APIWebSocketRoute /ws/spend", "APIWebSocketRoute /ws/on-a-router"]
    )


def test_the_routes_that_carry_a_limiter_are_exactly_the_inventory() -> None:
    actual: dict[str, str] = {}
    for served in _served_routes():
        buckets = {bucket for _dep, bucket in _limiters(served)}
        assert len(buckets) <= 1, f"{served.key} carries two different limiters: {buckets}"
        if buckets:
            actual[served.key] = buckets.pop()

    assert actual == LIMITED


def test_every_other_route_is_filed_as_unlimited_with_a_reason() -> None:
    served = {s.key for s in _served_routes()}

    assert served - set(LIMITED) - set(UNLIMITED) == set(), (
        "these routes are in neither LIMITED nor UNLIMITED -- decide which, and say why: "
        f"{sorted(served - set(LIMITED) - set(UNLIMITED))}"
    )
    assert (set(LIMITED) | set(UNLIMITED)) - served == set(), (
        f"entries for routes the app no longer serves: "
        f"{sorted((set(LIMITED) | set(UNLIMITED)) - served)}"
    )
    assert set(LIMITED).isdisjoint(UNLIMITED)
    for route, reason in UNLIMITED.items():
        assert len(reason.strip()) >= 20, f"{route} is left unlimited without a real reason"


def test_every_bucket_in_the_table_is_used_and_every_used_bucket_is_in_the_table() -> None:
    assert set(LIMITED.values()) == set(RATE_LIMITS)


def test_the_limiter_runs_before_anything_else_in_the_route() -> None:
    """It is the first dependency of every route that has one, so nothing expensive (or
    anything that could fail first and skip the count) runs ahead of it. Authentication, which
    it depends on, necessarily comes before it."""
    for served in _served_routes():
        limiters = _limiters(served)
        if not limiters:
            continue
        first = served.dependant.dependencies[0]
        assert limiter_bucket(first.call) is not None, f"{served.key}: the limiter is not first"


def test_limiters_authenticate_the_way_their_route_does() -> None:
    """The limiter reads the user id from the same dependency its route authenticates with:
    the extension's scoped token on /extension/*, the ordinary one everywhere else. Otherwise
    a route would count against one identity and run as another."""
    for served in _served_routes():
        for limiter, _bucket in _limiters(served):
            auth_calls = {sub.call for sub in limiter.dependencies}
            expected = (
                require_active_extension_user_id
                if served.path.startswith("/extension/")
                else require_user_id
            )
            assert expected in auth_calls, f"{served.key}: the limiter authenticates differently"


# -- 2. the scan ---------------------------------------------------------------------------

EXPENSIVE_NAMES = frozenset(
    {
        # a model call (every handler passes `generate=llm_generate`)
        "llm_generate",
        # pipeline functions in other modules whose model call is a default argument, so a
        # handler that forgot to pass `generate=` would hide it from a name scan
        "classify_reply",
        "synthesize_dossier",
        "generate_positioning_brief",
        "find_contacts",
        "pick_product_terms",
        "find_warm_path_events",
        "generate_outreach_draft",
        "generate_practice_questions",
        "score_answer",
        "score_jobs",
        # job search: provider fan-out and the registry lane
        "search_jobs",
        "fetch_registry_lane",
        # the forge-engines client
        "call_apply",
        "call_step0",
        "call_gap_interview",
        "call_gap_answer_draft",
        "call_ingest",
        "call_personal",
        "resolve_header_chips",
        "load_coverage_context",
        "run_prepare_application",
        # the chat bot's whole business logic (channel_core), which can start a resume
        # generation; the Telegram webhook reaches it
        "handle_inbound",
        # the LaTeX compiler
        "call_compile",
        "latest_resume_pdf",
        "latest_cover_letter_pdf",
        # search and scrape providers, and the paid lookups
        "scrape_firecrawl",
        "run_research",
        "run_contact_research",
        "run_event_research",
        "search_application",
        "search_tab",
        "enrich_candidate",
        "enrich_hunter",
        "find_linkedin_exa",
        # the key validators, which call the provider (credentials_routes)
        "_VALIDATORS",
        "_TWO_SECRET_VALIDATORS",
    }
)
"""What it means for a handler to spend money or heavy compute. A name, not a type: the scan
is a tripwire for the obvious ways in, not a proof. It sees names reached in the handler's own
module and its same-module helpers, so a pipeline function in another module counts only if it
is listed here (above all the ones whose model call is a default argument) or the handler passes
`generate=llm_generate` itself. Add a name here when a new kind of costly call is introduced."""

SCAN_EXEMPT: dict[str, str] = {
    "extension_routes.py::draft_answer": (
        "has its own dedicated limiter (extension_rate_limit.claim_draft_answer_slot), which "
        "predates the generic one and is deliberately kept"
    ),
    "telegram_webhook.py::telegram_webhook": (
        "no user session to hang a route dependency on (Telegram's shared secret authenticates "
        "the call); the one expensive action, generating a resume, claims the 'prepare' bucket "
        "itself in channel_core._start_prepare, before it starts the work"
    ),
}
"""'<module>::<handler>' -> why an expensive, un-limited handler is acceptable. Every entry
must still name a real handler that is expensive and has no limiter (a stale entry fails)."""

_VERBS = {"get", "post", "put", "patch", "delete", "head", "options", "api_route", "websocket"}


def _scanned_files() -> list[Path]:
    return sorted([*_SRC.glob("*_routes.py"), _SRC / "app.py", _SRC / "telegram_webhook.py"])


def _is_route_decorator(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _VERBS
    )


def _names_in(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            names.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            names.add(sub.attr)
    return names


def _carries_a_limiter(handler: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    """A `limit(...)` call in the decorators (`dependencies=[Depends(limit("x"))]`) or in a
    parameter default (`user: str = Depends(limit("x"))`)."""
    places: list[ast.AST] = [*handler.decorator_list, *handler.args.defaults]
    places.extend(d for d in handler.args.kw_defaults if d is not None)
    for place in places:
        for sub in ast.walk(place):
            if isinstance(sub, ast.Call):
                func = sub.func
                if (isinstance(func, ast.Name) and func.id == "limit") or (
                    isinstance(func, ast.Attribute) and func.attr == "limit"
                ):
                    return True
    return False


def _reaches(
    handler: ast.AsyncFunctionDef | ast.FunctionDef,
    functions: dict[str, ast.AsyncFunctionDef | ast.FunctionDef],
) -> set[str]:
    """Every name the handler references, following calls to helper functions of the same
    module (a handler that delegates to `_resume_evidence_for` reaches what it reaches)."""
    seen: set[str] = set()
    pending = [handler]
    visited = {handler.name}
    while pending:
        current = pending.pop()
        names = _names_in(current)
        seen |= names
        for name in names:
            helper = functions.get(name)
            if helper is not None and name not in visited:
                visited.add(name)
                pending.append(helper)
    return seen


@dataclass(frozen=True)
class _Handler:
    key: str
    expensive: set[str]
    limited: bool


def _handlers(source: str, filename: str) -> list[_Handler]:
    tree = ast.parse(source)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
    }
    found = []
    for node in functions.values():
        if not any(_is_route_decorator(d) for d in node.decorator_list):
            continue
        found.append(
            _Handler(
                key=f"{filename}::{node.name}",
                expensive=_reaches(node, functions) & EXPENSIVE_NAMES,
                limited=_carries_a_limiter(node),
            )
        )
    return found


def _all_handlers() -> list[_Handler]:
    return [h for path in _scanned_files() for h in _handlers(path.read_text(), path.name)]


def test_the_scan_finds_the_handlers_it_should() -> None:
    """Guards the guard: if the decorator or limiter detection stopped matching, the checks
    below would pass on nothing."""
    handlers = {h.key: h for h in _all_handlers()}
    assert len(handlers) > 60
    prepare = handlers["applications_routes.py::prepare_application"]
    assert prepare.limited and "run_prepare_application" in prepare.expensive
    discover = handlers["discovery_routes.py::search_discover"]
    assert discover.limited and {"search_jobs", "llm_generate"} <= discover.expensive
    assert handlers["extension_routes.py::draft_answer"].expensive  # found, through the call
    assert not handlers["today_routes.py::list_my_today_items"].expensive


def test_the_scan_covers_every_module_that_serves_a_route() -> None:
    """Guards the guard: the scan reads a fixed set of files, so a router in an oddly named
    module (resume_tips_api.py, webhooks.py) would otherwise be served but never scanned."""
    scanned = {path.resolve() for path in _scanned_files()}
    serving = {
        Path(inspect.getsourcefile(inspect.unwrap(served.endpoint)) or "").resolve()
        for served in _served_routes()
    }

    assert serving <= scanned, (
        "these modules serve routes but the static scan does not read them (add them to "
        f"_scanned_files): {sorted(p.name for p in serving - scanned)}"
    )


def test_no_expensive_handler_is_without_a_limiter_unless_it_says_why() -> None:
    offenders = [
        f"{h.key} reaches {sorted(h.expensive)}"
        for h in _all_handlers()
        if h.expensive and not h.limited and h.key not in SCAN_EXEMPT
    ]
    assert offenders == [], (
        "these route handlers spend money or heavy compute and carry no rate limit: add "
        "dependencies=[Depends(limit('<bucket>'))] to the route (and the bucket to RATE_LIMITS), "
        "or exempt it in SCAN_EXEMPT with a reason"
    )


def test_every_scan_exemption_is_still_needed() -> None:
    handlers = {h.key: h for h in _all_handlers()}
    for key, reason in SCAN_EXEMPT.items():
        assert key in handlers, f"{key} is exempt but is not a route handler any more"
        assert handlers[key].expensive, f"{key} is exempt but no longer reaches anything costly"
        assert not handlers[key].limited, f"{key} is exempt but has a limiter now: drop the entry"
        assert len(reason.strip()) >= 20


def test_the_scan_follows_a_helper_and_catches_a_handler_that_forgot() -> None:
    """The self-check: a mutated route module with a handler that reaches an expensive call
    only through a helper, and one that has a limiter, one that is a plain read."""
    source = """
from fastapi import APIRouter, Depends
router = APIRouter()

async def _helper(x):
    return await llm_generate(x)

@router.post("/forgot")
async def forgot(x):
    return await _helper(x)

@router.post("/limited", dependencies=[Depends(limit("prepare"))])
async def limited(x):
    return await _helper(x)

@router.get("/by-default")
async def by_default(guard=Depends(limit("prepare"))):
    return await llm_generate(1)

@router.get("/cheap")
async def cheap(x):
    return x

@router.websocket("/ws")
async def socket(websocket):
    return await llm_generate(websocket)

@router.post("/by-default-argument")
async def by_default_argument(x):
    return await generate_positioning_brief(x)
"""
    by_name = {h.key.split("::")[1]: h for h in _handlers(source, "x_routes.py")}

    assert by_name["forgot"].expensive == {"llm_generate"} and not by_name["forgot"].limited
    assert by_name["limited"].expensive and by_name["limited"].limited
    assert by_name["by_default"].expensive and by_name["by_default"].limited
    assert not by_name["cheap"].expensive
    # a websocket handler is a route like any other
    assert by_name["socket"].expensive == {"llm_generate"} and not by_name["socket"].limited
    # a cross-module pipeline whose model call is a default argument, named in EXPENSIVE_NAMES
    assert by_name["by_default_argument"].expensive == {"generate_positioning_brief"}


def test_the_bots_resume_generation_is_only_reachable_through_the_limiter() -> None:
    """The webhook is exempt from the route scan because the chat bot claims the "prepare"
    bucket itself. That only holds while the one function that starts a generation claims it
    first, and nothing else reaches the work: pinned here on `channel_core`'s own source."""
    source = (_SRC / "channel_core.py").read_text()
    functions = {
        node.name: node
        for node in ast.parse(source).body
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
    }
    work = functions["_prepare_and_deliver"]
    start = functions["_start_prepare"]

    assert {"run_prepare_application", "latest_resume_pdf"} <= _names_in(work)
    assert {"rate_limit_error_or_none", "_prepare_and_deliver"} <= _names_in(start)
    # the claim comes before the work is handed over, in source order
    claim_line = min(
        n.lineno
        for n in ast.walk(start)
        if isinstance(n, ast.Name) and n.id == "rate_limit_error_or_none"
    )
    handover_line = min(
        n.lineno
        for n in ast.walk(start)
        if isinstance(n, ast.Name) and n.id == "_prepare_and_deliver"
    )
    assert claim_line < handover_line
    # no other function reaches the work, or the engine and compiler it calls
    others = {name: fn for name, fn in functions.items() if name not in {"_prepare_and_deliver"}}
    for name, fn in others.items():
        reached = _names_in(fn)
        assert not {"run_prepare_application", "latest_resume_pdf"} & reached, name
        if name != "_start_prepare":
            assert "_prepare_and_deliver" not in reached, name
