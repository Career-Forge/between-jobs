// Fictional Discover data for the tests: companies, addresses and people are invented, and nothing is
// fetched. The shapes are the ones GET /discover returns (lib/discoverTypes.ts).

import type { DiscoverResponse, JobCard, JobScore } from "../lib/discoverTypes";

export function score(overrides: Partial<JobScore> = {}): JobScore {
  return {
    fit_score: 7,
    one_liner: "Strong overlap on data pipelines.",
    score100: 61,
    bin: "Good",
    bottleneck: "experience",
    sub_scores: { skills: 78, experience: 52, workauth: 60, location: 100, company_health: 60, compensation: 60 },
    inapplicable_dims: ["company_health", "compensation"],
    detected_location: "New York, NY",
    location_match: "match",
    ...overrides,
  };
}

export function job(overrides: Partial<JobCard> = {}): JobCard {
  return {
    provider: "serper",
    title: "Data Engineer",
    company: "Acme",
    location: "New York, NY",
    remote: false,
    apply_url: "https://jobs.example/acme/1",
    snippet: "Build and run data pipelines.",
    posted_at: "2026-09-20T08:00:00Z",
    salary_min: null,
    salary_max: null,
    salary_currency: null,
    sponsorship_signal: "unknown",
    source_tier: 1,
    location_verified: null,
    link_checked: true,
    score: score(),
    ...overrides,
  };
}

export function response(overrides: Partial<DiscoverResponse> = {}): DiscoverResponse {
  return {
    query: "data engineer",
    scored: [job()],
    more: [],
    dead_removed: 0,
    scoring_strategy: "direct",
    warnings: [],
    ...overrides,
  };
}

export interface TierMix {
  Strong: number;
  Good: number;
  Mixed: number;
  Poor: number;
  /** Rows past the scoring batch (no score). */
  unscored: number;
}

/** Twenty-five rows: six Strong, six Good, three Mixed, three Poor, seven not scored. */
export const DEFAULT_MIX: TierMix = { Strong: 6, Good: 6, Mixed: 3, Poor: 3, unscored: 7 };

const TIER_SCORE: Record<"Strong" | "Good" | "Mixed" | "Poor", { start: number; step: number }> = {
  Strong: { start: 95, step: 2 },
  Good: { start: 79, step: 2 },
  Mixed: { start: 59, step: 2 },
  Poor: { start: 39, step: 3 },
};

function dated(index: number): string | null {
  // Spread over September, with a few rows that carry no date.
  return index % 5 === 4 ? null : `2026-09-${String(1 + ((index * 7) % 27)).padStart(2, "0")}`;
}

/**
 * A response with some of every tier and some rows past the scoring batch, every `apply_url` and title
 * its own. The scored rows come back interleaved across tiers (as a search that scored them in a
 * different order than their fit would), so "Best fit" changes their order and "As returned" does not.
 * Titles are "Strong 1", "Good 3" and "Unscored 2"; within a tier a lower number scores higher.
 */
export function mixedResponse(mix: Partial<TierMix> = {}, overrides: Partial<DiscoverResponse> = {}): DiscoverResponse {
  const counts = { ...DEFAULT_MIX, ...mix };
  const queues = (["Good", "Strong", "Poor", "Mixed"] as const).map((bin) =>
    Array.from({ length: counts[bin] }, (_, index) =>
      job({
        title: `${bin} ${index + 1}`,
        apply_url: `https://jobs.example/mix/${bin.toLowerCase()}-${index + 1}`,
        posted_at: dated(index * 4 + bin.length),
        score: score({ bin, score100: TIER_SCORE[bin].start - TIER_SCORE[bin].step * index }),
      }),
    ),
  );
  const scored: JobCard[] = [];
  for (let round = 0; queues.some((queue) => queue.length > round); round++) {
    for (const queue of queues) if (queue[round] !== undefined) scored.push(queue[round]);
  }
  const more = Array.from({ length: counts.unscored }, (_, index) =>
    job({ title: `Unscored ${index + 1}`, apply_url: `https://jobs.example/mix/unscored-${index + 1}`, posted_at: dated(index), score: null }),
  );
  return response({ scored, more, ...overrides });
}
