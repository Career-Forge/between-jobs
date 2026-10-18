// Today's rows: what the band says when the page is the feed. The global rows (setup, profile, the
// page's own failed read) run first; these run after them, top to bottom, and the first that applies
// wins.
//
//   T1   a status proposal with a stage to move to, newest first
//   T2   a resume the fit check held back (the application is known, and no resume exists now)
//   T3   a resume ready for review (the application is known)
//   T4   a high fit job found by a saved search
//   T5a  nothing in the feed and no saved search: save one
//   T5   nothing needs a decision (unlit)
//
// Today is the one page whose band holds the lit item itself, so the rows are over what the page has
// read about the feed -- never over anything it would have to guess. A why line names a fact only
// when the field that holds it came back: no confidence, no percentage; no application, no job name;
// no matching saved search, no search label. Nothing is said about whether the person has read an
// item, because nothing returned says so.
//
// `todayLit` is the same choice as the rows make, exported so the page can lift the very item the
// band holds out of the list below it (an item never shows twice) without deciding it a second time.

import { CROSS_NAV_HASH } from "../../applicationsBoard";
import { definePage, type NextStep, type NextStepAction, type NextStepRow } from "../types";

export type TodayKind =
  | "job_tracked"
  | "resume_ready"
  | "resume_failed"
  | "stage_changed"
  | "high_fit_job"
  | "status_proposal";

export const TODAY_KINDS: readonly TodayKind[] = [
  "job_tracked",
  "resume_ready",
  "resume_failed",
  "stage_changed",
  "high_fit_job",
  "status_proposal",
];

// The proposals the server can accept into a stage (the keys of its own stage map), each with the
// words the band uses: what the sender may have done, and the stage Accept would move the
// application to. A type that is not here has no stage to move to, so it never lights T1 and its
// row offers Dismiss only. The server's labels are noun phrases ("interview requested"), which do
// not make a sentence, so the phrases are kept here.
export const PROPOSAL_STEPS: Readonly<Record<string, { verb: string; stage: string }>> = {
  "assessment.received": { verb: "sent an assessment", stage: "Screening" },
  "interview.requested": { verb: "requested an interview", stage: "Interviewing" },
  "interview.scheduled": { verb: "scheduled an interview", stage: "Interviewing" },
  "offer.received": { verb: "made an offer", stage: "Offer" },
  "application.rejected": { verb: "turned this application down", stage: "Rejected" },
};

export function isMappableProposal(proposedType: string): boolean {
  return Object.prototype.hasOwnProperty.call(PROPOSAL_STEPS, proposedType);
}

// A reply whose confidence reaches this share moves the stage by itself; only the ones under it
// become a proposal. It is the server's own line (the Gmail reply checker's auto-track threshold).
export const AUTO_MOVE_PERCENT = 85;

export interface ProposalFact {
  proposedType: string;
  /** 0 to 1, as returned. */
  confidence: number;
  /**
   * Where the proposal stands, as returned. Only a "pending" one can still be accepted or
   * dismissed: the server refuses both for any other status, and it leaves the item of a sibling
   * proposal it resolved on its own in the feed.
   */
  status: string;
}

/**
 * The whole percent a 0 to 1 confidence stands for, rounded down (a share is never shown as higher
 * than it is), or null for a value that is not a share. The product is cleaned of float error
 * first: 0.57 * 100 is 56.99999999999999, which must not read as 56.
 */
export function confidencePercent(confidence: number): number | null {
  if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1) return null;
  return Math.floor(Math.round(confidence * 1e6) / 1e4);
}

export interface MatchFact {
  title: string;
  company: string | null;
  score100: number;
  bin: string;
  /** The saved search's label, when the saved-searches list has the search that found it. */
  searchLabel: string | null;
}

/**
 * What the fit check's stored result says about a held-back resume, as far as it has been read:
 *  - "loading": the read is out;
 *  - "unread": it was not made, or failed, or the result could not be understood -- nothing is known;
 *  - "held": the latest result is still a hold, with no resume;
 *  - "moved_on": a resume exists now (a later run made one, or the gate let it through), so the
 *    card describes something that is no longer so.
 */
export type FitFact = "loading" | "unread" | "held" | "moved_on";

export interface TodayItemFact {
  id: string;
  kind: TodayKind;
  /** ISO time the item was logged: the only date the feed returns. */
  createdAt: string;
  /** Null on an item that belongs to no application (a high fit job is not tracked yet). */
  applicationId: string | null;
  /** "Title at Company" read from the application's snapshot; null when the lookup did not find it. */
  job: string | null;
  company: string | null;
  proposal: ProposalFact | null;
  match: MatchFact | null;
  /** The resume's ATS score, when the item's detail carried one. */
  atsScore: number | null;
  /** The application a Track in this visit created, for the Open workspace that replaces Track. */
  trackedApplicationId: string | null;
  /** What the stored fit check says about a held-back resume; null on any other kind of item. */
  fit: FitFact | null;
}

export type SavedSearchesFact =
  | { state: "loading" }
  | { state: "failed" }
  | { state: "ready"; total: number; active: number };

export interface TodayFacts {
  feed: { state: "loading" } | { state: "ready"; items: readonly TodayItemFact[] };
  /** The read that names jobs has finished, whether or not it worked. */
  names: "loading" | "settled";
  savedSearches: SavedSearchesFact;
}

export function isTodayFacts(value: unknown): value is TodayFacts {
  if (typeof value !== "object" || value === null) return false;
  const facts = value as Partial<TodayFacts>;
  const feed = facts.feed;
  if (typeof feed !== "object" || feed === null) return false;
  if (feed.state === "ready" && !Array.isArray(feed.items)) return false;
  if (feed.state !== "ready" && feed.state !== "loading") return false;
  if (facts.names !== "loading" && facts.names !== "settled") return false;
  const searches = facts.savedSearches;
  return typeof searches === "object" && searches !== null && ["loading", "failed", "ready"].includes(searches.state);
}

// ── intents ────────────────────────────────────────────────────────────────

export type TodayIntentName = "accept" | "track" | "generate-anyway";

// The band names an action by an intent string and the page maps it to a handler; the item is part
// of the intent because the lit item changes as items are dismissed.
export function todayIntent(name: TodayIntentName, itemId: string): string {
  return `${name}:${itemId}`;
}

export function parseTodayIntent(intent: string): { name: TodayIntentName; itemId: string } | null {
  const split = intent.indexOf(":");
  if (split === -1) return null;
  const name = intent.slice(0, split);
  const itemId = intent.slice(split + 1);
  if ((name !== "accept" && name !== "track" && name !== "generate-anyway") || itemId === "") return null;
  return { name, itemId };
}

// ── the choice ─────────────────────────────────────────────────────────────

export type TodayRowId = "T1" | "T2" | "T3" | "T4";

export interface Lit {
  row: TodayRowId;
  item: TodayItemFact;
}

// Newest first by the item's own time; an unreadable time sorts last, and equal times keep the
// order the feed returned them in.
export function newestFirst<T>(items: readonly T[], createdAt: (item: T) => string): T[] {
  const time = (item: T) => {
    const parsed = Date.parse(createdAt(item));
    return Number.isNaN(parsed) ? Number.NEGATIVE_INFINITY : parsed;
  };
  return items
    .map((item, index) => ({ item, index }))
    .sort((a, b) => {
      const by = time(b.item) - time(a.item);
      return by !== 0 && !Number.isNaN(by) ? by : a.index - b.index;
    })
    .map(({ item }) => item);
}

// A held-back card can light T2 only while the held-back fact may still be true.
function heldBack(item: TodayItemFact): boolean {
  return item.kind === "resume_failed" && item.applicationId !== null && item.fit !== "moved_on";
}

// Which item the band holds, if any. Precedence is the row order; within a row, the newest item.
// An item with no application can never light T2 or T3 (nothing to link to), so it falls through,
// and a proposal that is no longer pending can never light T1 (Accept and Dismiss would both be
// refused), so it stays in the list as a plain item.
export function todayLit(items: readonly TodayItemFact[]): Lit | null {
  const ordered = newestFirst(items, (item) => item.createdAt);
  const t1 = ordered.find(
    (item) =>
      item.kind === "status_proposal" &&
      item.proposal !== null &&
      item.proposal.status === "pending" &&
      isMappableProposal(item.proposal.proposedType),
  );
  if (t1 !== undefined) return { row: "T1", item: t1 };
  const t2 = ordered.find(heldBack);
  if (t2 !== undefined) return { row: "T2", item: t2 };
  const t3 = ordered.find((item) => item.kind === "resume_ready" && item.applicationId !== null);
  if (t3 !== undefined) return { row: "T3", item: t3 };
  const t4 = ordered.find((item) => item.kind === "high_fit_job" && item.match !== null);
  if (t4 !== undefined) return { row: "T4", item: t4 };
  return null;
}

type Decision = { kind: "wait" } | { kind: "lit"; lit: Lit } | { kind: "empty-no-searches" } | { kind: "quiet"; updates: number };

function decide(facts: TodayFacts): Decision {
  if (facts.feed.state === "loading") return { kind: "wait" };
  const items = facts.feed.items;
  const lit = todayLit(items);
  // A held-back card whose fit read is still out could turn out to have moved on, and would then be
  // replaced by the next choice. It matters only to a choice it would outrank (anything below T1),
  // and only if it is the card that is lit or newer than it: an older one cannot replace it.
  if (lit?.row !== "T1") {
    const ordered = newestFirst(items, (item) => item.createdAt);
    const limit = lit?.row === "T2" ? ordered.indexOf(lit.item) + 1 : ordered.length;
    if (ordered.slice(0, limit).some((item) => item.kind === "resume_failed" && item.applicationId !== null && item.fit === "loading")) {
      return { kind: "wait" };
    }
  }
  if (lit !== null) {
    // T1 to T3 name the job or the company in their sentence; arriving a moment later would change
    // the sentence under the person's eyes.
    const needsNames = lit.row !== "T4";
    return needsNames && facts.names === "loading" ? { kind: "wait" } : { kind: "lit", lit };
  }
  // Which of the two quiet rows applies depends on the saved-searches list.
  if (facts.savedSearches.state === "loading") return { kind: "wait" };
  if (items.length === 0 && facts.savedSearches.state === "ready" && facts.savedSearches.total === 0) return { kind: "empty-no-searches" };
  return { kind: "quiet", updates: items.length };
}

// ── the steps ──────────────────────────────────────────────────────────────

// The workspace of an application, optionally at one of its sections.
export function workspacePath(applicationId: string, hash?: string): string {
  const path = `/applications/${encodeURIComponent(applicationId)}`;
  return hash === undefined ? path : `${path}#${hash}`;
}

function plural(count: number, one: string, many: string): string {
  return count === 1 ? one : many;
}

function proposalStep(item: TodayItemFact): NextStep | null {
  const proposal = item.proposal;
  if (proposal === null) return null;
  const words = PROPOSAL_STEPS[proposal.proposedType];
  if (words === undefined) return null;
  const subject = item.company ?? "A reply to your application";
  const percent = confidencePercent(proposal.confidence);
  const why = ["status proposal"];
  if (percent !== null) {
    why.push(`${percent}%`);
    // Compared on the confidence itself, the way the server compares it, not on the rounded share.
    if (proposal.confidence < AUTO_MOVE_PERCENT / 100) why.push(`under the ${AUTO_MOVE_PERCENT}% auto-move line`);
  }
  return {
    sentence: `${subject} may have ${words.verb}. Read ${item.company === null ? "the" : "their"} words, then decide.`,
    action: { kind: "button", label: `Accept: move to ${words.stage}`, intent: todayIntent("accept", item.id) },
    why,
  };
}

function heldBackStep(item: TodayItemFact): NextStep | null {
  if (item.applicationId === null) return null;
  // "No resume generated" is a claim about the application now, so it is made only when the stored
  // result was read and still says so; a card whose read failed says only what the card is.
  const why = item.fit === "held" ? ["held back by the fit check", "no resume generated"] : ["held back by the fit check"];
  return {
    sentence: `The fit check held back your draft${item.job === null ? "" : ` for ${item.job}`}. Read why, then decide.`,
    action: { kind: "link", label: "See why", to: workspacePath(item.applicationId, CROSS_NAV_HASH.generate) },
    secondary: { kind: "button", label: "Generate anyway", intent: todayIntent("generate-anyway", item.id) },
    why,
  };
}

function readyStep(item: TodayItemFact): NextStep | null {
  if (item.applicationId === null) return null;
  const why = ["resume ready"];
  if (item.atsScore !== null) why.push(`ATS score ${item.atsScore} / 100`);
  return {
    sentence: item.job === null ? "Your resume is ready for review." : `Your resume for ${item.job} is ready for review.`,
    action: { kind: "link", label: "Open workspace", to: workspacePath(item.applicationId) },
    why,
  };
}

const KNOWN_BINS: readonly string[] = ["Strong", "Good", "Mixed", "Poor"];

function matchStep(item: TodayItemFact): NextStep | null {
  const match = item.match;
  if (match === null) return null;
  const action: NextStepAction =
    item.trackedApplicationId === null
      ? { kind: "button", label: "Track", intent: todayIntent("track", item.id) }
      : { kind: "link", label: "Open workspace", to: workspacePath(item.trackedApplicationId) };
  const what = KNOWN_BINS.includes(match.bin) ? `A ${match.bin} match` : "A match";
  const why = ["high fit job", `${match.score100} / 100`];
  if (match.searchLabel !== null) why.push(`saved search ${match.searchLabel}`);
  return {
    sentence: `${what} landed: ${match.title}${match.company === null ? "" : ` at ${match.company}`}.`,
    action,
    why,
  };
}

function litRow(id: TodayRowId, build: (item: TodayItemFact) => NextStep | null): NextStepRow<TodayFacts> {
  return {
    id,
    match: (input) => {
      if (input.facts === undefined) return null;
      const decision = decide(input.facts);
      if (decision.kind === "wait") return "wait";
      return decision.kind === "lit" && decision.lit.row === id ? build(decision.lit.item) : null;
    },
  };
}

const T1 = litRow("T1", proposalStep);
const T2 = litRow("T2", heldBackStep);
const T3 = litRow("T3", readyStep);
const T4 = litRow("T4", matchStep);

const T5A: NextStepRow<TodayFacts> = {
  id: "T5a",
  match: (input) => {
    if (input.facts === undefined) return null;
    const decision = decide(input.facts);
    if (decision.kind === "wait") return "wait";
    if (decision.kind !== "empty-no-searches") return null;
    return {
      sentence: "Save a search in Discover so Strong matches can land here.",
      action: { kind: "link", label: "Go to Discover", to: "/discover" },
      why: ["0 items", "0 saved searches"],
    };
  },
};

const T5: NextStepRow<TodayFacts> = {
  id: "T5",
  match: (input) => {
    if (input.facts === undefined) return null;
    const decision = decide(input.facts);
    if (decision.kind === "wait") return "wait";
    if (decision.kind !== "quiet") return null;
    const why = ["0 items need a decision"];
    const searches = input.facts.savedSearches;
    if (searches.state === "ready") why.push(`${searches.active} saved ${plural(searches.active, "search", "searches")} active`);
    return {
      sentence: `Nothing needs a decision right now.${decision.updates > 0 ? ` ${decision.updates} ${plural(decision.updates, "update", "updates")} below.` : ""}`,
      action: null,
      why,
    };
  },
};

export const rules = definePage<TodayFacts>("today", [T1, T2, T3, T4, T5A, T5], { facts: isTodayFacts });
