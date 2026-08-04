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

import jwt
from fastapi import HTTPException, Request
from jwt import PyJWKClient


def create_jwks_client(supabase_url: str) -> PyJWKClient:
    return PyJWKClient(f"{supabase_url}/auth/v1/.well-known/jwks.json", cache_keys=True)


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
        raise HTTPException(status_code=401, detail="missing or malformed Authorization header")
    token = auth_header[len("Bearer ") :].strip()
    try:
        return verify_access_token(
            token, request.app.state.jwks_client, request.app.state.supabase_url
        )
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"invalid token: {e}") from e
