// Job Finder P8 -- pure, testable logic for the Discover page (labels, salary formatting, how a
// warning reads), split out the same way honestFloor.ts and applicationsBoard.ts split their own
// pure logic from JSX. What each result row shows -- its fit block, trail and receipt -- is
// lib/discoverRow.ts; what the band is told is lib/discoverFacts.ts.

import type { JobCard, SavedSearch } from "./discoverTypes";
import { providerDisplayName } from "./providerNames";

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

// ── providers and warnings ─────────────────────────────────────────────────

// How the providers a search may name read in a sentence (names: lib/providerNames.ts). The job
// registry is not a search provider and so has a phrase of its own. An id nobody has heard of is shown
// as the server sent it: nothing is guessed about it.
export function providerName(id: string): string {
  return id === "registry" ? "The job registry" : providerDisplayName(id);
}

export interface WarningLine {
  /** The id in front of the message ("brave"), or null when the warning names no provider. */
  provider: string | null;
  /** What came after it, or the whole warning. */
  message: string;
}

// A live-search provider that ran and failed leaves "{provider}: {message}"; the registry notes are
// plain sentences. The prefix is read only when it has the shape of a provider id (lower case, no
// spaces), so a sentence that happens to hold a colon is never cut in two.
const PROVIDER_PREFIX = /^([a-z][a-z0-9_]{1,30}): (\S[\s\S]*)$/;

export function parseWarning(text: string): WarningLine {
  const trimmed = text.trim();
  const match = PROVIDER_PREFIX.exec(trimmed);
  return match === null ? { provider: null, message: trimmed } : { provider: match[1], message: match[2].trim() };
}
