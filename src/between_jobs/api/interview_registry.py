"""InterviewForge R1 (interviewforge-v1.md) -- turns the `interview_process`
claims `company_intel_pipeline.py` already produces into a structured,
shared, cross-user registry entry.

Deliberately NOT a second research pass: this takes only claims that already
passed `_parse_claims`' real-URL check (see `company_intel_pipeline.py`'s own
anti-fabrication backstop) and restructures them -- it can never introduce a
fact that pass didn't already verify, because it never sees raw search
evidence at all, only claims that already cleared that bar. No new provider
calls, no new citation-checking logic to get right; reuse of a mechanism
already live in production (D4, interviewforge-v1.md).

Scope, named honestly: keyed on `company_name` alone, not the fuller
`(company, role_family, level)` shape Proposal §28.1 describes -- the same
"real, stated scope cut, not silently guessed at" `company_intel_pipeline.
py`'s own `RoleFingerprint` docstring already accepts for the identical
reason (role_family/level extraction needs an LLM call or a keyword taxonomy
this codebase doesn't have yet). Grows to the fuller key once that
taxonomy is real infrastructure.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Literal, NotRequired, TypedDict

from .company_intel_pipeline import Claim
from .llm_client import LLMResponse
from .llm_client import generate as llm_generate

LlmGenerate = Callable[..., Awaitable[LLMResponse]]

_INTERVIEW_PROCESS_CATEGORY = "interview_process"

DifficultySignal = Literal["low", "medium", "high", "unknown"]
_VALID_DIFFICULTY: frozenset[str] = frozenset(("low", "medium", "high", "unknown"))


class InterviewRound(TypedDict):
    name: str
    format: NotRequired[str]
    focus: NotRequired[str]


class InterviewProcessModel(TypedDict):
    company_name: str
    rounds: list[InterviewRound]
    typical_topics: list[str]
    difficulty_signal: DifficultySignal
    values_signals: list[str]
    confidence: str
    """Mirrors `Claim.confidence` ("high"/"medium"/"low") -- the LOWEST
    confidence among the claims this model was built from, not a fresh LLM
    judgment call. Computed by `synthesize_interview_process_model`, never
    trusted from the model's own output -- see that function's docstring."""


_SYNTHESIS_SYSTEM_PROMPT = """You structure ALREADY-VERIFIED, cited facts about a company's interview process into one coherent process model. Every fact given to you has already been confirmed to come from a real source -- you are ONLY reorganizing what's given, never adding anything not present in the facts below.

Return ONLY valid JSON (no markdown, no explanations) with this schema:
{
  "rounds": [ { "name": "<e.g. 'Recruiter screen', 'Technical interview', 'Onsite panel'>", "format": "<e.g. '30 min phone call', 'live coding', 'panel of 3', or empty string if not stated>", "focus": "<what this round evaluates, or empty string if not stated>" } ],
  "typical_topics": ["<specific topics/skills mentioned as commonly assessed>"],
  "difficulty_signal": "<low|medium|high|unknown -- unknown unless the facts themselves describe difficulty>",
  "values_signals": ["<what the company appears to value in candidates, per the given facts only>"]
}

Rules:
- If the facts don't describe distinct rounds, return an empty "rounds" list -- do not invent a generic pipeline (phone screen -> onsite -> offer) that isn't actually stated.
- If nothing in the facts speaks to difficulty, use "unknown" -- never guess a level from vibes.
- Every item in "typical_topics" and "values_signals" must be traceable to something stated in the facts -- no generic filler ("problem-solving", "communication skills") unless the facts actually say so.
- If the facts are too thin to say anything meaningful, return {"rounds": [], "typical_topics": [], "difficulty_signal": "unknown", "values_signals": []}."""  # noqa: E501


def _format_claims(claims: list[Claim]) -> str:
    return "\n".join(
        f"- {c['claim_text']} (source: {c['source_title'] or c['source_url']})" for c in claims
    )


_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def _aggregate_confidence(claims: list[Claim]) -> str:
    """The model's own confidence is the WEAKEST link among what it was
    built from -- a structured summary is never more trustworthy than its
    thinnest source, regardless of what an LLM might self-report."""
    ranked = [_CONFIDENCE_RANK.get(c["confidence"], 1) for c in claims]
    lowest = min(ranked)
    return next(k for k, v in _CONFIDENCE_RANK.items() if v == lowest)


async def synthesize_interview_process_model(
    company_name: str,
    claims: list[Claim],
    *,
    llm_api_key: str,
    llm_model: str,
    llm_base_url: str | None,
    generate: LlmGenerate | None = None,
) -> InterviewProcessModel | None:
    """Returns `None` -- not an empty/guessed model -- when there are no
    `interview_process` claims to build from yet. A company with zero
    verified interview-process evidence has no registry entry, honestly,
    rather than a hollow placeholder row."""
    process_claims = [c for c in claims if c["category"] == _INTERVIEW_PROCESS_CATEGORY]
    if not process_claims:
        return None

    call = generate or llm_generate
    response: LLMResponse = await call(
        api_key=llm_api_key,
        model=llm_model,
        base_url=llm_base_url,
        system_prompt=_SYNTHESIS_SYSTEM_PROMPT,
        user_prompt=f"Company: {company_name}\n\nVerified facts:\n{_format_claims(process_claims)}",
        max_tokens=1000,
    )
    parsed = _parse_process_model(response.content)
    if parsed is None:
        return None

    rounds, typical_topics, difficulty_signal, values_signals = parsed
    return InterviewProcessModel(
        company_name=company_name,
        rounds=rounds,
        typical_topics=typical_topics,
        difficulty_signal=difficulty_signal,
        values_signals=values_signals,
        confidence=_aggregate_confidence(process_claims),
    )


def _parse_process_model(
    raw: str,
) -> tuple[list[InterviewRound], list[str], DifficultySignal, list[str]] | None:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None

    rounds: list[InterviewRound] = []
    for item in parsed.get("rounds") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name or not isinstance(name, str):
            continue
        round_entry: InterviewRound = {"name": name}
        fmt = item.get("format")
        if isinstance(fmt, str) and fmt:
            round_entry["format"] = fmt
        focus = item.get("focus")
        if isinstance(focus, str) and focus:
            round_entry["focus"] = focus
        rounds.append(round_entry)

    topics = [t for t in (parsed.get("typical_topics") or []) if isinstance(t, str) and t]
    values = [v for v in (parsed.get("values_signals") or []) if isinstance(v, str) and v]

    difficulty = parsed.get("difficulty_signal")
    difficulty_signal: DifficultySignal = (
        difficulty if difficulty in _VALID_DIFFICULTY else "unknown"
    )

    return rounds, topics, difficulty_signal, values
