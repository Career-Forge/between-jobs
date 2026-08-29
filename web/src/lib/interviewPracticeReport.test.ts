import { describe, expect, it } from "vitest";
import { buildSessionReport } from "./interviewPracticeReport";
import type { InterviewQuestion } from "./interviewPracticeTypes";

function question(overrides: Partial<InterviewQuestion>): InterviewQuestion {
  return {
    id: "q1",
    session_id: "s1",
    ordinal: 0,
    question_text: "Tell me about a time you led a project.",
    question_type: "behavioral",
    target_skill: "Leadership",
    grounded_in: null,
    answer_text: null,
    score: null,
    feedback: null,
    answered_at: null,
    ...overrides,
  };
}

function fullStarFeedback(score: number) {
  return {
    score,
    structure_feedback: "Clear.",
    specificity_feedback: "Specific.",
    star_coverage: { situation: true, task: true, action: true, result: true },
  };
}

function partialStarFeedback(score: number) {
  return {
    score,
    structure_feedback: "Muddled.",
    specificity_feedback: "Vague.",
    star_coverage: { situation: true, task: false, action: true, result: false },
  };
}

describe("buildSessionReport", () => {
  it("returns nulls for average_score and star_coverage_rate when nothing is answered", () => {
    const questions = [
      question({ id: "q1", ordinal: 0 }),
      question({ id: "q2", ordinal: 1 }),
    ];
    expect(buildSessionReport(questions)).toEqual({
      question_count: 2,
      answered_count: 0,
      average_score: null,
      star_coverage_rate: null,
    });
  });

  it("computes the mean score and STAR coverage rate for a normal mixed session", () => {
    const questions = [
      question({
        id: "q1",
        ordinal: 0,
        answer_text: "Led a migration.",
        score: 9,
        feedback: fullStarFeedback(9),
        answered_at: "2026-08-29T00:00:00Z",
      }),
      question({
        id: "q2",
        ordinal: 1,
        answer_text: "Handled a conflict.",
        score: 6,
        feedback: partialStarFeedback(6),
        answered_at: "2026-08-29T00:01:00Z",
      }),
      question({ id: "q3", ordinal: 2 }),
    ];
    expect(buildSessionReport(questions)).toEqual({
      question_count: 3,
      answered_count: 2,
      average_score: 7.5,
      star_coverage_rate: 0.5,
    });
  });

  it("rounds star_coverage_rate to 2 decimals at a non-clean boundary", () => {
    const questions = [
      question({
        id: "q1",
        ordinal: 0,
        answer_text: "a",
        score: 5,
        feedback: fullStarFeedback(5),
        answered_at: "2026-08-29T00:00:00Z",
      }),
      question({
        id: "q2",
        ordinal: 1,
        answer_text: "b",
        score: 5,
        feedback: partialStarFeedback(5),
        answered_at: "2026-08-29T00:01:00Z",
      }),
      question({
        id: "q3",
        ordinal: 2,
        answer_text: "c",
        score: 5,
        feedback: partialStarFeedback(5),
        answered_at: "2026-08-29T00:02:00Z",
      }),
    ];
    const report = buildSessionReport(questions);
    expect(report.star_coverage_rate).toBe(0.33);
    expect(report.average_score).toBe(5);
  });
});
