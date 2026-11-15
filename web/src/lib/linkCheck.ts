// What the "Check posting" button on an application card says, as pure functions: the reply the server
// sends, the words for each answer, and the line the card draws. No React, no request (that is
// linkCheckClient.ts) and nothing stored: the answer lives in the page until it is reloaded.
//
// THREE ANSWERS, NEVER A GUESS. The server says one of `still_listed`, `gone` or `cant_tell`, and a
// `cant_tell` always carries a reason. Each reason has its own words below, and a reason this table
// does not know (a server newer than this bundle) is said as one we cannot read, never as a reason we
// made up. Every line that stops short of an answer ends with the same suggestion: open the posting.
//
// WORDS NOT TO SAY. `gone` says the posting is no longer on the company's own board and that this usually
// means it closed; it never says the person was turned down, and never that a job was filled. `still_listed`
// says it is on the board, never that it is "verified" (lib/trail.ts bans the word for the same reason).
//
// The reply also carries `ats`, which platform the link was read as. The card does not show it, so it is
// not read here.

import { friendlyApiMessage } from "./rateLimitMessage";
import { formatTrailTime } from "./trail";

export type LinkCheckState = "still_listed" | "gone" | "cant_tell";

const STATES: readonly LinkCheckState[] = ["still_listed", "gone", "cant_tell"];

export interface LinkCheckResult {
  state: LinkCheckState;
  /** The server's reason code. Only a `cant_tell` is worded from it (`cantTellWords`). */
  reason: string;
  /** The server's ISO UTC time of the check, or null when the reply had none. */
  checked_at: string | null;
}

// A reply that is not an object, or whose `state` is not one of the three, is not an answer.
export function parseLinkCheck(body: unknown): LinkCheckResult | null {
  if (typeof body !== "object" || body === null || Array.isArray(body)) return null;
  const raw = body as { state?: unknown; reason?: unknown; checked_at?: unknown };
  const state = STATES.find((candidate) => candidate === raw.state);
  if (state === undefined) return null;
  return {
    state,
    reason: typeof raw.reason === "string" ? raw.reason : "",
    checked_at: typeof raw.checked_at === "string" ? raw.checked_at : null,
  };
}

// ── the words ──────────────────────────────────────────────────────────────

export const STILL_LISTED_WORDS = "Still on the company's own board.";
export const GONE_WORDS =
  "No longer on the company's own board. That usually means the posting closed. It does not mean you were rejected.";
export const CANT_TELL_SUGGESTION = "Open the posting to see for yourself.";

// The kill switch's reason. It comes back as a 503 (`isLinkCheckDisabled`), not as an answer, but it has words
// like any other reason.
export const LINK_CHECK_DISABLED = "link_check_disabled";

const ROBOTS_WORDS = "We don't check this site because its rules ask automated tools to stay out";
const NO_ANSWER_WORDS = "The company's site did not answer in time";

// A Map, so a reason such as "constructor" is simply not found.
export const CANT_TELL_REASON_WORDS: ReadonlyMap<string, string> = new Map([
  ["board_missing_or_moved", "The company's board is not where we expected it, so we can't say about this job"],
  ["empty_listing", "The company's board returned no jobs right now"],
  ["signals_disagree", "The company's board and the job page disagree, so we won't guess"],
  ["blocked", "The company's site declined the check"],
  ["rate_limited", "The company's site asked us to slow down; try again later"],
  ["robots_disallowed", ROBOTS_WORDS],
  ["robots_unavailable", ROBOTS_WORDS],
  ["not_covered", "We don't check this company's job platform"],
  ["denylisted", "We don't check this site"],
  ["company_page", "This link is on the company's own site, not a job platform we can read"],
  ["no_link", "This job has no link saved"],
  ["unlisted_direct_link", "This job is only reachable by direct link, so absence from the board means nothing"],
  ["timeout", NO_ANSWER_WORDS],
  ["network", NO_ANSWER_WORDS],
  [LINK_CHECK_DISABLED, "Checking is switched off right now"],
]);

// What a `cant_tell` says for a reason this table does not have: the server stopped short for a reason
// this page cannot name, and says so.
export const UNKNOWN_REASON_WORDS = "The company's board gave an answer we can't use, so we can't say about this job";

export function cantTellWords(reason: string): string {
  return `${CANT_TELL_REASON_WORDS.get(reason) ?? UNKNOWN_REASON_WORDS}. ${CANT_TELL_SUGGESTION}`;
}

// ── a request that did not come back as an answer ─────────────────────────

export const UNREADABLE_WORDS = `The check didn't go through. The answer could not be read. ${CANT_TELL_SUGGESTION}`;

// The 503 an operator's kill switch answers with. The envelope names it in `details.reason` (read into
// `reason`); a 503 that does not (a proxy's, a deploy in progress) is a plain failure, not "switched off".
export function isLinkCheckDisabled(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  const fields = error as Error & { status?: unknown; code?: unknown; reason?: unknown };
  return fields.status === 503 && (fields.reason === LINK_CHECK_DISABLED || fields.code === LINK_CHECK_DISABLED);
}

// The person pressed Check posting on a card whose application they have since deleted (in another tab, say).
// The server's 404 names the id, which is nothing to show, and there is no posting left to open.
export const APPLICATION_MISSING_WORDS = "That application is no longer here.";

function isApplicationMissing(error: unknown): boolean {
  return error instanceof Error && (error as Error & { status?: unknown }).status === 404;
}

function endSentence(text: string): string {
  const trimmed = text.trim();
  return trimmed === "" || /[.!?]$/.test(trimmed) ? trimmed : `${trimmed}.`;
}

// The words for a press that got no answer: the kill switch, the person's own rate limit (the app's usual
// wording: friendlyApiMessage), or anything else in the server's words. Each ends with the suggestion, except
// the application that is no longer there: it has no posting to open.
export function failureWords(error: unknown): string {
  if (isLinkCheckDisabled(error)) return cantTellWords(LINK_CHECK_DISABLED);
  if (isApplicationMissing(error)) return APPLICATION_MISSING_WORDS;
  const message = endSentence(friendlyApiMessage(error, "Something went wrong."));
  return ["The check didn't go through.", message, CANT_TELL_SUGGESTION].filter((part) => part !== "").join(" ");
}

// ── what a card holds, and the line it draws ──────────────────────────────

// Held by the page, per application, until it is reloaded. `receivedAt` is when this page got the answer
// (its own clock, so a skewed server clock cannot make "just now" wrong).
export type LinkCheckEntry =
  | { kind: "pending" }
  | { kind: "answered"; result: LinkCheckResult; receivedAt: number }
  | { kind: "unanswered"; words: string };

export interface LinkCheckLine {
  /** What `data-state` carries: the answer, or "unanswered" for a press that got none. */
  state: LinkCheckState | "unanswered";
  text: string;
  /** "Checked just now." A still-listed answer is the only one that says when: the others make no claim about time. */
  when: string | null;
}

// "Checked just now." is true for a minute. After that the line gives the clock time of the check, which stays
// true however long the page is left open.
export const JUST_NOW_MS = 60_000;

export function checkedWords(result: LinkCheckResult, receivedAt: number, now: number): string {
  if (now - receivedAt < JUST_NOW_MS) return "Checked just now.";
  const time = formatTrailTime(result.checked_at) ?? formatTrailTime(new Date(receivedAt).toISOString());
  return time === null ? "Checked earlier." : `Checked at ${time}.`;
}

// The line for an entry, or null while nothing is to be said (no press yet, or the request is out).
export function linkCheckLine(entry: LinkCheckEntry | undefined, now: number): LinkCheckLine | null {
  if (entry === undefined || entry.kind === "pending") return null;
  if (entry.kind === "unanswered") return { state: "unanswered", text: entry.words, when: null };
  const { result, receivedAt } = entry;
  switch (result.state) {
    case "still_listed":
      return { state: "still_listed", text: STILL_LISTED_WORDS, when: checkedWords(result, receivedAt, now) };
    case "gone":
      return { state: "gone", text: GONE_WORDS, when: null };
    case "cant_tell":
      return { state: "cant_tell", text: cantTellWords(result.reason), when: null };
  }
}
