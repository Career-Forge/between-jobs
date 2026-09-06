"""Tests for the Gmail reply/status classifier (outreach-v2-search-
first.md's Gmail reply/status parsing, R2) -- the deterministic
re-grounding of the LLM's classification, the anti-fabrication check on
evidence_spans, and the never-fails "unknown" fallback.
"""

from __future__ import annotations

import json
import math
from typing import Any

from between_jobs.api.application_status_classifier import (
    _CLASSIFIER_SYSTEM_PROMPT,
    AUTO_TRACK_THRESHOLD,
    classify_reply,
)
from between_jobs.api.llm_client import LLMResponse

_REPLY_TEXT = "Thanks for reaching out! We'd love to schedule a call this week to discuss the role."


def _response(payload: dict[str, Any]) -> LLMResponse:
    return LLMResponse(content=json.dumps(payload))


async def test_classify_reply_returns_a_grounded_proposal() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return _response(
            {
                "proposed_type": "interview.requested",
                "confidence": 0.9,
                "evidence_spans": ["We'd love to schedule a call this week"],
            }
        )

    result = await classify_reply(
        reply_body_text=_REPLY_TEXT,
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["proposed_type"] == "interview.requested"
    assert result["confidence"] == 0.9
    assert result["evidence_spans"] == ["We'd love to schedule a call this week"]


async def test_classify_reply_sends_the_company_role_and_reply_text() -> None:
    captured: dict[str, Any] = {}

    async def fake_generate(**kwargs: Any) -> LLMResponse:
        captured.update(kwargs)
        return _response({"proposed_type": "unknown", "confidence": 0.0, "evidence_spans": []})

    await classify_reply(
        reply_body_text=_REPLY_TEXT,
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert "Acme" in captured["user_prompt"]
    assert "Staff Engineer" in captured["user_prompt"]
    assert _REPLY_TEXT in captured["user_prompt"]


async def test_classify_reply_forwards_every_parameter_to_generate_exactly() -> None:
    """Strengthens the substring-only check above with an exact match on
    every field `generate` receives -- a stricter regression guard than
    "these strings appear somewhere in user_prompt.\""""
    captured: dict[str, Any] = {}

    async def fake_generate(**kwargs: Any) -> LLMResponse:
        captured.update(kwargs)
        return _response({"proposed_type": "unknown", "confidence": 0.0, "evidence_spans": []})

    await classify_reply(
        reply_body_text=_REPLY_TEXT,
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key-value",
        llm_model="model-value",
        llm_base_url="https://example.test/v1",
        generate=fake_generate,
    )

    assert captured == {
        "api_key": "key-value",
        "model": "model-value",
        "base_url": "https://example.test/v1",
        "system_prompt": _CLASSIFIER_SYSTEM_PROMPT,
        "user_prompt": f"Company: Acme\nRole: Staff Engineer\n\nReply text:\n{_REPLY_TEXT}",
        "max_tokens": 400,
    }


async def test_classify_reply_rejects_a_fabricated_evidence_span() -> None:
    """A single quote that isn't a real, exact substring of the reply
    text disqualifies the WHOLE proposal -- the same "no partial trust"
    stance positioning_brief._parse_brief and outreach_writer's own
    hook-grounding already take."""

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return _response(
            {
                "proposed_type": "offer.received",
                "confidence": 0.95,
                "evidence_spans": ["We are thrilled to extend you an offer"],
            }
        )

    result = await classify_reply(
        reply_body_text=_REPLY_TEXT,
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["proposed_type"] == "unknown"
    assert result["confidence"] == 0.0
    assert result["evidence_spans"] == []


async def test_classify_reply_rejects_a_mix_of_real_and_fabricated_spans() -> None:
    """One bad span among otherwise-real ones still disqualifies the
    whole proposal -- never silently keeps the ones that verify."""

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return _response(
            {
                "proposed_type": "interview.requested",
                "confidence": 0.8,
                "evidence_spans": [
                    "We'd love to schedule a call this week",
                    "specifically next Tuesday at 3pm",
                ],
            }
        )

    result = await classify_reply(
        reply_body_text=_REPLY_TEXT,
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["proposed_type"] == "unknown"


async def test_classify_reply_rejects_an_empty_evidence_spans_list() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return _response(
            {"proposed_type": "recruiter.replied", "confidence": 0.7, "evidence_spans": []}
        )

    result = await classify_reply(
        reply_body_text=_REPLY_TEXT,
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["proposed_type"] == "unknown"


async def test_classify_reply_rejects_a_non_list_evidence_spans_value() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return _response(
            {
                "proposed_type": "interview.requested",
                "confidence": 0.9,
                "evidence_spans": "We'd love to schedule a call this week",
            }
        )

    result = await classify_reply(
        reply_body_text=_REPLY_TEXT,
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["proposed_type"] == "unknown"


async def test_classify_reply_drops_non_string_entries_from_evidence_spans() -> None:
    """A real span survives alongside garbage entries in the same list --
    those entries are filtered out, not fabricated quotes needing the
    whole-proposal disqualification `_parse_status_proposal` uses for a
    bad STRING span."""

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return _response(
            {
                "proposed_type": "interview.requested",
                "confidence": 0.9,
                "evidence_spans": ["We'd love to schedule a call this week", 42, None, True],
            }
        )

    result = await classify_reply(
        reply_body_text=_REPLY_TEXT,
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["proposed_type"] == "interview.requested"
    assert result["evidence_spans"] == ["We'd love to schedule a call this week"]


async def test_classify_reply_rejects_a_whitespace_only_evidence_span() -> None:
    """Regression guard for a real, adversarially-reproduced bug: a
    single space is a non-empty string, so the original filter
    (`isinstance(s, str) and s`) let it through, and a lone space is
    trivially a substring of almost any real multi-word reply --
    defeating the anti-fabrication check entirely."""

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return _response(
            {
                "proposed_type": "offer.received",
                "confidence": 0.95,
                "evidence_spans": ["     "],
            }
        )

    result = await classify_reply(
        reply_body_text=_REPLY_TEXT,
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["proposed_type"] == "unknown"


async def test_classify_reply_verifies_a_typographically_normalized_quote() -> None:
    """An LLM commonly normalizes a real reply's curly punctuation to
    plain ASCII even when told to quote verbatim. The reply here uses a
    real curly apostrophe and em dash; the "quoted" span uses their
    plain-ASCII equivalents -- this must still verify, not be treated as
    a fabricated span."""
    reply_text = (
        "We\u2019re thrilled to extend an offer \u2014 welcome aboard!"  # curly quote, em dash
    )

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return _response(
            {
                "proposed_type": "offer.received",
                "confidence": 0.9,
                "evidence_spans": ["We're thrilled to extend an offer - welcome aboard!"],
            }
        )

    result = await classify_reply(
        reply_body_text=reply_text,
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["proposed_type"] == "offer.received"


async def test_classify_reply_rejects_a_missing_proposed_type_key() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return _response(
            {
                "confidence": 0.9,
                "evidence_spans": ["We'd love to schedule a call this week"],
            }
        )

    result = await classify_reply(
        reply_body_text=_REPLY_TEXT,
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["proposed_type"] == "unknown"


async def test_classify_reply_rejects_an_unrecognized_proposed_type() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return _response(
            {
                "proposed_type": "candidate.interested",  # not a real taxonomy member
                "confidence": 0.9,
                "evidence_spans": ["We'd love to schedule a call this week"],
            }
        )

    result = await classify_reply(
        reply_body_text=_REPLY_TEXT,
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["proposed_type"] == "unknown"


async def test_classify_reply_clamps_an_out_of_range_confidence() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return _response(
            {
                "proposed_type": "interview.requested",
                "confidence": 1.5,
                "evidence_spans": ["We'd love to schedule a call this week"],
            }
        )

    result = await classify_reply(
        reply_body_text=_REPLY_TEXT,
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["confidence"] == 1.0


async def test_classify_reply_clamps_a_negative_confidence() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return _response(
            {
                "proposed_type": "interview.requested",
                "confidence": -0.5,
                "evidence_spans": ["We'd love to schedule a call this week"],
            }
        )

    result = await classify_reply(
        reply_body_text=_REPLY_TEXT,
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["confidence"] == 0.0


async def test_classify_reply_rejects_a_boolean_confidence() -> None:
    """Regression guard for a real, adversarially-reproduced bug: bool is
    a subclass of int in Python, so `isinstance(True, int | float)` is
    True and `float(True) == 1.0` -- a bare `true` confidence silently
    coerced to full confidence under the original check."""

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return _response(
            {
                "proposed_type": "interview.requested",
                "confidence": True,
                "evidence_spans": ["We'd love to schedule a call this week"],
            }
        )

    result = await classify_reply(
        reply_body_text=_REPLY_TEXT,
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["confidence"] == 0.0


async def test_classify_reply_rejects_a_nan_confidence() -> None:
    """Regression guard for a real, adversarially-reproduced bug: any
    comparison against NaN is False, so Python's own `max(0.0, min(1.0,
    nan))` returns 1.0 instead of clamping it to a safe value -- the
    original clamp silently let a NaN confidence through as full
    confidence."""

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        payload = {
            "proposed_type": "interview.requested",
            "confidence": math.nan,
            "evidence_spans": ["We'd love to schedule a call this week"],
        }
        return LLMResponse(content=json.dumps(payload))

    result = await classify_reply(
        reply_body_text=_REPLY_TEXT,
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["confidence"] == 0.0


async def test_classify_reply_rejects_an_infinite_confidence() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        payload = {
            "proposed_type": "interview.requested",
            "confidence": math.inf,
            "evidence_spans": ["We'd love to schedule a call this week"],
        }
        return LLMResponse(content=json.dumps(payload))

    result = await classify_reply(
        reply_body_text=_REPLY_TEXT,
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["confidence"] == 0.0


async def test_classify_reply_defaults_a_missing_confidence_key_to_zero() -> None:
    """Documents the real, asymmetric behavior: a missing confidence
    still yields a real, grounded proposed_type/evidence_spans -- only
    confidence itself defaults to 0.0, since "no confidence given" and
    "confidence explicitly 0" aren't distinguished by this schema."""

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return _response(
            {
                "proposed_type": "interview.requested",
                "evidence_spans": ["We'd love to schedule a call this week"],
            }
        )

    result = await classify_reply(
        reply_body_text=_REPLY_TEXT,
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["proposed_type"] == "interview.requested"
    assert result["confidence"] == 0.0


async def test_classify_reply_handles_malformed_json() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content="not json at all")

    result = await classify_reply(
        reply_body_text=_REPLY_TEXT,
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["proposed_type"] == "unknown"


async def test_classify_reply_handles_a_non_object_json_value() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps(["not", "an", "object"]))

    result = await classify_reply(
        reply_body_text=_REPLY_TEXT,
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["proposed_type"] == "unknown"


async def test_classify_reply_strips_markdown_code_fences() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        payload = json.dumps(
            {
                "proposed_type": "interview.requested",
                "confidence": 0.9,
                "evidence_spans": ["We'd love to schedule a call this week"],
            }
        )
        return LLMResponse(content=f"```json\n{payload}\n```")

    result = await classify_reply(
        reply_body_text=_REPLY_TEXT,
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["proposed_type"] == "interview.requested"


async def test_classify_reply_strips_a_bare_markdown_fence_without_a_json_tag() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        payload = json.dumps(
            {
                "proposed_type": "interview.requested",
                "confidence": 0.9,
                "evidence_spans": ["We'd love to schedule a call this week"],
            }
        )
        return LLMResponse(content=f"```\n{payload}\n```")

    result = await classify_reply(
        reply_body_text=_REPLY_TEXT,
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["proposed_type"] == "interview.requested"


async def test_classify_reply_returns_unknown_without_calling_the_llm_for_empty_text() -> None:
    calls = []

    async def fake_generate(**kwargs: Any) -> LLMResponse:
        calls.append(kwargs)
        return _response({"proposed_type": "unknown", "confidence": 0.0, "evidence_spans": []})

    result = await classify_reply(
        reply_body_text="   ",
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["proposed_type"] == "unknown"
    assert calls == []


async def test_classify_reply_skips_the_llm_for_invisible_chars_only_text() -> None:
    """Regression guard for a real, adversarially-reproduced bug: zero-
    width space/joiners and a stray BOM are Unicode format characters,
    not whitespace -- Python's own str.strip() doesn't remove them, so a
    reply body made up solely of these survived the original blank-text
    short-circuit as "non-empty," wasting a real LLM call on content
    with nothing to classify."""
    calls = []

    async def fake_generate(**kwargs: Any) -> LLMResponse:
        calls.append(kwargs)
        return _response({"proposed_type": "unknown", "confidence": 0.0, "evidence_spans": []})

    result = await classify_reply(
        reply_body_text="\u200b\u200c\u200d\ufeff",  # ZWSP, ZWNJ, ZWJ, BOM
        company="Acme",
        role_title="Staff Engineer",
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["proposed_type"] == "unknown"
    assert calls == []


def test_auto_track_threshold_is_exactly_0_85() -> None:
    """Strengthens the range-only sanity check below -- a review flagged
    that a lower threshold (still `<= 1.0` and `> 0.0`) would silently
    pass this same assertion despite changing real auto-apply behavior."""
    assert AUTO_TRACK_THRESHOLD == 0.85


def test_auto_track_threshold_is_a_real_probability() -> None:
    assert 0.0 < AUTO_TRACK_THRESHOLD <= 1.0
