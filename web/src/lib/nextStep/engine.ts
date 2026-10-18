// The engine: global rows first, then the asking page's own rows, the first row that applies wins.
// Pure -- no React, no fetch, no clock. Same input, same answer.

import type { NextStepInput, NextStepOutcome, NextStepRow, PageRules } from "./types";

export interface Evaluation {
  outcome: NextStepOutcome;
  /** The id of the row that decided ("G1", "T3"), or null when no row applied. */
  rowId: string | null;
}

export function evaluateWithRow(
  input: NextStepInput,
  globalRows: readonly NextStepRow[],
  pages: readonly PageRules<never>[],
): Evaluation {
  const pageRows = (pages.find((rules) => rules.page === input.page)?.rows ?? []) as readonly NextStepRow[];
  for (const row of [...globalRows, ...pageRows]) {
    const result = row.match(input);
    if (result === null) continue;
    if (result === "wait") return { outcome: { status: "loading" }, rowId: row.id };
    if (result === "none") return { outcome: { status: "none" }, rowId: row.id };
    return { outcome: { status: "ready", step: result }, rowId: row.id };
  }
  // No row applies: the page has no step to offer, and the engine does not make one up.
  return { outcome: { status: "none" }, rowId: null };
}

export function evaluate(
  input: NextStepInput,
  globalRows: readonly NextStepRow[],
  pages: readonly PageRules<never>[],
): NextStepOutcome {
  return evaluateWithRow(input, globalRows, pages).outcome;
}
