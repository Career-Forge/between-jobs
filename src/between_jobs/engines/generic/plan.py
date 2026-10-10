"""How much fits, and which entries are kept: decided from counts, before and after the model.

Nothing here measures glyphs. A document's size is estimated in *lines*: each bullet costs the
number of full text lines its characters need at the template's line width, each heading and
section title a fixed number, and a page holds a fixed number of lines (`LayoutModel`). The
constants are conservative and were checked by compiling real documents through the PDF
renderer (tests/test_generic_engine_compile.py), but they are estimates: the checklist's page
count on the compiled PDF is the authority, and the shape report says only what code decided
(`target_pages`, whether pins were honored), never a fill percentage nobody measured.

Pins (`pin` on an entry of the profile) are satisfied here, by code: a pinned entry is always
kept and always given at least its `min_bullets`, whatever the model proposed and whatever the
budget says. A pin can run the document past its page target; it never loses to the budget.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from between_jobs.api.profile import Experience, ResumeTemplate

from .job import JobRead
from .provenance import mentions
from .sources import Corpus, Source

Density = Literal["compact", "balanced", "spacious"]

_YEAR_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@dataclass(frozen=True, slots=True)
class LayoutModel:
    """The size of things in the resume template, in text lines, for one density."""

    lines_per_page: float
    chars_per_line: int
    bullet_gap: float
    section_title: float
    experience_heading: float
    project_heading: float
    education_entry: float
    header: float

    def bullet(self, text: str) -> float:
        """A bullet: its lines at the indented width, plus the gap between bullets."""
        return self.wrapped(text, indented=True) + self.bullet_gap

    def wrapped(self, text: str, *, indented: bool = False) -> int:
        """How many lines `text` takes: words are placed greedily, a line holding at most
        `chars_per_line` characters (a little fewer when indented, as a bullet is). It counts
        characters, not glyph widths, so it is the same on every machine."""
        width = self.chars_per_line - (INDENT_CHARS if indented else 0)
        lines, current = 1, 0
        for word in text.split():
            needed = len(word) + (1 if current else 0)
            if current and current + needed > width:
                lines += 1
                current = len(word)
            else:
                current += needed
        return lines


INDENT_CHARS = 4
"""How many characters of a line a bullet's indent takes."""

LAYOUTS: dict[Density, LayoutModel] = {
    # A page of the template is 60 lines of text at 10 pt; what differs between densities is the
    # space around sections, entries and bullets, in fractions of a line.
    "compact": LayoutModel(60, 100, 0.0, 1.7, 2.17, 1.17, 2.0, 2.7),
    "balanced": LayoutModel(60, 100, 0.08, 2.0, 2.33, 1.33, 2.0, 2.7),
    "spacious": LayoutModel(60, 100, 0.21, 2.33, 2.5, 1.5, 2.0, 2.7),
}

HEADLINE_LINES = 1.1
"""What the headline under the name adds to the header."""

PAGE_SLACK = 1.5
"""Lines left unused on a page, so an estimate that is a little low does not push a line onto a
new page."""

EXPERIENCE_QUOTA = {1: (5, 4, 3, 3), 2: (6, 5, 4, 4, 3, 3, 3)}
"""The most bullets an experience entry may show, by its position among the kept entries."""
MAX_EXPERIENCE = {1: 4, 2: 7}
MAX_PROJECTS = {1: 2, 2: 4}
PROJECT_QUOTA = {1: 2, 2: 3}
MAX_EDUCATION = 3
MAX_SKILLS = {1: 30, 2: 52}
FLOOR_BULLETS = 2
"""Every kept entry shows at least this many bullets (or all it has), unless a pin asks for
more. The model chooses between the floor and the entry's quota."""
PROJECT_CANDIDATES = 8
"""How many projects are offered to the model to choose from."""
MAX_OFFERED_BULLETS = 10


@dataclass(frozen=True, slots=True)
class OfferedEntry:
    """An entry the model may use: which of its bullets it is shown (`shown`, numbers into
    `Source.bullets`, the most relevant `MAX_OFFERED_BULLETS` at most, in the entry's order) and
    how many it may give (`minimum` and `maximum`, counts within what is shown)."""

    source: Source
    minimum: int
    maximum: int
    shown: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class Shape:
    pages: int
    pages_source: Literal["override", "auto"]
    density: Density

    @property
    def layout(self) -> LayoutModel:
        return LAYOUTS[self.density]

    @property
    def budget(self) -> float:
        return self.pages * self.layout.lines_per_page - PAGE_SLACK


@dataclass(frozen=True, slots=True)
class Plan:
    shape: Shape
    experience: tuple[OfferedEntry, ...]
    """The entries kept, in the order the document shows them (the profile's own order)."""
    projects: tuple[OfferedEntry, ...]
    """The projects the model chooses among, most relevant first."""
    project_limit: int


def _month_index(value: str, now: datetime) -> int | None:
    text = value.strip().lower()
    if text == "present":
        return now.year * 12 + now.month - 1
    try:
        year, month = text.split("-")
        return int(year) * 12 + int(month) - 1
    except ValueError:
        return None


def start_key(job: Experience) -> str:
    """Sorts jobs by when they started: `YYYY-MM` as it is; anything else ("present", which the
    profile allows in a start date) as the latest, so it never reads as the oldest."""
    return job.start_date if _YEAR_MONTH.match(job.start_date) else "9999-99"


def most_recent(entries: list[Source], template: ResumeTemplate) -> Source | None:
    """The job a person is in now or left last: current ones first, then the latest start."""
    if not entries:
        return None
    return max(
        entries,
        key=lambda s: (
            template.experience[s.index].is_current,
            start_key(template.experience[s.index]),
            -s.index,
        ),
    )


def career_years(corpus: Corpus, now: datetime) -> float:
    """Years from the earliest experience start to the latest end (or today). Zero when the
    profile has no dated experience: unknown is not a number of years."""
    starts: list[int] = []
    ends: list[int] = []
    for job in corpus.template.experience:
        start = _month_index(job.start_date, now)
        end = _month_index("present" if job.is_current else job.end_date, now)
        if start is not None and end is not None and end >= start:
            starts.append(start)
            ends.append(end)
    return (max(ends) - min(starts)) / 12 if starts else 0.0


def choose_shape(
    corpus: Corpus, page_count_override: int | None, density: Density, now: datetime | None
) -> Shape:
    """One page for most people; two for ten or more years of dated experience or six or more
    jobs. A page count the person chose always wins."""
    if page_count_override in (1, 2):
        return Shape(page_count_override, "override", density)
    years = career_years(corpus, now or datetime.now(UTC))
    long_career = years >= 10 or len(corpus.experience) >= 6
    return Shape(2 if long_career else 1, "auto", density)


def relevance(source: Source, read: JobRead) -> float:
    """How many of the job's terms an entry's own text mentions: the entry's context counts
    more than a single bullet, a bullet with a number a little more than one without. Pure
    counting, so it is the same on every run."""
    text = " ".join([*source.context, *source.bullets]).lower()
    score = 0.0
    for rank, term in enumerate(read.terms):
        if mentions(term, text):
            score += 3.0 + max(0.0, 2.0 - rank / 10)
    score += 0.2 * sum(1 for bullet in source.bullets if any(c.isdigit() for c in bullet))
    return score


def bullet_relevance(text: str, read: JobRead) -> float:
    lowered = text.lower()
    score = sum(3.0 for term in read.terms if mentions(term, lowered))
    return score + (1.0 if any(c.isdigit() for c in text) else 0.0)


def rank_bullets(source: Source, read: JobRead) -> list[int]:
    """Bullet indices of `source`, most relevant first; ties keep the candidate's own order."""
    return sorted(
        range(len(source.bullets)),
        key=lambda i: (-bullet_relevance(source.bullets[i], read), i),
    )


def _offer(source: Source, quota: int, read: JobRead) -> OfferedEntry:
    shown = tuple(sorted(rank_bullets(source, read)[:MAX_OFFERED_BULLETS]))
    available = len(shown)
    if source.pinned and source.min_bullets:
        minimum = min(source.min_bullets, len(source.bullets))
        maximum = max(min(quota, available), minimum)
    else:
        minimum = min(FLOOR_BULLETS, available)
        maximum = min(max(quota, minimum), available)
    return OfferedEntry(source=source, minimum=minimum, maximum=max(maximum, minimum), shown=shown)


def build_plan(corpus: Corpus, read: JobRead, shape: Shape) -> Plan:
    pages = shape.pages
    cap = MAX_EXPERIENCE[pages]
    entries = list(corpus.experience)
    if len(entries) > cap:
        keep = {s.pointer for s in entries if s.pinned}
        latest = most_recent(entries, corpus.template)
        if latest is not None:
            keep.add(latest.pointer)  # the most recent job is never the one to leave out
        by_relevance = sorted(entries, key=lambda s: (-relevance(s, read), s.index))
        for source in by_relevance:
            if len(keep) >= cap:
                break
            keep.add(source.pointer)
        entries = [s for s in entries if s.pointer in keep]
    quotas = EXPERIENCE_QUOTA[pages]
    experience = tuple(
        _offer(source, quotas[min(position, len(quotas) - 1)], read)
        for position, source in enumerate(entries)
    )

    ranked_projects = sorted(
        corpus.projects, key=lambda s: (not s.pinned, -relevance(s, read), s.index)
    )
    projects = tuple(
        _offer(source, PROJECT_QUOTA[pages], read)
        for source in ranked_projects[:PROJECT_CANDIDATES]
    )
    limit = max(MAX_PROJECTS[pages], sum(1 for s in corpus.projects if s.pinned))
    return Plan(shape=shape, experience=experience, projects=projects, project_limit=limit)
