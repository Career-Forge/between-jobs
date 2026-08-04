"""Tests for Supabase Auth JWT verification (auth.py).

Uses a real, locally-generated ES256 keypair and real PyJWT encode/decode
-- not mocked away, since signature/issuer/audience/expiry verification is
exactly the logic that must be proven correct, not assumed. The JWKS
*fetch* itself (PyJWKClient's own HTTP behavior) isn't re-tested here --
that's the library's job -- only how this module uses what it returns.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
from fastapi.testclient import TestClient

from between_jobs.api.app import app
from between_jobs.api.auth import verify_access_token

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


@pytest.fixture
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", _ISSUER_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")


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
