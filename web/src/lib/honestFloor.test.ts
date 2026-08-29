import { describe, expect, it } from "vitest";
import type { ForgeScoreDimensions } from "./generateTypes";
import { dimensionRows, fitBadgeLabel } from "./honestFloor";

describe("dimensionRows", () => {
  it("returns all six dimensions, in weight order, when the LLM provided every one", () => {
    const dimensions: ForgeScoreDimensions = {
      skills_match: 7,
      experience_relevance: 6,
      metric_impact: 8,
      seniority_fit: 5,
      keyword_coverage: 9,
      leadership_signals: 4,
    };
    const rows = dimensionRows(dimensions);
    expect(rows.map((r) => r.key)).toEqual([
      "skills_match",
      "experience_relevance",
      "metric_impact",
      "seniority_fit",
      "keyword_coverage",
      "leadership_signals",
    ]);
    expect(rows.map((r) => r.weightPct)).toEqual([25, 25, 15, 15, 10, 10]);
  });

  it("omits a dimension the LLM left out entirely, rather than treating it as zero", () => {
    const dimensions: ForgeScoreDimensions = { skills_match: 7, leadership_signals: 4 };
    const rows = dimensionRows(dimensions);
    expect(rows.map((r) => r.key)).toEqual(["skills_match", "leadership_signals"]);
  });

  it("returns an empty list for an empty dimensions object", () => {
    expect(dimensionRows({})).toEqual([]);
  });
});

describe("fitBadgeLabel", () => {
  it("maps each known recommendation to its own label", () => {
    expect(fitBadgeLabel("Apply")).toBe("Still worth a look");
    expect(fitBadgeLabel("Caution")).toBe("Borderline fit");
    expect(fitBadgeLabel("Skip")).toBe("Poor fit");
  });

  it("falls back to 'Poor fit' for an empty or unrecognized recommendation", () => {
    expect(fitBadgeLabel("")).toBe("Poor fit");
    expect(fitBadgeLabel("something-unexpected")).toBe("Poor fit");
  });
});
