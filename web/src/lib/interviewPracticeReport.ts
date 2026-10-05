// InterviewForge R4 (interviewforge-v1.md) -- pure recomputation of a
// session's report, mirroring interview_practice.py's `build_session_report`
// EXACTLY. The backend only ever returns `session_report` inline on the
// answers-endpoint response that completes a session -- it isn't a stored
// column, so GET /sessions/{id} never includes one. Resuming into a session
// that's already "completed" (see InterviewPracticePanel.tsx) needs this
// same math run client-side against that session's own persisted questions.

import type { InterviewQuestion, QuestionType, SessionReport } from "./interviewPracticeTypes";

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

// STAR (situation, task, action, result) is the shape of a story, so it is a fair measure of a
// behavioral or situational answer and of nothing else: a technical answer is judged on
// correctness and depth. Mirrors interview_practice.py's `star_applies`.
export function starApplies(questionType: QuestionType): boolean {
  return questionType === "behavioral" || questionType === "situational";
}

// average_score = mean of all questions' `score` field where `answered_at`
// is not null, rounded to 2 decimals, or null if none answered.
// star_coverage_rate = fraction of the answered behavioral and situational
// questions whose `feedback.star_coverage` has all 4 of
// situation/task/action/result true, rounded to 2 decimals, or null if there
// is none to measure (nothing answered, or only technical answers). A
// technical answer is left out of the numerator AND the denominator, and so is
// a story answer with no STAR data at all.
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
  const starMeasured = answered.flatMap((q) => {
    const star = q.feedback?.star_coverage;
    return starApplies(q.question_type) && star ? [star] : [];
  });
  const fullStarCount = starMeasured.filter(
    (star) => star.situation && star.task && star.action && star.result,
  ).length;

  return {
    question_count: questions.length,
    answered_count: answeredCount,
    average_score: round2(scores.reduce((a, b) => a + b, 0) / answeredCount),
    star_coverage_rate:
      starMeasured.length === 0 ? null : round2(fullStarCount / starMeasured.length),
  };
}

// The STAR part of the session summary line, or null when there is nothing to say. Says what the
// rate is a rate OF, and that technical answers were not scored for it.
export function starSummary(report: SessionReport, questions: InterviewQuestion[]): string | null {
  const technicalAnswered = questions.some(
    (q) => q.answered_at !== null && !starApplies(q.question_type),
  );
  if (report.star_coverage_rate !== null) {
    const rate = `full STAR coverage on ${Math.round(report.star_coverage_rate * 100)}% of behavioral and situational answers`;
    return technicalAnswered ? `${rate} (technical answers are not scored for STAR)` : rate;
  }
  return technicalAnswered ? "STAR is not scored for technical questions" : null;
}
