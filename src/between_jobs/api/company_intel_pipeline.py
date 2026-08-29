"""Company intelligence pipeline (Horizon Sprint 5.0) -- Proposal §26.1,
scoped to Stage 1 (role fingerprint), Stage 2 (query plan), Stage 3
(broad retrieval), and Stage 7 (synthesis) only. Stage 4 (Firecrawl
selective full-page crawl) and Stages 5-6 (candidate extraction/identity
resolution/evidence graph) are §27's contact-finding territory -- this
module never resolves or stores a named individual, only company-level
facts (product, funding, hiring activity, culture, interview process,
news), each claim carrying the real source URL it came from.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

import httpx

from .errors import ApiError
from .llm_client import LLMResponse
from .llm_client import generate as llm_generate
from .research_clients import SearchHit, search_firecrawl, search_you_com

_CATEGORIES = (
    "product_and_mission",
    "team_and_technical_direction",
    "funding_and_financial_health",
    "hiring_activity",
    "layoffs_and_risk",
    "culture_and_values",
    "interview_process",
    "relevant_news",
)

LlmGenerate = Callable[..., Awaitable[LLMResponse]]


class RoleFingerprint(TypedDict):
    """Stage 1 -- deterministic from the immutable job snapshot only, no
    LLM, no parsing of unstructured JD text into a controlled taxonomy.
    Proposal §26.1's own fingerprint example also has `role_family`,
    `level`, `teams`, `hiring_signals` -- extracting those needs either
    an LLM call (which Stage 1 is supposed to be free of) or a keyword
    taxonomy this v0 doesn't build. A real, stated scope cut, not
    silently guessed at."""

    company: str
    title: str
    location: str | None


def build_role_fingerprint(job_snapshot: dict[str, Any]) -> RoleFingerprint:
    return RoleFingerprint(
        company=job_snapshot["company_name"],
        title=job_snapshot["title"],
        location=job_snapshot.get("location_text"),
    )


class ResearchQuery(TypedDict):
    purpose: str
    query: str


def build_query_plan(fingerprint: RoleFingerprint) -> list[ResearchQuery]:
    """Stage 2 -- a fixed, bounded set of company-level query templates
    (Proposal §26.1: "The planner cannot generate an unbounded loop").
    Deliberately excludes §26.1's own team/recruiter/hiring-manager query
    families ("exact requisition and recruiter", "team hiring manager",
    etc.) -- those exist to find named people, §27's job, not this
    module's."""
    company = fingerprint["company"]
    return [
        ResearchQuery(
            purpose="product_and_mission", query=f"{company} company overview mission product"
        ),
        ResearchQuery(
            purpose="funding_and_financial_health",
            query=f"{company} funding valuation financial health",
        ),
        ResearchQuery(purpose="hiring_activity", query=f"{company} hiring growth 2026"),
        ResearchQuery(purpose="layoffs_and_risk", query=f"{company} layoffs restructuring"),
        ResearchQuery(purpose="culture_and_values", query=f"{company} engineering culture values"),
        ResearchQuery(purpose="interview_process", query=f"{company} interview process experience"),
        ResearchQuery(purpose="relevant_news", query=f"{company} news"),
    ]


QueryResults = list[tuple[ResearchQuery, list[SearchHit]]]


async def run_research(
    http: httpx.AsyncClient,
    queries: list[ResearchQuery],
    *,
    you_com_key: str | None,
    firecrawl_key: str | None,
) -> tuple[QueryResults, list[str], list[str]]:
    """Stage 3 -- runs each query against whichever provider is
    available, per §26.1's own "Provider failure behavior" table: You.com
    is primary when present (broad discovery is its stated role);
    Firecrawl search is the fallback when You.com isn't configured.
    Returns (per-query hits, providers actually used, warnings) --  one
    failed query becomes a warning, not a reason to abandon the whole
    dossier."""
    if you_com_key is None and firecrawl_key is None:
        raise ApiError(
            "SETUP_REQUIRED",
            "Company intel needs a You.com or Firecrawl key configured.",
            capability="company_intel",
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
            warnings.append(f"{q['purpose']}: {e.message}")
            hits = []
        results.append((q, hits))
    return results, [provider], warnings


class Claim(TypedDict):
    category: str
    claim_text: str
    source_url: str
    source_title: str | None
    confidence: str


_SYNTHESIS_SYSTEM_PROMPT = """You are a company-research synthesizer. You are given search \
snippets about a company, each with a source URL. Produce a JSON array of factual claims about \
the company, each citing exactly one of the provided URLs. Never invent a fact, a source, or a \
URL that wasn't given to you. If the evidence doesn't support a category, omit it -- do not guess \
or pad the output to cover every category.

Each claim object must have exactly these fields:
- "category": one of {categories}
- "claim_text": one sentence, factual, no speculation
- "source_url": must be one of the URLs given in the evidence below, verbatim
- "confidence": "high" if multiple sources agree or the source is authoritative, "medium" if one \
decent source, "low" if the source is thin or old

Return ONLY the JSON array, no prose, no markdown code fences."""


def _format_evidence(results: QueryResults) -> str:
    lines = []
    for query, hits in results:
        for hit in hits:
            lines.append(f"[{query['purpose']}] {hit['title']} -- {hit['url']}\n{hit['snippet']}")
    return "\n\n".join(lines)


async def synthesize_dossier(
    fingerprint: RoleFingerprint,
    results: QueryResults,
    *,
    llm_api_key: str,
    llm_model: str,
    llm_base_url: str | None,
    generate: LlmGenerate = llm_generate,
) -> list[Claim]:
    """Stage 7 -- one LLM call, given the fingerprint plus the bounded
    evidence gathered in Stage 3. `_parse_claims` re-validates every
    returned claim's `source_url` against the evidence actually
    retrieved -- the prompt asks the model not to invent a source, but
    this is the deterministic backstop that actually enforces it,
    matching "LLM decides words, never shape.\""""
    evidence = _format_evidence(results)
    if not evidence.strip():
        return []

    hits_by_url = {hit["url"]: hit for _q, hits in results for hit in hits}

    response = await generate(
        api_key=llm_api_key,
        model=llm_model,
        base_url=llm_base_url,
        system_prompt=_SYNTHESIS_SYSTEM_PROMPT.format(categories=", ".join(_CATEGORIES)),
        user_prompt=(
            f"Company: {fingerprint['company']}\nRole: {fingerprint['title']}\n\n"
            f"Evidence:\n{evidence}"
        ),
        max_tokens=2000,
    )

    return _parse_claims(response.content, hits_by_url)


def _parse_claims(raw: str, hits_by_url: dict[str, SearchHit]) -> list[Claim]:
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

    claims: list[Claim] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        category = item.get("category")
        claim_text = item.get("claim_text")
        source_url = item.get("source_url")
        confidence = item.get("confidence")

        if category not in _CATEGORIES:
            continue
        if not claim_text or not isinstance(claim_text, str):
            continue
        if source_url not in hits_by_url:
            continue
        if confidence not in ("high", "medium", "low"):
            confidence = "medium"

        claims.append(
            Claim(
                category=category,
                claim_text=claim_text,
                source_url=source_url,
                source_title=hits_by_url[source_url]["title"] or None,
                confidence=confidence,
            )
        )
    return claims
