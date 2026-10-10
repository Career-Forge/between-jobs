"""The profile as pointers, and the text each pointer may be written from.

A candidate's profile (`ResumeTemplate`, validated by `api/profile.py`) is the only source of
fact the engine has. This module reads it once into `Source`s: one per experience or project
entry, addressed by the same pointer scheme `api/profile.py` uses for its career facts
(`/experience/3`, `/projects/0`). A bullet is addressed by its index inside its entry
(`/experience/3` bullet 2), which is all a model is ever asked to return in place of a fact.

Each source says which text it allows a rewrite to lean on (`Source.context`: the entry's own
skills, tools, metrics, title, company and place), and `Corpus.profile_text` is everything a
summary or cover letter may draw on across the whole profile. Neither includes dates (they are
printed from the profile and never written by a model, and a date's digits must not make a bare
"6" or "2019" in a bullet look supported), contact details, date of birth, nationality, marital
status, a photo or the work-authorization text: those never enter a prompt, and never become
a claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from between_jobs.api.errors import ApiError
from between_jobs.api.profile import ResumeTemplate

from .text import clean_line

SourceKind = Literal["experience", "project"]

MAX_BULLET_CHARS = 320
"""A bullet longer than this is clipped before a prompt carries it; the document itself always
uses the candidate's text in full when it falls back to it."""


@dataclass(frozen=True, slots=True)
class Source:
    """One experience or project entry: where its bullets are and what may support a rewrite."""

    pointer: str
    kind: SourceKind
    label: str
    bullets: tuple[str, ...]
    context: tuple[str, ...]
    pinned: bool = False
    min_bullets: int | None = None

    @property
    def index(self) -> int:
        return int(self.pointer.rsplit("/", 1)[1])

    def support_for(self, bullet_indices: list[int]) -> list[str]:
        """The text a rewrite of `bullet_indices` may lean on: those bullets and the entry's
        own context. Never another entry's: a skill used in one job is not claimed for another."""
        return [self.bullets[i] for i in bullet_indices] + list(self.context)


@dataclass(frozen=True, slots=True)
class Corpus:
    template: ResumeTemplate
    experience: tuple[Source, ...]
    projects: tuple[Source, ...]
    by_pointer: dict[str, Source]
    profile_text: str
    """Everything in the profile a summary or letter may state, one fact per line (no dates)."""

    @property
    def canonical_json(self) -> dict[str, Any]:
        return self.template.model_dump(mode="json")


def _nonempty(values: list[str]) -> list[str]:
    return [clean_line(value) for value in values if clean_line(value)]


def build_corpus(canonical_json: dict[str, Any]) -> Corpus:
    """Validates the profile and indexes it. A profile that does not validate is the caller's
    to fix (a stored profile always does); the error names the fields, never their values."""
    try:
        template = ResumeTemplate.model_validate(canonical_json)
    except ValidationError as e:
        fields = sorted({".".join(str(part) for part in err["loc"]) for err in e.errors()})
        raise ApiError(
            "INVALID_INPUT",
            "The resume profile isn't valid, so nothing could be written from it. "
            f"Check these fields: {', '.join(fields[:8]) or 'the top level'}.",
        ) from e

    experience: list[Source] = []
    for index, job in enumerate(template.experience):
        experience.append(
            Source(
                pointer=f"/experience/{index}",
                kind="experience",
                label=clean_line(f"{job.title} at {job.company}"),
                bullets=tuple(_nonempty(job.bullets)),
                context=tuple(
                    _nonempty(
                        [
                            job.title,
                            job.company,
                            job.location,
                            *job.skills,
                            *job.metrics,
                        ]
                    )
                ),
                pinned=bool(job.pin and job.pin.mandatory),
                min_bullets=job.pin.min_bullets if job.pin and job.pin.mandatory else None,
            )
        )
    projects: list[Source] = []
    for index, project in enumerate(template.projects):
        projects.append(
            Source(
                pointer=f"/projects/{index}",
                kind="project",
                label=clean_line(project.name),
                bullets=tuple(_nonempty(project.bullets)),
                context=tuple(_nonempty([project.name, *project.tech, *project.metrics])),
                pinned=bool(project.pin and project.pin.mandatory),
                min_bullets=project.pin.min_bullets
                if project.pin and project.pin.mandatory
                else None,
            )
        )
    by_pointer = {source.pointer: source for source in (*experience, *projects)}
    return Corpus(
        template=template,
        experience=tuple(experience),
        projects=tuple(projects),
        by_pointer=by_pointer,
        profile_text=_profile_text(template, experience, projects),
    )


def _profile_text(
    template: ResumeTemplate, experience: list[Source], projects: list[Source]
) -> str:
    lines: list[str] = [template.personal.headline, *template.summary_bullets]
    lines.extend(template.achievements)
    for source in (*experience, *projects):
        lines.extend(source.context)
        lines.extend(source.bullets)
    for edu in template.education:
        lines.extend([edu.degree, edu.field, edu.institution, edu.gpa, *edu.coursework])
    skills = template.skills
    for group in (
        skills.programming,
        skills.ai_ml,
        skills.data_mlops,
        skills.cloud_devops,
        skills.tools,
        skills.other,
    ):
        lines.extend(group)
    for cert in template.certifications:
        lines.extend([cert.name, cert.issuer])
    for pub in template.publications:
        lines.extend([pub.title, pub.venue])
    for patent in template.patents:
        lines.extend([patent.title, patent.status])
    for language in template.languages:
        lines.extend([language.language, language.fluency])
    for volunteer in template.volunteering:
        lines.extend([volunteer.organization, volunteer.role, *volunteer.bullets])
    return "\n".join(line for line in (clean_line(raw) for raw in lines) if line)
