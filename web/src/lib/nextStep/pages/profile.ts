// The Profile page's rows. They run after the global ones, so by the time one is asked the profile
// has been read and is active (the global G2 and G2b answer first when it is not), the setup reads
// have answered, and the page is not in a whole-page failure. Every sentence is chosen by code, and
// every fact in a `why` line is something the page was told it read.
//
//   PR1  an import is waiting and the person is not on its review   "Your import is waiting ..."  Review the import
//   IR1  the person is on the review itself                          "Check every field ..."        Confirm and make active
//   PR0  nothing is waiting                                          "Your profile is active ..."   (unlit)
//
// WHAT "ON THE REVIEW" MEANS. A waiting import is known only in the session that created it: the API
// returns the active version and nothing that lists a draft, so after a reload there is none to review
// and PR0 applies. Within the session the review is "the thing the person is on" when it is the page's
// main content (the JSON preview replaces the page; with no profile in use the review is all that is
// there) or when they followed the band's link to #import-review. Anywhere else the band offers the
// link first.
//
// WHICH BUTTON IS CYAN. The band holds the page's only cyan fill. On the review that is "Confirm and
// make active"; the review's own "Use this profile" and "Looks good -- activate" are drawn as ordinary
// outlined buttons, because one action is cyan once. For a resume file the button is locked (a dashed
// outline and a hint, not a fill) until every field read from the file has been checked one by one:
// there is deliberately no "check all". The JSON import has no such list to check -- it is parsed by
// code from the person's own template and shows what was counted -- so its button is never locked.
//
// THE GLOBAL ROWS ON THIS PAGE. G2 and G2b link to /profile, which is where the person already is:
// `profileOutcome` points them at the import section (#set-up) or the review (#import-review), and
// puts the review's own step in place of G2b while the person is on the review, since the global row
// would say "Review the import" to someone looking at it.

import { definePage, type NextStep, type NextStepOutcome, type NextStepRow } from "../types";

export const IMPORT_REVIEW_ID = "import-review";
export const SET_UP_ID = "set-up";
export const IMPORT_REVIEW_PATH = `/profile#${IMPORT_REVIEW_ID}`;
export const SET_UP_PATH = `/profile#${SET_UP_ID}`;

/** The intents the band's buttons carry on this page. */
export const PROFILE_INTENTS = {
  /** A resume file's draft: make it the active profile. The page asks the import card to do it. */
  useImport: "use-import",
  /** The JSON import's preview: make it the active profile. */
  activateJson: "activate-json",
} as const;

/** What is waiting for review, as far as the page knows. */
export type WaitingImport =
  | { source: "json" }
  | {
      source: "file";
      /** Values read from the file that the person is to check one by one. */
      found: number;
      /** Values the model proposed that the check against the file did not keep; null when the server did not say. */
      leftOut: number | null;
      /** How many of `found` have not been checked yet. */
      remaining: number;
    };

export interface ProfilePageFacts {
  waiting: WaitingImport | null;
  /** The review is what the person is looking at (see the top of this file). */
  onReview: boolean;
  /** "Sep 21": when the active version was activated, from `activated_at`; null when it was not returned. */
  activeSince: string | null;
  /**
   * A profile is in use now, so making the waiting import active takes its place. The band's one-step
   * button does that in a single press, so its sentence says so; absent means the page did not say.
   */
  replacesCurrent?: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

export function isProfilePageFacts(value: unknown): value is ProfilePageFacts {
  if (!isRecord(value) || typeof value.onReview !== "boolean") return false;
  if (value.activeSince !== null && typeof value.activeSince !== "string") return false;
  if (value.replacesCurrent !== undefined && typeof value.replacesCurrent !== "boolean") return false;
  const waiting = value.waiting;
  if (waiting === null) return true;
  if (!isRecord(waiting)) return false;
  if (waiting.source === "json") return true;
  return (
    waiting.source === "file" &&
    isCount(waiting.found) &&
    (waiting.leftOut === null || isCount(waiting.leftOut)) &&
    isCount(waiting.remaining) &&
    waiting.remaining <= waiting.found
  );
}

const WAITING_WHY = {
  json: "parsed by code, no model",
  file: "converted by your model, probabilistic",
} as const;

const PR1: NextStepRow<ProfilePageFacts> = {
  id: "PR1",
  match: (input) => {
    const facts = input.facts;
    if (facts === undefined || facts.waiting === null || facts.onReview) return null;
    return {
      sentence: "Your import is waiting. Check every field, then activate.",
      action: { kind: "link", label: "Review the import", to: IMPORT_REVIEW_PATH },
      why: ["import waiting for review", WAITING_WHY[facts.waiting.source], "not active until you confirm"],
    };
  },
};

function fieldsWord(count: number): string {
  return count === 1 ? "field" : "fields";
}

// The band's button activates in one press, with no question asked at the button, so when it would take
// the place of a profile in use the sentence beside it says that.
const REPLACES_NOTE = " It replaces your current one.";

/** IR1's step for a waiting import, or null when none is waiting. Also what replaces G2b on the review. */
export function reviewStep(facts: ProfilePageFacts): NextStep | null {
  const waiting = facts.waiting;
  if (waiting === null) return null;
  const replaces = facts.replacesCurrent === true ? REPLACES_NOTE : "";
  if (waiting.source === "json") {
    return {
      sentence: `Check what was read, then make this your profile.${replaces}`,
      action: { kind: "button", label: "Confirm and make active", intent: PROFILE_INTENTS.activateJson },
      why: ["import waiting for review", WAITING_WHY.json, "draft inactive"],
    };
  }
  const why = [
    `${waiting.found} ${fieldsWord(waiting.found)} found`,
    ...(waiting.leftOut === null ? [] : [`${waiting.leftOut} left out`]),
    `${waiting.remaining} still to check`,
    "draft inactive",
  ];
  const step: NextStep = {
    sentence: `Check every field, then make this your profile.${replaces}`,
    action: { kind: "button", label: "Confirm and make active", intent: PROFILE_INTENTS.useImport },
    why,
  };
  if (waiting.remaining === 0) return step;
  return {
    ...step,
    locked: { hint: `Check the ${waiting.remaining} remaining ${fieldsWord(waiting.remaining)} to unlock this.` },
  };
}

const IR1: NextStepRow<ProfilePageFacts> = {
  id: "IR1",
  match: (input) => {
    const facts = input.facts;
    if (facts === undefined || !facts.onReview) return null;
    return reviewStep(facts);
  },
};

const PR0: NextStepRow<ProfilePageFacts> = {
  id: "PR0",
  match: (input) => {
    const facts = input.facts;
    if (facts === undefined || facts.waiting !== null) return null;
    return {
      sentence: "Your profile is active. Edits save a new version.",
      action: null,
      why: [facts.activeSince === null ? "profile active" : `profile active since ${facts.activeSince}`],
    };
  },
};

export const rules = definePage("profile", [PR1, IR1, PR0], { facts: isProfilePageFacts });

// What the page shows for the answer the engine gave, given the row that decided it.
export function profileOutcome(
  evaluation: { outcome: NextStepOutcome; rowId: string | null },
  facts: ProfilePageFacts,
): NextStepOutcome {
  const { outcome, rowId } = evaluation;
  if (outcome.status !== "ready") return outcome;
  const step = outcome.step;
  if (step.action === null || step.action.kind !== "link") return outcome;
  if (rowId === "G2") {
    // The person is on /profile already: the link goes to the section that does the setting up.
    return { status: "ready", step: { ...step, action: { ...step.action, to: SET_UP_PATH } } };
  }
  if (rowId === "G2b") {
    // No profile is in use and an import is waiting. On its review the step is the review's own.
    const own = facts.onReview ? reviewStep(facts) : null;
    if (own !== null) return { status: "ready", step: own };
    return { status: "ready", step: { ...step, action: { ...step.action, to: IMPORT_REVIEW_PATH } } };
  }
  return outcome;
}
