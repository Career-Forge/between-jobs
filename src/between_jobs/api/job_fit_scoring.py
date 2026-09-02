"""Job Finder P6a -- batch fit-scoring (live-search-track.md's own P6
scoping). The composite-scoring ALGORITHM (batch-of-30 shape, the parse
cascade, the applicability/renormalization layer, sub-score weighting)
follows the same design n8n's own `JobScorer` -> `Parse Scorer Output`
node chain established, ported field-for-field. `_DEFAULT_SYSTEM_PROMPT`
below is this repo's own original, independently-authored prompt against
that same I/O contract, not a copy of n8n's own `prompts/JobScorer.md` --
that file is a member of a real, curated, hosted-only prompt pack this
public repo's own CLAUDE.md rule keeps out of git, the same "algorithm
public, curated/tuned artifact private" split `company_tiers.py`/
`geo_gazetteer.py` already established for reference DATA, applied here
to a prompt for the first time. `_DEFAULT_WEIGHTS` below stays as this
repo's own plain, generic default weighting -- six numbers and three bin
cut-points, not a curated competitive asset (the same test this project
already applies to the ~40-company cohort lookup in `search_aggregation.
expand_cohort`). A hosted deployment wanting its own further-tuned
prompt can supply one via `JOB_SCORING_SYSTEM_PROMPT_PATH` (see
`_load_system_prompt` below) rather than this repo ever carrying it.

One LLM call scores up to 30 jobs at once (`aggregate_jobs()`'s own
tier+recency sort already put the best candidates first, so this is
simply the first 30) -- a real, deliberately cheap ranking pass, distinct
from forge-engines' own deep ATS/ForgeScore engine, which only ever runs
once a user commits to ONE specific posting, never in a batch loop over
search results. Results beyond 30 are returned unscored, not dropped --
the caller decides how to present them (already-sorted by tier/recency).

Three of the six /100 sub-score weights need a real signal between-jobs'
profile schema doesn't have yet, checked directly before writing this,
not assumed:
- `workauth`'s H1B floor/cap needs real USCIS approval data (a real
  import, same precedent as P5d's gazetteer) AND a "candidate explicitly
  stated a US sponsorship need" signal this project has no LLM query-
  expansion stage to derive (same reason P4 never built one). Deferred:
  `workauth` here is the LLM's own 0-100 judgment alone.
- `compensation`'s salary-floor comparison needs a stored candidate
  salary-expectation field between-jobs' profile doesn't have. Deferred:
  falls back to n8n's own already-defined "no floor known" path (a flat
  neutral 60, included only when the job itself states a salary).
- The upstream YOE (years-of-experience) filter needs YOE extracted from
  JD text, which `job_posting_extraction.py` doesn't do yet (salary/
  sponsorship only). Deferred as a separate, later enhancement.

`company_health` is different -- no missing signal, just needed the same
import-precedent execution already proven for the gazetteer (P6b,
`company_tiers.py` + `scripts/import_company_tiers.py`).
`company_health_lookup` stays a plain sync callable rather than taking
a Supabase client directly -- a caller awaits `company_tiers.
get_company_tier_index(supabase)` once per scoring batch (not once per
job) and passes its `.lookup` bound method in; defaults to "always
inapplicable" (every company misses) when omitted, since no route
wires this in yet.

A real, deliberate translation from n8n's own JS: `location_verified`
being JS `null` (a location filter ran, this job's location genuinely
couldn't be resolved) vs `undefined` (no location filter ran at all) is
a real distinction n8n's own ternary relies on -- Python's `SearchResult.
location_verified: bool | None` can't carry that same 3-state
distinction (`None` covers both). `location_requested: bool` recovers
it explicitly: when a location filter actually ran (P5d), `location_
verified` is trusted (`True` -> 100, `None`/unresolved -> 35); when it
didn't, the LLM's own `location_match` guess is trusted instead (the
exact fallback n8n's own code takes for its `undefined` case), since
there's no deterministic signal to prefer over it in that case.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from .llm_client import LLMResponse
from .llm_client import generate as llm_generate
from .profile import ResumeTemplate
from .search_providers import SearchResult

_BATCH_SIZE = 30
_SYSTEM_PROMPT_OVERRIDE_ENV_VAR = "JOB_SCORING_SYSTEM_PROMPT_PATH"

LlmGenerate = Callable[..., Awaitable[LLMResponse]]
"""Same seam `interview_practice.py` already established -- the route
resolves the real BYOK credential and passes `llm_api_key`/`llm_model`/
`llm_base_url` in directly, rather than this module calling
`credential_resolver.resolve()` itself. R3's own real bug (a route
passing its own `llm_generate` reference into a module that also
imported the real one as its default, so tests patching the module-level
import silently missed real API calls) is exactly what this separation
avoids -- this module never touches `credential_resolver` or a Supabase
client at all."""

_DEFAULT_WEIGHTS = {
    "skills": 0.35,
    "experience": 0.20,
    "workauth": 0.15,
    "location": 0.10,
    "company_health": 0.10,
    "compensation": 0.10,
}

_DEFAULT_SYSTEM_PROMPT = """You are the job-matching engine for a job-search assistant. Given a candidate's resume summary and a batch of job postings, score every posting's fit for that candidate and explain why in one short line.

Respond with raw JSON only. Your entire reply must start with { -- no prose, no markdown code fences, no commentary before or after the JSON.

INPUT SHAPE
- master_resume_summary: condensed text covering the candidate's skills, recent roles, target titles, location, and work authorization.
- jobs: a list of postings, each with job_id, title, company, location, url, and optionally description_snippet (a short excerpt -- may be absent).

WHAT TO RETURN
Score every job in the batch -- never skip or omit one; filtering by threshold happens downstream, not here. For each job return:
- job_id: copied verbatim from the input, unchanged.
- fit_score: integer 0-10, your holistic judgment of how well this candidate fits this specific role.
- one_liner: under 90 characters, naming the single strongest reason behind your score (a skill/domain match, a seniority gap, a location issue, etc). No filler words.
- detected_location: the job's actual location as best you can infer from its title/location/snippet, or null if genuinely not stated.
- location_match: "match" if the job is in (or, for a remote role, serves) the candidate's stated or preferred location; "mismatch" if it clearly is not; "unknown" if you can't tell.
- skills_score: integer 0-100, how closely the candidate's real, listed skills cover what this posting implies it needs.
- experience_score: integer 0-100, how well the candidate's seniority and domain background line up with what the role implies.
- workauth_score: integer 0-100, work-authorization fit. When the posting itself says nothing about sponsorship, use only a mild heuristic (a larger, more established employer skews somewhat more likely to sponsor than an early-stage startup) and default near 60 when genuinely unsure -- never invent or assume a sponsorship requirement the candidate never actually stated.

HOW TO SCORE
Judge holistically: real overlap between the candidate's skills and what the posting implies it needs; whether the job's title and level track the candidate's recent trajectory and target roles; whether the location is workable (including a genuinely remote role); and general seniority fit. Score 8-10 when the match is strong enough the candidate would likely clear an initial screen. Score 4-6 for a plausible but imperfect fit -- real but survivable gaps in seniority, domain, or scope. Score below 4 when the specialization, level, or location is a genuine mismatch with no remote option. Score near 0 only when the posting shares essentially nothing with the candidate's background.

This is a fast batch-ranking pass across many postings at once, not a deep read of any single one -- work from the title, company, location, and snippet you're given; don't speculate past that.

Return exactly this shape, one entry per input job, no extra keys:
{"scored": [{"job_id": "...", "fit_score": 0, "one_liner": "...", "detected_location": null, "location_match": "unknown", "skills_score": 0, "experience_score": 0, "workauth_score": 0}]}
"""  # noqa: E501


def _load_system_prompt() -> str:
    """This repo's own generic default is what makes Discover genuinely
    usable BYOK-standalone -- no demo shell, per this project's own
    charter. A hosted deployment can supply its own further-tuned prompt
    at runtime via `JOB_SCORING_SYSTEM_PROMPT_PATH` (a plain text file,
    read fresh per call -- cheap next to the LLM call itself, no stale-
    cache complexity to reason about) -- fails open to the public
    default on any missing/unset/unreadable override, matching this
    project's own established fail-open discipline for optional config."""
    override_path = os.environ.get(_SYSTEM_PROMPT_OVERRIDE_ENV_VAR)
    if not override_path:
        return _DEFAULT_SYSTEM_PROMPT
    try:
        text = Path(override_path).read_text()
    except OSError:
        return _DEFAULT_SYSTEM_PROMPT
    return text if text.strip() else _DEFAULT_SYSTEM_PROMPT


class CompanyHealthLookup(Protocol):
    def __call__(self, company: str | None) -> float | None: ...


def _default_company_health_lookup(company: str | None) -> float | None:
    return None


@dataclass(frozen=True)
class SubScores:
    skills: int
    experience: int
    workauth: int
    location: int
    company_health: int
    compensation: int


@dataclass(frozen=True)
class ScoredJob:
    apply_url: str
    """The stable key -- mirrors n8n's own `job_id`, which was itself
    each job's canonical URL for this pipeline's purposes."""
    fit_score: int
    one_liner: str
    sub_scores: SubScores
    score100: int
    bin: Literal["Strong", "Good", "Mixed", "Poor"]
    bottleneck: str | None
    inapplicable_dims: tuple[str, ...]
    detected_location: str | None
    location_match: Literal["match", "mismatch", "unknown"]
    """The LLM's own location guess -- carried through to the final
    result (matching n8n's own real output shape) even when `location`
    scored off the deterministic `location_verified` signal instead."""


def summarize_profile(profile: ResumeTemplate) -> str:
    """Adapted from n8n's real `summarizeStructured` -- same shape and
    intent (name/headline/summary/experience/projects/skills/education,
    capped and truncated so the prompt stays cheap), translated field-
    for-field to between-jobs' own `ResumeTemplate` schema instead of
    n8n's `resume_bubbles` shape, which this project doesn't have."""
    parts: list[str] = []
    p = profile.personal
    if p.name:
        parts.append(f"CANDIDATE: {p.name}")
    if p.headline:
        parts.append(f"HEADLINE: {p.headline}")
    location = ", ".join(filter(None, [p.location.city, p.location.region, p.location.country]))
    if location:
        parts.append(f"LOCATION: {location}")
    if p.work_authorization:
        parts.append(f"WORK AUTHORIZATION: {p.work_authorization}")

    if profile.summary_bullets:
        bullets = "\n".join(f"- {b}" for b in profile.summary_bullets[:6])
        parts.append(f"SUMMARY:\n{bullets}")

    if profile.experience:
        blocks = []
        for e in profile.experience[:5]:
            dates = f"{e.start_date} to {'current' if e.is_current else e.end_date}"
            block = f"{e.title} @ {e.company} ({dates})"
            if e.bullets:
                block += "\n  " + "\n  ".join(f"- {b}" for b in e.bullets[:8])
            blocks.append(block)
        parts.append("EXPERIENCE:\n" + "\n\n".join(blocks))

    if profile.projects:
        blocks = []
        for pj in profile.projects[:6]:
            tech = f" [{', '.join(pj.tech[:8])}]" if pj.tech else ""
            block = f"{pj.name}{tech}"
            if pj.bullets:
                block += "\n  " + "\n  ".join(f"- {b}" for b in pj.bullets[:3])
            blocks.append(block)
        parts.append("PROJECTS:\n" + "\n".join(blocks))

    skills_list = list(
        dict.fromkeys(
            s
            for cat in (
                profile.skills.programming,
                profile.skills.ai_ml,
                profile.skills.data_mlops,
                profile.skills.cloud_devops,
                profile.skills.tools,
                profile.skills.other,
            )
            for s in cat
        )
    )
    if skills_list:
        parts.append("SKILLS: " + ", ".join(skills_list[:60]))

    if profile.education:
        edu = " | ".join(
            " ".join(filter(None, [e.degree, e.field, e.institution])) for e in profile.education
        )
        parts.append(f"EDUCATION: {edu}")

    return "\n\n".join(parts)[:6000]


def _build_job_batch(results: list[SearchResult]) -> list[dict[str, Any]]:
    return [
        {
            "job_id": r.apply_url,
            "title": r.title,
            "company": r.company,
            "location": r.location,
            "url": r.apply_url,
            "description_snippet": r.snippet[:500] if r.snippet else None,
        }
        for r in results
    ]


def _normalize_fit_score(raw: Any) -> int:
    """n8n's own `normalizeScore` also caps at 3 when `source_tier === 3`
    -- dropped here deliberately, not overlooked: `aggregate_jobs()`
    (P5a) already drops every tier->=3 result before anything reaches
    this module, so that branch is unreachable dead code in this port,
    the same way `_floorParsed`/`_workAuth` (see module docstring) are
    always-null dead branches in the composite calc below."""
    try:
        score = round(float(raw))
    except (TypeError, ValueError):
        score = 0
    return max(0, min(10, score))


def _validate_scored_items(items: Any) -> list[dict[str, Any]] | None:
    if not isinstance(items, list):
        return None
    valid = []
    for s in items:
        if not isinstance(s, dict) or not isinstance(s.get("job_id"), str) or not s["job_id"]:
            continue
        valid.append(
            {
                "job_id": s["job_id"],
                "fit_score": _normalize_fit_score(s.get("fit_score")),
                "one_liner": str(s.get("one_liner") or "No summary")[:100],
                "detected_location": (
                    str(s["detected_location"])[:80]
                    if s.get("detected_location") is not None
                    else None
                ),
                "location_match": (
                    s["location_match"]
                    if s.get("location_match") in ("match", "mismatch", "unknown")
                    else "unknown"
                ),
                "skills_score": (
                    float(s["skills_score"]) if s.get("skills_score") is not None else None
                ),
                "experience_score": (
                    float(s["experience_score"]) if s.get("experience_score") is not None else None
                ),
                "workauth_score": (
                    float(s["workauth_score"]) if s.get("workauth_score") is not None else None
                ),
            }
        )
    return valid or None


_MARKDOWN_FENCE_RX = re.compile(r"```(?:json)?\s*([\s\S]+?)\s*```")
_OBJECT_RX = re.compile(r"\{[\s\S]*\}")
_ITEM_RX = re.compile(
    r'\{\s*"job_id"\s*:\s*"([^"]+)"\s*,\s*"fit_score"\s*:\s*(\d+)\s*,'
    r'\s*"one_liner"\s*:\s*"([^"]*)"\s*\}'
)


def _parse_scorer_response(
    raw: str, expected_job_ids: list[str]
) -> tuple[list[dict[str, Any]], str]:
    """Ported verbatim from n8n's real `Parse Scorer Output` cascade,
    minus its own Strategy 0 (a chainLlm-attached structured-output
    parser between-jobs' own simpler `llm_client.generate` has no
    equivalent of, since it returns raw text) -- starting from the
    strategy that actually applies to a raw-text LLM response. The final
    `neutral_fallback` NEVER fails outright, same as the reference: a
    garbled response still produces something scoreable rather than an
    error, matching this project's own fail-open discipline."""
    try:
        parsed = json.loads(raw)
        result = _validate_scored_items(parsed.get("scored") if isinstance(parsed, dict) else None)
        if result:
            return result, "direct"
    except json.JSONDecodeError:
        pass

    fence_match = _MARKDOWN_FENCE_RX.search(raw)
    if fence_match:
        try:
            parsed = json.loads(fence_match.group(1).strip())
            result = _validate_scored_items(
                parsed.get("scored") if isinstance(parsed, dict) else None
            )
            if result:
                return result, "markdown_fence"
        except json.JSONDecodeError:
            pass

    object_match = _OBJECT_RX.search(raw)
    if object_match:
        try:
            parsed = json.loads(object_match.group(0))
            result = _validate_scored_items(
                parsed.get("scored") if isinstance(parsed, dict) else None
            )
            if result:
                return result, "regex_object"
        except json.JSONDecodeError:
            pass

    items = [
        {
            "job_id": m.group(1),
            "fit_score": _normalize_fit_score(m.group(2)),
            "one_liner": m.group(3)[:100],
            "detected_location": None,
            "location_match": "unknown",
            "skills_score": None,
            "experience_score": None,
            "workauth_score": None,
        }
        for m in _ITEM_RX.finditer(raw)
    ]
    if items:
        return items, "item_regex"

    fallback = [
        {
            "job_id": jid,
            "fit_score": 5,
            "one_liner": "Scoring unavailable -- showing by source quality",
            "detected_location": None,
            "location_match": "unknown",
            "skills_score": None,
            "experience_score": None,
            "workauth_score": None,
        }
        for jid in expected_job_ids
    ]
    return fallback, "neutral_fallback"


def _clamp100(n: float | None) -> int:
    return max(0, min(100, round(n or 0)))


_LOC_MAP_GENERAL = {"match": 100, "unknown": 60, "mismatch": 20}
_LOC_SORT_ORDER = {"match": 0, "unknown": 1, "mismatch": 2}
"""Ported verbatim from n8n's own `_ordLoc` -- a secondary sort key,
tie-breaking equal `score100` values by location confidence."""


def _compute_composite(
    item: dict[str, Any],
    result: SearchResult,
    *,
    location_requested: bool,
    company_health_lookup: CompanyHealthLookup,
) -> ScoredJob:
    base = item["fit_score"] * 10
    skills = _clamp100(item["skills_score"] if item["skills_score"] is not None else base)
    experience = _clamp100(
        item["experience_score"] if item["experience_score"] is not None else base
    )
    workauth = _clamp100(item["workauth_score"] if item["workauth_score"] is not None else base)

    if location_requested:
        location = 100 if result.location_verified is True else 35
    else:
        location = _LOC_MAP_GENERAL.get(item["location_match"], 60)

    health_weight = company_health_lookup(result.company)
    company_health = round(50 + health_weight * 50) if health_weight is not None else 60
    compensation = 60

    sub = SubScores(
        skills=skills,
        experience=experience,
        workauth=workauth,
        location=location,
        company_health=company_health,
        compensation=compensation,
    )
    applicable = {
        "skills": True,
        "experience": True,
        "workauth": True,
        "location": True,
        "company_health": health_weight is not None,
        "compensation": result.salary_min is not None,
    }
    weighted_sum = 0.0
    total_weight = 0.0
    for key, weight in _DEFAULT_WEIGHTS.items():
        if not applicable[key]:
            continue
        weighted_sum += getattr(sub, key) * weight
        total_weight += weight
    # skills+experience are unconditionally applicable (0.55 combined
    # weight), so total_weight can never be 0 -- no fallback needed.
    score100 = round(weighted_sum / total_weight)

    bottleneck = None
    lowest = 101
    for key in _DEFAULT_WEIGHTS:
        if not applicable[key]:
            continue
        value = getattr(sub, key)
        if value < lowest:
            lowest = value
            bottleneck = key

    bin_: Literal["Strong", "Good", "Mixed", "Poor"] = (
        "Strong"
        if score100 >= 70
        else "Good"
        if score100 >= 55
        else "Mixed"
        if score100 >= 40
        else "Poor"
    )

    return ScoredJob(
        apply_url=result.apply_url,
        fit_score=item["fit_score"],
        one_liner=item["one_liner"],
        sub_scores=sub,
        score100=score100,
        bin=bin_,
        bottleneck=bottleneck,
        inapplicable_dims=tuple(k for k, v in applicable.items() if not v),
        detected_location=item["detected_location"],
        location_match=item["location_match"],
    )


async def score_jobs(
    *,
    profile: ResumeTemplate,
    results: list[SearchResult],
    llm_api_key: str,
    llm_model: str,
    llm_base_url: str | None,
    location_requested: bool = False,
    company_health_lookup: CompanyHealthLookup = _default_company_health_lookup,
    generate: LlmGenerate | None = None,
) -> tuple[list[ScoredJob], list[SearchResult], str]:
    """Scores the first 30 `results` (already sorted by `aggregate_jobs()`
    -- this function trusts that ordering, it doesn't re-rank before
    picking the batch). Returns `(scored, unscored, strategy)`:
    `unscored` is `results[30:]`, still real `SearchResult`s the caller
    can still show, just never sent to the LLM; `strategy` names which
    parse strategy actually produced `scored` (`"direct"` through
    `"neutral_fallback"`), surfaced for the same observability reason
    `search_jobs()` already returns its own `warnings`. The caller
    resolves the real BYOK credential (capability `"job_scoring"`) and
    passes it in -- this function never touches `credential_resolver` or
    a Supabase client itself, matching `generate_practice_questions`'s
    own established separation."""
    to_score = results[:_BATCH_SIZE]
    unscored = results[_BATCH_SIZE:]
    if not to_score:
        return [], unscored, "none"

    call = generate or llm_generate
    payload = {
        "master_resume_summary": summarize_profile(profile),
        "jobs": _build_job_batch(to_score),
    }
    response = await call(
        api_key=llm_api_key,
        model=llm_model,
        base_url=llm_base_url,
        system_prompt=_load_system_prompt(),
        user_prompt=json.dumps(payload),
    )

    expected_ids = [r.apply_url for r in to_score]
    items, strategy = _parse_scorer_response(response.content, expected_ids)
    by_url = {r.apply_url: r for r in to_score}

    scored = []
    for item in items:
        result = by_url.get(item["job_id"])
        if result is None:
            continue
        scored.append(
            _compute_composite(
                item,
                result,
                location_requested=location_requested,
                company_health_lookup=company_health_lookup,
            )
        )
    scored.sort(key=lambda s: (-s.score100, _LOC_SORT_ORDER.get(s.location_match, 1)))

    return scored, unscored, strategy
