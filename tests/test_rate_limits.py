"""The per-user rate limiter (api/rate_limits.py).

The database half (the atomic counter) is tested against a real local stack in
tests/integration/test_local_rate_limits.py. Here: the numbers, the wording, the RPC call and its
answer, and the FastAPI dependency -- what it does before and after authentication, what it
counts, how it answers, and that it lets a request through when the limiter itself breaks.

tests/conftest.py replaces `claim_rate_limit_slot` with an allow-everything double for every
test; the tests below put the real function back, or their own, where they need it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError
from pydantic import BaseModel

from between_jobs.api import rate_limits
from between_jobs.api.app import app
from between_jobs.api.app import handle_api_error as real_api_error_handler
from between_jobs.api.app_state import get_supabase
from between_jobs.api.auth import require_user_id
from between_jobs.api.errors import ApiError
from between_jobs.api.rate_limits import (
    DAY,
    HOUR,
    RATE_LIMITS,
    RateLimitDecision,
    describe_wait,
    limit,
    limiter_bucket,
    rate_limit_error_or_none,
    rate_limited_error,
)
from between_jobs.api.rate_limits import claim_rate_limit_slot as real_claim_rate_limit_slot

_USER = "00000000-0000-0000-0000-0000000000a1"


# -- the table -----------------------------------------------------------------------------


def test_the_numbers_set_for_the_four_most_expensive_actions() -> None:
    assert RATE_LIMITS["discover"] == (20, HOUR)
    assert RATE_LIMITS["prepare"] == (10, HOUR)
    assert RATE_LIMITS["company_intel"] == (10, DAY)
    assert RATE_LIMITS["interview_practice_session"] == (10, DAY)


_EXPECTED_LIMITS: dict[str, tuple[int, int]] = {
    "discover": (20, HOUR),
    "prepare": (10, HOUR),
    "company_intel": (10, DAY),
    "interview_practice_session": (10, DAY),
    "contact_research": (10, DAY),
    "warm_path_events": (10, DAY),
    "positioning_brief": (30, HOUR),
    "outreach_draft": (60, HOUR),
    "contact_lookup": (60, HOUR),
    "interview_practice_answer": (60, HOUR),
    "gap_interview": (60, HOUR),
    "tailor_coverage": (120, HOUR),
    "header_preview": (300, HOUR),
    "hiring_signal_search": (60, HOUR),
    "job_ingest": (60, HOUR),
    "pdf_compile": (120, HOUR),
    "credential_save": (30, HOUR),
    "fill_outcome": (300, HOUR),
    "profile_import": (10, HOUR),
}


def test_every_number_in_the_table_is_pinned() -> None:
    """The budgets are the product of this module, and a typo in one (a PDF download budget
    of 1 an hour, a stolen-key-testing guard of 30000) breaks nothing else in the suite. Pinned
    in full, so changing a number means changing it here on purpose."""
    assert RATE_LIMITS == _EXPECTED_LIMITS


def test_every_bucket_is_a_positive_budget_over_a_positive_window() -> None:
    for bucket, (max_requests, window_seconds) in RATE_LIMITS.items():
        assert bucket and bucket == bucket.strip(), bucket
        assert isinstance(max_requests, int) and max_requests >= 1, bucket
        assert isinstance(window_seconds, int) and window_seconds >= 1, bucket


# -- the wording ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (1, "less than a minute"),
        (59, "less than a minute"),
        (60, "about a minute"),
        (61, "about 2 minutes"),
        (700, "about 12 minutes"),
        (3540, "about 59 minutes"),
        (3541, "about an hour"),  # 59 minutes and a bit is an hour, not "60 minutes"
        (3599, "about an hour"),
        (3600, "about an hour"),
        (3601, "about 2 hours"),
        (10_800, "about 3 hours"),
        (82_800, "about 23 hours"),
        (82_801, "about a day"),  # 23 hours and a bit is a day, not "24 hours"
        (86_399, "about a day"),
        (86_400, "about a day"),
        (86_401, "about 2 days"),
    ],
)
def test_the_wait_is_rounded_up_and_put_in_words(seconds: int, expected: str) -> None:
    assert describe_wait(seconds) == expected


def test_the_error_names_the_limit_the_wait_and_carries_both_in_details() -> None:
    error = rate_limited_error("prepare", 725)

    assert error.code == "RATE_LIMITED"
    assert error.status_code == 429
    assert error.retryable is True
    assert error.message == (
        "You've reached the limit for this action (10 per hour). Try again in about 13 minutes."
    )
    assert error.details == {"retry_after_seconds": 725, "bucket": "prepare"}


def test_a_daily_bucket_says_per_day() -> None:
    assert "(10 per day)" in rate_limited_error("company_intel", 5000).message


def test_it_is_not_the_providers_rate_limit() -> None:
    """PROVIDER_RATE_LIMITED means an upstream provider throttled us; this is ours."""
    assert rate_limited_error("discover", 5).code != "PROVIDER_RATE_LIMITED"


# -- the RPC call --------------------------------------------------------------------------


class _Rpc:
    def __init__(self, data: Any) -> None:
        self._data = data

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._data)


class _FakeSupabase:
    def __init__(self, data: Any = None, error: Exception | None = None) -> None:
        self.data = data
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def rpc(self, name: str, params: dict[str, Any]) -> _Rpc:
        self.calls.append((name, params))
        if self.error is not None:
            raise self.error
        return _Rpc(self.data)


async def test_the_claim_sends_the_buckets_own_numbers_to_the_function() -> None:
    supabase = _FakeSupabase([{"allowed": True, "retry_after_seconds": 0}])

    decision = await real_claim_rate_limit_slot(supabase, _USER, "company_intel")  # type: ignore[arg-type]

    assert decision == RateLimitDecision(True, 0)
    assert supabase.calls == [
        (
            "claim_rate_limit_slot",
            {
                "p_user_id": _USER,
                "p_bucket": "company_intel",
                "p_window_seconds": DAY,
                "p_max_requests": 10,
            },
        )
    ]


async def test_a_refusal_is_a_decision_not_an_error() -> None:
    supabase = _FakeSupabase([{"allowed": False, "retry_after_seconds": 412}])

    decision = await real_claim_rate_limit_slot(supabase, _USER, "discover")  # type: ignore[arg-type]

    assert decision == RateLimitDecision(False, 412)


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {"allowed": True, "retry_after_seconds": 0},  # not a list of rows
        [{"allowed": True, "retry_after_seconds": 0}, {"allowed": True, "retry_after_seconds": 0}],
        [{"allowed": "yes", "retry_after_seconds": 0}],
        [{"allowed": True}],
        [{"allowed": True, "retry_after_seconds": "soon"}],
        ["allowed"],
    ],
)
async def test_an_answer_of_the_wrong_shape_is_an_error_not_a_guess(data: Any) -> None:
    with pytest.raises(RuntimeError, match="unexpected shape"):
        await real_claim_rate_limit_slot(_FakeSupabase(data), _USER, "prepare")  # type: ignore[arg-type]


# -- the dependency, in a small app of its own ---------------------------------------------


class _Body(BaseModel):
    name: str


class _Recorder:
    """Stands in for the database: counts claims, answers from a script."""

    def __init__(self, *decisions: RateLimitDecision, error: Exception | None = None) -> None:
        self.decisions = list(decisions)
        self.error = error
        self.claims: list[tuple[str, str]] = []

    async def __call__(self, _supabase: Any, user_id: str, bucket: str) -> RateLimitDecision:
        self.claims.append((user_id, bucket))
        if self.error is not None:
            raise self.error
        return self.decisions.pop(0) if self.decisions else RateLimitDecision(True, 0)


_ALLOW = RateLimitDecision(True, 0)


def _tiny_app(recorder: _Recorder, *, auth_calls: list[str]) -> FastAPI:
    tiny = FastAPI()
    tiny.add_exception_handler(ApiError, real_api_error_handler)  # type: ignore[arg-type]

    async def counted_auth() -> str:
        auth_calls.append("verified")
        return _USER

    tiny.dependency_overrides[require_user_id] = counted_auth
    tiny.dependency_overrides[get_supabase] = lambda: object()

    @tiny.post("/work", dependencies=[Depends(limit("prepare"))])
    async def work(body: _Body, user_id: str = Depends(require_user_id)) -> dict[str, str]:
        return {"user": user_id, "name": body.name}

    @tiny.get("/read")
    async def read(user_id: str = Depends(require_user_id)) -> dict[str, str]:
        return {"user": user_id}

    return tiny


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Recorder]:
    recorder = _Recorder()
    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", recorder)
    yield recorder


def test_an_allowed_request_goes_through_and_is_counted_once(recorded: _Recorder) -> None:
    auth_calls: list[str] = []
    client = TestClient(_tiny_app(recorded, auth_calls=auth_calls))

    response = client.post("/work", json={"name": "x"})

    assert response.status_code == 200
    assert response.json() == {"user": _USER, "name": "x"}
    assert recorded.claims == [(_USER, "prepare")]


def test_the_user_is_verified_once_even_though_both_the_limiter_and_the_handler_ask(
    recorded: _Recorder,
) -> None:
    auth_calls: list[str] = []
    client = TestClient(_tiny_app(recorded, auth_calls=auth_calls))

    client.post("/work", json={"name": "x"})

    assert auth_calls == ["verified"]


def test_a_refused_request_is_a_429_with_the_envelope_and_a_retry_after_header(
    recorded: _Recorder,
) -> None:
    recorded.decisions = [RateLimitDecision(False, 725)]
    client = TestClient(_tiny_app(recorded, auth_calls=[]))

    response = client.post("/work", json={"name": "x"})

    assert response.status_code == 429
    assert response.headers["retry-after"] == "725"
    error = response.json()["error"]
    assert error["code"] == "RATE_LIMITED"
    assert error["retryable"] is True
    assert error["details"] == {"retry_after_seconds": 725, "bucket": "prepare"}
    assert "about 13 minutes" in error["message"]


def test_the_handler_never_runs_for_a_refused_request(recorded: _Recorder) -> None:
    ran: list[str] = []
    tiny = FastAPI()
    tiny.add_exception_handler(ApiError, real_api_error_handler)  # type: ignore[arg-type]
    tiny.dependency_overrides[require_user_id] = lambda: _USER
    tiny.dependency_overrides[get_supabase] = lambda: object()

    @tiny.post("/work", dependencies=[Depends(limit("prepare"))])
    async def work() -> dict[str, str]:
        ran.append("ran")
        return {}

    recorded.decisions = [RateLimitDecision(False, 30)]
    response = TestClient(tiny).post("/work")

    assert response.status_code == 429
    assert ran == []


def test_a_retry_after_of_zero_from_a_confused_function_still_says_at_least_one_second(
    recorded: _Recorder,
) -> None:
    recorded.decisions = [RateLimitDecision(False, 0)]
    client = TestClient(_tiny_app(recorded, auth_calls=[]))

    response = client.post("/work", json={"name": "x"})

    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"


def test_an_unauthenticated_request_is_refused_before_the_limiter_is_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", recorder)
    tiny = FastAPI()
    tiny.add_exception_handler(ApiError, real_api_error_handler)  # type: ignore[arg-type]
    tiny.dependency_overrides[get_supabase] = lambda: object()
    # The real authentication, with nothing it could accept.
    tiny.state.jwks_client = object()
    tiny.state.supabase_url = "https://example.supabase.co"

    @tiny.post("/work", dependencies=[Depends(limit("prepare"))])
    async def work() -> dict[str, str]:
        return {}

    client = TestClient(tiny)
    assert client.post("/work").status_code == 401
    assert client.post("/work", headers={"Authorization": "Basic abc"}).status_code == 401
    assert recorder.claims == []


def test_a_request_that_fails_validation_after_authentication_is_still_counted(
    recorded: _Recorder,
) -> None:
    """The counting policy: every attempt that has passed authentication counts, including
    one that goes on to fail."""
    client = TestClient(_tiny_app(recorded, auth_calls=[]))

    response = client.post("/work", json={"nope": 1})

    assert response.status_code == 422
    assert recorded.claims == [(_USER, "prepare")]


def test_a_body_that_is_not_json_is_refused_before_authentication_and_not_counted(
    recorded: _Recorder,
) -> None:
    """FastAPI parses the body before it runs any dependency, so this never reaches the
    limiter (or authentication). Pinned because the module docstring says so."""
    client = TestClient(_tiny_app(recorded, auth_calls=[]))

    response = client.post(
        "/work", content=b"{not json", headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422
    assert recorded.claims == []


def test_a_route_without_the_limiter_never_asks(recorded: _Recorder) -> None:
    client = TestClient(_tiny_app(recorded, auth_calls=[]))

    assert client.get("/read").status_code == 200
    assert recorded.claims == []


def test_a_route_authenticated_another_way_names_its_own_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", recorder)
    tiny = FastAPI()
    tiny.dependency_overrides[get_supabase] = lambda: object()

    async def scoped_auth() -> str:
        return "the-extension-user"

    @tiny.get("/pdf", dependencies=[Depends(limit("pdf_compile", auth=scoped_auth))])
    async def pdf() -> dict[str, str]:
        return {}

    assert TestClient(tiny).get("/pdf").status_code == 200
    assert recorder.claims == [("the-extension-user", "pdf_compile")]


def test_each_bucket_is_claimed_under_its_own_name(recorded: _Recorder) -> None:
    tiny = FastAPI()
    tiny.dependency_overrides[require_user_id] = lambda: _USER
    tiny.dependency_overrides[get_supabase] = lambda: object()

    @tiny.get("/a", dependencies=[Depends(limit("discover"))])
    async def a() -> dict[str, str]:
        return {}

    @tiny.get("/b", dependencies=[Depends(limit("company_intel"))])
    async def b() -> dict[str, str]:
        return {}

    client = TestClient(tiny)
    client.get("/a")
    client.get("/b")

    assert recorded.claims == [(_USER, "discover"), (_USER, "company_intel")]


def test_a_bucket_that_does_not_exist_fails_at_import_not_at_the_first_request() -> None:
    with pytest.raises(ValueError, match="unknown rate-limit bucket 'no_such_bucket'"):
        limit("no_such_bucket")


def test_a_limiter_can_be_told_from_any_other_dependency() -> None:
    dependency = limit("prepare")
    assert limiter_bucket(dependency) == "prepare"
    assert limiter_bucket(require_user_id) is None


# -- failing open --------------------------------------------------------------------------


def test_when_the_limiter_cannot_answer_the_request_goes_through_and_an_error_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    recorder = _Recorder(error=RuntimeError("the database is down: postgres://secret@host/db"))
    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", recorder)
    client = TestClient(_tiny_app(recorder, auth_calls=[]))

    with caplog.at_level(logging.ERROR, logger="between_jobs.api.rate_limits"):
        response = client.post("/work", json={"name": "x"})

    assert response.status_code == 200
    records = [r for r in caplog.records if r.name == "between_jobs.api.rate_limits"]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].getMessage() == "rate limiter unavailable; letting the request through"
    assert records[0].ctx == {"bucket": "prepare"}  # type: ignore[attr-defined]
    assert "postgres://secret" not in caplog.text.split("Traceback")[0]


def test_a_real_rpc_failure_fails_open_through_the_real_claim(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The same, with the real claim function and a Supabase whose RPC raises (a missing
    migration, a revoked grant, a network error)."""
    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", real_claim_rate_limit_slot)
    supabase = _FakeSupabase(error=ConnectionError("unreachable"))
    tiny = FastAPI()
    tiny.dependency_overrides[require_user_id] = lambda: _USER
    tiny.dependency_overrides[get_supabase] = lambda: supabase

    @tiny.post("/work", dependencies=[Depends(limit("prepare"))])
    async def work() -> dict[str, str]:
        return {"ok": "yes"}

    with caplog.at_level(logging.ERROR, logger="between_jobs.api.rate_limits"):
        response = TestClient(tiny).post("/work")

    assert response.status_code == 200
    assert len(supabase.calls) == 1  # it did ask
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_a_malformed_answer_also_fails_open(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", real_claim_rate_limit_slot)
    tiny = FastAPI()
    tiny.dependency_overrides[require_user_id] = lambda: _USER
    tiny.dependency_overrides[get_supabase] = lambda: _FakeSupabase([{"surprise": 1}])

    @tiny.post("/work", dependencies=[Depends(limit("prepare"))])
    async def work() -> dict[str, str]:
        return {"ok": "yes"}

    with caplog.at_level(logging.ERROR, logger="between_jobs.api.rate_limits"):
        response = TestClient(tiny).post("/work")

    assert response.status_code == 200
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def _api_error(code: str) -> APIError:
    return APIError({"message": "boom", "code": code, "details": None, "hint": None})


async def test_a_user_id_with_no_account_is_refused_not_let_through(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The first claim for a (user, bucket) inserts a row that references auth.users, so a
    deleted account's id raises foreign_key_violation. Its access token keeps verifying until
    it expires, so letting it through would give it a bucket that never fills."""
    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", real_claim_rate_limit_slot)
    supabase = _FakeSupabase(error=_api_error("23503"))

    with caplog.at_level(logging.WARNING, logger="between_jobs.api.rate_limits"):
        error = await rate_limit_error_or_none(supabase, _USER, "credential_save")  # type: ignore[arg-type]

    assert error is not None
    assert error.code == "AUTH_REQUIRED"
    assert error.status_code == 401
    records = [r for r in caplog.records if r.name == "between_jobs.api.rate_limits"]
    assert [r.levelno for r in records] == [logging.WARNING]  # a refusal, not an outage


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("the database is down"),
        ConnectionError("unreachable"),
        _api_error("42883"),  # function does not exist: a missing migration
        _api_error("42501"),  # permission denied: a revoked grant
        _api_error("57014"),  # statement timeout
        _api_error("22023"),  # a bad parameter the limiter itself sent
    ],
)
async def test_every_other_limiter_failure_still_fails_open(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", real_claim_rate_limit_slot)
    assert await rate_limit_error_or_none(_FakeSupabase(error=error), _USER, "prepare") is None  # type: ignore[arg-type]


def test_a_deleted_account_is_a_401_on_a_limited_route_and_the_handler_never_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", real_claim_rate_limit_slot)
    ran: list[str] = []
    tiny = FastAPI()
    tiny.add_exception_handler(ApiError, real_api_error_handler)  # type: ignore[arg-type]
    tiny.dependency_overrides[require_user_id] = lambda: _USER
    tiny.dependency_overrides[get_supabase] = lambda: _FakeSupabase(error=_api_error("23503"))

    @tiny.post("/work", dependencies=[Depends(limit("credential_save"))])
    async def work() -> dict[str, str]:
        ran.append("ran")  # stands in for the provider call this bucket exists to bound
        return {}

    response = TestClient(tiny).post("/work")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"
    assert ran == []


async def test_a_cancelled_request_is_not_swallowed_as_a_limiter_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-open covers the limiter's own errors, never the server cancelling the request."""

    async def cancelled(*_: Any) -> RateLimitDecision:
        raise asyncio.CancelledError

    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", cancelled)
    dependency = limit("prepare")

    with pytest.raises(asyncio.CancelledError):
        await dependency(user_id=_USER, supabase=object())


# -- through the real app: the header, CORS, the request id --------------------------------


@contextmanager
def _limited_route_on_the_real_app() -> Iterator[None]:
    async def work() -> dict[str, str]:
        return {"ok": "yes"}

    app.add_api_route(
        "/__test_limited", work, methods=["GET"], dependencies=[Depends(limit("discover"))]
    )
    app.dependency_overrides[require_user_id] = lambda: _USER
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        app.router.routes[:] = [
            r for r in app.router.routes if getattr(r, "path", None) != "/__test_limited"
        ]


def test_the_real_app_sends_retry_after_and_lets_a_browser_read_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    recorder = _Recorder(RateLimitDecision(False, 3000))
    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", recorder)

    with _limited_route_on_the_real_app(), TestClient(app) as client:
        response = client.get("/__test_limited", headers={"Origin": "https://app.example.com"})

    assert response.status_code == 429
    assert response.headers["retry-after"] == "3000"
    assert response.headers["access-control-allow-origin"] == "*"
    exposed = response.headers["access-control-expose-headers"].lower()
    assert "retry-after" in exposed and "x-request-id" in exposed
    assert "x-request-id" in response.headers
    assert response.json()["error"]["code"] == "RATE_LIMITED"


def test_other_429s_do_not_grow_a_retry_after_they_never_had(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PROVIDER_RATE_LIMITED has no `retry_after_seconds`, and gets no header."""

    async def throttled() -> None:
        raise ApiError("PROVIDER_RATE_LIMITED", "The provider is throttling.", retryable=True)

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    app.add_api_route("/__test_throttled", throttled, methods=["GET"])
    try:
        with TestClient(app) as client:
            response = client.get("/__test_throttled")
    finally:
        app.router.routes[:] = [
            r for r in app.router.routes if getattr(r, "path", None) != "/__test_throttled"
        ]

    assert response.status_code == 429
    assert "retry-after" not in response.headers
