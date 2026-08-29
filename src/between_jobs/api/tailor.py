"""Deterministic requirement-coverage matching (Sprint 3.3b) -- Proposal
§24.5.4's "requirement coverage map crosses Step0 clusters with career
evidence."

No LLM here. Step0 (forge-engines' `/step0`, the one real LLM call this
needs) produces requirement clusters with keywords; matching those
keywords against the user's own `career_facts` text is plain substring
search, done entirely on this side. "Match display is coverage counts,
never an unexplained percentage ring" (§24.5.4) -- `coverage_count` is
exactly that count, and `matched_fact_ids` is the real evidence behind
it, not a summary a caller has to trust blind.
"""

from __future__ import annotations

from typing import Any, TypedDict

from .skills import find_gap_bridge


class ClusterCoverage(TypedDict):
    name: str
    priority: str
    keywords: list[str]
    coverage_count: int
    matched_fact_ids: list[str]


def _fact_text(fact: dict[str, Any]) -> str:
    """Flattens a career_fact's `value_json` into one lowercased,
    searchable string. Field names span every fact_type this platform
    derives (experience/project/education/publication, profile.py's
    `_derive_facts`) -- an unrecognized field is just never checked
    against, not an error."""
    value = fact.get("value_json") or {}
    parts: list[str] = []
    for key in ("title", "company", "name", "degree", "field", "institution", "venue", "authors"):
        v = value.get(key)
        if isinstance(v, str):
            parts.append(v)
    for key in ("bullets", "skills", "tech", "metrics", "coursework"):
        v = value.get(key)
        if isinstance(v, list):
            parts.extend(str(item) for item in v)
    return " ".join(parts).lower()


def compute_coverage(
    clusters: list[dict[str, Any]], facts: list[dict[str, Any]]
) -> list[ClusterCoverage]:
    """One entry per Step0 cluster, in Step0's own order -- callers that
    want a "top gaps first" or similar view sort this themselves rather
    than have this function guess at a presentation order."""
    fact_texts = [(fact["id"], _fact_text(fact)) for fact in facts]
    coverage: list[ClusterCoverage] = []
    for cluster in clusters:
        keywords = [str(k).lower() for k in (cluster.get("keywords") or []) if k]
        matched = [
            fact_id for fact_id, text in fact_texts if any(keyword in text for keyword in keywords)
        ]
        coverage.append(
            {
                "name": cluster.get("name", ""),
                "priority": cluster.get("priority", ""),
                "keywords": cluster.get("keywords") or [],
                "coverage_count": len(matched),
                "matched_fact_ids": matched,
            }
        )
    return coverage


class GapCandidate(TypedDict):
    cluster_name: str
    bridge_skill: str


GAP_INTERVIEW_MAX_QUESTIONS = 3
"""D3 (honest-score-surfaces.md): a hard cap on how many Gap Interview
questions surface per application. Enforced here, the one and only place
this cap applies -- forge-engines' own gap_interview.py repeats the same
constant only as a sanity bound on prompt size, not as a second
enforcement point."""


def pick_gap_interview_questions(
    coverage: list[ClusterCoverage], profile: dict[str, Any]
) -> list[GapCandidate]:
    """D3's trigger, cluster by cluster: a must-have cluster with zero
    deterministic matches (`compute_coverage`, above) is a real gap.
    `find_gap_bridge` (skills.py) decides whether the profile has anything
    honestly adjacent to bridge it -- a cluster with no bridge is simply
    skipped, never forced into a question. Capped at
    GAP_INTERVIEW_MAX_QUESTIONS, in `coverage`'s own cluster order (Step0's
    own priority ordering, not re-sorted here)."""
    candidates: list[GapCandidate] = []
    for cluster in coverage:
        if cluster["priority"] != "must_have" or cluster["coverage_count"] > 0:
            continue
        bridge = find_gap_bridge(cluster["keywords"], profile)
        if bridge is None:
            continue
        candidates.append({"cluster_name": cluster["name"], "bridge_skill": bridge})
        if len(candidates) >= GAP_INTERVIEW_MAX_QUESTIONS:
            break
    return candidates
