import { describe, expect, it } from "vitest";
import { INITIAL_FORM } from "./hiringSignalsTab";
import { hiddenOf, hiringFactsOf, needsSearchKey, pageFailureOf, resultsMatchBoxes, retryableSearchFailure } from "./hiringSignalsPage";
import { initialTabState, type TabState } from "./hiringSignalsTabModel";
import type { TabCounts, TabSearchOutcome } from "./hiringSignalsTabTypes";

// What the Hiring signals page tells the band, from the shared status, the model's state and the
// credentials. Pure, so each is a table.

const REQUEST = { query: "software engineer", location: "Pune", freshness: "3days", locale: null } as const;

function counts(overrides: Partial<TabCounts> = {}): TabCounts {
  return { raw_hits: 10, rejected: 1, duplicates: 1, role_mismatch_hidden: 2, echoes_hidden: 0, job_seekers_hidden: 1, too_old_hidden: 0, shown: 5, ...overrides };
}

function outcome(shown = 5, extra: { counts?: TabCounts | null; cached?: boolean } = {}): TabSearchOutcome {
  return {
    response: {
      provider: "firecrawl",
      cached: extra.cached ?? false,
      freshness: "3days",
      locale: null,
      query_label: "x",
      signals: Array.from({ length: shown }, (_, index) => ({ activity_id: `70000000000000000${index}` })) as never,
      counts: extra.counts === undefined ? counts() : extra.counts,
    },
    unreadable: 0,
  };
}

function withResults(extra: Partial<TabState> = {}, results: TabSearchOutcome = outcome()): TabState {
  return initialTabState({
    savesStatus: "ready",
    searchesStatus: "ready",
    form: { ...INITIAL_FORM, query: "software engineer", location: "Pune" },
    search: { kind: "ready", outcome: results, request: REQUEST },
    ...extra,
  });
}

describe("hiddenOf", () => {
  it("sums every category the server's counts name as hidden", () => {
    expect(hiddenOf(counts())).toBe(5);
  });

  it("is null, not 0, when the counts could not be read: no number is made up", () => {
    expect(hiddenOf(null)).toBeNull();
  });
});

describe("results for what the boxes hold", () => {
  it("are the results of the search the boxes still describe", () => {
    expect(resultsMatchBoxes(withResults())).toBe(true);
  });

  it("stop being the boxes' results when the role, the location, the window or the wording changed after", () => {
    const form = { ...INITIAL_FORM, query: "software engineer", location: "Pune" };
    expect(resultsMatchBoxes(withResults({ form: { ...form, query: "designer" } }))).toBe(false);
    expect(resultsMatchBoxes(withResults({ form: { ...form, location: "Delhi" } }))).toBe(false);
    expect(resultsMatchBoxes(withResults({ form: { ...form, freshness: "week" } }))).toBe(false);
    expect(resultsMatchBoxes(withResults({ form: { ...form, locale: "india" } }))).toBe(false);
  });

  it("compare the way the model compares: whitespace collapsed, a blank location as none", () => {
    const form = { ...INITIAL_FORM, query: "  software   engineer ", location: " Pune " };
    expect(resultsMatchBoxes(withResults({ form }))).toBe(true);
    const state = withResults({ form: { ...INITIAL_FORM, query: "software engineer", location: "" }, search: { kind: "ready", outcome: outcome(), request: { ...REQUEST, location: null } } });
    expect(resultsMatchBoxes(state)).toBe(true);
  });

  it("are nothing when no search has answered, or the boxes are invalid", () => {
    expect(resultsMatchBoxes(initialTabState())).toBe(false);
    expect(resultsMatchBoxes(withResults({ form: { ...INITIAL_FORM, query: "   " } }))).toBe(false);
  });
});

describe("hiringFactsOf", () => {
  const facts = (state: TabState, status: "enabled" | "disabled" | "checking" | "unavailable" = "enabled", searchKeys: number | null = 2) =>
    hiringFactsOf({ status: { kind: status }, state, searchKeys });

  it("carries the status, with a status that could not be read as 'checking': that is the band's F1, not a row's", () => {
    expect(facts(initialTabState(), "enabled").status).toBe("enabled");
    expect(facts(initialTabState(), "disabled").status).toBe("disabled");
    expect(facts(initialTabState(), "checking").status).toBe("checking");
    expect(facts(initialTabState(), "unavailable").status).toBe("checking");
  });

  it("counts the posts on screen, the hidden ones and the cached flag, for the boxes' own results", () => {
    expect(facts(withResults()).results).toEqual({ shown: 5, hidden: 5, cached: false });
    expect(facts(withResults({}, outcome(2, { cached: true }))).results).toEqual({ shown: 2, hidden: 5, cached: true });
    expect(facts(withResults({}, outcome(3, { counts: null }))).results).toEqual({ shown: 3, hidden: null, cached: false });
  });

  it("has no results once the boxes were changed, and says whether the search is saved only for the boxes' own results", () => {
    const saved = [{ id: "s", query: "Software Engineer", location: "pune", created_at: "" }];
    expect(facts(withResults({ searches: saved })).resultsSaved).toBe(true);
    expect(facts(withResults({ searches: [] })).resultsSaved).toBe(false);
    const changed = withResults({ searches: saved, form: { ...INITIAL_FORM, query: "designer" } });
    expect(facts(changed).results).toBeNull();
    expect(facts(changed).resultsSaved).toBe(false);
  });

  it("reads the saved searches as loading, failed or a count", () => {
    expect(facts(initialTabState()).savedSearches).toEqual({ state: "loading" });
    expect(facts(initialTabState({ searchesStatus: "error" })).savedSearches).toEqual({ state: "failed" });
    expect(facts(initialTabState({ searchesStatus: "ready", searches: [{ id: "a", query: "q", location: null, created_at: "" }] })).savedSearches).toEqual({ state: "ready", count: 1 });
  });

  it("says a search is in flight while one is, and a feature switched off when a request said so", () => {
    expect(facts(initialTabState({ search: { kind: "loading", request: REQUEST } })).searching).toBe(true);
    expect(facts(initialTabState()).searching).toBe(false);
    expect(facts(initialTabState({ disabled: true })).switchedOff).toBe(true);
  });

  it("passes the search key count through, null when it was not read", () => {
    expect(facts(initialTabState(), "enabled", 0).searchKeys).toBe(0);
    expect(facts(initialTabState(), "enabled", null).searchKeys).toBeNull();
  });
});

describe("a failed search", () => {
  const failed = (failure: Extract<TabState["search"], { kind: "failed" }>["failure"]) => initialTabState({ search: { kind: "failed", failure, request: REQUEST } });

  it("is retryable only for a plain error the server marked so", () => {
    expect(retryableSearchFailure(failed({ kind: "error", message: "x", retryable: true }))).toBe(true);
    expect(retryableSearchFailure(failed({ kind: "error", message: "x", retryable: false }))).toBe(false);
    expect(retryableSearchFailure(failed({ kind: "not_found", message: "x" }))).toBe(false);
    expect(retryableSearchFailure(failed({ kind: "setup_required", message: "x" }))).toBe(false);
    expect(retryableSearchFailure(initialTabState())).toBe(false);
  });

  it("is a need for a search key only for setup-required", () => {
    expect(needsSearchKey(failed({ kind: "setup_required", message: "x" }))).toBe(true);
    expect(needsSearchKey(failed({ kind: "error", message: "x", retryable: true }))).toBe(false);
  });
});

describe("pageFailureOf", () => {
  const at = { statusFailedAt: "09:14", searchFailedAt: "09:20" };
  const failedSearch = (retryable: boolean) => initialTabState({ search: { kind: "failed", failure: { kind: "error", message: "x", retryable }, request: REQUEST } });

  it("names the status call and its time when the status could not be read", () => {
    expect(pageFailureOf({ status: { kind: "unavailable" }, state: initialTabState(), ...at })).toEqual({
      what: "Couldn't check whether hiring signals are available",
      call: "GET /hiring-signals/status",
      at: "09:14",
      intent: "retry-status",
    });
  });

  it("names the search call and its time for a search that can be retried", () => {
    expect(pageFailureOf({ status: { kind: "enabled" }, state: failedSearch(true), ...at })).toEqual({
      what: "Couldn't finish the search",
      call: "POST /hiring-signals/search",
      at: "09:20",
      intent: "retry-search",
    });
  });

  it("is nothing for a search that cannot be retried: it keeps its message in the results block", () => {
    expect(pageFailureOf({ status: { kind: "enabled" }, state: failedSearch(false), ...at })).toBeNull();
  });

  it("is nothing when the feature is off, checking, or the search has not failed", () => {
    expect(pageFailureOf({ status: { kind: "disabled" }, state: failedSearch(true), ...at })).toBeNull();
    expect(pageFailureOf({ status: { kind: "checking" }, state: failedSearch(true), ...at })).toBeNull();
    expect(pageFailureOf({ status: { kind: "enabled" }, state: initialTabState(), ...at })).toBeNull();
    expect(pageFailureOf({ status: { kind: "enabled" }, state: { ...failedSearch(true), disabled: true }, ...at })).toBeNull();
  });
});
