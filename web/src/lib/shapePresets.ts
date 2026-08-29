import type { ShapeOverrides } from "./headerComposerTypes";

// Studio tier presets (R6, resumeforge-shape-and-fit.md) -- starting
// points a user's own edits can freely diverge from afterward, not an
// enforced or hidden mode: picking one just pre-fills the settings form
// with a `ShapeOverrides` value, the exact same shape a manual edit would
// produce. Per the plan's own "over-configurability trap" citation, this
// is deliberately a small, fixed catalog rather than a user-authored
// preset system.
export interface ShapePreset {
  key: string;
  label: string;
  description: string;
  overrides: ShapeOverrides;
}

export const SHAPE_PRESETS: ShapePreset[] = [
  {
    key: "ai_engineer",
    label: "AI Engineer",
    description: "Technical, experience-first -- no summary, plain bullets.",
    overrides: { summary: "off", bullet_style: "plain", density: "balanced", show_gpa: false },
  },
  {
    key: "research",
    label: "Research",
    description: "Leads with a summary framing your research focus; denser layout.",
    overrides: { summary: "on", bullet_style: "plain", density: "compact", show_gpa: false },
  },
  {
    key: "new_grad",
    label: "New Grad",
    description: "Surfaces GPA, no summary -- lets projects and coursework carry the page.",
    overrides: { summary: "off", bullet_style: "plain", density: "balanced", show_gpa: true },
  },
  {
    key: "senior",
    label: "Senior",
    description: "Opens with a summary framing scope and impact.",
    overrides: { summary: "on", bullet_style: "plain", density: "balanced", show_gpa: false },
  },
];
