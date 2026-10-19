import { describe, expect, it } from "vitest";
import type { PrepareApplicationResult } from "./generateTypes";
import { sourceWords } from "./applicationsView";
import { buildReceipt, buildTrail, findForbiddenWord } from "./trail";
import { checklistTrail, claimTrail, fitCeiling, generationTrail, headerTrail, scoreTrail, stateSummary, tailorTrail } from "./workspaceTrails";

const NOW = new Date("2026-10-19T12:00:00Z");

function result(overrides: Partial<PrepareApplicationResult> = {}): PrepareApplicationResult {
  return {
    run_id: "0123456789abcdef",
    resume: { artifact_id: "a", version_id: "v", media_type: "application/pdf", sha256: "f00dfeedbabe0123456789" },
    ats_attempts: [
      { overall_score: 64, breakdown: { semantic_coverage: 20, experience_quality: 20, hard_req_score: 8, quantification: 6, company_alignment: 5, structure: 5 }, confidence: "medium", rating: "Moderate", gaps: [] },
      { overall_score: 72, breakdown: { semantic_coverage: 27, experience_quality: 22, hard_req_score: 8, quantification: 6, company_alignment: 4, structure: 5 }, confidence: "medium", rating: "Moderate", gaps: [] },
    ],
    final_score: 72,
    score_scale: "ats_100",
    gate_outcome: "proceed",
    gate_cautions: [],
    warnings: [],
    ...overrides,
  };
}

const text = (parts: { trail: Parameters<typeof buildTrail>[0] }) => buildTrail(parts.trail)?.text;

describe("the page's own trail", () => {
  it("words the channel exactly as the application's card in the list does", () => {
    for (const channel of ["discover", "web", "telegram", "discord", "carrier-pigeon"]) {
      expect(headerTrail({ source_channel: channel, created_at: "2026-09-22T11:00:00Z" }, NOW).basis, channel).toBe(sourceWords(channel));
    }
  });

  it.each([
    ["discover", "You tracked this · from Discover · Sep 22"],
    ["web", "You tracked this · added on the web · Sep 22"],
    ["telegram", "You tracked this · from Telegram · Sep 22"],
    ["discord", "You tracked this · from Discord · Sep 22"],
  ])("reads a %s track", (channel, words) => {
    expect(buildTrail(headerTrail({ source_channel: channel, created_at: "2026-09-22T11:00:00Z" }, NOW))?.text).toBe(words);
  });

  it("leaves out a channel it does not know, and a date that did not come back", () => {
    expect(buildTrail(headerTrail({ source_channel: "carrier-pigeon", created_at: "2026-09-22T11:00:00Z" }, NOW))?.text).toBe("You tracked this · Sep 22");
    expect(buildTrail(headerTrail({ source_channel: "discover" }, NOW))?.text).toBe("You tracked this · from Discover");
    expect(buildTrail(headerTrail({ created_at: "not a date" }, NOW))?.text).toBe("You tracked this");
  });

  it("writes the year for a date from another year", () => {
    expect(buildTrail(headerTrail({ source_channel: "web", created_at: "2025-12-30T11:00:00Z" }, NOW))?.text).toBe("You tracked this · added on the web · Dec 30, 2025");
  });
});

describe("a generation", () => {
  it("says who drafted it and on what, without a model name or a time", () => {
    const parts = generationTrail(result(), { restored: false, flagged: 1, earlierResume: false });
    expect(text(parts)).toBe("Drafted by your model · on your model key · 1 flagged");
    expect(parts.trail.glyph).toBe("model");
  });

  it("leaves the count out when nothing was flagged", () => {
    expect(text(generationTrail(result(), { restored: false, flagged: 0, earlierResume: false }))).toBe("Drafted by your model · on your model key");
  });

  it("builds the receipt from the returned run id, hash, attempts and gate only", () => {
    const parts = generationTrail(result({ gate_cautions: ["thin evidence for the leadership requirement"] }), { restored: false, flagged: 0, earlierResume: false });
    expect(buildReceipt(parts.receipt)?.rows).toEqual([
      { label: "run", value: "01234567" },
      { label: "pdf", value: "f00dfeedbabe" },
      { label: "ATS", value: "64 -> 72" },
      { label: "gate", value: "proceed · thin evidence for the leadership requirement" },
    ]);
  });

  it("marks a restored result as restored with no date, and an older resume as from an earlier generation", () => {
    const parts = generationTrail(result(), { restored: true, flagged: 0, earlierResume: true });
    const rows = buildReceipt(parts.receipt)?.rows ?? [];
    expect(rows).toContainEqual({ note: "Restored from your last stored generation. No time came back with it." });
    expect(rows).toContainEqual({ note: "From an earlier successful generation." });
  });

  it("leaves a slot out when its field did not come back: no run id, no resume hash, no attempts, no gate", () => {
    const bare = result({ run_id: "", resume: null, ats_attempts: [], gate_outcome: null, gate_cautions: undefined });
    expect(buildReceipt(generationTrail(bare, { restored: false, flagged: 0, earlierResume: false }).receipt)).toBeNull();
  });

  it("draws one attempt as the one score, and equal attempts as one score", () => {
    const one = result({ ats_attempts: [result().ats_attempts[1]] });
    expect(buildReceipt(generationTrail(one, { restored: false, flagged: 0, earlierResume: false }).receipt)?.rows).toContainEqual({ label: "ATS", value: "72" });
    const same = result({ ats_attempts: [result().ats_attempts[1], result().ats_attempts[1]] });
    expect(buildReceipt(generationTrail(same, { restored: false, flagged: 0, earlierResume: false }).receipt)?.rows).toContainEqual({ label: "ATS", value: "72" });
  });

  it("describes a held-back attempt as held back, not as a draft", () => {
    const held = result({ resume: null, ats_attempts: [], gate_outcome: "REJECT_MISMATCH" });
    const parts = generationTrail(held, { restored: true, flagged: 0, earlierResume: false });
    expect(text(parts)).toBe("Held back by the fit check · no resume was generated");
    expect(parts.trail.glyph).toBe("evidence");
    expect(buildReceipt(parts.receipt)?.rows).toContainEqual({ label: "gate", value: "REJECT_MISMATCH" });
  });
});

describe("the score, the claim check, the export checks and Tailor", () => {
  it("calls the score code over a model's signals: engine, never the star alone", () => {
    const parts = scoreTrail(result());
    expect(buildTrail(parts.trail)?.text).toBe("engine · code-weighted from your model's sub-scores");
    expect(parts.trail.glyph).toBe("engine");
    expect(buildReceipt(parts.receipt)?.rows).toContainEqual({ label: "attempts", value: "2" });
  });

  it("says what the claim check compares, and that a check that did not run reads the same as none flagged", () => {
    const parts = claimTrail(2);
    expect(text(parts)).toBe("Claim check · claims compared to the achievements picked for this draft · 2 flagged");
    expect(text(claimTrail(0))).toBe("Claim check · claims compared to the achievements picked for this draft");
    expect(JSON.stringify(parts.receipt)).toContain("did not run reads the same");
  });

  it("calls the export checks code, no model, and says they recompile", () => {
    const parts = checklistTrail();
    expect(text(parts)).toBe("Checked by code · no model involved");
    expect(JSON.stringify(parts.receipt)).toContain("recompile");
  });

  it("calls Tailor an engine's work: a model extracted the clusters, code matched them", () => {
    const parts = tailorTrail({ clusters: 7, skills: 12, profileVersionId: "9f8e7d6c5b4a" });
    expect(buildTrail(parts.trail)?.text).toBe("engine · clusters extracted by a model · matched to your profile by code");
    expect(buildReceipt(parts.receipt)?.rows.slice(0, 3)).toEqual([
      { label: "clusters", value: "7" },
      { label: "skills", value: "12" },
      { label: "profile", value: "9f8e7d6c" },
    ]);
  });

  it("never says a word a trail does not say", () => {
    for (const parts of [generationTrail(result(), { restored: false, flagged: 1, earlierResume: false }), scoreTrail(result()), claimTrail(1), checklistTrail(), tailorTrail({ clusters: 1, skills: 1, profileVersionId: null })]) {
      expect(findForbiddenWord(buildTrail(parts.trail)?.text ?? "")).toBeNull();
    }
  });
});

describe("the compact state", () => {
  it.each([
    [{ status: "saved", hasResume: false, latest: null, atsScore: null, flagged: null }, "Status saved · no resume yet"],
    [{ status: "saved", hasResume: true, latest: "drafted", atsScore: 72, flagged: 1 }, "Status saved · resume drafted · ATS 72 / 100 · 1 claim flagged"],
    [{ status: "saved", hasResume: true, latest: "drafted", atsScore: 72, flagged: 0 }, "Status saved · resume drafted · ATS 72 / 100 · 0 claims flagged"],
    [{ status: "applied", hasResume: true, latest: null, atsScore: null, flagged: null }, "Status applied · resume drafted"],
    [{ status: "saved", hasResume: false, latest: "held_back", atsScore: null, flagged: null }, "Status saved · latest attempt held back, no resume"],
    [{ status: "saved", hasResume: true, latest: "held_back", atsScore: null, flagged: null }, "Status saved · latest attempt held back, earlier resume kept"],
    [{ status: "ghosted", hasResume: false, latest: null, atsScore: null, flagged: null }, "Status not shown · no resume yet"],
  ] as const)("reads %j", (parts, words) => {
    expect(stateSummary(parts)).toBe(words);
  });

  it("reads a fit ceiling only when the score is a number", () => {
    expect(fitCeiling(undefined)).toBeNull();
    expect(fitCeiling(null)).toBeNull();
    expect(fitCeiling({ overall_score: 3.6 } as never)).toBe(3.6);
    expect(fitCeiling({ overall_score: Number.NaN } as never)).toBeNull();
  });
});
