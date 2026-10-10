"""Tests for the forge-engines HTTP client (Sprint 3.0d).

The outbound call is faked at the httpx client boundary -- same convention
as test_credentials_routes.py's `_FakeHttpClient` -- never a real network
call in a unit test. forge-engines' own live behavior is covered by its
own test suite in the forge-engines repo; this file only proves between-
jobs builds the right request and handles forge-engines' response (and
failure modes) correctly.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from between_jobs.api.credential_resolver import ResolvedCredential
from between_jobs.api.engine_contract import ForgeFitResult
from between_jobs.api.errors import ApiError
from between_jobs.api.forge_engines_client import (
    ForgeApplyResult,
    GateInfo,
    call_apply,
    call_gap_answer_draft,
    call_gap_interview,
    job_posting_payload,
    resolve_header_chips,
)

_CREDENTIAL = ResolvedCredential(
    provider="openrouter",
    model="anthropic/claude-sonnet-4-6",
    secret="sk-or-v1-plaintext",
    base_url=None,
    source="byok",
)

_SNAPSHOT = {
    "id": "snap-1",
    "job_id": "job-1",
    "title": "Staff Engineer",
    "company_name": "Acme",
    "location_text": "Remote",
    "description_text": "Build things.",
    "source_url": "https://example.com/jobs/1",
    "source_kind": "manual_paste",
}

_RESUME_TEMPLATE: dict[str, Any] = {"personal": {"name": "Jordan Rivera"}}

_APPLY_RESPONSE_BODY = {
    "job": {"title": "Staff Engineer"},
    "personal": {"name": "Jordan Rivera"},
    "seniority": {"mode": "mid"},
    "fit": {"overall_score": 8.0},
    "gate": {"outcome": "proceed", "reason": "", "cautions": ["Borderline seniority match."]},
    "resume": {"latex": r"\begin{document}...", "resumePlainText": "..."},
    "ats_attempts": [
        {
            "overall_score": 72,
            "breakdown": {
                "semanticCoverage": 80,
                "experienceQuality": 70,
                "hardReqScore": 90,
                "quantification": 60,
                "companyAlignment": 50,
                "structure": 100,
            },
            "confidence": "high",
            "rating": "strong",
            "gaps": [],
        }
    ],
    "regenerated": False,
    "pass1": {},
    "step0": {},
}


class _FakeHttpClient:
    def __init__(self, *, status_code: int = 200, body: Any = None, raise_error: bool = False):
        self.status_code = status_code
        self.body = body if body is not None else _APPLY_RESPONSE_BODY
        self.raise_error = raise_error
        self.requests: list[tuple[str, dict[str, Any]]] = []

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.requests.append((url, kwargs))
        if self.raise_error:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(
            status_code=self.status_code, json=self.body, request=httpx.Request("POST", url)
        )


def test_job_posting_payload_maps_snapshot_fields() -> None:
    payload = job_posting_payload(_SNAPSHOT)
    assert payload == {
        "job_id": "job-1",
        "title": "Staff Engineer",
        "company": "Acme",
        "location": "Remote",
        "description": "Build things.",
        "url": "https://example.com/jobs/1",
        "source": "manual_paste",
    }


def test_job_posting_payload_handles_missing_location_and_url() -> None:
    snapshot = {**_SNAPSHOT, "location_text": None, "source_url": None}
    payload = job_posting_payload(snapshot)
    assert payload["location"] == ""
    assert payload["url"] == ""


async def test_call_apply_sends_the_expected_request_body() -> None:
    http = _FakeHttpClient()

    await call_apply(
        http,  # type: ignore[arg-type]
        resume_template=_RESUME_TEMPLATE,
        job_snapshot=_SNAPSHOT,
        credential=_CREDENTIAL,
        now="2026-08-15T00:00:00.000Z",
    )

    assert len(http.requests) == 1
    url, kwargs = http.requests[0]
    assert url.endswith("/apply")
    body = kwargs["json"]
    assert body["resume_template"] == _RESUME_TEMPLATE
    assert body["job_url"] == "https://example.com/jobs/1"
    assert body["now"] == "2026-08-15T00:00:00.000Z"
    assert body["credential"] == {
        "secret": "sk-or-v1-plaintext",
        "model": "anthropic/claude-sonnet-4-6",
        "base_url": None,
    }
    assert body["job_posting"]["title"] == "Staff Engineer"
    assert body["density"] == "balanced"
    assert body["show_nationality"] is False
    assert body["generate_cover_letter"] is False
    assert body["dealbreaker_assertions"] is None
    assert body["force_generate"] is False
    assert body["header_layout"] is None


async def test_call_apply_sends_dealbreaker_assertions_when_provided() -> None:
    http = _FakeHttpClient()

    await call_apply(
        http,  # type: ignore[arg-type]
        resume_template=_RESUME_TEMPLATE,
        job_snapshot=_SNAPSHOT,
        credential=_CREDENTIAL,
        now="2026-08-15T00:00:00.000Z",
        dealbreaker_assertions=["On-site role"],
    )

    body = http.requests[0][1]["json"]
    assert body["dealbreaker_assertions"] == ["On-site role"]


async def test_call_apply_sends_force_generate_when_true() -> None:
    http = _FakeHttpClient()

    await call_apply(
        http,  # type: ignore[arg-type]
        resume_template=_RESUME_TEMPLATE,
        job_snapshot=_SNAPSHOT,
        credential=_CREDENTIAL,
        now="2026-08-15T00:00:00.000Z",
        force_generate=True,
    )

    body = http.requests[0][1]["json"]
    assert body["force_generate"] is True


async def test_call_apply_sends_show_nationality_when_true() -> None:
    http = _FakeHttpClient()

    await call_apply(
        http,  # type: ignore[arg-type]
        resume_template=_RESUME_TEMPLATE,
        job_snapshot=_SNAPSHOT,
        credential=_CREDENTIAL,
        now="2026-08-15T00:00:00.000Z",
        show_nationality=True,
    )

    body = http.requests[0][1]["json"]
    assert body["show_nationality"] is True


async def test_call_apply_sends_generate_cover_letter_when_true() -> None:
    http = _FakeHttpClient()

    await call_apply(
        http,  # type: ignore[arg-type]
        resume_template=_RESUME_TEMPLATE,
        job_snapshot=_SNAPSHOT,
        credential=_CREDENTIAL,
        now="2026-08-15T00:00:00.000Z",
        generate_cover_letter=True,
    )

    body = http.requests[0][1]["json"]
    assert body["generate_cover_letter"] is True


async def test_call_apply_parses_a_successful_response() -> None:
    http = _FakeHttpClient()

    result = await call_apply(
        http,  # type: ignore[arg-type]
        resume_template=_RESUME_TEMPLATE,
        job_snapshot=_SNAPSHOT,
        credential=_CREDENTIAL,
        now="2026-08-15T00:00:00.000Z",
    )

    assert result.generated is True
    assert result.regenerated is False
    assert result.gate.cautions == ["Borderline seniority match."]
    assert result.final_ats is not None
    assert result.final_ats.overall_score == 72
    assert result.final_ats.breakdown.semantic_coverage == 80
    assert result.fit.overall_score == 8.0
    assert result.cover_letter is None
    assert result.claim_warnings == []


async def test_call_apply_parses_cover_letter_when_present() -> None:
    body_with_cover_letter = {
        **_APPLY_RESPONSE_BODY,
        "cover_letter": {"latex": r"\documentclass{article}", "word_count": 42},
    }
    http = _FakeHttpClient(body=body_with_cover_letter)

    result = await call_apply(
        http,  # type: ignore[arg-type]
        resume_template=_RESUME_TEMPLATE,
        job_snapshot=_SNAPSHOT,
        credential=_CREDENTIAL,
        now="2026-08-15T00:00:00.000Z",
        generate_cover_letter=True,
    )

    assert result.cover_letter is not None
    assert result.cover_letter["word_count"] == 42


async def test_call_apply_parses_claim_warnings_when_present() -> None:
    """C4 (coverforge-port.md): plain strings, forward-compat default `[]`
    when a not-yet-updated forge-engines deploy omits the field entirely."""
    body_with_claim_warnings = {
        **_APPLY_RESPONSE_BODY,
        "claim_warnings": ['unsupported claim (contradicted): "Led 20 engineers" -- no match'],
    }
    http = _FakeHttpClient(body=body_with_claim_warnings)

    result = await call_apply(
        http,  # type: ignore[arg-type]
        resume_template=_RESUME_TEMPLATE,
        job_snapshot=_SNAPSHOT,
        credential=_CREDENTIAL,
        now="2026-08-15T00:00:00.000Z",
    )

    assert result.claim_warnings == [
        'unsupported claim (contradicted): "Led 20 engineers" -- no match'
    ]


_GAP_INTERVIEW_ITEMS = [{"cluster_name": "Computer Vision", "bridge_skill": "opencv"}]


async def test_call_gap_interview_returns_empty_without_a_request_when_items_is_empty() -> None:
    http = _FakeHttpClient()

    result = await call_gap_interview(http, items=[], credential=_CREDENTIAL)  # type: ignore[arg-type]

    assert result == []
    assert http.requests == []


async def test_call_gap_interview_sends_the_expected_request_body() -> None:
    http = _FakeHttpClient(body={"questions": []})

    await call_gap_interview(
        http,  # type: ignore[arg-type]
        items=_GAP_INTERVIEW_ITEMS,
        credential=_CREDENTIAL,
    )

    assert len(http.requests) == 1
    url, kwargs = http.requests[0]
    assert url.endswith("/gap-interview")
    body = kwargs["json"]
    assert body["items"] == _GAP_INTERVIEW_ITEMS
    assert body["credential"] == {
        "secret": "sk-or-v1-plaintext",
        "model": "anthropic/claude-sonnet-4-6",
        "base_url": None,
    }


async def test_call_gap_interview_parses_a_successful_response() -> None:
    question_text = "Have you used OpenCV for anything beyond preprocessing?"
    http = _FakeHttpClient(
        body={"questions": [{"cluster_name": "Computer Vision", "question": question_text}]}
    )

    result = await call_gap_interview(
        http,  # type: ignore[arg-type]
        items=_GAP_INTERVIEW_ITEMS,
        credential=_CREDENTIAL,
    )

    assert len(result) == 1
    assert result[0].cluster_name == "Computer Vision"
    assert result[0].question == question_text


async def test_call_gap_interview_handles_a_response_with_no_questions() -> None:
    http = _FakeHttpClient(body={"questions": []})

    result = await call_gap_interview(
        http,  # type: ignore[arg-type]
        items=_GAP_INTERVIEW_ITEMS,
        credential=_CREDENTIAL,
    )

    assert result == []


_GAP_ANSWER_CANDIDATES = [
    {"pointer": "/experience/0", "label": "AI Engineer at Capgemini"},
    {"pointer": "/projects/0", "label": "VectorBench"},
]


async def test_call_gap_answer_draft_sends_the_expected_request_body() -> None:
    http = _FakeHttpClient(
        body={"bullet": "Preprocessed images with OpenCV.", "entity_pointer": "/projects/0"}
    )

    await call_gap_answer_draft(
        http,  # type: ignore[arg-type]
        question="Have you used OpenCV?",
        answer="Yes, a bit.",
        candidates=_GAP_ANSWER_CANDIDATES,
        credential=_CREDENTIAL,
    )

    assert len(http.requests) == 1
    url, kwargs = http.requests[0]
    assert url.endswith("/gap-interview/draft")
    body = kwargs["json"]
    assert body["question"] == "Have you used OpenCV?"
    assert body["answer"] == "Yes, a bit."
    assert body["candidates"] == _GAP_ANSWER_CANDIDATES
    assert body["credential"] == {
        "secret": "sk-or-v1-plaintext",
        "model": "anthropic/claude-sonnet-4-6",
        "base_url": None,
    }


async def test_call_gap_answer_draft_parses_a_successful_response() -> None:
    http = _FakeHttpClient(
        body={"bullet": "Preprocessed images with OpenCV.", "entity_pointer": "/projects/0"}
    )

    result = await call_gap_answer_draft(
        http,  # type: ignore[arg-type]
        question="Have you used OpenCV?",
        answer="Yes, a bit.",
        candidates=_GAP_ANSWER_CANDIDATES,
        credential=_CREDENTIAL,
    )

    assert result.bullet == "Preprocessed images with OpenCV."
    assert result.entity_pointer == "/projects/0"


async def test_call_gap_answer_draft_maps_a_422_to_an_api_error() -> None:
    http = _FakeHttpClient(
        status_code=422, body={"detail": "Couldn't draft a bullet from that answer."}
    )

    with pytest.raises(ApiError) as exc_info:
        await call_gap_answer_draft(
            http,  # type: ignore[arg-type]
            question="Have you used OpenCV?",
            answer="not much",
            candidates=_GAP_ANSWER_CANDIDATES,
            credential=_CREDENTIAL,
        )

    assert "Couldn't draft a bullet" in str(exc_info.value)


_NAME = "Pat Smith"
_PHONE = "+1 201 555 0187"
_EMPLOYER = "Initech Holdings"
_BULLET = "Led the payments migration at Initech Holdings"


def _skewed_engine_answer() -> dict[str, Any]:
    """What FastAPI answers when a required field is missing -- here, after one service was
    deployed ahead of the other: the offending input, the whole request body, rides in it."""
    return {
        "detail": [
            {
                "type": "missing",
                "loc": ["body", "new_required_field"],
                "msg": "Field required",
                "input": {
                    "resume_template": {
                        "personal": {"name": _NAME, "phone": _PHONE},
                        "experience": [{"company": _EMPLOYER, "bullets": [_BULLET]}],
                    },
                    "credential": {"secret": "sk-or-v1-plaintext"},
                },
                "ctx": {"error": f"{_NAME} is not allowed"},
                "url": "https://errors.pydantic.dev/2.9/v/missing",
            }
        ]
    }


async def test_a_validation_422_from_the_engine_reports_where_and_why_never_the_input() -> None:
    http = _FakeHttpClient(status_code=422, body=_skewed_engine_answer())

    with pytest.raises(ApiError) as exc_info:
        await call_apply(
            http,  # type: ignore[arg-type]
            resume_template=_RESUME_TEMPLATE,
            job_snapshot=_SNAPSHOT,
            credential=_CREDENTIAL,
            now="2026-08-15T00:00:00.000Z",
        )

    error = exc_info.value
    assert error.code == "RUN_FAILED"
    assert error.message == (
        "The resume engine rejected this run: body.new_required_field: Field required"
    )
    for leaked in (_NAME, _PHONE, _EMPLOYER, _BULLET, "sk-or-v1-plaintext", "pydantic.dev"):
        assert leaked not in error.message
        assert leaked not in str(error)


async def test_every_engine_call_reports_a_validation_422_the_same_safe_way() -> None:
    http = _FakeHttpClient(status_code=422, body=_skewed_engine_answer())
    calls = [
        resolve_header_chips(
            http,  # type: ignore[arg-type]
            personal={"name": _NAME},
            header_layout=None,
        ),
        call_gap_interview(
            http,  # type: ignore[arg-type]
            items=[{"cluster_name": "Python", "bridge_skill": "Rust"}],
            credential=_CREDENTIAL,
        ),
        call_gap_answer_draft(
            http,  # type: ignore[arg-type]
            question="Have you used Rust?",
            answer=f"{_NAME} wrote some at {_EMPLOYER}",
            candidates=_GAP_ANSWER_CANDIDATES,
            credential=_CREDENTIAL,
        ),
    ]
    for call in calls:
        with pytest.raises(ApiError) as exc_info:
            await call
        assert "body.new_required_field: Field required" in exc_info.value.message
        assert _NAME not in exc_info.value.message
        assert _PHONE not in exc_info.value.message
        assert _EMPLOYER not in exc_info.value.message


async def test_a_validation_list_with_nothing_usable_in_it_is_not_printed() -> None:
    http = _FakeHttpClient(
        status_code=422,
        body={"detail": [{"input": {"name": _NAME}}, {"ctx": {"phone": _PHONE}}]},
    )

    with pytest.raises(ApiError) as exc_info:
        await call_apply(
            http,  # type: ignore[arg-type]
            resume_template=_RESUME_TEMPLATE,
            job_snapshot=_SNAPSHOT,
            credential=_CREDENTIAL,
            now="2026-08-15T00:00:00.000Z",
        )

    assert _NAME not in exc_info.value.message and _PHONE not in exc_info.value.message
    assert exc_info.value.code == "RUN_FAILED"


async def test_call_apply_maps_a_declined_gate_with_no_resume() -> None:
    body = {**_APPLY_RESPONSE_BODY, "resume": None, "ats_attempts": []}
    http = _FakeHttpClient(body=body)

    result = await call_apply(
        http,  # type: ignore[arg-type]
        resume_template=_RESUME_TEMPLATE,
        job_snapshot=_SNAPSHOT,
        credential=_CREDENTIAL,
        now="2026-08-15T00:00:00.000Z",
    )

    assert result.generated is False
    assert result.final_ats is None


@pytest.mark.parametrize("missing", ["fit", "gate"])
async def test_call_apply_rejects_a_remote_response_without_the_fit_read_or_the_gate(
    missing: str,
) -> None:
    """The separate service always sends both. A reply without one is a break of its contract
    and must fail loudly, not be stored as an unscored resume as if the built-in engine (which
    has neither) had written it."""
    body = {key: value for key, value in _APPLY_RESPONSE_BODY.items() if key != missing}
    http = _FakeHttpClient(body=body)

    with pytest.raises(ValidationError) as raised:
        await call_apply(
            http,  # type: ignore[arg-type]
            resume_template=_RESUME_TEMPLATE,
            job_snapshot=_SNAPSHOT,
            credential=_CREDENTIAL,
            now="2026-08-15T00:00:00.000Z",
        )

    assert missing in str(raised.value)


async def test_call_apply_raises_provider_unavailable_on_connection_failure() -> None:
    http = _FakeHttpClient(raise_error=True)

    with pytest.raises(ApiError) as exc_info:
        await call_apply(
            http,  # type: ignore[arg-type]
            resume_template=_RESUME_TEMPLATE,
            job_snapshot=_SNAPSHOT,
            credential=_CREDENTIAL,
            now="2026-08-15T00:00:00.000Z",
        )

    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"
    assert exc_info.value.retryable is True


async def test_call_apply_raises_provider_unavailable_on_a_5xx() -> None:
    http = _FakeHttpClient(status_code=503, body={"error": "boom"})

    with pytest.raises(ApiError) as exc_info:
        await call_apply(
            http,  # type: ignore[arg-type]
            resume_template=_RESUME_TEMPLATE,
            job_snapshot=_SNAPSHOT,
            credential=_CREDENTIAL,
            now="2026-08-15T00:00:00.000Z",
        )

    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"


async def test_call_apply_raises_run_failed_on_a_422() -> None:
    http = _FakeHttpClient(
        status_code=422, body={"error": "IngestError", "message": "bad template"}
    )

    with pytest.raises(ApiError) as exc_info:
        await call_apply(
            http,  # type: ignore[arg-type]
            resume_template=_RESUME_TEMPLATE,
            job_snapshot=_SNAPSHOT,
            credential=_CREDENTIAL,
            now="2026-08-15T00:00:00.000Z",
        )

    assert exc_info.value.code == "RUN_FAILED"
    assert exc_info.value.retryable is False


async def test_resolve_header_chips_sends_personal_and_layout() -> None:
    http = _FakeHttpClient(body={"chips": [{"field": "email", "text": "j@x.com", "href": None}]})
    personal = {"name": "Jordan Rivera", "email": "j@x.com"}
    layout = {"chips": [{"field": "email"}]}

    result = await resolve_header_chips(
        http,  # type: ignore[arg-type]
        personal=personal,
        header_layout=layout,
    )

    assert result == [{"field": "email", "text": "j@x.com", "href": None}]
    url, kwargs = http.requests[0]
    assert url.endswith("/header/resolve")
    assert kwargs["json"] == {"personal": personal, "header_layout": layout}


async def test_resolve_header_chips_raises_provider_unavailable_on_connection_failure() -> None:
    http = _FakeHttpClient(raise_error=True)

    with pytest.raises(ApiError) as exc_info:
        await resolve_header_chips(
            http,  # type: ignore[arg-type]
            personal={"name": "Jordan Rivera"},
            header_layout=None,
        )

    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"


# ── R5: shape_report / pin-conflict warnings ────────────────────────────────

_GATE = GateInfo(outcome="proceed")
_FIT = ForgeFitResult(overall_score=8.0)


def test_shape_warnings_empty_when_no_shape_report() -> None:
    result = ForgeApplyResult(resume=None, regenerated=False, gate=_GATE, fit=_FIT)
    assert result.shape_warnings == []


def test_shape_warnings_reads_the_nested_warnings_list() -> None:
    result = ForgeApplyResult(
        resume=None,
        regenerated=False,
        gate=_GATE,
        fit=_FIT,
        shape_report={"warnings": ["pinned item e1 requested 3 bullets but only 1 fit"]},
    )
    assert result.shape_warnings == ["pinned item e1 requested 3 bullets but only 1 fit"]


def test_shape_warnings_empty_when_shape_report_has_no_warnings_key() -> None:
    result = ForgeApplyResult(resume=None, regenerated=False, gate=_GATE, fit=_FIT, shape_report={})
    assert result.shape_warnings == []


# ── R7: validate_resume violations ───────────────────────────────────────────


def test_violation_messages_empty_when_no_violations() -> None:
    result = ForgeApplyResult(resume=None, regenerated=False, gate=_GATE, fit=_FIT)
    assert result.violation_messages == []


def test_violation_messages_formats_each_violation_with_a_validation_prefix() -> None:
    result = ForgeApplyResult(
        resume=None,
        regenerated=False,
        fit=_FIT,
        gate=_GATE,
        violations=[
            {
                "code": "entry_not_in_source",
                "message": "experience entry at 'Fake LLC' has no matching company",
                "regenerable": False,
            }
        ],
    )
    assert result.violation_messages == [
        "validation: experience entry at 'Fake LLC' has no matching company"
    ]


async def test_call_apply_sends_the_saved_header_layout_when_given() -> None:
    http = _FakeHttpClient()
    layout = {"chips": [{"field": "github"}, {"field": "email"}], "separator": "dot"}

    await call_apply(
        http,  # type: ignore[arg-type]
        resume_template=_RESUME_TEMPLATE,
        job_snapshot=_SNAPSHOT,
        credential=_CREDENTIAL,
        now="2026-08-15T00:00:00.000Z",
        header_layout=layout,
    )

    assert http.requests[0][1]["json"]["header_layout"] == layout
