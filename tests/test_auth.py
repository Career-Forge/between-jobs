"""Tests for Supabase Auth JWT verification (auth.py).

Uses a real, locally-generated ES256 keypair and real PyJWT encode/decode
-- not mocked away, since signature/issuer/audience/expiry verification is
exactly the logic that must be proven correct, not assumed. The JWKS
*fetch* itself (PyJWKClient's own HTTP behavior) isn't re-tested here --
that's the library's job -- only how this module uses what it returns.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
from fastapi.testclient import TestClient
from jwt.algorithms import ECAlgorithm
from jwt.exceptions import PyJWKClientError

from between_jobs.api.app import app
from between_jobs.api.auth import (
    ThrottledJWKClient,
    create_jwks_client,
    require_user_id,
    verify_access_token,
    verify_access_token_and_iat,
)
from between_jobs.api.errors import ApiError

_ISSUER_URL = "https://example.supabase.co"
_ISSUER = f"{_ISSUER_URL}/auth/v1"
_USER_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def private_key() -> EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _sign(private_key: EllipticCurvePrivateKey, claims: dict[str, Any]) -> str:
    return jwt.encode(claims, private_key, algorithm="ES256")


def _fake_jwks_client(public_key: Any) -> Any:
    return SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=public_key))


def _base_claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": _ISSUER,
        "aud": "authenticated",
        "sub": _USER_ID,
        "role": "authenticated",
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    return claims


def test_valid_token_returns_sub(private_key: EllipticCurvePrivateKey) -> None:
    token = _sign(private_key, _base_claims())
    user_id = verify_access_token(token, _fake_jwks_client(private_key.public_key()), _ISSUER_URL)
    assert user_id == _USER_ID


def test_wrong_issuer_rejected(private_key: EllipticCurvePrivateKey) -> None:
    token = _sign(private_key, _base_claims(iss="https://attacker.example.com/auth/v1"))
    with pytest.raises(jwt.PyJWTError):
        verify_access_token(token, _fake_jwks_client(private_key.public_key()), _ISSUER_URL)


def test_wrong_audience_rejected(private_key: EllipticCurvePrivateKey) -> None:
    # "anon" is a real Supabase audience value -- for a key-derived token,
    # not a signed-in user. Must not be accepted where a user is required.
    token = _sign(private_key, _base_claims(aud="anon"))
    with pytest.raises(jwt.PyJWTError):
        verify_access_token(token, _fake_jwks_client(private_key.public_key()), _ISSUER_URL)


def test_expired_token_rejected(private_key: EllipticCurvePrivateKey) -> None:
    now = int(time.time())
    token = _sign(private_key, _base_claims(iat=now - 7200, exp=now - 3600))
    with pytest.raises(jwt.ExpiredSignatureError):
        verify_access_token(token, _fake_jwks_client(private_key.public_key()), _ISSUER_URL)


def test_signature_from_wrong_key_rejected(private_key: EllipticCurvePrivateKey) -> None:
    attacker_key = ec.generate_private_key(ec.SECP256R1())
    token = _sign(attacker_key, _base_claims())
    # Verifying against the REAL public key, but the token was signed by a
    # different private key -- the actual impersonation attack this whole
    # module exists to stop.
    with pytest.raises(jwt.InvalidSignatureError):
        verify_access_token(token, _fake_jwks_client(private_key.public_key()), _ISSUER_URL)


def test_verify_access_token_and_iat_returns_both(private_key: EllipticCurvePrivateKey) -> None:
    claims = _base_claims()
    token = _sign(private_key, claims)
    user_id, issued_at = verify_access_token_and_iat(
        token, _fake_jwks_client(private_key.public_key()), _ISSUER_URL
    )
    assert user_id == _USER_ID
    assert issued_at == claims["iat"]


def test_verify_access_token_and_iat_fails_closed_on_a_token_with_no_iat_claim(
    private_key: EllipticCurvePrivateKey,
) -> None:
    """Regression: a validly-signed token missing `iat` entirely used to
    raise a bare `KeyError` from `payload["iat"]` -- not a `jwt.
    PyJWTError`, so it would propagate past every caller's own `except
    jwt.PyJWTError` (extension_auth.require_active_extension_user_id
    included) as an unhandled 500 instead of a clean 401. `jwt.encode`
    won't omit `iat` given a dict that has one, so the claims are built by
    hand rather than via `_base_claims()`, to actually produce a token
    with no `iat` claim in its payload at all."""
    now = int(time.time())
    claims = {
        "iss": _ISSUER,
        "aud": "authenticated",
        "sub": _USER_ID,
        "role": "authenticated",
        "exp": now + 3600,
    }
    assert "iat" not in claims
    token = _sign(private_key, claims)

    with pytest.raises(jwt.PyJWTError):
        verify_access_token_and_iat(token, _fake_jwks_client(private_key.public_key()), _ISSUER_URL)


@pytest.fixture
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", _ISSUER_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")


@pytest.fixture
def client(_stub_env: None, private_key: EllipticCurvePrivateKey) -> Iterator[TestClient]:
    # End-to-end proof that header parsing -> JWKS lookup -> signature/
    # claim verification -> the endpoint's use of the resulting user_id
    # all actually fit together -- not just each piece in isolation.
    # app.state.jwks_client is swapped for a fake right after lifespan
    # startup (which built a real one pointed at a fake, unreachable URL)
    # since PyJWKClient's own HTTP fetch has no place in this test.
    with TestClient(app) as test_client:
        app.state.jwks_client = _fake_jwks_client(private_key.public_key())
        yield test_client


def test_missing_auth_header_rejected(client: TestClient) -> None:
    response = client.post("/sessions", json={"context": {}})
    assert response.status_code == 401


def test_malformed_auth_header_rejected(client: TestClient) -> None:
    response = client.post(
        "/sessions", json={"context": {}}, headers={"Authorization": "NotBearer abc"}
    )
    assert response.status_code == 401


def test_expired_token_rejected_end_to_end(
    client: TestClient, private_key: EllipticCurvePrivateKey
) -> None:
    now = int(time.time())
    token = _sign(private_key, _base_claims(iat=now - 7200, exp=now - 3600))
    response = client.post(
        "/sessions", json={"context": {}}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401


# --- E6 hardening: unauthenticated forged-`kid` tokens ----------------------
#
# The `kid` header is chosen by the caller and read before any signature
# check. Stock PyJWKClient answers an unknown `kid` by re-downloading the
# JWK set, synchronously -- so a loop of forged tokens both hammered
# Supabase's JWKS endpoint and (because require_user_id is a coroutine that
# called it inline) froze the event loop for every other request.


def _jwk_set(private_key: EllipticCurvePrivateKey, *kids: str) -> dict[str, Any]:
    jwk = json.loads(ECAlgorithm.to_jwk(private_key.public_key()))
    return {"keys": [{**jwk, "kid": kid, "alg": "ES256", "use": "sig"} for kid in kids]}


def _token_with_kid(private_key: EllipticCurvePrivateKey, kid: str) -> str:
    return jwt.encode(_base_claims(), private_key, algorithm="ES256", headers={"kid": kid})


class _CountingJwksClient(ThrottledJWKClient):
    """Serves an in-memory JWK set through the client's real cache path and
    counts how many times the network fetch would have run."""

    def __init__(self, served: Any, **kwargs: Any) -> None:
        super().__init__("https://jwks.invalid/.well-known/jwks.json", **kwargs)
        self.served = served
        self.fetches = 0

    def fetch_data(self) -> Any:
        self.fetches += 1
        if self.jwk_set_cache is not None:
            self.jwk_set_cache.put(self.served)
        return self.served


def test_forged_kid_tokens_cannot_force_a_jwks_refetch_per_request(
    private_key: EllipticCurvePrivateKey,
) -> None:
    client = _CountingJwksClient(_jwk_set(private_key, "real-key"), cache_keys=True)
    client.get_signing_key_from_jwt(_token_with_kid(private_key, "real-key"))
    assert client.fetches == 1  # cold cache

    for i in range(25):
        with pytest.raises(PyJWKClientError):
            client.get_signing_key_from_jwt(_token_with_kid(private_key, f"attacker-{i}"))

    # One forced refresh for the first unknown kid, then none: the other 24
    # were rejected from the cached key set with no network call at all.
    assert client.fetches == 2


def test_a_genuinely_rotated_key_still_resolves_after_a_refresh(
    private_key: EllipticCurvePrivateKey,
) -> None:
    client = _CountingJwksClient(_jwk_set(private_key, "old-key"), cache_keys=True)
    client.get_signing_key_from_jwt(_token_with_kid(private_key, "old-key"))

    client.served = _jwk_set(private_key, "old-key", "rotated-key")
    key = client.get_signing_key_from_jwt(_token_with_kid(private_key, "rotated-key"))

    assert key.key_id == "rotated-key"
    assert client.fetches == 2


def test_unknown_kid_is_retried_once_the_refresh_window_has_passed(
    private_key: EllipticCurvePrivateKey,
) -> None:
    client = _CountingJwksClient(
        _jwk_set(private_key, "real-key"), cache_keys=True, min_refresh_interval=0.05
    )
    client.get_signing_key_from_jwt(_token_with_kid(private_key, "real-key"))
    with pytest.raises(PyJWKClientError):
        client.get_signing_key_from_jwt(_token_with_kid(private_key, "attacker-a"))
    time.sleep(0.06)
    with pytest.raises(PyJWKClientError):
        client.get_signing_key_from_jwt(_token_with_kid(private_key, "attacker-b"))
    assert client.fetches == 3


def test_create_jwks_client_returns_the_throttled_client() -> None:
    assert isinstance(create_jwks_client("https://example.supabase.co"), ThrottledJWKClient)


class _SlowJwksClient:
    """A synchronous key lookup that takes a while -- what a cold-cache or
    rotation-triggered JWKS fetch looks like from the caller's side."""

    def __init__(self, public_key: Any, delay: float) -> None:
        self._public_key = public_key
        self._delay = delay

    def get_signing_key_from_jwt(self, token: str) -> Any:
        time.sleep(self._delay)
        return SimpleNamespace(key=self._public_key)


def _request_with(jwks_client: Any, token: str) -> Any:
    state = SimpleNamespace(jwks_client=jwks_client, supabase_url=_ISSUER_URL)
    return SimpleNamespace(
        headers={"authorization": f"Bearer {token}"}, app=SimpleNamespace(state=state)
    )


async def test_a_slow_jwks_lookup_does_not_stall_the_event_loop(
    private_key: EllipticCurvePrivateKey,
) -> None:
    token = _sign(private_key, _base_claims())
    request = _request_with(_SlowJwksClient(private_key.public_key(), delay=0.4), token)

    worst_stall = 0.0
    stop = False

    async def ticker() -> None:
        nonlocal worst_stall
        while not stop:
            started = time.perf_counter()
            await asyncio.sleep(0.01)
            worst_stall = max(worst_stall, time.perf_counter() - started - 0.01)

    # The ticker has to already be mid-sleep when the lookup starts: a
    # blocked loop only shows up as a late wake-up of a timer that was
    # already pending, never as anything a not-yet-started task can see.
    ticker_task = asyncio.create_task(ticker())
    await asyncio.sleep(0.05)
    user_id = await require_user_id(request)
    stop = True
    await ticker_task

    assert user_id == _USER_ID
    # Run inline, the 0.4s blocking lookup freezes every other coroutine
    # (observed ~0.4s here); off-loop it never registers.
    assert worst_stall < 0.15


async def test_an_invalid_token_error_does_not_echo_pyjwt_internals(
    private_key: EllipticCurvePrivateKey,
) -> None:
    client = _CountingJwksClient(_jwk_set(private_key, "real-key"), cache_keys=True)
    request = _request_with(client, _token_with_kid(private_key, "attacker-0"))

    with pytest.raises(ApiError) as excinfo:
        await require_user_id(request)

    assert excinfo.value.code == "AUTH_REQUIRED"
    assert excinfo.value.message == "invalid token"
