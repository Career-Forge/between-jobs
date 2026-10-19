// The Applications page's rows. They run after the global ones, so by the time one is asked the
// profile has been read and is active, the setup reads have answered, and the page's own list did
// not fail (a failed read is the global F1 row, with "Try again").
//
//   P0  the list is still being read            wait: nothing is lit, then replaced a moment later
//   P1  nothing tracked yet                     "Track your first job ..."   Start tracking
//   P2  something is tracked                    "Your move on 3 · their move on 2."   (unlit)
//
// P2 is unlit on purpose. Which application to act on is a choice per card, so no single button on
// the page is the right one, and nothing else on the page turns cyan to fill the gap. The sentence
// counts what the list says and nothing it would have to guess: "your move" is the four statuses
// where the next action is the person's (saved, screening, interviewing, offer) and "their move"
// is applied. Rejected and withdrawn applications are at rest and counted in neither.
//
// The button of P1 is the paste form: it needs no key. Fetching a posting by URL needs a Firecrawl
// key unless the registry already has the posting, so it is not what a first-time visitor is sent to.

import { ALL_STATUSES, type ApplicationStatus } from "../../applicationsTypes";
import { definePage, type NextStepRow } from "../types";

export type StatusCounts = Readonly<Record<ApplicationStatus, number>>;

export type ApplicationsFacts = { list: { state: "loading" } } | { list: { state: "ready"; counts: StatusCounts } };

/** The intent of P1's button: the page scrolls to the paste form and focuses it. */
export const START_TRACKING_INTENT = "start-tracking";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isApplicationsFacts(value: unknown): value is ApplicationsFacts {
  if (!isRecord(value) || !isRecord(value.list)) return false;
  if (value.list.state === "loading") return true;
  if (value.list.state !== "ready" || !isRecord(value.list.counts)) return false;
  const counts = value.list.counts;
  return ALL_STATUSES.every((status) => typeof counts[status] === "number" && Number.isInteger(counts[status]) && counts[status] >= 0);
}

/** The statuses where the next move is the person's. */
export const YOUR_MOVE: readonly ApplicationStatus[] = ["saved", "screening", "interviewing", "offer"];

function total(counts: StatusCounts): number {
  return ALL_STATUSES.reduce((sum, status) => sum + counts[status], 0);
}

const P0: NextStepRow<ApplicationsFacts> = {
  id: "P0",
  match: (input) => (input.facts?.list.state === "loading" ? "wait" : null),
};

const P1: NextStepRow<ApplicationsFacts> = {
  id: "P1",
  match: (input) => {
    const list = input.facts?.list;
    if (list?.state !== "ready" || total(list.counts) > 0) return null;
    return {
      sentence: "Track your first job -- paste a posting or fetch it by URL.",
      action: { kind: "button", label: "Start tracking", intent: START_TRACKING_INTENT },
      why: ["0 applications"],
    };
  },
};

const P2: NextStepRow<ApplicationsFacts> = {
  id: "P2",
  match: (input) => {
    const list = input.facts?.list;
    if (list?.state !== "ready" || total(list.counts) === 0) return null;
    const yours = YOUR_MOVE.reduce((sum, status) => sum + list.counts[status], 0);
    const theirs = list.counts.applied;
    return {
      sentence: `Your move on ${yours} · their move on ${theirs}.`,
      action: null,
      why: [`${yours} saved, screening, interviewing, offer`, `${theirs} applied`],
    };
  },
};

export const rules = definePage<ApplicationsFacts>("applications", [P0, P1, P2], { facts: isApplicationsFacts });
