import { describe, expect, it } from "vitest";
import { MAX_REPORTED_FIELDS, summarizeFill } from "@/lib/fillOutcome";
import type { FillResult } from "@/lib/types";

function result(overrides: Partial<FillResult> = {}): FillResult {
  return {
    filledFields: [],
    skippedFields: [],
    resumeAttached: false,
    resumeError: null,
    coverLetterAttached: false,
    coverLetterError: null,
    resumeWritten: false,
    coverLetterWritten: false,
    unresolvedQuestions: [],
    fieldMapError: null,
    fillError: null,
    ...overrides,
  };
}

describe("summarizeFill", () => {
  it("a fill that put everything it tried in place is ok", () => {
    expect(summarizeFill(result({ filledFields: ["#first_name", "#last_name", "#email"], resumeAttached: true, resumeWritten: true }))).toEqual({
      fieldsAttempted: 4,
      fieldsFilled: 4,
      outcome: "ok",
    });
  });

  it("a fill that left some fields empty on purpose is partial, and counts them as tried", () => {
    expect(
      summarizeFill(
        result({
          filledFields: ["#first_name", "#email"],
          skippedFields: ["Last name: not filled -- your profile name has only one part."],
          resumeAttached: true,
          resumeWritten: true,
        }),
      ),
    ).toEqual({ fieldsAttempted: 4, fieldsFilled: 3, outcome: "partial" });
  });

  it("a résumé or cover letter that could not be attached counts as tried and not filled", () => {
    expect(
      summarizeFill(result({ filledFields: ["#email"], resumeAttached: true, resumeWritten: true, coverLetterError: "No cover letter was generated for this application yet." })),
    ).toEqual({ fieldsAttempted: 3, fieldsFilled: 2, outcome: "partial" });
  });

  it("an attached cover letter counts as tried and filled, on its own and next to a résumé", () => {
    expect(summarizeFill(result({ filledFields: ["#a"], coverLetterAttached: true, coverLetterWritten: true }))).toEqual({
      fieldsAttempted: 2,
      fieldsFilled: 2,
      outcome: "ok",
    });
    expect(
      summarizeFill(
        result({
          filledFields: ["#a", "#b"],
          resumeAttached: true,
          resumeWritten: true,
          coverLetterAttached: true,
          coverLetterWritten: true,
        }),
      ),
    ).toEqual({ fieldsAttempted: 4, fieldsFilled: 4, outcome: "ok" });
  });

  it("a file that was already on the input (not written by this fill) is neither tried nor filled", () => {
    expect(summarizeFill(result({ resumeAttached: true, coverLetterAttached: true }))).toBeNull();
    expect(summarizeFill(result({ filledFields: ["#a"], resumeAttached: true, coverLetterAttached: true }))).toEqual({
      fieldsAttempted: 1,
      fieldsFilled: 1,
      outcome: "ok",
    });
  });

  it("a replacement that failed over an existing file is tried and not filled", () => {
    expect(
      summarizeFill(result({ resumeAttached: true, resumeWritten: false, resumeError: "Failed to attach file" })),
    ).toEqual({ fieldsAttempted: 1, fieldsFilled: 0, outcome: "failed" });
  });

  it("a fill that tried and filled nothing failed", () => {
    expect(summarizeFill(result({ skippedFields: ["Country: not filled -- your profile has no country."], resumeError: "network down" }))).toEqual({
      fieldsAttempted: 2,
      fieldsFilled: 0,
      outcome: "failed",
    });
  });

  it("a fill that threw is failed with nothing written, partial with something", () => {
    expect(summarizeFill(result({ fillError: "boom" }))).toEqual({ fieldsAttempted: 0, fieldsFilled: 0, outcome: "failed" });
    expect(summarizeFill(result({ filledFields: ["#a", "#b"], fillError: "boom" }))).toEqual({
      fieldsAttempted: 2,
      fieldsFilled: 2,
      outcome: "partial",
    });
  });

  it("a fill whose signed map could not be used is partial even if everything it tried went in", () => {
    expect(summarizeFill(result({ filledFields: ["#a", "#b"], fieldMapError: "No field map has been published for this ATS yet." }))).toEqual({
      fieldsAttempted: 2,
      fieldsFilled: 2,
      outcome: "partial",
    });
  });

  it("a fill that found everything already in place tried nothing, so there is nothing to report", () => {
    expect(summarizeFill(result())).toBeNull();
    expect(summarizeFill(result({ unresolvedQuestions: [{ fieldName: "question_1", label: "Why us?", kind: "text" }] }))).toBeNull();
  });

  it("never reports more filled than tried, or more than the service accepts", () => {
    const many = Array.from({ length: 1500 }, (_, i) => `#f${i}`);
    expect(summarizeFill(result({ filledFields: many }))).toEqual({
      fieldsAttempted: MAX_REPORTED_FIELDS,
      fieldsFilled: MAX_REPORTED_FIELDS,
      outcome: "ok",
    });
  });

  it("reports counts and one word and nothing else -- no text from the result can reach it", () => {
    const secret = "SECRET-LABEL-4471 alice@example.com https://jobs.lever.co/acme/token123";
    const counts = summarizeFill(
      result({
        filledFields: [`input[name="${secret}"]`],
        skippedFields: [secret],
        resumeError: secret,
        coverLetterError: secret,
        fieldMapError: secret,
        unresolvedQuestions: [{ fieldName: secret, label: secret, kind: "text" }],
      }),
    );
    expect(Object.keys(counts!).sort()).toEqual(["fieldsAttempted", "fieldsFilled", "outcome"]);
    expect(JSON.stringify(counts)).not.toMatch(/SECRET|alice|token123|lever\.co/);
    expect(typeof counts!.fieldsAttempted).toBe("number");
    expect(typeof counts!.fieldsFilled).toBe("number");
  });
});
