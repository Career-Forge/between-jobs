"""The engine seam (src/between_jobs/engines): the contract, what each backend says it can do,
that the remote backend is the HTTP client unchanged, and that the built-in backend answers "not
available" for what it does not do and nothing else.

The remote backend's wire behaviour (the request bodies, the error mapping) is
tests/test_forge_engines_client.py; here it is only shown to forward to that client without
changing a byte of what is sent or returned. Which backend the app picks is
tests/test_engine_gateway.py.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import httpx
import pytest

import between_jobs
from between_jobs.api import forge_engines_client as client
from between_jobs.api.credential_resolver import ResolvedCredential
from between_jobs.api.errors import ApiError
from between_jobs.engines import (
    CAPABILITIES_BY_KIND,
    ENGINE_OPERATIONS,
    EngineBackend,
    EngineOperation,
    GenericBackend,
    RemoteBackend,
    claims_were_verified_by,
    not_available,
)

_CREDENTIAL = ResolvedCredential(
    provider="openrouter",
    model="anthropic/claude-sonnet-4-6",
    secret="sk-or-v1-plaintext",
    base_url=None,
    source="byok",
)
_SNAPSHOT = {
    "job_id": "job-1",
    "title": "Staff Engineer",
    "company_name": "Acme",
    "location_text": "Remote",
    "description_text": "Build things.",
    "source_url": "https://example.com/jobs/1",
    "source_kind": "manual_paste",
}

# The arguments of every operation, and the client function that carries it over the wire.
_CALLS: dict[EngineOperation, tuple[dict[str, Any], Callable[..., Any]]] = {
    "apply": (
        {
            "resume_template": {"personal": {"name": "Jordan Rivera"}},
            "job_snapshot": _SNAPSHOT,
            "credential": _CREDENTIAL,
            "now": "2026-10-10T00:00:00.000Z",
            "locale": "US",
            "generate_cover_letter": True,
        },
        client.call_apply,
    ),
    "step0": ({"job_description": "Build things.", "credential": _CREDENTIAL}, client.call_step0),
    "gap_interview": (
        {
            "items": [{"cluster_name": "Computer Vision", "bridge_skill": "opencv"}],
            "credential": _CREDENTIAL,
        },
        client.call_gap_interview,
    ),
    "gap_answer_draft": (
        {
            "question": "Have you used OpenCV?",
            "answer": "Yes, a bit.",
            "candidates": [{"pointer": "/projects/0", "label": "VectorBench"}],
            "credential": _CREDENTIAL,
        },
        client.call_gap_answer_draft,
    ),
    "ingest": (
        {"template": {"personal": {"name": "Jordan Rivera"}}, "now": "2026-10-10T00:00:00.000Z"},
        client.call_ingest,
    ),
    "personal": (
        {"resume_doc": {"basics": {}}, "job_context": {"job_title": "Staff Engineer"}},
        client.call_personal,
    ),
    "resolve_header_chips": (
        {"personal": {"name": "Jordan Rivera"}, "header_layout": {"chips": []}},
        client.resolve_header_chips,
    ),
}

# What the remote service answers for each of them.
_ANSWERS: dict[EngineOperation, Any] = {
    "apply": {
        "fit": {"overall_score": 8.0},
        "gate": {"outcome": "proceed", "reason": "", "cautions": []},
        "resume": {"latex": r"\begin{document}x\end{document}"},
        "cover_letter": {"latex": "c", "word_count": 3},
        "ats_attempts": [],
        "regenerated": False,
    },
    "step0": {"clusters": [{"name": "Python", "priority": "must_have", "keywords": ["python"]}]},
    "gap_interview": {"questions": [{"cluster_name": "Python", "question": "Used Rust?"}]},
    "gap_answer_draft": {"bullet": "Did a thing.", "entity_pointer": "/projects/0"},
    "ingest": {"resume_doc": {"basics": {}}},
    "personal": {"resume_text": "Jordan", "personal": {"name": "Jordan"}, "resume_source": "x"},
    "resolve_header_chips": {"chips": [{"field": "email", "text": "j@x.com", "href": None}]},
}


class _RecordingHttp:
    """An HTTP client that answers each path with the scripted body and remembers the requests."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.requests: list[tuple[str, dict[str, Any]]] = []

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.requests.append((url, kwargs))
        return httpx.Response(200, json=self.answers[url], request=httpx.Request("POST", url))


class _NoHttp:
    """The built-in engine runs in this process: any use of the network is a bug."""

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        raise AssertionError(f"the built-in engine made an HTTP request to {url}")


def _shape(function: Callable[..., Any]) -> list[tuple[str, Any, Any]]:
    """What a caller can see of a signature: parameter names, kinds and defaults."""
    return [
        (parameter.name, parameter.kind, parameter.default)
        for name, parameter in inspect.signature(function).parameters.items()
        if name != "self"
    ]


# -- the contract -------------------------------------------------------------------------


def test_the_operations_are_exactly_the_contracts_methods() -> None:
    """`capabilities.operations` can only name what the contract has, and the contract has
    nothing a backend could not name."""
    methods = {
        name
        for name, member in inspect.getmembers(EngineBackend, inspect.isfunction)
        if not name.startswith("_")
    }
    assert methods == set(ENGINE_OPERATIONS)
    assert len(ENGINE_OPERATIONS) == 7
    assert set(_CALLS) == set(ENGINE_OPERATIONS)
    assert set(_ANSWERS) == set(ENGINE_OPERATIONS)


def test_both_backends_satisfy_the_contract() -> None:
    # Checked by mypy (strict): assigning to the Protocol type is the test.
    backends: list[EngineBackend] = [RemoteBackend(), GenericBackend()]
    assert [backend.capabilities.kind for backend in backends] == ["remote", "generic"]


@pytest.mark.parametrize("operation", sorted(ENGINE_OPERATIONS))
def test_every_backend_takes_exactly_what_the_contract_and_the_client_take(
    operation: EngineOperation,
) -> None:
    contract = _shape(getattr(EngineBackend, operation))
    assert _shape(getattr(RemoteBackend, operation)) == contract
    assert _shape(getattr(GenericBackend, operation)) == contract
    # The client function the call sites used before the seam: same arguments, same defaults.
    assert _shape(_CALLS[operation][1]) == contract


# -- what each backend says it can do -------------------------------------------------------


def test_the_remote_backend_does_everything_and_is_stamped_with_the_services_name() -> None:
    capabilities = RemoteBackend().capabilities

    assert capabilities.kind == "remote"
    assert capabilities.operations == ENGINE_OPERATIONS
    # What every artifact written through it has carried since the first one.
    assert (capabilities.generator, capabilities.generator_version) == ("forge-engines", "0.0.1")
    assert capabilities.verifies_claims is True


def test_the_built_in_backend_claims_only_what_it_does_and_is_stamped_with_its_own_name() -> None:
    capabilities = GenericBackend().capabilities

    assert capabilities.kind == "generic"
    # five of the seven: the gap interview's two operations are not implemented
    assert capabilities.operations == {
        "apply",
        "step0",
        "ingest",
        "personal",
        "resolve_header_chips",
    }
    assert capabilities.generator == "between-jobs-builtin"
    assert capabilities.generator_version == between_jobs.__version__
    # every fact in what it writes is checked against the profile, and what the check had to
    # replace is reported as "unsupported claim" warnings
    assert capabilities.verifies_claims is True


def test_each_kind_has_one_set_of_capabilities_and_its_own_generator_name() -> None:
    assert set(CAPABILITIES_BY_KIND) == {"remote", "generic"}
    for kind, capabilities in CAPABILITIES_BY_KIND.items():
        assert capabilities.kind == kind
    names = {capabilities.generator for capabilities in CAPABILITIES_BY_KIND.values()}
    assert len(names) == 2  # a stored document can always tell which engine wrote it


@pytest.mark.parametrize(
    ("generator", "verified"),
    [
        ("forge-engines", True),
        ("between-jobs-builtin", True),
        (None, False),
        ("", False),
        ("some-other-engine", False),
        ("Forge-Engines", False),
    ],
)
def test_a_document_counts_as_claim_checked_only_if_its_engine_is_known_to_check(
    generator: str | None, verified: bool
) -> None:
    assert claims_were_verified_by(generator) is verified


# -- the remote backend is the client, unchanged ---------------------------------------------


@pytest.mark.parametrize("operation", sorted(ENGINE_OPERATIONS))
async def test_the_remote_backend_sends_and_returns_exactly_what_the_client_does(
    operation: EngineOperation,
) -> None:
    arguments, client_function = _CALLS[operation]
    paths = {
        "apply": "/apply",
        "step0": "/step0",
        "gap_interview": "/gap-interview",
        "gap_answer_draft": "/gap-interview/draft",
        "ingest": "/ingest",
        "personal": "/personal",
        "resolve_header_chips": "/header/resolve",
    }
    url = f"http://resume-engine.test{paths[operation]}"  # the address tests/conftest.py sets
    direct = _RecordingHttp({url: _ANSWERS[operation]})
    through_backend = _RecordingHttp({url: _ANSWERS[operation]})

    expected = await client_function(direct, **arguments)
    actual = await getattr(RemoteBackend(), operation)(through_backend, **arguments)

    assert through_backend.requests == direct.requests
    assert len(through_backend.requests) == 1
    assert through_backend.requests[0][0] == url
    if hasattr(expected, "model_dump"):
        assert actual.model_dump() == expected.model_dump()
    elif isinstance(expected, list) and expected and hasattr(expected[0], "model_dump"):
        assert [item.model_dump() for item in actual] == [item.model_dump() for item in expected]
    else:
        assert actual == expected


# Every setting of `apply`, each set to something other than its default, so that an argument
# the seam fails to forward leaves the request different from the one made directly.
_EVERY_APPLY_ARGUMENT: dict[str, Any] = {
    "resume_template": {"personal": {"name": "Jordan Rivera"}},
    "job_snapshot": _SNAPSHOT,
    "credential": _CREDENTIAL,
    "now": "2026-10-10T00:00:00.000Z",
    "density": "compact",
    "locale": "UK",
    "page_count_override": 2,
    "bullet_lead_in": "bold_keyword",
    "summary_mode": "always",
    "show_gpa": False,
    "show_nationality": True,
    "generate_cover_letter": True,
    "dealbreaker_assertions": ["no relocation"],
    "force_generate": True,
    "header_layout": {"chips": [{"field": "email"}], "separator": "dot"},
}
_APPLY_URL = "http://resume-engine.test/apply"


def test_the_every_argument_set_names_each_setting_of_apply_and_none_is_left_at_its_default() -> (
    None
):
    """A setting added to the client has to be added here, and forwarded, before the suite is
    green: a seam that drops it would send the engine the default instead of the person's choice."""
    parameters = {
        name: parameter
        for name, parameter in inspect.signature(client.call_apply).parameters.items()
        if name != "http"
    }

    assert set(_EVERY_APPLY_ARGUMENT) == set(parameters)
    for name, parameter in parameters.items():
        if parameter.default is not inspect.Parameter.empty:
            assert _EVERY_APPLY_ARGUMENT[name] != parameter.default, name


async def test_the_remote_backend_forwards_every_setting_of_apply() -> None:
    direct = _RecordingHttp({_APPLY_URL: _ANSWERS["apply"]})
    through_backend = _RecordingHttp({_APPLY_URL: _ANSWERS["apply"]})

    await client.call_apply(direct, **_EVERY_APPLY_ARGUMENT)  # type: ignore[arg-type]
    await RemoteBackend().apply(through_backend, **_EVERY_APPLY_ARGUMENT)  # type: ignore[arg-type]

    assert through_backend.requests == direct.requests
    body = through_backend.requests[0][1]["json"]
    # not vacuous: the client really does put each of them on the wire
    for name in (
        "density",
        "locale",
        "page_count_override",
        "bullet_lead_in",
        "summary_mode",
        "show_gpa",
        "show_nationality",
        "generate_cover_letter",
        "dealbreaker_assertions",
        "force_generate",
        "header_layout",
        "now",
    ):
        assert body[name] == _EVERY_APPLY_ARGUMENT[name], name


async def test_the_remote_backend_forwards_the_clock_of_ingest() -> None:
    url = "http://resume-engine.test/ingest"
    direct = _RecordingHttp({url: _ANSWERS["ingest"]})
    through_backend = _RecordingHttp({url: _ANSWERS["ingest"]})
    arguments = {"template": {"personal": {"name": "Jordan Rivera"}}, "now": "2031-02-03T04:05:06Z"}

    await client.call_ingest(direct, **arguments)  # type: ignore[arg-type]
    await RemoteBackend().ingest(through_backend, **arguments)  # type: ignore[arg-type]

    assert through_backend.requests == direct.requests
    assert through_backend.requests[0][1]["json"]["now"] == "2031-02-03T04:05:06Z"


async def test_the_remote_backend_reports_a_failure_the_way_the_client_does() -> None:
    class _Down:
        async def post(self, url: str, **kwargs: Any) -> httpx.Response:
            raise httpx.ConnectError("refused")

    with pytest.raises(ApiError) as raised:
        await RemoteBackend().step0(_Down(), job_description="x", credential=_CREDENTIAL)  # type: ignore[arg-type]

    assert raised.value.code == "PROVIDER_UNAVAILABLE"
    assert raised.value.retryable is True


# -- what the built-in backend does not do ------------------------------------------------------


_NOT_LISTED = sorted(ENGINE_OPERATIONS - GenericBackend().capabilities.operations)
"""Every operation the built-in backend does not list as one it can do. An operation moves out
of this list only by being listed, and then it needs a test of what it does instead."""


@pytest.mark.parametrize("operation", _NOT_LISTED)
async def test_an_operation_the_built_in_backend_does_not_list_says_so_and_does_nothing_else(
    operation: EngineOperation,
) -> None:
    backend = GenericBackend()
    assert operation not in backend.capabilities.operations
    arguments = _CALLS[operation][0]

    with pytest.raises(ApiError) as raised:
        await getattr(backend, operation)(_NoHttp(), **arguments)

    error = raised.value
    assert error.code == "NOT_AVAILABLE_IN_GENERIC_ENGINE"
    assert error.status_code == 409
    assert error.retryable is False
    assert error.details == {"operation": operation, "engine": "generic"}


def test_the_built_in_backend_does_not_do_the_gap_interview() -> None:
    """The statement `EngineCapabilities.operations` makes, from the other side: what is not
    listed answers "not available", and what is listed is tested in tests/test_generic_engine_*.py
    (an operation that is listed and raises `NOT_AVAILABLE_IN_GENERIC_ENGINE` fails there)."""
    assert _NOT_LISTED == ["gap_answer_draft", "gap_interview"]


@pytest.mark.parametrize("operation", sorted(ENGINE_OPERATIONS))
def test_the_message_says_what_to_do_and_leaks_nothing(operation: EngineOperation) -> None:
    message = not_available(operation).message

    assert "built-in engine" in message
    assert "hosted resume engine" in message  # what would make it work
    assert "use another feature" in message  # and what to do meanwhile
    # Plain words for a person: no setting name, address, key or dash-separated aside.
    assert "FORGE_ENGINES" not in message
    assert "http" not in message
    assert " -- " not in message and "—" not in message


def test_each_thing_a_person_can_be_trying_to_do_is_named_in_its_own_words() -> None:
    messages = {operation: not_available(operation).message for operation in ENGINE_OPERATIONS}

    # ingest and personal are two halves of one thing to the person (their resume text)
    assert messages["ingest"] == messages["personal"]
    distinct = {message for operation, message in messages.items() if operation != "personal"}
    assert len(distinct) == len(ENGINE_OPERATIONS) - 1
