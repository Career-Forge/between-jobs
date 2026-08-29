"""The canonical resume contract (Sprint 2.5, schema v1.1 in Sprint 3.1d) --
Proposal.md §15-16.

Deterministic only -- no LLM anywhere in this module. A user (or an LLM
they've pasted their resume into, per the onboarding instructions) supplies
JSON in the fixed template shape; this module validates it, assigns stable
ids, computes a content hash, and derives normalized `career_facts` rows.

Scope deliberately stops here: this module produces the platform's OWN
canonical_json + career_facts. It does not call forge-engines, and it does
not produce forge-engines' resume_doc/bubbles shape -- that stays engine-
side, wired up in Stage B (Sprint 3.0) when `prepare_application` actually
calls the service. This is the engine's INPUT, not its output.

Fact derivation is deliberately narrow for v1: experience, projects,
education, publication, and patent entries only -- the evidence Proposal
§15 says generated claims must cite. Summary bullets, achievements,
certifications, languages, and volunteering stay queryable inside
canonical_json without their own fact rows for now -- add them the day
something downstream actually needs to cite one.

v1.3 (resumeforge-shape-and-fit.md, Patents+Publications) adds `Patent` --
the same citable-evidence shape `Publication` already has, and the reason
`Publication` got promoted to a real `fact_type` in the first place.
Deliberately not pinnable, same reasoning as v1.2's note above: neither is
a bubble.

v1.1 (Sprint 3.1d) widens the schema for career shapes v1.0 couldn't
represent: a PhD-track profile with publications and no work experience
was REJECTED by v1.0's business rule ("no experience or projects"), which
was simply wrong -- publications and volunteering are evidence too. Every
new section is optional; nothing about v1.0 profiles changes shape.

v1.2 (resumeforge-shape-and-fit.md R5) adds an optional `pin` field to
Experience/Project/Education entries: a candidate-set hard guarantee that
the entry survives resume generation regardless of relevance ranking, with
an optional `min_bullets` floor (experience/projects only -- forge-engines
ignores it harmlessly on education, which has no bullets to floor). Capped
at 6 pinned entries total, enforced in `_check_business_rules`. Deliberately
NOT added to publications/certifications/languages/volunteering: forge-
engines' ingest pipeline doesn't consume those section types into bubbles
at all yet, so a pin there would validate but silently do nothing -- worse
than not offering it.

Stable-id preservation across re-imports ("content and structural
matching," Proposal §16) is deliberately NOT implemented: every import
assigns fresh ids. Nothing downstream references a fact id across versions
yet (no resume_documents, no Resume Studio) -- implementing fuzzy matching
before anything consumes it risks guessing at the wrong algorithm. Revisit
when Sprint 3.2 gives it a real consumer.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

_SCHEMA_VERSION = "1.3"

_PLACEHOLDER_RE = re.compile(r"^\s*(TODO|TBD|N/?A|XXX|<.*>|\[.*\])\s*$", re.IGNORECASE)
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _reject_placeholder(v: str, field_name: str) -> str:
    if _PLACEHOLDER_RE.match(v):
        raise ValueError(f"{field_name} looks like a placeholder, not real content: {v!r}")
    return v


def _validate_month(v: str, field_name: str) -> str:
    if v.lower() != "present" and not _MONTH_RE.match(v):
        raise ValueError(f"{field_name} must be YYYY-MM or 'present', got {v!r}")
    return v


class Email(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: str = Field(min_length=3)
    primary: bool = False


class Phone(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number: str = Field(min_length=3)
    primary: bool = False
    region: str = ""


class OtherLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1)
    url: str = Field(min_length=1)


class Links(BaseModel):
    model_config = ConfigDict(extra="forbid")
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""
    # Google Scholar -- the PhD-track equivalent of a portfolio link,
    # added in v1.1 rather than overloading `portfolio` with two meanings.
    scholar: str = ""
    # Escape hatch for anything that isn't one of the four named links
    # (ORCID, a personal blog, a Kaggle profile, ...) -- v1.0 was a fixed
    # set of exactly three link slots with no way to add a fourth.
    other: list[OtherLink] = Field(default_factory=list)


class Location(BaseModel):
    model_config = ConfigDict(extra="forbid")
    city: str = ""
    region: str = ""
    country: str = ""
    show_on_resume: bool = False


class Personal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    headline: str = ""
    emails: list[Email] = Field(default_factory=list)
    phones: list[Phone] = Field(default_factory=list)
    links: Links = Field(default_factory=Links)
    location: Location = Field(default_factory=Location)
    work_authorization: str = ""
    # Locale-specific, render-only fields (Proposal §17) -- never enter any
    # LLM prompt, kept here only so the canonical JSON can round-trip them.
    dob: str = ""
    nationality: str = ""
    marital_status: str = ""
    work_authorization_status: dict[str, str] = Field(default_factory=dict)
    photo: str = ""
    signature: bool = False

    @field_validator("name")
    @classmethod
    def _name_not_placeholder(cls, v: str) -> str:
        return _reject_placeholder(v, "personal.name")


class Pin(BaseModel):
    """R5: a candidate-set hard guarantee this entry survives generation
    regardless of relevance ranking. `min_bullets` is interpreted only for
    experience/projects; forge-engines ignores it harmlessly elsewhere."""

    model_config = ConfigDict(extra="forbid")
    mandatory: bool
    min_bullets: int | None = Field(default=None, ge=1, le=6)


class Experience(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1)
    company: str = Field(min_length=1)
    location: str = ""
    start_date: str
    end_date: str
    is_current: bool = False
    bullets: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    pin: Pin | None = None

    @field_validator("start_date", "end_date")
    @classmethod
    def _valid_month(cls, v: str, info: Any) -> str:
        return _validate_month(v, f"experience.{info.field_name}")


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    url: str = ""
    tech: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    pin: Pin | None = None


class Education(BaseModel):
    model_config = ConfigDict(extra="forbid")
    degree: str = Field(min_length=1)
    field: str = ""
    institution: str = Field(min_length=1)
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    gpa: str = ""
    coursework: list[str] = Field(default_factory=list)
    pin: Pin | None = None


class Skills(BaseModel):
    model_config = ConfigDict(extra="forbid")
    programming: list[str] = Field(default_factory=list)
    ai_ml: list[str] = Field(default_factory=list)
    data_mlops: list[str] = Field(default_factory=list)
    cloud_devops: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    other: list[str] = Field(default_factory=list)


class Publication(BaseModel):
    """A citable research output -- the evidence type v1.0 had no home
    for. `authors` is a plain string (as it appears in the citation, e.g.
    "Rivera, A., Patel, R., & Kim, J.") rather than a structured list --
    matching how every citation style actually renders it, and avoiding
    guessing at name-order/affiliation structure the source may not give."""

    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1)
    authors: str = ""
    venue: str = ""
    date: str = ""
    url: str = ""


class Patent(BaseModel):
    """A citable, invention-disclosure-style output -- the same evidence
    shape `Publication` already has (Proposal §15), for the case a
    candidate's most relevant output is a patent rather than a paper."""

    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1)
    patent_number: str = ""
    status: str = ""
    date: str = ""
    url: str = ""


class LanguageEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str = Field(min_length=1)
    fluency: str = ""


class Certification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    issuer: str = ""
    date: str = ""
    url: str = ""


class VolunteerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organization: str = Field(min_length=1)
    role: str = ""
    start_date: str = ""
    end_date: str = ""
    bullets: list[str] = Field(default_factory=list)


class ResumeTemplate(BaseModel):
    """The exact shape a user (or an LLM they've briefed) fills in --
    Proposal §16. `extra="forbid"` everywhere catches drift/typos as a
    validation error rather than silently dropping data.

    publications/languages/certifications/volunteering (v1.1) are all
    optional and default empty -- a v1.0 payload validates unchanged."""

    model_config = ConfigDict(extra="forbid")
    personal: Personal
    summary_bullets: list[str] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    publications: list[Publication] = Field(default_factory=list)
    patents: list[Patent] = Field(default_factory=list)
    skills: Skills = Field(default_factory=Skills)
    certifications: list[Certification] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    languages: list[LanguageEntry] = Field(default_factory=list)
    volunteering: list[VolunteerEntry] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ProfileImportError(Exception):
    """Raised for any reason an import can't proceed. `message` is always
    the honest, specific, user-facing reason -- never a generic failure."""

    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class ImportedProfile:
    """The result of a successful deterministic import, ready to persist."""

    canonical_json: dict[str, Any]
    content_hash: str
    schema_version: str
    warnings: tuple[str, ...] = ()
    stats: dict[str, int] = field(default_factory=dict)
    career_facts: tuple[CareerFact, ...] = ()


@dataclass(frozen=True, slots=True)
class CareerFact:
    fact_type: Literal["experience", "project", "education", "publication", "patent"]
    entity_key: str
    value_json: dict[str, Any]
    source_pointer: str


def parse_and_validate(raw_text: str) -> ResumeTemplate:
    """Stage 1: JSON syntax + shape. Raises ProfileImportError with a
    specific, human-readable reason -- never a raw pydantic traceback."""
    text = raw_text.strip()
    # Strip a UTF-8 BOM if present -- the same class of encoding gotcha
    # n8n's file-upload path had to handle.
    if text.startswith("﻿"):
        text = text[1:]

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise ProfileImportError(
            "That wasn't valid JSON -- check the format (a stray comma or "
            f"missing bracket is the usual cause) and resend. Detail: {e}"
        ) from e

    if not isinstance(payload, dict):
        raise ProfileImportError("That JSON parsed, but it's not a resume object at the top level.")

    try:
        return ResumeTemplate.model_validate(payload)
    except ValidationError as e:
        raise ProfileImportError(_format_validation_error(e)) from e


_MAX_REPORTED_ERRORS = 10


def _format_validation_error(e: ValidationError) -> str:
    # Reporting only errors()[0] made a resume with N problems (e.g. a
    # stray field repeated across every array entry) a one-at-a-time
    # fix-resubmit-fix loop -- found live on a real resume with ~30
    # violations. List every problem in one message instead (capped, so a
    # truly pathological payload doesn't produce a wall of text).
    errors = e.errors()
    lines = []
    for err in errors[:_MAX_REPORTED_ERRORS]:
        loc = ".".join(str(p) for p in err["loc"]) or "(top level)"
        lines.append(f"• `{loc}`: {err['msg']}")
    if len(errors) > _MAX_REPORTED_ERRORS:
        lines.append(f"...and {len(errors) - _MAX_REPORTED_ERRORS} more.")
    problems = "\n".join(lines)
    return (
        f"Your resume JSON has {len(errors)} problem(s):\n{problems}\n\n"
        "Fix them and resend the whole JSON."
    )


_MAX_PINNED_ENTRIES = 6


def _check_business_rules(template: ResumeTemplate) -> None:
    # v1.0 required experience or projects -- rejecting a real, complete
    # PhD-track profile (education + publications, no work experience).
    # Any one of these five counts as real evidence a document can be
    # built from; education-only (nothing to write bullets from) still
    # doesn't.
    if not (
        template.experience
        or template.projects
        or template.publications
        or template.patents
        or template.volunteering
    ):
        raise ProfileImportError(
            "Your resume JSON needs at least one of: experience, projects, "
            "publications, patents, or volunteering. Fill in one and resend."
        )

    # R5: pins are a hard guarantee, not a soft nudge -- capped so a
    # candidate can't pin their whole resume and defeat the point (every
    # pin must still fit on the page).
    pinned_count = sum(
        1
        for entries in (template.experience, template.projects, template.education)
        for entry in entries
        if entry.pin is not None and entry.pin.mandatory
    )
    if pinned_count > _MAX_PINNED_ENTRIES:
        raise ProfileImportError(
            f"You've marked {pinned_count} entries as mandatory, but the limit is "
            f"{_MAX_PINNED_ENTRIES} across experience, projects, and education combined. "
            "Unmark some and resend."
        )


def _collect_warnings(template: ResumeTemplate) -> list[str]:
    warnings: list[str] = []
    if not any(e.primary for e in template.personal.emails):
        warnings.append("No primary email set in personal.emails.")
    # Only flag missing experience when there's also no other professional
    # evidence -- a publications-and-projects profile (the PhD shape) is a
    # normal, complete resume, not a resume missing its experience section.
    if (
        not template.experience
        and not template.projects
        and not template.publications
        and not template.patents
    ):
        warnings.append("No experience, projects, publications, or patents entries.")
    if not template.education:
        warnings.append("No education entries.")
    return warnings


def _derive_facts(template: ResumeTemplate) -> list[CareerFact]:
    facts: list[CareerFact] = []
    for i, exp in enumerate(template.experience):
        facts.append(
            CareerFact(
                fact_type="experience",
                entity_key=f"exp_{uuid4().hex[:8]}",
                value_json=exp.model_dump(mode="json"),
                source_pointer=f"/experience/{i}",
            )
        )
    for i, proj in enumerate(template.projects):
        facts.append(
            CareerFact(
                fact_type="project",
                entity_key=f"proj_{uuid4().hex[:8]}",
                value_json=proj.model_dump(mode="json"),
                source_pointer=f"/projects/{i}",
            )
        )
    for i, edu in enumerate(template.education):
        facts.append(
            CareerFact(
                fact_type="education",
                entity_key=f"edu_{uuid4().hex[:8]}",
                value_json=edu.model_dump(mode="json"),
                source_pointer=f"/education/{i}",
            )
        )
    for i, pub in enumerate(template.publications):
        facts.append(
            CareerFact(
                fact_type="publication",
                entity_key=f"pub_{uuid4().hex[:8]}",
                value_json=pub.model_dump(mode="json"),
                source_pointer=f"/publications/{i}",
            )
        )
    for i, pat in enumerate(template.patents):
        facts.append(
            CareerFact(
                fact_type="patent",
                entity_key=f"pat_{uuid4().hex[:8]}",
                value_json=pat.model_dump(mode="json"),
                source_pointer=f"/patents/{i}",
            )
        )
    return facts


def _content_hash(canonical_json: dict[str, Any]) -> str:
    # Canonical (sorted-key) serialization so semantically-identical JSON
    # hashes the same regardless of key order in the user's paste.
    normalized = json.dumps(canonical_json, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _compute_stats(template: ResumeTemplate, facts: list[CareerFact]) -> dict[str, int]:
    return {
        "experience": len(template.experience),
        "projects": len(template.projects),
        "education": len(template.education),
        "publications": len(template.publications),
        "patents": len(template.patents),
        "skills": (
            len(template.skills.programming)
            + len(template.skills.ai_ml)
            + len(template.skills.data_mlops)
            + len(template.skills.cloud_devops)
            + len(template.skills.tools)
            + len(template.skills.other)
        ),
        "certifications": len(template.certifications),
        "achievements": len(template.achievements),
        "languages": len(template.languages),
        "volunteering": len(template.volunteering),
        "career_facts": len(facts),
    }


def import_profile(raw_text: str) -> ImportedProfile:
    """The whole deterministic pipeline: validate -> business rules ->
    derive facts -> hash. Pure function -- no database, no network. The
    caller (the API/Telegram layer) is responsible for persistence."""
    template = parse_and_validate(raw_text)
    _check_business_rules(template)

    canonical_json = template.model_dump(mode="json")
    facts = _derive_facts(template)
    warnings = _collect_warnings(template)

    return ImportedProfile(
        canonical_json=canonical_json,
        content_hash=_content_hash(canonical_json),
        schema_version=_SCHEMA_VERSION,
        warnings=tuple(warnings),
        stats=_compute_stats(template, facts),
        career_facts=tuple(facts),
    )


class EntityCandidate(TypedDict):
    pointer: str
    label: str


def entity_candidates(canonical_json: dict[str, Any]) -> list[EntityCandidate]:
    """The `(pointer, label)` pairs a Gap Interview answer can attach a new
    bullet to -- every experience and project entry, in `_derive_facts`'s
    own pointer format (`/experience/{i}`, `/projects/{i}`). Education/
    publications/patents are deliberately excluded: they aren't
    bullet-shaped entries a freeform "here's something I've done" answer
    naturally extends."""
    candidates: list[EntityCandidate] = []
    for i, exp in enumerate(canonical_json.get("experience") or []):
        title = exp.get("title") or "Experience"
        company = exp.get("company") or ""
        label = f"{title} at {company}" if company else title
        candidates.append({"pointer": f"/experience/{i}", "label": label})
    for i, proj in enumerate(canonical_json.get("projects") or []):
        candidates.append({"pointer": f"/projects/{i}", "label": proj.get("name") or "Project"})
    return candidates


_ENTITY_POINTER_RE = re.compile(r"^/(experience|projects)/(\d+)$")


def _resolve_entity_pointer(canonical_json: dict[str, Any], entity_pointer: str) -> tuple[str, int]:
    """Parses and validates an entity pointer against the CURRENT
    canonical_json (not whatever it resolved to when a draft was first
    shown -- the profile may have changed since). Raises ProfileImportError
    with an honest, specific reason rather than guessing a fallback entity."""
    match = _ENTITY_POINTER_RE.match(entity_pointer)
    if match is None:
        raise ProfileImportError(
            f"{entity_pointer!r} isn't a valid entity pointer -- expected "
            "/experience/{i} or /projects/{i}."
        )
    section, index_str = match.group(1), match.group(2)
    index = int(index_str)
    entries = canonical_json.get(section)
    if not isinstance(entries, list) or index >= len(entries):
        raise ProfileImportError(
            f"No {section} entry at index {index} -- your profile may have "
            "changed since this question was generated. Try again."
        )
    return section, index


def append_bullet_to_entity(
    canonical_json: dict[str, Any], entity_pointer: str, bullet_text: str
) -> ImportedProfile:
    """S4b (honest-score-surfaces.md): builds a new ImportedProfile with
    exactly one bullet appended to one existing experience/project entry --
    the Gap Interview's deterministic apply step, once a candidate has
    explicitly approved a drafted fact. Reuses this module's own
    validate -> business-rules -> derive-facts -> hash pipeline rather than
    re-implementing it, so a Gap-Interview-originated version is held to
    the exact same shape guarantees as any other import."""
    bullet_text = bullet_text.strip()
    if not bullet_text:
        raise ProfileImportError("The bullet text can't be empty.")

    section, index = _resolve_entity_pointer(canonical_json, entity_pointer)
    modified = copy.deepcopy(canonical_json)
    modified[section][index].setdefault("bullets", [])
    modified[section][index]["bullets"].append(bullet_text)

    try:
        template = ResumeTemplate.model_validate(modified)
    except ValidationError as e:
        raise ProfileImportError(_format_validation_error(e)) from e
    _check_business_rules(template)

    new_canonical_json = template.model_dump(mode="json")
    facts = _derive_facts(template)
    warnings = _collect_warnings(template)

    return ImportedProfile(
        canonical_json=new_canonical_json,
        content_hash=_content_hash(new_canonical_json),
        schema_version=_SCHEMA_VERSION,
        warnings=tuple(warnings),
        stats=_compute_stats(template, facts),
        career_facts=tuple(facts),
    )
