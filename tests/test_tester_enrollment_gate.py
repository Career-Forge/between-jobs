"""The tester-programme gate: when TESTER_PROGRAM_REQUIRED is on, the costly features are closed to
a person who has not accepted the current tester agreement (api/tester_enrollment.py, enforced
from api/rate_limits.py).

What is pinned here: the gate does nothing at all while the switch is off; what it refuses and
with what answer; that a refusal is not counted against the person's limit; that a lookup that
fails refuses the request (a consent check fails CLOSED, unlike the limiter itself); that every
route that carries a limiter is behind it except the ones that enroll; and that the route with a
limiter of its own and the chat bot are behind it too. The inventory of which routes carry a
limiter is tests/test_rate_limit_inventory.py."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import test_telegram_prepare_callback as prepare_fakes
import tester_fakes
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from test_rate_limit_inventory import LIMITED
from tester_fakes import OTHER, USER, FakeSupabase

from between_jobs.api import applications_routes, channel_messages, rate_limits, tester_enrollment
from between_jobs.api.app import app
from between_jobs.api.app import handle_api_error as real_api_error_handler
from between_jobs.api.app_state import get_supabase
from between_jobs.api.auth import require_user_id
from between_jobs.api.errors import ApiError
from between_jobs.api.extension_auth import require_active_extension_user_id
from between_jobs.api.rate_limits import RateLimitDecision, limit
from between_jobs.api.tester_enrollment import TESTER_AGREEMENT_VERSION

_ENV = "TESTER_PROGRAM_REQUIRED"


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", prepare_fakes._WEBHOOK_SECRET)
    monkeypatch.delenv(_ENV, raising=False)


class _Claims:
    """Stands in for the limiter's database function: notes what it was asked, answers from a
    script, and can be made to break."""

    def __init__(self, *, error: Exception | None = None, allowed: bool = True) -> None:
        self.calls: list[tuple[str, str]] = []
        self.error = error
        self.allowed = allowed

    async def __call__(self, _supabase: Any, user_id: str, bucket: str) -> RateLimitDecision:
        self.calls.append((user_id, bucket))
        if self.error is not None:
            raise self.error
        return RateLimitDecision(self.allowed, 0 if self.allowed else 600)


@pytest.fixture
def claims(monkeypatch: pytest.MonkeyPatch) -> _Claims:
    recorder = _Claims()
    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", recorder)
    return recorder


def _require(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "on")


def _tiny_app(supabase: FakeSupabase, ran: list[str]) -> FastAPI:
    """One costly route (a bucket that is gated) and one that enrolls (a bucket that is not)."""
    tiny = FastAPI()
    tiny.add_exception_handler(ApiError, real_api_error_handler)  # type: ignore[arg-type]
    tiny.dependency_overrides[require_user_id] = lambda: USER
    tiny.dependency_overrides[get_supabase] = lambda: supabase

    @tiny.post("/costly", dependencies=[Depends(limit("prepare"))])
    async def costly() -> dict[str, str]:
        ran.append("costly")
        return {"ok": "yes"}

    @tiny.post("/enroll", dependencies=[Depends(limit("tester_enrollment"))])
    async def enrolling() -> dict[str, str]:
        ran.append("enroll")
        return {"ok": "yes"}

    return tiny


def _current(user_id: str = USER) -> dict[str, Any]:
    return tester_fakes.row(user_id, version=TESTER_AGREEMENT_VERSION)


# -- off: nothing changes -------------------------------------------------------------------


def test_with_the_programme_off_the_gate_does_nothing_and_asks_the_database_nothing(
    claims: _Claims,
) -> None:
    supabase = FakeSupabase()  # nobody is enrolled
    ran: list[str] = []

    response = TestClient(_tiny_app(supabase, ran)).post("/costly")

    assert response.status_code == 200
    assert ran == ["costly"]
    assert supabase.enrollments.operations == []
    assert claims.calls == [(USER, "prepare")]


def test_with_the_programme_off_a_broken_enrollment_table_cannot_matter(
    claims: _Claims,
) -> None:
    supabase = FakeSupabase()
    supabase.enrollments.fail_with = ConnectionError("down")

    assert TestClient(_tiny_app(supabase, [])).post("/costly").status_code == 200


def test_off_is_also_what_an_explicit_off_means(
    monkeypatch: pytest.MonkeyPatch, claims: _Claims
) -> None:
    monkeypatch.setenv(_ENV, "off")
    assert TestClient(_tiny_app(FakeSupabase(), [])).post("/costly").status_code == 200


# -- on: what is refused --------------------------------------------------------------------


def test_a_person_who_has_not_joined_is_refused_with_a_403_that_says_to_join(
    monkeypatch: pytest.MonkeyPatch, claims: _Claims
) -> None:
    _require(monkeypatch)
    ran: list[str] = []

    response = TestClient(_tiny_app(FakeSupabase(), ran)).post("/costly")

    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "ENROLLMENT_REQUIRED"
    assert error["retryable"] is False
    assert "tester programme" in error["message"]
    assert "website" in error["message"]
    assert ran == []


def test_a_refusal_is_not_counted_against_the_limit(
    monkeypatch: pytest.MonkeyPatch, claims: _Claims
) -> None:
    _require(monkeypatch)

    TestClient(_tiny_app(FakeSupabase(), [])).post("/costly")

    assert claims.calls == []


def test_a_current_tester_goes_through_and_is_counted_once(
    monkeypatch: pytest.MonkeyPatch, claims: _Claims
) -> None:
    _require(monkeypatch)
    ran: list[str] = []

    response = TestClient(_tiny_app(FakeSupabase([_current()]), ran)).post("/costly")

    assert response.status_code == 200
    assert ran == ["costly"]
    assert claims.calls == [(USER, "prepare")]


@pytest.mark.parametrize(
    "stored",
    [
        tester_fakes.row(version="2026-01-01"),
        tester_fakes.row(
            version=TESTER_AGREEMENT_VERSION, withdrawn_at="2026-10-07T00:00:00+00:00"
        ),
        tester_fakes.row(version="2026-01-01", withdrawn_at="2026-10-07T00:00:00+00:00"),
        tester_fakes.row(version=""),
    ],
    ids=["an older agreement", "withdrawn", "withdrawn from an older agreement", "no version"],
)
def test_an_old_or_withdrawn_enrollment_does_not_count(
    monkeypatch: pytest.MonkeyPatch, claims: _Claims, stored: dict[str, Any]
) -> None:
    _require(monkeypatch)

    response = TestClient(_tiny_app(FakeSupabase([stored]), [])).post("/costly")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ENROLLMENT_REQUIRED"


def test_somebody_elses_enrollment_is_not_yours(
    monkeypatch: pytest.MonkeyPatch, claims: _Claims
) -> None:
    _require(monkeypatch)

    response = TestClient(_tiny_app(FakeSupabase([_current(OTHER)]), [])).post("/costly")

    assert response.status_code == 403


def test_the_routes_that_enroll_are_open_to_someone_who_has_not_enrolled(
    monkeypatch: pytest.MonkeyPatch, claims: _Claims
) -> None:
    """Otherwise the gate would lock everyone out of the one door past it."""
    _require(monkeypatch)
    ran: list[str] = []

    response = TestClient(_tiny_app(FakeSupabase(), ran)).post("/enroll")

    assert response.status_code == 200
    assert ran == ["enroll"]
    assert claims.calls == [(USER, "tester_enrollment")]


def test_the_limit_still_applies_to_a_tester_who_is_let_through(
    monkeypatch: pytest.MonkeyPatch, claims: _Claims
) -> None:
    _require(monkeypatch)
    claims.allowed = False

    response = TestClient(_tiny_app(FakeSupabase([_current()]), [])).post("/costly")

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"


def test_a_broken_limiter_still_fails_open_for_a_tester(
    monkeypatch: pytest.MonkeyPatch, claims: _Claims
) -> None:
    """The limiter's own failure policy is unchanged: only the consent check fails closed."""
    _require(monkeypatch)
    claims.error = ConnectionError("the limiter is down")

    assert TestClient(_tiny_app(FakeSupabase([_current()]), [])).post("/costly").status_code == 200


# -- on: when it cannot tell ----------------------------------------------------------------


def test_a_lookup_that_fails_refuses_the_request_with_a_retryable_503(
    monkeypatch: pytest.MonkeyPatch, claims: _Claims, caplog: pytest.LogCaptureFixture
) -> None:
    """Fail CLOSED. Letting a request through because the check broke would let someone use the
    costly features with no consent recorded, which is what the gate is for."""
    _require(monkeypatch)
    supabase = FakeSupabase([_current()])  # enrolled, but the lookup cannot say so
    supabase.enrollments.fail_with = ConnectionError("the database is unreachable")
    ran: list[str] = []

    with caplog.at_level(logging.ERROR, logger="between_jobs.api.tester_enrollment"):
        response = TestClient(_tiny_app(supabase, ran)).post("/costly")

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "PROVIDER_UNAVAILABLE"
    assert error["retryable"] is True
    assert "nothing was started" in error["message"]
    assert ran == []
    assert claims.calls == []
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert [r.getMessage() for r in errors] == [
        "tester enrollment lookup failed; refusing the request"
    ]
    assert errors[0].exc_info is not None


def test_an_answer_of_the_wrong_shape_also_refuses(
    monkeypatch: pytest.MonkeyPatch, claims: _Claims
) -> None:
    _require(monkeypatch)

    async def odd(_supabase: Any, _user_id: str) -> dict[str, Any] | None:
        raise RuntimeError("tester_enrollments returned an unexpected shape")

    monkeypatch.setattr(tester_enrollment, "get_enrollment_row", odd)

    assert TestClient(_tiny_app(FakeSupabase(), [])).post("/costly").status_code == 503


def test_the_routes_that_enroll_are_not_blocked_by_a_broken_lookup(
    monkeypatch: pytest.MonkeyPatch, claims: _Claims
) -> None:
    _require(monkeypatch)
    supabase = FakeSupabase()
    supabase.enrollments.fail_with = ConnectionError("down")

    assert TestClient(_tiny_app(supabase, [])).post("/enroll").status_code == 200


# -- on: the real app -----------------------------------------------------------------------


@pytest.fixture
def real_app() -> Iterator[FakeSupabase]:
    supabase = FakeSupabase()
    app.dependency_overrides[require_user_id] = lambda: USER
    app.dependency_overrides[require_active_extension_user_id] = lambda: USER
    app.dependency_overrides[get_supabase] = lambda: supabase
    try:
        yield supabase
    finally:
        app.dependency_overrides.clear()


def _url(route: str) -> tuple[str, str]:
    method, path = route.split(" ", 1)
    return method, re.sub(r"\{[^}]*\}", "00000000-0000-0000-0000-0000000000f1", path)


def test_capabilities_says_whether_the_programme_is_required(
    monkeypatch: pytest.MonkeyPatch, real_app: FakeSupabase
) -> None:
    with TestClient(app) as client:
        assert client.get("/capabilities").json()["tester_program_required"] is False
        monkeypatch.setenv(_ENV, "on")
        assert client.get("/capabilities").json()["tester_program_required"] is True
        monkeypatch.setenv(_ENV, "off")
        assert client.get("/capabilities").json()["tester_program_required"] is False


def test_every_route_that_carries_a_limiter_is_behind_the_gate_except_the_ones_that_enroll(
    monkeypatch: pytest.MonkeyPatch, real_app: FakeSupabase, claims: _Claims
) -> None:
    """Walks the inventory of limited routes (test_rate_limit_inventory.LIMITED) and calls each as
    a person who has not joined: a 403 ENROLLMENT_REQUIRED before anything else about the request
    (even its body) is looked at, and not a count against anyone's limit. Then as a tester: the
    same call is not turned away for enrollment."""
    _require(monkeypatch)
    gated = {route: bucket for route, bucket in LIMITED.items() if bucket != "tester_enrollment"}
    assert len(gated) >= 25, "the inventory is not being walked"

    with TestClient(app, raise_server_exceptions=False) as client:
        for route in gated:
            method, path = _url(route)
            refused = client.request(method, path, json={})
            assert refused.status_code == 403, f"{route} is not behind the gate: {refused.text}"
            assert refused.json()["error"]["code"] == "ENROLLMENT_REQUIRED", route
        assert claims.calls == [], "a refused request was counted"

        real_app.enrollments.rows[USER] = _current()
        for route in gated:
            method, path = _url(route)
            let_through = client.request(method, path, json={})
            code = None
            if let_through.headers.get("content-type", "").startswith("application/json"):
                code = (let_through.json().get("error") or {}).get("code")
            assert code != "ENROLLMENT_REQUIRED", f"{route} turns a current tester away"


def test_a_costly_route_does_its_work_only_for_a_tester(
    monkeypatch: pytest.MonkeyPatch, real_app: FakeSupabase, claims: _Claims
) -> None:
    _require(monkeypatch)
    ran: list[tuple[Any, ...]] = []

    async def fake_prepare(*args: Any, **kwargs: Any) -> dict[str, Any]:
        ran.append(args)
        return {"result": "prepared"}

    monkeypatch.setattr(applications_routes, "run_prepare_application", fake_prepare)
    path = "/applications/00000000-0000-0000-0000-0000000000f1/prepare"
    body = {"idempotency_key": "k" * 20}

    with TestClient(app) as client:
        assert client.post(path, json=body).status_code == 403
        assert ran == []
        real_app.enrollments.rows[USER] = _current()
        assert client.post(path, json=body).status_code == 201
        assert len(ran) == 1


def test_the_enrollment_routes_work_for_someone_who_has_not_enrolled_and_then_open_the_rest(
    monkeypatch: pytest.MonkeyPatch, real_app: FakeSupabase, claims: _Claims
) -> None:
    _require(monkeypatch)
    body = {
        "role_cohort": "data_engineer",
        "seniority": "mid",
        "needs_sponsorship": None,
        "accept_version": TESTER_AGREEMENT_VERSION,
    }
    cheap = "/applications/00000000-0000-0000-0000-0000000000f1/prepare-result"

    with TestClient(app) as client:
        assert client.get("/tester/enrollment").json() == {"enrolled": False}
        # a cheap read is not behind the gate
        assert client.get(cheap).status_code != 403
        joined = client.post("/tester/enrollment", json=body)
        assert joined.status_code == 200 and joined.json()["enrolled"] is True
        assert client.get("/tester/enrollment").json()["enrolled"] is True
        # withdrawing closes the costly features again, and leaving is never blocked
        left = client.post("/tester/enrollment/withdraw")
        assert left.status_code == 200 and left.json()["enrolled"] is False
        gate = client.post(
            "/applications/00000000-0000-0000-0000-0000000000f1/prepare",
            json={"idempotency_key": "k" * 20},
        )
        assert gate.status_code == 403


def test_the_route_with_a_limiter_of_its_own_is_behind_the_gate_as_well(
    monkeypatch: pytest.MonkeyPatch, real_app: FakeSupabase, claims: _Claims
) -> None:
    _require(monkeypatch)
    body = {"application_id": "00000000-0000-0000-0000-0000000000f1", "question_text": "Why us?"}

    with TestClient(app, raise_server_exceptions=False) as client:
        refused = client.post("/extension/draft-answer", json=body)
        assert refused.status_code == 403
        assert refused.json()["error"]["code"] == "ENROLLMENT_REQUIRED"

        real_app.enrollments.rows[USER] = _current()
        let_through = client.post("/extension/draft-answer", json=body)
        # past the gate it is the ordinary route: no such application, so a 404
        assert let_through.status_code == 404


def test_the_extensions_draft_answer_is_open_when_the_programme_is_off(
    real_app: FakeSupabase,
) -> None:
    body = {"application_id": "00000000-0000-0000-0000-0000000000f1", "question_text": "Why us?"}

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post("/extension/draft-answer", json=body).status_code == 404
    assert real_app.enrollments.operations == []


def test_account_deletion_and_reading_your_own_data_stay_open(
    monkeypatch: pytest.MonkeyPatch, real_app: FakeSupabase, claims: _Claims
) -> None:
    """Nobody is locked out of leaving, or of what is theirs."""
    _require(monkeypatch)

    with TestClient(app, raise_server_exceptions=False) as client:
        for route in (
            "GET /applications",
            "GET /profile/current",
            "GET /today",
            "GET /saved-searches",
            "GET /capabilities",
        ):
            method, path = _url(route)
            assert client.request(method, path).status_code != 403, route
        # the wrong phrase is a 422 from the route itself: the gate is not in the way
        assert client.post("/account/delete", json={"confirm": "no"}).status_code == 422


# -- background work is not gated, and the text a person accepts says so --------------------------

_API_DIR = Path(tester_enrollment.__file__).parent
_SCHEDULED_WORKERS = ("saved_search_matcher.py", "gmail_reply_checker.py")
_GATE_NAMES = (
    "tester_enrollment",
    "enrollment_error_or_none",
    "require_enrollment",
    "counts_as_enrolled",
    "is_enrolled",
    "tester_program_required",
)


@pytest.mark.parametrize("worker", _SCHEDULED_WORKERS)
def test_the_scheduled_workers_do_not_ask_about_enrollment(worker: str) -> None:
    """A tester who withdraws still has their saved searches matched and their Gmail reply
    threads checked, on their own AI key: there is no request to refuse, and nothing here asks
    whose account it is. The Tester Agreement ('Leaving the programme') and the withdraw
    confirmation tell the person exactly that, and that they pause, delete or disconnect those
    themselves. If this test fails because a worker now asks about enrollment, the two texts
    (web/src/content/testerAgreement.ts and web/src/components/EnrollmentView.tsx) and the
    'Background work is NOT gated' paragraph of api/tester_enrollment.py are now wrong: change
    all of them in the same commit, and bump TESTER_AGREEMENT_VERSION in both languages."""
    source = (_API_DIR / worker).read_text(encoding="utf-8")

    for name in _GATE_NAMES:
        assert name not in source, f"{worker} mentions {name}"


# -- on: the chat bot -----------------------------------------------------------------------


def _bot_post(supabase: Any, telegram: Any, http: Any) -> Any:
    return prepare_fakes._post(
        supabase,
        telegram,
        http,
        prepare_fakes._callback_update(f"app:prepare:{prepare_fakes._APPLICATION_ID}"),
    )


def _bot_fakes() -> tuple[Any, Any, Any]:
    supabase = prepare_fakes._FakeSupabaseClient(
        channel_identities_rows=[{"user_id": prepare_fakes._USER_ID}]
    )
    return supabase, prepare_fakes._FakeTelegramClient(), prepare_fakes._FakeHttpClient()


def test_the_bot_turns_an_unenrolled_person_away_and_says_how_to_join(
    monkeypatch: pytest.MonkeyPatch, claims: _Claims
) -> None:
    _require(monkeypatch)

    async def not_enrolled(_supabase: Any, _user_id: str) -> bool:
        return False

    monkeypatch.setattr(tester_enrollment, "is_enrolled", not_enrolled)
    supabase, telegram, http = _bot_fakes()

    response = _bot_post(supabase, telegram, http)

    assert response.status_code == 200
    assert [text for _chat, text, _markup in telegram.sent] == [
        channel_messages.ENROLLMENT_REQUIRED_TEXT
    ]
    assert "website" in channel_messages.ENROLLMENT_REQUIRED_TEXT
    assert "/link" in channel_messages.ENROLLMENT_REQUIRED_TEXT
    assert http.post_calls == []  # neither the engine nor the LaTeX service was called
    assert telegram.documents_sent == []
    assert claims.calls == []  # a refusal spends none of the limit


def test_the_bot_goes_ahead_for_a_tester(monkeypatch: pytest.MonkeyPatch, claims: _Claims) -> None:
    _require(monkeypatch)

    async def enrolled(_supabase: Any, user_id: str) -> bool:
        return user_id == prepare_fakes._USER_ID

    monkeypatch.setattr(tester_enrollment, "is_enrolled", enrolled)
    supabase, telegram, http = _bot_fakes()

    _bot_post(supabase, telegram, http)

    assert len(telegram.documents_sent) == 1
    assert claims.calls == [(prepare_fakes._USER_ID, "prepare")]


def test_the_bot_refuses_when_it_cannot_tell(
    monkeypatch: pytest.MonkeyPatch, claims: _Claims
) -> None:
    _require(monkeypatch)

    async def broken(_supabase: Any, _user_id: str) -> bool:
        raise ConnectionError("down")

    monkeypatch.setattr(tester_enrollment, "is_enrolled", broken)
    supabase, telegram, http = _bot_fakes()

    _bot_post(supabase, telegram, http)

    assert [text for _chat, text, _markup in telegram.sent] == [
        f"❌ {tester_enrollment.LOOKUP_FAILED_MESSAGE}"
    ]
    assert http.post_calls == [] and telegram.documents_sent == []


def test_the_bot_is_untouched_while_the_programme_is_off(claims: _Claims) -> None:
    supabase, telegram, http = _bot_fakes()

    _bot_post(supabase, telegram, http)

    assert len(telegram.documents_sent) == 1
