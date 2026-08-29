// Headroom Ledger (honest-score-surfaces.md, S1) -- pure, testable logic for
// the ATS score breakdown. Kept separate from the rendering component so the
// step-function math (the part most likely to have an off-by-one) has real
// unit test coverage, per this repo's own "new behavior needs tests" rule --
// not backend-scoped, so the frontend doesn't get a pass.

import type {
  AtsAttempt,
  AtsScoreBreakdown,
  CompanyAlignmentDetail,
  ExperienceDetail,
  QuantificationDetail,
} from "./generateTypes";

// Verbatim from forge-engines' calculate_ats_score -- semantic 35 / experience
// 30 / hard-req 15 / quant 10 / company 5 / structure 5. Company + structure
// are shown merged as one row (Reality Check artboard 1's own layout), so
// their maxes are pre-summed here too.
export const SCORE_COMPONENT_MAX = {
  semantic_coverage: 35,
  experience_quality: 30,
  hard_req_score: 15,
  quantification: 10,
  company_and_structure: 10,
} as const;

export type BarState = "maxed" | "near max" | "has headroom";

export function barState(value: number, max: number): BarState {
  if (max <= 0) return "has headroom";
  if (value >= max) return "maxed";
  return value / max >= 0.6 ? "near max" : "has headroom";
}

export interface ScoreRow {
  key: string;
  label: string;
  value: number;
  max: number;
}

export function scoreRows(breakdown: AtsScoreBreakdown): ScoreRow[] {
  return [
    {
      key: "semantic_coverage",
      label: "Semantic coverage",
      value: breakdown.semantic_coverage,
      max: SCORE_COMPONENT_MAX.semantic_coverage,
    },
    {
      key: "experience_quality",
      label: "Experience quality",
      value: breakdown.experience_quality,
      max: SCORE_COMPONENT_MAX.experience_quality,
    },
    {
      key: "hard_req_score",
      label: "Hard requirements",
      value: breakdown.hard_req_score,
      max: SCORE_COMPONENT_MAX.hard_req_score,
    },
    {
      key: "quantification",
      label: "Quantified impact",
      value: breakdown.quantification,
      max: SCORE_COMPONENT_MAX.quantification,
    },
    {
      key: "company_and_structure",
      label: "Company alignment · structure",
      value: breakdown.company_alignment + breakdown.structure,
      max: SCORE_COMPONENT_MAX.company_and_structure,
    },
  ];
}

const MISSING_DEALBREAKER_PREFIX = "Missing dealbreaker: ";
const WEAK_REQUIREMENT_PREFIX = "Weak/missing requirement: ";

export interface ParsedGaps {
  missingDealbreakers: string[];
  weakRequirements: string[];
  other: string[];
}

// forge-engines emits gaps as pre-formatted prose with a fixed prefix per
// kind (ats_score.py's own `gaps` construction) -- same "parse a known
// prefix" pattern GeneratePanel.tsx already uses for pin-conflict warnings.
export function parseGaps(gaps: string[]): ParsedGaps {
  const missingDealbreakers: string[] = [];
  const weakRequirements: string[] = [];
  const other: string[] = [];
  for (const g of gaps) {
    if (g.startsWith(MISSING_DEALBREAKER_PREFIX)) {
      missingDealbreakers.push(g.slice(MISSING_DEALBREAKER_PREFIX.length));
    } else if (g.startsWith(WEAK_REQUIREMENT_PREFIX)) {
      weakRequirements.push(g.slice(WEAK_REQUIREMENT_PREFIX.length));
    } else {
      other.push(g);
    }
  }
  return { missingDealbreakers, weakRequirements, other };
}

// Verbatim step function from calculate_ats_score: 0 missing -> 15,
// 1 missing -> 8, 2+ missing -> 0.
export function hardReqScoreForMissingCount(missingCount: number): number {
  if (missingCount <= 0) return 15;
  if (missingCount === 1) return 8;
  return 0;
}

// The exact point gain from clearing exactly ONE more missing dealbreaker,
// whichever one -- the formula only counts, it doesn't care which. Honestly
// returns 0 when clearing just one wouldn't move the needle yet (3+ missing),
// rather than promising a gain that isn't there.
export function nextDealbreakerGain(missingCount: number): number {
  if (missingCount <= 0) return 0;
  return hardReqScoreForMissingCount(missingCount - 1) - hardReqScoreForMissingCount(missingCount);
}

// S2 (honest-score-surfaces.md): the "adjusted" what-if total after a
// dealbreaker assertion, recomputed from the OTHER 5 (unchanged) breakdown
// components plus a new hard_req_score -- not "current overall + delta",
// because the current overall may already be silently capped at 65
// (calculate_ats_score's own rule), and clearing enough dealbreakers can
// lift that cap entirely. Re-deriving the full sum and re-applying the same
// cap rule handles both cases correctly with no special-casing.
export function adjustedOverallScore(breakdown: AtsScoreBreakdown, newHardReqScore: number): number {
  const sum =
    breakdown.semantic_coverage +
    breakdown.experience_quality +
    newHardReqScore +
    breakdown.quantification +
    breakdown.company_alignment +
    breakdown.structure;
  return newHardReqScore < 8 ? Math.min(sum, 65) : sum;
}

// True only when the hard-req shortfall is actually the thing holding the
// total down -- i.e. the sum of the visible parts exceeds what was actually
// awarded. calculate_ats_score's cap (`hard_req_score < 8` -> `min(total, 65)`)
// always applies in that case, but it only visibly matters when it truly
// clipped something.
export function hardReqCapIsBinding(breakdown: AtsScoreBreakdown, overallScore: number): boolean {
  if (breakdown.hard_req_score >= 8) return false;
  const sum =
    breakdown.semantic_coverage +
    breakdown.experience_quality +
    breakdown.hard_req_score +
    breakdown.quantification +
    breakdown.company_alignment +
    breakdown.structure;
  return sum > overallScore;
}

export function latestAttempt(attempts: AtsAttempt[]): AtsAttempt | null {
  return attempts.length > 0 ? attempts[attempts.length - 1] : null;
}

// ForgeScore chart-UI pass -- surfacing sub-signals `calculate_ats_score`
// already computes (S3's own `AtsScoreDetail`) but that were never actually
// rendered anywhere: quantification/company-alignment/experience-years.
// Each returns `undefined` when there's nothing real to say (an older
// attempt predating S3, or -- for experience -- no years to report at all)
// rather than a caption built from a guessed default.

export function quantificationCaption(detail: QuantificationDetail | undefined): string | undefined {
  if (!detail) return undefined;
  const count = Math.round(detail.metrics_count);
  const metricWord = count === 1 ? "metric" : "metrics";
  return `${count} quantified ${metricWord} found, judged ${Math.round(detail.relevance_score)}% relevant`;
}

export function experienceYearsCaption(detail: ExperienceDetail | undefined): string | undefined {
  if (!detail || detail.total_years <= 0) return undefined;
  const pct = Math.round(detail.relevance_ratio * 100);
  return `${detail.relevant_years.toFixed(1)} of ${detail.total_years.toFixed(1)} years judged relevant (${pct}%)`;
}

export function companyAlignmentCaption(detail: CompanyAlignmentDetail | undefined): string | undefined {
  if (!detail) return undefined;
  return `Culture-fit signal ${Math.round(detail.culture_score)}%`;
}

// The one real, already-applied penalty behind company_alignment worth its
// own callout, same reasoning as the hard-req cap callout below --
// `calculate_ats_score` subtracts 8 points outright when this fires
// (ats_score.py's culture-fit block), not a soft/estimated signal.
export function whiteTextSpamPenaltyApplied(detail: CompanyAlignmentDetail | undefined): boolean {
  return detail?.has_white_text_spam === true;
}

// S3 (honest-score-surfaces.md): the ONE exact, discrete lever behind
// experience_quality -- calculate_ats_score's `management_bonus` is a flat
// +5 gated on a boolean, not a continuous signal like relevance_ratio, so
// it's the only sub-signal here honest enough to draw as a dashed "exactly
// this many points, from exactly this cause" segment. Derives both sides
// fresh from relevance_ratio (not the server-rounded breakdown value) so
// the two independent roundings can never disagree.
export function experienceManagementHeadroom(detail: ExperienceDetail | undefined): number {
  if (!detail || detail.has_management) return 0;
  const withoutBonus = Math.min(detail.relevance_ratio * 25, 30);
  const withBonus = Math.min(detail.relevance_ratio * 25 + 5, 30);
  return Math.round(withBonus - withoutBonus);
}
