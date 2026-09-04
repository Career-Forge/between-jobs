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
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal, NotRequired, TypedDict, cast

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
    provider: NotRequired[Literal["firecrawl"]]
    """Absent/None means "use whatever provider this run picked" (the
    existing You.com-primary/Firecrawl-fallback choice). "firecrawl"
    means this query is only ever safe to run on Firecrawl and must be
    skipped (with a warning), never silently run on You.com -- see the
    hiring-post query below."""


_TITLE_STOPWORDS = {
    "senior",
    "staff",
    "principal",
    "lead",
    "sr",
    "jr",
    "junior",
    "i",
    "ii",
    "iii",
    "iv",
    "the",
    "a",
    "an",
    "of",
    "and",
    "for",
}


def _extract_role_keywords(role_title: str, *, limit: int = 3) -> str:
    """Deterministic, no LLM. A short, UNQUOTED keyword phrase -- never
    the full exact title. The 2026-09-04 empirical trial found that
    quoting the exact job title as a strict phrase returns ZERO Firecrawl
    results the moment the title has any real specificity (a listing's
    own exact wording essentially never repeats anywhere else) -- the
    root cause of 3 of the 5 original queries being dead weight. Splits
    on the title's own punctuation (a dash, colon, or comma almost always
    separates the core role from a sub-specialization or level, e.g.
    "AI Engineer - Model Optimization & Acceleration" -> "AI Engineer"),
    then drops seniority/level words that add no retrieval signal."""
    head = re.split(r"[-:,\u2013\u2014]", role_title, maxsplit=1)[0]
    words = [w for w in re.split(r"[\s/&]+", head) if w]
    kept = [w for w in words if w.lower().strip(",.") not in _TITLE_STOPWORDS]
    if not kept:
        kept = words or role_title.split()
    return " ".join(kept[:limit])


def build_contact_query_plan(
    company: str, role_title: str, *, product_terms: list[str] | None = None
) -> list[ContactQuery]:
    """A fixed, bounded query plan, same discipline as
    `company_intel_pipeline.build_query_plan` ("the planner cannot
    generate an unbounded loop") -- covers Proposal §27.1's tiers 1-5.

    Rewritten 2026-09-04 (outreach-v2-search-first.md Phase G) off a real
    5-strategy empirical trial against the live AMD application: the
    original plan's control group returned 1 LinkedIn profile out of 47
    results (2%) because 3 of its 5 queries quoted the exact role title
    (see `_extract_role_keywords`) -- this is a replacement, not a tweak.
    Every query here is a *search-endpoint* query, including the
    `site:linkedin.com/...` ones -- reading only the snippet/title/URL
    the provider returns. That's the same treatment every other search
    result in this codebase already gets (identical to a human typing it
    into Google) and is explicitly distinct from ever pointing a
    scrape/crawl endpoint at linkedin.com, which this module never does.

    `product_terms` is Phase H's own output (a company's flagship
    software product + sub-area, extracted from the JD and Company
    Intel) -- not yet built, so it defaults to None here and the two
    product-anchored manager queries are simply omitted when it's empty.
    The trial confirmed the remaining four queries "still work" without
    it (the recruiter/TA/hiring-post/director-VP lane), so this is a
    graceful degrade, not a broken state."""
    role_keywords = _extract_role_keywords(role_title)
    queries: list[ContactQuery] = [
        ContactQuery(
            persona="recruiter",
            query=f"{company} Talent Acquisition LinkedIn profile",
        ),
        ContactQuery(
            persona="recruiter",
            query=f"site:linkedin.com/in {company} recruiter {role_keywords}",
        ),
        ContactQuery(
            persona="hiring_lead",
            query=(
                f"site:linkedin.com/posts {company} hiring"
                + (f" {product_terms[0]}" if product_terms else "")
            ),
            provider="firecrawl",
        ),
        ContactQuery(
            persona="senior_leader",
            query=f"{company} director VP engineering {role_keywords}",
        ),
    ]
    if product_terms:
        product_phrase = " ".join(product_terms[:2])
        queries.append(
            ContactQuery(
                persona="manager",
                query=f"{company} {product_phrase} engineering manager LinkedIn",
            )
        )
        queries.append(
            ContactQuery(
                persona="manager",
                query=f"{company} {product_phrase} team lead",
            )
        )
    return queries


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
    providers_used = [provider]
    for q in queries:
        # A query tagged provider="firecrawl" (the hiring-post lane) is
        # never safe to run on You.com -- the trial found it returns
        # stale/login-wall junk there. Skip rather than silently degrade.
        if q.get("provider") == "firecrawl":
            if firecrawl_key is None:
                warnings.append(f"{q['persona']}: needs Firecrawl, not configured -- skipped.")
                results.append((q, []))
                continue
            try:
                hits = await search_firecrawl(http, api_key=firecrawl_key, query=q["query"])
            except ApiError as e:
                warnings.append(f"{q['persona']}: {e.message}")
                hits = []
            if "firecrawl" not in providers_used:
                providers_used.append("firecrawl")
            results.append((q, hits))
            continue

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

    if github_org_slug:
        github_hits = await fetch_github_org_members(http, org_slug=github_org_slug)
        if github_hits:
            results.append(
                (ContactQuery(persona="senior_ic", query=f"github:{github_org_slug}"), github_hits)
            )
            providers_used.append("github")

    return results, providers_used, warnings


_LOGIN_WALL_MARKERS = (
    "sign in to linkedin",
    "log in or sign up",
    "join linkedin now",
    "javascript is not available",
    "join to view",
)

_HIRING_INTENT_TERMS = ("hiring", "my team", "join us", "looking for")
"""Deliberately narrow -- the trial found bare `hiring` (never a quoted
"we're hiring" phrase or an OR-list) is what the query itself already
filters for; this is a second, independent check on the snippet content
so a post that merely mentions the word in passing (an "AMD" fan account
retweeting someone else's hiring post) doesn't survive on query match
alone."""

_LINKEDIN_POST_MAX_AGE_DAYS = 365
"""No fixed cutoff came out of the trial itself -- deliberately generous
and disclosed rather than tuned: a hiring post over a year old is very
likely for a req that's since closed or been reposted under a new
activity id, but there's no live-verified data yet suggesting a tighter
number is safe."""

_LINKEDIN_SNOWFLAKE_EPOCH_MS = 1288834974657  # 2010-11-04, the LinkedIn/Twitter Snowflake epoch


def _linkedin_activity_id(url: str) -> int | None:
    """Extract the numeric id from a `.../activity-<id>-.../` LinkedIn
    post URL, if present."""
    match = re.search(r"activity[-:](\d{15,20})", url)
    return int(match.group(1)) if match else None


def _linkedin_post_age_days(url: str, *, now: datetime | None = None) -> float | None:
    """LinkedIn (like Twitter/Discord) mints post ids as Snowflake-style
    64-bit integers: the top 41 bits (`id >> 22`) are a millisecond
    timestamp since 2010-11-04. This is a real, zero-page-fetch
    freshness signal living entirely in the URL -- not an approximation
    from crawling anything."""
    activity_id = _linkedin_activity_id(url)
    if activity_id is None:
        return None
    posted_at_ms = (activity_id >> 22) + _LINKEDIN_SNOWFLAKE_EPOCH_MS
    posted_at = datetime.fromtimestamp(posted_at_ms / 1000, tz=UTC)
    reference = now or datetime.now(UTC)
    return (reference - posted_at).total_seconds() / 86400


def _is_brand_account_post(url: str, company: str) -> bool:
    """A `/posts/{slug}_.../` URL whose slug IS the company's own page
    handle is the company account re-sharing, not a person -- e.g.
    `/posts/amd_...` for AMD. Reuses `guess_github_org_slug`'s own
    alnum-slug normalization, checked against both the full company name
    and its first word (handles "Advanced Micro Devices" vs "AMD" style
    mismatches between the legal name and the LinkedIn page handle)."""
    match = re.search(r"linkedin\.com/posts/([a-z0-9-]+)[_-]", url.lower())
    if not match:
        return False
    slug = match.group(1).replace("-", "")
    if len(slug) <= 1:
        return False
    company_slug = guess_github_org_slug(company) or ""
    first_word = company.split()[0] if company.split() else ""
    first_word_slug = guess_github_org_slug(first_word) or ""
    return slug in (company_slug, first_word_slug)


def _is_login_wall_hit(hit: SearchHit) -> bool:
    haystack = f"{hit['title']} {hit['snippet']}".lower()
    return any(marker in haystack for marker in _LOGIN_WALL_MARKERS)


def _has_hiring_intent(hit: SearchHit) -> bool:
    haystack = f"{hit['title']} {hit['snippet']}".lower()
    return any(term in haystack for term in _HIRING_INTENT_TERMS)


def apply_l2_search_filters(
    results: QueryResults, *, company: str
) -> tuple[QueryResults, list[str]]:
    """Deterministic, no-LLM cleanup of the raw search hits BEFORE they
    are ever shown to the extraction LLM -- cheaper (fewer tokens) and
    safer (less noise for the LLM to hallucinate a person out of) than
    filtering only after extraction. Four guardrails the trial showed
    are needed specifically for the `site:linkedin.com/posts` hiring-post
    lane (~30-40% raw precision): a login-wall/placeholder drop, the
    brand-account-post drop, the hiring-intent snippet check, and the
    activity-id freshness cutoff. Also does URL-level dedupe across every
    query in the run, on top of the name-level merge
    `extract_and_rank_candidates` already does -- two different queries
    can and do return the same URL.

    Returns the filtered results plus a list of human-readable drop
    reasons for the run's own diagnostics (never surfaced to the end
    user, no PII beyond a URL that was already public)."""
    filtered: QueryResults = []
    seen_urls: set[str] = set()
    dropped: list[str] = []
    for query, hits in results:
        kept_hits: list[SearchHit] = []
        for hit in hits:
            if hit["url"] in seen_urls:
                continue
            if _is_login_wall_hit(hit):
                dropped.append(f"login-wall placeholder: {hit['url']}")
                continue
            if "linkedin.com/posts/" in hit["url"].lower():
                if _is_brand_account_post(hit["url"], company):
                    dropped.append(f"brand-account post: {hit['url']}")
                    continue
                if not _has_hiring_intent(hit):
                    dropped.append(f"no hiring-intent language: {hit['url']}")
                    continue
                age_days = _linkedin_post_age_days(hit["url"])
                if age_days is not None and age_days > _LINKEDIN_POST_MAX_AGE_DAYS:
                    dropped.append(f"stale post ({age_days:.0f}d old): {hit['url']}")
                    continue
            seen_urls.add(hit["url"])
            kept_hits.append(hit)
        filtered.append((query, kept_hits))
    return filtered, dropped


def _classify_evidence_kind(url: str) -> str:
    """Deterministic source-type classification from the URL alone --
    "deterministic code owns structure and shape; LLMs choose words
    only" applied to evidence_kind specifically: the LLM's own guess at
    this field is no longer trusted (see `extract_and_rank_candidates`),
    since the URL itself is a strictly more reliable signal for what kind
    of source this is."""
    u = url.lower()
    if "linkedin.com/in/" in u:
        return "linkedin_profile"
    if "linkedin.com/posts/" in u or "linkedin.com/pulse/" in u or "linkedin.com/feed/update" in u:
        return "linkedin_post"
    if "linkedin.com/company/" in u:
        return "company_page"
    if "github.com/" in u:
        return "github_membership"
    return "search_snippet"


_FORMER_EMPLOYMENT_MARKER_TEMPLATES = (
    "former {}",
    "formerly at {}",
    "previously at {}",
    "ex-{}",
    "no longer at {}",
    "no longer with {}",
    "left {}",
)


def _is_former_employee_evidence(evidence: list[ContactEvidence], company: str) -> bool:
    """Deterministic, no-LLM current-employer guard -- the same spirit as
    command-center's `classifyCurrentEmployerEvidence` (noted in the
    original Phase A research, never ported until now), applied at the
    evidence-text level since this module has no structured "current
    employer" field to check. Markers are anchored to the target
    `company` name specifically (both the full name and its first word,
    e.g. "AMD" for "Advanced Micro Devices") -- an unanchored "ex-" or
    "former" would false-positive on "ex-Google, now VP Eng at {company}"
    wording, which is actually a CURRENT-employee signal, not a drop.
    The trial's own ~80% current-employer precision on the
    `site:linkedin.com/in` recruiter query is what makes this check
    mandatory rather than a nice-to-have."""
    haystack = " ".join(f"{e['source_title']} {e['source_snippet']}".lower() for e in evidence)
    names = {company.lower()}
    if company.split():
        names.add(company.split()[0].lower())
    markers = [
        template.format(name) for name in names for template in _FORMER_EMPLOYMENT_MARKER_TEMPLATES
    ]
    return any(marker in haystack for marker in markers)


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
        # evidence_kind is classified deterministically from the URL, not
        # trusted from the LLM's own guess -- "deterministic code owns
        # structure and shape; LLMs choose words only."
        evidence_kind = _classify_evidence_kind(source_url)

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

    candidates = [
        c for c in merged.values() if not _is_former_employee_evidence(c["evidence"], company)
    ]
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


_PRODUCT_TERM_PATTERN = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:\.[A-Za-z0-9]+)?\b")

_PRODUCT_TERM_STOPWORDS = {
    "the",
    "a",
    "an",
    "we",
    "our",
    "you",
    "your",
    "this",
    "that",
    "these",
    "those",
    "team",
    "teams",
    "engineering",
    "engineer",
    "engineers",
    "join",
    "role",
    "position",
    "company",
    "job",
    "work",
    "remote",
    "hybrid",
    "onsite",
    "senior",
    "staff",
    "principal",
    "lead",
    "who",
    "what",
    "why",
    "how",
    "if",
    "and",
    "or",
    "for",
    "with",
    "about",
    "us",
    # Common JD-prose sentence-openers -- a bare capitalized word at the
    # start of a sentence is capitalized purely by English orthography,
    # not because it names anything. No positional sentence-boundary
    # detection exists here (a review pass confirmed the module's own
    # docstring previously overclaimed one) -- this is a disclosed,
    # pragmatic mitigation against a heuristic's known noise, not a
    # structural fix; a genuine product name is still a real word that
    # can happen to open a sentence too and isn't specifically guarded
    # against here.
    "passionate",
    "excited",
    "motivated",
    "committed",
    "proven",
    "strong",
    "excellent",
    "great",
    "looking",
    "seeking",
    "here",
    "today",
    "now",
    "as",
    "at",
    "in",
    "on",
    "to",
    "do",
    "does",
    "did",
    "have",
    "has",
    "had",
    "will",
    "would",
    "could",
    "should",
    "must",
    "can",
    "please",
    "note",
}

_MAX_PRODUCT_TERM_CANDIDATES = 30
"""Bounded on purpose, same "the planner cannot generate an unbounded
loop" discipline as `company_intel_pipeline.build_query_plan` -- a long
JD plus several Company Intel claims could otherwise hand the picker
prompt an unbounded token cost."""


def extract_product_term_candidates(company: str, *texts: str | None) -> list[str]:
    """Deterministic, no LLM (outreach-v2-search-first.md Phase H).
    Builds the bounded candidate list `pick_product_terms` is only ever
    allowed to choose from -- a real per-request candidate SET built
    fresh from this company's own JD text and Company Intel claims,
    mirroring `company_intel_pipeline._parse_claims`'s own `hits_by_url`
    membership-check pattern rather than a fixed global enum like
    `_CATEGORIES`: Phase H's candidates are inherently per-company, not a
    known-in-advance vocabulary.

    Matches capitalized/technical-looking tokens -- a real product or
    platform name is almost always capitalized ("ROCm.AI", "PyTorch",
    "Kubernetes"), including one embedded dot-suffix ("ROCm.AI" is a
    single token, not two). Drops the company's own name (never its own
    product) and a stopword list of generic capitalized words (common
    JD-prose sentence-openers, level/role words) that carry no product
    signal -- a heuristic mitigation, not real sentence-boundary
    detection (see `_PRODUCT_TERM_STOPWORDS`).

    Collects candidates ROUND-ROBIN across `texts` (one term per source
    per pass) rather than draining each source in order before moving to
    the next -- an adversarial review caught that the naive "accumulate
    in order, slice at the end" approach let a long JD (processed first,
    per its call site in contact_research_routes.py) silently fill the
    entire bounded budget before a later, higher-signal Company Intel
    claim was ever considered. Real JDs commonly carry 30+ unique
    capitalized tokens across headers, tool names, and requirements/
    benefits sections -- round-robin guarantees every source gets a fair
    share of the budget regardless of its own length."""
    excluded = {company.lower()}
    if company.split():
        excluded.add(company.split()[0].lower())

    per_source_terms: list[list[str]] = []
    for text in texts:
        if not text:
            continue
        seen_in_source: set[str] = set()
        source_terms: list[str] = []
        for match in _PRODUCT_TERM_PATTERN.finditer(text):
            term = match.group(0)
            key = term.lower()
            if len(term) < 3 or key in excluded or key in _PRODUCT_TERM_STOPWORDS:
                continue
            if key in seen_in_source:
                continue
            seen_in_source.add(key)
            source_terms.append(term)
        if source_terms:
            per_source_terms.append(source_terms)

    candidates: dict[str, None] = {}
    round_index = 0
    while len(candidates) < _MAX_PRODUCT_TERM_CANDIDATES and any(
        round_index < len(terms) for terms in per_source_terms
    ):
        for terms in per_source_terms:
            if round_index < len(terms):
                candidates.setdefault(terms[round_index], None)
                if len(candidates) >= _MAX_PRODUCT_TERM_CANDIDATES:
                    break
        round_index += 1

    return list(candidates)[:_MAX_PRODUCT_TERM_CANDIDATES]


_PRODUCT_TERM_SYSTEM_PROMPT = """You are given a bounded list of candidate terms extracted from a \
job posting and company research. Pick UP TO 2 terms from this exact list that best represent the \
company's flagship SOFTWARE product or platform and its most relevant sub-area for this specific \
role -- never a hardware-only product, a generic buzzword, or a team/department name.

Rules:
- Only return terms that appear VERBATIM in the candidate list below -- never invent a term, never \
paraphrase or combine one.
- If nothing in the list clearly names a software product or platform, return an empty array.

Return ONLY a JSON array of strings, no prose, no markdown code fences."""


async def pick_product_terms(
    candidates: list[str],
    *,
    llm_api_key: str,
    llm_model: str,
    llm_base_url: str | None,
    generate: LlmGenerate = llm_generate,
) -> list[str]:
    """The one bounded LLM call Phase H makes -- skipped entirely (zero
    cost) when there's nothing to pick from, since `build_contact_query_
    plan` already degrades cleanly to its 4-query core without product
    terms. Deterministic re-validation mirrors `company_intel_pipeline.
    _parse_claims`'s own `hits_by_url` check: every returned term must be
    an exact member of the candidate list actually offered -- the LLM's
    own say-so is never trusted on its own, "LLM decides words, never
    shape.\""""
    if not candidates:
        return []

    response = await generate(
        api_key=llm_api_key,
        model=llm_model,
        base_url=llm_base_url,
        system_prompt=_PRODUCT_TERM_SYSTEM_PROMPT,
        user_prompt="Candidate terms:\n" + "\n".join(candidates),
        max_tokens=200,
    )

    text = response.content.strip()
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

    candidate_set = set(candidates)
    picked: list[str] = []
    for item in parsed:
        if not isinstance(item, str):
            continue
        # Trimmed before the membership check -- a real, if less common,
        # LLM formatting artifact (incidental whitespace inside the JSON
        # string value) shouldn't silently fail an otherwise-correct,
        # genuinely-verbatim pick. Never invents or normalizes the term
        # itself, only strips surrounding whitespace.
        term = item.strip()
        if term in candidate_set and term not in picked:
            picked.append(term)
    return picked[:2]
