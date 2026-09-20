// Hiring Signals P4 -- pure, testable logic for the standalone "Hiring signals"
// tab: input normalization, the request the form turns into, the wording of
// every computed fact, and the small predicates the model and the view share.
// Split out of the render components (and the model) the same way
// hiringSignals.ts, discover.ts and applicationsBoard.ts split theirs.
//
// Three groups, and the first is the one a mistake would make visible.
//
// 1. NORMALIZATION THAT MATCHES THE SERVER. A saved search is unique per user on
//    its role and location compared case-insensitively with whitespace
//    collapsed (a unique index; the server is the authority). The page decides
//    "already saved" and "Save this search is pointless" before it sends
//    anything, so it must collapse and fold case the same way, or it offers a
//    Save the server then answers with the existing row. `collapseText` and
//    `equivalenceKey` are that one shared definition. A difference at the very
//    edge (a Unicode space, a Turkish dotted capital I) can only make the page
//    offer a Save the server then answers with 200 and the existing row -- which
//    the model handles by replacing, not adding -- never lose or duplicate one.
//
// 2. WHAT A FORM MEANS. `checkForm` turns the raw text fields into either a
//    normalized request or per-field errors, and `searchRequestBody` turns a
//    request into the exact body that goes over the wire: `locale` only when the
//    person chose one ("Auto" is a form-only choice and is omitted), `location`
//    null when blank. The server's own limits (a role of 1-200 characters after
//    trimming, a location of at most 100) are counted in code points, like the
//    server counts them.
//
// 3. HONEST LABELS. As in P3, "unknown" stays visibly unknown and a short list
//    is never a silent one: the counts sentence spells out every category that
//    was hidden and why, using THIS tab's keys. `role_mismatch_hidden` is
//    "posts that did not contain all the words of the role" -- never "not about
//    this company", which was P3's meaning and is false here. An empty result is
//    a true, useful answer and is worded as one: it says what the search
//    returned and what could not be shown, never that nobody is hiring, because
//    the index sees only part of what is posted.
//
// Nothing here touches the network, the DOM or React, and (like the types
// module) it must not import `./api`.

import {
  type Failure,
  freshnessLabel,
  joinList,
  plural,
  postsSeen,
  providerLabel,
} from "./hiringSignals";
import {
  LOCATION_MAX_CHARS,
  type Locale,
  type LocaleChoice,
  QUERY_MAX_CHARS,
  type SavedHiringSearch,
  type TabCounts,
} from "./hiringSignalsTabTypes";
import type { Freshness, RegistryMatch, SearchProvider } from "./hiringSignalsTypes";

// ── the default window ─────────────────────────────────────────────────────

// The window the form starts on. The contract leaves the server's default open
// ("chosen from real yield data during the build, start from 3days"), so this is
// the ONE place the page states it: a request always sends the window
// explicitly, which means this constant only decides what the form shows first.
// If the server's default is settled on something else, change it here.
export const TAB_DEFAULT_FRESHNESS: Freshness = "3days";

// ── normalization ──────────────────────────────────────────────────────────

// Trim and collapse every run of whitespace to one space: the server's
// definition of "the same words".
export function collapseText(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

// The server counts characters as Python does, by code point; JavaScript's
// `.length` counts UTF-16 units and would call one emoji two.
export function codePointLength(value: string): number {
  return Array.from(value).length;
}

// The identity of a saved search: case-folded, whitespace-collapsed role and
// location, with a blank location the same as none. The separator is a newline,
// which `collapseText` guarantees can never appear inside either part.
export function equivalenceKey(query: string, location: string | null): string {
  return `${collapseText(query).toLowerCase()}\n${collapseText(location ?? "").toLowerCase()}`;
}

export function findEquivalentSearch(
  searches: readonly SavedHiringSearch[],
  query: string,
  location: string | null,
): SavedHiringSearch | null {
  const key = equivalenceKey(query, location);
  return searches.find((s) => equivalenceKey(s.query, s.location) === key) ?? null;
}

// ── the form and the request it becomes ────────────────────────────────────

export interface TabForm {
  query: string;
  location: string;
  freshness: Freshness;
  locale: LocaleChoice;
}

export const INITIAL_FORM: TabForm = {
  query: "",
  location: "",
  freshness: TAB_DEFAULT_FRESHNESS,
  locale: "auto",
};

// A search exactly as it goes over the wire (and as the results remember it).
export interface TabSearchRequest {
  query: string;
  location: string | null;
  freshness: Freshness;
  // null = Auto: the server derives it from the location, and the key is omitted.
  locale: Locale | null;
}

export interface FieldErrors {
  query: string | null;
  location: string | null;
}

export const NO_FIELD_ERRORS: FieldErrors = { query: null, location: null };

export const QUERY_REQUIRED_MESSAGE = "Enter a role to search for.";
export const QUERY_TOO_LONG_MESSAGE = `Use ${QUERY_MAX_CHARS} characters or fewer for the role.`;
export const LOCATION_TOO_LONG_MESSAGE =
  `Use ${LOCATION_MAX_CHARS} characters or fewer for the location.`;

export type FormCheck =
  | { ok: true; request: TabSearchRequest }
  | { ok: false; errors: FieldErrors };

// Validates and normalizes. Errors are per field so each can be shown next to
// its own input; the request holds the collapsed text, so what is searched,
// saved and compared is one and the same string.
export function checkForm(form: TabForm): FormCheck {
  const query = collapseText(form.query);
  const location = collapseText(form.location);
  const errors: FieldErrors = {
    query:
      query === ""
        ? QUERY_REQUIRED_MESSAGE
        : codePointLength(query) > QUERY_MAX_CHARS
          ? QUERY_TOO_LONG_MESSAGE
          : null,
    location: codePointLength(location) > LOCATION_MAX_CHARS ? LOCATION_TOO_LONG_MESSAGE : null,
  };
  if (errors.query !== null || errors.location !== null) return { ok: false, errors };
  return {
    ok: true,
    request: {
      query,
      location: location === "" ? null : location,
      freshness: form.freshness,
      locale: form.locale === "auto" ? null : form.locale,
    },
  };
}

// The form a request corresponds to: what a re-run (a saved search, a widened
// window, a retry) writes back so the inputs always show what was searched.
export function formFromRequest(request: TabSearchRequest): TabForm {
  return {
    query: request.query,
    location: request.location ?? "",
    freshness: request.freshness,
    locale: request.locale ?? "auto",
  };
}

export function sameRequest(a: TabSearchRequest, b: TabSearchRequest): boolean {
  return (
    a.query === b.query &&
    a.location === b.location &&
    a.freshness === b.freshness &&
    a.locale === b.locale
  );
}

// The body of POST /hiring-signals/search. Exported so a test can assert the
// exact key set: `locale` is present only when the person chose one.
export function searchRequestBody(request: TabSearchRequest): {
  query: string;
  location: string | null;
  freshness: Freshness;
  locale?: Locale;
} {
  const body: { query: string; location: string | null; freshness: Freshness; locale?: Locale } =
    {
      query: request.query,
      location: request.location,
      freshness: request.freshness,
    };
  if (request.locale !== null) body.locale = request.locale;
  return body;
}

// The body of POST /hiring-signals/searches: the person's own typed role and
// location, and nothing else (a saved search does not run, schedule or watch
// anything, and does not remember the window or the wording).
export function savedSearchBody(request: TabSearchRequest): {
  query: string;
  location: string | null;
} {
  return { query: request.query, location: request.location };
}

// ── "Save this search" ─────────────────────────────────────────────────────

export const SAVE_NEEDS_ROLE_REASON = "Enter a role to save this search.";
export const ALREADY_SAVED_REASON = "This search is already saved.";

export type SaveSearchGate = { kind: "ok" } | { kind: "blocked"; reason: string };

// Whether "Save this search" would do anything, and if not, why -- the reason is
// shown as visible text (not only a tooltip) next to the button. The 25-search
// cap is NOT checked here on purpose: the server owns it and its own message is
// what the person should read.
export function saveSearchGate(
  form: TabForm,
  searches: readonly SavedHiringSearch[],
): SaveSearchGate {
  // No role at all reads in the save button's own terms; a role or location that is
  // too long carries the field's own message.
  if (collapseText(form.query) === "") {
    return { kind: "blocked", reason: SAVE_NEEDS_ROLE_REASON };
  }
  const checked = checkForm(form);
  if (!checked.ok) {
    return {
      kind: "blocked",
      reason: checked.errors.query ?? checked.errors.location ?? SAVE_NEEDS_ROLE_REASON,
    };
  }
  if (findEquivalentSearch(searches, checked.request.query, checked.request.location) !== null) {
    return { kind: "blocked", reason: ALREADY_SAVED_REASON };
  }
  return { kind: "ok" };
}

// "software engineer -- Bengaluru": what a saved search reads as in the list. It
// is the person's own typed text, never anything from a provider.
export function savedSearchLabel(search: Pick<SavedHiringSearch, "query" | "location">): string {
  return search.location === null ? search.query : `${search.query} -- ${search.location}`;
}

// ── option labels ──────────────────────────────────────────────────────────

export const LOCALE_OPTIONS: readonly { value: LocaleChoice; label: string }[] = [
  { value: "auto", label: "Auto (from the location)" },
  { value: "global", label: "Global" },
  { value: "india", label: "India" },
];

const LOCALE_LABELS: Record<Locale, string> = { india: "India", global: "Global" };

export function localeLabel(locale: Locale): string {
  return LOCALE_LABELS[locale];
}

// ── the page's own words ───────────────────────────────────────────────────

// Not every result is a hiring call ("Unclassified" says a post matched no known
// pattern), so the page never calls the whole list "hiring posts"; and the
// location is a search word, not a filter: only the part before its first comma is
// searched, and no post is checked against it.
export const TAB_INTRO =
  "Recent public posts that mention a role, found through the search provider you connected. Some are hiring calls and others are not.";
export const TAB_LOCATION_HINT =
  "Narrows the search: only the part before the first comma is searched, and no post is checked against it.";
export const TAB_FORM_LABEL = "Search posts";

// ── notices (the polite live region) ───────────────────────────────────────

export const NOTICE_POST_SAVED = "Saved to your saved posts.";
export const NOTICE_POST_REMOVED = "Removed from saved posts.";
export const NOTICE_SEARCH_SAVED = "Search saved.";
export const NOTICE_SEARCH_ALREADY_SAVED = "That search was already saved.";
export const NOTICE_SEARCH_REMOVED = "Removed the saved search.";

// NOT_FOUND on a tab route can only mean the thing the person acted on is gone;
// P3's wording for it names an application, which is wrong here.
export const TAB_NOT_FOUND_MESSAGE = "That could not be found. It may already have been removed.";
export const SAVED_SEARCHES_LOAD_MESSAGE = "Could not load your saved searches.";
export const SAVED_POSTS_LOAD_MESSAGE = "Could not load your saved posts.";

// The one line of text for a failure shown inline (a card, the saved lists, the
// save-search error). The server's own message is shown as it is -- that is how
// the 25-search cap reaches the person -- and only a missing thing is reworded.
export function tabFailureMessage(failure: Failure): string {
  return failure.kind === "not_found" ? TAB_NOT_FOUND_MESSAGE : failure.message;
}

// Per-card errors belong to one namespace or the other (see searchCardKey and
// savedCardKey); a new search replaces the results, so only THEIR errors go.
export function withoutSearchErrors(
  errors: Readonly<Record<string, string>>,
): Readonly<Record<string, string>> {
  return Object.fromEntries(Object.entries(errors).filter(([key]) => !key.startsWith("search:")));
}

// ── result wording ─────────────────────────────────────────────────────────

// The headline above the cards. It counts POSTS, not hiring posts (an
// "Unclassified" post is explicitly not confirmed as a hiring call), and zero is
// an empty RESULT, not a finding about the role -- unless entries came back that
// this page could not display, in which case "nothing to show" would be untrue.
export function tabResultHeadline(shown: number, unreadable = 0): string {
  if (shown === 0) {
    return unreadable > 0
      ? "This page could not display the posts the search found"
      : "No posts to show for this search";
  }
  return `${plural(shown, "post", "posts")} found`;
}

// "<what was searched> -- via <provider> -- <wording> wording". The query label
// comes from the server (a human summary, never the raw provider query); the
// wording is the server's own echo of the locale it used, so the person can see
// what "Auto" chose.
export function tabResultSource(
  queryLabel: string,
  provider: SearchProvider | null,
  locale: Locale | null,
): string {
  const parts = [
    queryLabel,
    `via ${providerLabel(provider)}`,
    locale === null ? "" : `${localeLabel(locale)} wording`,
  ].filter((part) => part !== "");
  return parts.join(" -- ");
}

export interface TabCountsSummaryInput {
  counts: TabCounts | null;
  // Cards actually on screen (not the server's own `shown`, so the sentence can
  // never disagree with the page).
  shown: number;
  // Entries this page had to leave out (see TabSearchOutcome).
  unreadable: number;
  freshness: Freshness;
}

// One sentence saying what the search returned and, for everything that is not
// on screen, exactly why. null when the server's counts were unreadable or did
// not add up (the parser decided): better no sentence than a partial breakdown
// that reads as complete.
export function tabCountsSummary(input: TabCountsSummaryInput): string | null {
  const { counts, shown, unreadable, freshness } = input;
  if (counts === null) return null;
  if (counts.raw_hits === 0) return "The search returned no results.";

  // The server hides a post for the ROLE here (P3 hid it for the company): the
  // role filter needs every word of the role to appear in the post's text.
  const reasons: string[] = [];
  if (counts.role_mismatch_hidden > 0) {
    reasons.push(`${counts.role_mismatch_hidden} that did not contain all the words of your role`);
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
    reasons.push(
      plural(counts.job_seekers_hidden, "possible job-seeker post", "possible job-seeker posts"),
    );
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

// The search index's coverage is the limit of this feature, and an empty list is
// where that shows: it is evidence about the index at least as often as about
// the role. These are the honest things an empty list can be, in order of how
// much is known.
export const TAB_YOU_COM_NO_LINKEDIN_NOTE =
  "You.com's search index does not appear to include LinkedIn posts, so an empty result from it says nothing about this role. Another provider, such as Firecrawl, may cover them -- connect one in Integrations settings.";
export const TAB_NO_POSTS_SEEN_NOTE =
  "The search returned no LinkedIn posts at all, which says little about whether anyone is hiring for this role -- coverage depends on your search provider.";
export const TAB_NOTHING_SHOWN_NOTE =
  "The search index returned nothing we could show for this role and window. That does not mean nobody is hiring -- the index only sees part of what is posted, and coverage depends on your search provider.";

export interface TabEmptyStateInput {
  provider: SearchProvider | null;
  counts: TabCounts | null;
  unreadable: number;
}

// The explanation under an empty result. null when entries came back that this
// page could not display (the headline and the counts sentence already say so,
// and "the index returned nothing" would be untrue).
export function tabEmptyNote(input: TabEmptyStateInput): string | null {
  if (input.unreadable > 0) return null;
  if (postsSeen(input.counts) === 0) {
    return input.provider === "you_com" ? TAB_YOU_COM_NO_LINKEDIN_NOTE : TAB_NO_POSTS_SEEN_NOTE;
  }
  return TAB_NOTHING_SHOWN_NOTE;
}

export const HINT_SHORTER_ROLE =
  "Some posts did not contain all the words of your role -- a shorter role title may match more of them.";
export const HINT_DROP_LOCATION =
  "The location only narrows the search; removing it may return more posts.";

// The role hint outside an empty result: when the role filter hid at least half of
// what the search returned, the role is probably worded more narrowly than the
// posts are (on 2026-09-20 a search for a C++ developer in Pune hid 8 of its 10
// results this way; the hint used to be shown only when NOTHING was left, so a
// person with two posts on screen never learned the other eight existed). Below
// half, the counts sentence already says how many were hidden and that is enough.
export function tabRoleHint(counts: TabCounts | null): string | null {
  if (counts === null || counts.role_mismatch_hidden === 0) return null;
  return counts.role_mismatch_hidden * 2 >= counts.raw_hits ? HINT_SHORTER_ROLE : null;
}

// ── a search that stopped at the provider's cap ────────────────────────────

// The most results the server asks a provider for in one search
// (`MAX_RESULTS_PER_CALL` in hiring_signal_search.py, which a backend test keeps
// equal to this). A search that came back with that many stopped there: the index
// may hold more, and the counts sentence alone reads as if it had returned
// everything. Two searches over the same words a window apart, both at the cap,
// share only part of their results -- so the page says so instead of implying a
// complete list. (An observation on 2026-09-20: the 3-day and 7-day Bengaluru
// searches, both at 20, shared 7 posts, though every 3-day post is inside the week.)
export const PROVIDER_RESULT_LIMIT = 20;

export const TAB_TRUNCATED_NOTE = `The search returned ${PROVIDER_RESULT_LIMIT} results, the most your provider gives for one search, so it may have stopped early. A different window or place can bring up different posts.`;

export function tabTruncationNote(counts: TabCounts | null): string | null {
  return counts !== null && counts.raw_hits >= PROVIDER_RESULT_LIMIT ? TAB_TRUNCATED_NOTE : null;
}

// ── a role too short to search safely ──────────────────────────────────────

// The role filter needs the role's words to appear in a post, and a very short one
// appears everywhere: `PM` is also a time of day (in the sanitized sample corpus
// under tests/golden/hiring_signals/inputs the only posts a `PM` role keeps are
// three walk-in posts that name a time). The server cannot tell which sense a post
// means, so the page says so before the person trusts the list.
export const LOOSE_ROLE_NOTE =
  "That role is very short, so it also matches posts that use those letters in another sense, such as a time of day. Adding a second word, like the field, makes the match tighter.";

const LOOSE_ROLE_MAX_CHARS = 3;

// True for a role that is a single word of at most three characters. Words are
// runs of letters and digits (a `+`, `#` or `.` stays inside a word: `c++`, `.net`);
// this looks at the role as typed, the same text the person sees in the box.
export function isLooseRole(query: string): boolean {
  const words = query.toLowerCase().split(/[^\p{L}\p{N}+#.]+/u).filter((word) => word !== "");
  return words.length === 1 && Array.from(words[0]).length <= LOOSE_ROLE_MAX_CHARS;
}

export function looseRoleNote(query: string): string | null {
  return isLooseRole(query) ? LOOSE_ROLE_NOTE : null;
}

// ── notes on a tab card ────────────────────────────────────────────────────

// The per-application panel's note says "this company"; on the tab there is no
// company of the person's own, and the registry match is about the page that
// SHARED the listing.
const TAB_REGISTRY_MATCH_NOTES: Record<RegistryMatch, string> = {
  matched: "Matches a job listing we already track.",
  possible: "May match a job listing we already track.",
  unmatched: "We track the company that shared this, but not this listing yet.",
};

export function tabRegistryMatchNote(match: RegistryMatch | null): string | null {
  return match === null ? null : TAB_REGISTRY_MATCH_NOTES[match];
}

// Suggestions for an empty result, each tied to something that is actually true
// of THIS search (a count, an input), never generic advice.
export function tabEmptyHints(input: {
  counts: TabCounts | null;
  location: string | null;
}): string[] {
  const hints: string[] = [];
  if (input.counts !== null && input.counts.role_mismatch_hidden > 0) {
    hints.push(HINT_SHORTER_ROLE);
  }
  if (input.location !== null) hints.push(HINT_DROP_LOCATION);
  return hints;
}
