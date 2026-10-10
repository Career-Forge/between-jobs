"""A job posting, cleaned for a prompt, and what it asks for.

The posting is the one input in the whole engine that a stranger wrote, so it is treated as one:
control and invisible characters are removed, its length is capped, and it only ever reaches a
prompt as the text of a tagged section (`text.section`) that the prompts tell the model is data.
It never reaches a pointer, a document structure or a LaTeX command: the only parts of a posting
that appear in a document are its company name and title, and those go through `LatexText`
like any other text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from between_jobs.api.engine_contract import Step0Result

from .provenance import mentions, usable_job_terms
from .sources import Corpus
from .text import clean_block, clean_line

DESCRIPTION_CHARS = 6000
"""How much of a posting a prompt carries. A posting is mostly boilerplate after its first few
thousand characters; the cap bounds cost and the room a hostile posting has."""


@dataclass(frozen=True, slots=True)
class Job:
    """The facts of the posting a document may state, and its text for a prompt."""

    title: str
    company: str
    location: str
    description: str


def prepare_job(snapshot: dict[str, Any], *, description_chars: int = DESCRIPTION_CHARS) -> Job:
    return Job(
        title=clean_line(str(snapshot.get("title") or ""), 160),
        company=clean_line(str(snapshot.get("company_name") or ""), 120),
        location=clean_line(str(snapshot.get("location_text") or ""), 120),
        description=clean_block(str(snapshot.get("description_text") or ""), description_chars),
    )


@dataclass(frozen=True, slots=True)
class JobRead:
    """What the posting asks for, as far as the engine could tell. Empty means unknown."""

    must_have: tuple[str, ...] = ()
    nice_to_have: tuple[str, ...] = ()
    key_terms: tuple[str, ...] = ()
    terms: tuple[str, ...] = field(default=())
    """Every term worth matching a bullet against, most important first: the key terms and the
    clusters' keywords, plus the skills the candidate lists that the posting mentions."""

    @property
    def guarded(self) -> tuple[str, ...]:
        """The terms a rewrite may not bring in unless its source already has them."""
        return usable_job_terms(list(self.terms))

    @property
    def known(self) -> bool:
        return bool(self.must_have or self.nice_to_have or self.key_terms)


def listed_skills(corpus: Corpus) -> list[str]:
    template = corpus.template
    skills = template.skills
    declared = [
        *skills.programming,
        *skills.ai_ml,
        *skills.data_mlops,
        *skills.cloud_devops,
        *skills.tools,
        *skills.other,
    ]
    from_entries = [skill for job in template.experience for skill in job.skills] + [
        tech for project in template.projects for tech in project.tech
    ]
    return [clean_line(skill, 60) for skill in (*declared, *from_entries) if clean_line(skill)]


def combine_terms(corpus: Corpus, job: Job, step0: Step0Result | None) -> JobRead:
    """The read of a posting from the model's analysis (when there is one) plus the one signal
    that needs no model: which of the candidate's own skills the posting mentions."""
    description = job.description.lower() + " " + job.title.lower()
    ordered: dict[str, None] = {}
    must: list[str] = []
    nice: list[str] = []
    key_terms: list[str] = []
    if step0 is not None:
        for cluster in step0.clusters:
            (must if cluster.priority == "must_have" else nice).append(cluster.name)
            for keyword in cluster.keywords:
                ordered.setdefault(keyword.lower())
        key_terms = list(step0.key_terms)
        for term in key_terms:
            ordered.setdefault(term.lower())
    for skill in listed_skills(corpus):
        if mentions(skill.lower(), description):
            ordered.setdefault(skill.lower())
    return JobRead(
        must_have=tuple(must),
        nice_to_have=tuple(nice),
        key_terms=tuple(key_terms),
        terms=tuple(ordered),
    )
