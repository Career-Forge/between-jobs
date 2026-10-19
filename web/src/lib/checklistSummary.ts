// The counts line above a human-review checklist: "5 pass · 1 fail · 4 not checked". Counts only --
// never a percentage, a score or the word "verified". Not checked is its own count and is never added
// to the passes.

export type ChecklistState = "pass" | "fail" | "not_checked";

export interface ChecklistCounts {
  pass: number;
  fail: number;
  notChecked: number;
}

export function checklistCounts(states: readonly ChecklistState[]): ChecklistCounts {
  const counts: ChecklistCounts = { pass: 0, fail: 0, notChecked: 0 };
  for (const state of states) {
    if (state === "pass") counts.pass += 1;
    else if (state === "fail") counts.fail += 1;
    else counts.notChecked += 1;
  }
  return counts;
}

export function checklistSummary(states: readonly ChecklistState[]): string {
  const { pass, fail, notChecked } = checklistCounts(states);
  return `${pass} pass · ${fail} fail · ${notChecked} not checked`;
}
