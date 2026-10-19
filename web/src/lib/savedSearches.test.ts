import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import type { SavedSearch } from "./discoverTypes";
import { buildTrail } from "./trail";
import {
  deleteFocusKey,
  focusKeysAfterRemoving,
  SAVED_SEARCHES_NOTE,
  SAVED_SEARCHES_PATH,
  deleteSavedSearch,
  initialSavedSearchesState,
  loadSavedSearches,
  parseSavedSearch,
  parseSavedSearches,
  createSavedSearch,
  isChecked,
  savedSearchActionLabel,
  savedSearchTrail,
  savedSearchesReducer,
  setSavedSearchActive,
  type SavedSearchesFetcher,
  type SavedSearchesState,
} from "./savedSearches";

// Pausing and deleting saved searches from a page the tester-programme gate leaves open. The page
// that draws it is components/SavedSearchCard*.tsx; this is the logic.

function search(overrides: Partial<SavedSearch> = {}): SavedSearch {
  return {
    id: "s-1",
    query: "data analyst",
    location: "Jersey City",
    companies: [],
    remote_only: false,
    is_active: true,
    created_at: "2026-10-01T00:00:00+00:00",
    ...overrides,
  };
}

const ROW = (id: string, extra: Record<string, unknown> = {}) => ({
  id,
  query: "data analyst",
  location: null,
  companies: ["Acme"],
  remote_only: true,
  is_active: true,
  created_at: "2026-10-01T00:00:00+00:00",
  ...extra,
});

describe("parseSavedSearch", () => {
  it("reads a search as the API gives it", () => {
    expect(parseSavedSearch(ROW("a"))).toEqual({
      id: "a",
      query: "data analyst",
      location: null,
      companies: ["Acme"],
      remote_only: true,
      is_active: true,
      created_at: "2026-10-01T00:00:00+00:00",
      last_matched_at: null,
    });
  });

  it("reads when the matcher last ran, and nothing from a value that is not a time", () => {
    expect(parseSavedSearch(ROW("a", { last_matched_at: "2026-10-02T06:00:00+00:00" }))?.last_matched_at).toBe("2026-10-02T06:00:00+00:00");
    expect(parseSavedSearch(ROW("a", { last_matched_at: 12 }))?.last_matched_at).toBeNull();
    expect(parseSavedSearch(ROW("a", { last_matched_at: null }))?.last_matched_at).toBeNull();
  });

  it("keeps a search whose other fields it cannot read: a search that is running must stay on the list", () => {
    expect(parseSavedSearch({ id: "a", is_active: false, query: 7, location: 3, companies: ["x", 4], remote_only: "yes" })).toEqual({
      id: "a",
      query: "",
      location: null,
      companies: ["x"],
      remote_only: false,
      is_active: false,
      created_at: "",
      last_matched_at: null,
    });
  });

  it.each([
    ["null", null],
    ["a string", "s"],
    ["an array", [ROW("a")]],
    ["no id", { is_active: true }],
    ["an empty id", { id: "", is_active: true }],
    ["a numeric id", { id: 4, is_active: true }],
    ["no active flag", { id: "a" }],
    ["a string active flag", { id: "a", is_active: "true" }],
  ])("does not guess from %s", (_label, body) => {
    expect(parseSavedSearch(body)).toBeNull();
  });
});

describe("parseSavedSearches", () => {
  it("reads a list, an empty one included", () => {
    expect(parseSavedSearches([])).toEqual([]);
    expect(parseSavedSearches([ROW("a"), ROW("b", { is_active: false })])?.map((s) => [s.id, s.is_active])).toEqual([
      ["a", true],
      ["b", false],
    ]);
  });

  it("reads nothing as a shorter list than the server has: one entry it cannot read makes the whole answer unknown", () => {
    expect(parseSavedSearches([ROW("a"), { nope: true }])).toBeNull();
    expect(parseSavedSearches({ searches: [] })).toBeNull();
    expect(parseSavedSearches(null)).toBeNull();
  });
});

describe("savedSearchesReducer", () => {
  const ready = (searches: SavedSearch[], extra: Partial<Extract<SavedSearchesState, { kind: "ready" }>> = {}): SavedSearchesState => ({
    kind: "ready",
    searches,
    busyId: null,
    error: null,
    ...extra,
  });

  it("goes loading, ready; or loading, failed and back to loading on a retry", () => {
    let state = initialSavedSearchesState;
    expect(state).toEqual({ kind: "loading" });
    state = savedSearchesReducer(state, { type: "load_failed", message: "down" });
    expect(state).toEqual({ kind: "failed", message: "down" });
    state = savedSearchesReducer(state, { type: "load_started" });
    expect(state).toEqual({ kind: "loading" });
    state = savedSearchesReducer(state, { type: "loaded", searches: [search()] });
    expect(state).toEqual(ready([search()]));
  });

  it("keeps what is known through a refresh that starts or fails", () => {
    const known = ready([search()]);
    expect(savedSearchesReducer(known, { type: "load_started" })).toBe(known);
    expect(savedSearchesReducer(known, { type: "load_failed", message: "down" })).toBe(known);
  });

  it("marks the search an action is out for, clearing the last error", () => {
    const state = savedSearchesReducer(ready([search()], { error: "old" }), { type: "action_started", id: "s-1" });
    expect(state).toEqual(ready([search()], { busyId: "s-1", error: null }));
  });

  it("swaps in the search the server answered with, and takes one out when it was deleted", () => {
    const two = ready([search({ id: "a" }), search({ id: "b" })], { busyId: "a" });
    const updated = savedSearchesReducer(two, { type: "updated", search: search({ id: "a", is_active: false }) });
    expect(updated).toEqual(ready([search({ id: "a", is_active: false }), search({ id: "b" })]));
    const removed = savedSearchesReducer(two, { type: "removed", id: "a" });
    expect(removed).toEqual(ready([search({ id: "b" })]));
  });

  it("keeps the list and says why when an action fails", () => {
    const state = savedSearchesReducer(ready([search()], { busyId: "s-1" }), { type: "action_failed", message: "no" });
    expect(state).toEqual(ready([search()], { error: "no" }));
  });

  it("ignores an action's result while there is no known list to apply it to", () => {
    const loading: SavedSearchesState = { kind: "loading" };
    for (const event of [
      { type: "action_started", id: "a" },
      { type: "updated", search: search() },
      { type: "removed", id: "a" },
      { type: "action_failed", message: "x" },
    ] as const) {
      expect(savedSearchesReducer(loading, event)).toBe(loading);
    }
  });
});

function recorder(answer: unknown) {
  const calls: { path: string; init?: RequestInit }[] = [];
  const fetcher: SavedSearchesFetcher = async <T,>(path: string, init?: RequestInit) => {
    calls.push({ path, init });
    if (answer instanceof Error) throw answer;
    return answer as T;
  };
  return { calls, fetcher };
}

describe("loadSavedSearches", () => {
  it("asks the list route and returns the searches", async () => {
    const { calls, fetcher } = recorder([ROW("a")]);
    const event = await loadSavedSearches(fetcher);
    expect(event.type === "loaded" && event.searches.map((s) => s.id)).toEqual(["a"]);
    expect(calls).toEqual([{ path: SAVED_SEARCHES_PATH, init: undefined }]);
  });

  it("fails, with the server's words or the shared wording, never reading a failed ask as 'no saved searches'", async () => {
    expect(await loadSavedSearches(recorder(new ApiError(503, "The service is down.", "PROVIDER_UNAVAILABLE", true)).fetcher)).toEqual({
      type: "load_failed",
      message: "The service is down.",
    });
    const limited = await loadSavedSearches(recorder(new ApiError(429, "x", "RATE_LIMITED", true, 700)).fetcher);
    expect(limited.type === "load_failed" && limited.message).toContain("too often");
    const odd = await loadSavedSearches(async () => {
      throw "nope";
    });
    expect(odd).toEqual({ type: "load_failed", message: "We could not load your saved searches." });
    const unreadable = await loadSavedSearches(recorder({ not: "a list" }).fetcher);
    expect(unreadable.type).toBe("load_failed");
  });
});

describe("setSavedSearchActive", () => {
  it("patches the one search with the new flag and returns what the server says it is now", async () => {
    const { calls, fetcher } = recorder(ROW("a", { is_active: false }));
    const event = await setSavedSearchActive(fetcher, "a", false);
    expect(event.type === "updated" && event.search.is_active).toBe(false);
    expect(calls).toHaveLength(1);
    expect(calls[0].path).toBe("/saved-searches/a");
    expect(calls[0].init?.method).toBe("PATCH");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ is_active: false });
  });

  it("puts the id in the path safely", async () => {
    const { calls, fetcher } = recorder(ROW("a/../b"));
    await setSavedSearchActive(fetcher, "a/../b", true);
    expect(calls[0].path).toBe("/saved-searches/a%2F..%2Fb");
  });

  it("fails with its reason, and does not announce a success it could not read", async () => {
    expect(await setSavedSearchActive(recorder(new ApiError(404, "no saved search found", "NOT_FOUND")).fetcher, "a", true)).toEqual({
      type: "action_failed",
      message: "no saved search found",
    });
    const unreadable = await setSavedSearchActive(recorder({ ok: true }).fetcher, "a", true);
    expect(unreadable.type).toBe("action_failed");
    const odd = await setSavedSearchActive(async () => {
      throw 5;
    }, "a", true);
    expect(odd).toEqual({ type: "action_failed", message: "We could not update that saved search." });
  });
});

describe("deleteSavedSearch", () => {
  it("sends a DELETE for the one search and says it is gone", async () => {
    const { calls, fetcher } = recorder(undefined);
    expect(await deleteSavedSearch(fetcher, "a")).toEqual({ type: "removed", id: "a" });
    expect(calls).toEqual([{ path: "/saved-searches/a", init: { method: "DELETE" } }]);
  });

  it("fails with its reason, and keeps the search on the list", async () => {
    expect(await deleteSavedSearch(recorder(new ApiError(404, "no saved search found", "NOT_FOUND")).fetcher, "a")).toEqual({
      type: "action_failed",
      message: "no saved search found",
    });
    const odd = await deleteSavedSearch(async () => {
      throw 5;
    }, "a");
    expect(odd).toEqual({ type: "action_failed", message: "We could not delete that saved search." });
  });
});

describe("the words", () => {
  it("names each button for its search, so a list of 'Pause' buttons means something to a screen reader", () => {
    expect(savedSearchActionLabel("pause", search())).toBe('Pause the saved search "data analyst" in Jersey City');
    expect(savedSearchActionLabel("resume", search({ is_active: false }))).toBe('Resume the saved search "data analyst" in Jersey City');
    expect(savedSearchActionLabel("delete", search({ query: "", location: null }))).toBe("Delete the saved search Any job");
  });

  it("says what a saved search does in the background, which the Tester Agreement relies on", () => {
    expect(SAVED_SEARCHES_NOTE).toBe(
      "Active saved searches are checked every few hours in the background, using your AI key to score what they find. Pause one to stop the checks, or delete it.",
    );
  });
});

describe("a search saved from Discover", () => {
  const ready = (searches: SavedSearch[]): SavedSearchesState => ({ kind: "ready", searches, busyId: null, error: null });

  it("goes on top of the list, newest first", () => {
    const state = savedSearchesReducer(ready([search({ id: "a" })]), { type: "created", search: search({ id: "b" }) });
    expect(state).toEqual(ready([search({ id: "b" }), search({ id: "a" })]));
  });

  it("replaces a search already listed instead of doubling it", () => {
    const state = savedSearchesReducer(ready([search({ id: "a" })]), { type: "created", search: search({ id: "a", query: "newer" }) });
    expect(state).toEqual(ready([search({ id: "a", query: "newer" })]));
  });

  it("is ignored while the list is loading or failed: a reload shows it", () => {
    const loading: SavedSearchesState = { kind: "loading" };
    const failed: SavedSearchesState = { kind: "failed", message: "x" };
    expect(savedSearchesReducer(loading, { type: "created", search: search() })).toBe(loading);
    expect(savedSearchesReducer(failed, { type: "created", search: search() })).toBe(failed);
  });
});

describe("createSavedSearch", () => {
  const body = { query: "data analyst", location: null, companies: ["Acme"], remote_only: false };

  it("posts the filters and returns the search the server made", async () => {
    const { calls, fetcher } = recorder(ROW("new"));
    const result = await createSavedSearch(fetcher, body);
    expect(result.ok && result.search.id).toBe("new");
    expect(calls).toHaveLength(1);
    expect(calls[0].path).toBe("/saved-searches");
    expect(calls[0].init?.method).toBe("POST");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual(body);
  });

  it("fails with the server's words, or the shared ones, and never claims a save it could not read", async () => {
    expect(await createSavedSearch(recorder(new ApiError(422, "Too many saved searches.", "INVALID_INPUT")).fetcher, body)).toEqual({
      ok: false,
      message: "Too many saved searches.",
    });
    expect(await createSavedSearch(async () => {
      throw 5;
    }, body)).toEqual({ ok: false, message: "We could not save that search." });
    const unreadable = await createSavedSearch(recorder({ nothing: true }).fetcher, body);
    expect(unreadable.ok).toBe(false);
  });
});

describe("when a saved search was last checked", () => {
  const NOW = new Date("2026-09-22T12:00:00Z");
  const text = (value: SavedSearch) => buildTrail(savedSearchTrail(value, NOW, "UTC"))?.text;

  it("counts a check only when it is later than the search's own creation: the matcher may stamp a search as it is saved", () => {
    expect(isChecked(search({ created_at: "2026-09-22T05:00:00Z", last_matched_at: "2026-09-22T06:00:00Z" }))).toBe(true);
    expect(isChecked(search({ created_at: "2026-09-22T06:00:00Z", last_matched_at: "2026-09-22T06:00:00Z" }))).toBe(false);
    expect(isChecked(search({ created_at: "2026-09-22T07:00:00Z", last_matched_at: "2026-09-22T06:00:00Z" }))).toBe(false);
    expect(isChecked(search({ last_matched_at: null }))).toBe(false);
    expect(isChecked(search({ last_matched_at: undefined }))).toBe(false);
  });

  it("reads an unreadable time on either side as not checked: no time is guessed", () => {
    expect(isChecked(search({ created_at: "", last_matched_at: "2026-09-22T06:00:00Z" }))).toBe(false);
    expect(isChecked(search({ created_at: "2026-09-22T05:00:00Z", last_matched_at: "garbage" }))).toBe(false);
  });

  it("says 'Checked 06:00' for a check today, and the date for a check on another day", () => {
    expect(text(search({ created_at: "2026-09-01T00:00:00Z", last_matched_at: "2026-09-22T06:00:00Z" }))).toBe("Checked 06:00");
    expect(text(search({ created_at: "2026-09-01T00:00:00Z", last_matched_at: "2026-09-20T06:00:00Z" }))).toBe("Checked Sep 20");
  });

  it("says 'Not checked yet' when no check is on record, with the muted code glyph", () => {
    const unchecked = search({ created_at: "2026-09-22T06:00:00Z", last_matched_at: "2026-09-22T06:00:00Z" });
    expect(text(unchecked)).toBe("Not checked yet");
    expect(buildTrail(savedSearchTrail(unchecked, NOW, "UTC"))?.glyphs).toEqual(["code"]);
  });

  it("adds Paused after either, when the search is paused", () => {
    expect(text(search({ created_at: "2026-09-01T00:00:00Z", last_matched_at: "2026-09-22T06:00:00Z", is_active: false }))).toBe("Checked 06:00 · Paused");
    expect(text(search({ last_matched_at: null, is_active: false }))).toBe("Not checked yet · Paused");
  });

  it("claims no model and no key: the matcher stamps a skipped run the same way", () => {
    for (const value of [search({ last_matched_at: "2026-10-02T06:00:00Z" }), search({ last_matched_at: null })]) {
      const built = buildTrail(savedSearchTrail(value, NOW, "UTC"));
      expect(built?.glyphs ?? []).not.toContain("model");
      expect(built?.text.toLowerCase()).not.toContain("key");
      expect(built?.text.toLowerCase()).not.toContain("model");
    }
  });
});


describe("where focus goes when a row is deleted", () => {
  const rows = [search({ id: "a" }), search({ id: "b" }), search({ id: "c" })];

  it("is the next row's Delete button, then the previous row's", () => {
    expect(focusKeysAfterRemoving(rows, "a")).toEqual([deleteFocusKey("b")]);
    expect(focusKeysAfterRemoving(rows, "b")).toEqual([deleteFocusKey("c"), deleteFocusKey("a")]);
    expect(focusKeysAfterRemoving(rows, "c")).toEqual([deleteFocusKey("b")]);
  });

  it("is nothing for the only row, and for a row that is not listed", () => {
    expect(focusKeysAfterRemoving([search({ id: "a" })], "a")).toEqual([]);
    expect(focusKeysAfterRemoving(rows, "zzz")).toEqual([]);
  });
});
