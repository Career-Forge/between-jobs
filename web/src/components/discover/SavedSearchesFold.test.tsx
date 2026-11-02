// @vitest-environment jsdom
import { act } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { buttonNamed, click, installViewport, query, render } from "../../testing/render";
import type { SavedSearch } from "../../lib/discoverTypes";
import type { SavedSearchesState } from "../../lib/savedSearches";
import { SavedSearchesFold, type SavedSearchesFoldProps } from "./SavedSearchesFold";

// The saved-searches row on Discover: what its closed header says, when it opens by itself, that a
// person can always close it, and that the list inside still works as it does on Settings.

const NOW = new Date("2026-09-22T12:00:00Z");

function search(id: string, overrides: Partial<SavedSearch> = {}): SavedSearch {
  return { id, query: `role ${id}`, location: null, companies: [], remote_only: false, is_active: true, created_at: "2026-09-01T00:00:00Z", last_matched_at: null, ...overrides };
}

function ready(searches: SavedSearch[], extra: Partial<Extract<SavedSearchesState, { kind: "ready" }>> = {}): SavedSearchesState {
  return { kind: "ready", searches, busyId: null, error: null, ...extra };
}

const CHECKED = { last_matched_at: "2026-09-21T06:00:00Z" } as const;

function props(state: SavedSearchesState, extra: { createError?: string | null; saving?: boolean } = {}): SavedSearchesFoldProps {
  return {
    saved: { state, actions: { toggle: vi.fn(), remove: vi.fn(), retry: vi.fn() }, createError: extra.createError ?? null },
    saving: extra.saving ?? false,
    now: NOW,
    timeZone: "UTC",
  };
}

function draw(input: SavedSearchesFoldProps) {
  installViewport(1000);
  const view = render(
    <MemoryRouter>
      <SavedSearchesFold {...input} />
    </MemoryRouter>,
  );
  return {
    ...view,
    again: (next: SavedSearchesFoldProps) =>
      view.rerender(
        <MemoryRouter>
          <SavedSearchesFold {...next} />
        </MemoryRouter>,
      ),
  };
}

const words = (element: Element | null) => (element?.textContent ?? "").replace(/\s+/g, " ").trim();
const toggle = (container: HTMLElement) => query<HTMLButtonElement>(container, "button.bj-disc-saved-toggle");
const expanded = (container: HTMLElement) => toggle(container).getAttribute("aria-expanded");
const hint = (container: HTMLElement) => words(query(container, ".bj-disc-saved-hint"));

describe("no row at all", () => {
  it("draws nothing for a person with no saved searches, while the list is loading, or for a save with nothing yet to show", () => {
    expect(draw(props(ready([]))).container.innerHTML).toBe("");
    expect(draw(props({ kind: "loading" })).container.innerHTML).toBe("");
    expect(draw(props(ready([]), { saving: true })).container.innerHTML).toBe("");
  });
});

describe("the closed row", () => {
  it("is a heading with one button in it: the name, one hint, and a glyph that is hidden from assistive technology", () => {
    const { container } = draw(props(ready([search("a", CHECKED), search("b")])));
    const heading = query(container, "h2.bj-disc-saved-heading");
    expect(heading.querySelector("button")).toBe(toggle(container));
    expect(container.querySelectorAll("h2")).toHaveLength(1);
    expect(words(query(heading, ".bj-disc-saved-title"))).toBe("Saved searches");
    expect(hint(container)).toBe("2 searches, checked in the background");
    expect(query(heading, ".bj-disc-saved-glyph").getAttribute("aria-hidden")).toBe("true");
    expect(words(query(heading, ".bj-disc-saved-glyph"))).toBe("›");
    expect(words(toggle(container))).toBe("Saved searches 2 searches, checked in the background ›");
  });

  it("is closed by default, and keeps the list, its note and its buttons out of the page", () => {
    const { container } = draw(props(ready([search("a")])));
    expect(expanded(container)).toBe("false");
    expect(toggle(container).hasAttribute("aria-controls")).toBe(false);
    expect(toggle(container).getAttribute("type")).toBe("button");
    expect(container.querySelector(".bj-ss, .bj-ss-row, .bj-disc-saved-body")).toBeNull();
    expect(container.querySelectorAll("button")).toHaveLength(1);
  });

  it("says each state of the list in its hint", () => {
    expect(hint(draw(props(ready([search("a")]))).container)).toBe("1 search, not checked yet");
    expect(hint(draw(props(ready([search("a", { is_active: false })]))).container)).toBe("1 search, paused");
    expect(hint(draw(props(ready([search("a", { is_active: false }), search("b", { is_active: false })]))).container)).toBe("2 searches, all paused");
    expect(hint(draw(props(ready([search("a"), search("b", { is_active: false })]))).container)).toBe("2 searches, 1 active");
  });

  it("says Updating... or Saving... in the hint, and opens nothing for them", () => {
    const updating = draw(props(ready([search("a")], { busyId: "a" })));
    expect(hint(updating.container)).toBe("Updating...");
    expect(expanded(updating.container)).toBe("false");
    const saving = draw(props(ready([search("a")]), { saving: true }));
    expect(hint(saving.container)).toBe("Saving...");
    expect(expanded(saving.container)).toBe("false");
  });
});

describe("a save that lands while the row is closed", () => {
  it("says so in the closed row's hint, since the new search is not on screen, and opens nothing", () => {
    const view = draw(props(ready([search("a", CHECKED), search("b")]), { saving: true }));
    expect(hint(view.container)).toBe("Saving...");
    view.again(props(ready([search("a", CHECKED), search("b"), search("c")])));
    expect(hint(view.container)).toBe("Search saved -- 3 searches, checked in the background");
    expect(expanded(view.container)).toBe("false");
  });

  it("brings the row in saying so for a person's first saved search", () => {
    const view = draw(props(ready([]), { saving: true }));
    expect(view.container.innerHTML).toBe("");
    view.again(props(ready([search("a")])));
    expect(hint(view.container)).toBe("Search saved -- 1 search, not checked yet");
    expect(expanded(view.container)).toBe("false");
  });

  it("says nothing of a save that was refused", () => {
    const view = draw(props(ready([search("a")]), { saving: true }));
    view.again(props(ready([search("a")]), { createError: "We could not save that search." }));
    expect(hint(view.container)).toBe("A search couldn't be saved");
    expect(words(view.container)).not.toContain("Search saved");
  });

  it("stops saying it once the person opens the row, and does not say it again when they close it", () => {
    const view = draw(props(ready([search("a")]), { saving: true }));
    view.again(props(ready([search("a"), search("b")])));
    expect(hint(view.container)).toContain("Search saved");
    click(toggle(view.container));
    expect(hint(view.container)).toBe("2 searches, not checked yet");
    click(toggle(view.container));
    expect(hint(view.container)).toBe("2 searches, not checked yet");
  });

  it("stops saying it when the next save starts", () => {
    const view = draw(props(ready([search("a")]), { saving: true }));
    view.again(props(ready([search("a"), search("b")])));
    expect(hint(view.container)).toContain("Search saved");
    view.again(props(ready([search("a"), search("b")]), { saving: true }));
    expect(hint(view.container)).toBe("Saving...");
    view.again(props(ready([search("a"), search("b"), search("c")])));
    expect(hint(view.container)).toBe("Search saved -- 3 searches, not checked yet");
  });

  it("does not say it on first draw, or for a list that was only read again", () => {
    const view = draw(props(ready([search("a")])));
    expect(hint(view.container)).toBe("1 search, not checked yet");
    view.again(props(ready([search("a"), search("b")])));
    expect(hint(view.container)).toBe("2 searches, not checked yet");
  });
});

describe("opening and closing it", () => {
  it("opens with a press, names the region it controls, and shows the list under one heading", () => {
    const { container } = draw(props(ready([search("a", CHECKED), search("b")])));
    click(toggle(container));
    expect(expanded(container)).toBe("true");
    const body = query(container, ".bj-disc-saved-body");
    expect(toggle(container).getAttribute("aria-controls")).toBe(body.id);
    expect(body.id).not.toBe("");
    expect(words(query(container, ".bj-disc-saved-glyph"))).toBe("⌃");
    expect(container.querySelectorAll("h2")).toHaveLength(1);
    expect(words(container)).toContain("Active saved searches are checked every few hours in the background");
    expect([...body.querySelectorAll(".bj-ss-label")].map((label) => words(label))).toEqual(['"role a"', '"role b"']);
  });

  it("is a native button, so Enter and Space press it, and focus stays on it as it opens and closes", () => {
    const { container } = draw(props(ready([search("a")])));
    const button = toggle(container);
    expect(button.tagName).toBe("BUTTON");
    expect(button.hasAttribute("tabindex")).toBe(false);
    act(() => button.focus());
    click(button);
    expect(document.activeElement).toBe(button);
    click(button);
    expect(expanded(container)).toBe("false");
    expect(document.activeElement).toBe(button);
    expect(container.querySelector(".bj-ss")).toBeNull();
  });

  it("starts closed on every visit and does not remember what was open", () => {
    const first = draw(props(ready([search("a")])));
    click(toggle(first.container));
    first.unmount();
    expect(expanded(draw(props(ready([search("a")]))).container)).toBe("false");
  });

  it("keeps working inside the open row: Pause, Resume and Delete reach the page by search, and each button has a name of its own", () => {
    const input = props(ready([search("a"), search("b", { is_active: false })]));
    const { container } = draw(input);
    click(toggle(container));
    click(buttonNamed(container, /^Pause the saved search "role a"/));
    click(buttonNamed(container, /^Resume the saved search "role b"/));
    click(buttonNamed(container, /^Delete the saved search "role b"/));
    expect(input.saved.actions.toggle).toHaveBeenNthCalledWith(1, "a", false);
    expect(input.saved.actions.toggle).toHaveBeenNthCalledWith(2, "b", true);
    expect(input.saved.actions.remove).toHaveBeenCalledWith("b");
    expect(container.querySelectorAll("[data-focus-key]")).toHaveLength(2);
  });

  it("waits on the row's buttons while an update is out, as the card does on Settings", () => {
    const { container } = draw(props(ready([search("a")], { busyId: "a" })));
    click(toggle(container));
    const pause = buttonNamed(container, /^Pause the saved search/);
    expect(pause.getAttribute("aria-disabled")).toBe("true");
    expect(hint(container)).toBe("Updating...");
  });
});

describe("a failure opens the row on its own, and the person can still close it", () => {
  const failures: Array<[string, SavedSearchesFoldProps, string, string]> = [
    ["a failed read", props({ kind: "failed", message: "We could not load your saved searches." }), "Couldn't be read", "We could not load your saved searches."],
    ["a refused save", props(ready([search("a")]), { createError: "We could not save that search." }), "A search couldn't be saved", "We could not save that search."],
    ["a refused save with no searches", props(ready([]), { createError: "We could not save that search." }), "A search couldn't be saved", "We could not save that search."],
    ["a refused save while the list is loading", props({ kind: "loading" }, { createError: "We could not save that search." }), "A search couldn't be saved", "We could not save that search."],
    ["an update that did not go through", props(ready([search("a")], { error: "We could not update that saved search." })), "An update didn't go through", "We could not update that saved search."],
  ];

  it.each(failures)("%s is open on the first draw, with no closed row first, and says so in its hint", (_name, input, expectedHint, message) => {
    const markup = renderToStaticMarkup(
      <MemoryRouter>
        <SavedSearchesFold {...input} />
      </MemoryRouter>,
    );
    expect(markup).toContain('aria-expanded="true"');
    expect(markup).toContain(message);
    const { container } = draw(input);
    expect(expanded(container)).toBe("true");
    expect(hint(container)).toBe(expectedHint);
    expect(words(container)).toContain(message);
    expect(container.querySelectorAll("h2")).toHaveLength(1);
  });

  it.each(failures)("%s can be closed, is never locked, and its closed header still carries the failure", (_name, input, expectedHint) => {
    const { container } = draw(input);
    expect(toggle(container).hasAttribute("aria-disabled")).toBe(false);
    click(toggle(container));
    expect(expanded(container)).toBe("false");
    expect(container.querySelector(".bj-disc-saved-body")).toBeNull();
    expect(hint(container)).toBe(expectedHint);
    click(toggle(container));
    expect(expanded(container)).toBe("true");
  });

  it("opens a closed row when a failure turns up later, and then leaves it to the person", () => {
    const view = draw(props(ready([search("a")])));
    expect(expanded(view.container)).toBe("false");
    view.again(props(ready([search("a")]), { createError: "We could not save that search." }));
    expect(expanded(view.container)).toBe("true");
    click(toggle(view.container));
    expect(expanded(view.container)).toBe("false");
    view.again(props(ready([search("a")]), { createError: "We could not save that search." }));
    expect(expanded(view.container)).toBe("false");
  });

  it("stays open after the failure clears, until the person closes it", () => {
    const view = draw(props(ready([search("a")]), { createError: "We could not save that search." }));
    view.again(props(ready([search("a")])));
    expect(expanded(view.container)).toBe("true");
    expect(hint(view.container)).toBe("1 search, not checked yet");
    click(toggle(view.container));
    expect(expanded(view.container)).toBe("false");
  });

  it("does not open for an update that is only running or a save that is only out", () => {
    const view = draw(props(ready([search("a")])));
    view.again(props(ready([search("a")], { busyId: "a" })));
    expect(expanded(view.container)).toBe("false");
    view.again(props(ready([search("a")]), { saving: true }));
    expect(expanded(view.container)).toBe("false");
  });

  it("goes when the last search is deleted, and a search saved afterwards starts closed", () => {
    const view = draw(props(ready([search("a")])));
    click(toggle(view.container));
    expect(expanded(view.container)).toBe("true");
    view.again(props(ready([])));
    expect(view.container.innerHTML).toBe("");
    view.again(props(ready([search("b")])));
    expect(expanded(view.container)).toBe("false");
  });

  it("keeps the row open through the read that follows Try again, so the list it brings is where the person was looking", () => {
    const view = draw(props({ kind: "failed", message: "We could not load your saved searches." }));
    view.again(props({ kind: "loading" }));
    expect(view.container.innerHTML).toBe("");
    view.again(props(ready([search("a", CHECKED)])));
    expect(expanded(view.container)).toBe("true");
    expect(words(query(view.container, ".bj-ss-label"))).toBe('"role a"');
  });

  it("shows the failed read's Try again inside the open row, and asks the page to read again", () => {
    const input = props({ kind: "failed", message: "We could not load your saved searches." });
    const { container } = draw(input);
    click(buttonNamed(query(container, ".bj-ss[role='alert']"), "Try again"));
    expect(input.saved.actions.retry).toHaveBeenCalledTimes(1);
  });
});

describe("accessibility", () => {
  const cases: Array<[string, SavedSearchesFoldProps, boolean]> = [
    ["closed", props(ready([search("a", CHECKED), search("b")])), false],
    ["open", props(ready([search("a", CHECKED), search("b")])), true],
    ["open on its own for a failed read", props({ kind: "failed", message: "We could not load your saved searches." }), false],
    ["open on its own for a refused save", props(ready([search("a")]), { createError: "We could not save that search." }), false],
  ];

  it.each(cases)("%s has no serious or critical axe violation", async (_name, input, press) => {
    const { container } = draw(input);
    if (press) click(toggle(container));
    await expectNoSeriousA11yViolations(container);
  });
});
