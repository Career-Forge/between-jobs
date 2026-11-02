import { describe, expect, it } from "vitest";
import type { SavedSearch } from "./discoverTypes";
import { savedSearchesSummary } from "./discoverSaved";
import type { SavedSearchesState } from "./savedSearches";

// The closed saved-searches row: the hint it shows and whether it opens by itself.

function search(id: string, overrides: Partial<SavedSearch> = {}): SavedSearch {
  return { id, query: "data engineer", location: null, companies: [], remote_only: false, is_active: true, created_at: "2026-09-20T08:00:00Z", last_matched_at: null, ...overrides };
}

const CHECKED = { last_matched_at: "2026-09-21T06:00:00Z" } as const;

function ready(searches: SavedSearch[], extra: Partial<Extract<SavedSearchesState, { kind: "ready" }>> = {}): SavedSearchesState {
  return { kind: "ready", searches, busyId: null, error: null, ...extra };
}

const summary = (state: SavedSearchesState, createError: string | null = null, saving = false) => savedSearchesSummary({ state, createError, saving });

describe("no row at all", () => {
  it("draws nothing while the list is loading, and nothing for a list with no searches", () => {
    expect(summary({ kind: "loading" })).toBeNull();
    expect(summary(ready([]))).toBeNull();
  });

  it("draws nothing for a save in flight when there is no search to show yet", () => {
    expect(summary(ready([]), null, true)).toBeNull();
    expect(summary({ kind: "loading" }, null, true)).toBeNull();
  });
});

describe("a failure needs a person's eye", () => {
  it("says a failed read could not be read, and asks to be seen", () => {
    expect(summary({ kind: "failed", message: "We could not load your saved searches." })).toEqual({ count: 0, hint: "Couldn't be read", attention: true });
  });

  it("says a refused save, in every state of the list", () => {
    const hint = { hint: "A search couldn't be saved", attention: true };
    expect(summary({ kind: "loading" }, "We could not save that search.")).toEqual({ count: 0, ...hint });
    expect(summary(ready([]), "We could not save that search.")).toEqual({ count: 0, ...hint });
    expect(summary(ready([search("a"), search("b")]), "We could not save that search.")).toEqual({ count: 2, ...hint });
  });

  it("says an update, pause or delete that did not go through", () => {
    expect(summary(ready([search("a")], { error: "We could not update that saved search." }))).toEqual({ count: 1, hint: "An update didn't go through", attention: true });
  });

  it("reads a failed read before a refused save, which before a failed update", () => {
    expect(summary({ kind: "failed", message: "x" }, "no")).toMatchObject({ hint: "Couldn't be read" });
    expect(summary(ready([search("a")], { error: "bad", busyId: "a" }), "no")).toMatchObject({ hint: "A search couldn't be saved" });
    expect(summary(ready([search("a")], { error: "bad", busyId: "a" }), null, true)).toMatchObject({ hint: "An update didn't go through" });
  });
});

describe("work under way is a hint, not a reason to open", () => {
  it("says Updating while a pause, resume or delete is out", () => {
    expect(summary(ready([search("a"), search("b")], { busyId: "a" }))).toEqual({ count: 2, hint: "Updating...", attention: false });
  });

  it("says Saving while a save is out and there are searches to show", () => {
    expect(summary(ready([search("a")]), null, true)).toEqual({ count: 1, hint: "Saving...", attention: false });
  });

  it("puts an update before a save in flight", () => {
    expect(summary(ready([search("a")], { busyId: "a" }), null, true)).toMatchObject({ hint: "Updating..." });
  });
});

describe("a save that has just landed", () => {
  const saved = (state: SavedSearchesState) => savedSearchesSummary({ state, createError: null, saving: false, justSaved: true });

  it("leads the hint with the news, over the counts, so a closed row says it", () => {
    expect(saved(ready([search("a"), search("b")]))).toEqual({ count: 2, hint: "Search saved -- 2 searches, not checked yet", attention: false });
    expect(saved(ready([search("a", CHECKED), search("b")]))).toMatchObject({ hint: "Search saved -- 2 searches, checked in the background" });
    expect(saved(ready([search("a", { is_active: false })]))).toMatchObject({ hint: "Search saved -- 1 search, paused" });
    expect(saved(ready([search("a"), search("b", { is_active: false })]))).toMatchObject({ hint: "Search saved -- 2 searches, 1 active" });
  });

  it("is said by nothing that is not a healthy list: a failure, an update under way or no searches at all", () => {
    expect(savedSearchesSummary({ state: ready([search("a")]), createError: "no", saving: false, justSaved: true })).toMatchObject({ hint: "A search couldn't be saved" });
    expect(saved({ kind: "failed", message: "x" })).toMatchObject({ hint: "Couldn't be read" });
    expect(saved(ready([search("a")], { busyId: "a" }))).toMatchObject({ hint: "Updating..." });
    expect(saved(ready([search("a")], { error: "bad" }))).toMatchObject({ hint: "An update didn't go through" });
    expect(saved(ready([]))).toBeNull();
    expect(saved({ kind: "loading" })).toBeNull();
  });

  it("is not said unless asked for, and is said in plain words with no em dash", () => {
    expect(summary(ready([search("a")]))).toMatchObject({ hint: "1 search, not checked yet" });
    expect(saved(ready([search("a")]))?.hint).not.toMatch(/\u2014|verified/i);
  });
});

describe("the counts", () => {
  it("says a search that is running has not been checked yet until the background matcher has run over one", () => {
    expect(summary(ready([search("a")]))).toEqual({ count: 1, hint: "1 search, not checked yet", attention: false });
    expect(summary(ready([search("a"), search("b")]))).toMatchObject({ hint: "2 searches, not checked yet" });
  });

  it("does not call a search checked when the matcher stamped it at the moment it was saved", () => {
    expect(summary(ready([search("a", { last_matched_at: "2026-09-20T08:00:00Z" })]))).toMatchObject({ hint: "1 search, not checked yet" });
    expect(summary(ready([search("a", { last_matched_at: "garbage" })]))).toMatchObject({ hint: "1 search, not checked yet" });
  });

  it("says checked in the background once any running search has been", () => {
    expect(summary(ready([search("a", CHECKED)]))).toEqual({ count: 1, hint: "1 search, checked in the background", attention: false });
    expect(summary(ready([search("a"), search("b", CHECKED), search("c")]))).toMatchObject({ count: 3, hint: "3 searches, checked in the background" });
  });

  it("says a single paused search is paused", () => {
    expect(summary(ready([search("a", { is_active: false })]))).toEqual({ count: 1, hint: "1 search, paused", attention: false });
  });

  it("says when every search is paused", () => {
    expect(summary(ready([search("a", { is_active: false }), search("b", { is_active: false })]))).toMatchObject({ count: 2, hint: "2 searches, all paused" });
  });

  it("says how many are active when some are paused", () => {
    expect(summary(ready([search("a"), search("b", { is_active: false }), search("c")]))).toMatchObject({ count: 3, hint: "3 searches, 2 active" });
    expect(summary(ready([search("a"), search("b", { is_active: false })]))).toMatchObject({ count: 2, hint: "2 searches, 1 active" });
  });

  it("never asks to be opened for a healthy list", () => {
    for (const state of [ready([search("a")]), ready([search("a", CHECKED), search("b", { is_active: false })]), ready([search("a", { is_active: false })])]) {
      expect(summary(state)?.attention).toBe(false);
    }
  });

  it("uses no em dash and not the word verified", () => {
    const hints = [
      summary({ kind: "failed", message: "x" }),
      summary(ready([search("a")]), "no"),
      summary(ready([search("a")], { error: "x" })),
      summary(ready([search("a")], { busyId: "a" })),
      summary(ready([search("a")]), null, true),
      summary(ready([search("a", CHECKED)])),
      summary(ready([search("a", { is_active: false })])),
    ].map((entry) => entry?.hint ?? "");
    for (const hint of hints) {
      expect(hint).not.toBe("");
      expect(hint).not.toContain("\u2014");
      expect(hint.toLowerCase()).not.toContain("verified");
    }
  });
});
