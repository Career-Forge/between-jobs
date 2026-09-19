"""Supabase Auth JWT verification.

Verifies the `Authorization: Bearer <jwt>` header against this project's
JWKS endpoint. Confirmed live (before writing this) that this project
issues asymmetric ES256-signed tokens via
`{SUPABASE_URL}/auth/v1/.well-known/jwks.json` -- not the legacy shared-
secret system -- so verification is local per Supabase's own recommended
path: no Auth-server round trip once the signing key is cached.

Follows Supabase's documented validation guidelines (docs/guides/auth/
jwt-fields): PyJWT checks `exp` automatically; `iss` and `aud` are
checked explicitly here. `aud` must be `"authenticated"` -- a real user
session token, not an anon-key-derived token.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import jwt
from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from jwt import PyJWK, PyJWKClient
from jwt.exceptions import PyJWKClientError

from .errors import ApiError

# How long an unknown `kid` has to wait before it may force another JWKS
# refetch. Legitimate key rotation is rare and the old key stays published
# through the rollover, so a minute is generous; what it buys is that an
# unauthenticated caller can no longer turn every forged token into an
# outbound request to Supabase.
_MIN_JWKS_REFRESH_INTERVAL_SECONDS = 60.0


class ThrottledJWKClient(PyJWKClient):
    """`PyJWKClient` that refuses to hammer the JWKS endpoint for unknown `kid`s.

    Stock PyJWKClient re-downloads the whole key set every time it sees a
    `kid` it doesn't know, *before* any signature check. The `kid` is an
    attacker-chosen header field on an unauthenticated request, so a loop
    of forged tokens becomes a loop of blocking fetches against Supabase --
    and every route in this API, the browser extension's included, sits
    behind this one dependency. Here an unknown `kid` may force a refresh
    at most once per `min_refresh_interval`; anything inside that window is
    rejected straight from the cached key set.
    """

    def __init__(
        self,
        uri: str,
        *,
        min_refresh_interval: float = _MIN_JWKS_REFRESH_INTERVAL_SECONDS,
        **kwargs: Any,
    ) -> None:
        self._min_refresh_interval = min_refresh_interval
        self._last_forced_refresh = float("-inf")
        self._refresh_lock = threading.Lock()
        super().__init__(uri, **kwargs)

    def get_signing_key(self, kid: str) -> PyJWK:
        signing_key = self.match_kid(self.get_signing_keys(), kid)
        if signing_key is not None:
            return signing_key

        with self._refresh_lock:
            # Another worker thread may have refreshed while this one
            # waited on the lock -- look again before spending a fetch.
            signing_key = self.match_kid(self.get_signing_keys(), kid)
            if signing_key is not None:
                return signing_key
            now = time.monotonic()
            if now - self._last_forced_refresh < self._min_refresh_interval:
                raise PyJWKClientError(f'Unable to find a signing key that matches: "{kid}"')
            # Stamped before the fetch so a failing endpoint is throttled
            # too, not retried by every request that arrives during an outage.
            self._last_forced_refresh = now
            signing_key = self.match_kid(self.get_signing_keys(refresh=True), kid)

        if signing_key is None:
            raise PyJWKClientError(f'Unable to find a signing key that matches: "{kid}"')
        return signing_key


def create_jwks_client(supabase_url: str) -> PyJWKClient:
    return ThrottledJWKClient(f"{supabase_url}/auth/v1/.well-known/jwks.json", cache_keys=True)


def verify_access_token(token: str, jwks_client: PyJWKClient, supabase_url: str) -> str:
    """Verify a Supabase Auth access token; return the verified user id (`sub`).

    Raises `jwt.PyJWTError` subclasses on any failure -- expired, bad
    signature, wrong issuer/audience -- callers map those to a 401, they
    aren't swallowed here.
    """
    signing_key = jwks_client.get_signing_key_from_jwt(token)
    payload = jwt.decode(
        token,
        signing_key.key,
        algorithms=["ES256"],
        audience="authenticated",
        issuer=f"{supabase_url}/auth/v1",
    )
    return str(payload["sub"])


async def require_user_id(request: Request) -> str:
    """FastAPI dependency: verifies the bearer token, returns the verified
    user id. 401s on anything wrong with the token itself -- missing,
    malformed, expired, bad signature, wrong issuer/audience."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise ApiError("AUTH_REQUIRED", "missing or malformed Authorization header")
    token = auth_header[len("Bearer ") :].strip()
    try:
        # verify_access_token is synchronous and can block on a JWKS fetch
        # (cold cache, expired key set, a key rotation). Running it inline
        # in this coroutine would stall the whole event loop for every
        # other request for the duration of that network call.
        return await run_in_threadpool(
            verify_access_token,
            token,
            request.app.state.jwks_client,
            request.app.state.supabase_url,
        )
    except jwt.PyJWTError as e:
        # PyJWT's own message names internals (which kid it looked for,
        # which claim failed) that an unauthenticated caller has no use for.
        raise ApiError("AUTH_REQUIRED", "invalid token") from e
