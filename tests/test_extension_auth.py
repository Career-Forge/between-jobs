"""Tests for the browser extension's scoped sign-out liveness check (E6
continuation) -- extension_auth.py's `require_active_extension_user_id`,
`get_extension_signed_out_at`, and `record_extension_sign_out`.

Uses real, locally-generated ES256 keypairs and real PyJWT encode/decode,
same reasoning as test_auth.py: the whole point of this module is
`iat`-vs-`signed_out_at` comparison logic, so that has to be exercised for
real, not mocked away. The `_request_with` helper and claim-building
constants below deliberately mirror test_auth.py's own -- this is testing
a sibling of that module's own dependency, not a different concern.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
from fastapi.testclient import TestClient

from between_jobs.api.app import app
from between_jobs.api.auth import require_user_id
from between_jobs.api.errors import ApiError
from between_jobs.api.extension_auth import (
    get_extension_signed_out_at,
    record_extension_sign_out,
    require_active_extension_user_id,
)

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


def _request_with(jwks_client: Any, token: str) -> Any:
    state = SimpleNamespace(jwks_client=jwks_client, supabase_url=_ISSUER_URL)
    return SimpleNamespace(
        headers={"authorization": f"Bearer {token}"}, app=SimpleNamespace(state=state)
    )


# ---- a small, self-contained fake Supabase for both the store functions ---
# and the full-app end-to-end tests below. `extension_sign_outs` supports
# exactly what extension_auth.py itself uses (select .eq, upsert); `sessions`
# supports exactly what POST /sessions (app.py) uses (insert) -- the one
# ordinary, non-extension route these tests use to prove scope isolation.


class _EqSelectBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, column: str, value: Any) -> _EqSelectBuilder:
        return _EqSelectBuilder([r for r in self._rows if r.get(column) == value])

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _SignOutsTable:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows: list[dict[str, Any]] = rows if rows is not None else []

    def select(self, *_: Any, **__: Any) -> _EqSelectBuilder:
        return _EqSelectBuilder(self.rows)

    def upsert(self, data: dict[str, Any], *, on_conflict: str) -> Any:
        conflict_cols = on_conflict.split(",")
        existing = next(
            (r for r in self.rows if all(r.get(c) == data.get(c) for c in conflict_cols)), None
        )
        if existing is not None:
            existing.update(data)
        else:
            self.rows.append(dict(data))

        async def _execute() -> SimpleNamespace:
            return SimpleNamespace(data=[data])

        return SimpleNamespace(execute=_execute)


class _SessionsTable:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def insert(self, data: dict[str, Any]) -> Any:
        row = {"id": f"session-{len(self.rows) + 1}", **data}
        self.rows.append(row)

        async def _execute() -> SimpleNamespace:
            return SimpleNamespace(data=[row])

        return SimpleNamespace(execute=_execute)


class _EmptyChain:
    """Always empty, whatever chain of `.eq`/`.in_`/`.order` is called on
    it -- enough for `/extension/lookup`'s `applications`/`jobs`/
    `job_snapshots` reads to reach a real, valid "untracked" outcome
    without needing real fixture data these tests don't care about."""

    def eq(self, *_: Any, **__: Any) -> _EmptyChain:
        return self

    def in_(self, *_: Any, **__: Any) -> _EmptyChain:
        return self

    def order(self, *_: Any, **__: Any) -> _EmptyChain:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=[])


class _EmptyTable:
    def select(self, *_: Any, **__: Any) -> _EmptyChain:
        return _EmptyChain()


class _FakeSupabase:
    def __init__(self, *, extension_sign_outs: list[dict[str, Any]] | None = None) -> None:
        self._extension_sign_outs = _SignOutsTable(extension_sign_outs)
        self._sessions = _SessionsTable()
        # /extension/lookup needs these; empty is a valid, fully-exercised
        # state ("untracked").
        self._empty = _EmptyTable()

    def table(self, name: str) -> Any:
        if name == "extension_sign_outs":
            return self._extension_sign_outs
        if name == "sessions":
            return self._sessions
        if name in ("applications", "jobs", "job_snapshots"):
            return self._empty
        raise AssertionError(f"unexpected table: {name}")


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", _ISSUER_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")


# ---- get_extension_signed_out_at / record_extension_sign_out --------------


async def test_get_signed_out_at_returns_none_for_a_user_who_never_signed_out() -> None:
    supabase = _FakeSupabase(extension_sign_outs=[])
    result = await get_extension_signed_out_at(supabase, _USER_ID)  # type: ignore[arg-type]
    assert result is None


async def test_record_then_get_round_trips_close_to_now() -> None:
    supabase = _FakeSupabase(extension_sign_outs=[])
    before = int(datetime.now(UTC).timestamp())

    await record_extension_sign_out(supabase, _USER_ID)  # type: ignore[arg-type]
    result = await get_extension_signed_out_at(supabase, _USER_ID)  # type: ignore[arg-type]

    after = int(datetime.now(UTC).timestamp())
    assert result is not None
    assert before <= result <= after


async def test_record_extension_sign_out_upserts_rather_than_duplicating() -> None:
    old_iso = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    supabase = _FakeSupabase(extension_sign_outs=[{"user_id": _USER_ID, "signed_out_at": old_iso}])

    await record_extension_sign_out(supabase, _USER_ID)  # type: ignore[arg-type]

    assert len(supabase.table("extension_sign_outs").rows) == 1
    result = await get_extension_signed_out_at(supabase, _USER_ID)  # type: ignore[arg-type]
    assert result is not None
    assert result > int(datetime.fromisoformat(old_iso).timestamp())


# ---- require_active_extension_user_id, called directly --------------------


async def test_a_token_issued_before_sign_out_is_rejected(
    private_key: EllipticCurvePrivateKey,
) -> None:
    now = int(time.time())
    token = _sign(private_key, _base_claims(iat=now - 600))  # issued 10 minutes ago
    signed_out_at_iso = datetime.fromtimestamp(now - 300, tz=UTC).isoformat()  # signed out 5m ago
    supabase = _FakeSupabase(
        extension_sign_outs=[{"user_id": _USER_ID, "signed_out_at": signed_out_at_iso}]
    )
    request = _request_with(_fake_jwks_client(private_key.public_key()), token)

    with pytest.raises(ApiError) as excinfo:
        await require_active_extension_user_id(request, supabase=supabase)  # type: ignore[arg-type]

    assert excinfo.value.code == "AUTH_REQUIRED"


async def test_a_token_issued_after_a_fresh_sign_in_still_works(
    private_key: EllipticCurvePrivateKey,
) -> None:
    now = int(time.time())
    signed_out_at_iso = datetime.fromtimestamp(now - 600, tz=UTC).isoformat()  # signed out 10m ago
    token = _sign(private_key, _base_claims(iat=now - 300))  # a fresh sign-in, 5m ago
    supabase = _FakeSupabase(
        extension_sign_outs=[{"user_id": _USER_ID, "signed_out_at": signed_out_at_iso}]
    )
    request = _request_with(_fake_jwks_client(private_key.public_key()), token)

    user_id = await require_active_extension_user_id(request, supabase=supabase)  # type: ignore[arg-type]

    assert user_id == _USER_ID


async def test_a_token_issued_in_the_same_wall_clock_second_as_sign_out_still_works(
    private_key: EllipticCurvePrivateKey,
) -> None:
    """Regression: `iat` is whole-second NumericDate and
    `get_extension_signed_out_at` truncates to whole seconds too, so a
    sign-out and a fresh sign-in landing in the same integer second used
    to compare equal under the old inclusive `signed_out_at >= issued_at`
    check and get spuriously rejected, even though the token was minted at
    (not before) the sign-out. Reproduces that exact boundary directly,
    with no `time.sleep` -- both values pinned to the identical second."""
    now = int(time.time())
    signed_out_at_iso = datetime.fromtimestamp(now, tz=UTC).isoformat()
    token = _sign(private_key, _base_claims(iat=now))  # same second as the sign-out
    supabase = _FakeSupabase(
        extension_sign_outs=[{"user_id": _USER_ID, "signed_out_at": signed_out_at_iso}]
    )
    request = _request_with(_fake_jwks_client(private_key.public_key()), token)

    user_id = await require_active_extension_user_id(request, supabase=supabase)  # type: ignore[arg-type]

    assert user_id == _USER_ID


async def test_a_token_issued_the_second_before_sign_out_is_still_rejected(
    private_key: EllipticCurvePrivateKey,
) -> None:
    """The other side of the same boundary: the fix widens the pass case
    to "equal," not to "before" -- a token whose `iat` second is strictly
    earlier than the sign-out must still be rejected."""
    now = int(time.time())
    signed_out_at_iso = datetime.fromtimestamp(now, tz=UTC).isoformat()
    token = _sign(private_key, _base_claims(iat=now - 1))  # one second before the sign-out
    supabase = _FakeSupabase(
        extension_sign_outs=[{"user_id": _USER_ID, "signed_out_at": signed_out_at_iso}]
    )
    request = _request_with(_fake_jwks_client(private_key.public_key()), token)

    with pytest.raises(ApiError) as excinfo:
        await require_active_extension_user_id(request, supabase=supabase)  # type: ignore[arg-type]

    assert excinfo.value.code == "AUTH_REQUIRED"


async def test_a_user_who_never_signed_out_of_the_extension_is_never_affected(
    private_key: EllipticCurvePrivateKey,
) -> None:
    token = _sign(private_key, _base_claims())
    supabase = _FakeSupabase(extension_sign_outs=[])
    request = _request_with(_fake_jwks_client(private_key.public_key()), token)

    user_id = await require_active_extension_user_id(request, supabase=supabase)  # type: ignore[arg-type]

    assert user_id == _USER_ID


async def test_an_invalid_token_is_still_rejected_before_any_sign_out_check(
    private_key: EllipticCurvePrivateKey,
) -> None:
    now = int(time.time())
    expired = _sign(private_key, _base_claims(iat=now - 7200, exp=now - 3600))
    supabase = _FakeSupabase(extension_sign_outs=[])
    request = _request_with(_fake_jwks_client(private_key.public_key()), expired)

    with pytest.raises(ApiError) as excinfo:
        await require_active_extension_user_id(request, supabase=supabase)  # type: ignore[arg-type]

    assert excinfo.value.code == "AUTH_REQUIRED"


# ---- end-to-end: the real router, the real dependency, a real TestClient --


@pytest.fixture
def client(private_key: EllipticCurvePrivateKey) -> Any:
    supabase = _FakeSupabase(extension_sign_outs=[])
    with TestClient(app) as test_client:
        app.state.jwks_client = _fake_jwks_client(private_key.public_key())
        app.state.supabase = supabase
        yield test_client, supabase
    app.dependency_overrides.clear()


def test_extension_route_rejects_a_token_issued_before_a_real_sign_out(
    client: Any, private_key: EllipticCurvePrivateKey
) -> None:
    test_client, supabase = client
    now = int(time.time())
    pre_sign_out_token = _sign(private_key, _base_claims(iat=now - 60))

    # A currently-valid token authenticates the sign-out call itself.
    sign_out_response = test_client.post(
        "/extension/sign-out", headers={"Authorization": f"Bearer {pre_sign_out_token}"}
    )
    assert sign_out_response.status_code == 204
    assert len(supabase.table("extension_sign_outs").rows) == 1

    # The SAME token -- its own iat now predates the sign-out it just
    # caused -- is rejected on a real extension route from here on.
    lookup_response = test_client.get(
        "/extension/lookup",
        params={"url": "https://jobs.lever.co/acme/1"},
        headers={"Authorization": f"Bearer {pre_sign_out_token}"},
    )
    assert lookup_response.status_code == 401

    # A fresh sign-in (a new token, later `iat`) works again. `iat` is
    # NumericDate -- whole seconds -- and PyJWT itself rejects a
    # future-dated `iat` (a real, separate validation from this
    # feature's own liveness check), so a deliberate real sleep past the
    # second boundary is the honest way to get a genuinely later,
    # still-valid `iat` here rather than faking one.
    time.sleep(1.1)
    fresh_token = _sign(private_key, _base_claims())
    fresh_response = test_client.get(
        "/extension/lookup",
        params={"url": "https://jobs.lever.co/acme/1"},
        headers={"Authorization": f"Bearer {fresh_token}"},
    )
    assert fresh_response.status_code == 200


def test_a_non_extension_route_is_completely_unaffected_by_an_extension_sign_out(
    client: Any, private_key: EllipticCurvePrivateKey
) -> None:
    """The whole point of "scoped": POST /sessions depends on plain
    require_user_id, never require_active_extension_user_id, so a real
    recorded extension sign-out must have zero effect on it -- confirmed
    here by hitting the real route with a token issued well before a real
    sign-out, not assumed from reading the code."""
    test_client, supabase = client
    now = int(time.time())
    token = _sign(private_key, _base_claims(iat=now - 60))

    sign_out_response = test_client.post(
        "/extension/sign-out", headers={"Authorization": f"Bearer {token}"}
    )
    assert sign_out_response.status_code == 204

    # The exact same, now-extension-stale token still creates a session.
    session_response = test_client.post(
        "/sessions", json={"context": {}}, headers={"Authorization": f"Bearer {token}"}
    )
    assert session_response.status_code == 201
    assert supabase.table("sessions").rows[0]["user_id"] == _USER_ID


async def test_require_user_id_itself_never_reads_the_sign_out_table(
    private_key: EllipticCurvePrivateKey,
) -> None:
    """Direct, unit-level companion to the end-to-end test above: proves
    `require_user_id` (auth.py, completely untouched by this feature)
    succeeds for a token that WOULD be rejected by
    `require_active_extension_user_id`, using the exact same fake
    Supabase state -- not just that the route wiring happens to route
    around it, but that the unmodified function genuinely has no
    dependency on this table at all (it doesn't even take a `supabase`
    argument)."""
    now = int(time.time())
    token = _sign(private_key, _base_claims(iat=now - 600))
    signed_out_at_iso = datetime.fromtimestamp(now - 300, tz=UTC).isoformat()
    request = _request_with(_fake_jwks_client(private_key.public_key()), token)
    supabase = _FakeSupabase(
        extension_sign_outs=[{"user_id": _USER_ID, "signed_out_at": signed_out_at_iso}]
    )

    # The extension-scoped dependency rejects this token, given a
    # sign-out recorded after it was issued...
    with pytest.raises(ApiError) as excinfo:
        await require_active_extension_user_id(request, supabase=supabase)  # type: ignore[arg-type]
    assert excinfo.value.code == "AUTH_REQUIRED"

    # ...but plain require_user_id -- the exact same request, no
    # awareness of `extension_sign_outs` at all -- still succeeds.
    user_id = await require_user_id(request)
    assert user_id == _USER_ID
