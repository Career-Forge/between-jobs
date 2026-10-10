"""The resume as content, ready to print: every field plain text, every choice made.

`assemble.py` builds one of these from the profile and the checked model output; `templates.py`
prints it. Nothing in it is LaTeX, and nothing in it came from a model without passing
`provenance.py` or being the candidate's own text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .plan import Density


@dataclass(slots=True)
class EntryBlock:
    """A job (or a volunteering role): two heading rows and bullets."""

    pointer: str
    primary: str
    place: str
    secondary: str
    dates: str
    bullets: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProjectBlock:
    pointer: str
    name: str
    url: str
    tech: str
    bullets: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EducationBlock:
    pointer: str
    institution: str
    place: str
    degree: str
    dates: str
    details: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ResumeDoc:
    name: str
    headline: str
    chips: list[dict[str, Any]]
    separator: str
    density: Density
    summary: str | None = None
    experience: list[EntryBlock] = field(default_factory=list)
    projects: list[ProjectBlock] = field(default_factory=list)
    education: list[EducationBlock] = field(default_factory=list)
    skills: list[tuple[str, str]] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    publications: list[str] = field(default_factory=list)
    patents: list[str] = field(default_factory=list)
    volunteering: list[EntryBlock] = field(default_factory=list)
    achievements: list[str] = field(default_factory=list)
    languages: str = ""
