"""HTTP surface for Hiring Signals P3 -- the per-application button.

Four routes, all behind the same two gates:

- `DISABLE_HIRING_SIGNALS` (any non-empty value) turns the whole feature off,
  checked on every request: each route answers 404 `FEATURE_DISABLED`, before
  authentication, before the request body is read (a body that is not even
  JSON still gets the 404, not a 422) and before anything else runs, so a
  switched-off feature does not confirm to a caller that it exists. The flag
  follows the `DISABLE_*` naming convention `app.py` already uses. It is
  enforced by the router's own route class (`_FlagCheckedRoute`) rather than a
  dependency, because FastAPI reads and validates a body BEFORE it resolves
  dependencies.
- The verified user id, like every other route: an application, a save or a
  cache row is only ever reached through the caller's own id.

An id in a path that is not a canonical UUID (36 characters, ASCII hex and
hyphens -- not `uuid.UUID`'s wider set of spellings, such as full-width digits
or a `urn:uuid:` prefix, which Postgres rejects) is a 404, the same answer as
an id that does not exist -- it would otherwise reach Postgres as an invalid
uuid and come back as a 500.

Errors are the platform's own envelope and `ErrorCode` set, so a client needs
no special case: 401 `AUTH_REQUIRED`; 404 `NOT_FOUND` (an unknown, foreign or
non-UUID application or save id -- one answer for all three) and 404
`FEATURE_DISABLED`; 409 `SETUP_REQUIRED` (no search key saved; carries
`capability`, `missing` and `settings_path` like every other resolver error);
422 `INVALID_INPUT` (a bad window, a non-digit activity id, an extra field --
the app-wide validation handler; this is the API contract's `INVALID_REQUEST`,
which `ErrorCode` does not have); 503 `PROVIDER_UNAVAILABLE`, retryable.

Nothing here fires anything: the search reads a provider index the user has
their own key for, a save writes a pointer row, and neither posts, messages
or contacts anyone.
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
from .errors import ApiError
from .hiring_signal_saves_store import (
    HiringSignalSaveNotFound,
    create_save,
    delete_save,
    list_saves,
)
from .hiring_signal_service import search_application
from .hiring_signals import Freshness
from .models import SaveHiringSignalRequest, SearchHiringSignalsRequest


def require_hiring_signals_enabled() -> None:
    if os.environ.get("DISABLE_HIRING_SIGNALS"):
        raise ApiError("FEATURE_DISABLED", "Hiring signals are turned off on this server.")


class _FlagCheckedRoute(APIRoute):
    """Every route of this router checks the feature flag first, before FastAPI
    reads the body (see the module docstring)."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def flag_checked(request: Request) -> Response:
            require_hiring_signals_enabled()
            return await handler(request)

        return flag_checked


router = APIRouter(route_class=_FlagCheckedRoute)

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


@router.post("/applications/{application_id}/hiring-signals/search")
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
