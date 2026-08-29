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
  skill.
- "adjacent" if neither of those match, but the profile has OTHER skills
  in the same category -- there's a real, if indirect, connection.
- "unsupported" otherwise -- "renders as an honest gap and is never
  silently inserted into the document" (§24.5.4's own words). This
  module only classifies; enforcing "never inserted" is the generation
  layer's job, not this one's.
"""

from __future__ import annotations

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


def _evidence_text(profile: dict[str, Any]) -> str:
    """Every bullet/tech/skill string across experience and projects,
    lowercased and joined -- the same "flatten to one searchable string"
    approach tailor.py's `_fact_text` uses for career_facts, applied here
    directly to the profile's own canonical_json since skill evidence
    isn't scoped to a single fact."""
    parts: list[str] = []
    for entry in profile.get("experience") or []:
        parts.extend(str(b) for b in entry.get("bullets") or [])
        parts.extend(str(s) for s in entry.get("skills") or [])
    for entry in profile.get("projects") or []:
        parts.extend(str(b) for b in entry.get("bullets") or [])
        parts.extend(str(t) for t in entry.get("tech") or [])
    return " ".join(parts).lower()


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
    "prompt engineering": ["llm", "langchain", "genai", "large language models"],
    "agentic workflows": ["langchain", "llamaindex", "llm orchestration"],
    "responsible ai": ["bias mitigation", "model governance", "fairness"],
    "bias mitigation": ["responsible ai", "fairness", "model governance"],
}


def find_gap_bridge(cluster_keywords: list[str], profile: dict[str, Any]) -> str | None:
    """For a Step0 cluster's own keywords (a JD requirement with zero
    deterministic coverage), looks for a curated `GAP_ADJACENCY` entry
    whose bridge terms actually appear in the profile -- declared skills
    or evidence text, the same two sources `classify_skill` checks.
    Returns the first genuine match, or None. Never invents a connection:
    a gap with no curated entry, or an entry whose bridge terms don't
    actually appear anywhere, is honestly reported as no bridge, not
    forced into one."""
    skills_by_category = _profile_skill_texts(profile.get("skills") or {})
    all_declared = {s for values in skills_by_category.values() for s in values}
    evidence = _evidence_text(profile)

    for keyword in cluster_keywords:
        bridges = GAP_ADJACENCY.get(str(keyword).strip().lower())
        if not bridges:
            continue
        for bridge in bridges:
            if bridge in all_declared or bridge in evidence:
                return bridge
    return None


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

    evidence = _evidence_text(profile)
    if raw_lower in evidence or canonical_lower in evidence:
        return "supported"

    category = _category_for(requested_skill)
    if category is not None and skills_by_category.get(category):
        return "adjacent"

    return "unsupported"
