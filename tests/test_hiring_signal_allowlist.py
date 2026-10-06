"""`HIRING_SIGNALS_ALLOWED_USER_IDS`: Hiring signals for the people the operator names, and
for nobody else.

The feature is not advertised, and an operator can limit it to chosen people. What is pinned
here is the promise that follows from that: for anyone not on the list the feature does not
exist. Every route of it answers exactly what the kill switch
(`DISABLE_HIRING_SIGNALS`) answers -- 404 `FEATURE_DISABLED` -- before the body is read, and
that includes a caller who is not signed in; the status route says "not enabled" for them; and
nothing a route could do (a provider call, a read or write of a table) happens on their behalf.

These run through the REAL authentication dependency, not an override: the allowlist is
decided on the verified token's user id, so the tokens here are real ES256 JWTs signed by a
key the test generated, verified the way `test_auth.py` does. Only the database and the
provider transport are fakes (`hiring_signal_fakes`).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from hiring_signal_fakes import APP, OTHER_USER, USER, World

from between_jobs.api import hiring_signal_routes
from between_jobs.api.app import app
from between_jobs.api.app_state import get_hiring_http_client, get_supabase

_ISSUER_URL = "https://example.supabase.co"
_ENV = "HIRING_SIGNALS_ALLOWED_USER_IDS"
_THIRD_USER = "00000000-0000-0000-0000-000000000003"
_SAVE = "40000000-0000-0000-0000-000000000001"
_SEARCH = "50000000-0000-0000-0000-000000000001"
_ACTIVITY = "7506381452083381426"
_TAB_SEARCH = {"query": "data engineer"}
_ROOT = Path(__file__).parent.parent


@pytest.fixture(autouse=True)
def _environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SUPABASE_URL", _ISSUER_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")
    monkeypatch.delenv("DISABLE_HIRING_SIGNALS", raising=False)
    monkeypatch.delenv(_ENV, raising=False)
    yield
    app.dependency_overrides.clear()


class _Signer:
    """Mints real tokens for the key the app was told to trust."""

    def __init__(self) -> None:
        self._key = ec.generate_private_key(ec.SECP256R1())
        self.public_key = self._key.public_key()

    def token(self, user_id: str, *, key: Any = None) -> str:
        now = int(time.time())
        claims = {
            "iss": f"{_ISSUER_URL}/auth/v1",
            "aud": "authenticated",
            "sub": user_id,
            "role": "authenticated",
            "iat": now,
            "exp": now + 3600,
        }
        return jwt.encode(claims, key or self._key, algorithm="ES256")

    def header(self, user_id: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token(user_id)}"}


@pytest.fixture
def signer(monkeypatch: pytest.MonkeyPatch) -> _Signer:
    signer = _Signer()
    # The app was not started (no lifespan), so these are not set: the same two the real
    # `require_user_id` reads from `app.state`.
    monkeypatch.setattr(
        app.state,
        "jwks_client",
        SimpleNamespace(
            get_signing_key_from_jwt=lambda token: SimpleNamespace(key=signer.public_key)
        ),
        raising=False,
    )
    monkeypatch.setattr(app.state, "supabase_url", _ISSUER_URL, raising=False)
    return signer


def _client(world: World) -> TestClient:
    app.dependency_overrides[get_supabase] = lambda: world.supabase
    app.dependency_overrides[get_hiring_http_client] = lambda: world.http
    return TestClient(app)


def _untouched(world: World) -> bool:
    """Neither the provider nor any table was reached."""
    return not world.transport.requests and not any(t.calls for t in world.tables.values())


def _has_the_feature(response: Any) -> bool:
    """The request got past both gates: whatever it then answered (a 409 SETUP_REQUIRED for a
    person with no search key counts), it was not the feature's absence or a 401."""
    code = response.json().get("error", {}).get("code") if response.content else None
    return code not in {"FEATURE_DISABLED", "AUTH_REQUIRED"}


def _error(response: Any) -> dict[str, Any]:
    body = response.json()
    assert set(body) == {"error"}
    error: dict[str, Any] = body["error"]
    return error


# -- the list, as the operator sets it -------------------------------------------------------


def test_with_no_list_everyone_has_the_feature_and_it_reads_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert hiring_signal_routes.hiring_signals_allowlist() is None
    assert hiring_signal_routes.user_may_use_hiring_signals(USER)
    assert hiring_signal_routes.user_may_use_hiring_signals(OTHER_USER)
    for blank in ("", "   "):
        monkeypatch.setenv(_ENV, blank)  # a blank `KEY=` line is "not configured"
        assert hiring_signal_routes.hiring_signals_allowlist() is None
        assert hiring_signal_routes.user_may_use_hiring_signals(OTHER_USER)


def test_with_a_list_only_those_on_it_have_it_and_spaces_around_an_entry_do_not_matter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV, f" {USER} , {_THIRD_USER}")
    assert hiring_signal_routes.hiring_signals_allowlist() == frozenset({USER, _THIRD_USER})
    assert hiring_signal_routes.user_may_use_hiring_signals(USER)
    assert hiring_signal_routes.user_may_use_hiring_signals(_THIRD_USER)
    assert not hiring_signal_routes.user_may_use_hiring_signals(OTHER_USER)


def test_a_listed_id_in_capitals_matches_the_lower_case_id_a_token_carries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upper = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
    monkeypatch.setenv(_ENV, upper)
    assert hiring_signal_routes.user_may_use_hiring_signals(upper.lower())
    assert hiring_signal_routes.user_may_use_hiring_signals(upper)


def test_the_status_route_and_the_route_gate_agree_when_a_token_writes_the_id_in_capitals(
    monkeypatch: pytest.MonkeyPatch, signer: _Signer
) -> None:
    """The two places that decide -- `user_may_use_hiring_signals` for the status route, and
    `require_listed_caller` for every other route -- must give the same answer for one caller,
    or the web app would show the entry (status: enabled) and every route would refuse it
    (404 FEATURE_DISABLED), or the other way round. Here the list holds the id in lower case
    and the token's `sub` is the same id in capitals."""
    lower = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    monkeypatch.setenv(_ENV, lower)
    client = _client(World())
    headers = signer.header(lower.upper())

    assert client.get("/hiring-signals/status", headers=headers).json() == {"enabled": True}
    assert _has_the_feature(
        client.post("/hiring-signals/search", json=_TAB_SEARCH, headers=headers)
    )

    # and for someone who is not on the list both say no, in the same request cycle
    stranger = signer.header(OTHER_USER)
    assert client.get("/hiring-signals/status", headers=stranger).json() == {"enabled": False}
    assert not _has_the_feature(
        client.post("/hiring-signals/search", json=_TAB_SEARCH, headers=stranger)
    )


def test_both_gates_decide_membership_with_the_one_helper() -> None:
    source = (_ROOT / "src" / "between_jobs" / "api" / "hiring_signal_routes.py").read_text()
    # the membership test is written once, inside the helper, and nowhere else
    assert source.count(".lower() in allowed") == 1


def test_the_kill_switch_beats_the_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, USER)
    monkeypatch.setenv("DISABLE_HIRING_SIGNALS", "1")
    assert not hiring_signal_routes.user_may_use_hiring_signals(USER)


# -- every route of the feature ---------------------------------------------------------------


def _routes() -> list[tuple[str, str]]:
    """Every (method, concrete path) of the flag-checked router, read from the router itself,
    so a route added later is covered without anyone remembering to list it."""
    found: list[tuple[str, str]] = []
    for route in hiring_signal_routes.router.routes:
        assert isinstance(route, APIRoute)
        path = (
            route.path.replace("{application_id}", APP)
            .replace("{save_id}", _SAVE)
            .replace("{search_id}", _SEARCH)
        )
        assert "{" not in path, f"{route.path} has a path parameter this test does not fill"
        found.extend((method, path) for method in sorted(route.methods or ()))
    return found


def _send(client: TestClient, method: str, path: str, headers: dict[str, str] | None) -> Any:
    body = {"activity_id": _ACTIVITY} if path.endswith("/saves") and method == "POST" else None
    return client.request(method, path, json=body, headers=headers)


def test_the_enumeration_really_covers_the_whole_feature() -> None:
    """Guards the guard: the router's routes, the status route and the app's OpenAPI surface
    are the same set, and every route of the router is the flag-checked kind."""
    routes = _routes()
    assert len(routes) >= 10
    for route in hiring_signal_routes.router.routes:
        assert isinstance(route, hiring_signal_routes._FlagCheckedRoute)

    surface = {
        (method.upper(), path)
        for path, methods in app.openapi()["paths"].items()
        if "hiring-signals" in path
        for method in methods
    }
    listed = {
        (m, p.replace(APP, "{application_id}").replace(_SAVE, "{save_id}")) for m, p in routes
    }
    listed = {(m, p.replace(_SEARCH, "{search_id}")) for m, p in listed}
    assert surface == listed | {("GET", "/hiring-signals/status")}


@pytest.mark.parametrize("caller", ["not_listed", "no_token", "garbage_token", "bad_scheme"])
def test_no_route_of_the_feature_is_reachable_by_anyone_not_on_the_list(
    monkeypatch: pytest.MonkeyPatch, signer: _Signer, caller: str
) -> None:
    """A signed-in stranger, a caller with no token, one with a token that does not verify
    and one with a header that is not even a bearer token: all get the answer the kill switch
    gives, on every route, and none of them reaches a provider or a table."""
    monkeypatch.setenv(_ENV, f"{USER},{_THIRD_USER}")
    world = World()
    client = _client(world)
    headers = {
        "not_listed": signer.header(OTHER_USER),
        "no_token": None,
        "garbage_token": {"Authorization": "Bearer not.a.jwt"},
        "bad_scheme": {"Authorization": "Basic abc"},
    }[caller]

    for method, path in _routes():
        response = _send(client, method, path, headers)
        assert response.status_code == 404, (method, path, response.text)
        error = _error(response)
        assert error["code"] == "FEATURE_DISABLED", (method, path)
        assert error["retryable"] is False
    assert _untouched(world)


def test_the_answer_for_a_stranger_is_byte_for_byte_the_kill_switchs(
    monkeypatch: pytest.MonkeyPatch, signer: _Signer
) -> None:
    client = _client(World())
    monkeypatch.setenv("DISABLE_HIRING_SIGNALS", "1")
    switched_off = client.post("/hiring-signals/search", json=_TAB_SEARCH)
    monkeypatch.delenv("DISABLE_HIRING_SIGNALS")
    monkeypatch.setenv(_ENV, USER)
    stranger = client.post(
        "/hiring-signals/search",
        json=_TAB_SEARCH,
        headers=signer.header(OTHER_USER),
    )

    assert stranger.status_code == switched_off.status_code == 404
    assert stranger.json() == switched_off.json()


def test_a_stranger_is_refused_before_the_body_is_read_even_when_it_is_not_json(
    monkeypatch: pytest.MonkeyPatch, signer: _Signer
) -> None:
    """A dependency would run after FastAPI had read and parsed the body, so a stranger who
    sent junk would learn the route exists from the 422. The route class refuses first."""
    monkeypatch.setenv(_ENV, USER)
    client = _client(World())

    def post_junk(user_id: str) -> Any:
        return client.post(
            "/hiring-signals/search",
            content=b"{not json",
            headers={"Content-Type": "application/json", **signer.header(user_id)},
        )

    stranger = post_junk(OTHER_USER)
    assert stranger.status_code == 404
    assert _error(stranger)["code"] == "FEATURE_DISABLED"

    # ... and the same junk from someone on the list is the validation error it always was.
    assert post_junk(USER).status_code == 422


def test_a_token_signed_by_another_key_is_not_a_way_in_for_a_listed_id(
    monkeypatch: pytest.MonkeyPatch, signer: _Signer
) -> None:
    """The list is checked against the VERIFIED id: a forged token that names a listed user
    gets the same 404 as any stranger."""
    monkeypatch.setenv(_ENV, USER)
    forger = ec.generate_private_key(ec.SECP256R1())
    world = World()
    forged = {"Authorization": f"Bearer {signer.token(USER, key=forger)}"}

    response = _client(world).post("/hiring-signals/search", json={"query": "x"}, headers=forged)

    assert response.status_code == 404
    assert _error(response)["code"] == "FEATURE_DISABLED"
    assert _untouched(world)


def test_everyone_on_the_list_has_every_route_as_before(
    monkeypatch: pytest.MonkeyPatch, signer: _Signer
) -> None:
    monkeypatch.setenv(_ENV, f"{OTHER_USER},{USER}")
    world = World()
    client = _client(world)

    for method, path in _routes():
        response = _send(client, method, path, signer.header(USER))
        error = response.json().get("error", {}) if response.content else {}
        assert error.get("code") not in {"FEATURE_DISABLED", "AUTH_REQUIRED"}, (method, path)

    assert (
        client.post(
            "/hiring-signals/search", json=_TAB_SEARCH, headers=signer.header(USER)
        ).status_code
        == 200
    )
    # the loop above saved one post for them; their own list is what they get back
    saves = client.get("/hiring-signals/saves", headers=signer.header(USER))
    assert [save["activity_id"] for save in saves.json()["saves"]] == [_ACTIVITY]


def test_with_no_list_the_routes_are_as_they_were_for_any_signed_in_user(
    signer: _Signer,
) -> None:
    client = _client(World())
    assert _has_the_feature(
        client.post("/hiring-signals/search", json=_TAB_SEARCH, headers=signer.header(OTHER_USER))
    )
    # still the 401 of an ordinary route when nobody is signed in: the list changes nothing
    # when it is not set
    anonymous = client.post("/hiring-signals/search", json=_TAB_SEARCH)
    assert anonymous.status_code == 401
    assert _error(anonymous)["code"] == "AUTH_REQUIRED"


def test_the_list_is_read_on_every_request_like_the_switch(
    monkeypatch: pytest.MonkeyPatch, signer: _Signer
) -> None:
    client = _client(World())
    headers = signer.header(OTHER_USER)

    def ask() -> Any:
        return client.post("/hiring-signals/search", json=_TAB_SEARCH, headers=headers)

    assert _has_the_feature(ask())
    monkeypatch.setenv(_ENV, USER)
    assert ask().status_code == 404
    monkeypatch.setenv(_ENV, f"{USER},{OTHER_USER}")
    assert _has_the_feature(ask())
    monkeypatch.setenv(_ENV, "")
    assert _has_the_feature(ask())


def test_a_malformed_list_never_opens_the_feature_to_anyone(
    monkeypatch: pytest.MonkeyPatch, signer: _Signer
) -> None:
    """The boot refuses a malformed list (tests/test_env.py). Should one appear after boot, the
    routes fail (a 500 that says nothing of the setting) instead of guessing who is on it:
    nobody is served, listed or not."""
    monkeypatch.setenv(_ENV, f"{USER},not-a-user-id")
    world = World()
    client = _client(world)
    for user_id in (USER, OTHER_USER):
        response = client.post(
            "/hiring-signals/search", json=_TAB_SEARCH, headers=signer.header(user_id)
        )
        assert response.status_code == 500
        assert _error(response)["code"] == "INTERNAL_ERROR"
        assert "not-a-user-id" not in response.text and _ENV not in response.text
    assert _untouched(world)


# -- the status route -------------------------------------------------------------------------


def test_the_status_route_answers_for_the_caller(
    monkeypatch: pytest.MonkeyPatch, signer: _Signer
) -> None:
    client = _client(World())
    path = "/hiring-signals/status"

    # no list: on for everyone (today's behaviour)
    assert client.get(path, headers=signer.header(USER)).json() == {"enabled": True}
    assert client.get(path, headers=signer.header(OTHER_USER)).json() == {"enabled": True}

    # a list: on for the people on it and off for everyone else, in the same request cycle
    monkeypatch.setenv(_ENV, USER)
    assert client.get(path, headers=signer.header(USER)).json() == {"enabled": True}
    assert client.get(path, headers=signer.header(OTHER_USER)).json() == {"enabled": False}

    # the switch still wins for everyone
    monkeypatch.setenv("DISABLE_HIRING_SIGNALS", "1")
    assert client.get(path, headers=signer.header(USER)).json() == {"enabled": False}


def test_the_status_route_is_not_itself_hidden_and_still_needs_a_signed_in_caller(
    monkeypatch: pytest.MonkeyPatch, signer: _Signer
) -> None:
    """It REPORTS the answer, so it is the one route the gates do not block; but it is not a
    public one: with nobody signed in it is the 401 of every other route, list or no list."""
    client = _client(World())
    for configured in (None, USER):
        if configured is not None:
            monkeypatch.setenv(_ENV, configured)
        anonymous = client.get("/hiring-signals/status")
        assert anonymous.status_code == 401
        assert _error(anonymous)["code"] == "AUTH_REQUIRED"
    response = client.get("/hiring-signals/status", headers=signer.header(OTHER_USER))
    assert response.status_code == 200
    assert response.json() == {"enabled": False}


# -- what the list does not touch ---------------------------------------------------------------


def test_only_the_routes_read_the_list_so_the_cache_and_the_workers_keep_working() -> None:
    """The shared signal cache, its purge worker and the registry are not per person and know
    nothing of the list: the one module that reads the variable is the routes' own."""
    readers = sorted(
        path.name for path in (_ROOT / "src").rglob("*.py") if _ENV in path.read_text()
    )
    assert readers == ["hiring_signal_routes.py"]
    users_of_the_function = sorted(
        path.name
        for path in (_ROOT / "src").rglob("*.py")
        if "hiring_signals_allowlist" in path.read_text()
    )
    assert users_of_the_function == ["app.py", "hiring_signal_routes.py"]
