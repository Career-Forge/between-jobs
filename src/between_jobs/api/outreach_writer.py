"""OutreachWriter -- message drafting (outreach-contactfinder.md Phase E)
-- Proposal §27.4. In-app only: no send integration exists here (that's
Phase F, a genuinely separate OAuth surface) -- matches what BOTH
reference repos actually ship today. n8n's real OutreachWriter renders 4
text variants to Telegram ending "_Reply "sent" once you actually use
this._" -- the human copies and sends themselves; careerforge-command-
center's `generateOutreach()` returns the same 4-field JSON shape ported
here ({subject, emailBody, linkedinMessage, followUpMessage}) and its
only "send-adjacent" UI is a `mailto:` link that still needs the human
to press send in their own client.

Deterministic structure wraps the one LLM call that phrases the actual
words -- "LLMs decide words, never shape":
- **One verified public hook, never the whole evidence list** (Proposal
  §27.4's own rule) -- `build_hook_context` deterministically picks the
  single strongest evidence row (highest confidence, most recent as a
  tiebreak) rather than letting the LLM choose from everything, which
  risks stitching together an implied relationship that doesn't exist.
- **A banned-opener rubric** (mirrors n8n's own rubric-check node) and a
  **hard 300-character ceiling on the LinkedIn message** (command-center's
  own rule) -- checked in Python after the LLM call, never just prompted
  and trusted.
- **No evidence, no draft.** If a candidate has zero evidence rows
  (shouldn't happen given ContactFinder's own grounding, but defended
  against explicitly), this module refuses to draft rather than let the
  LLM invent a hook from nothing.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from .llm_client import LLMResponse
from .llm_client import generate as llm_generate

LlmGenerate = Callable[..., Awaitable[LLMResponse]]

_LINKEDIN_MESSAGE_MAX_CHARS = 300
"""careerforge-command-center's own rule for `linkedinMessage`."""

_BANNED_OPENERS = (
    "i hope this email finds you well",
    "i hope this message finds you well",
    "i hope you're doing well",
    "i came across your profile",
    "i stumbled upon your profile",
    "my name is",
)
"""Mirrors n8n's real OutreachWriter rubric-check node -- generic,
relationship-free openers that read as templated spam rather than a
genuine, hook-specific reason for reaching out."""

_CONFIDENCE_RANK = {"verified": 3, "strong": 2, "inferred": 1, "unsupported": 0}


class OutreachDraft(TypedDict):
    subject: str
    email_body: str
    linkedin_message: str
    follow_up_message: str
    hook_evidence_id: str | None


def build_hook_context(evidence: list[dict[str, Any]]) -> tuple[str, str | None]:
    """Deterministically picks ONE piece of evidence as the outreach hook
    -- never hands the LLM the whole evidence list to choose from, which
    risks stitching together an implied relationship across sources that
    doesn't actually exist. Highest confidence wins; most recent
    `observed_at` breaks a tie."""
    if not evidence:
        return "", None
    ranked = sorted(
        evidence,
        key=lambda e: (
            _CONFIDENCE_RANK.get(e.get("confidence", ""), 0),
            e.get("observed_at") or "",
        ),
        reverse=True,
    )
    best = ranked[0]
    hook_text = f"{best['source_title']}: {best['source_snippet']}"
    return hook_text, best.get("id")


_DRAFT_SYSTEM_PROMPT = """You are drafting a short outreach message from a job seeker to a real \
professional contact. Rules, all mandatory:
- Use ONLY the single verified hook provided below (the contact's own real public work) -- never \
invent an achievement, shared history, or relationship that doesn't exist.
- Reference the hook specifically and naturally -- generic flattery is not a hook.
- Never open with a generic greeting like "I hope this email finds you well," "I came across your \
profile," or a bare self-introduction ("My name is...") -- open with the specific, real reason \
you're reaching out.
- Keep it short. The LinkedIn message must be under 300 characters.
- Include one short, specific call to action -- not "let me know if you'd like to chat," \
something concrete.
- Never mention compensation, visa/work-authorization status, or any sensitive personal detail \
unless explicitly told to.
- Never promise an outcome ("I'm confident I'd be a great fit") -- state genuine interest, not a \
guarantee.

Return ONLY a JSON object with exactly these fields:
- "subject": a short email subject line
- "email_body": the full email message
- "linkedin_message": a LinkedIn connection-request note, under 300 characters
- "follow_up_message": a short follow-up message to send if there's no response after a week

No prose, no markdown code fences -- ONLY the JSON object."""


def _parse_draft(raw: str, *, hook_evidence_id: str | None) -> OutreachDraft | None:
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

    fields = ("subject", "email_body", "linkedin_message", "follow_up_message")
    values: dict[str, str] = {}
    for field in fields:
        value = parsed.get(field)
        if not value or not isinstance(value, str):
            return None
        values[field] = value

    return OutreachDraft(
        subject=values["subject"],
        email_body=values["email_body"],
        linkedin_message=values["linkedin_message"],
        follow_up_message=values["follow_up_message"],
        hook_evidence_id=hook_evidence_id,
    )


def check_rubric(draft: OutreachDraft) -> list[str]:
    """Deterministic post-check, never just prompted-and-trusted --
    matches this project's own 'LLM decides words, never shape' pattern
    applied to a quality gate rather than a factual one."""
    warnings: list[str] = []
    if draft["email_body"].strip().lower().startswith(_BANNED_OPENERS):
        warnings.append("email_body opens with a generic, banned phrase")
    if draft["linkedin_message"].strip().lower().startswith(_BANNED_OPENERS):
        warnings.append("linkedin_message opens with a generic, banned phrase")
    if len(draft["linkedin_message"]) > _LINKEDIN_MESSAGE_MAX_CHARS:
        warnings.append(f"linkedin_message exceeds {_LINKEDIN_MESSAGE_MAX_CHARS} characters")
    return warnings


async def generate_outreach_draft(
    *,
    person_name: str,
    claimed_title: str | None,
    company: str,
    role_title: str,
    evidence: list[dict[str, Any]],
    llm_api_key: str,
    llm_model: str,
    llm_base_url: str | None,
    generate: LlmGenerate = llm_generate,
    max_attempts: int = 2,
) -> tuple[OutreachDraft | None, list[str]]:
    """One candidate at a time, never bulk -- this function's own
    signature has no way to draft for more than one person per call,
    matching MASTER_PLAN's own hard "no bulk mode ever" rule structurally,
    not just by convention. Retries at most once if the rubric check
    fails on the first attempt -- "every retry has a hard cap," same as
    every other regen path in this codebase. Returns (None, [reason]) if
    no draft could be produced at all (no evidence, or the LLM never
    returned valid JSON across every attempt)."""
    hook_text, hook_evidence_id = build_hook_context(evidence)
    if not hook_text:
        return None, ["no grounded evidence available to draft an outreach hook from"]

    user_prompt = (
        f"Contact: {person_name}, {claimed_title or 'title unknown'} at {company}\n"
        f"Role I'm applying for: {role_title}\n"
        f"Verified hook (the ONLY thing you may reference about this person):\n{hook_text}"
    )

    draft: OutreachDraft | None = None
    warnings: list[str] = ["draft could not be parsed"]
    for _attempt in range(max_attempts):
        response = await generate(
            api_key=llm_api_key,
            model=llm_model,
            base_url=llm_base_url,
            system_prompt=_DRAFT_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=1200,
        )
        draft = _parse_draft(response.content, hook_evidence_id=hook_evidence_id)
        if draft is None:
            warnings = ["draft could not be parsed"]
            continue
        warnings = check_rubric(draft)
        if not warnings:
            return draft, []

    return draft, warnings
