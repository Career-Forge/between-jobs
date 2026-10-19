import { describe, expect, it } from "vitest";
import { PAGES, nextStepWithRow } from "..";
import type { NextStepInput, NextStepOutcome, ProfileFacts, SetupCheck } from "..";
import { findForbiddenWord } from "../../trail";
import { DISCOVER_FORM_ID, isDiscoverFacts, rules, type DiscoverFacts, type DiscoverResultsFact } from "./discover";

// Discover's rows, as a table: what the page has read in, which row decides, what the band says
// out. The global rows (G0..F1) run before these, so the table also pins the cases they decide here.

const ACTIVE: SetupCheck<ProfileFacts> = { state: "ready", value: { active: true, importWaiting: false } };
const NO_PROFILE: SetupCheck<ProfileFacts> = { state: "ready", value: { active: false, importWaiting: false } };
const CREDS: SetupCheck<Record<string, never>> = { state: "ready", value: {} };

function results(overrides: Partial<DiscoverResultsFact> = {}): DiscoverResultsFact {
  return {
    scored: 30,
    more: 42,
    deadRemoved: 3,
    providers: [
      { provider: "registry", count: 24 },
      { provider: "serper", count: 6 },
    ],
    failedProviders: [],
    otherNotes: 0,
    fallback: false,
    filtersSet: 2,
    companiesSet: true,
    ...overrides,
  };
}

function facts(overrides: Partial<DiscoverFacts> = {}): DiscoverFacts {
  return { phase: "idle", key: "ready", searchProviders: 2, results: null, ...overrides };
}

function ask(input: Partial<NextStepInput<DiscoverFacts>>) {
  return nextStepWithRow({ page: "discover", signedIn: true, profile: ACTIVE, credentials: CREDS, facts: facts(), ...input } as NextStepInput);
}

function step(outcome: NextStepOutcome) {
  if (outcome.status !== "ready") throw new Error(`expected a step, got ${outcome.status}`);
  return outcome.step;
}

describe("the page is registered", () => {
  it("is one of the pages the registry found, with its own row ids", () => {
    expect(PAGES.map((page) => page.page)).toContain("discover");
    expect(rules.rows.map((row) => row.id)).toEqual(["D1", "D2", "D3", "D4b", "D4", "D5"]);
  });
});

describe("the table", () => {
  const rows: Array<{
    name: string;
    input: Partial<NextStepInput<DiscoverFacts>>;
    row: string;
    sentence: string;
    action: string | null;
    why: string[];
  }> = [
    {
      name: "no model key saved",
      input: { facts: facts({ key: "none" }) },
      row: "D1",
      sentence: "Discover scores jobs with your model. Add a model key first.",
      action: "link:Add a model key:/settings#ai-provider",
      why: ["profile active", "no model key saved"],
    },
    {
      name: "a key is saved and checked, with no model chosen",
      input: { facts: facts({ key: "needs_model" }) },
      row: "D1",
      sentence: "Discover scores jobs with your model. Your key is saved -- choose a model first.",
      action: "link:Choose a model:/settings#ai-provider",
      why: ["profile active", "a key is saved, no model chosen"],
    },
    {
      name: "a key is saved and the provider did not confirm it",
      input: { facts: facts({ key: "needs_key" }) },
      row: "D1",
      sentence: "Discover scores jobs with your model. Add a model key first.",
      action: "link:Add a model key:/settings#ai-provider",
      why: ["profile active", "a key is saved, not confirmed"],
    },
    {
      name: "the server answered setup required although the keys read as ready",
      input: { facts: facts({ phase: "setup" }) },
      row: "D1",
      sentence: "Discover scores jobs with your model. Add a model key first.",
      action: "link:Add a model key:/settings#ai-provider",
      why: ["profile active", "the server says setup is missing"],
    },
    {
      name: "the server answered setup required and the keys read as none",
      input: { facts: facts({ phase: "setup", key: "none" }) },
      row: "D1",
      sentence: "Discover scores jobs with your model. Add a model key first.",
      action: "link:Add a model key:/settings#ai-provider",
      why: ["profile active", "no model key saved"],
    },
    {
      name: "ready, nothing searched yet",
      input: { facts: facts() },
      row: "D2",
      sentence: "Search company job boards and your search providers.",
      action: `submit:Search:${DISCOVER_FORM_ID}`,
      why: ["profile active", "model key set", "2 search providers connected"],
    },
    {
      name: "ready with one search provider",
      input: { facts: facts({ searchProviders: 1 }) },
      row: "D2",
      sentence: "Search company job boards and your search providers.",
      action: `submit:Search:${DISCOVER_FORM_ID}`,
      why: ["profile active", "model key set", "1 search provider connected"],
    },
    {
      name: "ready with no search provider: the registry still answers, and the count says zero",
      input: { facts: facts({ searchProviders: 0 }) },
      row: "D2",
      sentence: "Search company job boards and your search providers.",
      action: `submit:Search:${DISCOVER_FORM_ID}`,
      why: ["profile active", "model key set", "0 search providers connected"],
    },
    {
      name: "ready and the provider count is not known: the clause is left out",
      input: { facts: facts({ searchProviders: null }) },
      row: "D2",
      sentence: "Search company job boards and your search providers.",
      action: `submit:Search:${DISCOVER_FORM_ID}`,
      why: ["profile active", "model key set"],
    },
    {
      name: "searching",
      input: { facts: facts({ phase: "searching" }) },
      row: "D3",
      sentence: "Searching...",
      action: null,
      why: ["link checks can take about 20 seconds"],
    },
    {
      name: "results, scored and more, with per-provider counts",
      input: { facts: facts({ phase: "results", results: results() }) },
      row: "D4",
      sentence: "Track the jobs worth a draft.",
      action: null,
      why: ["30 scored", "42 more, unscored", "3 dropped -- no longer live", "registry 24", "serper 6"],
    },
    {
      name: "results, nothing unscored and nothing dropped",
      input: { facts: facts({ phase: "results", results: results({ more: 0, deadRemoved: 0, providers: [{ provider: "registry", count: 30 }] }) }) },
      row: "D4",
      sentence: "Track the jobs worth a draft.",
      action: null,
      why: ["30 scored", "registry 30"],
    },
    {
      name: "results with a provider that failed: the gap is in the why line",
      input: { facts: facts({ phase: "results", results: results({ failedProviders: ["brave"] }) }) },
      row: "D4",
      sentence: "Track the jobs worth a draft.",
      action: null,
      why: ["30 scored", "42 more, unscored", "3 dropped -- no longer live", "registry 24", "serper 6", "brave failed"],
    },
    {
      name: "results with two failed providers and notes that name none",
      input: { facts: facts({ phase: "results", results: results({ failedProviders: ["brave", "serper"], otherNotes: 2, deadRemoved: 0, more: 0 }) }) },
      row: "D4",
      sentence: "Track the jobs worth a draft.",
      action: null,
      why: ["30 scored", "registry 24", "serper 6", "brave failed", "serper failed", "2 search notes"],
    },
    {
      name: "only unscored rows came back",
      input: { facts: facts({ phase: "results", results: results({ scored: 0, more: 12, deadRemoved: 0, providers: [{ provider: "registry", count: 12 }] }) }) },
      row: "D4",
      sentence: "Track the jobs worth a draft.",
      action: null,
      why: ["0 scored", "12 more, unscored", "registry 12"],
    },
    {
      name: "results scored by the neutral fallback",
      input: { facts: facts({ phase: "results", results: results({ fallback: true }) }) },
      row: "D4b",
      sentence: "Your model's reply couldn't be read. These scores are placeholders.",
      action: `submit:Search again:${DISCOVER_FORM_ID}`,
      why: ["scoring fell back", "every bin shows Placeholder"],
    },
    {
      name: "the fallback and a provider that failed: the gap stays",
      input: { facts: facts({ phase: "results", results: results({ fallback: true, failedProviders: ["brave"] }) }) },
      row: "D4b",
      sentence: "Your model's reply couldn't be read. These scores are placeholders.",
      action: `submit:Search again:${DISCOVER_FORM_ID}`,
      why: ["scoring fell back", "every bin shows Placeholder", "brave failed"],
    },
    {
      name: "no results with filters set, Companies among them",
      input: { facts: facts({ phase: "results", results: results({ scored: 0, more: 0, deadRemoved: 0, providers: [], filtersSet: 3 }) }) },
      row: "D5",
      sentence: "No results. Widen your filters or clear Companies.",
      action: "button:Clear filters:clear-filters",
      why: ["0 results", "filters: 3 set"],
    },
    {
      name: "no results with filters set and no Companies filter",
      input: { facts: facts({ phase: "results", results: results({ scored: 0, more: 0, deadRemoved: 0, providers: [], filtersSet: 1, companiesSet: false }) }) },
      row: "D5",
      sentence: "No results. Widen your filters.",
      action: "button:Clear filters:clear-filters",
      why: ["0 results", "filters: 1 set"],
    },
    {
      name: "no results, with listings dropped as no longer live and a failed provider",
      input: {
        facts: facts({ phase: "results", results: results({ scored: 0, more: 0, deadRemoved: 4, providers: [], filtersSet: 2, failedProviders: ["serper"] }) }),
      },
      row: "D5",
      sentence: "No results. Widen your filters or clear Companies.",
      action: "button:Clear filters:clear-filters",
      why: ["0 results", "filters: 2 set", "4 dropped -- no longer live", "serper failed"],
    },
    {
      name: "no results and no filter set: nothing to clear, so unlit",
      input: { facts: facts({ phase: "results", results: results({ scored: 0, more: 0, deadRemoved: 0, providers: [], filtersSet: 0, companiesSet: false }) }) },
      row: "D5",
      sentence: "No results, and no filters were set.",
      action: null,
      why: ["0 results", "filters: 0 set"],
    },
  ];

  it.each(rows)("$name -> $row", ({ input, row, sentence, action, why }) => {
    const answer = ask(input);
    expect(answer.rowId).toBe(row);
    const decided = step(answer.outcome);
    expect(decided.sentence).toBe(sentence);
    expect(decided.why).toEqual(why);
    if (action === null) {
      expect(decided.action).toBeNull();
    } else {
      const [kind, label, ...rest] = action.split(":");
      const target = rest.join(":");
      const expected = kind === "link" ? { kind, label, to: target } : kind === "submit" ? { kind, label, form: target } : { kind, label, intent: target };
      expect(decided.action).toMatchObject(expected);
    }
  });

  it("is lit by one cyan action in D1, D2, D4b and D5 (with filters), and unlit in D3, D4 and a D5 with no filter", () => {
    const lit = rows.filter((entry) => step(ask(entry.input).outcome).action !== null).map((entry) => entry.row);
    expect(new Set(lit)).toEqual(new Set(["D1", "D2", "D4b", "D5"]));
    const unlit = rows.filter((entry) => step(ask(entry.input).outcome).action === null).map((entry) => entry.row);
    expect(new Set(unlit)).toEqual(new Set(["D3", "D4", "D5"]));
  });

  it("never says a word a trail never says, and never ends a sentence with an em dash", () => {
    for (const entry of rows) {
      const decided = step(ask(entry.input).outcome);
      expect(findForbiddenWord([decided.sentence, ...decided.why].join(" "))).toBeNull();
      expect(decided.sentence).not.toContain("—");
    }
  });

  it("keeps the Search of D2 and D4b a form submit, so Enter and the band's button are the same request", () => {
    for (const row of ["D2", "D4b"]) {
      const entry = rows.find((candidate) => candidate.row === row);
      expect(entry?.action?.startsWith("submit:")).toBe(true);
    }
  });
});

describe("what the global rows decide on this page", () => {
  it("waits, and lights nothing, while the profile or the credentials are still being read", () => {
    for (const input of [{ profile: { state: "loading" } as const }, { credentials: { state: "loading" } as const }, { profile: undefined }]) {
      expect(ask(input).outcome).toEqual({ status: "loading" });
    }
  });

  it("sends a person with no active profile to their profile before any row here (G2 wins over D1)", () => {
    const answer = ask({ profile: NO_PROFILE, facts: facts({ key: "none" }) });
    expect(answer.rowId).toBe("G2");
    expect(step(answer.outcome).action).toMatchObject({ kind: "link", to: "/profile" });
  });

  it("says the setup check failed when the credentials could not be read, with the call and the time", () => {
    const answer = ask({ credentials: { state: "failed", call: "GET /credentials", at: "09:14" } });
    expect(answer.rowId).toBe("G1");
    const decided = step(answer.outcome);
    expect(decided.tone).toBe("failure");
    expect(decided.why).toEqual(["GET /credentials failed", "09:14"]);
    expect(decided.action).toMatchObject({ kind: "button", label: "Try again", intent: "retry-setup" });
  });

  it("says a failed search is the page's own data failing: F1 names GET /discover and the time, and Try again re-runs it", () => {
    const answer = ask({ pageFailure: { what: "Couldn't finish the search", call: "GET /discover", at: "09:14", intent: "retry" } });
    expect(answer.rowId).toBe("F1");
    const decided = step(answer.outcome);
    expect(decided.sentence).toBe("Couldn't finish the search. You didn't do anything wrong, and nothing was lost.");
    expect(decided.tone).toBe("failure");
    expect(decided.why).toEqual(["GET /discover failed", "09:14"]);
    expect(decided.action).toMatchObject({ kind: "button", label: "Try again", intent: "retry" });
  });

  it("is signed-out safe: G0 decides before the page's rows", () => {
    const answer = nextStepWithRow({ page: "discover", signedIn: false, facts: facts() } as NextStepInput);
    expect(answer.rowId).toBe("G0");
  });
});

describe("facts the page rows cannot read", () => {
  it.each([
    ["nothing", undefined],
    ["a string", "idle"],
    ["an unknown phase", { phase: "thinking", key: "ready", searchProviders: 1, results: null }],
    ["an unknown key reading", { phase: "idle", key: "maybe", searchProviders: 1, results: null }],
    ["a negative provider count", { phase: "idle", key: "ready", searchProviders: -1, results: null }],
    ["a result with a fractional count", { phase: "results", key: "ready", searchProviders: 1, results: { ...results(), scored: 1.5 } }],
    ["a result whose providers are not counted", { phase: "results", key: "ready", searchProviders: 1, results: { ...results(), providers: [{ provider: "x" }] } }],
    ["a result whose failed providers are not text", { phase: "results", key: "ready", searchProviders: 1, results: { ...results(), failedProviders: [1] } }],
  ])("are refused (%s), and the band then shows nothing rather than a guess", (_name, value) => {
    expect(isDiscoverFacts(value)).toBe(false);
    expect(ask({ facts: value as DiscoverFacts }).outcome).toEqual({ status: "none" });
  });

  it("accepts the facts the page builds, including a finished search with nothing in it", () => {
    expect(isDiscoverFacts(facts())).toBe(true);
    expect(isDiscoverFacts(facts({ key: null, searchProviders: null }))).toBe(true);
    expect(isDiscoverFacts(facts({ phase: "results", results: results({ scored: 0, more: 0 }) }))).toBe(true);
  });

  it("shows no step for a finished search that carries no result (it cannot be described)", () => {
    expect(ask({ facts: facts({ phase: "results", results: null }) }).outcome).toEqual({ status: "none" });
  });
});

describe("a finished search is not replaced by an out-of-date setup step", () => {
  it("keeps D4 when the keys now read as missing but results are on screen", () => {
    const answer = ask({ facts: facts({ phase: "results", key: "none", results: results() }) });
    expect(answer.rowId).toBe("D4");
  });
});
