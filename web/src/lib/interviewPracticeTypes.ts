// Mirrors interview_practice_routes.py's `/applications/{id}/interview-practice/*`
// responses and interview_practice.py's `AnswerFeedback`/`SessionReport`
// TypedDicts exactly (InterviewForge R3, interviewforge-v1.md).
//
// A dedicated file rather than CompanyIntelPanel.tsx's own inline-types
// pattern: these shapes have two real consumers -- InterviewPracticePanel.tsx
// AND interviewPracticeReport.ts (a plain lib module, not a component, that
// needs to import the question/report shapes to type its own pure function).
// CompanyIntelPanel's response type has exactly one consumer, so inlining
// there doesn't force a shared file; here it would mean either duplicating
// the shapes or having the lib module reach into a component file for types,
// so this follows tailorTypes.ts/profileTypes.ts's convention instead.

export type QuestionType = "behavioral" | "technical" | "situational";

export interface StarCoverage {
  situation: boolean;
  task: boolean;
  action: boolean;
  result: boolean;
}

export interface AnswerFeedback {
  score: number;
  structure_feedback: string;
  specificity_feedback: string;
  star_coverage: StarCoverage;
  improved_answer?: string;
}

export interface InterviewSession {
  id: string;
  user_id: string;
  application_id: string;
  company_name: string;
  resume_evidence: string;
  registry_entry_id: string | null;
  status: "in_progress" | "completed";
  started_at: string;
  completed_at: string | null;
}

export interface InterviewQuestion {
  id: string;
  session_id: string;
  ordinal: number;
  question_text: string;
  question_type: QuestionType;
  target_skill: string;
  grounded_in: string | null;
  answer_text: string | null;
  score: number | null;
  feedback: AnswerFeedback | null;
  answered_at: string | null;
}

export interface SessionReport {
  question_count: number;
  answered_count: number;
  average_score: number | null;
  star_coverage_rate: number | null;
}

export interface StartSessionResponse {
  session: InterviewSession;
  questions: InterviewQuestion[];
}

export interface SessionDetailResponse {
  session: InterviewSession;
  questions: InterviewQuestion[];
}

export interface ListSessionsResponse {
  sessions: InterviewSession[];
}

export interface SubmitAnswerResponse {
  feedback: AnswerFeedback;
  next_question: InterviewQuestion | null;
  session_report: SessionReport | null;
}

export const QUESTION_TYPE_LABELS: Record<QuestionType, string> = {
  behavioral: "Behavioral",
  technical: "Technical",
  situational: "Situational",
};
