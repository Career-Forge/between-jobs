// The next-step band's rule table, as one pure function.
//
//   nextStep(input) -> { status: "loading" } | { status: "none" } | { status: "ready", step }
//
// Code picks the sentence -- never a model. Global rows (global.ts) run first on every page, then
// the rows of the page that is asking, top to bottom, and the first row that applies decides. A row
// reads only the input, and every fact in a why line comes from a field of it, so the answer can
// never name a state the page did not read.
//
// ADDING A PAGE. Add one file, pages/<name>.ts, that exports the page's rows as `rules`:
//
//   export const rules = definePage("today", [rows], { facts: isTodayFacts });
//
// Nothing else changes: the registry below is built from the files in pages/, so two pages are
// two files and never edit the same line. A file that exports no `rules`, a page name used twice
// and a row id used twice all throw when this module loads.

import { collectPages, evaluate, evaluateWithRow, type Evaluation } from "./engine";
import { GLOBAL_ROWS } from "./global";
import type { NextStepInput, NextStepOutcome, PageRules } from "./types";

// Test files in pages/ are not pages.
const modules = import.meta.glob<{ rules?: PageRules<never> }>(["./pages/*.ts", "!./pages/*.test.ts"], { eager: true });

export const PAGES: readonly PageRules<never>[] = collectPages(modules, GLOBAL_ROWS);

export function nextStep(input: NextStepInput): NextStepOutcome {
  return evaluate(input, GLOBAL_ROWS, PAGES);
}

// The same answer with the id of the row that decided it, for tests and debugging.
export function nextStepWithRow(input: NextStepInput): Evaluation {
  return evaluateWithRow(input, GLOBAL_ROWS, PAGES);
}

export { evaluate, evaluateWithRow } from "./engine";
export { AUTH_FORM_ID, GLOBAL_ROWS, LEGAL_PAGES, PUBLIC_PAGES, SIGNED_OUT_WHY } from "./global";
export { collectPages } from "./engine";
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
