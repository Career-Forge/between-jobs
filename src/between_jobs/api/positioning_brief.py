"""Positioning brief -- outreach-v2-search-first.md Phase I, direction
(B) then (A) from the 2026-09-04 brainstorm; Proposal §27.6's own
"gap-analysis sleeper feature." Synthesizes signals this platform already
computes -- Tailor's live coverage/skill-state read (tailor_coverage.py),
Company Intel claims, and (optionally) the Honest Floor fit/gate read
(Phase I's own persistence fix, applications_store.get_latest_prepare_
result) -- into three short, individually-grounded sentences: what to
lead with, the one gap that actually matters, and one project that would
close it. Never a homework list -- capped at one project structurally,
not just by prompt instruction.

Same "LLM decides words, never shape" discipline as outreach_writer.py:
the LLM picks WHICH real skill/requirement to cite from a deterministic,
per-request candidate list (never invents one), and every citation is
re-validated as an actual member of that list before being trusted --
`strength_candidates`/`gap_candidates` mirror `outreach_writer.build_
hook_context`'s own role, just for two buckets instead of one ranked pick.

"unsupported" (skills.py's own words: "renders as an honest gap") is the
only skill state treated as a citable gap; "adjacent" -- a real but
indirect connection, per skills.py's own docstring -- is deliberately
excluded from both the strength and gap buckets, neither a clean win nor
a clean gap. A zero-match must-have cluster (tailor.py's own
`ClusterCoverage`) is the other citable gap source, matching this
project's existing Gap Interview trigger (`tailor.pick_gap_interview_
questions`) almost exactly, applied here to a different surface.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from .llm_client import LLMResponse
from .llm_client import generate as llm_generate
from .tailor import ClusterCoverage

LlmGenerate = Callable[..., Awaitable[LLMResponse]]

_BANNED_PROMISE_PHRASES = (
    "you'll get",
    "you will get",
    "will get you",
    "you'll land",
    "you will land",
    "will land you",
    "guaranteed",
    "guarantee",
    "shoo-in",
    "sure thing",
    "surefire",
    "definitely land",
    "definitely get",
    "in the bag",
    "lock in the offer",
    "can't miss",
    "cannot miss",
)
"""Mirrors outreach_writer.py's own banned-openers rubric, applied here to
outcome-promise language anywhere in the brief rather than just its
opening words -- Proposal's own "never promise an outcome" rule. An
adversarial review confirmed the original, shorter list missed common
real phrasings ("you will land this role," "will get you the offer") --
still an exact-substring list, not exhaustive, but widened to the
phrasings actually found to slip through."""


class PositioningBrief(TypedDict):
    lead_with: str
    lead_with_citation: str
    gap_that_matters: str
    gap_citation: str
    recommended_project: str


def strength_candidates(skills: list[dict[str, Any]], coverage: list[ClusterCoverage]) -> list[str]:
    """Real VERIFIED/SUPPORTED skill names, plus real covered must-have
    cluster names -- the only terms `lead_with_citation` is allowed to
    reference. `skills` is `resume_documents_routes.get_coverage`'s own
    per-skill list shape: `{"skill", "requested_as", "state"}`."""
    names = {s["skill"] for s in skills if s.get("state") in ("verified", "supported")}
    names |= {
        c["name"] for c in coverage if c["priority"] == "must_have" and c["coverage_count"] > 0
    }
    return sorted(n for n in names if n)


def gap_candidates(skills: list[dict[str, Any]], coverage: list[ClusterCoverage]) -> list[str]:
    """Real UNSUPPORTED skill names, plus real zero-match must-have
    cluster names -- the only terms `gap_citation` is allowed to
    reference."""
    names = {s["skill"] for s in skills if s.get("state") == "unsupported"}
    names |= {
        c["name"] for c in coverage if c["priority"] == "must_have" and c["coverage_count"] == 0
    }
    return sorted(n for n in names if n)


def fit_context_line(fit: dict[str, Any] | None) -> str:
    if not fit:
        return "(no Honest Floor read available for this application yet)"
    recommendation = fit.get("recommendation") or "unknown"
    overall = fit.get("overall_score")
    return f"Honest Floor read: recommendation={recommendation}, overall_score={overall}/10"


_BRIEF_SYSTEM_PROMPT = """You are helping a job seeker decide how to position themselves for a \
specific role and company. You are given a list of their real STRENGTHS (skills or requirements \
they genuinely have, already verified deterministically) and a list of their real GAPS (skills or \
requirements they genuinely lack) -- never invent a skill, requirement, or fact outside these lists.

Rules, all mandatory:
- "lead_with": one sentence naming the single strongest real match to lead with in outreach or an \
application. Must cite exactly one term from the STRENGTHS list, verbatim, in "lead_with_citation".
- "gap_that_matters": one sentence naming the single most important real gap for THIS role. Must \
cite exactly one term from the GAPS list, verbatim, in "gap_citation".
- "recommended_project": one sentence describing ONE project idea that would close that specific \
gap -- never a list, never generic career advice, must clearly relate to the cited gap.
- Never promise an outcome ("you'll get the offer," "this makes you a shoo-in," "guaranteed to \
land you the role") -- state a genuine, honest read, never a guarantee.
- Optional company research may be provided as context only -- it is never itself a valid citation \
for "lead_with_citation" or "gap_citation", which must always be a term from the STRENGTHS/GAPS \
lists.

Return ONLY a JSON object with exactly these fields: "lead_with", "lead_with_citation", \
"gap_that_matters", "gap_citation", "recommended_project". No prose, no markdown code fences."""


def _parse_brief(
    raw: str, *, strength_candidates: set[str], gap_candidates: set[str]
) -> PositioningBrief | None:
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

    fields = (
        "lead_with",
        "lead_with_citation",
        "gap_that_matters",
        "gap_citation",
        "recommended_project",
    )
    values: dict[str, str] = {}
    for field in fields:
        value = parsed.get(field)
        if not value or not isinstance(value, str):
            return None
        values[field] = value

    # Deterministic re-grounding -- the actual enforcement of "LLMs
    # decide words, never shape," not the prompt's own wording. A
    # citation outside what was actually offered fails the whole brief,
    # same "no draft" precedent as outreach_writer.py's own hook check.
    if values["lead_with_citation"] not in strength_candidates:
        return None
    if values["gap_citation"] not in gap_candidates:
        return None

    return PositioningBrief(
        lead_with=values["lead_with"],
        lead_with_citation=values["lead_with_citation"],
        gap_that_matters=values["gap_that_matters"],
        gap_citation=values["gap_citation"],
        recommended_project=values["recommended_project"],
    )


def check_rubric(brief: PositioningBrief) -> list[str]:
    """Deterministic post-check, never just prompted-and-trusted -- same
    pattern as outreach_writer.check_rubric applied to a different
    rule."""
    haystack = " ".join(
        [brief["lead_with"], brief["gap_that_matters"], brief["recommended_project"]]
    ).lower()
    if any(phrase in haystack for phrase in _BANNED_PROMISE_PHRASES):
        return ["brief contains an outcome-promise phrase"]
    return []


async def generate_positioning_brief(
    *,
    company: str,
    role_title: str,
    coverage: list[ClusterCoverage],
    skills: list[dict[str, Any]],
    company_intel_claims: list[dict[str, Any]],
    fit: dict[str, Any] | None,
    llm_api_key: str,
    llm_model: str,
    llm_base_url: str | None,
    generate: LlmGenerate = llm_generate,
    max_attempts: int = 2,
) -> tuple[PositioningBrief | None, list[str]]:
    """One application at a time. Retries at most once if the rubric or
    grounding check fails on the first attempt -- "every retry has a
    hard cap," same as outreach_writer.generate_outreach_draft. Returns
    (None, [reason]) when there's genuinely nothing real to build a brief
    from -- no LLM call is made in that case, same "no evidence, no
    draft" precedent, not a failure worth spending a call on."""
    strengths = strength_candidates(skills, coverage)
    gaps = gap_candidates(skills, coverage)
    if not strengths:
        return None, ["no verified or supported skill/requirement to lead with yet"]
    if not gaps:
        return None, ["no honest gap found -- coverage looks complete for this role so far"]

    claim_lines = (
        "\n".join(f"- {c['claim_text']}" for c in company_intel_claims)
        if company_intel_claims
        else "(no Company Intel research available for this company yet)"
    )
    user_prompt = (
        f"Company: {company}\nRole: {role_title}\n\n"
        f"STRENGTHS (cite exactly one, verbatim):\n"
        + "\n".join(f"- {s}" for s in strengths)
        + "\n\nGAPS (cite exactly one, verbatim):\n"
        + "\n".join(f"- {g}" for g in gaps)
        + f"\n\nCompany research (context only, never a citation source):\n{claim_lines}"
        + f"\n\n{fit_context_line(fit)}"
    )

    strength_set = set(strengths)
    gap_set = set(gaps)

    brief: PositioningBrief | None = None
    warnings: list[str] = ["brief could not be parsed"]
    for _attempt in range(max_attempts):
        response = await generate(
            api_key=llm_api_key,
            model=llm_model,
            base_url=llm_base_url,
            system_prompt=_BRIEF_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=800,
        )
        brief = _parse_brief(
            response.content, strength_candidates=strength_set, gap_candidates=gap_set
        )
        if brief is None:
            warnings = ["brief could not be parsed, or cited a term outside the real candidates"]
            continue
        warnings = check_rubric(brief)
        if not warnings:
            return brief, []

    return brief, warnings
