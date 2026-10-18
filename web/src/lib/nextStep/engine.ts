// The engine: global rows first, then the asking page's own rows, the first row that applies wins.
// Pure -- no React, no fetch, no clock. Same input, same answer.

import type { NextStepInput, NextStepOutcome, NextStepRow, PageRules } from "./types";

export interface Evaluation {
  outcome: NextStepOutcome;
  /** The id of the row that decided ("G1", "L0"), or null when no row applied. */
  rowId: string | null;
}

export function evaluateWithRow(
  input: NextStepInput,
  globalRows: readonly NextStepRow[],
  pages: readonly PageRules<never>[],
): Evaluation {
  const rules = pages.find((candidate) => candidate.page === input.page);
  // Rows are typed to their page's facts, which the string-keyed entry point cannot know; the
  // page's own guard (when it has one) is what stands between a caller's value and a row.
  const factsReadable = rules?.facts === undefined || rules.facts(input.facts);
  const pageRows = (factsReadable ? (rules?.rows ?? []) : []) as readonly NextStepRow[];
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

// What the registry in index.ts is built from: the modules found in pages/, each exporting its
// page as `rules`. Order follows the file path so the registry is the same on every machine.
// Throws, at load, for a module that exports no page, a page name used twice (the first would
// silently win), or a row id used twice anywhere (a test keyed on it would be ambiguous).
export function collectPages(
  modules: Readonly<Record<string, { rules?: PageRules<never> } | undefined>>,
  globalRows: readonly NextStepRow[],
): readonly PageRules<never>[] {
  const pages: PageRules<never>[] = [];
  const pageNames = new Map<string, string>();
  const rowIds = new Map<string, string>(globalRows.map((row) => [row.id, "the global rows"]));
  if (rowIds.size !== globalRows.length) throw new Error("next step: a global row id is used twice");
  for (const path of Object.keys(modules).sort()) {
    const rules = modules[path]?.rules;
    if (rules === undefined || typeof rules.page !== "string" || !Array.isArray(rules.rows)) {
      throw new Error(`next step: ${path} does not export its page as \`rules\` (use definePage)`);
    }
    const earlier = pageNames.get(rules.page);
    if (earlier !== undefined) throw new Error(`next step: page "${rules.page}" is defined in both ${earlier} and ${path}`);
    pageNames.set(rules.page, path);
    for (const row of rules.rows) {
      const owner = rowIds.get(row.id);
      if (owner !== undefined) throw new Error(`next step: row id "${row.id}" in ${path} is already used by ${owner}`);
      rowIds.set(row.id, path);
    }
    pages.push(rules);
  }
  return pages;
}
