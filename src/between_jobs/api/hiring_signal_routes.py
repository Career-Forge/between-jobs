"""HTTP surface for Hiring Signals: the per-application button (P3) and the
standalone "Hiring signals" tab (P4).

Per-application (P3):

- `POST /applications/{id}/hiring-signals/search`
- `POST` / `GET /applications/{id}/hiring-signals/saves`

Standalone tab (P4), no application and no company:

- `POST /hiring-signals/search` -- a typed role and optionally a metro;
- `POST` / `GET /hiring-signals/searches`, `DELETE /hiring-signals/searches/{id}`
  -- the user's saved searches (their typed text, nothing that runs);
- `POST` / `GET /hiring-signals/saves` -- standalone saves, the same pointer rows
  as the per-application saves but with no application (the two lists never
  contain each other's rows);
- `GET /hiring-signals/status` -- whether the feature is on, so the web app can
  hide a dead link instead of showing one.

Both surfaces share `DELETE /hiring-signals/saves/{save_id}`, and all of them sit
behind the same two gates:

- `DISABLE_HIRING_SIGNALS` (any non-empty value) turns the whole feature off,
  checked on every request: each route answers 404 `FEATURE_DISABLED`, before
  authentication, before the request body is read (a body that is not even
  JSON still gets the 404, not a 422) and before anything else runs, so a
  switched-off feature does not confirm to a caller that it exists. The flag
  follows the `DISABLE_*` naming convention `app.py` already uses. It is
  enforced by the router's own route class (`_FlagCheckedRoute`) rather than a
  dependency, because FastAPI reads and validates a body BEFORE it resolves
  dependencies.
- `HIRING_SIGNALS_ALLOWED_USER_IDS` (optional, a comma-separated list of user ids)
  limits the feature to the people named. Unset or blank means everyone, which is
  how the feature behaves when nobody has chosen a list. When it is set, a caller
  who is not verifiably on the list gets exactly the answer the switch above gives
  -- 404 `FEATURE_DISABLED`, before the body is read -- so for them the feature does
  not exist. That includes a caller with no valid token: they are not on the list
  either, and a 401 would tell them the route is there. The same route class
  enforces it, for the same reason (a dependency runs after the body is read), and
  it checks the token with the same verification every route uses. The list is read
  on every request, like the switch, and checked at start-up so a malformed one
  stops the boot (`hiring_signals_allowlist`).
- The verified user id, like every other route: an application, a save, a
  saved search or a cache row is only ever reached through the caller's own id.

The one exception to both gates is `GET /hiring-signals/status`, which REPORTS the
answer for the caller (`{"enabled": false}` when the switch is off or the caller is
not on the list) and so cannot be blocked by it. It is on its own router without the
flag-checked route class, and it still requires authentication.

An id in a path that is not a canonical UUID (36 characters, ASCII hex and
hyphens -- not `uuid.UUID`'s wider set of spellings, such as full-width digits
or a `urn:uuid:` prefix, which Postgres rejects) is a 404, the same answer as
an id that does not exist -- it would otherwise reach Postgres as an invalid
uuid and come back as a 500.

Errors are the platform's own envelope and `ErrorCode` set, so a client needs
no special case: 401 `AUTH_REQUIRED`; 404 `NOT_FOUND` (an unknown, foreign or
non-UUID application, save or saved-search id -- one answer for all of them) and
404 `FEATURE_DISABLED`; 409 `SETUP_REQUIRED` (no search key saved; carries
`capability`, `missing` and `settings_path` like every other resolver error);
409 `CONFLICT` (saving a search past the per-user cap -- the closest existing
code: the request is well formed, the user's own state forbids it; the message
says so and the UI shows it -- or a concurrent save took the last slot, `retryable`
and worded to try again); 422 `INVALID_INPUT` (a bad window, a non-digit
activity id, an extra field, a role or place that cannot be searched -- the
app-wide validation handler; this is the API contract's `INVALID_REQUEST`, which
`ErrorCode` does not have); 503 `PROVIDER_UNAVAILABLE`, retryable.

Nothing here fires anything: a search reads a provider index the user has their
own key for, a save writes a pointer row, a saved search stores two typed
strings, and none of it posts, messages, contacts anyone or notifies anyone. It
is pull-only: it runs when the user clicks.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Coroutine
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.routing import APIRoute

from supabase import AsyncClient

from .app_state import get_hiring_http_client, get_supabase
from .applications_store import ApplicationNotFound, get_application
from .auth import require_user_id
from .env import optional_uuid_set
from .errors import ApiError
from .hiring_signal_saves_store import (
    HiringSignalSaveNotFound,
    create_save,
    delete_save,
    list_saves,
)
from .hiring_signal_searches_store import (
    MAX_SAVED_SEARCHES,
    HiringSignalSearchLimitReached,
    HiringSignalSearchNotFound,
    HiringSignalSearchSaveRaced,
    create_search,
    delete_search,
    list_searches,
)
from .hiring_signal_service import search_application, search_tab
from .hiring_signal_tab import InvalidSearchInput
from .hiring_signals import Freshness, Locale
from .models import (
    CreateHiringSearchRequest,
    SaveHiringSignalRequest,
    SearchHiringSignalsRequest,
    SearchHiringTabRequest,
)
from .rate_limits import limit

_ALLOWED_USER_IDS_ENV = "HIRING_SIGNALS_ALLOWED_USER_IDS"


def hiring_signals_allowlist() -> frozenset[str] | None:
    """The user ids (lower-cased) allowed to use Hiring signals, or None when no list is set
    and everyone is. A malformed list stops the API from starting; `app.lifespan` reads it
    once at boot for that reason."""
    return optional_uuid_set(_ALLOWED_USER_IDS_ENV)


def _is_listed(allowed: frozenset[str] | None, user_id: str) -> bool:
    """Whether an id is on the allowlist that was read: everyone is when there is no list, and
    otherwise a user is by the lower-case form of their id, since the list is kept lower-cased
    and a token's `sub` may be written in capitals. The one membership test: the status answer
    and the route gate both decide with it, so they cannot disagree about who is on the list."""
    return allowed is None or user_id.lower() in allowed


def user_may_use_hiring_signals(user_id: str) -> bool:
    """Whether the switch and the allowlist let this (already verified) user have the
    feature, as the status route reports it. The route gate (`require_hiring_signals_enabled`
    and `require_listed_caller`) decides the same way -- the same switch, and membership by
    the same `_is_listed` -- and a test runs both on one caller, so the web app never shows
    an entry that every route then refuses."""
    if os.environ.get("DISABLE_HIRING_SIGNALS"):
        return False
    return _is_listed(hiring_signals_allowlist(), user_id)


def _feature_disabled() -> ApiError:
    """The one answer for "this feature does not exist for you", whichever gate says it."""
    return ApiError("FEATURE_DISABLED", "Hiring signals are turned off on this server.")


def require_hiring_signals_enabled() -> None:
    if os.environ.get("DISABLE_HIRING_SIGNALS"):
        raise _feature_disabled()


async def require_listed_caller(request: Request) -> None:
    """With an allowlist set, refuses (as `_feature_disabled`) anyone who is not verifiably on
    it. The token is verified by `require_user_id`, the same check every route runs; a
    caller it cannot verify is refused the same way, not with its 401."""
    allowed = hiring_signals_allowlist()
    if allowed is None:
        return
    try:
        user_id = await require_user_id(request)
    except ApiError:
        raise _feature_disabled() from None
    if not _is_listed(allowed, user_id):
        raise _feature_disabled()


class _FlagCheckedRoute(APIRoute):
    """Every route of this router checks the feature flag and the allowlist first, before
    FastAPI reads the body (see the module docstring)."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def flag_checked(request: Request) -> Response:
            require_hiring_signals_enabled()
            await require_listed_caller(request)
            return await handler(request)

        return flag_checked


router = APIRouter(route_class=_FlagCheckedRoute)

status_router = APIRouter()
"""Not flag-checked: `GET /hiring-signals/status` reports the flag."""

_UUID_RX = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _require_uuid(value: str, *, what: str) -> None:
    if _UUID_RX.fullmatch(value) is None:
        raise ApiError("NOT_FOUND", f"no {what} found for id {value!r}")


async def _require_application(supabase: AsyncClient, user_id: str, application_id: str) -> None:
    _require_uuid(application_id, what="application")
    try:
        await get_application(supabase, user_id, application_id)
    except ApplicationNotFound as e:
        raise ApiError("NOT_FOUND", f"no application found for id {application_id!r}") from e


@router.post(
    "/applications/{application_id}/hiring-signals/search",
    dependencies=[Depends(limit("hiring_signal_search"))],
)
async def search_hiring_signals(
    application_id: str,
    body: SearchHiringSignalsRequest | None = None,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_hiring_http_client),
) -> dict[str, Any]:
    """Searches a provider index the user has a key for, for recent hiring
    posts about this application's company. Spends the USER'S OWN provider
    credits (absorbed by the shared cache on a repeat), so it is a POST and
    runs only on a deliberate click."""
    _require_uuid(application_id, what="application")
    freshness = Freshness((body or SearchHiringSignalsRequest()).freshness)
    return await search_application(supabase, http, user_id, application_id, freshness=freshness)


@router.post("/applications/{application_id}/hiring-signals/saves", status_code=201)
async def save_hiring_signal(
    application_id: str,
    body: SaveHiringSignalRequest,
    response: Response,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    """201 for a new save, 200 with the existing row for one that was already
    saved -- the same request twice is the same result."""
    await _require_application(supabase, user_id, application_id)
    saved, created = await create_save(
        supabase,
        user_id,
        application_id,
        activity_id=body.activity_id,
        query_label=body.query_label,
    )
    if not created:
        response.status_code = 200
    return saved


@router.get("/applications/{application_id}/hiring-signals/saves")
async def list_hiring_signal_saves(
    application_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    await _require_application(supabase, user_id, application_id)
    return {"saves": await list_saves(supabase, user_id, application_id)}


@router.delete("/hiring-signals/saves/{save_id}", status_code=204)
async def delete_hiring_signal_save(
    save_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> None:
    _require_uuid(save_id, what="save")
    try:
        await delete_save(supabase, user_id, save_id)
    except HiringSignalSaveNotFound as e:
        raise ApiError("NOT_FOUND", f"no save found for id {save_id!r}") from e


# ── the standalone tab (P4) ──────────────────────────────────────────────


@status_router.get("/hiring-signals/status")
async def hiring_signals_status(user_id: str = Depends(require_user_id)) -> dict[str, bool]:
    """Whether Hiring signals is on for the caller: `DISABLE_HIRING_SIGNALS` unset or empty
    and, when `HIRING_SIGNALS_ALLOWED_USER_IDS` is set, the caller is on it. Read on every
    request, like the gates themselves. The answer is about the caller's own id and tells
    nothing about anyone else."""
    return {"enabled": user_may_use_hiring_signals(user_id)}


@router.post("/hiring-signals/search", dependencies=[Depends(limit("hiring_signal_search"))])
async def search_hiring_signals_tab(
    body: SearchHiringTabRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_hiring_http_client),
) -> dict[str, Any]:
    """Searches a provider index the user has a key for, for recent hiring posts
    for a typed role (and optionally a metro), with no company. Spends the
    USER'S OWN provider credits (absorbed by the shared cache on a repeat), so it
    is a POST and runs only on a deliberate click."""
    return await search_tab(
        supabase,
        http,
        user_id,
        query=body.query,
        location=body.location,
        freshness=Freshness(body.freshness),
        locale=Locale(body.locale) if body.locale is not None else None,
    )


@router.post("/hiring-signals/searches", status_code=201)
async def save_hiring_search(
    body: CreateHiringSearchRequest,
    response: Response,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    """201 for a new saved search, 200 with the existing row for an equivalent
    one (same words ignoring case) -- the same request twice is the same result.
    Past the per-user cap it is a 409 `CONFLICT` that says to delete one; when a
    save of the same user's took the last slot a moment earlier it is a 409
    `CONFLICT` too, worded as "try again" and marked retryable."""
    try:
        saved, created = await create_search(
            supabase, user_id, query=body.query, location=body.location
        )
    except InvalidSearchInput as e:
        raise ApiError("INVALID_INPUT", str(e)) from e
    except HiringSignalSearchSaveRaced as e:  # before its base class: the message differs
        raise ApiError(
            "CONFLICT",
            "Another save was in progress at the same moment. Try saving this search again.",
            retryable=True,
        ) from e
    except HiringSignalSearchLimitReached as e:
        raise ApiError(
            "CONFLICT",
            f"You can keep at most {MAX_SAVED_SEARCHES} saved searches. "
            "Delete one to save another.",
        ) from e
    if not created:
        response.status_code = 200
    return saved


@router.get("/hiring-signals/searches")
async def list_hiring_searches(
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    return {"searches": await list_searches(supabase, user_id)}


@router.delete("/hiring-signals/searches/{search_id}", status_code=204)
async def delete_hiring_search(
    search_id: str,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> None:
    _require_uuid(search_id, what="saved search")
    try:
        await delete_search(supabase, user_id, search_id)
    except HiringSignalSearchNotFound as e:
        raise ApiError("NOT_FOUND", f"no saved search found for id {search_id!r}") from e


@router.post("/hiring-signals/saves", status_code=201)
async def save_standalone_hiring_signal(
    body: SaveHiringSignalRequest,
    response: Response,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    """A save from the tab: the same pointer row as a per-application save, with
    no application. 201 for a new save, 200 with the existing row for one that
    was already saved."""
    saved, created = await create_save(
        supabase, user_id, None, activity_id=body.activity_id, query_label=body.query_label
    )
    if not created:
        response.status_code = 200
    return saved


@router.get("/hiring-signals/saves")
async def list_standalone_hiring_signal_saves(
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    """Only the saves with no application; the per-application list never
    contains these and this never contains those."""
    return {"saves": await list_saves(supabase, user_id, None)}
