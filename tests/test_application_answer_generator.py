"""Tests for the LLM-drafted-answer engine (browser-extension.md E3b)."""

from __future__ import annotations

import json
from typing import Any

from between_jobs.api.application_answer_generator import (
    _ANSWER_GENERATION_SYSTEM_PROMPT,
    AnswerVerification,
    flagged_answer_warnings,
    generate_answer,
    is_generation_eligible,
    verify_answer_claims,
)
from between_jobs.api.llm_client import LLMResponse

_LONG_DISCLAIMER = (
    "Upon successful completion of the hiring process, Company may extend an offer of "
    "employment. Where permitted by applicable law, Company reserves the right to conduct "
    "background check investigations and/or reference checks. Company also performs a "
    "one-time visual verification to confirm that the individual accepting employment or a "
    "contractor role reasonably matches a valid government-issued photo ID (e.g., passport, "
    "national ID card, or driver's license). Any offer is contingent upon satisfactory "
    "completion of any background investigation, reference check, and/or identity "
    "verification. Subject to applicable law, Company may rescind the offer based on the "
    "results of such checks, an applicant's refusal to participate, or any attempts to "
    "interfere with the process."
)


def test_is_generation_eligible_accepts_a_real_short_question() -> None:
    assert is_generation_eligible("Why do you want to work here?") is True


def test_is_generation_eligible_rejects_a_real_disclaimer_paragraph() -> None:
    """Regression guard against the exact real Sysdig content that
    prompted this filter's existence."""
    assert is_generation_eligible(_LONG_DISCLAIMER) is False


def test_is_generation_eligible_rejects_empty_or_whitespace() -> None:
    assert is_generation_eligible("") is False
    assert is_generation_eligible("   ") is False


def test_is_generation_eligible_boundary_is_inclusive() -> None:
    exactly_400 = "x" * 400
    assert is_generation_eligible(exactly_400) is True
    assert is_generation_eligible(exactly_400 + "x") is False


async def test_generate_answer_returns_a_parsed_answer() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                {
                    "answer_text": "I'm drawn to this role given my Python and ML background.",
                    "declined_reason": None,
                }
            )
        )

    result = await generate_answer(
        question_text="Why do you want to work here?",
        profile_summary="CANDIDATE: Jane Doe\nEXPERIENCE:\nML Engineer @ Acme",
        job_description="We build ML systems in Python.",
        llm_api_key="sk-test",
        llm_model="test-model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["answer_text"] is not None
    assert "Python" in result["answer_text"]
    assert result["declined_reason"] is None


async def test_generate_answer_honors_a_model_decline() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                {
                    "answer_text": None,
                    "declined_reason": "This is a consent statement, not a question.",
                }
            )
        )

    result = await generate_answer(
        question_text=_LONG_DISCLAIMER[:400],
        profile_summary="",
        job_description="",
        llm_api_key="sk-test",
        llm_model="test-model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["answer_text"] is None
    assert result["declined_reason"] == "This is a consent statement, not a question."


async def test_generate_answer_handles_a_code_fenced_response() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content="```json\n"
            + json.dumps({"answer_text": "A short answer.", "declined_reason": None})
            + "\n```"
        )

    result = await generate_answer(
        question_text="Describe yourself.",
        profile_summary="",
        job_description="",
        llm_api_key="sk-test",
        llm_model="test-model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["answer_text"] == "A short answer."


async def test_generate_answer_treats_a_garbled_response_as_an_unknown_decline() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content="not json at all")

    result = await generate_answer(
        question_text="Why do you want to work here?",
        profile_summary="",
        job_description="",
        llm_api_key="sk-test",
        llm_model="test-model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert result["answer_text"] is None
    assert result["declined_reason"] is None


async def test_verify_answer_claims_parses_a_full_verification() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                {
                    "claims": [
                        {
                            "claim": "led a team of 5",
                            "verdict": "grounded",
                            "reason": "matches facts",
                        },
                        {
                            "claim": "invented a new algorithm",
                            "verdict": "contradicted",
                            "reason": "not in facts",
                        },
                    ]
                }
            )
        )

    verification = await verify_answer_claims(
        answer_text="I led a team of 5 and invented a new algorithm.",
        profile_summary="Led a team of 5.",
        job_description="",
        llm_api_key="sk-test",
        llm_model="test-model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert len(verification["claims"]) == 2
    assert verification["claims"][0]["verdict"] == "grounded"
    assert verification["claims"][1]["verdict"] == "contradicted"


async def test_verify_answer_claims_downgrades_an_invalid_verdict_to_unverifiable() -> None:
    """Same "unknown labeled, never guessed" precedent C4's own parser
    already established -- an unparseable verdict is kept and downgraded,
    not silently dropped."""

    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(
                {"claims": [{"claim": "something", "verdict": "not_a_real_verdict"}]}
            )
        )

    verification = await verify_answer_claims(
        answer_text="something",
        profile_summary="",
        job_description="",
        llm_api_key="sk-test",
        llm_model="test-model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert verification["claims"][0]["verdict"] == "unverifiable"


async def test_verify_answer_claims_fails_open_on_a_garbled_response() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content="garbage")

    verification = await verify_answer_claims(
        answer_text="anything",
        profile_summary="",
        job_description="",
        llm_api_key="sk-test",
        llm_model="test-model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert verification["claims"] == []


def test_flagged_answer_warnings_only_surfaces_non_grounded_claims() -> None:
    verification: AnswerVerification = {
        "claims": [
            {"claim": "led a team", "verdict": "grounded", "reason": "matches"},
            {"claim": "invented X", "verdict": "contradicted", "reason": "not in facts"},
            {"claim": "worked with Y", "verdict": "unverifiable", "reason": "not found either way"},
        ]
    }

    warnings = flagged_answer_warnings(verification)

    assert len(warnings) == 2
    assert all("led a team" not in w for w in warnings)
    assert any("invented X" in w and "contradicted" in w for w in warnings)
    assert any("worked with Y" in w and "unverifiable" in w for w in warnings)


# Regression coverage for work-authorization-status.md's real incident: E3b's
# LLM drafted "not currently legally authorized to work in the United States"
# by extrapolating from `work_authorization: "Indian Citizen"` alone -- a
# citizenship fact, not a work-authorization one. A scripted `fake_generate`
# can't prove a live model actually complies (that needs a real LLM call,
# out of scope for this mocked suite, same as every other test in this
# file) -- what these two tests DO prove: (1) the anti-extrapolation rule
# text actually reaches the system prompt on this exact incident shape, not
# just somewhere in the module, and (2) the intended hedge/decline output
# threads through `generate_answer` correctly rather than being dropped or
# reinterpreted, contrasted with a well-guided fact still producing a full,
# grounded, non-hedged answer -- proving the hardening isn't over-cautious.

_US_AUTHORIZATION_QUESTION = (
    "Are you legally authorized to work in the United States? Will you now or in "
    "the future require sponsorship for employment visa status?"
)


def test_generation_prompt_forbids_inferring_authorization_from_citizenship() -> None:
    """The hardening rule itself must actually be present and specific --
    not just "be careful," but the exact citizenship-is-not-authorization
    distinction the real incident hinged on."""
    assert "NOT a work-authorization fact" in _ANSWER_GENERATION_SYSTEM_PROMPT
    assert "never infer one from the other" in _ANSWER_GENERATION_SYSTEM_PROMPT
    assert "citizenship" in _ANSWER_GENERATION_SYSTEM_PROMPT.lower()


def test_generation_prompt_prefers_the_candidates_own_terminology_over_the_questions() -> None:
    """Region-agnostic hardening (F3): the prompt's own illustrative
    examples in rule 1 are US-shaped ("authorized to work," "will require
    sponsorship"), but the plan doc this feature ships from spends real
    effort establishing that vocabulary is NOT interchangeable across
    regions (Canada never uses "sponsorship" for employment immigration
    the way the US does). Without an explicit instruction, a US-templated
    screening question reaching a non-US candidate (the plan doc's own
    documented Cloudflare-Bangalore pattern) could otherwise get answered
    back in the question's own mismatched vocabulary rather than the
    candidate's."""
    assert "not interchangeable across countries" in _ANSWER_GENERATION_SYSTEM_PROMPT
    assert "CANDIDATE's own terms" in _ANSWER_GENERATION_SYSTEM_PROMPT


async def test_generate_answer_hedges_on_a_citizenship_only_self_report() -> None:
    """Reproduces the real incident's shape: a US-style two-part
    authorization/sponsorship question against a candidate whose stated
    `work_authorization` is only a citizenship fact ("Indian Citizen"). A
    decline (or a hedge) is the correct output here -- an asserted "I am/am
    not authorized" conclusion grounded only in citizenship is exactly what
    caused the real incident."""
    captured: dict[str, Any] = {}

    async def fake_generate(**kwargs: Any) -> LLMResponse:
        captured.update(kwargs)
        return LLMResponse(
            content=json.dumps(
                {
                    "answer_text": None,
                    "declined_reason": (
                        "The candidate's stated facts only give citizenship "
                        "(Indian Citizen), not a specific work-authorization or "
                        "visa status, so this can't be answered accurately."
                    ),
                }
            )
        )

    result = await generate_answer(
        question_text=_US_AUTHORIZATION_QUESTION,
        profile_summary="CANDIDATE: Priya Raman\nWORK AUTHORIZATION: Indian Citizen",
        job_description="We are hiring a backend engineer for our US office.",
        llm_api_key="sk-test",
        llm_model="test-model",
        llm_base_url=None,
        generate=fake_generate,
    )

    # Load-bearing, not identity-by-construction: comparing captured[...]
    # against the SAME mutable global it was read from is true no matter
    # what that global currently contains (confirmed by mutation repro --
    # deleting the hardening block from the live prompt still "passed"
    # that comparison). Asserting the actual hardening TEXT reached this
    # call is what makes a future accidental deletion of the rule fail
    # this test directly.
    assert "NOT a work-authorization fact" in captured["system_prompt"]
    assert "never infer one from the other" in captured["system_prompt"]

    assert result["answer_text"] is None
    assert result["declined_reason"] is not None
    assert "citizenship" in result["declined_reason"].lower()


async def test_generate_answer_well_guided_fact_still_produces_a_grounded_answer() -> None:
    """The hardening must not make the prompt over-cautious. Uses one of
    work-authorization-status.md's own real, researched region examples
    (US) verbatim as the candidate's self-report -- a well-guided answer
    should still produce a full, grounded, non-hedged draft."""
    captured: dict[str, Any] = {}
    guided_fact = (
        "I'm on F-1 OPT authorization and will need H-1B sponsorship to "
        "continue working in the US after it expires."
    )

    async def fake_generate(**kwargs: Any) -> LLMResponse:
        captured.update(kwargs)
        return LLMResponse(
            content=json.dumps(
                {
                    "answer_text": (
                        "I'm currently on F-1 OPT authorization and will need "
                        "H-1B sponsorship to continue working in the US after "
                        "it expires."
                    ),
                    "declined_reason": None,
                }
            )
        )

    result = await generate_answer(
        question_text=_US_AUTHORIZATION_QUESTION,
        profile_summary=f"CANDIDATE: Priya Raman\nWORK AUTHORIZATION: {guided_fact}",
        job_description="We are hiring a backend engineer for our US office.",
        llm_api_key="sk-test",
        llm_model="test-model",
        llm_base_url=None,
        generate=fake_generate,
    )

    # Same load-bearing content check as the hedging test above, on the
    # SAME call -- proves the hardening reaches the model here too, not
    # just that this test happens to share a global with the module.
    assert "NOT a work-authorization fact" in captured["system_prompt"]
    assert "never infer one from the other" in captured["system_prompt"]
    assert result["declined_reason"] is None
    assert result["answer_text"] is not None
    assert "OPT" in result["answer_text"]
    assert "H-1B" in result["answer_text"]
