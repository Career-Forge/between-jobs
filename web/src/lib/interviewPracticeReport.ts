// InterviewForge R4 (interviewforge-v1.md) -- pure recomputation of a
// session's report, mirroring interview_practice.py's `build_session_report`
// EXACTLY. The backend only ever returns `session_report` inline on the
// answers-endpoint response that completes a session -- it isn't a stored
// column, so GET /sessions/{id} never includes one. Resuming into a session
// that's already "completed" (see InterviewPracticePanel.tsx) needs this
// same math run client-side against that session's own persisted questions.

import type { InterviewQuestion, SessionReport } from "./interviewPracticeTypes";

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

// average_score = mean of all questions' `score` field where `answered_at`
// is not null, rounded to 2 decimals, or null if none answered.
// star_coverage_rate = fraction of answered questions whose
// `feedback.star_coverage` has all 4 of situation/task/action/result true,
// rounded to 2 decimals, or null if none answered.
export function buildSessionReport(questions: InterviewQuestion[]): SessionReport {
  const answered = questions.filter((q) => q.answered_at !== null);
  const answeredCount = answered.length;

  if (answeredCount === 0) {
    return {
      question_count: questions.length,
      answered_count: 0,
      average_score: null,
      star_coverage_rate: null,
    };
  }

  const scores = answered.map((q) => q.score ?? 0);
  const fullStarCount = answered.filter((q) => {
    const star = q.feedback?.star_coverage;
    return !!star && star.situation && star.task && star.action && star.result;
  }).length;

  return {
    question_count: questions.length,
    answered_count: answeredCount,
    average_score: round2(scores.reduce((a, b) => a + b, 0) / answeredCount),
    star_coverage_rate: round2(fullStarCount / answeredCount),
  };
}
