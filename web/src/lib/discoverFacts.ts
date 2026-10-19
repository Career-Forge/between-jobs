// What Discover tells the next-step band, and the request it makes, as pure functions: the filters a
// search ran with, how many were set, and the counts a finished search holds. Nothing here reads the
// network or the clock. The rows that use these facts are lib/nextStep/pages/discover.ts.

import { parseWarning } from "./discover";
import type { DiscoverResponse, JobCard } from "./discoverTypes";
import type { DiscoverResultsFact } from "./nextStep/pages/discover";

export interface DiscoverRequest {
  q: string;
  location: string;
  companies: string;
  remoteOnly: boolean;
}

export const EMPTY_REQUEST: DiscoverRequest = { q: "", location: "", companies: "", remoteOnly: false };

// The query string of GET /discover: only the filters that hold something, as typed.
export function discoverQuery(request: DiscoverRequest): string {
  const qs = new URLSearchParams();
  if (request.q) qs.set("q", request.q);
  if (request.location) qs.set("location", request.location);
  if (request.companies) qs.set("companies", request.companies);
  if (request.remoteOnly) qs.set("remote_only", "true");
  return qs.toString();
}

function held(text: string): boolean {
  return text.trim() !== "";
}

// How many of the four filters held something for this search: the search words, the location, the
// companies and "remote only".
export function filtersSet(request: DiscoverRequest): number {
  return [held(request.q), held(request.location), held(request.companies), request.remoteOnly].filter(Boolean).length;
}

// The rows of a response, defensively: an answer that is not a list is no rows.
export function rowsOf(list: unknown): JobCard[] {
  return Array.isArray(list) ? (list as JobCard[]) : [];
}

export function warningsOf(result: DiscoverResponse): string[] {
  return Array.isArray(result.warnings) ? result.warnings.filter((warning): warning is string => typeof warning === "string" && warning.trim() !== "") : [];
}

// Rows per provider, most first (ties in the order the providers first appear), over every row the
// response returned, scored or not. A provider id is capped so one strange value cannot make the
// band's line unreadable.
function providerCounts(rows: readonly JobCard[]): { provider: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const row of rows) {
    const provider = typeof row.provider === "string" && row.provider !== "" ? row.provider.slice(0, 40) : null;
    if (provider !== null) counts.set(provider, (counts.get(provider) ?? 0) + 1);
  }
  return [...counts.entries()].map(([provider, count]) => ({ provider, count })).sort((a, b) => b.count - a.count);
}

function nonNegative(value: unknown): number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : 0;
}

export function resultsFactOf(result: DiscoverResponse, request: DiscoverRequest): DiscoverResultsFact {
  const scored = rowsOf(result.scored);
  const more = rowsOf(result.more);
  const failed: string[] = [];
  let otherNotes = 0;
  for (const warning of warningsOf(result)) {
    const line = parseWarning(warning);
    if (line.provider === null) otherNotes += 1;
    else if (!failed.includes(line.provider)) failed.push(line.provider);
  }
  return {
    scored: scored.length,
    more: more.length,
    deadRemoved: nonNegative(result.dead_removed),
    providers: providerCounts([...scored, ...more]),
    failedProviders: failed,
    otherNotes,
    fallback: result.scoring_strategy === "neutral_fallback",
    filtersSet: filtersSet(request),
    companiesSet: held(request.companies),
  };
}

// The body of POST /saved-searches for the filters on the page: the search words as typed, a blank
// location as none, the companies split on commas. (The same shape Discover has always sent.)
export function savedSearchBody(request: DiscoverRequest): {
  query: string;
  location: string | null;
  companies: string[];
  remote_only: boolean;
} {
  return {
    query: request.q,
    location: request.location || null,
    companies: request.companies
      ? request.companies
          .split(",")
          .map((company) => company.trim())
          .filter(Boolean)
      : [],
    remote_only: request.remoteOnly,
  };
}

// What a screen reader is told once a search has answered, from the same counts the band reads.
export function resultsAnnouncement(fact: DiscoverResultsFact): string {
  if (fact.scored + fact.more === 0) return "The search finished with no results.";
  const parts = [`${fact.scored} scored`, ...(fact.more > 0 ? [`${fact.more} more, unscored`] : []), ...(fact.deadRemoved > 0 ? [`${fact.deadRemoved} dropped as no longer live`] : [])];
  return `The search finished: ${parts.join(", ")}.`;
}
