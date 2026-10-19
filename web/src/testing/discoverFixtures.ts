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
