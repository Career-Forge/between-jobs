// Mirrors engine_contract.py's PrepareApplicationResult (Sprint 3.0d) and
// export_checklist.py's ChecklistItem (Sprint 3.3f).

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
  // S3 (honest-score-surfaces.md): optional, same as forge-engines' own
  // AtsAttempt.detail -- absent from a response built before this sprint.
  detail?: AtsScoreDetail;
}

export interface ArtifactRef {
  artifact_id: string;
  version_id: string;
  media_type: string;
  sha256: string;
}

// S4c (honest-score-surfaces.md) -- the Honest Floor's pre-generation fit
// read. 0-10 scale, deliberately not shown as a fake "/100" -- see
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

export interface PrepareApplicationResult {
  run_id: string;
  resume: ArtifactRef | null;
  // C1/C2 (coverforge-port.md): null unless the request opted in
  // (generate_cover_letter: true) -- see GeneratePanel.tsx.
  cover_letter?: ArtifactRef | null;
  ats_attempts: AtsAttempt[];
  final_score: number | null;
  score_scale: string;
  fit?: ForgeFitResult;
  warnings: string[];
}

export type ChecklistStatus = "pass" | "fail" | "not_checked";

export interface ChecklistItem {
  key: string;
  label: string;
  status: ChecklistStatus;
  detail: string;
}

// Gap Interview (S4, honest-score-surfaces.md) -- mirrors forge-engines'
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
