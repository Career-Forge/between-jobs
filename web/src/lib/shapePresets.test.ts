import { describe, expect, it } from "vitest";
import { SKILL_CATEGORY_LABELS } from "./profileTypes";
import { SHAPE_PRESETS } from "./shapePresets";

// The values the backend's ShapeOverrides model accepts (models.py); a preset that sends
// anything else is rejected with a 422 the moment someone clicks it.
const SUMMARY = ["auto", "on", "off"];
const BULLET_STYLE = ["plain", "bold_lead_in"];
const DENSITY = ["compact", "balanced", "spacious"];
const PAGE_COUNT = ["auto", "1", "2"];

describe("SHAPE_PRESETS", () => {
  it("has unique keys and labels", () => {
    expect(new Set(SHAPE_PRESETS.map((p) => p.key)).size).toBe(SHAPE_PRESETS.length);
    expect(new Set(SHAPE_PRESETS.map((p) => p.label)).size).toBe(SHAPE_PRESETS.length);
  });

  it("sends only values the backend accepts", () => {
    for (const preset of SHAPE_PRESETS) {
      const { summary, bullet_style, density, page_count, show_gpa, ...rest } = preset.overrides;
      expect(rest).toEqual({});
      if (summary != null) expect(SUMMARY).toContain(summary);
      if (bullet_style != null) expect(BULLET_STYLE).toContain(bullet_style);
      if (density != null) expect(DENSITY).toContain(density);
      if (page_count != null) expect(PAGE_COUNT).toContain(page_count);
      if (show_gpa != null) expect(typeof show_gpa).toBe("boolean");
    }
  });

  it("no longer names a preset after one job title", () => {
    expect(SHAPE_PRESETS.map((p) => p.label)).not.toContain("AI Engineer");
    expect(SHAPE_PRESETS.map((p) => p.key)).not.toContain("ai_engineer");
  });

  it("keeps what the renamed preset does: no summary, plain bullets, balanced, no GPA", () => {
    const technical = SHAPE_PRESETS.find((p) => p.label === "Technical, experience-first");
    expect(technical?.overrides).toEqual({
      summary: "off",
      bullet_style: "plain",
      density: "balanced",
      show_gpa: false,
    });
  });

  it("offers a Data / Analytics preset that leads with a summary and highlights tools", () => {
    const analytics = SHAPE_PRESETS.find((p) => p.label === "Data / Analytics");
    expect(analytics?.overrides).toEqual({
      summary: "on",
      bullet_style: "bold_lead_in",
      density: "balanced",
      show_gpa: false,
    });
  });

  it("leaves the other presets as they were", () => {
    const byLabel = Object.fromEntries(SHAPE_PRESETS.map((p) => [p.label, p.overrides]));
    expect(byLabel["Research"]).toEqual({
      summary: "on",
      bullet_style: "plain",
      density: "compact",
      show_gpa: false,
    });
    expect(byLabel["New Grad"]).toEqual({
      summary: "off",
      bullet_style: "plain",
      density: "balanced",
      show_gpa: true,
    });
    expect(byLabel["Senior"]).toEqual({
      summary: "on",
      bullet_style: "plain",
      density: "balanced",
      show_gpa: false,
    });
  });
});

describe("SKILL_CATEGORY_LABELS", () => {
  it("shows data_mlops as Data & Pipelines and keeps the six keys", () => {
    expect(SKILL_CATEGORY_LABELS.data_mlops).toBe("Data & Pipelines");
    expect(Object.keys(SKILL_CATEGORY_LABELS)).toEqual([
      "programming",
      "ai_ml",
      "data_mlops",
      "cloud_devops",
      "tools",
      "other",
    ]);
  });

  it("no longer calls any category MLOps", () => {
    for (const label of Object.values(SKILL_CATEGORY_LABELS)) {
      expect(label).not.toMatch(/mlops/i);
    }
  });
});
