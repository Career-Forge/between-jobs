import { describe, expect, it } from "vitest";
import type {
  AtsScoreBreakdown,
  CompanyAlignmentDetail,
  ExperienceDetail,
  QuantificationDetail,
} from "./generateTypes";
import {
  adjustedOverallScore,
  barState,
  companyAlignmentCaption,
  experienceManagementHeadroom,
  experienceYearsCaption,
  hardReqCapIsBinding,
  hardReqScoreForMissingCount,
  latestAttempt,
  nextDealbreakerGain,
  parseGaps,
  quantificationCaption,
  scoreRows,
  whiteTextSpamPenaltyApplied,
} from "./scoreBreakdown";

function breakdown(overrides: Partial<AtsScoreBreakdown> = {}): AtsScoreBreakdown {
  return {
    semantic_coverage: 0,
    experience_quality: 0,
    hard_req_score: 0,
    quantification: 0,
    company_alignment: 0,
    structure: 0,
    ...overrides,
  };
}

describe("barState", () => {
  it("is maxed when value reaches max", () => {
    expect(barState(35, 35)).toBe("maxed");
    expect(barState(40, 35)).toBe("maxed"); // never actually happens, but don't crash on it
  });

  it("is near max at 60% or above, below the max itself", () => {
    expect(barState(21, 35)).toBe("near max"); // exactly 60%
    expect(barState(20, 35)).toBe("has headroom"); // just under 60%
  });

  it("treats a zero max as headroom rather than dividing by zero", () => {
    expect(barState(0, 0)).toBe("has headroom");
  });
});

describe("scoreRows", () => {
  it("merges company_alignment and structure into one row against a combined max of 10", () => {
    const rows = scoreRows(breakdown({ company_alignment: 4, structure: 4 }));
    const combined = rows.find((r) => r.key === "company_and_structure");
    expect(combined?.value).toBe(8);
    expect(combined?.max).toBe(10);
  });

  it("produces exactly 5 rows, in the artboard's order", () => {
    const rows = scoreRows(breakdown());
    expect(rows.map((r) => r.key)).toEqual([
      "semantic_coverage",
      "experience_quality",
      "hard_req_score",
      "quantification",
      "company_and_structure",
    ]);
  });
});

describe("parseGaps", () => {
  it("splits dealbreakers, weak requirements, and anything else into separate buckets", () => {
    const parsed = parseGaps([
      "Missing dealbreaker: On-site role",
      "Weak/missing requirement: Hardware-Efficient AI & Kernels",
      "Missing dealbreaker: 5+ years Python",
      "some other gap string with no known prefix",
    ]);
    expect(parsed.missingDealbreakers).toEqual(["On-site role", "5+ years Python"]);
    expect(parsed.weakRequirements).toEqual(["Hardware-Efficient AI & Kernels"]);
    expect(parsed.other).toEqual(["some other gap string with no known prefix"]);
  });

  it("returns empty buckets for an empty gaps list", () => {
    expect(parseGaps([])).toEqual({ missingDealbreakers: [], weakRequirements: [], other: [] });
  });
});

describe("hardReqScoreForMissingCount -- verbatim mirror of calculate_ats_score's step function", () => {
  it("maps 0/1/2+ missing dealbreakers to 15/8/0", () => {
    expect(hardReqScoreForMissingCount(0)).toBe(15);
    expect(hardReqScoreForMissingCount(1)).toBe(8);
    expect(hardReqScoreForMissingCount(2)).toBe(0);
    expect(hardReqScoreForMissingCount(5)).toBe(0);
  });
});

describe("nextDealbreakerGain -- the '+N exact' annotation math", () => {
  it("is +7 when exactly one dealbreaker is missing (15 - 8)", () => {
    expect(nextDealbreakerGain(1)).toBe(7);
  });

  it("is +8 when exactly two are missing -- clearing either one reaches 8 (8 - 0)", () => {
    expect(nextDealbreakerGain(2)).toBe(8);
  });

  it("is honestly 0 when three or more are missing -- clearing just one doesn't move the needle yet", () => {
    expect(nextDealbreakerGain(3)).toBe(0);
    expect(nextDealbreakerGain(4)).toBe(0);
  });

  it("is 0 when nothing is missing -- there's nothing to clear", () => {
    expect(nextDealbreakerGain(0)).toBe(0);
  });
});

describe("hardReqCapIsBinding", () => {
  it("is false whenever hard_req_score is already 8 or 15 -- the cap only ever applies below 8", () => {
    expect(hardReqCapIsBinding(breakdown({ hard_req_score: 8 }), 100)).toBe(false);
    expect(hardReqCapIsBinding(breakdown({ hard_req_score: 15 }), 100)).toBe(false);
  });

  it("is true when the visible parts sum above what was actually awarded", () => {
    // 35 + 30 + 0 + 10 + 4 + 4 = 83 raw, but calculate_ats_score would have
    // capped the real total at 65 -- the frontend only has the rounded
    // components + the final overall_score to compare, so it detects the
    // clip by noticing the sum exceeds what came back.
    const b = breakdown({
      semantic_coverage: 35,
      experience_quality: 30,
      hard_req_score: 0,
      quantification: 10,
      company_alignment: 4,
      structure: 4,
    });
    expect(hardReqCapIsBinding(b, 65)).toBe(true);
  });

  it("is false when hard_req is low but the sum never would have exceeded the actual score anyway", () => {
    const b = breakdown({
      semantic_coverage: 10,
      experience_quality: 5,
      hard_req_score: 0,
      quantification: 2,
      company_alignment: 1,
      structure: 1,
    });
    expect(hardReqCapIsBinding(b, 19)).toBe(false);
  });
});

describe("adjustedOverallScore -- the 'what if I assert this' math", () => {
  it("adds the hard-req delta straight through when the cap was never involved", () => {
    // Before (hard_req=0): 20+15+0+5+3+3=46. After (hard_req=8):
    // 20+15+8+5+3+3=54 -- well under 65 either way, so the cap never enters it.
    const b = breakdown({
      semantic_coverage: 20,
      experience_quality: 15,
      hard_req_score: 0,
      quantification: 5,
      company_alignment: 3,
      structure: 3,
    });
    expect(adjustedOverallScore(b, 8)).toBe(54);
  });

  it("lifts the 65-cap once the new hard_req_score clears the cap threshold", () => {
    // The exact case that motivated this function: raw parts sum to 83
    // (35+30+0+10+4+4), the CURRENT overall_score would show a capped 65,
    // but asserting a dealbreaker down to hard_req=15 must show the real,
    // uncapped 35+30+15+10+4+4=98 -- "current overall + delta" would
    // wrongly compute 65 + 15 = 80.
    const b = breakdown({
      semantic_coverage: 35,
      experience_quality: 30,
      hard_req_score: 0,
      quantification: 10,
      company_alignment: 4,
      structure: 4,
    });
    expect(adjustedOverallScore(b, 15)).toBe(98);
  });

  it("still applies the cap when the new hard_req_score is itself still below 8", () => {
    const b = breakdown({
      semantic_coverage: 35,
      experience_quality: 30,
      hard_req_score: 0,
      quantification: 10,
      company_alignment: 4,
      structure: 4,
    });
    // Only relevant with 3+ original dealbreakers, where clearing one still
    // leaves hard_req at 0 -- the cap must still bind.
    expect(adjustedOverallScore(b, 0)).toBe(65);
  });
});

function experienceDetail(overrides: Partial<ExperienceDetail> = {}): ExperienceDetail {
  return {
    relevant_years: 0,
    total_years: 0,
    relevance_ratio: 0,
    has_management: false,
    management_bonus: 0,
    ...overrides,
  };
}

describe("experienceManagementHeadroom -- the one exact lever behind experience_quality", () => {
  it("is 0 when management is already credited -- nothing left to gain", () => {
    expect(experienceManagementHeadroom(experienceDetail({ has_management: true }))).toBe(0);
  });

  it("is 0 when there is no detail at all -- an older attempt predating S3", () => {
    expect(experienceManagementHeadroom(undefined)).toBe(0);
  });

  it("is exactly 5 when relevance_ratio leaves room under the 30 cap", () => {
    // 0.6*25=15, +5=20 -- both well under 30, so the full flat bonus shows.
    expect(experienceManagementHeadroom(experienceDetail({ relevance_ratio: 0.6 }))).toBe(5);
  });

  it("shrinks once the 30 cap starts eating into the bonus", () => {
    // 0.9*25=22.5, +5=27.5 -- still under 30, so still the full 5.
    expect(experienceManagementHeadroom(experienceDetail({ relevance_ratio: 0.9 }))).toBe(5);
    // 1.0*25=25, +5=30 -- exactly at the cap, still the full 5.
    expect(experienceManagementHeadroom(experienceDetail({ relevance_ratio: 1.0 }))).toBe(5);
  });

  it("is 0 once relevance_ratio alone already hits the cap -- no room for any bonus", () => {
    // A pathological ratio >1 (relevant_years > total_years, bad extraction)
    // already clamps to 30 on its own; the bonus adds nothing further.
    expect(experienceManagementHeadroom(experienceDetail({ relevance_ratio: 1.3 }))).toBe(0);
  });
});

describe("quantificationCaption", () => {
  it("is undefined without a detail object -- an older attempt predating S3", () => {
    expect(quantificationCaption(undefined)).toBeUndefined();
  });

  it("singularizes 'metric' for exactly one", () => {
    const detail: QuantificationDetail = { metrics_count: 1, relevance_score: 80 };
    expect(quantificationCaption(detail)).toBe("1 quantified metric found, judged 80% relevant");
  });

  it("pluralizes for zero and for more than one", () => {
    expect(quantificationCaption({ metrics_count: 0, relevance_score: 0 })).toBe(
      "0 quantified metrics found, judged 0% relevant",
    );
    expect(quantificationCaption({ metrics_count: 6, relevance_score: 91 })).toBe(
      "6 quantified metrics found, judged 91% relevant",
    );
  });
});

function experienceDetailFor(overrides: Partial<ExperienceDetail>): ExperienceDetail {
  return {
    relevant_years: 0,
    total_years: 0,
    relevance_ratio: 0,
    has_management: false,
    management_bonus: 0,
    ...overrides,
  };
}

describe("experienceYearsCaption", () => {
  it("is undefined without a detail object", () => {
    expect(experienceYearsCaption(undefined)).toBeUndefined();
  });

  it("is undefined when total_years is zero -- nothing to report a ratio of", () => {
    expect(experienceYearsCaption(experienceDetailFor({ total_years: 0 }))).toBeUndefined();
  });

  it("formats years to one decimal and the ratio as a rounded percentage", () => {
    const detail = experienceDetailFor({
      relevant_years: 3.5,
      total_years: 7,
      relevance_ratio: 0.5,
    });
    expect(experienceYearsCaption(detail)).toBe("3.5 of 7.0 years judged relevant (50%)");
  });
});

describe("companyAlignmentCaption", () => {
  it("is undefined without a detail object", () => {
    expect(companyAlignmentCaption(undefined)).toBeUndefined();
  });

  it("rounds culture_score to a whole percentage", () => {
    const detail: CompanyAlignmentDetail = { culture_score: 77.6, has_white_text_spam: false };
    expect(companyAlignmentCaption(detail)).toBe("Culture-fit signal 78%");
  });
});

describe("whiteTextSpamPenaltyApplied", () => {
  it("is false without a detail object", () => {
    expect(whiteTextSpamPenaltyApplied(undefined)).toBe(false);
  });

  it("is true only when has_white_text_spam is true", () => {
    expect(whiteTextSpamPenaltyApplied({ culture_score: 50, has_white_text_spam: true })).toBe(
      true,
    );
    expect(whiteTextSpamPenaltyApplied({ culture_score: 50, has_white_text_spam: false })).toBe(
      false,
    );
  });
});

describe("latestAttempt", () => {
  it("returns the last attempt -- the post-regen score when a regen happened", () => {
    const attempts = [
      { overall_score: 65, breakdown: breakdown(), confidence: "low", rating: "Moderate Match", gaps: [] },
      { overall_score: 80, breakdown: breakdown(), confidence: "medium", rating: "Strong Match", gaps: [] },
    ];
    expect(latestAttempt(attempts)?.overall_score).toBe(80);
  });

  it("returns null for an empty list rather than throwing", () => {
    expect(latestAttempt([])).toBeNull();
  });
});
