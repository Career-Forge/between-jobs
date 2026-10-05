"""Skill canonicalization v0 (Sprint 3.3c) -- Proposal §24.5.4.

Deterministic only, platform-side, no LLM. A curated alias table maps raw
phrases ("LangChain", "GenAI") onto one canonical name per concept
("LLM Orchestration"), tagged with the same six skill categories
`profile.py`'s own `Skills` model already uses (`programming`, `ai_ml`,
`data_mlops`, `cloud_devops`, `tools`, `other`) -- reusing that taxonomy
rather than inventing a second one.

Genuinely "v0": a small, curated table, not an attempt at exhaustive
coverage. "Architecture open to ESCO/O*NET later" (capability map's own
words) -- widen this table as real gaps show up, not speculatively now.

The four evidence states (§24.5.4): a requested skill is
- "verified" if it (or an alias of it) is explicitly in the profile's own
  `skills` lists -- the user directly claimed it.
- "supported" if it doesn't appear in `skills` but its canonical form (or
  raw text) shows up in evidence text (experience/project bullets, tech
  stacks) -- the user has done it, even if they never listed it as a
  skill. It is a WHOLE-WORD match, never a substring: "excel" is not in
  "excellent", "react" not in "reaction", "spark" not in "sparkline". A
  curated skill name is matched exactly; a free-form phrase from a job
  description also matches its plural ("microservice" finds "microservices").
  A family skill (below) is also found under any of its alias spellings.
- "adjacent" if neither of those match, but there's a real, if indirect,
  connection. For a skill that belongs to a SKILL_FAMILY (Tableau/Power BI/
  Looker, Selenium/Cypress/Playwright, ...) that means the profile verifies
  or evidences a DIFFERENT member of the same family. For any other skill it
  is the original, weaker rule: the profile has OTHER skills in the same
  category. The category rule is deliberately not applied to family skills --
  a profile with only Git does not make Tableau "adjacent" -- nor to the few
  skills that are aliased only so their spellings merge (EXACT_ONLY_SKILLS):
  knowing Python does not make C# "adjacent".
- "unsupported" otherwise -- "renders as an honest gap and is never
  silently inserted into the document" (§24.5.4's own words). This
  module only classifies; enforcing "never inserted" is the generation
  layer's job, not this one's.
"""

from __future__ import annotations

import re
from typing import Any, Literal

SkillState = Literal["verified", "supported", "adjacent", "unsupported"]

SKILL_CATEGORIES = ("programming", "ai_ml", "data_mlops", "cloud_devops", "tools", "other")
"""Mirrors profile.py's `Skills` model field-for-field."""

# raw phrase (lowercased at lookup time) -> (canonical name, category).
# Deliberately small -- v0 curated coverage of common overlap points
# between how a JD phrases a requirement and how a resume phrases the
# same skill, not an attempt to enumerate every technology.
SKILL_ALIASES: dict[str, tuple[str, str]] = {
    "langchain": ("LLM Orchestration", "ai_ml"),
    "llamaindex": ("LLM Orchestration", "ai_ml"),
    "genai": ("Generative AI", "ai_ml"),
    "generative ai": ("Generative AI", "ai_ml"),
    "llm": ("Large Language Models", "ai_ml"),
    "llms": ("Large Language Models", "ai_ml"),
    "large language models": ("Large Language Models", "ai_ml"),
    "foundation models": ("Large Language Models", "ai_ml"),
    "rag": ("Retrieval-Augmented Generation", "ai_ml"),
    "retrieval augmented generation": ("Retrieval-Augmented Generation", "ai_ml"),
    "vector search": ("Vector Search", "ai_ml"),
    "vector database": ("Vector Search", "ai_ml"),
    "embeddings": ("Vector Search", "ai_ml"),
    "pytorch": ("PyTorch", "ai_ml"),
    "tensorflow": ("TensorFlow", "ai_ml"),
    "scikit-learn": ("Scikit-learn", "ai_ml"),
    "sklearn": ("Scikit-learn", "ai_ml"),
    "nlp": ("Natural Language Processing", "ai_ml"),
    "natural language processing": ("Natural Language Processing", "ai_ml"),
    "computer vision": ("Computer Vision", "ai_ml"),
    "cv": ("Computer Vision", "ai_ml"),
    "python3": ("Python", "programming"),
    "py": ("Python", "programming"),
    "typescript": ("TypeScript", "programming"),
    "ts": ("TypeScript", "programming"),
    "js": ("JavaScript", "programming"),
    "javascript": ("JavaScript", "programming"),
    "golang": ("Go", "programming"),
    "postgres": ("PostgreSQL", "data_mlops"),
    "postgresql": ("PostgreSQL", "data_mlops"),
    "psql": ("PostgreSQL", "data_mlops"),
    "etl": ("Data Pipelines", "data_mlops"),
    "data pipeline": ("Data Pipelines", "data_mlops"),
    "data pipelines": ("Data Pipelines", "data_mlops"),
    "airflow": ("Data Pipelines", "data_mlops"),
    "aws": ("AWS", "cloud_devops"),
    "amazon web services": ("AWS", "cloud_devops"),
    "gcp": ("Google Cloud Platform", "cloud_devops"),
    "google cloud": ("Google Cloud Platform", "cloud_devops"),
    "google cloud platform": ("Google Cloud Platform", "cloud_devops"),
    "azure": ("Microsoft Azure", "cloud_devops"),
    "k8s": ("Kubernetes", "cloud_devops"),
    "kubernetes": ("Kubernetes", "cloud_devops"),
    "docker": ("Docker", "cloud_devops"),
    "ci/cd": ("CI/CD", "cloud_devops"),
    "cicd": ("CI/CD", "cloud_devops"),
    "continuous integration": ("CI/CD", "cloud_devops"),
    "terraform": ("Infrastructure as Code", "cloud_devops"),
    "iac": ("Infrastructure as Code", "cloud_devops"),
    "infrastructure as code": ("Infrastructure as Code", "cloud_devops"),
    # Business intelligence tools. Analysts and BI developers list these under `tools`: the
    # profile schema has no better category, and the resume import prompt says so.
    "tableau": ("Tableau", "tools"),
    "power bi": ("Power BI", "tools"),
    "power-bi": ("Power BI", "tools"),
    "powerbi": ("Power BI", "tools"),
    "looker": ("Looker", "tools"),
    "qlik": ("Qlik", "tools"),
    "excel": ("Excel", "tools"),
    "ms excel": ("Excel", "tools"),
    "sql": ("SQL", "programming"),
    # Test automation and load testing.
    "selenium": ("Selenium", "tools"),
    "cypress": ("Cypress", "tools"),
    "playwright": ("Playwright", "tools"),
    "jmeter": ("JMeter", "tools"),
    "gatling": ("Gatling", "tools"),
    "locust": ("Locust", "tools"),
    "k6": ("k6", "tools"),
    # Front-end frameworks.
    "react": ("React", "tools"),
    "reactjs": ("React", "tools"),
    "react.js": ("React", "tools"),
    "vue": ("Vue", "tools"),
    "vuejs": ("Vue", "tools"),
    "vue.js": ("Vue", "tools"),
    "angular": ("Angular", "tools"),
    "next.js": ("Next.js", "tools"),
    "nextjs": ("Next.js", "tools"),
    "next js": ("Next.js", "tools"),
    # Data platform: warehouses, orchestration and transformation, processing, streaming.
    "snowflake": ("Snowflake", "data_mlops"),
    "bigquery": ("BigQuery", "data_mlops"),
    "big query": ("BigQuery", "data_mlops"),
    "redshift": ("Redshift", "data_mlops"),
    "amazon redshift": ("Redshift", "data_mlops"),
    "dbt": ("dbt", "data_mlops"),
    "dbt core": ("dbt", "data_mlops"),
    "dagster": ("Dagster", "data_mlops"),
    "prefect": ("Prefect", "data_mlops"),
    "spark": ("Spark", "data_mlops"),
    "apache spark": ("Spark", "data_mlops"),
    "pyspark": ("Spark", "data_mlops"),
    "kafka": ("Kafka", "data_mlops"),
    "apache kafka": ("Kafka", "data_mlops"),
    # Languages.
    "java": ("Java", "programming"),
    "kotlin": ("Kotlin", "programming"),
    "scala": ("Scala", "programming"),
    "c#": ("C#", "programming"),
    "csharp": ("C#", "programming"),
    "c sharp": ("C#", "programming"),
    # Observability and infrastructure as code (Terraform's own alias is above).
    "datadog": ("Datadog", "cloud_devops"),
    "new relic": ("New Relic", "cloud_devops"),
    "newrelic": ("New Relic", "cloud_devops"),
    "grafana": ("Grafana", "cloud_devops"),
    "prometheus": ("Prometheus", "cloud_devops"),
    "pulumi": ("Pulumi", "cloud_devops"),
    "cloudformation": ("CloudFormation", "cloud_devops"),
    "aws cloudformation": ("CloudFormation", "cloud_devops"),
    # Deep-learning framework (PyTorch and TensorFlow are above).
    "jax": ("JAX", "ai_ml"),
}

EXACT_ONLY_SKILLS = frozenset({"SQL", "Excel", "C#"})
"""Canonical names that are in SKILL_ALIASES only so their spellings merge ("MS Excel" and
"Excel", "csharp" and "C#"). They belong to no family and having a neighbour in their category
says nothing about them, so the category rule never makes them "adjacent": a Python-only
profile does not make C# adjacent, a Git-only profile does not make Excel adjacent."""

# Skill family: canonical skill name -> the family it belongs to. Two skills in one family are
# interchangeable enough that having used one is a real, if indirect, reason to say the other is
# "adjacent" -- and only that: nothing else makes a family skill adjacent (see classify_skill).
#
# A key is normally a canonical name from SKILL_ALIASES. Two are not, on purpose: "airflow" and
# "terraform" are alias PHRASES, because their canonical name ("Data Pipelines", "Infrastructure
# as Code") is a broader concept that other phrases share ("etl", "iac"). Putting the concept in
# a family would make "ETL" and "IaC" family skills too and take away the category rule they
# have always had. classify_skill looks a skill up by canonical name first, then by the phrase.
SKILL_FAMILIES: dict[str, str] = {
    "Tableau": "BI tools",
    "Power BI": "BI tools",
    "Looker": "BI tools",
    "Qlik": "BI tools",
    "Selenium": "browser test automation",
    "Cypress": "browser test automation",
    "Playwright": "browser test automation",
    "JMeter": "load testing",
    "Gatling": "load testing",
    "Locust": "load testing",
    "k6": "load testing",
    "React": "front-end frameworks",
    "Vue": "front-end frameworks",
    "Angular": "front-end frameworks",
    "Next.js": "front-end frameworks",
    "Snowflake": "cloud data warehouses",
    "BigQuery": "cloud data warehouses",
    "Redshift": "cloud data warehouses",
    "airflow": "pipeline orchestration",
    "dbt": "pipeline orchestration",
    "Dagster": "pipeline orchestration",
    "Prefect": "pipeline orchestration",
    "Spark": "data processing and streaming",
    "Kafka": "data processing and streaming",
    "Java": "JVM languages",
    "Kotlin": "JVM languages",
    "Scala": "JVM languages",
    "terraform": "infrastructure as code",
    "Pulumi": "infrastructure as code",
    "CloudFormation": "infrastructure as code",
    "Datadog": "observability",
    "New Relic": "observability",
    "Grafana": "observability",
    "Prometheus": "observability",
    "PyTorch": "ML frameworks",
    "TensorFlow": "ML frameworks",
    "JAX": "ML frameworks",
    # Scikit-learn is the classical-ML end of the same family: a profile with scikit-learn and no
    # deep-learning framework has long classified PyTorch as adjacent, and still does.
    "Scikit-learn": "ML frameworks",
}


def canonicalize_skill(raw: str) -> str:
    """The canonical display name for a raw skill phrase. Falls back to
    the input, title-cased, when no alias entry exists -- "unknown means
    labeled as unknown," not silently dropped or force-mapped to
    something close but wrong."""
    entry = SKILL_ALIASES.get(raw.strip().lower())
    if entry is not None:
        return entry[0]
    return raw.strip()


def _category_for(raw: str) -> str | None:
    entry = SKILL_ALIASES.get(raw.strip().lower())
    return entry[1] if entry is not None else None


def _profile_skill_texts(skills: dict[str, Any]) -> dict[str, list[str]]:
    """`{category: [lowercased skill strings]}` from a profile's `skills`
    object (profile.py's `Skills` shape -- a plain dict at this layer,
    the same "opaque dict crossing an API boundary" posture every other
    consumer of `canonical_json` in this codebase takes)."""
    return {
        category: [str(s).strip().lower() for s in (skills.get(category) or [])]
        for category in SKILL_CATEGORIES
    }


def _evidence_parts(profile: dict[str, Any]) -> tuple[list[str], list[str]]:
    """The profile's evidence as `(structured, free)`, every string lowercased.

    `structured` is what the person LISTED as a skill or technology: `experience[].skills` and
    `projects[].tech`. `free` is prose: experience and project bullets. The difference matters
    for a few skill names that are also ordinary words (see `AMBIGUOUS_TERMS`): a technology
    list is a trustworthy place for them, a sentence is not."""
    structured: list[str] = []
    free: list[str] = []
    for entry in profile.get("experience") or []:
        free.extend(str(b).lower() for b in entry.get("bullets") or [])
        structured.extend(str(s).lower() for s in entry.get("skills") or [])
    for entry in profile.get("projects") or []:
        free.extend(str(b).lower() for b in entry.get("bullets") or [])
        structured.extend(str(t).lower() for t in entry.get("tech") or [])
    return structured, free


AMBIGUOUS_TERMS = frozenset({"react", "cypress", "locust", "prefect", "angular", "scala"})
"""Skill names (lowercase) that are also ordinary English words or proper nouns: "react to
on-call alerts", "Cypress Semiconductor", "angular momentum", "a locust swarm", "a school
prefect", "La Scala". Found as a SIBLING skill or a gap BRIDGE, they count only in the structured
evidence (declared skills, `experience[].skills`, `projects[].tech`), never in a bullet --
otherwise one verb would make Vue, Angular and Next.js "adjacent" and hide the real gap."""


# JD-side term (lowercased) -> candidate profile-side terms that, if
# present, are a real if indirect bridge -- DELIBERATELY the opposite
# direction of a plain keyword match: compute_coverage's own substring
# search already catches "the profile literally says this term," so an
# entry here only earns its keep when the bridge terms are genuinely
# DIFFERENT words from the key. v0, small, honest -- widened as real gaps
# show up (S4, honest-score-surfaces.md), same discipline as SKILL_ALIASES.
GAP_ADJACENCY: dict[str, list[str]] = {
    "computer vision": ["opencv", "pil", "pillow", "image processing"],
    "image enhancement": ["opencv", "pil", "pillow", "computer vision"],
    "image processing": ["opencv", "pil", "pillow", "computer vision"],
    "kubernetes": ["docker", "container", "helm"],
    "container orchestration": ["docker", "kubernetes", "helm"],
    "distributed systems": ["kafka", "microservices", "message queue"],
    "mlops": ["docker", "airflow", "ci/cd", "kubernetes", "model deployment"],
    "model deployment": ["docker", "kubernetes", "fastapi", "mlops"],
    "model governance": ["mlops", "model deployment", "monitoring"],
    "vector search": ["embeddings", "rag", "faiss", "pinecone"],
    "vector database": ["embeddings", "rag", "faiss", "pinecone"],
    "prompt engineering": ["llm", "llms", "langchain", "genai", "large language models"],
    "agentic workflows": ["langchain", "llamaindex", "llm orchestration"],
    "responsible ai": ["bias mitigation", "model governance", "fairness"],
    "bias mitigation": ["responsible ai", "fairness", "model governance"],
    # Tool-for-tool bridges: a different product that does the same job.
    "tableau": ["power bi", "looker", "qlik"],
    "power bi": ["tableau", "looker", "qlik"],
    "selenium": ["cypress", "playwright", "webdriver"],
    "cypress": ["selenium", "playwright"],
    "react": ["vue", "angular", "next.js"],
    "snowflake": ["bigquery", "redshift", "databricks"],
    "airflow": ["dbt", "dagster", "prefect"],
    "java": ["kotlin", "scala"],
    "terraform": ["pulumi", "cloudformation"],
    "datadog": ["new relic", "grafana", "prometheus"],
    "jmeter": ["gatling", "locust", "k6"],
}

SUBSTRING_BRIDGE_STEMS = frozenset({"container", "docker"})
"""Bridge terms that are the STEM of the forms people write ("containerized", "dockerized",
"Dockerfile"), so they match as a substring of the evidence. Every other bridge matches as a
whole word: "rag" is not in "leveraged", "helm" not in "overwhelm", "prefect" not in
"prefecture", "looker" not in "onlooker"."""


def _mentions(escaped_phrase: str, text: str) -> bool:
    """Whole-word search: the phrase may not be glued to a letter or digit on either side
    ("java" is not in "javascript", "dbt" is not in "adbt")."""
    return re.search(rf"(?<![a-z0-9])(?:{escaped_phrase})(?![a-z0-9])", text) is not None


def _spellings(bridge: str) -> set[str]:
    """Every spelling of a bridge term: the term itself plus every alias whose canonical name IS
    that term ("power bi" -> "powerbi", "power-bi"). It is not looked up by the term's own
    canonical name, so a bridge that is an alias of a broader concept ("airflow" is "Data
    Pipelines", which "etl" shares) gets only its own spelling: an ETL mention is not Airflow."""
    spellings = {bridge}
    spellings.update(alias for alias, (name, _) in SKILL_ALIASES.items() if name.lower() == bridge)
    return spellings


def _bridge_mentioned(spelling: str, structured: str, everything: str) -> bool:
    if spelling in SUBSTRING_BRIDGE_STEMS:
        return spelling in everything
    if spelling in AMBIGUOUS_TERMS:
        return _mentions(re.escape(spelling), structured)
    return _mentions(re.escape(spelling), everything)


def find_gap_bridge(cluster_keywords: list[str], profile: dict[str, Any]) -> str | None:
    """For a Step0 cluster's own keywords (a JD requirement with zero
    deterministic coverage), looks for a curated `GAP_ADJACENCY` entry
    whose bridge terms actually appear in the profile -- declared skills
    or evidence text, the same two sources `classify_skill` checks.
    Returns the first genuine match, or None. Never invents a connection:
    a gap with no curated entry, or an entry whose bridge terms don't
    actually appear anywhere, is honestly reported as no bridge, not
    forced into one.

    Both sides are alias-aware, as `classify_skill` is: a keyword typed "PowerBI" finds the
    "power bi" entry, and a profile that says "Vue.js" or "Amazon Redshift" has the "vue" or
    "redshift" bridge. The bridge returned is always the curated string."""
    skills_by_category = _profile_skill_texts(profile.get("skills") or {})
    all_declared = {s for values in skills_by_category.values() for s in values}
    structured_evidence, free_evidence = _evidence_parts(profile)
    # a declared skill is as structured as a technology list: found as a whole word like one
    structured = "\n".join([*sorted(all_declared), *structured_evidence])
    everything = "\n".join([structured, *free_evidence])

    for keyword in cluster_keywords:
        raw = str(keyword).strip().lower()
        bridges = GAP_ADJACENCY.get(raw) or GAP_ADJACENCY.get(canonicalize_skill(raw).lower())
        if not bridges:
            continue
        for bridge in bridges:
            if any(_bridge_mentioned(sp, structured, everything) for sp in _spellings(bridge)):
                return bridge
    return None


def _family_key(raw_lower: str, canonical: str) -> str | None:
    """The `SKILL_FAMILIES` key a requested skill is filed under, or None if it belongs to no
    family. Canonical name first, then the raw phrase (see `SKILL_FAMILIES`)."""
    if canonical in SKILL_FAMILIES:
        return canonical
    if raw_lower in SKILL_FAMILIES:
        return raw_lower
    return None


def _member_phrases(key: str) -> set[str]:
    """Every spelling of a family member: its key plus every alias that canonicalizes to it. A
    phrase-keyed member ("terraform") has only its own phrase."""
    phrases = {key.lower()}
    phrases.update(alias for alias, (canonical, _) in SKILL_ALIASES.items() if canonical == key)
    return phrases


def _alternation(phrases: set[str]) -> str:
    return "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True))


def _member_patterns_by_family() -> dict[str, dict[str, tuple[str, str]]]:
    """`{family: {member key: (pattern found anywhere, pattern found in structured evidence
    only)}}`. The second is the member's ambiguous spellings (`AMBIGUOUS_TERMS`), if any."""
    by_family: dict[str, dict[str, tuple[str, str]]] = {}
    for key, family in SKILL_FAMILIES.items():
        phrases = _member_phrases(key)
        by_family.setdefault(family, {})[key] = (
            _alternation(phrases - AMBIGUOUS_TERMS),
            _alternation(phrases & AMBIGUOUS_TERMS),
        )
    return by_family


_FAMILY_MEMBER_PATTERNS = _member_patterns_by_family()


def _has_family_sibling(own_key: str, structured: str, free: str) -> bool:
    """True if the profile's evidence names a family member other than `own_key`, as a whole
    word. `structured` is its declared skills and technology lists, `free` its bullets
    (lowercased): a member's ambiguous spelling counts only in `structured`."""
    family = SKILL_FAMILIES[own_key]
    everything = f"{structured}\n{free}"
    for key, (anywhere, structured_only) in _FAMILY_MEMBER_PATTERNS[family].items():
        if key == own_key:
            continue
        if anywhere and _mentions(anywhere, everything):
            return True
        if structured_only and _mentions(structured_only, structured):
            return True
    return False


_CURATED_PHRASES = frozenset(SKILL_ALIASES) | {name.lower() for name, _ in SKILL_ALIASES.values()}


def _skill_in_evidence(
    raw_lower: str, canonical: str, family_key: str | None, evidence: str
) -> bool:
    """Whole-word search for the requested skill in the evidence text, under every spelling it
    has: what was asked for, its canonical name and, for a family skill (a product name) or an
    exact-only one, each of its aliases. Short aliases of other skills ("cv", "ts", "py") are
    NOT tried: "CV" in a bullet is not computer vision.

    A curated name is matched exactly ("excels" is not Excel, "reacts" is not React); a
    free-form phrase from a job description also matches its plural."""
    phrases = {raw_lower, canonical.lower()} - {""}
    spelling_key = family_key
    if spelling_key is None and canonical in EXACT_ONLY_SKILLS:
        spelling_key = canonical
    if spelling_key is not None:
        phrases |= _member_phrases(spelling_key)
    alternatives = [
        re.escape(p) if p in _CURATED_PHRASES else re.escape(p) + "s?"
        for p in sorted(phrases, key=len, reverse=True)
    ]
    return bool(alternatives) and _mentions("|".join(alternatives), evidence)


def classify_skill(requested_skill: str, profile: dict[str, Any]) -> SkillState:
    """`profile` is a profile_version's `canonical_json` (or anything
    with the same `skills`/`experience`/`projects` shape)."""
    canonical = canonicalize_skill(requested_skill)
    raw_lower = requested_skill.strip().lower()
    canonical_lower = canonical.lower()

    skills_by_category = _profile_skill_texts(profile.get("skills") or {})
    all_declared = [s for values in skills_by_category.values() for s in values]
    if raw_lower in all_declared or canonical_lower in all_declared:
        return "verified"
    if any(canonicalize_skill(s).lower() == canonical_lower for s in all_declared):
        return "verified"

    structured_evidence, free_evidence = _evidence_parts(profile)
    family_key = _family_key(raw_lower, canonical)
    if _skill_in_evidence(
        raw_lower, canonical, family_key, "\n".join([*structured_evidence, *free_evidence])
    ):
        return "supported"

    if family_key is not None:
        # A family skill is adjacent only through a sibling the profile really has; the weaker
        # category rule below does not apply to it.
        structured = "\n".join([*all_declared, *structured_evidence])
        free = "\n".join(free_evidence)
        return "adjacent" if _has_family_sibling(family_key, structured, free) else "unsupported"

    if canonical in EXACT_ONLY_SKILLS:
        return "unsupported"

    category = _category_for(requested_skill)
    if category is not None and skills_by_category.get(category):
        return "adjacent"

    return "unsupported"
