"""Building the resume's content from the profile and the checked model output, to a budget.

Deterministic. The titles, companies, places and dates of every entry are copied from the
profile here, the skills are the profile's own (filtered to the ones `api/skills.py` finds
verified or supported), and the bullets are whatever `body.py` settled on. What this module
adds is the arithmetic: the sections are filled in a fixed order of importance, each addition
costing its estimated lines (`plan.LayoutModel`), until the page budget is spent. The pieces
that are not negotiable come first and are added whether or not they fit: the header,
education, skills, every kept job's heading and its minimum bullets, and pinned entries in
full. Everything else fits or is left out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from between_jobs.api.profile import Certification, Publication
from between_jobs.api.skills import SKILL_ALIASES, canonicalize_skill, classify_skill

from .body import BodyResult
from .document import (
    EducationBlock,
    EntryBlock,
    ProjectBlock,
    ResumeDoc,
)
from .formatting import date_range, month_label
from .header import flat_personal, resolve_chips, separator_of
from .job import JobRead
from .plan import HEADLINE_LINES, MAX_EDUCATION, MAX_SKILLS, Plan, start_key
from .provenance import mentions
from .sources import Corpus
from .text import clean_line

_CATEGORY_LABELS = {
    "programming": "Programming",
    "ai_ml": "AI / ML",
    "data_mlops": "Data / MLOps",
    "cloud_devops": "Cloud / DevOps",
    "tools": "Tools",
    "other": "Other",
}
_EXTRAS_CAPS = {
    "certifications": {1: 3, 2: 6},
    "publications": {1: 2, 2: 5},
    "patents": {1: 1, 2: 3},
    "volunteering": {1: 1, 2: 3},
    "achievements": {1: 2, 2: 5},
}
_COURSEWORK_ITEMS = 8


@dataclass(slots=True)
class Assembled:
    doc: ResumeDoc
    evidence_pointers: list[str]
    pins_honored: bool
    estimated_lines: float
    """The size estimate the budget was spent against, in text lines (see `plan.LayoutModel`)."""
    warnings: list[str] = field(default_factory=list)


_MAX_SKILL_CANDIDATES = 2000
"""How many listed skills are considered at all. A profile has no limit on its skills list, and
ranking one costs time in proportion to its length; nobody's resume lists two thousand skills."""
_ATTEMPTS_PER_SLOT = 4
"""How many ranked skills are checked for each place on the page before giving up: all of them
pass by construction, so the cap only bounds a profile built to make them fail."""


def build_skills(corpus: Corpus, read: JobRead, limit: int) -> list[tuple[str, str]]:
    """The candidate's skills by category, the ones the job mentions first, at most `limit`.

    Candidates are the skills the profile lists plus the tools it names in its entries; only
    those `classify_skill` finds verified or supported are kept (all of them are, by
    construction; the filter is the guard that nothing adjacent or unsupported is ever
    printed). A skill spelled two ways is printed once, as the candidate first spelled it.

    The candidates are ranked first and checked afterwards, in rank order, until `limit` have
    passed: `classify_skill` reads the whole profile, so checking every candidate would make a
    long skills list cost time in proportion to its length squared."""
    template = corpus.template
    canonical_json = corpus.canonical_json
    declared = template.skills
    candidates: list[tuple[str, str]] = []
    for category in _CATEGORY_LABELS:
        candidates += [(category, skill) for skill in getattr(declared, category)]
    for job in template.experience:
        candidates += [(_category_of(skill), skill) for skill in job.skills]
    for project in template.projects:
        candidates += [(_category_of(tech), tech) for tech in project.tech]

    wanted_terms = [(term, canonicalize_skill(term).lower()) for term in read.terms]
    seen: set[str] = set()
    ranked: list[tuple[int, int, str, str]] = []
    for position, (category, raw) in enumerate(candidates[:_MAX_SKILL_CANDIDATES]):
        skill = clean_line(raw, 60)
        key = canonicalize_skill(skill).lower()
        if not skill or key in seen:
            continue
        seen.add(key)
        # a skill the job asks for sorts first, under any spelling ("k8s" for "Kubernetes")
        lowered = skill.lower()
        wanted = any(mentions(term, lowered) or term_key == key for term, term_key in wanted_terms)
        ranked.append((0 if wanted else 1, position, category, skill))
    ranked.sort()

    chosen: list[tuple[int, int, str, str]] = []
    for item in ranked[: limit * _ATTEMPTS_PER_SLOT]:
        if len(chosen) >= limit:
            break
        if classify_skill(item[3], canonical_json) in {"verified", "supported"}:
            chosen.append(item)
    by_category: dict[str, list[tuple[int, int, str]]] = {}
    for rank, position, category, skill in chosen:
        by_category.setdefault(category, []).append((rank, position, skill))
    lines: list[tuple[str, str]] = []
    for category, label in _CATEGORY_LABELS.items():
        if category in by_category:
            skills = ", ".join(skill for _m, _p, skill in sorted(by_category[category]))
            lines.append((label, skills))
    return lines


def _certification_line(cert: Certification) -> str:
    name = ", ".join(part for part in (clean_line(cert.name), clean_line(cert.issuer)) if part)
    if not name:
        return ""
    when = month_label(clean_line(cert.date))
    return clean_line(f"{name} ({when})" if when else name)


def _publication_line(publication: Publication) -> str:
    """`Title. Venue, May 2022`: the title's own closing punctuation is kept (no second full
    stop after "?"), a title alone is printed without one, and the date is written the way a
    certification's is."""
    title = clean_line(publication.title)
    if not title:
        return ""
    details = ", ".join(
        part
        for part in (clean_line(publication.venue), month_label(clean_line(publication.date)))
        if part
    )
    if not details:
        return title
    return f"{title}{'' if title[-1] in '.?!' else '.'} {details}"


def _category_of(skill: str) -> str:
    entry = SKILL_ALIASES.get(skill.strip().lower())
    return entry[1] if entry else "other"


def assemble(
    corpus: Corpus,
    plan: Plan,
    read: JobRead,
    body: BodyResult,
    summary: str | None,
    *,
    show_gpa: bool,
    header_layout: dict[str, Any] | None,
) -> Assembled:
    template = corpus.template
    shape = plan.shape
    layout = shape.layout
    pages = shape.pages
    warnings: list[str] = []
    evidence: list[str] = []

    personal = flat_personal(template)
    doc = ResumeDoc(
        name=personal["name"],
        headline=personal["headline"],
        chips=resolve_chips(personal, header_layout),
        separator=separator_of(header_layout),
        density=shape.density,
        summary=summary,
    )
    used = layout.header + (HEADLINE_LINES if doc.headline else 0.0)
    used += layout.wrapped(summary) + 0.4 if summary else 0.0

    # -- education: always kept (pinned first when there are more than fit), in profile order
    education = template.education
    pinned_education = {
        i for i, entry in enumerate(education) if entry.pin is not None and entry.pin.mandatory
    }
    indices = sorted(range(len(education)), key=lambda i: (i not in pinned_education, i))[
        : max(MAX_EDUCATION, len(pinned_education))
    ]
    for index in sorted(indices):
        edu = education[index]
        details: list[str] = []
        if show_gpa and clean_line(edu.gpa):
            details.append(f"GPA: {clean_line(edu.gpa)}")
        if pages >= 2 and edu.coursework:
            courses = ", ".join(clean_line(c) for c in edu.coursework[:_COURSEWORK_ITEMS])
            details.append(f"Coursework: {courses}")
        degree = clean_line(f"{edu.degree}, {edu.field}" if edu.field else edu.degree)
        doc.education.append(
            EducationBlock(
                pointer=f"/education/{index}",
                institution=clean_line(edu.institution),
                place=clean_line(edu.location),
                degree=degree,
                dates=date_range(edu.start_date, edu.end_date),
                details=details,
            )
        )
        evidence.append(f"/education/{index}")
    if doc.education:
        used += layout.section_title + sum(
            layout.education_entry + sum(layout.wrapped(d) for d in block.details)
            for block in doc.education
        )

    doc.skills = build_skills(corpus, read, MAX_SKILLS[pages])
    if doc.skills:
        used += layout.section_title + sum(layout.wrapped(f"{a}: {b}") for a, b in doc.skills)

    # -- experience: every kept job, its heading and its minimum bullets are not negotiable
    kept = list(plan.experience)
    blocks: list[EntryBlock] = []
    lists: list[list[str]] = []
    for offer in kept:
        job = template.experience[offer.source.index]
        blocks.append(
            EntryBlock(
                pointer=offer.source.pointer,
                primary=clean_line(job.company),
                place=clean_line(job.location),
                secondary=clean_line(job.title),
                dates=date_range(job.start_date, job.end_date, current=job.is_current),
            )
        )
        lists.append([b.text for b in body.bullets.get(offer.source.pointer, [])])

    def mandatory(index: int) -> float:
        return layout.experience_heading + sum(
            layout.bullet(text) for text in lists[index][: kept[index].minimum]
        )

    if kept:
        used += layout.section_title
    budget = shape.budget
    while kept and used + sum(mandatory(i) for i in range(len(kept))) > budget:
        droppable = [i for i, offer in enumerate(kept) if not offer.source.pinned]
        if not droppable:
            break
        # the oldest job that is not pinned (by its start date, not by where it sits in the list)
        gone = min(
            droppable, key=lambda i: (start_key(template.experience[kept[i].source.index]), -i)
        )
        warnings.append(
            f'The job "{kept[gone].source.label}" was left off to keep to {pages} page(s).'
        )
        del kept[gone], blocks[gone], lists[gone]
    used += sum(mandatory(i) for i in range(len(kept)))
    shown = [list(lists[i][: kept[i].minimum]) for i in range(len(kept))]
    longest = max((len(items) for items in lists), default=0)
    for rank in range(longest):
        for i, items in enumerate(lists):
            if rank < kept[i].minimum or rank >= len(items) or len(shown[i]) != rank:
                continue
            cost = layout.bullet(items[rank])
            if used + cost <= budget:
                shown[i].append(items[rank])
                used += cost
    for block, items, offer in zip(blocks, shown, kept, strict=True):
        block.bullets = items
        doc.experience.append(block)
        evidence.append(offer.source.pointer)

    # -- projects: whatever room is left, except a pinned project, which is always printed
    project_offers = {o.source.pointer: o for o in plan.projects}
    project_cost_title = layout.section_title
    for pointer in body.project_order:
        project_offer = project_offers.get(pointer)
        if project_offer is None:
            continue
        offer_source = project_offer.source
        project = template.projects[offer_source.index]
        items = [b.text for b in body.bullets.get(pointer, [])]
        title_cost = project_cost_title if not doc.projects else 0.0
        heading = layout.project_heading
        first = layout.bullet(items[0]) if items else 0.0
        if not offer_source.pinned and used + title_cost + heading + first > budget:
            continue
        used += title_cost + heading
        chosen: list[str] = []
        required = project_offer.minimum if offer_source.pinned else 0
        for position, text in enumerate(items):
            cost = layout.bullet(text)
            if position >= required and used + cost > budget:
                break
            chosen.append(text)
            used += cost
        doc.projects.append(
            ProjectBlock(
                pointer=pointer,
                name=clean_line(project.name),
                url=clean_line(project.url),
                tech=", ".join(clean_line(t) for t in project.tech[:6]),
                bullets=chosen,
            )
        )
        evidence.append(pointer)

    # -- extras, most useful first; each is kept whole or left out
    def fits(lines: float) -> bool:
        return used + layout.section_title + lines <= budget

    # an entry with no text left after cleaning is left out BEFORE the page cap is applied, so a
    # blank entry neither prints as an empty line nor uses up a place
    certs = [line for c in template.certifications if (line := _certification_line(c))][
        : _EXTRAS_CAPS["certifications"][pages]
    ]
    if certs and fits(sum(layout.wrapped(c) for c in certs)):
        doc.certifications = certs
        used += layout.section_title + sum(layout.wrapped(c) for c in certs)
    pubs = [
        (index, line)
        for index, publication in enumerate(template.publications)
        if (line := _publication_line(publication))
    ][: _EXTRAS_CAPS["publications"][pages]]
    if pubs and fits(sum(layout.wrapped(line) for _i, line in pubs)):
        doc.publications = [line for _i, line in pubs]
        used += layout.section_title + sum(layout.wrapped(line) for _i, line in pubs)
        evidence += [f"/publications/{index}" for index, _line in pubs]
    patents = [
        (index, line)
        for index, patent in enumerate(template.patents)
        if (
            line := clean_line(
                patent.title + (f" ({patent.patent_number})" if patent.patent_number else "")
            )
        )
    ][: _EXTRAS_CAPS["patents"][pages]]
    if patents and fits(sum(layout.wrapped(line) for _i, line in patents)):
        doc.patents = [line for _i, line in patents]
        used += layout.section_title + sum(layout.wrapped(line) for _i, line in patents)
        evidence += [f"/patents/{index}" for index, _line in patents]
    volunteers = [
        EntryBlock(
            pointer=f"/volunteering/{index}",
            primary=clean_line(v.organization),
            place="",
            secondary=clean_line(v.role),
            dates=date_range(v.start_date, v.end_date),
            bullets=[clean_line(b) for b in v.bullets[:2] if clean_line(b)],
        )
        for index, v in enumerate(template.volunteering[: _EXTRAS_CAPS["volunteering"][pages]])
    ]
    cost = sum(
        layout.experience_heading + sum(layout.bullet(b) for b in v.bullets) for v in volunteers
    )
    if volunteers and fits(cost):
        doc.volunteering = volunteers
        used += layout.section_title + cost
    awards = [
        clean_line(a)
        for a in template.achievements[: _EXTRAS_CAPS["achievements"][pages]]
        if clean_line(a)
    ]
    if awards and fits(sum(layout.bullet(a) for a in awards)):
        doc.achievements = awards
        used += layout.section_title + sum(layout.bullet(a) for a in awards)
    spoken = ", ".join(
        clean_line(f"{lang.language} ({lang.fluency})" if lang.fluency else lang.language)
        for lang in template.languages[:6]
    )
    if spoken and fits(layout.wrapped(spoken)):
        doc.languages = spoken
        used += layout.section_title + layout.wrapped(spoken)

    if used > budget + 0.01:
        warnings.append(
            f"The pinned entries make the resume longer than {pages} page(s); trim a pin or "
            "choose more pages."
        )

    # -- pins: kept, and given the bullets they asked for
    pins_honored = True
    printed: list[EntryBlock | ProjectBlock] = [*doc.experience, *doc.projects]
    shown_by_pointer = {block.pointer: len(block.bullets) for block in printed}
    for source in (*corpus.experience, *corpus.projects):
        if not source.pinned:
            continue
        count = shown_by_pointer.get(source.pointer)
        wanted = source.min_bullets or 0
        if count is None or count < wanted:
            pins_honored = False
            have = len(source.bullets)
            warnings.append(
                f'The pinned entry "{source.label}" shows {count or 0} bullet(s); {wanted} '
                f"were asked for" + (f", and your profile has {have}." if have < wanted else ".")
            )
    return Assembled(
        doc=doc,
        evidence_pointers=evidence,
        pins_honored=pins_honored,
        estimated_lines=used,
        warnings=warnings,
    )
