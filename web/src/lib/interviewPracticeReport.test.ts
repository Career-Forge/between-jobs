import { describe, expect, it } from "vitest";
import { buildSessionReport, starApplies, starSummary } from "./interviewPracticeReport";
import type { InterviewQuestion, QuestionType } from "./interviewPracticeTypes";

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

function answered(
  id: string,
  questionType: QuestionType,
  score: number,
  star: { situation: boolean; task: boolean; action: boolean; result: boolean } | null,
): InterviewQuestion {
  return question({
    id,
    question_type: questionType,
    answer_text: "an answer",
    score,
    feedback: {
      score,
      structure_feedback: "",
      specificity_feedback: "",
      star_coverage: star,
    },
    answered_at: "2026-10-05T00:00:00Z",
  });
}

const FULL = { situation: true, task: true, action: true, result: true };
const NONE = { situation: false, task: false, action: false, result: false };

describe("starApplies", () => {
  it("applies to behavioral and situational questions only", () => {
    expect(starApplies("behavioral")).toBe(true);
    expect(starApplies("situational")).toBe(true);
    expect(starApplies("technical")).toBe(false);
  });
});

describe("buildSessionReport STAR scope", () => {
  it("leaves technical answers out of the numerator and the denominator", () => {
    const report = buildSessionReport([
      answered("a", "behavioral", 8, FULL),
      answered("b", "situational", 6, NONE),
      // all flags set must not lift the rate, none set must not lower it
      answered("c", "technical", 9, FULL),
      answered("d", "technical", 3, NONE),
      question({ id: "e", ordinal: 4 }),
    ]);

    expect(report).toEqual({
      question_count: 5,
      answered_count: 4,
      average_score: 6.5,
      star_coverage_rate: 0.5,
    });
  });

  // Full coverage means all FOUR parts. Dropping any single one, whichever it is, takes an
  // otherwise complete answer out of the numerator -- the same fixtures as
  // test_interview_practice.py, so the two builders cannot drift on this rule.
  it.each(["situation", "task", "action", "result"] as const)(
    "does not count a story answer missing only the %s part as full coverage",
    (part) => {
      const report = buildSessionReport([
        answered("a", "behavioral", 5, { ...FULL, [part]: false }),
      ]);

      expect(report.star_coverage_rate).toBe(0);
    },
  );

  it.each(["situation", "task", "action", "result"] as const)(
    "rates one full and one answer missing only the %s part at one half",
    (part) => {
      const report = buildSessionReport([
        answered("a", "behavioral", 5, FULL),
        answered("b", "situational", 5, { ...FULL, [part]: false }),
      ]);

      expect(report.star_coverage_rate).toBe(0.5);
    },
  );

  it("has no STAR rate when only technical questions were answered", () => {
    const report = buildSessionReport([
      answered("a", "technical", 7, FULL),
      answered("b", "technical", 8, null),
    ]);

    expect(report.star_coverage_rate).toBeNull();
    expect(report.average_score).toBe(7.5);
  });

  it("does not count a story answer that has no STAR data as a miss", () => {
    const report = buildSessionReport([
      answered("a", "behavioral", 5, FULL),
      answered("b", "behavioral", 5, null),
    ]);

    expect(report.star_coverage_rate).toBe(1);
  });

  it("rounds the rate to 2 decimals over the story answers only", () => {
    const report = buildSessionReport([
      answered("a", "behavioral", 5, FULL),
      answered("b", "situational", 5, NONE),
      answered("c", "behavioral", 5, NONE),
      answered("d", "technical", 5, FULL),
    ]);

    expect(report.star_coverage_rate).toBe(0.33);
  });

  // The same fixtures as test_interview_practice.py's STAR-scope tests: the two builders must
  // agree to the digit.
  it("matches the backend on the shared fixtures", () => {
    expect(
      buildSessionReport([
        answered("a", "behavioral", 8, FULL),
        answered("b", "situational", 6, NONE),
        answered("c", "technical", 9, FULL),
        answered("d", "technical", 3, NONE),
      ]).star_coverage_rate,
    ).toBe(0.5);
    expect(
      buildSessionReport([
        answered("a", "technical", 7, FULL),
        answered("b", "technical", 8, null),
      ]).average_score,
    ).toBe(7.5);
  });
});

describe("starSummary", () => {
  const base = { question_count: 5, answered_count: 2, average_score: 6 };

  it("says what the rate is a rate of", () => {
    const questions = [answered("a", "behavioral", 6, FULL), answered("b", "situational", 6, NONE)];
    expect(starSummary({ ...base, star_coverage_rate: 0.5 }, questions)).toBe(
      "full STAR coverage on 50% of behavioral and situational answers",
    );
  });

  it("notes that technical answers were not scored when there are some", () => {
    const questions = [answered("a", "behavioral", 6, FULL), answered("b", "technical", 6, null)];
    expect(starSummary({ ...base, star_coverage_rate: 1 }, questions)).toBe(
      "full STAR coverage on 100% of behavioral and situational answers (technical answers are not scored for STAR)",
    );
  });

  it("says STAR is not scored when only technical questions were answered", () => {
    const questions = [answered("a", "technical", 6, null)];
    expect(starSummary({ ...base, star_coverage_rate: null }, questions)).toBe(
      "STAR is not scored for technical questions",
    );
  });

  it("says nothing when nothing was answered", () => {
    expect(
      starSummary({ ...base, answered_count: 0, average_score: null, star_coverage_rate: null }, [
        question({ id: "a" }),
      ]),
    ).toBeNull();
  });

  it("does not mention technical questions that were never answered", () => {
    const questions = [
      answered("a", "behavioral", 6, FULL),
      question({ id: "b", question_type: "technical" }),
    ];
    expect(starSummary({ ...base, star_coverage_rate: 1 }, questions)).toBe(
      "full STAR coverage on 100% of behavioral and situational answers",
    );
  });
});
