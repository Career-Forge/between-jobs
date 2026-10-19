import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { SavedSearch } from "../lib/discoverTypes";
import { SAVED_SEARCHES_NOTE, type SavedSearchesState } from "../lib/savedSearches";
import { tagsOf, textOfMarkup } from "../testing/markup";
import { buttonsLabelled, expand, findAll, press, prop, textOf } from "../testing/reactTree";
import { SavedSearchesCardView, type SavedSearchesActions } from "./SavedSearchesCardView";

// The saved-searches card on the Settings page: what it draws for each state of the lookup, and
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

  it("marks a paused search as paused in its trail line, and offers Resume instead of Pause", () => {
    const text = textOfMarkup(html(ready([search({ is_active: false })])));
    expect(text).toContain("Not checked yet · Paused");
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

  it("waits on every row while one action is out (one request at a time), with aria-disabled so focus stays, and every button is a plain button (never a submit)", () => {
    const nodes = tree(ready([search({ id: "a" }), search({ id: "b" })], { busyId: "a" }));
    const buttons = findAll(nodes, (el) => el.type === "button");
    expect(buttons.map((button) => prop(button, "aria-disabled"))).toEqual([true, true, true, true]);
    expect(buttons.map((button) => prop(button, "disabled"))).toEqual([undefined, undefined, undefined, undefined]);
    expect(buttons.every((button) => prop(button, "type") === "button")).toBe(true);
  });

  it("ignores a press while an action is out, on the busy row and on any other", () => {
    const { actions, calls } = recorder();
    const nodes = tree(ready([search({ id: "a" }), search({ id: "b" })], { busyId: "a" }), actions);
    for (const button of findAll(nodes, (el) => el.type === "button")) press(button);
    expect(calls).toEqual([]);
  });
});

describe("when each search was last checked (the trail line under its label)", () => {
  const NOW = new Date("2026-09-22T12:00:00Z");
  const drawn = (searches: SavedSearch[]) => textOfMarkup(renderToStaticMarkup(<SavedSearchesCardView state={ready(searches)} actions={recorder().actions} now={NOW} timeZone="UTC" />));

  it("says 'Checked 06:00' for a check made today, after the matcher ran since the search was saved", () => {
    expect(drawn([search({ created_at: "2026-09-01T00:00:00Z", last_matched_at: "2026-09-22T06:00:00Z" })])).toContain("Checked 06:00");
  });

  it("says the date for a check made on another day", () => {
    expect(drawn([search({ created_at: "2026-09-01T00:00:00Z", last_matched_at: "2026-09-18T06:00:00Z" })])).toContain("Checked Sep 18");
  });

  it("says 'Not checked yet' when the matcher has not run, or only stamped the search as it was saved", () => {
    expect(drawn([search({ last_matched_at: null })])).toContain("Not checked yet");
    expect(drawn([search({ created_at: "2026-09-22T06:00:00Z", last_matched_at: "2026-09-22T06:00:00Z" })])).toContain("Not checked yet");
  });

  it("says nothing of a model or a key in the line: the matcher stamps a skipped run the same way", () => {
    const text = drawn([search({ created_at: "2026-09-01T00:00:00Z", last_matched_at: "2026-09-22T06:00:00Z" })]);
    const line = text.slice(text.indexOf("Checked"));
    expect(line.toLowerCase()).not.toContain("model");
    expect(line.toLowerCase()).not.toContain("your key");
  });

  it("puts the line last in the row, after the label", () => {
    const markup = renderToStaticMarkup(<SavedSearchesCardView state={ready([search()])} actions={recorder().actions} now={NOW} />);
    expect(markup.indexOf("bj-ss-label")).toBeLessThan(markup.indexOf("bj-trail"));
  });
});

describe("a search that could not be saved from Discover", () => {
  it("is shown in the card as an alert, even when the list is empty, so the failure sits with the list it would have joined", () => {
    const markup = renderToStaticMarkup(<SavedSearchesCardView state={ready([])} actions={recorder().actions} createError="You can keep at most 25 saved searches." />);
    expect(textOfMarkup(markup)).toContain("You can keep at most 25 saved searches.");
    expect(tagsOf(markup, "div").some((tag) => tag.role === "alert")).toBe(true);
  });

  it("is shown while the list is still loading, as its own alert, so a failed save is never silent", () => {
    const loading = renderToStaticMarkup(<SavedSearchesCardView state={{ kind: "loading" }} actions={recorder().actions} createError="You can keep at most 25 saved searches." />);
    expect(textOfMarkup(loading)).toContain("You can keep at most 25 saved searches.");
    expect(tagsOf(loading, "div").some((tag) => tag.role === "alert")).toBe(true);
  });

  it("is shown beside a failed read of the list, inside the card that is already the alert", () => {
    const failed = renderToStaticMarkup(
      <SavedSearchesCardView state={{ kind: "failed", message: "The service is down." }} actions={recorder().actions} createError="You can keep at most 25 saved searches." />,
    );
    const text = textOfMarkup(failed);
    expect(text).toContain("The service is down.");
    expect(text).toContain("You can keep at most 25 saved searches.");
    expect(tagsOf(failed, "div").filter((tag) => tag.role === "alert")).toHaveLength(1);
  });

  it("still draws nothing for a loading list when no save failed", () => {
    expect(renderToStaticMarkup(<SavedSearchesCardView state={{ kind: "loading" }} actions={recorder().actions} createError={null} />)).toBe("");
  });
});
