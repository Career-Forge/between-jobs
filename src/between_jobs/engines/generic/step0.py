"""Reading what a job posting asks for: the one model call behind the Tailor panel, and the
first step of writing a resume.

The model's answer is cleaned field by field into the shape `Step0Result` declares: strings are
clipped, the number of clusters and terms is capped, a priority is `must_have` only when the
model said it was required, and a company, role or tier the model did not name stays empty or
"unknown". An answer that is not a JSON object at all is the only thing refused.
"""

from __future__ import annotations

from typing import Any

from between_jobs.api.engine_contract import Step0Result
from between_jobs.api.errors import ApiError

from .llm import ModelSession, parse_object
from .prompts import STEP0_SYSTEM, step0_user
from .text import clean_block, clean_line

STEP0_DESCRIPTION_CHARS = 8000

_MAX_CLUSTERS = 8
_MAX_KEYWORDS = 8
_MAX_DEALBREAKERS = 6
_MAX_KEY_TERMS = 25
_TIERS = frozenset({"entry", "mid", "senior", "staff", "executive", "unknown"})
_MUST_HAVE = frozenset({"must_have", "must have", "required", "essential", "mandatory", "core"})


def _text(*values: Any, width: int) -> str:
    """The first of `values` that is a non-empty string, cleaned and clipped; else empty. A
    number, a list or null is not a name: it is left unknown."""
    for value in values:
        if isinstance(value, str) and clean_line(value):
            return clean_line(value, width)
    return ""


def _strings(value: Any, limit: int, width: int) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned = [clean_line(item, width) for item in value if isinstance(item, str)]
    return list(dict.fromkeys(item for item in cleaned if item))[:limit]


def normalize_step0(answer: dict[str, Any]) -> Step0Result:
    """The model's analysis as a `Step0Result`: each field cleaned, clipped and defaulted, so a
    slightly wrong answer is a usable one, and whatever this returns validates as the contract.

    Unknown stays unknown: a company or role the model left empty is empty, and a tier it did
    not name is "unknown"."""
    clusters: list[dict[str, Any]] = []
    raw_clusters = answer.get("clusters")
    for raw in raw_clusters if isinstance(raw_clusters, list) else []:
        if not isinstance(raw, dict):
            continue
        name = _text(raw.get("name"), width=80)
        if not name:
            continue
        label = _text(raw.get("priority"), width=30).lower().replace("-", " ")
        priority = "must_have" if label in _MUST_HAVE else "nice_to_have"
        clusters.append(
            {
                "name": name,
                "priority": priority,
                "keywords": _strings(raw.get("keywords"), _MAX_KEYWORDS, 40),
            }
        )
        if len(clusters) >= _MAX_CLUSTERS:
            break
    tier = _text(answer.get("target_tier"), answer.get("targetTier"), width=20).lower()
    return Step0Result.model_validate(
        {
            "clusters": clusters,
            "dealbreakers": _strings(answer.get("dealbreakers"), _MAX_DEALBREAKERS, 160),
            "targetTier": tier if tier in _TIERS else "unknown",
            "keyTerms": _strings(
                answer.get("key_terms") or answer.get("keyTerms"), _MAX_KEY_TERMS, 40
            ),
            "companyName": _text(answer.get("company_name"), answer.get("companyName"), width=120),
            "roleName": _text(answer.get("role_name"), answer.get("roleName"), width=160),
            "shortRole": _text(answer.get("short_role"), answer.get("shortRole"), width=60),
        }
    )


async def read_job(session: ModelSession, description: str) -> Step0Result:
    """One model call that reads a posting's requirements. Raises `ApiError` (`RUN_FAILED`)
    when the answer is not a JSON object at all: callers that can do without an analysis (a
    resume still gets written) catch it; the Tailor panel's operation lets it through."""
    cleaned = clean_block(description, STEP0_DESCRIPTION_CHARS)
    if not cleaned:
        raise ApiError("INVALID_INPUT", "There's no job description to read.")
    raw = await session.ask(system=STEP0_SYSTEM, user=step0_user(cleaned), max_tokens=1400)
    answer = parse_object(raw)
    if answer is None:
        raise _unreadable()
    return normalize_step0(answer)


def _unreadable() -> ApiError:
    return ApiError(
        "RUN_FAILED",
        "Couldn't read this job's requirements from the model's answer. Try again.",
        retryable=True,
    )
