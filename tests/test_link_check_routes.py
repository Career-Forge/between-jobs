"""POST /applications/{id}/check-link (applications_routes.py): ownership first, the kill switch,
the per-user limit, which link is read, and that the whole thing writes nothing anywhere.

The route is exercised through the real app (TestClient), with a Supabase fake that RECORDS every
write and every table read, and a link checker built on `FakeWeb` (no network)."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from link_check_fakes import GH, LEVER, NOW, FakeWeb, fixture, make_checker
from rendered_logs import rendered

from between_jobs.api import rate_limits
from between_jobs.api.app import app
from between_jobs.api.app_state import get_link_checker, get_supabase
from between_jobs.api.auth import require_user_id
from between_jobs.api.link_check import LinkChecker
from between_jobs.api.link_fetch import USER_AGENT, refuse_hosts_outside_allowlist

_USER = "00000000-0000-0000-0000-000000000001"
_OTHER_USER = "00000000-0000-0000-0000-000000000002"
_JOB = "20000000-0000-0000-0000-000000000001"
_SNAPSHOT = "20000000-0000-0000-0000-000000000002"
_APPLICATION = "30000000-0000-0000-0000-000000000001"
_GH_ID = str(fixture("greenhouse_board_list")["body"]["jobs"][0]["id"])
_LEVER_ID = fixture("lever_postings_list")["body"][0]["id"]
_GH_LINK = f"https://boards.greenhouse.io/stripe/jobs/{_GH_ID}"
_PATH = f"/applications/{_APPLICATION}/check-link"


class _Query:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, column: str, value: Any) -> _Query:
        return _Query([r for r in self._rows if r.get(column) == value])

    def in_(self, column: str, values: list[Any]) -> _Query:
        return _Query([r for r in self._rows if r.get(column) in values])

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _Table:
    def __init__(self, supabase: FakeSupabase, name: str) -> None:
        self._supabase = supabase
        self._name = name

    def select(self, *_: Any, **__: Any) -> _Query:
        self._supabase.reads.append(self._name)
        return _Query(list(self._supabase.rows.get(self._name, [])))

    def _write(self, verb: str) -> Any:
        self._supabase.writes.append(f"{verb} {self._name}")
        raise AssertionError(f"the link check wrote to {self._name}")

    def insert(self, *_: Any, **__: Any) -> Any:
        return self._write("insert")

    def update(self, *_: Any, **__: Any) -> Any:
        return self._write("update")

    def upsert(self, *_: Any, **__: Any) -> Any:
        return self._write("upsert")

    def delete(self, *_: Any, **__: Any) -> Any:
        return self._write("delete")


class FakeSupabase:
    """Reads are answered (filtering on `eq` and `in_`, as the real thing does, so ownership is
    really tested); anything that could change a row is recorded in `writes` and refused."""

    def __init__(
        self, *, source_url: str | None, canonical_url: str | None, owner: str = _USER
    ) -> None:
        self.reads: list[str] = []
        self.writes: list[str] = []
        self.rows: dict[str, list[dict[str, Any]]] = {
            "applications": [
                {
                    "id": _APPLICATION,
                    "user_id": owner,
                    "job_id": _JOB,
                    "active_job_snapshot_id": _SNAPSHOT,
                    "status": "saved",
                }
            ],
            "jobs": [{"id": _JOB, "canonical_url": canonical_url}],
            "job_snapshots": (
                [{"id": _SNAPSHOT, "job_id": _JOB, "source_url": source_url}]
                if source_url is not None
                else []
            ),
        }

    def table(self, name: str) -> _Table:
        return _Table(self, name)

    def rpc(self, fn: str, *_: Any, **__: Any) -> Any:
        self.writes.append(f"rpc {fn}")
        raise AssertionError(f"the link check called the database function {fn}")


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")


@pytest.fixture(autouse=True)
def _clear_overrides() -> Any:
    yield
    app.dependency_overrides.clear()


def _client(supabase: FakeSupabase, checker: LinkChecker, user: str = _USER) -> TestClient:
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[require_user_id] = lambda: user
    app.dependency_overrides[get_link_checker] = lambda: checker
    return TestClient(app)


def _greenhouse_web() -> FakeWeb:
    web = FakeWeb()
    web.on("GET", GH, "greenhouse_board_list")
    return web


def test_the_contract_a_200_with_the_four_fields(recorded_events: list[dict[str, Any]]) -> None:
    web = _greenhouse_web()
    supabase = FakeSupabase(source_url=_GH_LINK, canonical_url=None)

    with _client(supabase, make_checker(web)) as client:
        response = client.post(_PATH)

    assert response.status_code == 200
    assert response.json() == {
        "state": "still_listed",
        "reason": "still_listed",
        "ats": "greenhouse",
        "checked_at": NOW.isoformat(timespec="seconds"),
    }
    assert web.urls() == [f"GET {GH}"]


def test_a_check_writes_nothing_anywhere(recorded_events: list[dict[str, Any]]) -> None:
    """Every outcome: an answer, a refusal, a company page, no link. No table is written, no
    database function is called (the recording fake refuses both), and no product event is
    emitted."""
    for source_url in (_GH_LINK, "https://stripe.com/jobs/search?gh_jid=1", "", None):
        supabase = FakeSupabase(source_url=source_url, canonical_url=None)
        with _client(supabase, make_checker(_greenhouse_web())) as client:
            assert client.post(_PATH).status_code == 200
        assert supabase.writes == []
        assert set(supabase.reads) <= {"applications", "job_snapshots", "jobs"}

    assert recorded_events == []


def test_the_link_read_is_the_active_snapshots_source_url() -> None:
    web = _greenhouse_web()
    supabase = FakeSupabase(
        source_url=_GH_LINK, canonical_url=f"https://jobs.lever.co/x/{_LEVER_ID}"
    )

    with _client(supabase, make_checker(web)) as client:
        client.post(_PATH)

    assert web.urls() == [f"GET {GH}"]


@pytest.mark.parametrize("source_url", ["", "   ", None])
def test_without_a_source_url_the_jobs_canonical_url_is_read(source_url: str | None) -> None:
    web = FakeWeb()
    web.on("GET", LEVER, "lever_postings_list")
    supabase = FakeSupabase(
        source_url=source_url, canonical_url=f"https://jobs.lever.co/leverdemo/{_LEVER_ID}"
    )

    with _client(supabase, make_checker(web)) as client:
        response = client.post(_PATH)

    assert response.json()["ats"] == "lever"
    assert response.json()["state"] == "still_listed"


@pytest.mark.parametrize(("source_url", "canonical_url"), [("", None), (None, None), ("  ", "")])
def test_a_job_with_no_link_is_cant_tell_no_link_and_asks_no_one(
    source_url: str | None, canonical_url: str | None
) -> None:
    web = FakeWeb()
    supabase = FakeSupabase(source_url=source_url, canonical_url=canonical_url)

    with _client(supabase, make_checker(web)) as client:
        response = client.post(_PATH)

    assert response.status_code == 200
    body = response.json()
    assert (body["state"], body["reason"], body["ats"]) == ("cant_tell", "no_link", "other")
    assert web.requests == []


def test_a_hostile_saved_link_is_cant_tell_and_never_a_request() -> None:
    web = FakeWeb()
    supabase = FakeSupabase(
        source_url="https://boards.greenhouse.io@evil.example/stripe/jobs/1", canonical_url=None
    )

    with _client(supabase, make_checker(web)) as client:
        response = client.post(_PATH)

    assert (response.json()["state"], response.json()["reason"]) == ("cant_tell", "company_page")
    assert web.requests == []


def test_an_application_the_user_does_not_own_is_a_404_before_anything_else() -> None:
    web = _greenhouse_web()
    supabase = FakeSupabase(source_url=_GH_LINK, canonical_url=None, owner=_OTHER_USER)

    with _client(supabase, make_checker(web)) as client:
        response = client.post(_PATH)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert web.requests == []
    assert supabase.reads == ["applications"]


def test_an_unknown_application_is_a_404_even_with_the_switch_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISABLE_LINK_CHECK", "1")
    web = _greenhouse_web()
    supabase = FakeSupabase(source_url=_GH_LINK, canonical_url=None)

    with _client(supabase, make_checker(web)) as client:
        response = client.post(f"/applications/{_JOB}/check-link")

    assert response.status_code == 404
    assert web.requests == []


@pytest.mark.parametrize("value", ["1", "true", "0", "false", "anything"])
def test_the_kill_switch_answers_503_and_asks_no_one(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("DISABLE_LINK_CHECK", value)
    web = _greenhouse_web()
    supabase = FakeSupabase(source_url=_GH_LINK, canonical_url=None)

    with _client(supabase, make_checker(web)) as client:
        response = client.post(_PATH)

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "PROVIDER_UNAVAILABLE"
    assert error["details"] == {"reason": "link_check_disabled"}
    assert web.requests == []
    assert supabase.reads == ["applications"]
    assert supabase.writes == []


def test_an_empty_switch_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISABLE_LINK_CHECK", "")
    supabase = FakeSupabase(source_url=_GH_LINK, canonical_url=None)

    with _client(supabase, make_checker(_greenhouse_web())) as client:
        assert client.post(_PATH).status_code == 200


def test_a_user_past_twenty_checks_an_hour_is_answered_429_and_nothing_is_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-user limit is the `link_check` bucket of `rate_limits`: a fixed window per user,
    refused with the app's own RATE_LIMITED. The counter here stands in for the database."""
    used: dict[tuple[str, str], int] = {}

    async def claim(_supabase: object, user_id: str, bucket: str) -> rate_limits.RateLimitDecision:
        limit, _window = rate_limits.RATE_LIMITS[bucket]
        used[(user_id, bucket)] = used.get((user_id, bucket), 0) + 1
        allowed = used[(user_id, bucket)] <= limit
        return rate_limits.RateLimitDecision(allowed, 0 if allowed else 1800)

    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", claim)
    web = _greenhouse_web()
    supabase = FakeSupabase(source_url=_GH_LINK, canonical_url=None)
    checker = make_checker(web)

    with _client(supabase, checker) as client:
        statuses = [client.post(_PATH).status_code for _ in range(21)]
        refused = client.post(_PATH)
    with _client(supabase, checker, user=_OTHER_USER) as client:
        elsewhere = client.post(_PATH)

    assert statuses == [200] * 20 + [429]
    assert refused.status_code == 429
    error = refused.json()["error"]
    assert error["code"] == "RATE_LIMITED"
    assert error["details"] == {"retry_after_seconds": 1800, "bucket": "link_check"}
    assert refused.headers["retry-after"] == "1800"
    assert rate_limits.RATE_LIMITS["link_check"] == (20, rate_limits.HOUR)
    # the other person's application is not theirs: 404, not a 429, and not counted against us
    assert elsewhere.status_code == 404
    # one list for all twenty checks of one board
    assert web.urls() == [f"GET {GH}"]


def test_a_refused_check_reads_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def deny(*_: object) -> rate_limits.RateLimitDecision:
        return rate_limits.RateLimitDecision(False, 120)

    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", deny)
    web = _greenhouse_web()
    supabase = FakeSupabase(source_url=_GH_LINK, canonical_url=None)

    with _client(supabase, make_checker(web)) as client:
        response = client.post(_PATH)

    assert response.status_code == 429
    assert supabase.reads == []
    assert web.requests == []


def test_the_log_has_the_platform_the_state_the_reason_and_nothing_of_the_link_or_the_page(
    caplog: pytest.LogCaptureFixture,
) -> None:
    web = _greenhouse_web()
    supabase = FakeSupabase(
        source_url=f"{_GH_LINK}?gh_src=SECRET-QUERY&token=SECRET-TOKEN", canonical_url=None
    )

    with caplog.at_level(logging.DEBUG), _client(supabase, make_checker(web)) as client:
        client.post(_PATH)

    ours = [r for r in caplog.records if r.name == "between_jobs.api.link_check"]
    assert [r.ctx["state"] for r in ours] == ["still_listed"]  # type: ignore[attr-defined]
    text = rendered(r for r in caplog.records if r.name.startswith("between_jobs"))
    for leaked in ("SECRET-QUERY", "SECRET-TOKEN", "gh_src", _GH_ID, "stripe"):
        assert leaked not in text


# -- how the app builds it ---------------------------------------------------------------------


def test_the_app_builds_the_checker_with_its_own_allowlisted_client_and_closes_it() -> None:
    with TestClient(app):
        checker = app.state.link_checker
        http = checker.fetcher.http

        assert isinstance(checker, LinkChecker)
        assert http is not app.state.http
        assert http is not app.state.hiring_http
        assert http.event_hooks["request"] == [refuse_hosts_outside_allowlist]
        assert http.follow_redirects is False
        assert http.headers["user-agent"] == USER_AGENT
        assert not http.is_closed

    assert http.is_closed


def test_nothing_is_started_by_the_app_for_the_check() -> None:
    """The check is not a worker: no loop, no task, nothing on a schedule. `DISABLE_LINK_CHECK` is
    read per request by the route, so the registry /health reports on has no entry for it."""
    with TestClient(app):
        registered = set(app.state.workers.workers)

    assert registered
    assert not {name for name in registered if "link" in name}
