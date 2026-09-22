"""Extension-scoped sign-out liveness check (E6 continuation).

The ORIGINAL spec's own "server-side revocation" requirement was never
built for this platform's JWTs -- a Supabase Auth access token is a
self-contained, signature-verified bearer credential with no server-side
session record, so `require_user_id` (auth.py) has no way to know a token
was supposed to stop working before its own `exp`. That's fine for the
web app (it holds a live Supabase session it can actually sign out of),
but the browser extension caches a token in its own storage across
browser restarts with no equivalent "the session object itself is gone"
signal -- so a stolen or leftover extension token would otherwise stay
valid for its full lifetime after the user believed they'd signed out.

Pranav's explicit, scoped go-ahead (CLAUDE.local.md): build a liveness
check for extension routes ONLY, not a rewrite of `require_user_id` or
the app's general auth path. So this module, not auth.py, owns:

  - `extension_sign_outs` -- one row per user, `signed_out_at` (its own
    migration). The simplest correct shape for "reject anything issued
    before the last sign-out": no per-token blocklist, no token ids
    parsed or stored, just a single watermark per user.
  - `require_active_extension_user_id` -- the FastAPI dependency
    `extension_routes.py`'s routes use INSTEAD OF `require_user_id`.
    Nothing else in the app imports this. It performs the exact same
    signature/`exp`/`iss`/`aud` verification `require_user_id` does (via
    `auth.verify_access_token_and_iat`, `auth.py`'s own sibling of
    `verify_access_token`), then additionally rejects a token whose own
    `iat` predates that user's last recorded extension sign-out. A fresh
    sign-in mints a new token with a later `iat`, which passes -- and a
    user who has never signed out of the extension (no row at all) is
    never affected by this check.

Because this is a wholly separate dependency function -- not a change to
`require_user_id` itself -- every other route in the app (the web
frontend's included) is structurally unable to be affected by an
extension sign-out: they never call this function, so `signed_out_at` is
never even read on their behalf. Proven, not just asserted, by
`tests/test_extension_auth.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import jwt
from fastapi import Depends, Request
from fastapi.concurrency import run_in_threadpool

from supabase import AsyncClient

from .app_state import get_supabase
from .auth import verify_access_token_and_iat
from .errors import ApiError

_TABLE = "extension_sign_outs"


async def get_extension_signed_out_at(supabase: AsyncClient, user_id: str) -> int | None:
    """This user's last extension sign-out, as Unix epoch seconds --
    the same units as a JWT's own `iat` claim, so the dependency below
    can compare them directly with no timezone or format juggling.
    `None` means the user has never signed out of the extension (or
    never used it), which must never be treated as "sign out immediately
    always wins"."""
    result = await supabase.table(_TABLE).select("signed_out_at").eq("user_id", user_id).execute()
    if not result.data:
        return None
    raw = cast(dict[str, Any], result.data[0])["signed_out_at"]
    if raw is None:
        return None
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        # Postgrest always returns a `timestamptz` column with an offset --
        # this only guards a hand-inserted or fixture-supplied naive
        # string in tests from silently comparing against the local
        # machine's own timezone instead of UTC.
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp())


async def record_extension_sign_out(supabase: AsyncClient, user_id: str) -> None:
    """Upserts "signed out now" for this user. Every extension token
    issued before this moment is rejected by
    `require_active_extension_user_id` from this point on -- including,
    deliberately, the very token that just authenticated this call: its
    own `iat` is always earlier than the `now()` this write stamps, so
    calling sign-out invalidates the session that called it too, exactly
    as a sign-out should."""
    await (
        supabase.table(_TABLE)
        .upsert(
            {"user_id": user_id, "signed_out_at": datetime.now(UTC).isoformat()},
            on_conflict="user_id",
        )
        .execute()
    )


async def require_active_extension_user_id(
    request: Request,
    supabase: AsyncClient = Depends(get_supabase),
) -> str:
    """`extension_routes.py`'s own auth dependency -- see the module
    docstring. Structurally independent of `require_user_id`: it reads
    and verifies the bearer token itself (same rules, `auth.
    verify_access_token_and_iat`) rather than depending on
    `require_user_id` as a sub-dependency, so overriding one in a test
    never silently affects the other -- each route's own auth story
    stays exactly what its own `Depends(...)` says it is."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise ApiError("AUTH_REQUIRED", "missing or malformed Authorization header")
    token = auth_header[len("Bearer ") :].strip()
    try:
        user_id, issued_at = await run_in_threadpool(
            verify_access_token_and_iat,
            token,
            request.app.state.jwks_client,
            request.app.state.supabase_url,
        )
    except jwt.PyJWTError as e:
        raise ApiError("AUTH_REQUIRED", "invalid token") from e

    signed_out_at = await get_extension_signed_out_at(supabase, user_id)
    # Strict `>`, not `>=`: `signed_out_at` here has been truncated to whole
    # seconds (get_extension_signed_out_at's own int(...timestamp())) to
    # match `iat`'s own NumericDate (whole-second) precision -- so a
    # sign-out and a genuinely fresh sign-in that land in the same
    # wall-clock second produce EQUAL values, and an inclusive `>=` would
    # reject the fresh token even though it was minted at or after the
    # sign-out. `>` treats that ambiguous same-second case as fresh (the
    # module's own docstring's stated contract), matching a real bug
    # reproduced directly: recording a sign-out and presenting a
    # correctly-signed token with the identical `iat` second used to be
    # rejected. A token whose `iat` second is strictly BEFORE the sign-out
    # is unaffected either way -- it's still rejected.
    if signed_out_at is not None and signed_out_at > issued_at:
        raise ApiError("AUTH_REQUIRED", "signed out of the extension -- sign in again")
    return user_id
