import { describe, expect, it } from "vitest";
import { deriveFirstRun, type FirstRunFacts, type Fetched } from "./firstRun";

// The first-run checklist's rules, held to the same table the chat bot's copy of them is held to.
// `tests/shared/first_run_steps.json` lists accounts and the five steps each must come out with;
// `tests/test_first_run.py` runs it against src/between_jobs/api/first_run.py. The two
// implementations cannot drift apart without one of these tests failing. (vite's `?raw` glob, not
// `node:fs`: @types/node is not installed here.)

const raw = import.meta.glob("../../../tests/shared/first_run_steps.json", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

interface SharedCase {
  name: string;
  facts: {
    profile: boolean | null;
    model_key: boolean | null;
    saved_searches: number | null;
    applications: { count: number; from_discover: number; with_resume: number } | null;
  };
  searched: boolean | null;
  steps: Record<string, string>;
  done_count: number;
  next_step: string | null;
}

interface SharedTable {
  step_order: string[];
  labels: Record<string, string>;
  pages: Record<string, string>;
  cases: SharedCase[];
}

const files = Object.values(raw);
if (files.length !== 1) throw new Error("tests/shared/first_run_steps.json was not found");
const TABLE = JSON.parse(files[0]) as SharedTable;

// A null in the table is a fact that could not be read.
function fetched<T>(value: T | null): Fetched<T> {
  return value === null ? { kind: "failed" } : { kind: "ok", value };
}

function factsOf(c: SharedCase): FirstRunFacts {
  const applications = c.facts.applications;
  return {
    profile: fetched(c.facts.profile),
    modelKey: fetched(c.facts.model_key),
    savedSearches: fetched(c.facts.saved_searches),
    applications: fetched(
      applications === null
        ? null
        : {
            count: applications.count,
            fromDiscover: applications.from_discover,
            withResume: applications.with_resume,
            // Only decides where the web links "Generate a resume"; the chat has no such id.
            resumeTargetId: null,
          },
    ),
  };
}

describe("the first-run checklist agrees with the shared table", () => {
  it("has a table worth running", () => {
    expect(TABLE.cases.length).toBeGreaterThanOrEqual(15);
    expect(new Set(TABLE.cases.map((c) => c.name)).size).toBe(TABLE.cases.length);
  });

  it.each(TABLE.cases.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const view = deriveFirstRun(factsOf(c), { dismissed: false, searched: c.searched });

    expect(view.steps.map((s) => s.id)).toEqual(TABLE.step_order);
    expect(Object.fromEntries(view.steps.map((s) => [s.id, s.status]))).toEqual(c.steps);
    expect(view.doneCount).toBe(c.done_count);
    expect(view.nextStep?.id ?? null).toBe(c.next_step);
  });

  it("names and places the steps as the table says", () => {
    const view = deriveFirstRun(factsOf(TABLE.cases[0]), { dismissed: false, searched: false });

    expect(Object.fromEntries(view.steps.map((s) => [s.id, s.label]))).toEqual(TABLE.labels);
    expect(Object.fromEntries(view.steps.map((s) => [s.id, s.to]))).toEqual(TABLE.pages);
  });
});
