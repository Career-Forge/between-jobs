"""ContactFinder / Outreach evidence-graph core (outreach-contactfinder.md
Phase A) -- Proposal §26.1 stages 4-6 and §27. Finds and ranks named
individuals (recruiters, hiring managers, engineering leaders) publicly
tied to a company/role, each claim carrying the real source it came from.
Never resolves or stores an email or phone number -- that's Phase C's
job, opt-in and human-gated, layered on top of what this module produces.

Deliberately NOT ported from n8n's own thin single-pass ContactFinder the
way most of this codebase's prior ports work -- Proposal's own stage-6
ranking-factor list (team proximity, active hiring evidence, role/
location relevance, decision influence, source authority, recency,
identity confidence, warm-path strength) reads as a near-paraphrase of
careerforge-command-center's actual `scoreCandidatePortfolio()`, so that
engine is this module's primary design source. n8n is mined for two
narrower, still load-bearing things: its anti-fabrication rule ("Only
Real People From Provided Sources" -- must ONLY include people whose full
names appear explicitly in the provided sources, empty list if none) and
the `s154` incident lesson (a Postgres node there silently dropped
context fields downstream, causing a real billed search for a company
literally named "Unknown" -- read fields explicitly from where they were
produced, never trust passthrough state across pipeline stages).

Candidate extraction needs one real LLM call per run (same shape as
`company_intel_pipeline.py`'s own Stage 7 synthesis, and what both
reference repos actually do) -- the plan's L1(fetch)/L2(resolve+ground+
rank)/L3(hook-write) split describes deterministic fetch and
deterministic RE-VALIDATION wrapping this one narrow, always-re-grounded
extraction call, not a zero-LLM identity step. Phase E's own outreach-hook
phrasing is the separate, later LLM call the "L3" framing centrally means.

No LinkedIn-targeted fetching anywhere in this module, on purpose -- see
outreach-contactfinder.md's own "Rejected outright" section. A LinkedIn
URL may surface as one ordinary search hit among several from the general
web-search fetchers below; it is never specifically targeted, never
crawled, and never structurally parsed.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal, TypedDict, cast

import httpx

from .errors import ApiError
from .llm_client import LLMResponse
from .llm_client import generate as llm_generate
from .research_clients import SearchHit, search_firecrawl, search_you_com

_GITHUB_API_URL = "https://api.github.com"
_GITHUB_TIMEOUT_SECONDS = 15.0
_GITHUB_ORG_MEMBER_LIMIT = 10
"""Bounded on purpose -- GitHub's unauthenticated rate limit is 60
requests/hour, and each named member costs a second `/users/{login}` call
to get a real name (the org-members endpoint itself only returns a
handle). 10 members is enough to surface a real signal without risking
the rate limit on a single research run."""

LlmGenerate = Callable[..., Awaitable[LLMResponse]]

Confidence = Literal["verified", "strong", "inferred", "unsupported"]
_CONFIDENCE_VALUES: tuple[Confidence, ...] = ("verified", "strong", "inferred", "unsupported")

Persona = Literal["hiring_lead", "recruiter", "manager", "senior_leader", "senior_ic"]
_PERSONA_WEIGHT: dict[Persona, float] = {
    "hiring_lead": 40.0,
    "recruiter": 35.0,
    "manager": 25.0,
    "senior_leader": 15.0,
    "senior_ic": 10.0,
}
"""Proposal §27.1's own priority order, as base scores -- "a recruiter
publicly hiring for the exact role can outrank a VP with no role
connection" (§26.1 stage 6's own rule) falls directly out of this plus
`_hiring_language_bonus` below, without needing seniority to dominate.
Tier 6 (alumni/warm connectors) is opt-in and needs a signal (the user's
own alma mater) this module doesn't have wired yet -- a real, disclosed
scope cut for Phase A, not silently dropped."""


class ContactQuery(TypedDict):
    persona: Persona
    query: str


def build_contact_query_plan(company: str, role_title: str) -> list[ContactQuery]:
    """A fixed, bounded query plan, same discipline as
    `company_intel_pipeline.build_query_plan` ("the planner cannot
    generate an unbounded loop") -- covers Proposal §27.1's tiers 1-5."""
    return [
        ContactQuery(
            persona="hiring_lead",
            query=f'{company} "{role_title}" hiring manager team lead',
        ),
        ContactQuery(
            persona="recruiter",
            query=f"{company} technical recruiter talent acquisition {role_title}",
        ),
        ContactQuery(
            persona="manager",
            query=f'{company} engineering manager "{role_title}" team',
        ),
        ContactQuery(
            persona="senior_leader",
            query=f"{company} director VP engineering {role_title}",
        ),
        ContactQuery(
            persona="senior_ic",
            query=f'{company} "{role_title}" staff principal engineer',
        ),
    ]


def guess_github_org_slug(company: str) -> str | None:
    """Deterministic, no LLM guess and no search-engine lookup -- strips
    the company name down to a lowercase alphanumeric slug (the
    overwhelmingly common real convention: "Sarvam AI" -> "sarvamai",
    "OpenAI" -> "openai"). Returns None for a name that reduces to
    nothing usable. A wrong guess just 404s in `fetch_github_org_members`
    and is treated as an ordinary empty L1 result, never an error --
    this function's only job is to produce a plausible attempt, not a
    verified one."""
    slug = "".join(ch for ch in company.lower() if ch.isalnum())
    return slug or None


async def fetch_github_org_members(
    http: httpx.AsyncClient, *, org_slug: str, limit: int = _GITHUB_ORG_MEMBER_LIMIT
) -> list[SearchHit]:
    """A dedicated L1 fetcher, distinct from the general web-search
    lanes below -- GitHub's public org-members API needs no auth for
    public orgs and is exactly the kind of purpose-built, ToS-clean
    source outreach-contactfinder.md's own source strategy calls for.
    Tries the company name's own likely org slug directly; a 404 just
    means no org exists there, handled as an empty result like any other
    L1 miss, never an error -- no LLM guessing, no search-engine lookup
    to resolve the slug. The members endpoint itself only returns a
    handle, not a real name, so each member needs one follow-up `/users/
    {login}` call; a member with no public `name` set is skipped -- a
    bare handle isn't useful evidence for outreach."""
    try:
        response = await http.get(
            f"{_GITHUB_API_URL}/orgs/{org_slug}/members",
            headers={"Accept": "application/vnd.github+json"},
            params={"per_page": limit},
            timeout=_GITHUB_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        return []
    if response.status_code != 200:
        return []

    logins = [
        member["login"]
        for member in response.json()
        if isinstance(member, dict) and member.get("login")
    ][:limit]

    hits: list[SearchHit] = []
    for login in logins:
        try:
            user_response = await http.get(
                f"{_GITHUB_API_URL}/users/{login}",
                headers={"Accept": "application/vnd.github+json"},
                timeout=_GITHUB_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError:
            continue
        if user_response.status_code != 200:
            continue
        user = user_response.json()
        name = user.get("name")
        if not name:
            continue
        bio = (user.get("bio") or "").strip()
        hits.append(
            SearchHit(
                title=f"{name} -- {org_slug} GitHub organization member",
                url=user.get("html_url") or f"https://github.com/{login}",
                snippet=(
                    f"Public member of the {org_slug} GitHub organization."
                    + (f" {bio}" if bio else "")
                ),
                published_at=None,
            )
        )
    return hits


QueryResults = list[tuple[ContactQuery, list[SearchHit]]]


async def run_contact_research(
    http: httpx.AsyncClient,
    queries: list[ContactQuery],
    *,
    you_com_key: str | None,
    firecrawl_key: str | None,
    github_org_slug: str | None,
) -> tuple[QueryResults, list[str], list[str]]:
    """Same provider-fallback shape as `company_intel_pipeline.run_
    research`: You.com primary when configured, Firecrawl fallback, one
    failed query becomes a warning rather than aborting the whole run.
    GitHub org lookup runs once per research pass, independent of which
    search provider is configured -- it's a free, unauthenticated fetch,
    not gated behind a BYOK credential the way You.com/Firecrawl are."""
    if you_com_key is None and firecrawl_key is None:
        raise ApiError(
            "SETUP_REQUIRED",
            "ContactFinder needs a You.com or Firecrawl key configured.",
            capability="contact_research",
            missing=["you_com_or_firecrawl_credential"],
            settings_path="/profile/integrations",
        )

    provider = "you_com" if you_com_key is not None else "firecrawl"
    results: QueryResults = []
    warnings: list[str] = []
    for q in queries:
        try:
            if you_com_key is not None:
                hits = await search_you_com(http, api_key=you_com_key, query=q["query"])
            else:
                assert firecrawl_key is not None
                hits = await search_firecrawl(http, api_key=firecrawl_key, query=q["query"])
        except ApiError as e:
            warnings.append(f"{q['persona']}: {e.message}")
            hits = []
        results.append((q, hits))

    providers_used = [provider]
    if github_org_slug:
        github_hits = await fetch_github_org_members(http, org_slug=github_org_slug)
        if github_hits:
            results.append(
                (ContactQuery(persona="senior_ic", query=f"github:{github_org_slug}"), github_hits)
            )
            providers_used.append("github")

    return results, providers_used, warnings


class ContactEvidence(TypedDict):
    """Proposal §27.3's exact schema, field-for-field."""

    source_url: str
    source_title: str
    source_snippet: str
    observed_at: str
    evidence_kind: str
    confidence: Confidence


class ContactCandidate(TypedDict):
    person_name: str
    claimed_title: str | None
    claimed_team: str | None
    company: str
    persona: Persona
    relevance_reason: str
    priority_score: float
    evidence: list[ContactEvidence]


_EXTRACTION_SYSTEM_PROMPT = """You are a contact-discovery extractor. You are given search \
snippets about people who may work at a specific company, each with a source URL, title, and \
snippet. Your ONLY job is to identify real, named individuals who are explicitly named in the \
provided sources -- never invent a person, a title, or a company.

Rule: only include a person whose full name appears explicitly, verbatim, in that source's own \
title or snippet text. If a snippet describes a role or team without naming a person, do not \
invent someone to fill it. If no named people appear in the sources at all, return an empty array.

Each object in your JSON array must have exactly these fields:
- "person_name": the person's full name, exactly as it appears in the source
- "claimed_title": their title if stated in that source, else null
- "claimed_team": their team if stated in that source, else null
- "source_url": must be one of the URLs given in the evidence below, verbatim
- "evidence_kind": "search_snippet" or "github_membership", matching the source
- "confidence": "verified" if the source clearly and unambiguously names this person in this \
role, "strong" if likely but with minor ambiguity, "inferred" if the connection is indirect, \
"unsupported" if you are not confident this is a genuine match

Return ONLY the JSON array, no prose, no markdown code fences."""


def _format_evidence(results: QueryResults) -> str:
    lines = []
    for query, hits in results:
        for hit in hits:
            lines.append(f"[{query['persona']}] {hit['title']} -- {hit['url']}\n{hit['snippet']}")
    return "\n\n".join(lines)


def _normalize_name(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _source_explicitly_names_candidate(
    person_name: str, source_title: str, source_snippet: str
) -> bool:
    """Ported from careerforge-command-center's own
    `sourceExplicitlyNamesCandidate()` -- the cited source's own title/
    snippet must literally contain the candidate's normalized name, not
    just be a URL that happens to exist in the retrieved evidence set.
    Stronger than `company_intel_pipeline._parse_claims`'s bare
    URL-membership check, and the deterministic enforcement of n8n's own
    prompted-but-unenforced Rule 1."""
    normalized_name = _normalize_name(person_name)
    haystack = _normalize_name(f"{source_title} {source_snippet}")
    return bool(normalized_name) and normalized_name in haystack


def _hiring_language_bonus(evidence: list[ContactEvidence]) -> float:
    hiring_terms = ("hiring", "we're hiring", "join our team", "open role")
    if any(term in e["source_snippet"].lower() for e in evidence for term in hiring_terms):
        return 20.0
    return 0.0


def _score_candidate(persona: Persona, evidence: list[ContactEvidence]) -> float:
    base = _PERSONA_WEIGHT.get(persona, 10.0)
    evidence_bonus = min(len(evidence) * 10.0, 30.0)
    return min(base + evidence_bonus + _hiring_language_bonus(evidence), 100.0)


def _relevance_reason(persona: Persona, evidence: list[ContactEvidence]) -> str:
    persona_label = persona.replace("_", " ")
    parts = [f"{persona_label} persona match"]
    if len(evidence) > 1:
        parts.append(f"{len(evidence)} independent sources")
    if _hiring_language_bonus(evidence):
        parts.append("explicit hiring language found")
    return "; ".join(parts).capitalize() + "."


def extract_and_rank_candidates(
    raw: str, results: QueryResults, *, company: str
) -> list[ContactCandidate]:
    """Deterministic re-grounding and ranking (L2) applied to the LLM's
    raw extraction output (the one L1.5 LLM call this module makes --
    see the module docstring for why extraction itself isn't zero-LLM).
    Every returned name is re-checked against the evidence actually
    retrieved before it survives; candidates sharing a normalized name
    within this one run are merged into one, with multiple evidence rows.
    `company` is a required parameter, not read from `results` or patched
    on afterward -- the s154 lesson this module's own docstring names:
    pass values explicitly rather than threading them implicitly."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []

    hits_by_url: dict[str, SearchHit] = {}
    persona_by_url: dict[str, Persona] = {}
    for query, hits in results:
        for hit in hits:
            hits_by_url[hit["url"]] = hit
            persona_by_url[hit["url"]] = query["persona"]

    now_iso = datetime.now(UTC).isoformat()
    merged: dict[str, ContactCandidate] = {}

    for item in parsed:
        if not isinstance(item, dict):
            continue
        person_name = item.get("person_name")
        source_url = item.get("source_url")
        evidence_kind = item.get("evidence_kind")
        confidence = item.get("confidence")

        if not person_name or not isinstance(person_name, str):
            continue
        if source_url not in hits_by_url:
            continue
        hit = hits_by_url[source_url]
        if not _source_explicitly_names_candidate(person_name, hit["title"], hit["snippet"]):
            continue
        if confidence not in _CONFIDENCE_VALUES:
            confidence = "inferred"
        if not evidence_kind or not isinstance(evidence_kind, str):
            evidence_kind = "search_snippet"

        claimed_title = (
            item.get("claimed_title") if isinstance(item.get("claimed_title"), str) else None
        )
        claimed_team = (
            item.get("claimed_team") if isinstance(item.get("claimed_team"), str) else None
        )
        persona = persona_by_url.get(source_url, "senior_ic")

        evidence = ContactEvidence(
            source_url=source_url,
            source_title=hit["title"],
            source_snippet=hit["snippet"],
            observed_at=hit["published_at"] or now_iso,
            evidence_kind=evidence_kind,
            confidence=cast(Confidence, confidence),
        )

        # Merge on normalized name only, never on name+arbitrary text --
        # every query in a single run already targets one company (see
        # build_contact_query_plan), so a within-run name collision
        # across two different companies can't happen here. Proposal's
        # own "never merge two people merely because they share a name"
        # rule is about avoiding a false merge across DIFFERENT runs/
        # companies, which a fresh `merged` dict per call already can't do.
        key = _normalize_name(person_name)

        if key in merged:
            merged[key]["evidence"].append(evidence)
            if claimed_title and not merged[key]["claimed_title"]:
                merged[key]["claimed_title"] = claimed_title
            if claimed_team and not merged[key]["claimed_team"]:
                merged[key]["claimed_team"] = claimed_team
        else:
            merged[key] = ContactCandidate(
                person_name=person_name,
                claimed_title=claimed_title,
                claimed_team=claimed_team,
                company=company,
                persona=persona,
                relevance_reason="",
                priority_score=0.0,
                evidence=[evidence],
            )

    candidates = list(merged.values())
    for candidate in candidates:
        candidate["priority_score"] = _score_candidate(candidate["persona"], candidate["evidence"])
        candidate["relevance_reason"] = _relevance_reason(
            candidate["persona"], candidate["evidence"]
        )
    candidates.sort(key=lambda c: c["priority_score"], reverse=True)
    return candidates


async def find_contacts(
    company: str,
    results: QueryResults,
    *,
    llm_api_key: str,
    llm_model: str,
    llm_base_url: str | None,
    generate: LlmGenerate = llm_generate,
) -> list[ContactCandidate]:
    """The one LLM call this module makes, plus the deterministic
    re-grounding/ranking wrapped around it. Reads `company` from its own
    parameter, not from anywhere inside `results` -- the s154 lesson,
    applied here: never trust a value threaded implicitly through a
    prior stage's own data when it can be passed explicitly instead."""
    evidence_text = _format_evidence(results)
    if not evidence_text.strip():
        return []

    response = await generate(
        api_key=llm_api_key,
        model=llm_model,
        base_url=llm_base_url,
        system_prompt=_EXTRACTION_SYSTEM_PROMPT,
        user_prompt=f"Company: {company}\n\nEvidence:\n{evidence_text}",
        max_tokens=2000,
    )

    return extract_and_rank_candidates(response.content, results, company=company)
