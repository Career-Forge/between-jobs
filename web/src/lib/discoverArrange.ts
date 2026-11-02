// How a page of Discover results is narrowed and ordered, as pure functions: which rows each fit choice
// keeps, how many that is, how each sort orders a section, and the sentences that say what was chosen.
// No React, no clock, no storage. The toolbar that draws the choices is components/discover/ResultsToolbar.tsx
// and the state that holds them is ResultsView.
//
// Two sections, kept apart. A search returns rows the model scored and "More results" past its scoring
// batch. A sort orders each section among itself and never merges them, because a merged list would put
// unscored rows between scored ones and make "Not scored" rows look ranked. A tier choice keeps the rows
// of that tier (they are all in the scored section) and so hides "More results"; the count line says how
// many unscored rows that left out.
//
// A tier is only a group worth choosing when the bins mean something. Under the scoring fallback every
// bin is a placeholder, so no tier is offered and "Best fit" is not a sort.
//
// A row is scored when its bin is one of the four the ladder knows, and unscored otherwise: with no
// score at all, or with a bin nobody can read. One rule (tierOf), used by the row, the counts, the chips
// and the filter alike, so a row that reads "Not scored" is counted, filtered and drawn as one.

import type { JobCard, ScoreBin } from "./discoverTypes";
import { isScoreWithin } from "./scoreRange";
import { postedDay, postedMillis } from "./trail";

export type FitFilter = "all" | ScoreBin | "unscored";
export type SortMode = "returned" | "fit" | "newest";

/** The two lists a page of results is drawn in: the rows the model scored, then "More results". */
export type Section = "scored" | "more";

/** Tier chips in display order, best first (the ladder's FIT_LEVELS, reversed). */
export const TIER_ORDER: readonly ScoreBin[] = ["Strong", "Good", "Mixed", "Poor"];

/** The least number of rows for which choosing between groups or orders means anything. */
export const TOOLBAR_MIN_ROWS = 3;

export const FILTER_LABEL: Readonly<Record<FitFilter, string>> = {
  all: "All",
  Strong: "Strong",
  Good: "Good",
  Mixed: "Mixed",
  Poor: "Poor",
  unscored: "Not scored",
};

export const SORT_LABEL: Readonly<Record<SortMode, string>> = {
  returned: "As returned",
  fit: "Best fit",
  newest: "Newest",
};

export interface FitCounts {
  all: number;
  tiers: Readonly<Record<ScoreBin, number>>;
  unscored: number;
}

/** The row's tier, or null when it has no score or a bin the ladder does not know. */
export function tierOf(job: JobCard): ScoreBin | null {
  const bin = job.score?.bin;
  return TIER_ORDER.find((tier) => tier === bin) ?? null;
}

/**
 * True when the row has no tier: no score object (null or undefined), or a bin the ladder does not know.
 * It reads "Not scored", and is counted and filtered as not scored, whatever else its score object holds.
 */
export function isUnscored(job: JobCard): boolean {
  return tierOf(job) === null;
}

/** Counts over every row (scored then more): each row is in exactly one tier or in `unscored`. */
export function fitCounts(rows: readonly JobCard[]): FitCounts {
  const tiers: Record<ScoreBin, number> = { Strong: 0, Good: 0, Mixed: 0, Poor: 0 };
  let unscored = 0;
  for (const row of rows) {
    const tier = tierOf(row);
    if (tier === null) unscored += 1;
    else tiers[tier] += 1;
  }
  return { all: rows.length, tiers, unscored };
}

export function countFor(counts: FitCounts, filter: FitFilter): number {
  if (filter === "all") return counts.all;
  if (filter === "unscored") return counts.unscored;
  return counts.tiers[filter];
}

/**
 * The filters to draw, in order: "all", the non-empty tiers (best first) when tiers are meaningful, then
 * "unscored" when any row is unscored. Empty array when fewer than two non-empty groups exist (nothing to
 * choose between), and always empty when tiers are not meaningful and only unscored rows could be a group.
 */
export function offeredFilters(counts: FitCounts, tiersMeaningful: boolean): readonly FitFilter[] {
  const tiers = tiersMeaningful ? TIER_ORDER.filter((tier) => counts.tiers[tier] > 0) : [];
  const unscored: readonly FitFilter[] = counts.unscored > 0 ? ["unscored"] : [];
  if (tiers.length + unscored.length < 2) return [];
  return ["all", ...tiers, ...unscored];
}

/** `filter` when it is offered, otherwise "all". */
export function resolveFilter(filter: FitFilter, offered: readonly FitFilter[]): FitFilter {
  return offered.includes(filter) ? filter : "all";
}

// The number a row is ranked by: its score100 when the row reads as scored (it has a tier) and that number
// is a real score, 0 to 100. A row that reads "Not scored" has no rank key whatever number it carries, and
// NaN, Infinity, anything that is not a number and a number outside the scale are no score at all, here and
// wherever a number would be drawn (scoreRange.ts).
function fitOf(job: JobCard): number | null {
  if (isUnscored(job)) return null;
  const value: unknown = job.score?.score100;
  return isScoreWithin(value) ? value : null;
}

/**
 * True when `rows` hold at least two whose posting date the trail can read, in `timeZone` and against the
 * `now` the rows print with (both as the trail reads them). A section is put in date order by "Newest"
 * only then, so this is also whether its notice may say "newest first".
 */
export function reordersByDate(rows: readonly JobCard[], timeZone?: string, now?: Date): boolean {
  return rows.filter((row) => postedDay(row.posted_at, timeZone, now) !== null).length >= 2;
}

/**
 * ["returned", "fit"?, "newest"?]. "fit" only when tiers are meaningful and at least two of `scoredRows`
 * read as scored and have a score from 0 to 100. "newest" only when some section has at least two rows
 * whose posting date the trail can read: a sort that cannot move anything is not offered, and so is never
 * announced. Pass the rows the fit choice currently keeps, so a control that would do nothing to what is
 * shown is left out.
 */
export function offeredSorts(input: {
  tiersMeaningful: boolean;
  scoredRows: readonly JobCard[];
  moreRows?: readonly JobCard[];
  timeZone?: string;
  now?: Date;
}): readonly SortMode[] {
  const rankable = input.tiersMeaningful && input.scoredRows.filter((row) => fitOf(row) !== null).length >= 2;
  const datable = reordersByDate(input.scoredRows, input.timeZone, input.now) || reordersByDate(input.moreRows ?? [], input.timeZone, input.now);
  return [
    "returned" as const,
    ...(rankable ? ["fit" as const] : []),
    ...(datable ? ["newest" as const] : []),
  ];
}

export function resolveSort(sort: SortMode, offered: readonly SortMode[]): SortMode {
  return offered.includes(sort) ? sort : "returned";
}

// A sort key is a pair compared largest first: a score, or the day a row prints and then its instant.
type SortKey = readonly [number, number];

/**
 * A new array; stable; never mutates `rows`. "fit" puts the highest score100 first and rows with no real
 * score last: none, one outside 0 to 100, or a row that reads "Not scored" whatever number it carries.
 * "newest" puts the latest printed day first (read in `timeZone`, the viewer's own when it is omitted,
 * against `now`, exactly as the row's trail prints it), the later instant first within a day, and rows
 * whose date the trail cannot read last. Equal keys keep the order the search returned.
 */
export function sortRows(rows: readonly JobCard[], sort: SortMode, timeZone?: string, now?: Date): JobCard[] {
  if (sort === "returned") return [...rows];
  const key = (job: JobCard): SortKey | null => {
    if (sort === "fit") {
      const value = fitOf(job);
      return value === null ? null : [value, 0];
    }
    const day = postedDay(job.posted_at, timeZone, now);
    return day === null ? null : [day, postedMillis(job.posted_at) ?? 0];
  };
  return rows
    .map((row, index) => ({ row, index, value: key(row) }))
    .sort((a, b) => {
      if (a.value === null && b.value === null) return a.index - b.index;
      if (a.value === null) return 1;
      if (b.value === null) return -1;
      return b.value[0] - a.value[0] || b.value[1] - a.value[1] || a.index - b.index;
    })
    .map((entry) => entry.row);
}

export interface ArrangeInput {
  scored: readonly JobCard[];
  more: readonly JobCard[];
  filter: FitFilter;
  sort: SortMode;
  /** The zone dates are read in for Newest; the viewer's own when omitted. */
  timeZone?: string;
  /** The clock the rows print their dates against; when it is not a real date no row prints one. */
  now?: Date;
}

export interface Arranged {
  scored: JobCard[];
  more: JobCard[];
}

function keeps(filter: FitFilter, job: JobCard): boolean {
  if (filter === "all") return true;
  if (filter === "unscored") return isUnscored(job);
  return tierOf(job) === filter;
}

/**
 * The two sections after a fit choice and a sort. Rows past the scoring batch have no fit, so they keep
 * the order they came in under "Best fit". Under "Newest" a section is put in date order only when it has
 * at least two readable dates (reordersByDate); a section with fewer keeps the order it came in, so what a
 * section's notice says about its order is exactly what happened to it.
 */
export function arrange(input: ArrangeInput): Arranged {
  const { filter, sort, timeZone, now } = input;
  const scored = input.scored.filter((job) => keeps(filter, job));
  const more = input.more.filter((job) => keeps(filter, job));
  const byDate = (rows: readonly JobCard[]): SortMode => (reordersByDate(rows, timeZone, now) ? "newest" : "returned");
  return {
    scored: sortRows(scored, sort === "newest" ? byDate(scored) : sort, timeZone, now),
    more: sortRows(more, sort === "newest" ? byDate(more) : "returned", timeZone, now),
  };
}

function resultsWord(total: number): string {
  return total === 1 ? "result" : "results";
}

/** The visible count line: null for "all". */
export function filterSummary(filter: FitFilter, shown: number, total: number, hiddenUnscored: number): string | null {
  if (filter === "all") return null;
  const head = `Showing ${shown} of ${total} ${resultsWord(total)}, `;
  if (filter === "unscored") return `${head}not scored only.`;
  const hidden = hiddenUnscored > 0 ? ` ${hiddenUnscored} not scored ${hiddenUnscored === 1 ? "is" : "are"} not shown.` : "";
  return `${head}${filter} fit only.${hidden}`;
}

const SORT_SENTENCE: Readonly<Record<SortMode, string>> = {
  returned: "Sorted as the search returned them.",
  fit: "Sorted by best fit first.",
  newest: "Sorted by newest first.",
};

// What is said of a section Newest left in the order the search gave it, in the words a person sees on the
// page: the list under "More results", and the rows above that heading.
const KEPT_ORDER_SENTENCE: Readonly<Record<Section, string>> = {
  scored: "The rows above More results keep the search's order.",
  more: "More results keep the search's order.",
};

// "Sorted by newest first." claims an order for every row on the page, so it is said whole only when each
// non-empty section shown was put in date order. A section that kept the order it came in is named, and
// when none moved nothing is claimed at all.
function newestSentence(keptOrder: readonly Section[]): string {
  if (keptOrder.length === 0) return SORT_SENTENCE.newest;
  if (keptOrder.includes("scored") && keptOrder.includes("more")) return SORT_SENTENCE.returned;
  return `${SORT_SENTENCE.newest} ${keptOrder.map((section) => KEPT_ORDER_SENTENCE[section]).join(" ")}`;
}

/**
 * The polite announcement after a press. A filter's carries `sort` only when the choice left the sort that
 * was shown off offer, so the order that took its place is said as well: a person never finds the rows in
 * an order nobody told them about. A sort's carries, for Newest, the non-empty sections shown that it left
 * in the order the search gave them (none by default: every section moved).
 */
export function arrangeAnnouncement(
  change:
    | { kind: "filter"; filter: FitFilter; shown: number; total: number; hiddenUnscored: number; sort?: SortMode }
    | { kind: "sort"; sort: SortMode; keptOrder?: readonly Section[] },
): string {
  if (change.kind === "sort") return change.sort === "newest" ? newestSentence(change.keptOrder ?? []) : SORT_SENTENCE[change.sort];
  const summary = filterSummary(change.filter, change.shown, change.total, change.hiddenUnscored) ?? `Showing all ${change.total} ${resultsWord(change.total)}.`;
  return change.sort === undefined ? summary : `${summary} ${SORT_SENTENCE[change.sort]}`;
}
