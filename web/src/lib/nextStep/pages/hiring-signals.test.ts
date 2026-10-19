import { describe, expect, it } from "vitest";
import { PAGES, nextStepWithRow } from "..";
import type { NextStepInput, NextStepOutcome, ProfileFacts, SetupCheck } from "..";
import { findForbiddenWord } from "../../trail";
import { HIRING_FORM_ID, SEARCH_KEYS_PATH, isHiringFacts, rules, type HiringFacts } from "./hiring-signals";

// The Hiring signals rows, as a table. The global rows run first: a failed status read and a search
// that failed in a way trying again can change are the global F1 (the page passes `pageFailure`),
// and the table pins those cases too.

const ACTIVE: SetupCheck<ProfileFacts> = { state: "ready", value: { active: true, importWaiting: false } };
const NO_PROFILE: SetupCheck<ProfileFacts> = { state: "ready", value: { active: false, importWaiting: false } };
const CREDS: SetupCheck<Record<string, never>> = { state: "ready", value: {} };

function facts(overrides: Partial<HiringFacts> = {}): HiringFacts {
  return {
    status: "enabled",
    switchedOff: false,
    searchKeys: 2,
    needsKey: false,
    searching: false,
    savedSearches: { state: "ready", count: 3 },
    results: null,
    resultsSaved: false,
    ...overrides,
  };
}

function ask(input: Partial<NextStepInput<HiringFacts>>) {
  return nextStepWithRow({ page: "hiring-signals", signedIn: true, profile: ACTIVE, credentials: CREDS, facts: facts(), ...input } as NextStepInput);
}

function step(outcome: NextStepOutcome) {
  if (outcome.status !== "ready") throw new Error(`expected a step, got ${outcome.status}`);
  return outcome.step;
}

describe("the page is registered", () => {
  it("is one of the pages the registry found, with its own row ids", () => {
    expect(PAGES.map((page) => page.page)).toContain("hiring-signals");
    expect(rules.rows.map((row) => row.id)).toEqual(["HS0", "HSL", "HS1", "HS2", "HS3"]);
  });
});

describe("the table", () => {
  const withResults = (extra: Partial<NonNullable<HiringFacts["results"]>> = {}) => ({ shown: 7, hidden: 5, cached: false, ...extra });
  const rows: Array<{
    name: string;
    input: Partial<NextStepInput<HiringFacts>>;
    row: string;
    sentence: string;
    action: string | null;
    why: string[];
  }> = [
    {
      name: "the status says the feature is off",
      input: { facts: facts({ status: "disabled" }) },
      row: "HS0",
      sentence: "Hiring signals is switched off on this server. Nothing else is affected.",
      action: null,
      why: ["switched off on this server"],
    },
    {
      name: "a request answered FEATURE_DISABLED while the page was open",
      input: { facts: facts({ switchedOff: true, results: withResults() }) },
      row: "HS0",
      sentence: "Hiring signals is switched off on this server. Nothing else is affected.",
      action: null,
      why: ["switched off on this server"],
    },
    {
      name: "no search key among the four providers",
      input: { facts: facts({ searchKeys: 0 }) },
      row: "HS1",
      sentence: "Hiring signals searches with your own search provider. Add a Brave, Serper, Firecrawl or You.com key first.",
      action: `link:Add a search key:${SEARCH_KEYS_PATH}`,
      why: ["no Brave, Serper, Firecrawl or You.com key"],
    },
    {
      name: "a search answered SETUP_REQUIRED although a key was read",
      input: { facts: facts({ needsKey: true }) },
      row: "HS1",
      sentence: "Hiring signals searches with your own search provider. Add a Brave, Serper, Firecrawl or You.com key first.",
      action: `link:Add a search key:${SEARCH_KEYS_PATH}`,
      why: ["no Brave, Serper, Firecrawl or You.com key"],
    },
    {
      name: "ready, no search yet",
      input: { facts: facts() },
      row: "HS2",
      sentence: "Search recent public hiring posts for a role.",
      action: `submit:Search:${HIRING_FORM_ID}`,
      why: ["2 search keys connected", "3 saved searches"],
    },
    {
      name: "ready with one key and one saved search",
      input: { facts: facts({ searchKeys: 1, savedSearches: { state: "ready", count: 1 } }) },
      row: "HS2",
      sentence: "Search recent public hiring posts for a role.",
      action: `submit:Search:${HIRING_FORM_ID}`,
      why: ["1 search key connected", "1 saved search"],
    },
    {
      name: "ready and the saved list failed: the saved-searches clause is left out",
      input: { facts: facts({ savedSearches: { state: "failed" } }) },
      row: "HS2",
      sentence: "Search recent public hiring posts for a role.",
      action: `submit:Search:${HIRING_FORM_ID}`,
      why: ["2 search keys connected"],
    },
    {
      name: "ready and neither fact is known",
      input: { facts: facts({ searchKeys: null, savedSearches: { state: "loading" } }) },
      row: "HS2",
      sentence: "Search recent public hiring posts for a role.",
      action: `submit:Search:${HIRING_FORM_ID}`,
      why: [],
    },
    {
      name: "a search is in flight",
      input: { facts: facts({ searching: true }) },
      row: "HS2",
      sentence: "Searching for recent hiring posts...",
      action: null,
      why: ["searching with your own search keys"],
    },
    {
      name: "results for what the boxes hold, not saved",
      input: { facts: facts({ results: withResults() }) },
      row: "HS3",
      sentence: "Save this search to run it again later. It won't run on its own.",
      action: "button:Save this search:save-search",
      why: ["7 shown", "5 hidden", "search not saved"],
    },
    {
      name: "results from a cached search, not saved",
      input: { facts: facts({ results: withResults({ cached: true }) }) },
      row: "HS3",
      sentence: "Save this search to run it again later. It won't run on its own.",
      action: "button:Save this search:save-search",
      why: ["7 shown", "5 hidden", "search not saved", "from a recent cached search"],
    },
    {
      name: "results and the search is already saved",
      input: { facts: facts({ results: withResults(), resultsSaved: true }) },
      row: "HS3",
      sentence: "Open the posts worth a closer look.",
      action: null,
      why: ["7 shown", "5 hidden", "search saved"],
    },
    {
      name: "a result with no posts is still a result: an empty search can be saved",
      input: { facts: facts({ results: withResults({ shown: 0, hidden: 0 }) }) },
      row: "HS3",
      sentence: "Save this search to run it again later. It won't run on its own.",
      action: "button:Save this search:save-search",
      why: ["0 shown", "0 hidden", "search not saved"],
    },
    {
      name: "results whose counts could not be read: the hidden clause is left out",
      input: { facts: facts({ results: withResults({ hidden: null }) }) },
      row: "HS3",
      sentence: "Save this search to run it again later. It won't run on its own.",
      action: "button:Save this search:save-search",
      why: ["7 shown", "search not saved"],
    },
    {
      name: "results and the saved searches could not be loaded: the page cannot tell, so unlit",
      input: { facts: facts({ results: withResults(), savedSearches: { state: "failed" } }) },
      row: "HS3",
      sentence: "Open the posts worth a closer look.",
      action: null,
      why: ["7 shown", "5 hidden", "saved searches couldn't load"],
    },
    {
      name: "the saved list failed and the results were cached",
      input: { facts: facts({ results: withResults({ cached: true }), savedSearches: { state: "failed" } }) },
      row: "HS3",
      sentence: "Open the posts worth a closer look.",
      action: null,
      why: ["7 shown", "5 hidden", "saved searches couldn't load", "from a recent cached search"],
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

  it("never says a word a trail never says", () => {
    for (const entry of rows) {
      const decided = step(ask(entry.input).outcome);
      expect(findForbiddenWord([decided.sentence, ...decided.why].join(" "))).toBeNull();
    }
  });

  it("waits, and lights nothing, while the status is still being asked", () => {
    expect(ask({ facts: facts({ status: "checking" }) }).outcome).toEqual({ status: "loading" });
    expect(ask({ facts: facts({ status: "checking", searchKeys: 0 }) }).outcome).toEqual({ status: "loading" });
  });

  it("waits, rather than guess, when results are on screen and the saved searches have not answered", () => {
    const answer = ask({ facts: facts({ results: withResults(), savedSearches: { state: "loading" } }) });
    expect(answer.outcome).toEqual({ status: "loading" });
    expect(answer.rowId).toBe("HS3");
  });

  it("puts the switched-off note before the key rows: a server with the feature off has nothing to search", () => {
    expect(ask({ facts: facts({ status: "disabled", searchKeys: 0 }) }).rowId).toBe("HS0");
  });

  it("puts the key row before the ready row: no key, no lit Search", () => {
    expect(ask({ facts: facts({ searchKeys: 0, results: null }) }).rowId).toBe("HS1");
  });

  it("is lit by one cyan action in HS2 (ready), in HS3 when the search is not saved, and is a link in HS1", () => {
    const lit = rows.filter((entry) => step(ask(entry.input).outcome).action !== null).map((entry) => entry.row);
    expect(new Set(lit)).toEqual(new Set(["HS1", "HS2", "HS3"]));
    expect(rows.filter((entry) => entry.row === "HS2" && entry.action === null).map((entry) => entry.name)).toEqual(["a search is in flight"]);
  });
});

describe("what the global rows decide on this page", () => {
  it("waits while the profile or the credentials are still being read", () => {
    for (const input of [{ profile: { state: "loading" } as const }, { credentials: { state: "loading" } as const }, { profile: undefined }]) {
      expect(ask(input).outcome).toEqual({ status: "loading" });
    }
  });

  it("sends a person with no active profile to set one up first (flagged: this page needs no profile, but the global rows come first)", () => {
    const answer = ask({ profile: NO_PROFILE });
    expect(answer.rowId).toBe("G2");
    expect(step(answer.outcome).action).toMatchObject({ kind: "link", to: "/profile" });
  });

  it("does not ask for a model key: this page never hands the global key row a reading", () => {
    // Credentials that held no model key at all would light G3 if the page had passed `modelKey`.
    expect(ask({ credentials: { state: "ready", value: {} } }).rowId).toBe("HS2");
  });

  it("says a failed status read is the page's own data failing (F1), names the call and the time, and Try again re-reads it", () => {
    const answer = ask({
      pageFailure: { what: "Couldn't check whether hiring signals are available", call: "GET /hiring-signals/status", at: "09:14", intent: "retry-status" },
      facts: facts({ status: "checking" }),
    });
    expect(answer.rowId).toBe("F1");
    const decided = step(answer.outcome);
    expect(decided.sentence).toBe("Couldn't check whether hiring signals are available. You didn't do anything wrong, and nothing was lost.");
    expect(decided.tone).toBe("failure");
    expect(decided.why).toEqual(["GET /hiring-signals/status failed", "09:14"]);
    expect(decided.action).toMatchObject({ kind: "button", label: "Try again", intent: "retry-status" });
  });

  it("says a search that failed retryably is F1 too, and Try again re-runs that same request", () => {
    const answer = ask({ pageFailure: { what: "Couldn't finish the search", call: "POST /hiring-signals/search", at: "09:20", intent: "retry-search" } });
    expect(answer.rowId).toBe("F1");
    const decided = step(answer.outcome);
    expect(decided.why).toEqual(["POST /hiring-signals/search failed", "09:20"]);
    expect(decided.action).toMatchObject({ kind: "button", label: "Try again", intent: "retry-search" });
  });

  it("says the setup check failed when the credentials could not be read", () => {
    const answer = ask({ credentials: { state: "failed", call: "GET /credentials", at: "09:14" } });
    expect(answer.rowId).toBe("G1");
    expect(step(answer.outcome).action).toMatchObject({ kind: "button", intent: "retry-setup" });
  });
});

describe("facts the page rows cannot read", () => {
  it.each([
    ["nothing", undefined],
    ["a string", "enabled"],
    ["an unknown status", { ...facts(), status: "unavailable" }],
    ["a missing flag", { ...facts(), switchedOff: undefined }],
    ["a negative key count", { ...facts(), searchKeys: -1 }],
    ["a saved list that is ready with no count", { ...facts(), savedSearches: { state: "ready" } }],
    ["a result with a fractional count", { ...facts(), results: { shown: 1.5, hidden: 0, cached: false } }],
    ["a result whose cache flag is not a boolean", { ...facts(), results: { shown: 1, hidden: 0, cached: "yes" } }],
  ])("are refused (%s), and the band then shows nothing rather than a guess", (_name, value) => {
    expect(isHiringFacts(value)).toBe(false);
    expect(ask({ facts: value as HiringFacts }).outcome).toEqual({ status: "none" });
  });

  it("accepts every shape the page builds", () => {
    expect(isHiringFacts(facts())).toBe(true);
    expect(isHiringFacts(facts({ searchKeys: null, savedSearches: { state: "loading" } }))).toBe(true);
    expect(isHiringFacts(facts({ results: { shown: 0, hidden: null, cached: true } }))).toBe(true);
  });
});

describe("the key link", () => {
  it("goes to the search keys of Settings and is built from the Settings path, never spelled out", () => {
    expect(SEARCH_KEYS_PATH).toBe("/settings#search-keys");
  });
});
