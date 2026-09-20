// Hiring Signals P3 -- wire types and runtime parsers for the per-application
// "Hiring posts" panel.
//
// The panel shows individual hiring-intent posts that a SEARCH INDEX (the
// provider the user connected with their own key) returned for a company.
// The server hands back structured, already-sanitized signals -- url, a few
// computed tags, counts -- and this file is the browser's half of that
// contract: the shapes, plus parsers that turn an untyped JSON body into
// those shapes without ever throwing.
//
// WHY PARSE INSTEAD OF CAST. `apiFetch<T>` only asserts a type; it does not
// check one. Everything in these payloads is derived from strangers' posts
// via a third-party index, and the panel renders it next to an iframe, so a
// surprise field must degrade the card, never crash the panel:
//   - a signal without a digit-only `activity_id` is dropped (it has no
//     embed key and cannot be saved), and COUNTED, so the panel can say so
//     instead of silently showing fewer posts than the server reported;
//   - an unknown `species` becomes "unclassified" ("no template matched" --
//     the honest neutral tag), never a guess;
//   - a field that is missing or the wrong type becomes null, which the UI
//     labels as unknown rather than filling in something that looks like data;
//   - counts are all-or-nothing: one unreadable count makes them ALL unknown,
//     because a partial breakdown of "what was hidden and why" would read as
//     complete when it is not.
//
// WHAT IS DELIBERATELY NOT HERE. The parsed models carry exactly the
// contract's fields and nothing else. If a server bug ever leaked a title or
// snippet into a signal, the parser would drop it on the floor rather than
// carry post text into component state (a test pins this). Post/embed urls
// are kept as plain strings and re-validated at render time
// (`validateEmbedUrl`, `safePostUrl` in hiringSignals.ts) -- the parser is
// not the last line of defence for what a browser may load.
//
// Pure module: no React, no network, and it must not import `./api` (that
// pulls in the Supabase client, which throws at import time without env vars,
// and would make this untestable) -- same constraint honestFloor.ts and
// discover.ts already live under.

export const FRESHNESS_VALUES = ["day", "3days", "week"] as const;
export type Freshness = (typeof FRESHNESS_VALUES)[number];
export const DEFAULT_FRESHNESS: Freshness = "week";

export const SEARCH_PROVIDERS = ["you_com", "brave", "serper", "firecrawl"] as const;
export type SearchProvider = (typeof SEARCH_PROVIDERS)[number];

export const SPECIES_VALUES = [
  "unclassified",
  "ats_echo",
  "referral_offer",
  "hiring_drive",
] as const;
export type Species = (typeof SPECIES_VALUES)[number];

export const REGISTRY_MATCH_VALUES = ["matched", "possible", "unmatched"] as const;
export type RegistryMatch = (typeof REGISTRY_MATCH_VALUES)[number];

export const COUNT_KEYS = [
  "raw_hits",
  "rejected",
  "duplicates",
  "off_topic_hidden",
  "echoes_hidden",
  "job_seekers_hidden",
  "too_old_hidden",
  "shown",
] as const;
export type SignalCounts = Record<(typeof COUNT_KEYS)[number], number>;

export interface HiringSignal {
  activity_id: string;
  post_url: string;
  embed_url: string;
  author_name: string | null;
  posted_at: string | null;
  age_hint: string | null;
  species: Species;
  comment_count: number | null;
  // null = could not tell. Unknown is labeled unknown, never coerced to false.
  role_match: boolean | null;
  registry_match: RegistryMatch | null;
  saved: boolean;
}

export interface SearchResponse {
  // null when the server names a provider this page does not know -- the UI
  // then says "your search provider" rather than guessing a name.
  provider: SearchProvider | null;
  cached: boolean;
  freshness: Freshness;
  query_label: string;
  signals: HiringSignal[];
  // null when any count was unreadable (see file header).
  counts: SignalCounts | null;
}

// A search response plus what this page had to leave out of it. `unreadable`
// counts entries in `signals` that could not be displayed (malformed, or a
// repeat of an activity id already shown) so the counts sentence can own up
// to them.
export interface SearchOutcome {
  response: SearchResponse;
  unreadable: number;
}

export interface SavedPost {
  id: string;
  activity_id: string;
  post_url: string;
  embed_url: string;
  created_at: string;
}

// LinkedIn activity ids are 19 digits today; the server accepts 1-25 ASCII
// digits and never a leading zero (`0007...` is the same number as `7...`, so
// it would be one post saved twice), and this mirrors it. `[0-9]`, never
// `\d` -- in a JS regex `\d` is ASCII-only, but the explicit class keeps the
// intent obvious and identical to the Python side.
export const ACTIVITY_ID_RX = /^[1-9][0-9]{0,24}$/;

const MAX_URL_CHARS = 2048;
const MAX_AUTHOR_CHARS = 100;
const MAX_AGE_HINT_CHARS = 40;
const MAX_QUERY_LABEL_CHARS = 200;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// Control and format characters. Third-party text can carry U+202E (right-to-
// left override) to make a name or a label read backwards, and zero-width
// characters (U+200B-200D, U+2060, U+FEFF) to make a name that is blank or
// that looks like another one; React escapes markup but none of that. The
// Unicode "format" category (Cf) is every one of them -- the bidi embedding,
// override and isolate controls, the Arabic letter mark, the zero-width
// characters -- so the class is that category plus the C0/C1 controls and the
// line and paragraph separators. Real right-to-left scripts stay intact:
// Arabic and Hebrew LETTERS are directional on their own and are not Cf. The
// class is written as escapes on purpose: a source file must never carry
// literal bidi or NUL characters itself (git would treat it as binary, and an
// invisible override in a public repo is the kind of thing reviewers should
// never have to squint for).
const STRIP_RX = /[\u0000-\u001f\u007f-\u009f\u2028\u2029\p{Cf}]/gu;

// Display-text hygiene for anything that came from a stranger: collapse
// whitespace, strip the controls above, cap the length by code point (so a
// cut never leaves half of a surrogate pair), and return null for "nothing
// left". Non-strings are null, not "".
export function cleanText(value: unknown, maxChars: number): string | null {
  if (typeof value !== "string") return null;
  const cleaned = value.replace(/\s+/g, " ").replace(STRIP_RX, "").trim();
  if (cleaned === "") return null;
  const points = Array.from(cleaned);
  return points.length > maxChars ? points.slice(0, maxChars).join("").trim() : cleaned;
}

function nonNegativeInt(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

// `date.isoformat()` from the server: date, `T`, time with optional seconds
// and fraction, and a `Z` or numeric offset. `Date.parse` alone accepts far
// more (`"1"` is the year 2001, `"foo 12"` is a date in V8), and a timestamp
// this page cannot trust is shown as unknown instead.
const ISO_8601_RX =
  /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?(?:Z|[+-][0-9]{2}:[0-9]{2})$/;

export function isIsoTimestamp(value: unknown): value is string {
  return typeof value === "string" && ISO_8601_RX.test(value) && !Number.isNaN(Date.parse(value));
}

function isoStringOrNull(value: unknown): string | null {
  return isIsoTimestamp(value) ? value : null;
}

function urlStringOrEmpty(value: unknown): string {
  return typeof value === "string" && value.length <= MAX_URL_CHARS ? value : "";
}

export function isFreshness(value: unknown): value is Freshness {
  return typeof value === "string" && (FRESHNESS_VALUES as readonly string[]).includes(value);
}

function parseProvider(value: unknown): SearchProvider | null {
  return typeof value === "string" && (SEARCH_PROVIDERS as readonly string[]).includes(value)
    ? (value as SearchProvider)
    : null;
}

// An unknown species is "unclassified": the tag that means "no template
// matched", i.e. NOT a claim that the post is a hiring call.
export function parseSpecies(value: unknown): Species {
  return typeof value === "string" && (SPECIES_VALUES as readonly string[]).includes(value)
    ? (value as Species)
    : "unclassified";
}

function parseRegistryMatch(value: unknown): RegistryMatch | null {
  return typeof value === "string" && (REGISTRY_MATCH_VALUES as readonly string[]).includes(value)
    ? (value as RegistryMatch)
    : null;
}

function parseCounts(value: unknown): SignalCounts | null {
  if (!isRecord(value)) return null;
  const counts: Partial<SignalCounts> = {};
  for (const key of COUNT_KEYS) {
    const parsed = nonNegativeInt(value[key]);
    if (parsed === null) return null;
    counts[key] = parsed;
  }
  return counts as SignalCounts;
}

export function parseSignal(raw: unknown): HiringSignal | null {
  if (!isRecord(raw)) return null;
  const activityId = raw.activity_id;
  if (typeof activityId !== "string" || !ACTIVITY_ID_RX.test(activityId)) return null;
  return {
    activity_id: activityId,
    post_url: urlStringOrEmpty(raw.post_url),
    embed_url: urlStringOrEmpty(raw.embed_url),
    author_name: cleanText(raw.author_name, MAX_AUTHOR_CHARS),
    posted_at: isoStringOrNull(raw.posted_at),
    age_hint: cleanText(raw.age_hint, MAX_AGE_HINT_CHARS),
    species: parseSpecies(raw.species),
    comment_count: nonNegativeInt(raw.comment_count),
    role_match: typeof raw.role_match === "boolean" ? raw.role_match : null,
    registry_match: parseRegistryMatch(raw.registry_match),
    saved: raw.saved === true,
  };
}

// `requested` is the window the user asked for: used only when the server's
// own echo of it is missing or unrecognized, so the label under the results
// never disagrees with what was actually requested. Returns null (a whole-
// response failure the panel reports as "could not read the reply") only when
// the body is not an object or `signals` is not a list -- anything less
// degrades per signal.
export function parseSearchResponse(raw: unknown, requested: Freshness): SearchOutcome | null {
  if (!isRecord(raw) || !Array.isArray(raw.signals)) return null;

  const signals: HiringSignal[] = [];
  const seen = new Set<string>();
  let unreadable = 0;
  for (const item of raw.signals) {
    const signal = parseSignal(item);
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
      query_label: cleanText(raw.query_label, MAX_QUERY_LABEL_CHARS) ?? "",
      signals,
      counts: parseCounts(raw.counts),
    },
    unreadable,
  };
}

export function parseSavedPost(raw: unknown): SavedPost | null {
  if (!isRecord(raw)) return null;
  const id = typeof raw.id === "string" ? raw.id.trim() : "";
  if (id === "" || id.length > 64) return null;
  const activityId = raw.activity_id;
  if (typeof activityId !== "string" || !ACTIVITY_ID_RX.test(activityId)) return null;
  return {
    id,
    activity_id: activityId,
    post_url: urlStringOrEmpty(raw.post_url),
    embed_url: urlStringOrEmpty(raw.embed_url),
    // Kept verbatim even when unparsable: dropping the row would hide a real
    // saved post the user could then never remove. The label helper turns an
    // unreadable date into a plain "Saved".
    created_at: typeof raw.created_at === "string" ? raw.created_at : "",
  };
}

// null = the body was not `{ saves: [...] }`. Rows that fail to parse are
// left out (they carry no usable id to show or remove); repeats of one id are
// collapsed so a list key is never duplicated.
export function parseSavesResponse(raw: unknown): SavedPost[] | null {
  if (!isRecord(raw) || !Array.isArray(raw.saves)) return null;
  const saves: SavedPost[] = [];
  const seen = new Set<string>();
  for (const item of raw.saves) {
    const save = parseSavedPost(item);
    if (save !== null && !seen.has(save.id)) {
      seen.add(save.id);
      saves.push(save);
    }
  }
  return saves;
}
