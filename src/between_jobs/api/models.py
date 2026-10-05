"""Request/response models for the spine's HTTP boundary."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class GapInterviewDraftRequest(BaseModel):
    """The question that was asked and the candidate's freeform answer to it --
    everything the draft endpoint needs from the caller; it resolves the
    candidate-entity list itself from the user's own active profile."""

    question: str
    answer: str


class SubmitInterviewAnswerRequest(BaseModel):
    """The candidate's freeform answer to whichever question the session's
    own state says is next -- the route resolves which question, never a
    caller-supplied question id, matching the platform's own "next
    unanswered, in order" session model."""

    answer_text: str


class GapInterviewApproveRequest(BaseModel):
    """The candidate's final, explicitly-approved bullet + target
    entity -- may differ from what the draft endpoint proposed (the
    candidate can edit the bullet or pick a different entity before
    approving; this is the one and only value actually applied)."""

    bullet: str
    entity_pointer: str


class ImportProfileRequest(BaseModel):
    """The raw text of a resume-template JSON paste or .json file upload --
    unvalidated until `profile.import_profile()` runs on it. Kept as a
    plain string rather than a pre-parsed dict so JSON-syntax errors are
    caught by our own honest error messages (profile.py), not FastAPI's
    generic 422 body-parsing failure."""

    raw_text: str


class MintLinkCodeRequest(BaseModel):
    """`channel` matches channel_identities' own vocabulary -- only
    "telegram" is actually wired to a bot command yet (the route rejects
    anything else), but the field isn't narrowed to a single literal so the
    shape doesn't need to change when a second channel's bot integration
    arrives."""

    channel: str = Field(min_length=1)


class CreateApplicationFromPasteRequest(BaseModel):
    """The manual-paste lane -- no URL scraping, the caller supplies the
    job content directly. `canonical_url` is
    optional: some pastes (forwarded emails, screenshots retyped by hand)
    have no URL at all, and a job with no URL always gets its own row
    rather than being deduped against anything (jobs_store's own rule)."""

    title: str = Field(min_length=1)
    company_name: str = Field(min_length=1)
    description_text: str = Field(min_length=1)
    canonical_url: str | None = None
    location_text: str | None = None


class CreateApplicationFromUrlRequest(BaseModel):
    """The URL-ingest sibling of `CreateApplicationFromPasteRequest`.
    Deliberately just the one field
    the user actually has: title/company/location/description are all
    resolved server-side (a real registry lookup, or a real Firecrawl
    scrape) rather than trusted from the caller, matching this project's
    own repeated "resolve server-side, don't trust a caller-supplied
    reference" posture (see e.g. `PrepareApplicationRequest`'s own
    docstring)."""

    url: str = Field(min_length=1)


ApplicationStatus = Literal[
    "saved", "applied", "screening", "interviewing", "offer", "rejected", "withdrawn"
]
"""The seven application stages (the same values the web frontend's
STATUS_OPTIONS lists), and the single enforced vocabulary. Membership-only
enforcement, not a gated state machine -- any-to-any moves among these 7
stay legal. The Postgres `applications_status_check` CHECK constraint and
`change_application_stage`'s own copy of this same check are the real,
load-bearing enforcement; this Literal just gives the HTTP boundary a
friendly 422 before ever reaching the DB, matching `QuestionType`'s own
precedent."""


class ChangeApplicationStageRequest(BaseModel):
    """`idempotency_key` is caller-supplied, not server-generated --
    the platform's "idempotency keys on all commands" rule means the
    caller (the one who can actually retry) owns picking it, e.g. a fresh
    uuid per click of a "Mark as Applied" button, so a network retry of
    the same click doesn't double-record the transition."""

    new_status: ApplicationStatus
    idempotency_key: str = Field(min_length=1)


class SaveCredentialRequest(BaseModel):
    """`model` is free-text, not a dropdown of known models -- every model
    identifier is configuration, never durable domain data, and a hardcoded
    model list goes stale exactly the way this project's own model-currency
    discipline warns against.

    `model` is optional -- an LLM credential needs one (which model to
    call), but a search-provider credential (You.com, Firecrawl) doesn't
    have a "model" concept at all; forcing one would mean either a
    placeholder value or a second request shape, neither of which is
    honest.

    `secret_2` is optional -- a generic second-value
    slot for the rare provider whose BYOK credential genuinely needs two
    real values (Adzuna: app_id + app_key; USAJobs: an Authorization-Key
    AND a registered email). Every other provider leaves it unset."""

    service: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    secret: str = Field(min_length=1)
    model: str | None = None
    base_url: str | None = None
    secret_2: str | None = None


class PrepareApplicationRequest(BaseModel):
    """No profile_version_id/job_snapshot_id here, unlike the MCP-facing
    `PrepareApplicationInput` in `engine_contract.py` -- this HTTP route
    resolves both itself (the user's active profile, the application's
    current active_job_snapshot_id) rather than trusting a caller-supplied
    id, matching this project's "resolve server-side, don't trust a
    caller-supplied reference to sensitive resolution" posture."""

    idempotency_key: str = Field(min_length=16, max_length=128)
    force_generate: bool = False
    """"Generate anyway" -- an explicit human override of the pre-generation
    gate, after seeing its own honest `fit` read (the Honest Floor).
    Defaults False; every existing caller is unaffected."""
    generate_cover_letter: bool = False
    """Opt-in, defaults False. The cover letter is bundled into this same
    `/prepare` call rather than a separate endpoint, so one request produces
    both documents. A boolean, not the unwired
    `PrepareApplicationInput.requested_artifacts` list (the MCP-facing
    schema, a separate model this HTTP route has never used) -- same shape
    as `force_generate` above."""


class UpdateHeaderLayoutRequest(BaseModel):
    """`header_layout` stays `dict[str, Any]` rather than a
    typed model -- its shape is forge-engines' `HeaderLayout`,
    which this platform treats as opaque configuration it stores
    and forwards, the same way `resume_template`/`pass1`/`pass2` are
    opaque dicts at the forge-engines HTTP boundary itself."""

    header_layout: dict[str, Any] = Field(default_factory=dict)


class PreviewHeaderRequest(BaseModel):
    """`header_layout=None` previews the document's already-saved layout;
    a caller mid-edit sends the DRAFT layout instead, so the preview
    reflects unsaved changes without requiring a save first."""

    header_layout: dict[str, Any] | None = None


class UpdateSectionsRequest(BaseModel):
    """`section_order` is unconstrained here (not a Literal
    enum of known section names) -- the resume_documents row itself has
    no CHECK constraint on it either, matching this project's usual
    unconstrained-status-text posture, and validating against forge-
    engines' renderable section set would need this file to know that
    set, which is forge-engines' own concern, not this HTTP boundary's."""

    section_order: list[str]
    section_visibility: dict[str, bool] = Field(default_factory=dict)


class UpdateSelectedEvidenceRequest(BaseModel):
    """The evidence picker -- the full set of career_fact ids
    the user wants considered evidence for this document, replacing
    whatever was selected before."""

    evidence_fact_ids: list[str] = Field(default_factory=list)


class ShapeOverrides(BaseModel):
    """Resume settings, stored as one
    jsonb blob on `resume_documents.shape_overrides` -- both the master
    document (defaults) and a per-application document (overrides) use
    this exact same shape, merged by `shape_overrides.merge` before a
    generation call.

    Every field is optional with NO default rendered here on purpose:
    unset (`None`) means "no opinion, inherit from the next layer down the
    precedence chain" (per-application -> master -> system default). A
    stored `{}` and a stored `{"page_count": null}` both mean that. Storing
    the literal system-default value instead (e.g. `"auto"`) would mean
    "explicitly pinned to auto" -- indistinguishable from "unset" only
    because "auto" happens to BE today's system default -- so the real
    system defaults live in `shape_overrides.py`'s `resolve()`, not here."""

    model_config = ConfigDict(extra="ignore")

    page_count: Literal["auto", "1", "2"] | None = None
    density: Literal["compact", "balanced", "spacious"] | None = None
    """All three values render distinctly -- forge-engines has its own
    Spacious LaTeX macro family (looser vspace, same font size as Balanced)
    and a density-aware page-line-budget scale. See `shape_overrides.py`."""
    summary: Literal["auto", "on", "off"] | None = None
    """The system DEFAULT (applied when this is unset all the way down the
    chain) is "off", not "auto" -- a summary is opt-in. See
    `shape_overrides.py`'s `resolve()`."""
    bullet_style: Literal["plain", "bold_lead_in"] | None = None
    """forge-engines' own `bullet_lead_in` defaults to "none" (no bold
    lead-in) -- "plain" here maps onto that."""
    region: str | None = None
    """A country code forge-engines' locale profiles understand (or any
    string -- an unrecognized one resolves to forge-engines' own DEFAULT
    profile). Merged into `locale_resolver.resolve_locale_for_prepare`'s
    `document_override`/`user_default` parameters, which accept real
    values but were fed `None` until this setting gave them a source."""
    show_gpa: bool | None = None
    """Opt-in: the system default is `False` for every user (it is a
    platform-wide default, not any one person's preference). See
    `shape_overrides.py`'s `resolve()`."""
    show_nationality: bool | None = None
    """Opt-in only -- system default
    `False`. Even when on, forge-engines only actually renders a nationality
    chip when the RESOLVED locale's `effective_fields` also says
    "expected" (today: DE/AT) -- this flag alone is never sufficient. The
    resolved locale isn't exposed to this frontend before generation, so
    the UI can't gate the toggle's visibility on it; it gates rendering
    itself, backend-side. Render-only -- forge-engines' own whitelists keep
    this out of every LLM prompt."""


class UpdateShapeOverridesRequest(BaseModel):
    shape_overrides: ShapeOverrides


class UpdateAssertionsRequest(BaseModel):
    """The full set of dealbreaker
    requirement strings the candidate has personally confirmed are true
    for them (e.g. "on-site work is fine"), replacing whatever was
    asserted before -- same full-replace convention as
    `UpdateSelectedEvidenceRequest`. Plain strings, not ids: a dealbreaker
    has no stable identity across generations (Step0 re-extracts it fresh
    every run), so the requirement TEXT itself is the only handle there
    is -- matched fuzzily at scoring time, see forge-engines'
    `ats_score.apply_dealbreaker_assertions`."""

    assertions: list[str] = Field(default_factory=list)


class TrackDiscoveredJobRequest(BaseModel):
    """The frontend already has the full `SearchResult`
    (and, when scored, `ScoredJob`) data from its own last `/discover`
    response; tracking just resubmits the fields needed to create a real
    `jobs`/`job_snapshots` row, mirroring `CreateApplicationFromPasteRequest`'s
    own shape. Deliberately NOT reused directly: this route needs
    `provider` to decide whether a real full `jd_text` can be looked up
    from the job registry server-side, since neither
    `SearchResult` nor `ScoredJob` carries full JD text (only a 500-char
    snippet) for a live-search-lane result."""

    apply_url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    company: str | None = None
    location: str | None = None
    snippet: str = ""
    provider: str = "unknown"


SAVED_SEARCH_MAX_QUERY_CHARS = 200
SAVED_SEARCH_MAX_LOCATION_CHARS = 100
SAVED_SEARCH_MAX_COMPANIES = 20
SAVED_SEARCH_MAX_COMPANY_CHARS = 100
"""The limits on one saved search. The table's CHECK constraints (migration
`bound_saved_searches`) hold the same numbers, so the database refuses what the API
would."""


class CreateSavedSearchRequest(BaseModel):
    """Mirrors `/discover`'s own filter shape exactly
    (`GET /discover?q=...&location=...&companies=...&remote_only=...`),
    since the real UX is "save the search I already ran", not a second
    form asking the user to re-specify criteria.

    Every field is bounded: a saved search is read by the background matcher on every
    tick for as long as it is active, so its size is a cost the owner imposes on everyone."""

    query: str = Field(default="", max_length=SAVED_SEARCH_MAX_QUERY_CHARS)
    location: str | None = Field(default=None, max_length=SAVED_SEARCH_MAX_LOCATION_CHARS)
    companies: list[
        Annotated[str, StringConstraints(max_length=SAVED_SEARCH_MAX_COMPANY_CHARS)]
    ] = Field(default_factory=list, max_length=SAVED_SEARCH_MAX_COMPANIES)
    remote_only: bool = False


class SetSavedSearchActiveRequest(BaseModel):
    is_active: bool


HiringSignalFreshness = Literal["day", "3days", "week"]


class SearchHiringSignalsRequest(BaseModel):
    """How recent the posts must be. The whole body is
    optional (a bare POST means the default window); anything but the three
    windows, or any other field, is a 422 rather than silently ignored."""

    model_config = ConfigDict(extra="forbid")

    freshness: HiringSignalFreshness = "week"


class SaveHiringSignalRequest(BaseModel):
    """Save a post the search returned. ONLY the numeric
    activity id and the label of the search that found it: a url, author,
    title or text is not a field here and `extra="forbid"` turns one into a
    422 instead of an ignored extra, so nothing but the id can ever reach the
    store -- the server builds the address itself (see
    `hiring_signal_saves_store`). `activity_id` is 1-25 ASCII digits and never
    starts with 0: a real id does not, and `0007...` and `7...` are the same
    number, which would otherwise be two saves of one post."""

    model_config = ConfigDict(extra="forbid")

    activity_id: str = Field(pattern=r"^[1-9][0-9]{0,24}$")
    query_label: str | None = None


HiringSignalLocale = Literal["india", "global"]

_TypedRole = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
_TypedLocation = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]


class SearchHiringTabRequest(BaseModel):
    """The standalone tab's search: a typed role and,
    optionally, a metro, with NO company. Both are the user's own typing and
    are treated as untrusted (see `hiring_signal_tab`); the provider query is
    built server-side and is never a field here, and neither is a provider or a
    cache choice -- anything but these four fields is a 422 rather than silently
    ignored. `query` is 1-200 characters after trimming, `location` at most 100
    (a blank one means none), and `locale` (which hiring vocabulary to ask for)
    is derived from the location when omitted or `null`. The default window is
    the one `hiring_signal_tab.TAB_DEFAULT_FRESHNESS` names."""

    model_config = ConfigDict(extra="forbid")

    query: _TypedRole
    location: _TypedLocation | None = None
    freshness: HiringSignalFreshness = "3days"
    locale: HiringSignalLocale | None = None


class CreateHiringSearchRequest(BaseModel):
    """Save a tab search. ONLY the user's own typed role
    and metro: a saved search does not run, schedule or watch anything, so there
    is no window, locale or provider to store."""

    model_config = ConfigDict(extra="forbid")

    query: _TypedRole
    location: _TypedLocation | None = None


_MAX_NORMALIZED_QUESTION_CHARS = 500
"""`questionSafety.ts`'s own `MAX_LABEL_LENGTH` caps a client-read label at
300 characters (plus a 1-character ellipsis) before it's ever normalized
and sent here -- this is that bound with real headroom above it, not a
re-derivation of it, since nothing stops a modified client or a direct
API call from sending an uncapped string. Generous enough for any real
question label, bounded well short of the request becoming a vector for
an oversized storage key or lookup payload."""

_MAX_ANSWER_TEXT_CHARS = 2000
"""Same free-text ceiling this codebase already uses elsewhere for a
bounded prose field (`hiring_signal_tab._MAX_FIT_TEXT_CHARS`) -- an
approved answer is a short, human-reviewed paragraph before it's ever
saved (it either came out of `/draft-answer`'s own ~500-token generation
cap, or was typed/edited by the user in the panel), so this is headroom
above any real answer, not a tight fit around one."""


class MatchApprovedAnswerRequest(BaseModel):
    """The extension sends a screening
    question's own normalized label text (never the raw DOM id/uuid,
    which live DOM research found carries no semantic meaning on any of
    Greenhouse/Lever/Ashby); `canonical_intent` is optional, populated
    only when the selector map's own authoring already tags this field
    with a known deterministic intent (e.g. "willing_to_relocate")."""

    normalized_question: str = Field(min_length=1, max_length=_MAX_NORMALIZED_QUESTION_CHARS)
    canonical_intent: str | None = None
    jurisdiction: str | None = None


class SaveApprovedAnswerRequest(BaseModel):
    """Upserts on `(user_id, normalized_question)` -- approving the same
    question a second time replaces the stored answer. `sensitive_category`
    marks an EEO/demographic/work-authorization-class answer so the
    extension's own per-field opt-in gate has something to check against;
    left None for an ordinary factual answer."""

    normalized_question: str = Field(min_length=1, max_length=_MAX_NORMALIZED_QUESTION_CHARS)
    answer_text: str = Field(min_length=1, max_length=_MAX_ANSWER_TEXT_CHARS)
    canonical_intent: str | None = None
    evidence_fact_ids: list[str] = Field(default_factory=list)
    jurisdiction: str | None = None
    sensitive_category: str | None = None
    expires_at: str | None = None


class DraftAnswerRequest(BaseModel):
    """`question_text` is the raw rendered
    label the extension read off the page (not yet normalized; the
    extension normalizes separately when it later saves an approved
    answer via SaveApprovedAnswerRequest). `application_id` scopes the
    job-description evidence source to the one specific application this
    question came from, not a generic "current job" guess.

    `question_text`'s cap is `_MAX_ANSWER_TEXT_CHARS`, not
    `_MAX_NORMALIZED_QUESTION_CHARS` -- deliberately: it must stay well
    above `application_answer_generator._MAX_QUESTION_LENGTH_FOR_GENERATION`
    (400) so a genuinely long disclaimer-shaped question still reaches
    `is_generation_eligible` and gets a real, deterministic "not
    eligible" answer instead of a generic validation 422."""

    application_id: str = Field(min_length=1)
    question_text: str = Field(min_length=1, max_length=_MAX_ANSWER_TEXT_CHARS)
