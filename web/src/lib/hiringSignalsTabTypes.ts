// Hiring Signals P4 -- wire types and runtime parsers for the standalone
// "Hiring signals" tab.
//
// The tab is the company-less sibling of P3's per-application panel: the user
// types a role (and, optionally, a metro) and sees recent public hiring posts
// for it. The server's reply is the same kind of thing as P3's -- structured,
// already-sanitized signals -- with four differences this file owns:
//   - the counts have a different key set (`role_mismatch_hidden`, posts that
//     did not contain the role words, replaces P3's `off_topic_hidden`, posts
//     that were not about the company) and MUST add up, which is checked here;
//   - a signal has no `role_match` (the role filter HIDES a mismatch instead of
//     tagging it) and gains `aggregator` (an account with many posts in one
//     pull);
//   - the reply echoes the `locale` the query was worded for;
//   - there are saved SEARCHES (a role and a location, nothing else).
//
// EVERYTHING per-field is inherited from P3's parsers instead of re-written.
// `parseTabSignal` runs P3's `parseSignal` (activity-id gate, url length cap,
// text hygiene, unknown species -> "unclassified", null for anything unreadable)
// and then builds the tab's shape FIELD BY FIELD. Listing the fields (rather than
// spreading P3's result) is deliberate: a field added to P3's signal later must
// not slide into this page's state by accident, and a title or snippet that a
// server bug leaked into a signal is dropped on the floor here exactly as it is
// there (a test pins it).
//
// COUNTS ARE ALL-OR-NOTHING, and here also all-or-consistent. One unreadable
// count makes them all unknown (a partial "what was hidden and why" reads as
// complete when it is not), and so does a set that does not add up: the contract
// says raw_hits = shown + rejected + duplicates + role_mismatch_hidden +
// echoes_hidden + job_seekers_hidden + too_old_hidden, and a breakdown that
// contradicts its own total cannot be worded honestly. No sentence is better than
// a wrong one.
//
// Pure module: no React, no network, and it must not import `./api` (that pulls
// in the Supabase client, which throws at import time without env vars) -- the
// same constraint hiringSignalsTypes.ts lives under.

import {
  type Freshness,
  type HiringSignal,
  type SearchProvider,
  cleanText,
  isFreshness,
  isRecord,
  nonNegativeInt,
  parseProvider,
  parseSignal,
} from "./hiringSignalsTypes";

// ── locale ─────────────────────────────────────────────────────────────────

// Which hiring vocabulary the server words the query in. "auto" is a choice in
// the FORM only: it means "derive it from the location" and is never sent (the
// request simply omits `locale`), so the wire type has no "auto".
export const LOCALE_VALUES = ["india", "global"] as const;
export type Locale = (typeof LOCALE_VALUES)[number];
export type LocaleChoice = "auto" | Locale;

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALE_VALUES as readonly string[]).includes(value);
}

// The wording select's value, back to a choice. Anything the select could not
// have produced falls back to Auto (derive from the location), the one choice
// that claims nothing.
export function toLocaleChoice(value: string): LocaleChoice {
  return isLocale(value) ? value : "auto";
}

// ── shapes ─────────────────────────────────────────────────────────────────

export const TAB_COUNT_KEYS = [
  "raw_hits",
  "rejected",
  "duplicates",
  "role_mismatch_hidden",
  "echoes_hidden",
  "job_seekers_hidden",
  "too_old_hidden",
  "shown",
] as const;
export type TabCounts = Record<(typeof TAB_COUNT_KEYS)[number], number>;

// P3's signal without `role_match`, plus `aggregator`. `registry_match` keeps
// P3's three-value type: the contract only ever sends "unmatched" or "possible"
// here (an echo that MATCHES a tracked listing is hidden server-side), but a
// stray "matched" is honest to show with P3's note, and dropping a real value
// would lose information.
export type TabSignal = Omit<HiringSignal, "role_match"> & {
  // null = could not tell (the server has no author handle to count by). Unknown
  // is labeled unknown, never coerced to false.
  aggregator: boolean | null;
};

export interface TabSearchResponse {
  // null when the server names a provider this page does not know -- the UI then
  // says "your search provider" rather than guessing a name.
  provider: SearchProvider | null;
  cached: boolean;
  freshness: Freshness;
  // null when the echo is missing or unrecognized: nothing is claimed.
  locale: Locale | null;
  query_label: string;
  signals: TabSignal[];
  // null when any count was unreadable or they do not add up (see file header).
  counts: TabCounts | null;
}

// A response plus what this page had to leave out of it: entries in `signals`
// that could not be displayed (malformed, or a repeat of an id already shown),
// so the counts sentence can own up to them.
export interface TabSearchOutcome {
  response: TabSearchResponse;
  unreadable: number;
}

export interface SavedHiringSearch {
  id: string;
  query: string;
  location: string | null;
  created_at: string;
}

// The sizes the server enforces (1-200 after trim for the role, at most 100 for
// the location); the parsers cap what they carry at the same numbers.
export const QUERY_MAX_CHARS = 200;
export const LOCATION_MAX_CHARS = 100;
const MAX_QUERY_LABEL_CHARS = 200;
const MAX_ID_CHARS = 64;

// ── parsers ────────────────────────────────────────────────────────────────

export function parseTabSignal(raw: unknown): TabSignal | null {
  const base = parseSignal(raw);
  if (base === null) return null;
  return {
    activity_id: base.activity_id,
    post_url: base.post_url,
    embed_url: base.embed_url,
    author_name: base.author_name,
    posted_at: base.posted_at,
    age_hint: base.age_hint,
    species: base.species,
    comment_count: base.comment_count,
    registry_match: base.registry_match,
    saved: base.saved,
    aggregator: isRecord(raw) && typeof raw.aggregator === "boolean" ? raw.aggregator : null,
  };
}

// True when the breakdown is consistent with its own total (the contract's
// invariant). Exported so a test can pin exactly which sums count.
export function countsAddUp(counts: TabCounts): boolean {
  return (
    counts.raw_hits ===
    counts.shown +
      counts.rejected +
      counts.duplicates +
      counts.role_mismatch_hidden +
      counts.echoes_hidden +
      counts.job_seekers_hidden +
      counts.too_old_hidden
  );
}

export function parseTabCounts(value: unknown): TabCounts | null {
  if (!isRecord(value)) return null;
  const counts: Partial<TabCounts> = {};
  for (const key of TAB_COUNT_KEYS) {
    const parsed = nonNegativeInt(value[key]);
    if (parsed === null) return null;
    counts[key] = parsed;
  }
  const complete = counts as TabCounts;
  return countsAddUp(complete) ? complete : null;
}

// `requested` is the window the user asked for: used only when the server's own
// echo of it is missing or unrecognized, so the label under the results never
// disagrees with what was actually requested. Returns null (a whole-response
// failure the page reports as "could not read the reply") only when the body is
// not an object or `signals` is not a list -- anything less degrades per signal.
export function parseTabSearchResponse(
  raw: unknown,
  requested: Freshness,
): TabSearchOutcome | null {
  if (!isRecord(raw) || !Array.isArray(raw.signals)) return null;

  const signals: TabSignal[] = [];
  const seen = new Set<string>();
  let unreadable = 0;
  for (const item of raw.signals) {
    const signal = parseTabSignal(item);
    // A repeat of an id already kept would also collide as a React key; the
    // server dedupes, so this is a guard, and it is counted rather than hidden.
    if (signal === null || seen.has(signal.activity_id)) {
      unreadable += 1;
      continue;
    }
    seen.add(signal.activity_id);
    signals.push(signal);
  }

  return {
    response: {
      provider: parseProvider(raw.provider),
      cached: raw.cached === true,
      freshness: isFreshness(raw.freshness) ? raw.freshness : requested,
      locale: isLocale(raw.locale) ? raw.locale : null,
      query_label: cleanText(raw.query_label, MAX_QUERY_LABEL_CHARS) ?? "",
      signals,
      counts: parseTabCounts(raw.counts),
    },
    unreadable,
  };
}

// One saved-search row, or null when it has no usable id or role (there is
// nothing to show, re-run or delete by).
export function parseSavedHiringSearch(raw: unknown): SavedHiringSearch | null {
  if (!isRecord(raw)) return null;
  const id = typeof raw.id === "string" ? raw.id.trim() : "";
  if (id === "" || id.length > MAX_ID_CHARS) return null;
  const query = cleanText(raw.query, QUERY_MAX_CHARS);
  if (query === null) return null;
  return {
    id,
    query,
    location: cleanText(raw.location, LOCATION_MAX_CHARS),
    // Kept verbatim even when unparsable, like a saved post's: the row still
    // exists and must stay removable.
    created_at: typeof raw.created_at === "string" ? raw.created_at : "",
  };
}

// null = the body was not `{ searches: [...] }`. Rows that fail to parse are left
// out; repeats of one id are collapsed so a list key is never duplicated.
export function parseSavedSearchesResponse(raw: unknown): SavedHiringSearch[] | null {
  if (!isRecord(raw) || !Array.isArray(raw.searches)) return null;
  const searches: SavedHiringSearch[] = [];
  const seen = new Set<string>();
  for (const item of raw.searches) {
    const search = parseSavedHiringSearch(item);
    if (search !== null && !seen.has(search.id)) {
      seen.add(search.id);
      searches.push(search);
    }
  }
  return searches;
}
