"""Proposal §24.2's public engine contract types (Sprint 3.0d).

These are between-jobs' OWN public shapes, not a copy of forge-engines'
wire format. `AtsAttempt` isn't spelled out anywhere in the Proposal --
`PrepareApplicationResult` only forward-references `list["AtsAttempt"]` --
so its fields mirror forge-engines' `AtsScore` TypedDict one-for-one
(that's the only thing this type wraps), translated to this project's own
snake_case convention. forge-engines' `AtsScoreBreakdown` keeps camelCase
because it mirrors n8n's `calculateATSScore` return object exactly (see
forge-engines/src/forge_engines/ats_score.py); this contract has no such
parity obligation to an external JS object, so it follows the same
field-naming convention as every other model in this codebase.

`PrepareApplicationInput` is Proposal §9's MCP tool input, reused here as
the internal command shape per the v12 plan's own description of this
sprint ("IDs + idempotency_key, never raw text") -- the same shape works
for both an MCP tool call and this platform's own internal command, since
neither should ever trust caller-supplied profile/job text over a
resolved, authorized snapshot id.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    """Mirrors forge-engines' `ClusterContribution` (ats_score.py, S3 --
    honest-score-surfaces.md). New field, not an n8n-original one, so plain
    snake_case on the wire already -- no alias needed, unlike
    `AtsScoreBreakdown`."""

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
    `PipelineResult.ats_attempts` (Sprint 3.0b)."""

    model_config = ConfigDict(populate_by_name=True)

    overall_score: int
    breakdown: AtsScoreBreakdown
    confidence: str
    rating: str
    gaps: list[str] = Field(default_factory=list)
    page_count: float = 1
    """R7: the ATS-extraction LLM's own self-reported page count -- an
    informational cross-check value, not authoritative for anything (see
    forge-engines' `AtsScore.page_count` docstring). Defaults to 1 so
    responses from a not-yet-updated forge-engines deploy still validate."""
    detail: AtsScoreDetail | None = None
    """S3: sub-signals behind the breakdown totals (see forge-engines'
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
    """Mirrors forge-engines' `gap_interview.GapQuestion` field for field
    (S4a, honest-score-surfaces.md) -- already snake_case-compatible on
    the wire, no alias translation needed."""

    cluster_name: str
    question: str


class GapAnswerDraft(BaseModel):
    """Mirrors forge-engines' `gap_interview.GapAnswerDraft` field for
    field (S4b, honest-score-surfaces.md)."""

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
    field (S4c, honest-score-surfaces.md) -- the pre-generation fit read
    that was already computed on every real `/apply` call and silently
    dropped here until now (`ForgeApplyResult`'s own `extra="ignore"`
    config). 0-10 scale, deliberately NOT converted to a fake "/100" --
    this measures something different from `AtsScoreBreakdown`'s own
    honest 0-100 (pre- vs. post-generation, LLM read vs. deterministic
    formula), and dressing it up as directly comparable would be exactly
    the fabricated-looking precision this whole plan refuses elsewhere."""

    overall_score: float
    dimensions: ForgeScoreDimensions = Field(default_factory=ForgeScoreDimensions)
    gaps: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    recommendation: str = ""
    keyword_gaps: list[str] = Field(default_factory=list)
    keyword_hits: list[str] = Field(default_factory=list)
    visa_flag: bool | None = None


class PrepareApplicationResult(BaseModel):
    run_id: str
    profile_version_id: str
    job_snapshot_id: str
    resume: ArtifactRef | None
    cover_letter: ArtifactRef | None
    application_answers_id: str | None
    ats_attempts: list[AtsAttempt]
    final_score: float | None
    score_scale: str = "0-100"
    fit: ForgeFitResult | None = None
    """S4c: the pre-generation Honest Floor read -- present on every real
    `/apply` run (proceeded, cautioned, or declined alike), optional only
    so a response from a not-yet-updated forge-engines deploy still
    validates, same forward-compat rationale as `AtsAttempt.detail`."""
    warnings: list[str]
    evidence_fact_ids: list[str]
