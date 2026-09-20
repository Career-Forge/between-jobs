// Hiring Signals P3 -- pure, testable logic for the per-application "Hiring
// posts" panel, split out from the render components the same way
// honestFloor.ts, discover.ts and applicationsBoard.ts split theirs.
//
// Two groups of functions live here, and the first is the one that matters.
//
// 1. WHAT A BROWSER MAY LOAD OR LINK TO. The feature renders LinkedIn's own
//    public embed iframe in the user's browser, so the address that goes into
//    an iframe `src` (or an anchor `href`) decides whether a stranger's
//    payload can point the browser somewhere it should not. The server builds
//    those addresses, and the server is trusted -- but never blindly:
//    `validateEmbedUrl` accepts exactly one shape and nothing else, and
//    `safePostUrl` only lets an https url on a linkedin.com host through. If
//    either says no, the caller renders no iframe / no link. The server never
//    fetches these urls; only the user's browser does, and only after the user
//    clicks "Show post".
//
// 2. HONEST LABELS. Everything user-visible that is derived from a computed
//    field is worded so that "unknown" stays visibly unknown: a post whose
//    time cannot be told says "time unknown" instead of guessing; a species
//    tag that means "no template matched" is labeled "Unclassified", never as
//    a confirmed hiring call; and the counts sentence spells out what was
//    hidden and why, so a short list is never a silent one.
//
// Nothing here touches the network, the DOM or React, and (like the types
// module) it must not import `./api`.

import {
  ACTIVITY_ID_RX,
  type Freshness,
  type HiringSignal,
  type RegistryMatch,
  type SavedPost,
  type SearchProvider,
  type SignalCounts,
} from "./hiringSignalsTypes";

// ── embed and post urls ────────────────────────────────────────────────────

export const EMBED_URL_PREFIX = "https://www.linkedin.com/embed/feed/update/urn:li:activity:";

// Whole-string match on one exact shape: https, the www host, the embed path,
// then 1-25 ASCII digits and NOTHING after them -- no query, fragment, slash,
// whitespace or newline. Every alternative that a looser check tends to let
// through (a userinfo trick like `https://www.linkedin.com@evil.example/`, a
// lookalike or sub-domain host, http, another urn kind, Unicode digits) fails
// simply because the pattern is literal and anchored at both ends.
const EMBED_URL_RX =
  /^https:\/\/www\.linkedin\.com\/embed\/feed\/update\/urn:li:activity:([0-9]{1,25})$/;

// Returns the url only if it is EXACTLY the official embed address for a
// digit-only activity id, else null (and the caller must then render no
// iframe at all). `expectedActivityId`, when given, must match the id inside
// the url: a signal whose embed points at a different post than its own id is
// inconsistent, and failing closed costs nothing.
export function validateEmbedUrl(url: unknown, expectedActivityId?: string): string | null {
  if (typeof url !== "string") return null;
  const match = EMBED_URL_RX.exec(url);
  if (match === null) return null;
  if (expectedActivityId !== undefined) {
    if (!ACTIVITY_ID_RX.test(expectedActivityId) || match[1] !== expectedActivityId) return null;
  }
  return url;
}

// For the "Open post" anchor. Accepts only an https url whose host is
// linkedin.com or one of its subdomains (the index returns country hosts like
// es.linkedin.com), with no credentials and no non-default port. The query and
// fragment are dropped from what is returned -- the server already sends
// none, but a tracking token in a query string can identify a member, and
// there is no reason to hand it to a link the user may click. Anything else,
// including a lookalike such as `linkedin.com.example` or `notlinkedin.com`,
// returns null and the caller omits the link.
export function safePostUrl(url: unknown): string | null {
  if (typeof url !== "string" || url === "") return null;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:") return null;
  if (parsed.username !== "" || parsed.password !== "" || parsed.port !== "") return null;
  const host = parsed.hostname;
  if (host !== "linkedin.com" && !host.endsWith(".linkedin.com")) return null;
  parsed.search = "";
  parsed.hash = "";
  return parsed.toString();
}

// ── species, registry match, provider, freshness labels ────────────────────

export interface SpeciesInfo {
  label: string;
  // An existing bj-badge-* class (see tokens.css's semantic contract): gold =
  // evidence -- a template matched -- and muted = nothing matched.
  badgeClass: string;
  // Plain-language meaning, for the badge's tooltip. Wording is deliberately
  // hedged ("appears to"): every one of these is a pattern match over a short
  // snippet, not a verified fact about the post.
  description: string;
}

const SPECIES_INFO: Record<string, SpeciesInfo> = {
  unclassified: {
    label: "Unclassified",
    badgeClass: "bj-badge-muted",
    description:
      "No known post pattern matched, so this is not confirmed as a hiring call. Read it before assuming it is one.",
  },
  ats_echo: {
    label: "Job listing share",
    badgeClass: "bj-badge-gold",
    description:
      "An automatic share of a job listing, not a personal hiring call. The listing may already be somewhere you can see it.",
  },
  referral_offer: {
    label: "Referral offer",
    badgeClass: "bj-badge-gold",
    description: "The author appears to be offering referrals.",
  },
  hiring_drive: {
    label: "Hiring drive",
    badgeClass: "bj-badge-gold",
    description: "The post appears to announce a hiring or walk-in drive.",
  },
};

// Takes a plain string, not the Species union: the parser already folds an
// unknown species into "unclassified", but a label lookup that can never throw
// is the belt to that pair of braces (an unknown key falls back to the same
// neutral entry).
export function speciesInfo(species: string): SpeciesInfo {
  return SPECIES_INFO[species] ?? SPECIES_INFO.unclassified;
}

const REGISTRY_MATCH_NOTES: Record<RegistryMatch, string> = {
  matched: "Matches a job listing we already track.",
  possible: "May match a job listing we already track.",
  // "unmatched" is only sent when we found this company in our listings and
  // none of them is this one; when we cannot tie the page to a company at all
  // the server sends nothing (null), and this page says nothing either.
  unmatched: "We track this company, but not this listing yet.",
};

// null (not applicable / not determined) renders nothing -- there is no
// "unknown" note, because for most species the question is simply not asked.
export function registryMatchNote(match: RegistryMatch | null): string | null {
  return match === null ? null : REGISTRY_MATCH_NOTES[match];
}

const PROVIDER_LABELS: Record<SearchProvider, string> = {
  you_com: "You.com",
  brave: "Brave Search",
  serper: "Serper",
  firecrawl: "Firecrawl",
};

export function providerLabel(provider: SearchProvider | null): string {
  return provider === null ? "your search provider" : PROVIDER_LABELS[provider];
}

export const FRESHNESS_OPTIONS: readonly { value: Freshness; label: string }[] = [
  { value: "day", label: "24 hours" },
  { value: "3days", label: "3 days" },
  { value: "week", label: "7 days" },
];

const FRESHNESS_LABELS: Record<Freshness, string> = {
  day: "last 24 hours",
  "3days": "last 3 days",
  week: "last 7 days",
};

export function freshnessLabel(freshness: Freshness): string {
  return FRESHNESS_LABELS[freshness];
}

// The next wider window, or null when already at the widest -- the empty
// state only suggests widening when there is somewhere to widen to.
export function widerFreshness(freshness: Freshness): Freshness | null {
  switch (freshness) {
    case "day":
      return "3days";
    case "3days":
      return "week";
    case "week":
      return null;
  }
}

// ── time labels ────────────────────────────────────────────────────────────

const MINUTE_MS = 60_000;
const HOUR_MS = 3_600_000;
const DAY_MS = 86_400_000;
// A clock a few minutes off is normal; a timestamp further ahead than this is
// not believable as "when something happened", so it is treated as unknown.
const CLOCK_SKEW_TOLERANCE_MS = 5 * MINUTE_MS;

function unitAgo(amount: number, unit: string): string {
  return `${amount} ${unit}${amount === 1 ? "" : "s"} ago`;
}

// "just now", "5 minutes ago", "3 days ago", "2 weeks ago"... Deliberately
// relative and locale-free: the same inputs give the same words on every
// machine (and in tests). Null when `then` is invalid or implausibly in the
// future, so a caller falls back to "unknown" instead of printing nonsense.
export function relativeAgo(then: Date, now: Date): string | null {
  const diff = now.getTime() - then.getTime();
  if (Number.isNaN(diff) || diff < -CLOCK_SKEW_TOLERANCE_MS) return null;
  if (diff < MINUTE_MS) return "just now";
  if (diff < HOUR_MS) return unitAgo(Math.floor(diff / MINUTE_MS), "minute");
  if (diff < DAY_MS) return unitAgo(Math.floor(diff / HOUR_MS), "hour");
  const days = Math.floor(diff / DAY_MS);
  if (days < 14) return unitAgo(days, "day");
  if (days < 60) return unitAgo(Math.floor(days / 7), "week");
  if (days < 365) return unitAgo(Math.floor(days / 30), "month");
  return unitAgo(Math.floor(days / 365), "year");
}

// The compact "posted" caption on a card. `postedAt` (decoded server-side from
// the post's own id) wins. The index's own relative stamp is the fallback and
// says so: it is relative to when the index last answered, so it can lag, and
// the reader should know it is the index's estimate rather than a timestamp.
// With neither, "Posted time unknown" -- never a guess.
export function postedLabel(postedAt: string | null, ageHint: string | null, now: Date): string {
  if (postedAt !== null) {
    const ago = relativeAgo(new Date(postedAt), now);
    if (ago !== null) return `Posted ${ago}`;
  }
  const hint = typeof ageHint === "string" ? ageHint.trim() : "";
  if (hint !== "") return `Posted ${hint} (per search index)`;
  return "Posted time unknown";
}

// "Saved 2 days ago", or a plain "Saved" when the stored date cannot be read.
export function savedLabel(createdAt: string, now: Date): string {
  const created = new Date(createdAt);
  if (createdAt === "" || Number.isNaN(created.getTime())) return "Saved";
  const ago = relativeAgo(created, now);
  return ago === null ? "Saved" : `Saved ${ago}`;
}

// LinkedIn activity ids are snowflake-style: the id shifted right by 22 bits
// is the creation time in milliseconds since the Unix epoch. This is a port of
// the server's `posted_at_from_activity_id` (hiring_signals.py), used for one
// reason: a saved post is stored as a pointer (id and urls only), so the list
// endpoint carries no `posted_at`, yet the time IS knowable from the id, so
// "Posted time unknown" on a saved card would be wrong. Same plausibility
// band (2003 to 2100) so a garbage id is unknown rather than a nonsense date;
// clock-free, exactly like the original.
const ACTIVITY_ID_TIME_SHIFT = 22n;
const MIN_PLAUSIBLE_POST_MS = 1_041_379_200_000; // 2003-01-01T00:00:00Z
const MAX_PLAUSIBLE_POST_MS = 4_102_444_800_000; // 2100-01-01T00:00:00Z

export function postedAtFromActivityId(activityId: string): Date | null {
  if (typeof activityId !== "string" || !ACTIVITY_ID_RX.test(activityId)) return null;
  const millis = Number(BigInt(activityId) >> ACTIVITY_ID_TIME_SHIFT);
  if (millis < MIN_PLAUSIBLE_POST_MS || millis > MAX_PLAUSIBLE_POST_MS) return null;
  return new Date(millis);
}

// ── small labels ───────────────────────────────────────────────────────────

// null (no count) renders nothing; a count is never invented.
export function commentCountLabel(count: number | null): string | null {
  if (count === null) return null;
  return `${count.toLocaleString("en-US")} ${count === 1 ? "comment" : "comments"}`;
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

function joinList(items: readonly string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

// The headline above the cards. It counts POSTS, not hiring posts: the tags on
// the cards are patterns matched over a short snippet, an "Unclassified" post
// is explicitly "not confirmed as a hiring call", and a headline that called
// all of them hiring posts would contradict the legend under it. Zero shown is
// an empty RESULT, not a finding about the company (see `emptyStateNote`), and
// only when nothing was lost on this side: if entries came back that this page
// could not display, "nothing to show" would be untrue, so the headline says
// that instead (the counts sentence below it gives the numbers).
export function resultHeadline(shown: number, unreadable = 0): string {
  if (shown === 0) {
    return unreadable > 0
      ? "This page could not display the posts the search found"
      : "No posts to show for this company";
  }
  return `${plural(shown, "post", "posts")} found`;
}

// "<what was searched> -- via <provider>". The query label comes from the
// server (a human summary, never the raw provider query).
export function resultSource(queryLabel: string, provider: SearchProvider | null): string {
  const parts = [queryLabel, `via ${providerLabel(provider)}`].filter((part) => part !== "");
  return parts.join(" -- ");
}

export const CACHED_NOTE =
  "These results came from a recent cached search, so the newest posts may not be included.";

export const UNCLASSIFIED_LEGEND =
  "Tags come from the wording of each post. Unclassified means no known pattern matched, so it is not confirmed as a hiring call.";

// What the counts sentence needs. `shown` is the number of cards actually on
// screen (not the server's own `shown`, so the sentence can never disagree
// with the page), `unreadable` is how many entries this page had to leave out
// (see SearchOutcome), and `freshness` names the window for the "too old"
// reason.
export interface CountsSummaryInput {
  counts: SignalCounts | null;
  shown: number;
  unreadable: number;
  freshness: Freshness;
}

// One sentence saying what the search returned and, for everything that is not
// on screen, exactly why -- so a short list is never a silent one. null when
// the server's counts were unreadable: better no sentence than a partial
// breakdown that reads as complete.
export function countsSummary(input: CountsSummaryInput): string | null {
  const { counts, shown, unreadable, freshness } = input;
  if (counts === null) return null;
  if (counts.raw_hits === 0) return "The search returned no results.";

  // The server hides a post for the COMPANY, never for the role: role match
  // only orders the list, so the first reason says "not about this company".
  const reasons: string[] = [];
  if (counts.off_topic_hidden > 0) {
    reasons.push(`${counts.off_topic_hidden} not about this company`);
  }
  if (counts.duplicates > 0) {
    reasons.push(plural(counts.duplicates, "duplicate", "duplicates"));
  }
  if (counts.echoes_hidden > 0) {
    const n = counts.echoes_hidden;
    reasons.push(
      `${plural(n, "automatic job-listing share", "automatic job-listing shares")} ${n === 1 ? "that repeats a listing" : "that repeat listings"} we already track`,
    );
  }
  if (counts.job_seekers_hidden > 0) {
    // Hedged: the tag is a first-person phrase found in the opening of the post.
    reasons.push(plural(counts.job_seekers_hidden, "possible job-seeker post", "possible job-seeker posts"));
  }
  if (counts.too_old_hidden > 0) {
    reasons.push(`${counts.too_old_hidden} older than the ${freshnessLabel(freshness)}`);
  }
  if (counts.rejected > 0) {
    reasons.push(
      `${counts.rejected} ${counts.rejected === 1 ? "that was not a usable post link" : "that were not usable post links"}`,
    );
  }
  if (unreadable > 0) {
    reasons.push(`${unreadable} that this page could not display`);
  }

  const head = `The search returned ${plural(counts.raw_hits, "result", "results")}`;
  if (reasons.length === 0) {
    return shown > 0 ? `${head}; all shown.` : `${head}; none shown.`;
  }
  return `${head}; ${shown} shown. Not shown: ${joinList(reasons)}.`;
}

// ── what an empty result does and does not mean ────────────────────────────

// How many entries the search index returned that WERE LinkedIn posts (whatever
// was then hidden): everything it returned, less what was not a usable post
// link. Null when the counts were unreadable.
export function postsSeen(counts: SignalCounts | null): number | null {
  return counts === null ? null : counts.raw_hits - counts.rejected;
}

// The search index's coverage is the limit of this feature, and an empty list
// is where that shows: it is evidence about the index at least as often as
// about the company (You.com, measured, returns no LinkedIn posts at all).
// These are the three honest things an empty list can be, in order of how much
// is known.
export const YOU_COM_NO_LINKEDIN_NOTE =
  "You.com's search index does not appear to include LinkedIn posts, so an empty result from it says nothing about this company. Another provider, such as Firecrawl, may cover them -- connect one in Integrations settings.";
export const NO_POSTS_SEEN_NOTE =
  "The search returned no LinkedIn posts at all, which says little about whether this company is hiring -- coverage depends on your search provider.";
export const NOTHING_SHOWN_NOTE =
  "The search index returned nothing we could show for this window. That does not mean nobody is hiring -- the index only sees part of what is posted, and coverage depends on your search provider.";

export interface EmptyStateInput {
  provider: SearchProvider | null;
  counts: SignalCounts | null;
  unreadable: number;
}

// The explanation under an empty result. Null when entries came back that this
// page could not display (the headline and the counts sentence already say so,
// and "the index returned nothing" would be untrue).
export function emptyStateNote(input: EmptyStateInput): string | null {
  if (input.unreadable > 0) return null;
  const seen = postsSeen(input.counts);
  if (seen === 0) {
    return input.provider === "you_com" ? YOU_COM_NO_LINKEDIN_NOTE : NO_POSTS_SEEN_NOTE;
  }
  return NOTHING_SHOWN_NOTE;
}

// One visible line per non-default species on screen, so the meaning of a tag
// is on the page for keyboard, touch and screen-reader users, not only in a
// hover tooltip. "Unclassified" has its own legend line (UNCLASSIFIED_LEGEND).
export interface SpeciesLegendLine {
  species: string;
  label: string;
  description: string;
}

export function speciesLegendLines(signals: readonly HiringSignal[]): SpeciesLegendLine[] {
  const seen = new Set<string>();
  const lines: SpeciesLegendLine[] = [];
  for (const signal of signals) {
    if (signal.species === "unclassified" || seen.has(signal.species)) continue;
    seen.add(signal.species);
    const info = speciesInfo(signal.species);
    lines.push({ species: signal.species, label: info.label, description: info.description });
  }
  return lines;
}

// ── saved posts ────────────────────────────────────────────────────────────

// Newest first: a save that is already in the list (same row id, or the same
// post under another row) is replaced in place; otherwise the new one goes on
// top. POST /saves is idempotent and returns the existing row on a repeat, so
// this never produces two entries for one post.
export function upsertSave(saves: readonly SavedPost[], saved: SavedPost): SavedPost[] {
  const index = saves.findIndex((s) => s.id === saved.id || s.activity_id === saved.activity_id);
  if (index === -1) return [saved, ...saves];
  const next = saves.slice();
  next[index] = saved;
  return next;
}

export function removeSave(saves: readonly SavedPost[], id: string): SavedPost[] {
  return saves.filter((s) => s.id !== id);
}

// activity id -> the saved row, so a result card can tell "saved" and knows
// which row a Remove should delete.
export function savesByActivity(saves: readonly SavedPost[]): Map<string, SavedPost> {
  return new Map(saves.map((s) => [s.activity_id, s]));
}

// Once the saved list has loaded it is the source of truth (a local save or
// remove updates it immediately, and a fresh search reloads it), so a stale
// `signal.saved` cannot resurrect a post the user just removed. Before it has
// loaded -- or if it failed to -- the server's per-signal flag is the best
// information there is.
export function isSignalSaved(
  signal: HiringSignal,
  saved: ReadonlyMap<string, SavedPost>,
  savesLoaded: boolean,
): boolean {
  if (saved.has(signal.activity_id)) return true;
  return !savesLoaded && signal.saved;
}

// ── failures ───────────────────────────────────────────────────────────────

// Fixed wording for failures whose server text would be technical or absent.
// None of them says "try again": where trying again can help, the failure view
// puts a "Try again" button next to the message, and the message must not say
// it a second time.
export const UNREACHABLE_MESSAGE = "Could not reach the server. Check your connection.";
export const UNREADABLE_MESSAGE = "The server sent a reply this page could not read.";
// NOT_FOUND on one of this panel's routes can only mean the application is
// gone, and the server's own wording for it quotes an id.
export const APPLICATION_MISSING_MESSAGE =
  "This application could not be found. It may have been deleted.";

// How the panel reacts to a failed request, decided from the API error's
// `code` alone (`ApiError` in api.ts carries `.status`, `.code` and `.message`
// -- read structurally here so this module stays free of that import):
//   disabled        -- FEATURE_DISABLED: the panel renders nothing at all
//   setup_required  -- SETUP_REQUIRED: no usable search-provider key saved
//   not_found       -- NOT_FOUND: the application is gone
//   error           -- everything else, including a network failure; the
//                      server's message is shown when there is one, and
//                      `retryable` says whether trying again can change the
//                      outcome. The server's own `retryable` flag (the error
//                      envelope carries one; `ApiError.retryable`) decides when
//                      it is present -- an application whose job snapshot is
//                      missing is a 500 that retrying can never fix -- and the
//                      status is the fallback (a 422 is a request the server
//                      rejected as invalid, and would fail the same way again)
export type PanelFailure =
  | { kind: "disabled" }
  | { kind: "setup_required"; message: string }
  | { kind: "not_found"; message: string }
  | { kind: "error"; message: string; retryable: boolean };

// A failure the panel shows (everything but "disabled", which shows nothing).
export type Failure = Exclude<PanelFailure, { kind: "disabled" }>;

function looksLikeApiError(
  error: unknown,
): error is Error & { status: number; code?: string; retryable?: boolean } {
  return error instanceof Error && typeof (error as { status?: unknown }).status === "number";
}

// The server's validation code is INVALID_INPUT (422); the design's contract
// names it INVALID_REQUEST. Either, or any 422, is a request that would fail
// the same way again.
const NOT_RETRYABLE_CODES: ReadonlySet<string> = new Set(["INVALID_INPUT", "INVALID_REQUEST"]);

// A non-API error (a `fetch` that never got an answer throws a bare
// TypeError("Failed to fetch")) gets `unreachableMessage` instead of the raw
// browser text, and is retryable.
export function classifyFailure(error: unknown, unreachableMessage: string): PanelFailure {
  if (!looksLikeApiError(error)) {
    return { kind: "error", message: unreachableMessage, retryable: true };
  }
  switch (error.code) {
    case "FEATURE_DISABLED":
      return { kind: "disabled" };
    case "SETUP_REQUIRED":
      return { kind: "setup_required", message: error.message };
    case "NOT_FOUND":
      return { kind: "not_found", message: error.message };
    default:
      return {
        kind: "error",
        message: error.message,
        retryable:
          typeof error.retryable === "boolean"
            ? error.retryable
            : !(error.status === 422 || NOT_RETRYABLE_CODES.has(error.code ?? "")),
      };
  }
}

// The one line of text for a failure shown inside a card or the saved list.
export function failureMessage(failure: Failure): string {
  return failure.kind === "not_found" ? APPLICATION_MISSING_MESSAGE : failure.message;
}

// ── card keys ──────────────────────────────────────────────────────────────

// Per-card UI state (which embeds are open, which card has an error) is keyed
// by these, and the two namespaces are separate on purpose: the same post can
// appear in the results AND the saved list, and opening one must not open the
// other.
export function searchCardKey(activityId: string): string {
  return `search:${activityId}`;
}

export function savedCardKey(saveId: string): string {
  return `saved:${saveId}`;
}

// New search results replace the old ones, so any open embed from the previous
// results is unmounted; the saved list's embeds are untouched.
export function withoutSearchKeys(keys: ReadonlySet<string>): Set<string> {
  return new Set([...keys].filter((key) => !key.startsWith("search:")));
}
