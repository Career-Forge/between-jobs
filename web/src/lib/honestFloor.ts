// ForgeScore chart-UI pass -- pure, testable logic for HonestFloor's
// dimension breakdown and recommendation-driven badge, split out from the
// render component per this repo's own "new behavior needs tests" rule,
// same pattern as scoreBreakdown.ts.

import type { ForgeScoreDimensions } from "./generateTypes";

// Verbatim weights from forge-engines' build_forge_score_system.
const DIMENSION_META: { key: keyof ForgeScoreDimensions; label: string; weightPct: number }[] = [
  { key: "skills_match", label: "Skills match", weightPct: 25 },
  { key: "experience_relevance", label: "Experience relevance", weightPct: 25 },
  { key: "metric_impact", label: "Metric impact", weightPct: 15 },
  { key: "seniority_fit", label: "Seniority fit", weightPct: 15 },
  { key: "keyword_coverage", label: "Keyword coverage", weightPct: 10 },
  { key: "leadership_signals", label: "Leadership signals", weightPct: 10 },
];

export interface DimensionRow {
  key: string;
  label: string;
  value: number;
  weightPct: number;
}

// Only dimensions the LLM actually returned -- an omitted dimension means
// unscored, not zero, per "unknown labeled as unknown, never guessed."
export function dimensionRows(dimensions: ForgeScoreDimensions): DimensionRow[] {
  return DIMENSION_META.filter((d) => typeof dimensions[d.key] === "number").map((d) => ({
    key: d.key,
    label: d.label,
    value: dimensions[d.key] as number,
    weightPct: d.weightPct,
  }));
}

const RECOMMENDATION_LABELS: Record<string, string> = {
  Apply: "Still worth a look",
  Caution: "Borderline fit",
  Skip: "Poor fit",
};

// Driven by the LLM's own recommendation rather than a hardcoded string --
// the gate that shows HonestFloor at all (REJECT_MISMATCH or
// SKIP_LOW_SCORE) is a SEPARATE signal from `recommendation` for a mismatch
// decline (seniority.py's classifier, not forge_score's own threshold), so
// the two can genuinely disagree. Defaults to "Poor fit" only when
// `recommendation` itself is empty (a not-yet-updated response), never
// guesses at an unrecognized value.
export function fitBadgeLabel(recommendation: string): string {
  return RECOMMENDATION_LABELS[recommendation] ?? "Poor fit";
}
