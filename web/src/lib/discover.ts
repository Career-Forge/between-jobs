// Job Finder P8 -- pure, testable logic for the Discover page (badge/label
// mapping, salary/date formatting), split out the same way honestFloor.ts
// and applicationsBoard.ts already split their own pure logic from JSX.

import type { JobCard, ScoreBin, SavedSearch } from "./discoverTypes";

const BIN_BADGE_CLASS: Record<ScoreBin, string> = {
  Strong: "bj-badge-emerald",
  Good: "bj-badge-cyan",
  Mixed: "bj-badge-gold",
  Poor: "bj-badge-danger",
};

export function binBadgeClass(bin: ScoreBin): string {
  return BIN_BADGE_CLASS[bin];
}

const SUB_SCORE_LABELS: Record<string, string> = {
  skills: "Skills",
  experience: "Experience",
  workauth: "Work Authorization",
  location: "Location",
  company_health: "Company Health",
  compensation: "Compensation",
};

export function subScoreLabel(key: string): string {
  return SUB_SCORE_LABELS[key] ?? key;
}

export function formatPostedDate(postedAt: string | null): string | null {
  if (!postedAt) return null;
  const date = new Date(postedAt);
  if (Number.isNaN(date.getTime())) return null;
  return `Posted ${date.toLocaleDateString()}`;
}

export function formatSalary(
  min: number | null,
  max: number | null,
  currency: string | null,
): string | null {
  if (min == null && max == null) return null;
  const c = currency ?? "";
  const fmt = (n: number) => n.toLocaleString();
  if (min != null && max != null && min !== max) return `${c} ${fmt(min)}-${fmt(max)}`.trim();
  const single = min ?? max;
  return single == null ? null : `${c} ${fmt(single)}`.trim();
}

// Real, deliberate v1 limitation, matching the backend route's own
// disclosed choice: a live-search-lane result (anything but the
// registry lane) has no full JD text anywhere -- tracking it sends the
// truncated snippet as-is, not a guess at a fuller description.
export function trackRequestBody(job: JobCard) {
  return {
    apply_url: job.apply_url,
    title: job.title,
    company: job.company,
    location: job.location,
    snippet: job.snippet,
    provider: job.provider,
  };
}

export function locationLabel(job: JobCard): string {
  const parts: string[] = [];
  if (job.location) parts.push(job.location);
  if (job.remote) parts.push("Remote");
  return parts.length > 0 ? parts.join(" -- ") : "Location unknown";
}

// Job Finder P9a -- a real display label derived from a saved search's own
// filters (no separate stored "name" field -- one less thing for the user
// to fill in, and the filters themselves already say what it's for).
export function savedSearchLabel(search: SavedSearch): string {
  const parts: string[] = [];
  if (search.query) parts.push(`"${search.query}"`);
  if (search.companies.length > 0) parts.push(`at ${search.companies.join(", ")}`);
  if (search.location) parts.push(`in ${search.location}`);
  if (search.remote_only) parts.push("remote only");
  return parts.length > 0 ? parts.join(" ") : "Any job";
}
