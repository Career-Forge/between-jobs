"""The public engine contract types: what the prepare flow hands to, and gets back from, a
resume engine.

These are between-jobs' OWN public shapes, not a copy of forge-engines' wire format.
`AtsAttempt` has no shape defined anywhere else -- `PrepareApplicationResult` only
forward-references `list["AtsAttempt"]` -- so its fields mirror forge-engines' `AtsScore`
TypedDict one-for-one (that's the only thing this type wraps), translated to this project's
own snake_case convention. forge-engines' `AtsScoreBreakdown` keeps camelCase because it
mirrors the reference `calculateATSScore` return object exactly (see
forge-engines/src/forge_engines/ats_score.py); this contract has no such parity obligation
to an external JS object, so it follows the same field-naming convention as every other
model in this codebase.

`PrepareApplicationInput` is the MCP tool input, reused here as the internal command shape
(IDs + idempotency_key, never raw text) -- the same shape works for both an MCP tool call
and this platform's own internal command, since neither should ever trust caller-supplied
profile/job text over a resolved, authorized snapshot id.

`ResumeContent` is the one type here that this repository owns outright instead of mirroring: the
wire contract for the "paper", a print-resolved description of the resume an engine just laid out,
which the web app draws as a preview. An engine sends it as `resume["paper"]` in its apply answer;
`parse_resume_content` is how every consumer reads it.
"""

from __future__ import annotations

import logging
import unicodedata
from typing import Annotated, Any, Literal, get_args
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    Strict,
    StringConstraints,
    ValidationError,
    model_serializer,
)

logger = logging.getLogger(__name__)


class ArtifactRef(BaseModel):
    artifact_id: str
    version_id: str
    media_type: str
    sha256: str
    download_url: str | None = None


class AtsScoreBreakdown(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    semantic_coverage: int = Field(alias="semanticCoverage")
    experience_quality: int = Field(alias="experienceQuality")
    hard_req_score: int = Field(alias="hardReqScore")
    quantification: int
    company_alignment: int = Field(alias="companyAlignment")
    structure: int


class ClusterContribution(BaseModel):
    """Mirrors forge-engines' `ClusterContribution` (ats_score.py). New field,
    not one the reference had, so plain snake_case on the wire already -- no
    alias needed, unlike `AtsScoreBreakdown`."""

    cluster_name: str
    coverage_score: float
    years_ratio: float
    points_earned: float
    points_possible: float


class ExperienceDetail(BaseModel):
    relevant_years: float
    total_years: float
    relevance_ratio: float
    has_management: bool
    management_bonus: float


class QuantificationDetail(BaseModel):
    metrics_count: float
    relevance_score: float


class CompanyAlignmentDetail(BaseModel):
    culture_score: float
    has_white_text_spam: bool


class AtsScoreDetail(BaseModel):
    clusters: list[ClusterContribution] = Field(default_factory=list)
    experience: ExperienceDetail
    quantification: QuantificationDetail
    company_alignment: CompanyAlignmentDetail


class AtsAttempt(BaseModel):
    """One ATS scoring pass. `PrepareApplicationResult.ats_attempts` holds
    one entry per attempt, in order -- pre-regen, then post-regen when a
    regeneration happened -- mirroring forge-engines'
    `PipelineResult.ats_attempts`."""

    model_config = ConfigDict(populate_by_name=True)

    overall_score: int
    breakdown: AtsScoreBreakdown
    confidence: str
    rating: str
    gaps: list[str] = Field(default_factory=list)
    page_count: float = 1
    """The ATS-extraction LLM's own self-reported page count -- an
    informational cross-check value, not authoritative for anything (see
    forge-engines' `AtsScore.page_count` docstring). Defaults to 1 so
    responses from a not-yet-updated forge-engines deploy still validate."""
    detail: AtsScoreDetail | None = None
    """Sub-signals behind the breakdown totals (see forge-engines'
    `AtsScoreDetail` docstring). Optional, same rationale as `page_count` --
    a not-yet-updated forge-engines deploy simply omits it."""


_RequestedArtifact = Literal["resume", "cover_letter", "application_answers"]


def _default_requested_artifacts() -> list[_RequestedArtifact]:
    return ["resume", "cover_letter"]


class PrepareApplicationInput(BaseModel):
    application_id: str
    profile_version_id: str
    job_snapshot_id: str
    requested_artifacts: list[_RequestedArtifact] = Field(
        default_factory=_default_requested_artifacts
    )
    template_id: str | None = None
    idempotency_key: str = Field(min_length=16, max_length=128)


class Step0Cluster(BaseModel):
    """Mirrors forge-engines' `Step0Cluster` (step0.py) field-for-field --
    already snake_case-compatible on the wire (forge-engines' own JSON
    keys here happen to already be plain lowercase words, not camelCase,
    so no alias translation is needed the way `AtsScoreBreakdown` needed
    one)."""

    name: str
    priority: str
    keywords: list[str] = Field(default_factory=list)


class GapQuestion(BaseModel):
    """Mirrors forge-engines' `gap_interview.GapQuestion` field for field --
    already snake_case-compatible on the wire, no alias translation
    needed."""

    cluster_name: str
    question: str


class GapAnswerDraft(BaseModel):
    """Mirrors forge-engines' `gap_interview.GapAnswerDraft` field for
    field."""

    bullet: str
    entity_pointer: str


class Step0Result(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    clusters: list[Step0Cluster] = Field(default_factory=list)
    dealbreakers: list[str] = Field(default_factory=list)
    target_tier: str = Field(default="", alias="targetTier")
    key_terms: list[str] = Field(default_factory=list, alias="keyTerms")
    company_name: str = Field(default="", alias="companyName")
    role_name: str = Field(default="", alias="roleName")
    short_role: str = Field(default="", alias="shortRole")


class ForgeScoreDimensions(BaseModel):
    """Mirrors forge-engines' `forge_score.ForgeScoreDimensions` field for
    field -- every dimension is optional there (`total=False`) since the
    scorer LLM isn't guaranteed to return all six; same here."""

    skills_match: float | None = None
    experience_relevance: float | None = None
    metric_impact: float | None = None
    seniority_fit: float | None = None
    keyword_coverage: float | None = None
    leadership_signals: float | None = None


class ForgeFitResult(BaseModel):
    """Mirrors forge-engines' `forge_score.ForgeScoreResult` field for
    field -- the pre-generation fit read, computed on every real `/apply`
    call (it would be silently dropped by `ForgeApplyResult`'s own
    `extra="ignore"` config if it were not typed here). 0-10 scale,
    deliberately NOT converted to a fake "/100" -- this measures something
    different from `AtsScoreBreakdown`'s own honest 0-100 (pre- vs.
    post-generation, LLM read vs. deterministic formula), and dressing it
    up as directly comparable would be exactly the fabricated-looking
    precision this platform refuses elsewhere."""

    overall_score: float
    dimensions: ForgeScoreDimensions = Field(default_factory=ForgeScoreDimensions)
    gaps: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    recommendation: str = ""
    keyword_gaps: list[str] = Field(default_factory=list)
    keyword_hits: list[str] = Field(default_factory=list)
    visa_flag: bool | None = None


# -- ResumeContent: the contract for the paper --------------------------------------------------
#
# What an engine sends, and how this platform reads it. Everything below until
# `PrepareApplicationResult` is that one contract.

ResumeContentSchema = Literal[1]
SUPPORTED_RESUME_CONTENT_SCHEMAS: frozenset[int] = frozenset(get_args(ResumeContentSchema))
"""The `schema` values this code can read. `schema` counts breaking changes: adding an optional
key a reader can ignore does not change it; adding a section kind, changing what a field means or
removing a key does. A paper of any other schema is never read, not even partly (see
`parse_resume_content`). Stored papers outlive engines, so when a schema 2 ships its reader is
added next to this one; the schema 1 reader stays as long as a stored result can carry one."""

RESUME_CONTENT_SECTION_KINDS: tuple[str, ...] = (
    "summary",
    "experience",
    "internships",
    "projects",
    "skills",
    "education",
)
"""The section kinds a schema 1 paper can draw. `section_order` names the ones that were drawn,
in print order; a kind that is not on this list is skipped, not an error."""

RESUME_CONTENT_CHIP_FIELDS: frozenset[str] = frozenset(
    {"phone", "email", "linkedin", "github", "portfolio", "location", "nationality"}
)
"""The personal fields a header chip may carry. A saved header layout can make a PDF print other
personal fields (a work-authorisation line, for one); a paper never carries those, so a chip of
any other field is left out here as well, however it got in."""

RESUME_CONTENT_MAX_BYTES = 64 * 1024
"""Above this, serialised as compact UTF-8 JSON, a paper is not stored or shown. This is the one
limit a legitimate paper can reach."""
RESUME_CONTENT_MAX_TEXT_LENGTH = 2_000
RESUME_CONTENT_MAX_HREF_LENGTH = 2_048
RESUME_CONTENT_MAX_SEPARATOR_LENGTH = 32
RESUME_CONTENT_MAX_ENTRIES = 100
"""Per section."""
RESUME_CONTENT_MAX_BULLETS = 100
"""Per entry."""
RESUME_CONTENT_MAX_SKILL_ROWS = 100
RESUME_CONTENT_MAX_SKILL_ITEMS = 1_000
"""Per row. An engine does not cap a skills row, so this is the loosest of the count limits."""
RESUME_CONTENT_MAX_CHIPS = 16
RESUME_CONTENT_MAX_SECTIONS = 32
RESUME_CONTENT_MAX_NOT_SHOWN = 32
"""The limits above, except `RESUME_CONTENT_MAX_BYTES`, are sanity bounds on the shape of one
paper, set far above what any resume page prints: a hostile or broken engine is refused cheaply,
and a legitimate paper (small, a few kilobytes) is never near them. They are not part of the
contract an engine writes to, which has the one size limit. A paper that breaks one is rejected
whole, never cut down to size, because a preview with bullets quietly missing would read as the
resume. A list longer than its limit is refused without being read item by item."""


_INVISIBLE_RANGES: tuple[range, ...] = (
    range(0x00AD, 0x00AE),  # soft hyphen
    range(0x180E, 0x180F),  # Mongolian vowel separator
    range(0x200B, 0x200E),  # zero-width space, non-joiner and joiner
    range(0x2060, 0x2070),  # word joiner, invisible operators, deprecated format controls
    range(0xFE00, 0xFE10),  # variation selectors
    range(0xFEFF, 0xFF00),  # byte order mark, zero-width no-break space
    range(0xFFF9, 0xFFFC),  # interlinear annotation marks
    range(0xE0000, 0xE0080),  # the tag block: invisible ASCII
    range(0xE0100, 0xE01F0),  # variation selectors supplement
)


def _text_table() -> dict[int, str | None]:
    """What happens to each character of a text field: a tab, a line break or a paragraph break
    becomes one space (the web collapses runs of spaces); every other C0 and C1 control
    character, the bidirectional controls that can reorder displayed text, the characters that
    print nothing (zero-width and joiner characters, the soft hyphen, the byte order mark,
    variation selectors and the tag block, which can carry invisible text) and lone surrogates
    (which cannot be encoded as UTF-8, so one in a stored value would fail the write) are
    removed. Both engines write Latin letters and ASCII, so none of these belongs in a real
    paper. Built from code points so the source stays plain ASCII."""
    table: dict[int, str | None] = {}
    for code in (*range(0x00, 0x20), *range(0x7F, 0xA0)):
        table[code] = None
    for code in (0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)):
        table[code] = None
    for codes in _INVISIBLE_RANGES:
        for code in codes:
            table[code] = None
    for code in range(0xD800, 0xE000):
        table[code] = None
    for code in (0x09, 0x0A, 0x0D, 0x2028, 0x2029):
        table[code] = " "
    return table


_TEXT_TABLE = _text_table()


def _plain_text(value: object) -> object:
    """Cleans a string field before it is checked. Anything that is not a string, or is longer
    than the limit, is handed on untouched for the checks to refuse; nothing here raises, and a
    long string is never scanned."""
    if isinstance(value, str) and len(value) <= RESUME_CONTENT_MAX_TEXT_LENGTH:
        return value.translate(_TEXT_TABLE)
    return value


_PlainText = Annotated[
    str,
    Strict(),
    BeforeValidator(_plain_text),
    StringConstraints(max_length=RESUME_CONTENT_MAX_TEXT_LENGTH),
]
"""Plain text, never HTML or LaTeX: the web draws it as text nodes. Cleaned, then limited."""


_HREF_REFUSED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp", "Zs"})
_HREF_SCHEMES = frozenset({"http", "https", "mailto", "tel"})


def _safe_href(value: object) -> str | None:
    """A chip's link target if it is safe to put in a link, otherwise None. Never raises.

    Kept: an `http`, `https`, `mailto` or `tel` address, the scheme in any case (it comes back
    lower case). Everything else is None, and the chip stays: `javascript:`, `data:`,
    `vbscript:` and any other scheme, an address with no scheme or a scheme-relative one
    (`//host`), an http(s) address with no host, anything that is not a string or is over the
    limit. A control, format (bidirectional or zero-width), space or surrogate character anywhere
    in it, and a backslash, make it None rather than being cleaned out: a browser ignores some
    of those characters when it reads a link, so what was checked has to be exactly what is
    stored."""
    if not isinstance(value, str) or not value or len(value) > RESUME_CONTENT_MAX_HREF_LENGTH:
        return None
    if "\\" in value:
        return None
    if any(unicodedata.category(char) in _HREF_REFUSED_CATEGORIES for char in value):
        return None
    scheme, colon, rest = value.partition(":")
    scheme = scheme.lower()
    if not colon or scheme not in _HREF_SCHEMES or not rest:
        return None
    if scheme in ("http", "https"):
        try:
            if not urlsplit(value).hostname:
                return None
        except ValueError:
            return None
    return f"{scheme}:{rest}"


def _known_chips(value: object) -> object:
    """Leaves out any chip whose field is not one of `RESUME_CONTENT_CHIP_FIELDS`. A chip that
    is not even an object, or has no text field name, is left in for the checks to refuse. A list
    longer than `RESUME_CONTENT_MAX_CHIPS` is refused here, before any chip is looked at: the
    length limit on the field runs after this, and by then every chip would have been read and
    every bad one reported."""
    if not isinstance(value, list):
        return value
    if len(value) > RESUME_CONTENT_MAX_CHIPS:
        raise ValueError("too many chips")
    kept: list[object] = []
    for chip in value:
        field = _plain_text(chip.get("field")) if isinstance(chip, dict) else None
        if isinstance(field, str) and field not in RESUME_CONTENT_CHIP_FIELDS:
            continue
        kept.append(chip)
    return kept


def _known_sections(value: object) -> object:
    """Leaves out a section kind this code does not know, and a kind named a second time (it is
    drawn once, at its first position). An item that is not a string is left in for the checks
    to refuse. A list longer than `RESUME_CONTENT_MAX_SECTIONS` is refused here, before any item
    is looked at, for the reason `_known_chips` gives."""
    if not isinstance(value, list):
        return value
    if len(value) > RESUME_CONTENT_MAX_SECTIONS:
        raise ValueError("too many section names")
    kept: list[object] = []
    for item in value:
        kind = _plain_text(item)
        if isinstance(kind, str) and (kind not in RESUME_CONTENT_SECTION_KINDS or kind in kept):
            continue
        kept.append(kind)
    return kept


def _exact_integer(value: object) -> object:
    """`schema` must be an integer: not a bool (which is an int to Python), not 1.0, not "1"."""
    if type(value) is not int:
        raise ValueError("schema must be an integer")
    return value


class ResumeContentChip(BaseModel):
    """One chip of the header, as the PDF header prints it: `field` is the personal field it
    shows (one of `RESUME_CONTENT_CHIP_FIELDS`), `text` what it says, `href` where it links."""

    model_config = ConfigDict(extra="ignore")

    field: _PlainText
    text: _PlainText
    href: Annotated[str | None, BeforeValidator(_safe_href)] = None
    """None, or an `http`, `https`, `mailto` or `tel` address; anything else an engine sends
    reads as None here, and the web checks the scheme again before it makes a link."""


class ResumeContentHeader(BaseModel):
    """The header block of the PDF. Chips are the ones the PDF header printed, in print order,
    limited to the known fields, so there can be fewer than the PDF has."""

    model_config = ConfigDict(extra="ignore")

    name: _PlainText
    chips: Annotated[
        list[ResumeContentChip],
        BeforeValidator(_known_chips),
        Field(max_length=RESUME_CONTENT_MAX_CHIPS),
    ]
    separator: Literal["pipe", "dot", "bullet"]


class ResumeContentBullet(BaseModel):
    """One bullet as the PDF prints it, after truncation, caps and merging. `text` can be ""
    (a bullet the PDF prints empty). `id` is `<section>.<entry index>.b<bullet index>`."""

    model_config = ConfigDict(extra="ignore")

    id: _PlainText
    text: _PlainText


class ResumeContentEntry(BaseModel):
    """One job, internship, project or education entry, as printed. `id` is positional
    (`experience.0`): stable inside one stored result, never across versions. `title` can be ""
    (a title written in a script the PDF drops). `dates` is the PDF's range with an en dash
    ("Jan 2020 - Present" with U+2013); an open end stays, and it is None when neither end
    prints. `detail` is the tech stack of a project, or the education detail line, on one line.
    There is no place field: an entry's location is never carried."""

    model_config = ConfigDict(extra="ignore")

    id: _PlainText
    pointer: _PlainText | None = None
    """A JSON pointer into the profile (`/experience/2`, the scheme of `api/profile.py`) when the
    engine knows one, else None."""
    title: _PlainText
    organisation: _PlainText | None = None
    dates: _PlainText | None = None
    detail: _PlainText | None = None
    bullets: Annotated[list[ResumeContentBullet], Field(max_length=RESUME_CONTENT_MAX_BULLETS)]


class ResumeContentSkillRow(BaseModel):
    """One printed skills line. `label` and each item can be ""; a row whose items all print as
    nothing is kept ("Label:" alone, as the PDF prints it)."""

    model_config = ConfigDict(extra="ignore")

    label: _PlainText
    items: Annotated[list[_PlainText], Field(max_length=RESUME_CONTENT_MAX_SKILL_ITEMS)]


class ResumeContent(BaseModel):
    """The paper: a strict, allowlisted, print-resolved description of the resume an engine just
    laid out, as plain text in print order. It is what the web's HTML preview draws, so it holds
    what the PDF prints (after the engine's own text folding, truncation, caps and merging), not
    what the engine started from, and nothing the PDF does not print. It is not the profile and
    not a way to edit the resume.

    Wire shape (JSON; the key is `paper` inside the engine's `resume` object)::

        {"schema": 1, "source": str,
         "header": null | {"name": str, "chips": [{"field", "text", "href"}], "separator": str},
         "summary": str | null,
         "section_order": [str],     # drawn sections only, print order, each kind at most once
         "not_shown": [str],         # printed sections the paper does not draw, by heading
         "experience": [Entry], "internships": [Entry], "projects": [Entry],
         "education": [Entry], "skills": [{"label": str, "items": [str]}]}
        Entry = {"id", "pointer", "title", "organisation", "dates", "detail",
                 "bullets": [{"id", "text"}]}

    Reading it (`parse_resume_content` is the entry point; validating this model directly gives
    the same cleaning, not the schema gate or the size cap):

    - Only schema values in `SUPPORTED_RESUME_CONTENT_SCHEMAS` are read. Any other, or none, is
      a paper that is absent, never one read partly.
    - Keys this model does not name are ignored (`extra="ignore"`), so it is a second allowlist
      behind the engine's own: a field an engine should never have sent does not survive here.
    - `source` names who emitted the paper. It is an open string: show or log it, never branch
      on it, never check it against a list.
    - A section kind in `section_order` that this code does not know is skipped; no list has a
      minimum length.
    - Every string is plain text, cleaned of control characters, and limited in length; the
      lists and the whole object are limited too (the `RESUME_CONTENT_MAX_*` constants).
    - A paper is bound to the resume it was drawn for by whoever stores it, not by the engine:
      see `PrepareApplicationResult.resume_content`.

    The Python attribute for the wire key `schema` is `schema_`, because `schema` is a
    `BaseModel` method; dumping and validating both use the wire name.
    """

    model_config = ConfigDict(extra="ignore")

    schema_: Annotated[ResumeContentSchema, BeforeValidator(_exact_integer)] = Field(alias="schema")
    source: _PlainText
    header: ResumeContentHeader | None = None
    summary: _PlainText | None = None
    section_order: Annotated[
        list[_PlainText],
        BeforeValidator(_known_sections),
        Field(max_length=RESUME_CONTENT_MAX_SECTIONS),
    ]
    not_shown: Annotated[list[_PlainText], Field(max_length=RESUME_CONTENT_MAX_NOT_SHOWN)]
    experience: Annotated[list[ResumeContentEntry], Field(max_length=RESUME_CONTENT_MAX_ENTRIES)]
    internships: Annotated[list[ResumeContentEntry], Field(max_length=RESUME_CONTENT_MAX_ENTRIES)]
    projects: Annotated[list[ResumeContentEntry], Field(max_length=RESUME_CONTENT_MAX_ENTRIES)]
    education: Annotated[list[ResumeContentEntry], Field(max_length=RESUME_CONTENT_MAX_ENTRIES)]
    skills: Annotated[list[ResumeContentSkillRow], Field(max_length=RESUME_CONTENT_MAX_SKILL_ROWS)]

    @model_serializer(mode="wrap")
    def _dump_with_the_wire_name(self, handler: Any) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if "schema_" in data:
            data = {"schema": data.pop("schema_"), **data}
        return data


_SHAPE_COUNT_KEYS = (
    "section_order",
    "not_shown",
    "experience",
    "internships",
    "projects",
    "education",
    "skills",
)


def _log_dropped(reason: str, **fields: object) -> None:
    """One line saying a paper was dropped, and why, in numbers and fixed words only: the
    schema number, list sizes, a byte count, the names of the checks that failed. Never a value
    from the paper, and never an exception's message, which can quote the input."""
    logger.warning("resume content dropped", extra={"ctx": {"reason": reason, **fields}})


def _loggable_schema(schema: object) -> int | None:
    """The `schema` number when it is a small integer, else None: an unreadable or huge value is
    not worth printing, and printing an arbitrary one is not safe."""
    if type(schema) is int and -10_000 <= schema <= 10_000:
        return schema
    return None


def parse_resume_content(raw: object) -> ResumeContent | None:
    """Reads an engine's paper (the `paper` value of its `resume` object), tolerantly: the paper,
    or None. NEVER raises, whatever it is given.

    None is returned, and nothing partial, when `raw` is None (an engine that sends no paper;
    nothing is logged), is not an object, has a `schema` this code does not know (a newer one, an
    old one no longer supported, a missing one, one that is not an integer), does not fit the
    shape of `ResumeContent`, or is over `RESUME_CONTENT_MAX_BYTES` once cleaned. Every one of
    those but the first logs one warning with the reason, the schema number and list sizes; no
    value from `raw` is ever logged. The caller shows no preview for None; a generation never
    fails because of it.

    What comes back is cleaned (see `ResumeContent`): keys it does not name are gone, strings
    have lost control characters, and every link is an `http`, `https`, `mailto` or `tel`
    address or None."""
    if raw is None:
        return None
    try:
        return _read_resume_content(raw)
    except Exception as exc:  # the contract: a paper can never fail the call that carries it
        _log_dropped("unexpected_error", error_type=type(exc).__name__)
        return None


def _read_resume_content(raw: object) -> ResumeContent | None:
    if not isinstance(raw, dict):
        _log_dropped("not_an_object")
        return None
    schema = raw.get("schema")
    if type(schema) is not int or schema not in SUPPORTED_RESUME_CONTENT_SCHEMAS:
        _log_dropped("unsupported_schema", schema=_loggable_schema(schema))
        return None
    try:
        content = ResumeContent.model_validate(raw)
    except ValidationError as exc:
        failed = exc.errors(include_url=False, include_context=False, include_input=False)
        _log_dropped(
            "invalid",
            schema=schema,
            errors=exc.error_count(),
            error_types=sorted({str(error["type"]) for error in failed}),
            **{key: _list_size(raw.get(key)) for key in _SHAPE_COUNT_KEYS},
        )
        return None
    size = len(content.model_dump_json().encode("utf-8"))
    if size > RESUME_CONTENT_MAX_BYTES:
        _log_dropped("oversize", schema=schema, bytes=size)
        return None
    return content


def _list_size(value: object) -> int | None:
    return len(value) if isinstance(value, list) else None


class PrepareApplicationResult(BaseModel):
    run_id: str
    profile_version_id: str
    job_snapshot_id: str
    resume: ArtifactRef | None
    cover_letter: ArtifactRef | None
    application_answers_id: str | None
    ats_attempts: list[AtsAttempt]
    """Empty, with `final_score` None, when the engine that wrote the resume does not score
    resumes: there is no score to show, and no consumer fills one in."""
    final_score: float | None
    score_scale: str = "0-100"
    fit: ForgeFitResult | None = None
    """The pre-generation Honest Floor read -- present on every real `/apply` run of the remote
    engine (proceeded, cautioned, or declined alike). None when the engine computes no such read
    (the built-in engine does not), or, as with `AtsAttempt.detail`, when the response came from
    a not-yet-updated deploy: the same forward-compat rationale. Absence is never a score of
    zero."""
    gate_outcome: str | None = None
    gate_reason: str = ""
    gate_cautions: list[str] = Field(default_factory=list)
    """The gate's own literal verdict (`GateInfo.outcome` -- "proceed", or a
    decline code like REJECT_MISMATCH/SKIP_LOW_SCORE), kept as its own field
    because folding it into plain `warnings` text would make it
    reconstructable only by checking whether `resume` is null.
    `record_event` already persists this whole result into
    `application_events.payload` on every prepare call regardless of gate
    outcome (fit is computed before the gate check runs), so capturing the
    literal outcome here needs no new table: it rides the same durable row
    that already exists. None, with an empty reason and no cautions, when the engine runs no
    gate: no verdict was reached, so none is recorded."""
    warnings: list[str]
    evidence_fact_ids: list[str]
    resume_content: ResumeContent | None = None
    """A preview of the draft: the resume as the engine laid it out, in print order, as plain
    text, for the web app to draw. It describes one resume, the one this same result names in
    `resume`: that reference's `version_id` and `sha256` are what the preview was drawn for, and
    nothing else binds the two, so a reader holding a different version of the resume must not
    show it as that version.

    Never present when `resume` is None: a run that declined to write a resume has no draft to
    preview, and any preview the engine sent with it is not kept. None as well when the engine
    sends no preview (the built-in engine does not yet) or sent one that could not be read
    (`parse_resume_content`: wrong schema, wrong shape, over the size limit). Absent altogether
    from results stored before this field existed, which read back as None; never a guess.

    It is a preview, not the document: the PDF compiled from the stored LaTeX is the document,
    and this holds what the engine says it printed, as the engine folded and truncated it. It
    carries no page count and says nothing of fit.

    It is stored with the rest of the result in `application_events.payload`, and so returned by
    `POST /applications/{id}/prepare` and `GET /applications/{id}/prepare-result`. It is not
    copied into the outbox payload that subscribers read, and the extension's payload does not
    carry it."""
