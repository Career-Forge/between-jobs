// The words and small rules the workspace's research sections share (Company intel, Contacts, Events).
// Pure: no React, no network, no clock (a caller that needs "this year" passes `now`).
//
// A run of any of the three comes back as `{ run, ...items }`. `run` is null when nothing has been run
// yet; otherwise it names the providers that searched, one warning per search that failed or was
// skipped, and when it was made. Everything a section says about a run is read from those fields.

import { providerDisplayName } from "./providerNames";
import { formatTrailDate } from "./trail";

export interface ResearchRun {
  id: string;
  company_name: string;
  providers_used: string[];
  warnings: string[];
  created_at: string;
}

// The name of a provider a run can report (lib/providerNames.ts); an id it does not know is shown as
// the server sent it rather than guessed at.
export const providerLabel = providerDisplayName;

// "You.com", "You.com and Firecrawl", "You.com, Firecrawl and GitHub"; null when none came back.
export function providersPhrase(ids: readonly string[]): string | null {
  const names = ids.filter((id) => typeof id === "string" && id.trim() !== "").map(providerLabel);
  if (names.length === 0) return null;
  if (names.length === 1) return names[0];
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

// How many of the run's searches failed or were skipped: one warning is one search.
export function failedSearchCount(run: Pick<ResearchRun, "warnings">): number {
  return run.warnings.filter((warning) => typeof warning === "string" && warning.trim() !== "").length;
}

// "1 of this run's searches failed or was skipped:" -- the lead of the list of warnings.
export function searchesLead(count: number): string {
  return count === 1 ? "1 of this run's searches failed or was skipped:" : `${count} of this run's searches failed or were skipped:`;
}

// The same count for a trail's extra slot: "2 searches failed or skipped".
export function searchesFailedWords(count: number): string {
  return count === 1 ? "1 search failed or skipped" : `${count} searches failed or skipped`;
}

// "Sep 20" for the day the run was made, from its own `created_at`; null when it cannot be read.
export function runDate(run: Pick<ResearchRun, "created_at"> | null, now: Date, timeZone?: string): string | null {
  return run === null ? null : formatTrailDate(run.created_at, { now, timeZone });
}

// A count in words: "1 claim", "7 claims".
export function countWords(count: number, one: string, many: string): string {
  return count === 1 ? `1 ${one}` : `${count} ${many}`;
}

// ── confidence ────────────────────────────────────────────────────────────

// A mark drawn as cells: `cells` filled out of `of`, with the word the server's value is read as. The
// value is a model's reading, never a checked fact, so it is gold (evidence) and never green.
export interface ConfidenceReading {
  cells: number;
  of: number;
  word: string;
}

// Company intel: the model's own "high", "medium" or "low" for a claim. 3, 2 and 1 cells of 3.
const INTEL_CONFIDENCE: Readonly<Record<string, ConfidenceReading>> = {
  high: { cells: 3, of: 3, word: "High" },
  medium: { cells: 2, of: 3, word: "Medium" },
  low: { cells: 1, of: 3, word: "Low" },
};

export function intelConfidence(value: string): ConfidenceReading | null {
  return Object.prototype.hasOwnProperty.call(INTEL_CONFIDENCE, value) ? INTEL_CONFIDENCE[value] : null;
}

// Contacts: the model's reading of how clearly a source names this person in this role. The server's
// own word "verified" stands for "the source clearly names this person in this role" -- a model's
// reading that code backed only by checking the name appears in the source, so it is not repeated
// here as a verification. 4 to 1 cells of 4.
const CONTACT_CONFIDENCE: Readonly<Record<string, ConfidenceReading>> = {
  verified: { cells: 4, of: 4, word: "Clear match" },
  strong: { cells: 3, of: 4, word: "Likely match" },
  inferred: { cells: 2, of: 4, word: "Indirect" },
  unsupported: { cells: 1, of: 4, word: "Weak" },
};

export function contactConfidence(value: string): ConfidenceReading | null {
  return Object.prototype.hasOwnProperty.call(CONTACT_CONFIDENCE, value) ? CONTACT_CONFIDENCE[value] : null;
}

// ── a link out of search results ──────────────────────────────────────────

// A source address is a stranger's text: a search result, or a model's copy of one. Only an http or
// https address is a link; anything else (a javascript: address, a data: address, text that is not an
// address at all) is shown as plain words and never as an href.
export function safeSourceUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const text = value.trim();
  if (text === "" || text.length > 2048) return null;
  try {
    const parsed = new URL(text);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
    return text;
  } catch {
    return null;
  }
}
