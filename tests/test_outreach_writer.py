"""Tests for OutreachWriter (outreach-contactfinder.md Phase E) -- the
single-hook selection, the deterministic rubric check, and the
capped-at-2-attempts retry-on-rubric-failure loop.
"""

from __future__ import annotations

import json
from typing import Any

from between_jobs.api.llm_client import LLMResponse
from between_jobs.api.outreach_writer import (
    OutreachDraft,
    build_hook_context,
    check_rubric,
    generate_outreach_draft,
)

_EVIDENCE_LOW = {
    "id": "ev-1",
    "source_title": "Jane Doe -- engineer",
    "source_snippet": "A weak, inferred mention.",
    "confidence": "inferred",
    "observed_at": "2026-01-01T00:00:00Z",
}
_EVIDENCE_HIGH = {
    "id": "ev-2",
    "source_title": "Jane Doe presents at PyData",
    "source_snippet": "Jane Doe gave a talk on scaling ML infra at PyData NYC.",
    "confidence": "verified",
    "observed_at": "2026-08-01T00:00:00Z",
}


def test_build_hook_context_returns_empty_with_no_evidence() -> None:
    hook_text, hook_evidence_id = build_hook_context([])
    assert hook_text == ""
    assert hook_evidence_id is None


def test_build_hook_context_picks_the_highest_confidence_evidence() -> None:
    hook_text, hook_evidence_id = build_hook_context([_EVIDENCE_LOW, _EVIDENCE_HIGH])
    assert hook_evidence_id == "ev-2"
    assert "PyData" in hook_text


def _draft(**overrides: Any) -> OutreachDraft:
    base = OutreachDraft(
        subject="Loved your PyData talk",
        email_body="Saw your PyData talk on scaling ML infra -- would love to chat about the role.",
        linkedin_message="Saw your PyData talk on scaling ML infra -- would love to connect.",
        follow_up_message="Following up in case my last note got buried!",
        hook_evidence_id="ev-2",
    )
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def test_check_rubric_passes_a_clean_draft() -> None:
    assert check_rubric(_draft()) == []


def test_check_rubric_flags_a_banned_opener() -> None:
    warnings = check_rubric(
        _draft(email_body="I hope this email finds you well. Saw your PyData talk.")
    )
    assert any("banned" in w for w in warnings)


def test_check_rubric_flags_an_oversized_linkedin_message() -> None:
    warnings = check_rubric(_draft(linkedin_message="x" * 301))
    assert any("300 characters" in w for w in warnings)


async def test_generate_outreach_draft_refuses_with_no_evidence() -> None:
    draft, warnings = await generate_outreach_draft(
        person_name="Jane Doe",
        claimed_title="Engineer",
        company="Acme",
        role_title="Staff Engineer",
        evidence=[],
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
    )
    assert draft is None
    assert "no grounded evidence" in warnings[0]


async def test_generate_outreach_draft_succeeds_on_first_attempt() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps(dict(_draft())))

    draft, warnings = await generate_outreach_draft(
        person_name="Jane Doe",
        claimed_title="Engineer",
        company="Acme",
        role_title="Staff Engineer",
        evidence=[_EVIDENCE_HIGH],
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )
    assert draft is not None
    assert warnings == []
    assert draft["hook_evidence_id"] == "ev-2"


async def test_generate_outreach_draft_retries_once_on_rubric_failure_then_succeeds() -> None:
    calls = 0

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return LLMResponse(
                content=json.dumps(dict(_draft(email_body="I hope this email finds you well.")))
            )
        return LLMResponse(content=json.dumps(dict(_draft())))

    draft, warnings = await generate_outreach_draft(
        person_name="Jane Doe",
        claimed_title="Engineer",
        company="Acme",
        role_title="Staff Engineer",
        evidence=[_EVIDENCE_HIGH],
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )
    assert calls == 2
    assert draft is not None
    assert warnings == []


async def test_generate_outreach_draft_caps_retries_at_two_attempts() -> None:
    calls = 0

    async def always_bad_generate(**_kwargs: Any) -> LLMResponse:
        nonlocal calls
        calls += 1
        return LLMResponse(
            content=json.dumps(dict(_draft(email_body="I hope this email finds you well.")))
        )

    draft, warnings = await generate_outreach_draft(
        person_name="Jane Doe",
        claimed_title="Engineer",
        company="Acme",
        role_title="Staff Engineer",
        evidence=[_EVIDENCE_HIGH],
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=always_bad_generate,
    )
    assert calls == 2  # never more than max_attempts
    assert draft is not None  # still returns the best-effort draft
    assert warnings != []  # but honestly flags it didn't pass the rubric


async def test_generate_outreach_draft_handles_malformed_json_across_all_attempts() -> None:
    async def broken_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content="not json")

    draft, warnings = await generate_outreach_draft(
        person_name="Jane Doe",
        claimed_title="Engineer",
        company="Acme",
        role_title="Staff Engineer",
        evidence=[_EVIDENCE_HIGH],
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=broken_generate,
    )
    assert draft is None
    assert warnings == ["draft could not be parsed"]
