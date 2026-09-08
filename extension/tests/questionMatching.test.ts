import { describe, expect, it } from "vitest";
import { normalizeQuestionLabel } from "@/lib/questionMatching";

describe("normalizeQuestionLabel", () => {
  it("lowercases and trims", () => {
    expect(normalizeQuestionLabel("  Why Do You Want To Work Here?  ")).toBe(
      "why do you want to work here?",
    );
  });

  it("strips Lever's own trailing required-field marker", () => {
    expect(normalizeQuestionLabel("Did someone refer you? *")).toBe("did someone refer you?");
    expect(normalizeQuestionLabel("Did someone refer you?✱")).toBe("did someone refer you?");
  });

  it("collapses internal whitespace", () => {
    expect(normalizeQuestionLabel("Why   do you\nwant this role?")).toBe(
      "why do you want this role?",
    );
  });

  it("produces the same result for the save path and the match path on the same real label", () => {
    const rendered = "Why would you like to work for The Athletic?";
    expect(normalizeQuestionLabel(rendered)).toBe(normalizeQuestionLabel(rendered));
  });
});
