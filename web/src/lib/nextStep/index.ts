// The next-step band's rule table, as one pure function.
//
//   nextStep(input) -> { status: "loading" } | { status: "none" } | { status: "ready", step }
//
// Code picks the sentence -- never a model. Global rows (global.ts) run first on every page, then
// the rows of the page that is asking, top to bottom, and the first row that applies decides. A row
// reads only the input, and every fact in a why line comes from a field of it, so the answer can
// never name a state the page did not read.
//
// ADDING A PAGE. Write the page's rows in their own file (today.ts, settings.ts, ...) with
// `definePage("today", [rows])`, then add one import and one entry to PAGES below. Nothing else
// changes, and two pages never edit the same lines.

import { evaluate, evaluateWithRow, type Evaluation } from "./engine";
import { GLOBAL_ROWS } from "./global";
import { landingRules } from "./landing";
import type { NextStepInput, NextStepOutcome, PageRules } from "./types";

const PAGES: readonly PageRules<never>[] = [
  landingRules,
  // one entry per page: todayRules, settingsRules, ...
];

export function nextStep(input: NextStepInput): NextStepOutcome {
  return evaluate(input, GLOBAL_ROWS, PAGES);
}

// The same answer with the id of the row that decided it, for tests and debugging.
export function nextStepWithRow(input: NextStepInput): Evaluation {
  return evaluateWithRow(input, GLOBAL_ROWS, PAGES);
}

export { evaluate, evaluateWithRow } from "./engine";
export { AUTH_FORM_ID, GLOBAL_ROWS, LEGAL_PAGES, PUBLIC_PAGES, SIGNED_OUT_WHY } from "./global";
export { definePage } from "./types";
export type {
  CredentialsFacts,
  NextStep,
  NextStepAction,
  NextStepInput,
  NextStepOutcome,
  NextStepRow,
  PageFailure,
  PageRules,
  ProfileFacts,
  RowResult,
  SetupCheck,
} from "./types";
