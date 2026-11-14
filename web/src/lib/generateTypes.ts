// Mirrors engine_contract.py's PrepareApplicationResult and export_checklist.py's ChecklistItem.

export interface AtsScoreBreakdown {
  semantic_coverage: number;
  experience_quality: number;
  hard_req_score: number;
  quantification: number;
  company_alignment: number;
  structure: number;
}

export interface ClusterContribution {
  cluster_name: string;
  coverage_score: number;
  years_ratio: number;
  points_earned: number;
  points_possible: number;
}

export interface ExperienceDetail {
  relevant_years: number;
  total_years: number;
  relevance_ratio: number;
  has_management: boolean;
  management_bonus: number;
}

export interface QuantificationDetail {
  metrics_count: number;
  relevance_score: number;
}

export interface CompanyAlignmentDetail {
  culture_score: number;
  has_white_text_spam: boolean;
}

export interface AtsScoreDetail {
  clusters: ClusterContribution[];
  experience: ExperienceDetail;
  quantification: QuantificationDetail;
  company_alignment: CompanyAlignmentDetail;
}

export interface AtsAttempt {
  overall_score: number;
  breakdown: AtsScoreBreakdown;
  confidence: string;
  rating: string;
  gaps: string[];
  // Optional, same as the engine's own AtsAttempt.detail: absent from a response
  // built before the score details were returned.
  detail?: AtsScoreDetail;
}

export interface ArtifactRef {
  artifact_id: string;
  version_id: string;
  media_type: string;
  sha256: string;
}

// The honest read's pre-generation fit read. 0-10 scale, deliberately not shown as a fake "/100" -- see
// engine_contract.py's ForgeFitResult docstring for why.
export interface ForgeScoreDimensions {
  skills_match?: number;
  experience_relevance?: number;
  metric_impact?: number;
  seniority_fit?: number;
  keyword_coverage?: number;
  leadership_signals?: number;
}

export interface ForgeFitResult {
  overall_score: number;
  dimensions: ForgeScoreDimensions;
  gaps: string[];
  strengths: string[];
  recommendation: string;
  keyword_gaps: string[];
  keyword_hits: string[];
  visa_flag: boolean | null;
}

// A preview of the resume the engine laid out, in print order, as plain text. Mirrors engine_contract.py's
// ResumeContent. Every string is text for a text node, never HTML or LaTeX. Read it only when `schema` is a
// value this app has a reader for (1 today); for any other value, treat the preview as absent.
export interface ResumeContentChip {
  // The personal field the chip shows: phone, email, linkedin, github, portfolio, location or nationality.
  field: string;
  text: string;
  // null, or an http, https, mailto or tel address. The server has already refused every other scheme; check it
  // again before making a link.
  href: string | null;
}

export interface ResumeContentHeader {
  name: string;
  // The chips the PDF header printed, in print order. Can be fewer than the PDF has.
  chips: ResumeContentChip[];
  separator: "pipe" | "dot" | "bullet";
}

export interface ResumeContentBullet {
  // "<section>.<entry index>.b<bullet index>": stable only inside one stored result.
  id: string;
  // Can be "" (a bullet the PDF prints empty).
  text: string;
}

export interface ResumeContentEntry {
  // Positional ("experience.0"): stable only inside one stored result, never across versions.
  id: string;
  // A JSON pointer into the profile when the engine knows one, else null.
  pointer: string | null;
  // Can be "" (a title the PDF drops).
  title: string;
  organisation: string | null;
  // The PDF's date range joined with an en dash (U+2013), e.g. Jan 2020, dash, Present; an open end stays; null when
  // neither end prints.
  dates: string | null;
  // The tech stack of a project, or the education detail line, on one line.
  detail: string | null;
  bullets: ResumeContentBullet[];
}

export interface ResumeContentSkillRow {
  // The label and each item can be "".
  label: string;
  items: string[];
}

export interface ResumeContent {
  schema: number;
  // Who emitted it. An open string: show or log it, never branch on it.
  source: string;
  // null when the PDF has no name of the person's to show: compose the header from the profile instead.
  header: ResumeContentHeader | null;
  summary: string | null;
  // The drawn sections, in print order, each kind at most once. A plain string, not a union: skip any kind this
  // app does not know how to draw (known today: summary, experience, internships, projects, skills, education).
  section_order: string[];
  // Printed sections the preview does not draw, by their printed heading.
  not_shown: string[];
  experience: ResumeContentEntry[];
  internships: ResumeContentEntry[];
  projects: ResumeContentEntry[];
  education: ResumeContentEntry[];
  skills: ResumeContentSkillRow[];
}

export interface PrepareApplicationResult {
  run_id: string;
  resume: ArtifactRef | null;
  // Null unless the request opted in
  // (generate_cover_letter: true) -- see GeneratePanel.tsx.
  cover_letter?: ArtifactRef | null;
  // Empty, with `final_score` null, when the engine that wrote the resume does not score
  // resumes: show nothing in place of a score, never a number.
  ats_attempts: AtsAttempt[];
  final_score: number | null;
  score_scale: string;
  // Absent or null when the engine computes no fit read. The server stores null, an older
  // payload has no key at all: both mean "no read", and neither is a score of zero.
  fit?: ForgeFitResult | null;
  // The gate's own literal verdict
  // ("proceed", or a decline code like REJECT_MISMATCH/SKIP_LOW_SCORE) --
  // previously only reconstructable indirectly via resume being null.
  gate_outcome?: string | null;
  gate_reason?: string;
  gate_cautions?: string[];
  warnings: string[];
  // A preview of the draft, bound to the resume this same result names (`resume.version_id`, `resume.sha256`): do
  // not show it as any other version. Absent on a result stored before this field existed, null when the engine
  // sent none or sent one the server could not read, and always null when `resume` is null.
  resume_content?: ResumeContent | null;
}

export type ChecklistStatus = "pass" | "fail" | "not_checked";

export interface ChecklistItem {
  key: string;
  label: string;
  status: ChecklistStatus;
  detail: string;
}

// Gap interview -- mirrors the engine's
// gap_interview.GapQuestion/EntityCandidate/GapAnswerDraft and between-jobs'
// own engine_contract.py mirrors of them, field for field.
export interface GapQuestion {
  cluster_name: string;
  question: string;
}

export interface EntityCandidate {
  pointer: string;
  label: string;
}

export interface GapInterviewQuestionsResponse {
  questions: GapQuestion[];
}

export interface GapAnswerDraftResponse {
  bullet: string;
  entity_pointer: string;
  candidates: EntityCandidate[];
}
