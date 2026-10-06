"""The tester programme's enrollment (api/tester_enrollment.py and tester_enrollment_routes.py):
the lists and the version it shares with the database and the web app, the three routes, and the
switch that turns the gate on. The gate itself (what it refuses, where, and what it does when it
cannot tell) is tests/test_tester_enrollment_gate.py.

What the real database does with the rows (the grants, the CHECKs, the cascade) is
tests/integration/test_local_product_events.py; who may see or change whose row, through the
routes and the functions, is the cross-tenant cases in tests/integration/cross_tenant/."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import tester_fakes
from fastapi.testclient import TestClient
from test_product_events import _check_list
from tester_fakes import OTHER, USER, FakeSupabase

from between_jobs.api import rate_limits, tester_enrollment
from between_jobs.api.app import app
from between_jobs.api.app_state import get_supabase
from between_jobs.api.auth import require_user_id
from between_jobs.api.env import ConfigurationError, strict_on_off
from between_jobs.api.tester_enrollment import (
    ROLE_COHORTS,
    SENIORITIES,
    TESTER_AGREEMENT_VERSION,
    EnrollmentNotFound,
    counts_as_enrolled,
    enroll,
    enrollment_view,
    get_enrollment_row,
    is_enrolled,
    withdraw,
)

_ROOT = Path(__file__).parent.parent
_SRC = _ROOT / "src" / "between_jobs" / "api"
_MIGRATIONS = _ROOT / "supabase" / "migrations"
_ENV = "TESTER_PROGRAM_REQUIRED"
_TIMES = ["2026-10-06T10:00:00+00:00", "2026-10-06T11:00:00+00:00", "2026-10-06T12:00:00+00:00"]


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.delenv(_ENV, raising=False)


@pytest.fixture(autouse=True)
def _clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each write takes the next time, so what is stored is predictable and two writes differ."""
    times = iter(_TIMES * 5)
    monkeypatch.setattr(tester_enrollment, "_now", lambda: next(times))


# -- what the code, the database and the web app agree on ----------------------------------


def test_the_servers_agreement_version_is_the_web_apps() -> None:
    """`TESTER_AGREEMENT_VERSION` here and in web/src/content/testerAgreement.ts are one string:
    a person accepts the version the web page shows, and the API refuses any other."""
    source = (_ROOT / "web" / "src" / "content" / "testerAgreement.ts").read_text()
    match = re.search(r'export const TESTER_AGREEMENT_VERSION = "([^"]+)";', source)

    assert match is not None, "the web constant is not where this test looks for it"
    assert match.group(1) == TESTER_AGREEMENT_VERSION
    assert TESTER_AGREEMENT_VERSION == "2026-10-06"


def test_the_version_fits_the_column_that_records_it() -> None:
    assert 0 < len(TESTER_AGREEMENT_VERSION) <= 40  # consent_version's CHECK


def test_the_role_cohorts_and_seniority_bands_are_the_databases() -> None:
    """Two copies of one list: a value only here makes every enrollment with it fail the CHECK; a
    value only in SQL is one nobody can pick."""
    assert _check_list("tester_enrollments", "role_cohort") == ROLE_COHORTS
    assert _check_list("tester_enrollments", "seniority") == SENIORITIES
    assert len(ROLE_COHORTS) == 10 and len(SENIORITIES) == 5


_TABLE_CHANGE = re.compile(
    r"\b(?:alter\s+table|drop\s+table|(?:add|drop|alter)\s+(?:column|constraint)|rename)\b",
    re.IGNORECASE,
)


def test_no_later_migration_changes_the_enrollment_table_without_these_checks_being_updated() -> (
    None
):
    """The list-parity test above reads the migration that created the table (the only way to
    change an applied one is a newer migration). A later `alter table` would pass it untouched,
    so this fails first and says what to do."""
    altered = []
    for path in sorted(_MIGRATIONS.glob("*.sql")):
        if path.name.endswith("_create_product_events_and_tester_enrollments.sql"):
            continue
        sql = re.sub(r"--[^\n]*", "", path.read_text())
        for statement in sql.split(";"):
            if "tester_enrollments" in statement and _TABLE_CHANGE.search(statement):
                altered.append(path.name)
    assert altered == [], (
        f"tester_enrollments was changed by {altered}: update the cohort and seniority lists "
        "here and in web/src/lib/enrollment.ts, and read the live catalog in the integration "
        "tests. If a column was added, dropped or renamed, the Privacy Policy's 'Tester "
        "programme' paragraph (web/src/content/legal.ts) and the list of what the Tester "
        "Agreement says it records (web/src/content/testerAgreement.ts) change in the same "
        "commit: web/src/content/legal.test.ts and testerAgreement.test.ts fail until they do"
    )


def test_nothing_but_the_enrollment_modules_reads_or_writes_the_sponsorship_answer() -> None:
    """The agreement and the Privacy Policy promise the answer is never used by the product.
    The column is named in the migration and in these two modules (the store and the request
    model that carries it) and nowhere else in the API: no scoring, search or generation
    logic can read it without this failing."""
    users = sorted(
        path.name for path in _SRC.glob("*.py") if "needs_sponsorship" in path.read_text()
    )
    assert users == ["tester_enrollment.py", "tester_enrollment_routes.py"]


# -- the switch -----------------------------------------------------------------------------


def test_the_programme_is_not_required_unless_the_switch_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert tester_enrollment.tester_program_required() is False  # unset
    monkeypatch.setenv(_ENV, "")
    assert (
        tester_enrollment.tester_program_required() is False
    )  # a blank line in a .env file is "not set"
    monkeypatch.setenv(_ENV, "   ")
    assert tester_enrollment.tester_program_required() is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [("on", True), ("ON", True), (" On ", True), ("off", False), ("OFF", False)],
)
def test_it_reads_on_and_off_in_any_case(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: bool
) -> None:
    monkeypatch.setenv(_ENV, value)
    assert tester_enrollment.tester_program_required() is expected


@pytest.mark.parametrize(
    "value", ["true", "false", "1", "0", "yes", "no", "onn", "enabled", "required"]
)
def test_any_other_spelling_is_refused_not_guessed_at(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, value: str
) -> None:
    """The DISABLE_* flags read any non-empty value as true. Here `=false` read that way would
    close the product to everyone, and a guess the other way would open a consent gate."""
    monkeypatch.setenv(_ENV, value)
    with caplog.at_level(logging.CRITICAL), pytest.raises(ConfigurationError):
        tester_enrollment.tester_program_required()

    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL]
    assert messages == [f"the API cannot start: {_ENV} must be 'on' or 'off'"]  # never the value


def test_a_bad_switch_stops_the_boot(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(_ENV, "maybe")
    with (
        caplog.at_level(logging.CRITICAL),
        pytest.raises(ConfigurationError, match="TESTER_PROGRAM_REQUIRED must be 'on' or 'off'"),
        TestClient(app),
    ):
        pass


def test_the_strict_switch_helper_takes_its_default_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SOME_SWITCH", raising=False)
    assert strict_on_off("SOME_SWITCH", default=True) is True
    assert strict_on_off("SOME_SWITCH", default=False) is False
    monkeypatch.setenv("SOME_SWITCH", "off")
    assert strict_on_off("SOME_SWITCH", default=True) is False


# -- who counts as enrolled -----------------------------------------------------------------


def test_enrolled_means_the_current_version_and_not_withdrawn() -> None:
    current = tester_fakes.row(version=TESTER_AGREEMENT_VERSION)
    assert counts_as_enrolled(current) is True
    assert counts_as_enrolled({**current, "withdrawn_at": "2026-10-07T00:00:00+00:00"}) is False
    assert counts_as_enrolled({**current, "consent_version": "2026-01-01"}) is False
    assert counts_as_enrolled({**current, "consent_version": ""}) is False
    assert counts_as_enrolled({}) is False


async def test_is_enrolled_reads_the_callers_row_only() -> None:
    supabase = FakeSupabase(
        [
            tester_fakes.row(USER, version=TESTER_AGREEMENT_VERSION),
            tester_fakes.row(OTHER, version="2020-01-01"),
        ]
    )
    assert await is_enrolled(supabase, USER) is True  # type: ignore[arg-type]
    assert await is_enrolled(supabase, OTHER) is False  # type: ignore[arg-type]
    assert await is_enrolled(supabase, "00000000-0000-0000-0000-0000000000c3") is False  # type: ignore[arg-type]


def test_the_answer_without_a_row_is_just_not_enrolled() -> None:
    assert enrollment_view(None) == {"enrolled": False}


def test_the_answer_for_a_current_tester() -> None:
    view = enrollment_view(
        tester_fakes.row(version=TESTER_AGREEMENT_VERSION, needs_sponsorship=True)
    )

    assert view == {
        "enrolled": True,
        "role_cohort": "data_analyst",
        "seniority": "mid",
        "needs_sponsorship": True,
        "consent_version": TESTER_AGREEMENT_VERSION,
        "consented_at": "2026-10-06T09:00:00+00:00",
        "withdrawn_at": None,
        "current_version": TESTER_AGREEMENT_VERSION,
        "needs_reconsent": False,
    }


def test_an_older_agreement_needs_accepting_again_but_a_withdrawn_tester_is_asked_to_join() -> None:
    older = enrollment_view(tester_fakes.row(version="2026-01-01"))
    assert older["enrolled"] is False
    assert older["needs_reconsent"] is True
    assert older["consent_version"] == "2026-01-01"
    assert older["current_version"] == TESTER_AGREEMENT_VERSION

    left = enrollment_view(
        tester_fakes.row(version="2026-01-01", withdrawn_at="2026-10-07T00:00:00+00:00")
    )
    assert left["enrolled"] is False
    assert left["needs_reconsent"] is False
    assert left["withdrawn_at"] == "2026-10-07T00:00:00+00:00"


def test_the_answer_never_carries_the_users_id_or_the_row_creation_time() -> None:
    view = enrollment_view(tester_fakes.row(version=TESTER_AGREEMENT_VERSION))
    assert "user_id" not in view and "created_at" not in view


# -- the store ------------------------------------------------------------------------------


async def test_enrolling_writes_the_current_version_and_the_time_and_clears_a_withdrawal() -> None:
    supabase = FakeSupabase(
        [tester_fakes.row(version="2026-01-01", withdrawn_at="2026-02-01T00:00:00+00:00")]
    )

    stored = await enroll(
        supabase,  # type: ignore[arg-type]
        USER,
        role_cohort="devops_sre",
        seniority="lead_plus",
        needs_sponsorship=False,
    )

    assert stored["consent_version"] == TESTER_AGREEMENT_VERSION
    assert stored["consented_at"] == _TIMES[0]
    assert stored["withdrawn_at"] is None
    assert (stored["role_cohort"], stored["seniority"], stored["needs_sponsorship"]) == (
        "devops_sre",
        "lead_plus",
        False,
    )
    # The upsert did not carry created_at, so the day the person first joined is kept.
    assert supabase.enrollments.rows[USER]["created_at"] == "2026-10-06T09:00:00+00:00"


async def test_enrolling_twice_is_the_same_row() -> None:
    supabase = FakeSupabase()
    for _ in range(2):
        await enroll(
            supabase,  # type: ignore[arg-type]
            USER,
            role_cohort="data_analyst",
            seniority="mid",
            needs_sponsorship=None,
        )

    assert list(supabase.enrollments.rows) == [USER]
    assert supabase.enrollments.rows[USER]["needs_sponsorship"] is None


async def test_an_account_that_no_longer_exists_is_refused_not_a_500() -> None:
    supabase = FakeSupabase()
    supabase.enrollments.upsert_error = tester_fakes.foreign_key_violation()

    from between_jobs.api.errors import ApiError

    with pytest.raises(ApiError) as raised:
        await enroll(
            supabase,  # type: ignore[arg-type]
            USER,
            role_cohort="data_analyst",
            seniority="mid",
            needs_sponsorship=None,
        )

    assert raised.value.code == "AUTH_REQUIRED"


async def test_any_other_database_error_is_not_dressed_up() -> None:
    from postgrest.exceptions import APIError

    supabase = FakeSupabase()
    supabase.enrollments.upsert_error = APIError({"message": "boom", "code": "XX000"})

    with pytest.raises(APIError):
        await enroll(
            supabase,  # type: ignore[arg-type]
            USER,
            role_cohort="data_analyst",
            seniority="mid",
            needs_sponsorship=None,
        )


async def test_withdrawing_sets_the_time_and_keeps_the_row_and_the_answers() -> None:
    supabase = FakeSupabase(
        [tester_fakes.row(version=TESTER_AGREEMENT_VERSION, needs_sponsorship=True)]
    )

    stored = await withdraw(supabase, USER)  # type: ignore[arg-type]

    assert stored["withdrawn_at"] == _TIMES[0]
    assert stored["needs_sponsorship"] is True
    assert stored["role_cohort"] == "data_analyst"
    assert list(supabase.enrollments.rows) == [USER]


async def test_withdrawing_again_keeps_the_first_time() -> None:
    supabase = FakeSupabase([tester_fakes.row(version=TESTER_AGREEMENT_VERSION)])

    first = await withdraw(supabase, USER)  # type: ignore[arg-type]
    second = await withdraw(supabase, USER)  # type: ignore[arg-type]

    assert first["withdrawn_at"] == second["withdrawn_at"] == _TIMES[0]
    assert supabase.enrollments.operations.count("update") == 1


async def test_withdrawing_with_no_enrollment_says_so() -> None:
    with pytest.raises(EnrollmentNotFound):
        await withdraw(FakeSupabase(), USER)  # type: ignore[arg-type]


async def test_withdrawing_touches_only_the_callers_row() -> None:
    supabase = FakeSupabase(
        [
            tester_fakes.row(USER, version=TESTER_AGREEMENT_VERSION),
            tester_fakes.row(OTHER, version=TESTER_AGREEMENT_VERSION),
        ]
    )

    await withdraw(supabase, USER)  # type: ignore[arg-type]

    assert supabase.enrollments.rows[OTHER]["withdrawn_at"] is None
    assert (await get_enrollment_row(supabase, OTHER))["withdrawn_at"] is None  # type: ignore[arg-type,index]


async def test_an_unexpected_answer_from_the_table_is_an_error_not_a_guess() -> None:
    class _Odd:
        def table(self, _name: str) -> Any:
            class _Chain:
                def select(self, *_: Any) -> Any:
                    return self

                def eq(self, *_: Any) -> Any:
                    return self

                async def execute(self) -> Any:
                    return type("R", (), {"data": ["not a row"]})()

            return _Chain()

    with pytest.raises(RuntimeError, match="unexpected shape"):
        await get_enrollment_row(_Odd(), USER)  # type: ignore[arg-type]


# -- the routes -----------------------------------------------------------------------------


class _Claims:
    """Stands in for the limiter's database function, and notes what it was asked."""

    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, str]] = []

    async def __call__(
        self, _supabase: Any, user_id: str, bucket: str
    ) -> rate_limits.RateLimitDecision:
        self.calls.append((user_id, bucket))
        return rate_limits.RateLimitDecision(self.allowed, 0 if self.allowed else 725)


@pytest.fixture
def served() -> Iterator[tuple[TestClient, FakeSupabase]]:
    supabase = FakeSupabase()
    app.dependency_overrides[require_user_id] = lambda: USER
    app.dependency_overrides[get_supabase] = lambda: supabase
    try:
        with TestClient(app) as client:
            yield client, supabase
    finally:
        app.dependency_overrides.clear()


def _join_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "role_cohort": "software_engineer",
        "seniority": "senior",
        "needs_sponsorship": None,
        "accept_version": TESTER_AGREEMENT_VERSION,
    }
    body.update(overrides)
    return body


def test_reading_with_no_enrollment_says_not_enrolled(
    served: tuple[TestClient, FakeSupabase],
) -> None:
    client, _ = served
    response = client.get("/tester/enrollment")

    assert response.status_code == 200
    assert response.json() == {"enrolled": False}


def test_reading_returns_the_callers_own_enrollment_and_nobody_elses(
    served: tuple[TestClient, FakeSupabase],
) -> None:
    client, supabase = served
    supabase.enrollments.rows = {
        USER: tester_fakes.row(USER, version=TESTER_AGREEMENT_VERSION, role_cohort="qa_sdet"),
        OTHER: tester_fakes.row(OTHER, version=TESTER_AGREEMENT_VERSION, role_cohort="devops_sre"),
    }

    body = client.get("/tester/enrollment").json()

    assert body["enrolled"] is True and body["role_cohort"] == "qa_sdet"
    assert "devops_sre" not in str(body)


def test_joining_records_the_answers_the_version_and_the_time(
    served: tuple[TestClient, FakeSupabase],
) -> None:
    client, supabase = served

    response = client.post("/tester/enrollment", json=_join_body(needs_sponsorship=True))

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "enrolled": True,
        "role_cohort": "software_engineer",
        "seniority": "senior",
        "needs_sponsorship": True,
        "consent_version": TESTER_AGREEMENT_VERSION,
        "consented_at": _TIMES[0],
        "withdrawn_at": None,
        "current_version": TESTER_AGREEMENT_VERSION,
        "needs_reconsent": False,
    }
    assert supabase.enrollments.rows[USER]["user_id"] == USER


@pytest.mark.parametrize("answer", [True, False, None])
def test_the_sponsorship_answer_is_stored_as_given_three_ways(
    served: tuple[TestClient, FakeSupabase], answer: bool | None
) -> None:
    client, supabase = served

    client.post("/tester/enrollment", json=_join_body(needs_sponsorship=answer))

    assert supabase.enrollments.rows[USER]["needs_sponsorship"] is answer


def test_joining_twice_is_one_enrollment(served: tuple[TestClient, FakeSupabase]) -> None:
    client, supabase = served

    first = client.post("/tester/enrollment", json=_join_body())
    second = client.post("/tester/enrollment", json=_join_body())

    assert first.status_code == second.status_code == 200
    assert list(supabase.enrollments.rows) == [USER]
    assert second.json()["enrolled"] is True


def test_an_old_version_is_refused_with_a_conflict_and_nothing_is_written(
    served: tuple[TestClient, FakeSupabase],
) -> None:
    client, supabase = served

    response = client.post("/tester/enrollment", json=_join_body(accept_version="2026-01-01"))

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "CONFLICT"
    assert "Reload the page" in error["message"]
    assert supabase.enrollments.rows == {}
    assert supabase.enrollments.operations == []


def test_joining_again_after_withdrawing_clears_the_withdrawal(
    served: tuple[TestClient, FakeSupabase],
) -> None:
    client, supabase = served
    client.post("/tester/enrollment", json=_join_body())
    client.post("/tester/enrollment/withdraw")
    assert supabase.enrollments.rows[USER]["withdrawn_at"] is not None

    again = client.post("/tester/enrollment", json=_join_body(role_cohort="data_engineer")).json()

    assert again["enrolled"] is True and again["withdrawn_at"] is None
    assert again["role_cohort"] == "data_engineer"


@pytest.mark.parametrize(
    "body",
    [
        _join_body(role_cohort="astronaut"),
        _join_body(role_cohort=""),
        _join_body(role_cohort=None),
        _join_body(seniority="wizard"),
        _join_body(needs_sponsorship="true"),
        _join_body(needs_sponsorship=1),
        _join_body(needs_sponsorship="yes"),
        _join_body(accept_version=None),
        _join_body(accept_version=20261006),
        _join_body(accept_version="x" * 41),
        {**_join_body(), "user_id": OTHER},
        {**_join_body(), "consent_version": "2020-01-01"},
        {k: v for k, v in _join_body().items() if k != "needs_sponsorship"},
        {k: v for k, v in _join_body().items() if k != "accept_version"},
        {k: v for k, v in _join_body().items() if k != "role_cohort"},
        {},
    ],
)
def test_a_malformed_body_is_a_422_and_writes_nothing(
    served: tuple[TestClient, FakeSupabase], body: dict[str, Any]
) -> None:
    client, supabase = served

    response = client.post("/tester/enrollment", json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"
    assert supabase.enrollments.operations == []


def test_every_cohort_and_band_in_the_lists_is_accepted(
    served: tuple[TestClient, FakeSupabase],
) -> None:
    client, _ = served
    for cohort in sorted(ROLE_COHORTS):
        assert (
            client.post("/tester/enrollment", json=_join_body(role_cohort=cohort)).status_code
            == 200
        )
    for band in sorted(SENIORITIES):
        assert client.post("/tester/enrollment", json=_join_body(seniority=band)).status_code == 200


def test_withdrawing_marks_the_enrollment_and_answers_with_it(
    served: tuple[TestClient, FakeSupabase],
) -> None:
    client, supabase = served
    client.post("/tester/enrollment", json=_join_body())

    response = client.post("/tester/enrollment/withdraw")

    assert response.status_code == 200
    body = response.json()
    assert body["enrolled"] is False
    assert body["withdrawn_at"] == _TIMES[1]
    assert body["needs_reconsent"] is False
    assert supabase.enrollments.rows[USER]["withdrawn_at"] == _TIMES[1]
    assert client.get("/tester/enrollment").json()["withdrawn_at"] == _TIMES[1]


def test_withdrawing_twice_is_the_same_answer(served: tuple[TestClient, FakeSupabase]) -> None:
    client, _ = served
    client.post("/tester/enrollment", json=_join_body())

    first = client.post("/tester/enrollment/withdraw")
    second = client.post("/tester/enrollment/withdraw")

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


def test_withdrawing_with_no_enrollment_is_a_404(served: tuple[TestClient, FakeSupabase]) -> None:
    client, _ = served

    response = client.post("/tester/enrollment/withdraw")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert "not in the tester programme" in response.json()["error"]["message"]


def test_withdrawing_does_not_delete_the_row(served: tuple[TestClient, FakeSupabase]) -> None:
    client, supabase = served
    client.post("/tester/enrollment", json=_join_body(needs_sponsorship=False))

    client.post("/tester/enrollment/withdraw")

    assert supabase.enrollments.rows[USER]["role_cohort"] == "software_engineer"
    assert supabase.enrollments.rows[USER]["needs_sponsorship"] is False


def test_all_three_routes_need_a_signed_in_user() -> None:
    with TestClient(app) as client:
        assert client.get("/tester/enrollment").status_code == 401
        assert client.post("/tester/enrollment", json=_join_body()).status_code == 401
        assert client.post("/tester/enrollment/withdraw").status_code == 401


def test_the_two_writes_share_one_cheap_limiter_bucket_and_the_read_is_not_limited(
    monkeypatch: pytest.MonkeyPatch, served: tuple[TestClient, FakeSupabase]
) -> None:
    client, _ = served
    claims = _Claims()
    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", claims)

    client.get("/tester/enrollment")
    client.post("/tester/enrollment", json=_join_body())
    client.post("/tester/enrollment/withdraw")

    assert claims.calls == [(USER, "tester_enrollment"), (USER, "tester_enrollment")]
    assert rate_limits.RATE_LIMITS["tester_enrollment"] == (30, rate_limits.HOUR)


def test_a_refused_write_is_a_429_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, served: tuple[TestClient, FakeSupabase]
) -> None:
    client, supabase = served
    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", _Claims(allowed=False))

    response = client.post("/tester/enrollment", json=_join_body())

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    assert supabase.enrollments.operations == []


def test_the_enrollment_is_in_the_api_error_contract() -> None:
    from between_jobs.api.errors import _STATUS_BY_CODE, ApiError

    assert _STATUS_BY_CODE["ENROLLMENT_REQUIRED"] == 403
    error = ApiError("ENROLLMENT_REQUIRED", "x")
    assert error.status_code == 403 and error.retryable is False
