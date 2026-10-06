import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { SavedSearch } from "../lib/discoverTypes";
import { SAVED_SEARCHES_NOTE, type SavedSearchesState } from "../lib/savedSearches";
import { tagsOf, textOfMarkup } from "../testing/markup";
import { buttonsLabelled, expand, findAll, press, prop, textOf } from "../testing/reactTree";
import { SavedSearchesCardView, type SavedSearchesActions } from "./SavedSearchesCardView";

// The saved-searches card on the Integrations page: what it draws for each state of the lookup, and
// that each button is wired to the action it is named for. No DOM: the markup is read as text and
// the buttons are pressed through a plain-element tree (testing/reactTree.ts).

function search(overrides: Partial<SavedSearch> = {}): SavedSearch {
  return {
    id: "s-1",
    query: "data analyst",
    location: null,
    companies: [],
    remote_only: false,
    is_active: true,
    created_at: "2026-10-01T00:00:00+00:00",
    ...overrides,
  };
}

function ready(searches: SavedSearch[], extra: Partial<Extract<SavedSearchesState, { kind: "ready" }>> = {}): SavedSearchesState {
  return { kind: "ready", searches, busyId: null, error: null, ...extra };
}

function recorder() {
  const calls: [string, ...unknown[]][] = [];
  const actions: SavedSearchesActions = {
    toggle: (id, isActive) => calls.push(["toggle", id, isActive]),
    remove: (id) => calls.push(["remove", id]),
    retry: () => calls.push(["retry"]),
  };
  return { calls, actions };
}

const html = (state: SavedSearchesState) => renderToStaticMarkup(<SavedSearchesCardView state={state} actions={recorder().actions} />);
const tree = (state: SavedSearchesState, actions = recorder().actions) => expand(SavedSearchesCardView({ state, actions }));

describe("what it draws", () => {
  it("draws nothing while the list is loading, and nothing for a person with no saved searches", () => {
    expect(html({ kind: "loading" })).toBe("");
    expect(html(ready([]))).toBe("");
  });

  it("says plainly when it could not load the list, with a way to try again: a search that still runs must not look as though it were not there", () => {
    const markup = html({ kind: "failed", message: "The service is down." });
    expect(markup).toContain('role="alert"');
    const text = textOfMarkup(markup);
    expect(text).toContain("Saved searches");
    expect(text).toContain("The service is down.");
    expect(text).not.toContain("No saved searches");
    const { actions, calls } = recorder();
    press(buttonsLabelled(tree({ kind: "failed", message: "x" }, actions), "Try again")[0]);
    expect(calls).toEqual([["retry"]]);
  });

  it("lists each search by its label, under the heading and the note about what runs in the background", () => {
    const text = textOfMarkup(html(ready([search(), search({ id: "s-2", query: "", location: "Remote", remote_only: true })])));
    expect(text).toContain("Saved searches");
    expect(text).toContain(SAVED_SEARCHES_NOTE);
    expect(text).toContain('"data analyst"');
    expect(text).toContain("in Remote remote only");
  });

  it("marks a paused search as paused, and offers Resume instead of Pause", () => {
    const text = textOfMarkup(html(ready([search({ is_active: false })])));
    expect(text).toContain("(paused)");
    const nodes = tree(ready([search({ is_active: false })]));
    expect(buttonsLabelled(nodes, "Resume")).toHaveLength(1);
    expect(buttonsLabelled(nodes, "Pause")).toHaveLength(0);
  });

  it("shows the last action's failure above the list, which is still there", () => {
    const markup = html(ready([search()], { error: "We could not delete that saved search." }));
    expect(textOfMarkup(markup)).toContain("We could not delete that saved search.");
    expect(textOfMarkup(markup)).toContain('"data analyst"');
    expect(tagsOf(markup, "div").some((tag) => tag.role === "alert")).toBe(true);
  });
});

describe("the buttons", () => {
  it("each have a name of their own that says which search they act on", () => {
    const nodes = tree(ready([search(), search({ id: "s-2", query: "qa", is_active: false })]));
    const names = findAll(nodes, (el) => el.type === "button").map((el) => prop(el, "aria-label"));
    expect(names).toEqual([
      'Pause the saved search "data analyst"',
      'Delete the saved search "data analyst"',
      'Resume the saved search "qa"',
      'Delete the saved search "qa"',
    ]);
  });

  it("Pause pauses, Resume resumes, Delete deletes, each for its own search", () => {
    const { actions, calls } = recorder();
    const nodes = tree(ready([search({ id: "a" }), search({ id: "b", is_active: false })]), actions);
    const [pauseA, deleteA, resumeB, deleteB] = findAll(nodes, (el) => el.type === "button");
    expect(textOf(pauseA)).toBe("Pause");
    press(pauseA);
    press(resumeB);
    press(deleteA);
    press(deleteB);
    expect(calls).toEqual([
      ["toggle", "a", false],
      ["toggle", "b", true],
      ["remove", "a"],
      ["remove", "b"],
    ]);
  });

  it("waits only for the search an action is out for, and every button is a plain button (never a submit)", () => {
    const nodes = tree(ready([search({ id: "a" }), search({ id: "b" })], { busyId: "a" }));
    const buttons = findAll(nodes, (el) => el.type === "button");
    expect(buttons.map((button) => prop(button, "disabled"))).toEqual([true, true, false, false]);
    expect(buttons.every((button) => prop(button, "type") === "button")).toBe(true);
  });
});
