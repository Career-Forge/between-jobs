"""Which resume engine answers: the separate service when FORGE_ENGINES_BASE_URL is set, the one
built into the API when it is unset or blank (api/engine_gateway.py), and everything that reports
the choice -- GET /capabilities, /health, the startup log line.

tests/conftest.py sets the variable for every test, so the route tests that fake the separate
service keep exercising it; the tests here take it away or set it, as they need.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from generic_engine_fakes import STEP0_ANSWER, ScriptedModel
from pydantic import ValidationError

from between_jobs.api import engine_gateway, forge_engines_client
from between_jobs.api.app import app
from between_jobs.api.auth import require_user_id
from between_jobs.api.credential_resolver import ResolvedCredential
from between_jobs.api.errors import ApiError
from between_jobs.api.forge_engines_client import (
    ForgeApplyResult,
    GateInfo,
    RemoteApplyResult,
    configured_base_url,
)
from between_jobs.engines import GenericBackend, RemoteBackend

_ENV = "FORGE_ENGINES_BASE_URL"
_USER_ID = "00000000-0000-0000-0000-000000000001"
_CREDENTIAL = ResolvedCredential(
    provider="openrouter",
    model="anthropic/claude-sonnet-4-6",
    secret="sk-or-v1-plaintext",
    base_url=None,
    source="byok",
)


# -- reading the setting ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "address"),
    [
        ("http://engine.internal:5682", "http://engine.internal:5682"),
        ("http://engine.internal:5682/", "http://engine.internal:5682"),
        ("http://engine.internal:5682//", "http://engine.internal:5682"),
        ("  http://engine.internal:5682  ", "http://engine.internal:5682"),
        ("https://engine.example.com/base/", "https://engine.example.com/base"),
    ],
)
def test_a_configured_address_is_read_without_its_trailing_slash(
    monkeypatch: pytest.MonkeyPatch, value: str, address: str
) -> None:
    monkeypatch.setenv(_ENV, value)

    assert configured_base_url() == address
    assert engine_gateway.engine_kind() == "remote"


@pytest.mark.parametrize("blank", ["", " ", "   ", "\t", "\n", " \t \n "])
def test_unset_empty_and_whitespace_all_mean_no_separate_engine(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    """A blank `KEY=` line in a .env file is how "not configured" usually looks."""
    monkeypatch.setenv(_ENV, blank)
    assert configured_base_url() is None
    assert engine_gateway.engine_kind() == "generic"

    monkeypatch.delenv(_ENV)
    assert configured_base_url() is None
    assert engine_gateway.engine_kind() == "generic"


def test_a_value_that_is_only_slashes_is_still_a_separate_engine_that_cannot_be_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Garbage is not "unset": quietly switching to a different engine would hide the mistake.
    It selects the remote engine, whose calls then fail the way an unreachable one does."""
    monkeypatch.setenv(_ENV, "/")

    assert engine_gateway.engine_kind() == "remote"


def test_the_remote_client_refuses_to_guess_an_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """The old behaviour was a silent fallback to http://localhost:5682, which is what sent a
    stranger with only a model key to "couldn't reach the resume engine"."""
    monkeypatch.delenv(_ENV)

    with pytest.raises(RuntimeError, match=_ENV):
        forge_engines_client._base_url()


def test_the_engine_is_chosen_again_on_every_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "http://engine.internal:5682")
    assert isinstance(engine_gateway.active_backend(), RemoteBackend)
    assert engine_gateway.active_capabilities().kind == "remote"

    monkeypatch.delenv(_ENV)
    assert isinstance(engine_gateway.active_backend(), GenericBackend)
    assert engine_gateway.active_capabilities().kind == "generic"

    monkeypatch.setenv(_ENV, "http://engine.internal:5682")
    assert isinstance(engine_gateway.active_backend(), RemoteBackend)


# -- the seven functions ------------------------------------------------------------------------

_CLIENT_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "call_apply": forge_engines_client.call_apply,
    "call_step0": forge_engines_client.call_step0,
    "call_gap_interview": forge_engines_client.call_gap_interview,
    "call_gap_answer_draft": forge_engines_client.call_gap_answer_draft,
    "call_ingest": forge_engines_client.call_ingest,
    "call_personal": forge_engines_client.call_personal,
    "resolve_header_chips": forge_engines_client.resolve_header_chips,
}


@pytest.mark.parametrize("name", sorted(_CLIENT_FUNCTIONS))
def test_the_gateway_keeps_the_name_and_arguments_of_the_client_function_it_replaced(
    name: str,
) -> None:
    def shape(function: Callable[..., Any]) -> list[tuple[str, Any, Any]]:
        return [
            (p.name, p.kind, p.default) for p in inspect.signature(function).parameters.values()
        ]

    assert shape(getattr(engine_gateway, name)) == shape(_CLIENT_FUNCTIONS[name])


# Each gateway function with arguments, the path the separate service answers it on, and what
# that service says.
_CASES: list[tuple[str, dict[str, Any], str, Any]] = [
    (
        "call_apply",
        {
            "resume_template": {"personal": {"name": "Jordan Rivera"}},
            "job_snapshot": {
                "job_id": "job-1",
                "title": "Staff Engineer",
                "company_name": "Acme",
                "description_text": "Build things.",
            },
            "credential": _CREDENTIAL,
            "now": "2026-10-10T00:00:00.000Z",
        },
        "/apply",
        {
            "fit": {"overall_score": 8.0},
            "gate": {"outcome": "proceed"},
            "resume": {"latex": "x"},
            "ats_attempts": [],
            "regenerated": False,
        },
    ),
    (
        "call_step0",
        {"job_description": "Build things.", "credential": _CREDENTIAL},
        "/step0",
        {"clusters": []},
    ),
    (
        "call_gap_interview",
        {"items": [{"cluster_name": "Python", "bridge_skill": "Rust"}], "credential": _CREDENTIAL},
        "/gap-interview",
        {"questions": []},
    ),
    (
        "call_gap_answer_draft",
        {"question": "q", "answer": "a", "candidates": [], "credential": _CREDENTIAL},
        "/gap-interview/draft",
        {"bullet": "b", "entity_pointer": "/projects/0"},
    ),
    (
        "call_ingest",
        {"template": {"personal": {}}, "now": "2026-10-10T00:00:00.000Z"},
        "/ingest",
        {"resume_doc": {}},
    ),
    (
        "call_personal",
        {"resume_doc": {}, "job_context": {}},
        "/personal",
        {"resume_text": "t", "personal": {}, "resume_source": ""},
    ),
    (
        "resolve_header_chips",
        {"personal": {}, "header_layout": None},
        "/header/resolve",
        {"chips": []},
    ),
]


class _Http:
    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.urls: list[str] = []

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.urls.append(url)
        return httpx.Response(200, json=self.answer, request=httpx.Request("POST", url))


@pytest.mark.parametrize(
    ("name", "arguments", "path", "answer"), _CASES, ids=[c[0] for c in _CASES]
)
async def test_with_an_address_set_every_call_goes_to_the_separate_service(
    monkeypatch: pytest.MonkeyPatch, name: str, arguments: dict[str, Any], path: str, answer: Any
) -> None:
    monkeypatch.setenv(_ENV, "http://engine.internal:5682/")
    http = _Http(answer)

    await getattr(engine_gateway, name)(http, **arguments)

    assert http.urls == [f"http://engine.internal:5682{path}"]


async def test_the_gateway_forwards_every_setting_of_apply_to_the_separate_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_engine_backends import _ANSWERS, _EVERY_APPLY_ARGUMENT, _RecordingHttp

    monkeypatch.setenv(_ENV, "http://engine.internal:5682")
    url = "http://engine.internal:5682/apply"
    direct = _RecordingHttp({url: _ANSWERS["apply"]})
    through_gateway = _RecordingHttp({url: _ANSWERS["apply"]})

    await forge_engines_client.call_apply(direct, **_EVERY_APPLY_ARGUMENT)  # type: ignore[arg-type]
    await engine_gateway.call_apply(through_gateway, **_EVERY_APPLY_ARGUMENT)  # type: ignore[arg-type]

    assert through_gateway.requests == direct.requests
    body = through_gateway.requests[0][1]["json"]
    assert body["density"] == "compact" and body["locale"] == "UK"
    assert body["show_gpa"] is False and body["dealbreaker_assertions"] == ["no relocation"]


async def test_the_gateway_forwards_the_clock_of_ingest_to_the_separate_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_engine_backends import _ANSWERS, _RecordingHttp

    monkeypatch.setenv(_ENV, "http://engine.internal:5682")
    url = "http://engine.internal:5682/ingest"
    direct = _RecordingHttp({url: _ANSWERS["ingest"]})
    through_gateway = _RecordingHttp({url: _ANSWERS["ingest"]})
    arguments = {"template": {"personal": {"name": "Jordan Rivera"}}, "now": "2031-02-03T04:05:06Z"}

    await forge_engines_client.call_ingest(direct, **arguments)  # type: ignore[arg-type]
    await engine_gateway.call_ingest(through_gateway, **arguments)  # type: ignore[arg-type]

    assert through_gateway.requests == direct.requests
    assert through_gateway.requests[0][1]["json"]["now"] == "2031-02-03T04:05:06Z"


# What the built-in engine is asked in place of each of those calls: a valid argument set for the
# operations it does, and the same arguments for the two it does not (which refuse them).
_BUILT_IN_ARGUMENTS: dict[str, dict[str, Any]] = {
    "call_apply": _CASES[0][1],
    "call_step0": _CASES[1][1],
    "call_ingest": {"template": {"personal": {"name": "Jordan Rivera"}}, "now": "x"},
    "call_personal": {
        "resume_doc": {"template": {"personal": {"name": "Jordan Rivera"}}},
        "job_context": {},
    },
    "resolve_header_chips": {
        "personal": {"email": "j@example.com"},
        "header_layout": {"chips": [{"field": "email"}]},
    },
}
_NOT_IMPLEMENTED = {"call_gap_interview", "call_gap_answer_draft"}


@pytest.mark.parametrize(
    ("name", "arguments", "path", "answer"), _CASES, ids=[c[0] for c in _CASES]
)
async def test_with_no_address_every_call_is_answered_by_the_built_in_engine_and_touches_no_network(
    monkeypatch: pytest.MonkeyPatch, name: str, arguments: dict[str, Any], path: str, answer: Any
) -> None:
    monkeypatch.delenv(_ENV)
    model = ScriptedModel(replies={"step0": [STEP0_ANSWER]})
    monkeypatch.setattr(engine_gateway, "_GENERIC", GenericBackend(generate=model))
    http = _Http(answer)

    if name in _NOT_IMPLEMENTED:
        with pytest.raises(ApiError) as raised:
            await getattr(engine_gateway, name)(http, **arguments)
        assert raised.value.code == "NOT_AVAILABLE_IN_GENERIC_ENGINE"
    else:
        result = await getattr(engine_gateway, name)(http, **_BUILT_IN_ARGUMENTS[name])
        assert result is not None
    assert http.urls == []  # the separate service was never asked, and no network was used


async def test_an_unset_address_is_not_the_old_localhost_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bug this replaces: with nothing set, a stranger's request went to localhost:5682
    and came back as "Couldn't reach the resume engine" (a retryable PROVIDER_UNAVAILABLE)."""
    monkeypatch.delenv(_ENV)
    http = _Http({"questions": []})

    with pytest.raises(ApiError) as raised:
        await engine_gateway.call_gap_interview(
            http,  # type: ignore[arg-type]
            items=[{"cluster_name": "Python", "bridge_skill": "Rust"}],
            credential=_CREDENTIAL,
        )

    assert raised.value.code != "PROVIDER_UNAVAILABLE"
    assert raised.value.retryable is False
    assert http.urls == []


def _real_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_with_an_address_set_the_separate_service_is_called_over_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV, "http://engine.internal:5682")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"clusters": []})

    async with _real_client(handler) as http:
        result = await engine_gateway.call_step0(
            http, job_description="Build things.", credential=_CREDENTIAL
        )

    assert [(r.method, str(r.url)) for r in seen] == [("POST", "http://engine.internal:5682/step0")]
    assert result.clusters == []


async def test_with_no_address_not_one_request_is_made_by_any_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transport that fails the test on any request at all: the built-in engine runs in this
    process, and its only outbound call (the model) goes through the injected function."""
    monkeypatch.delenv(_ENV)
    monkeypatch.setattr(
        engine_gateway,
        "_GENERIC",
        GenericBackend(generate=ScriptedModel(replies={"step0": [STEP0_ANSWER] * 3})),
    )

    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"the built-in engine made a request to {request.url}")

    async with _real_client(refuse) as http:
        await engine_gateway.call_step0(http, job_description="x", credential=_CREDENTIAL)
        await engine_gateway.call_apply(http, **_BUILT_IN_ARGUMENTS["call_apply"])
        doc = await engine_gateway.call_ingest(http, **_BUILT_IN_ARGUMENTS["call_ingest"])
        await engine_gateway.call_personal(http, resume_doc=doc, job_context={})
        await engine_gateway.resolve_header_chips(
            http, **_BUILT_IN_ARGUMENTS["resolve_header_chips"]
        )
        for unavailable in (
            engine_gateway.call_gap_interview(http, items=[], credential=_CREDENTIAL),
            engine_gateway.call_gap_answer_draft(
                http, question="q", answer="a", candidates=[], credential=_CREDENTIAL
            ),
        ):
            with pytest.raises(ApiError) as raised:
                await unavailable
            assert raised.value.code == "NOT_AVAILABLE_IN_GENERIC_ENGINE"


async def test_a_set_address_never_reaches_the_built_in_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the choice: with an address set, the model is never asked by this
    process (the separate service holds the key for that request) and the built-in engine is
    not consulted."""
    monkeypatch.setenv(_ENV, "http://engine.internal:5682")
    model = ScriptedModel()
    monkeypatch.setattr(engine_gateway, "_GENERIC", GenericBackend(generate=model))
    http = _Http({"clusters": []})

    await engine_gateway.call_step0(
        http,  # type: ignore[arg-type]
        job_description="Build things.",
        credential=_CREDENTIAL,
    )

    assert http.urls == ["http://engine.internal:5682/step0"]
    assert model.calls == []


# -- the result of an apply that scored nothing --------------------------------------------------


def test_the_general_apply_result_lets_an_engine_leave_out_what_it_did_not_compute() -> None:
    result = ForgeApplyResult(resume={"latex": "x"}, regenerated=False)

    assert result.generated is True
    assert result.fit is None
    assert result.gate is None
    assert result.ats_attempts == []
    assert result.final_ats is None


def test_the_remote_apply_result_still_insists_on_the_fit_read_and_the_gate() -> None:
    """Behaviour of the separate service's client is unchanged: a response without the Honest
    Floor's read or verdict is a break of that service's contract and fails as it always did,
    instead of quietly becoming an unscored result."""
    complete = {
        "fit": {"overall_score": 8.0},
        "gate": {"outcome": "proceed"},
        "resume": None,
        "regenerated": False,
    }
    assert RemoteApplyResult.model_validate(complete).gate == GateInfo(outcome="proceed")

    for missing in ("fit", "gate"):
        without = {key: value for key, value in complete.items() if key != missing}
        with pytest.raises(ValidationError) as raised:
            RemoteApplyResult.model_validate(without)
        assert missing in str(raised.value)
        # ... which the general type would have accepted
        assert ForgeApplyResult.model_validate(without).generated is False


# -- GET /capabilities ----------------------------------------------------------------------------


@contextmanager
def _client() -> Iterator[TestClient]:
    app.dependency_overrides[require_user_id] = lambda: _USER_ID
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")


def test_capabilities_says_which_engine_and_never_where(monkeypatch: pytest.MonkeyPatch) -> None:
    secret_looking_address = "http://engine-user:engine-pass@engine.internal:5682"
    monkeypatch.setenv(_ENV, secret_looking_address)
    with _client() as client:
        response = client.get("/capabilities")
    assert response.status_code == 200
    assert response.json()["engine"] == "remote"
    assert "engine.internal" not in response.text and "engine-pass" not in response.text

    monkeypatch.delenv(_ENV)
    with _client() as client:
        response = client.get("/capabilities")
    assert response.json()["engine"] == "generic"


# -- /health ---------------------------------------------------------------------------------------


def _health(
    monkeypatch: pytest.MonkeyPatch,
    answer: Callable[[httpx.Request], httpx.Response] | None,
    **env: str,
) -> Any:
    """`/health` with the dependency probes on (`answer` plays every host), or left off as
    tests/conftest.py has them when `answer` is None."""
    if answer is not None:
        monkeypatch.delenv("DISABLE_HEALTH_DEPENDENCY_CHECKS")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with TestClient(app) as client:
        state = client.app.state  # type: ignore[attr-defined]
        original = state.health_http
        if answer is not None:
            state.health_http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
        try:
            return client.get("/health")
        finally:
            if answer is not None:
                state.health_http = original


def test_health_reports_the_built_in_engine_as_builtin_and_stays_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_ENV)

    response = _health(monkeypatch, None)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["dependencies"]["forge_engines"] == "builtin"
    # the probes are switched off here, and the others still say so
    assert body["dependencies"]["supabase"] == "not_checked"
    assert body["dependencies"]["latex_service"] == "not_checked"


def test_health_reports_a_separate_engine_as_not_checked_when_the_probes_are_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _health(monkeypatch, None)

    assert response.json()["dependencies"]["forge_engines"] == "not_checked"


def test_health_does_not_probe_an_engine_that_is_not_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_ENV)
    hosts: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        hosts.append(str(request.url.host))
        return httpx.Response(200)

    response = _health(monkeypatch, answer, LATEX_SERVICE_BASE_URL="http://latex.test")

    assert response.status_code == 200
    assert response.json()["dependencies"] == {
        "supabase": "ok",
        "forge_engines": "builtin",
        "latex_service": "ok",
    }
    assert sorted(hosts) == ["example.supabase.co", "latex.test"]


def test_health_still_probes_a_separate_engine_and_reports_what_it_finds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hosts: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        hosts.append(str(request.url.host))
        return httpx.Response(503 if request.url.host == "engine.test" else 200)

    response = _health(
        monkeypatch,
        answer,
        FORGE_ENGINES_BASE_URL="http://engine.test:5682",
        LATEX_SERVICE_BASE_URL="http://latex.test",
    )

    assert response.status_code == 200  # dependencies are reported, never gating
    assert response.json()["dependencies"]["forge_engines"] == "error"
    assert "engine.test" in hosts


def test_a_cached_health_answer_is_not_reused_after_the_engine_setting_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/health caches its probes for 30 s. Within one process the setting does not change in
    practice, but an answer that said `builtin` must not be served for a separate engine (or the
    other way round) if it did."""

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    monkeypatch.delenv("DISABLE_HEALTH_DEPENDENCY_CHECKS")
    monkeypatch.setenv("LATEX_SERVICE_BASE_URL", "http://latex.test")
    monkeypatch.delenv(_ENV)
    with TestClient(app) as client:
        state = client.app.state  # type: ignore[attr-defined]
        original = state.health_http
        state.health_http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
        try:
            first = client.get("/health").json()["dependencies"]["forge_engines"]
            monkeypatch.setenv(_ENV, "http://engine.test:5682")
            second = client.get("/health").json()["dependencies"]["forge_engines"]
        finally:
            state.health_http = original

    assert (first, second) == ("builtin", "ok")


# -- the startup line ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "kind"), [("http://engine.internal:5682", "remote"), ("", "generic")]
)
def test_the_boot_says_which_engine_is_active_and_not_where(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    address: str,
    kind: str,
) -> None:
    monkeypatch.setenv(_ENV, address)

    with caplog.at_level(logging.INFO, logger="between_jobs"), _client():
        pass

    lines = [r for r in caplog.records if r.getMessage() == "resume engine active"]
    assert len(lines) == 1
    assert lines[0].ctx == {"engine": kind}  # type: ignore[attr-defined]
    assert lines[0].levelno == logging.INFO
    assert "engine.internal" not in repr(lines[0].__dict__)
