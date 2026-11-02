// @vitest-environment jsdom
import { act } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { job, mixedResponse, response, score } from "../../testing/discoverFixtures";
import { buttonNamed, click, installViewport, keydown, query, render } from "../../testing/render";
import type { DiscoverResponse, ScoreBin } from "../../lib/discoverTypes";
import { NONE_SCORED_NOTE, ResultsView, type ResultsViewProps } from "./ResultsView";

// A finished search as ResultsView draws it: what a person's choices do to the rows. The page states
// around it (the band, the form, setup and failure) are DiscoverView.test.tsx.

const NOW = new Date("2026-09-22T12:00:00Z");

function props(result: DiscoverResponse, overrides: Partial<ResultsViewProps> = {}): ResultsViewProps {
  return { result, now: NOW, tracking: new Set(), tracked: {}, trackErrors: {}, onTrack: vi.fn(), timeZone: "UTC", ...overrides };
}

function draw(result: DiscoverResponse, overrides: Partial<ResultsViewProps> = {}) {
  installViewport(1000);
  const rendered = render(
    <MemoryRouter>
      <ResultsView {...props(result, overrides)} />
    </MemoryRouter>,
  );
  return {
    ...rendered,
    again: (next: DiscoverResponse, more: Partial<ResultsViewProps> = {}) =>
      rendered.rerender(
        <MemoryRouter>
          <ResultsView {...props(next, more)} />
        </MemoryRouter>,
      ),
  };
}

const rowsOf = (container: HTMLElement) => [...container.querySelectorAll("li.bj-disc-row")];

describe("opening a row's detail", () => {
  it("keeps a posting that sits in both lists open on its own side only", () => {
    const shared = "https://jobs.example/acme/shared";
    // A row past the scoring batch normally has no score; one that carries a score still has a detail to open.
    const result = response({ scored: [job({ apply_url: shared })], more: [job({ apply_url: shared, title: "Data Engineer (listed again)" })] });
    const { container } = draw(result);
    const [first, second] = rowsOf(container);
    click(buttonNamed(first, /^Why this score for /));
    expect(first.querySelector(".bj-fit-detail")).not.toBeNull();
    expect(second.querySelector(".bj-fit-detail")).toBeNull();
    click(buttonNamed(second, /^Why this score for /));
    expect(second.querySelector(".bj-fit-detail")).not.toBeNull();
    click(buttonNamed(first, /^Why this score for /));
    expect(first.querySelector(".bj-fit-detail")).toBeNull();
    expect(second.querySelector(".bj-fit-detail")).not.toBeNull();
    expect(rowsOf(container)).toHaveLength(2);
  });

  it("keeps a row open across a re-render that keeps the same result, as a Track does", () => {
    const result = response({ scored: [job(), job({ apply_url: "https://jobs.example/acme/2", title: "Analyst" })] });
    const view = draw(result);
    click(buttonNamed(rowsOf(view.container)[1], /^Why this score for /));
    view.again(result, { tracking: new Set(["https://jobs.example/acme/1"]) });
    expect(rowsOf(view.container)[1].querySelector(".bj-fit-detail")).not.toBeNull();
    expect(rowsOf(view.container)[0].querySelector(".bj-fit-detail")).toBeNull();
    expect(query(view.container, "button.bj-fit-toggle[aria-expanded='true']")).not.toBeNull();
  });
});

const words = (element: Element | null) => (element?.textContent ?? "").replace(/\s+/g, " ").trim();
const titles = (container: HTMLElement) => [...container.querySelectorAll("h3.bj-disc-title")].map((title) => words(title));
const fitGroup = (container: HTMLElement) => query(container, '[role="group"][aria-label="Show results by fit"]');
const sortGroup = (container: HTMLElement) => query(container, '[role="group"][aria-label="Sort results"]');
const pressed = (group: Element) => [...group.querySelectorAll("button")].filter((button) => button.getAttribute("aria-pressed") === "true").map((button) => words(button));
const chip = (container: HTMLElement, label: string) => buttonNamed(fitGroup(container), new RegExp(`^${label} \\d+ results?$`));
const sortButton = (container: HTMLElement, label: string) => buttonNamed(sortGroup(container), label);
const live = (container: HTMLElement) => query(container, ".bj-disc-toolbar [role='status']");
const headings = (container: HTMLElement) => [...container.querySelectorAll("h2")].map((heading) => words(heading));
const countLine = (container: HTMLElement) => container.querySelector(".bj-disc-count")?.textContent ?? null;
const switchBox = (container: HTMLElement) => query<HTMLInputElement>(container, ".bj-disc-toggle input[type='checkbox']");
const detailToggles = (container: HTMLElement) => [...container.querySelectorAll<HTMLButtonElement>("button.bj-fit-toggle")];
const openCount = (container: HTMLElement) => container.querySelectorAll(".bj-fit-detail").length;

const MIXED = () => mixedResponse();
const ALL_TITLES = (result: DiscoverResponse) => [...result.scored, ...result.more].map((entry) => entry.title);

describe("the toolbar", () => {
  it("is drawn from three rows and not for two", () => {
    const three = response({ scored: [job(), job({ apply_url: "https://jobs.example/acme/2" }), job({ apply_url: "https://jobs.example/acme/3" })] });
    expect(draw(three).container.querySelector(".bj-disc-tools")).not.toBeNull();
    const two = draw(response({ scored: [job(), job({ apply_url: "https://jobs.example/acme/2" })] }));
    expect(two.container.querySelector(".bj-disc-tools")).toBeNull();
    expect(two.container.querySelector(".bj-disc-count")).toBeNull();
    expect(two.container.querySelector("[role='status']")).toBeNull();
  });

  it("is drawn for two scored rows and one past the batch: the total counts both sections", () => {
    const rows = response({ scored: [job(), job({ apply_url: "https://jobs.example/acme/2" })], more: [job({ apply_url: "https://jobs.example/acme/3", score: null })] });
    expect(draw(rows).container.querySelector(".bj-disc-tools")).not.toBeNull();
  });

  it("names its groups, and gives each chip a name that leads with its visible words and ends in the word results", () => {
    const { container } = draw(MIXED());
    expect(fitGroup(container).classList.contains("bj-segmented")).toBe(true);
    expect(sortGroup(container).classList.contains("bj-segmented")).toBe(true);
    const names = [...fitGroup(container).querySelectorAll("button")].map((button) => words(button));
    expect(names).toEqual(["All 25 results", "Strong 6 results", "Good 6 results", "Mixed 3 results", "Poor 3 results", "Not scored 7 results"]);
    const visible = [...fitGroup(container).querySelectorAll("button")].map((button) => words(button).replace(/\s+results?$/, ""));
    expect(visible).toEqual(["All 25", "Strong 6", "Good 6", "Mixed 3", "Poor 3", "Not scored 7"]);
    expect([...sortGroup(container).querySelectorAll("button")].map((button) => words(button))).toEqual(["As returned", "Best fit", "Newest"]);
  });

  it("sets each chip's number in the mono face, as its own element, and the group's words as aria-hidden labels", () => {
    const { container } = draw(MIXED());
    expect([...container.querySelectorAll(".bj-disc-chip-count")].map((count) => words(count))).toEqual(["25", "6", "6", "3", "3", "7"]);
    const labels = [...container.querySelectorAll(".bj-disc-tool-label")];
    expect(labels.map((label) => words(label))).toEqual(["Fit", "Sort"]);
    for (const label of labels) expect(label.getAttribute("aria-hidden")).toBe("true");
  });

  it("says result, not results, for a count of one", () => {
    const { container } = draw(mixedResponse({ Strong: 1, Good: 4, Mixed: 0, Poor: 0, unscored: 0 }));
    expect([...fitGroup(container).querySelectorAll("button")].map((button) => words(button))).toEqual(["All 5 results", "Strong 1 result", "Good 4 results"]);
  });

  it("presses one chip and one sort at a time, All and As returned to begin with, and draws no tier nobody holds", () => {
    const { container } = draw(mixedResponse({ Mixed: 0 }));
    expect(pressed(fitGroup(container))).toEqual(["All 22 results"]);
    expect(pressed(sortGroup(container))).toEqual(["As returned"]);
    expect(words(fitGroup(container))).not.toContain("Mixed");
    click(chip(container, "Good"));
    expect(pressed(fitGroup(container))).toEqual(["Good 6 results"]);
    click(sortButton(container, "Newest"));
    expect(pressed(sortGroup(container))).toEqual(["Newest"]);
  });

  it("makes every control a native button or checkbox, with no tab index and no key handler of its own", () => {
    const { container } = draw(MIXED());
    const tools = query(container, ".bj-disc-tools");
    for (const control of tools.querySelectorAll("button, input")) {
      expect(["BUTTON", "INPUT"]).toContain(control.tagName);
      expect(control.hasAttribute("tabindex")).toBe(false);
    }
    for (const button of tools.querySelectorAll("button")) expect(button.getAttribute("type")).toBe("button");
    const before = container.innerHTML;
    act(() => {
      for (const key of ["ArrowRight", "ArrowLeft", "Home", "End", "s", "/"]) document.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
    });
    keydown("ArrowRight", { target: chip(container, "Strong") });
    expect(container.innerHTML).toBe(before);
  });

  it("walks status, fit group, sort group, the switch, then the rows, in the page's own order", () => {
    const { container } = draw(response({ ...MIXED(), warnings: ["brave: down"] }));
    const kinds = ["bj-disc-status", "bj-disc-chips", "bj-disc-sort", "bj-disc-toggle", "bj-disc-list"];
    const order = [...container.querySelectorAll(".bj-disc-status, .bj-disc-chips, .bj-disc-sort, .bj-disc-toggle, ul.bj-disc-list")].map((element) => kinds.find((kind) => element.classList.contains(kind)));
    expect(order).toEqual(["bj-disc-status", "bj-disc-chips", "bj-disc-sort", "bj-disc-toggle", "bj-disc-list", "bj-disc-list"]);
  });
});

describe("keeping one fit", () => {
  it("keeps the rows of the tier chosen, hides More results, and says how many of how many, and how many not scored are not shown", () => {
    const result = MIXED();
    const { container } = draw(result);
    expect(countLine(container)).toBeNull();
    click(chip(container, "Strong"));
    expect(titles(container)).toEqual(["Strong 1", "Strong 2", "Strong 3", "Strong 4", "Strong 5", "Strong 6"]);
    expect(headings(container)).not.toContain("More results");
    expect(container.querySelectorAll("ul.bj-disc-list")).toHaveLength(1);
    expect(countLine(container)).toBe("Showing 6 of 25 results, Strong fit only. 7 not scored are not shown.");
    click(chip(container, "Poor"));
    expect(titles(container)).toEqual(["Poor 1", "Poor 2", "Poor 3"]);
    expect(countLine(container)).toBe("Showing 3 of 25 results, Poor fit only. 7 not scored are not shown.");
  });

  it("says 'is' for one not-scored row left out, and says nothing about it when there are none", () => {
    const one = draw(mixedResponse({ unscored: 1 }));
    click(chip(one.container, "Good"));
    expect(countLine(one.container)).toBe("Showing 6 of 19 results, Good fit only. 1 not scored is not shown.");
    const none = draw(mixedResponse({ unscored: 0 }));
    click(chip(none.container, "Good"));
    expect(countLine(none.container)).toBe("Showing 6 of 18 results, Good fit only.");
  });

  it("restores everything with All, and shows only More results for Not scored", () => {
    const result = MIXED();
    const { container } = draw(result);
    click(chip(container, "Not scored"));
    expect(titles(container)).toEqual(["Unscored 1", "Unscored 2", "Unscored 3", "Unscored 4", "Unscored 5", "Unscored 6", "Unscored 7"]);
    expect(headings(container)).toContain("More results");
    expect(container.querySelectorAll("ul.bj-disc-list")).toHaveLength(1);
    expect(countLine(container)).toBe("Showing 7 of 25 results, not scored only.");
    click(chip(container, "All"));
    expect(titles(container)).toEqual(ALL_TITLES(result));
    expect(countLine(container)).toBeNull();
    expect(headings(container)).toContain("More results");
  });

  it("does nothing when the chip pressed is already the one chosen", () => {
    const { container } = draw(MIXED());
    click(chip(container, "All"));
    expect(live(container).textContent).toBe("");
    click(chip(container, "Strong"));
    const said = live(container).textContent;
    click(chip(container, "Strong"));
    expect(live(container).textContent).toBe(said);
    click(sortButton(container, "As returned"));
    expect(live(container).textContent).toBe(said);
  });

  it("keeps a row's choices while the fit changes: a tracked row still says Open workspace after a filter and a sort", () => {
    const result = MIXED();
    const target = result.scored.find((entry) => entry.title === "Good 2")!;
    const view = draw(result);
    click(chip(view.container, "Good"));
    click(sortButton(view.container, "Best fit"));
    expect(titles(view.container)).toEqual(["Good 1", "Good 2", "Good 3", "Good 4", "Good 5", "Good 6"]);
    const onTrack = vi.fn();
    view.again(result, { onTrack });
    click(buttonNamed(rowsOf(view.container)[1], /^Track /));
    expect(onTrack.mock.calls[0][0].apply_url).toBe(target.apply_url);
    view.again(result, { onTrack, tracked: { [target.apply_url]: "app-7" } });
    const open = [...rowsOf(view.container)[1].querySelectorAll<HTMLAnchorElement>("a")].find((link) => words(link) === "Open workspace");
    expect(open?.getAttribute("href")).toBe("/applications/app-7");
    expect(pressed(fitGroup(view.container))).toEqual(["Good 6 results"]);
    expect(pressed(sortGroup(view.container))).toEqual(["Best fit"]);
  });
});

describe("sorting", () => {
  it("returns the rows as the search did for As returned", () => {
    const result = MIXED();
    const { container } = draw(result);
    expect(titles(container)).toEqual(ALL_TITLES(result));
  });

  it("puts the highest score first for Best fit, and More results stays below in the order it came", () => {
    const result = MIXED();
    const { container } = draw(result);
    click(sortButton(container, "Best fit"));
    const scoredTitles = [...container.querySelectorAll("ul.bj-disc-list")][0].querySelectorAll("h3.bj-disc-title");
    expect([...scoredTitles].map((title) => words(title))).toEqual([
      "Strong 1", "Strong 2", "Strong 3", "Strong 4", "Strong 5", "Strong 6",
      "Good 1", "Good 2", "Good 3", "Good 4", "Good 5", "Good 6",
      "Mixed 1", "Mixed 2", "Mixed 3", "Poor 1", "Poor 2", "Poor 3",
    ]);
    expect([...container.querySelectorAll("ul.bj-disc-list")][1].querySelectorAll("h3.bj-disc-title")).toHaveLength(7);
    expect(titles(container).slice(18)).toEqual(result.more.map((entry) => entry.title));
  });

  it("puts the latest posting first for Newest, in each section on its own, with undated rows last", () => {
    const { container } = draw(MIXED());
    click(sortButton(container, "Newest"));
    const lists = [...container.querySelectorAll("ul.bj-disc-list")];
    expect([...lists[0].querySelectorAll("h3.bj-disc-title")].map((title) => words(title))).toEqual([
      "Strong 6", "Strong 5", "Strong 4", "Strong 2", "Strong 1", "Mixed 3", "Mixed 1", "Good 5", "Good 4", "Good 3",
      "Poor 3", "Good 2", "Poor 2", "Good 1", "Poor 1", "Mixed 2", "Strong 3", "Good 6",
    ]);
    expect([...lists[1].querySelectorAll("h3.bj-disc-title")].map((title) => words(title))).toEqual(["Unscored 4", "Unscored 7", "Unscored 3", "Unscored 6", "Unscored 2", "Unscored 1", "Unscored 5"]);
  });

  it("changes what the More results note says only for Newest", () => {
    const { container } = draw(MIXED());
    const note = () => words(query(container, "h2.bj-disc-heading--more").nextElementSibling);
    expect(note()).toBe("Beyond this search's scoring batch -- sorted by tier and recency, not fit.");
    click(sortButton(container, "Best fit"));
    expect(note()).toBe("Beyond this search's scoring batch -- sorted by tier and recency, not fit.");
    click(sortButton(container, "Newest"));
    expect(note()).toBe("Beyond this search's scoring batch -- not scored, newest first.");
    click(sortButton(container, "As returned"));
    expect(note()).toBe("Beyond this search's scoring batch -- sorted by tier and recency, not fit.");
  });

  it("leaves out Best fit when fewer than two scored rows carry a number, and when the scoring fell back", () => {
    const one = draw(mixedResponse({ Strong: 1, Good: 0, Mixed: 0, Poor: 0, unscored: 3 }));
    expect([...sortGroup(one.container).querySelectorAll("button")].map((button) => words(button))).toEqual(["As returned", "Newest"]);
    const nan = draw(response({ scored: [job({ score: score({ score100: Number.NaN }) }), job({ apply_url: "https://jobs.example/acme/2", score: score({ score100: Number.NaN }) }), job({ apply_url: "https://jobs.example/acme/3" })] }));
    expect([...sortGroup(nan.container).querySelectorAll("button")].map((button) => words(button))).toEqual(["As returned", "Newest"]);
    const fallback = draw(mixedResponse({}, { scoring_strategy: "neutral_fallback" }));
    expect([...sortGroup(fallback.container).querySelectorAll("button")].map((button) => words(button))).toEqual(["As returned", "Newest"]);
  });
});

describe("what is drawn when there is nothing to choose between", () => {
  it("draws no fit group, no details switch and only two sorts under the scoring fallback, and no tier class", () => {
    const { container } = draw(mixedResponse({}, { scoring_strategy: "neutral_fallback" }));
    expect(container.querySelector('[aria-label="Show results by fit"]')).toBeNull();
    expect(container.querySelector(".bj-disc-toggle")).toBeNull();
    expect(container.querySelector(".bj-disc-count")).toBeNull();
    expect(query(container, ".bj-disc-tools")).not.toBeNull();
    expect(container.querySelectorAll(".bj-disc-row--placeholder").length).toBe(18);
    expect(container.querySelectorAll(".bj-disc-row--strong, .bj-disc-row--good, .bj-disc-row--mixed, .bj-disc-row--poor")).toHaveLength(0);
    expect(container.querySelector("button.bj-fit-toggle")).toBeNull();
  });

  it("draws no fit group, the sentence about no fit, and two sorts when nothing was scored", () => {
    const { container } = draw(mixedResponse({ Strong: 0, Good: 0, Mixed: 0, Poor: 0, unscored: 4 }));
    expect(container.querySelector('[aria-label="Show results by fit"]')).toBeNull();
    expect(container.querySelector(".bj-disc-toggle")).toBeNull();
    expect(words(container)).toContain(NONE_SCORED_NOTE);
    expect([...sortGroup(container).querySelectorAll("button")].map((button) => words(button))).toEqual(["As returned", "Newest"]);
    expect(headings(container)).toEqual(["Results", "More results"]);
    expect(titles(container)).toEqual(["Unscored 1", "Unscored 2", "Unscored 3", "Unscored 4"]);
  });

  it("does not say nothing was scored when something was, or when the scores are placeholders", () => {
    expect(words(draw(MIXED()).container)).not.toContain(NONE_SCORED_NOTE);
    expect(words(draw(mixedResponse({}, { scoring_strategy: "neutral_fallback" })).container)).not.toContain(NONE_SCORED_NOTE);
  });

  it("draws no fit group for one tier alone, but still offers the sort and the switch", () => {
    const { container } = draw(mixedResponse({ Strong: 0, Good: 4, Mixed: 0, Poor: 0, unscored: 0 }));
    expect(container.querySelector('[aria-label="Show results by fit"]')).toBeNull();
    expect(sortGroup(container)).not.toBeNull();
    expect(container.querySelector(".bj-disc-toggle")).not.toBeNull();
  });

  it("draws the status block and then the empty state, and no toolbar, for a search with no rows", () => {
    const { container } = draw(response({ scored: [], more: [], warnings: ["brave: down"], dead_removed: 2 }));
    expect(container.querySelector(".bj-disc-tools")).toBeNull();
    const status = query(container, ".bj-disc-status");
    expect(status.compareDocumentPosition(query(container, ".bj-state--empty")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(container.querySelector("[role='status']")).toBeNull();
  });

  it("offers a filter only when it keeps something: every chip drawn leaves at least one row, for each kind of response", () => {
    const responses: DiscoverResponse[] = [
      MIXED(),
      mixedResponse({ Strong: 0, Good: 3, Mixed: 0, Poor: 0, unscored: 2 }),
      mixedResponse({ Strong: 2, Good: 0, Mixed: 0, Poor: 5, unscored: 0 }),
      mixedResponse({ Strong: 0, Good: 0, Mixed: 0, Poor: 0, unscored: 5 }),
      mixedResponse({}, { scoring_strategy: "neutral_fallback" }),
      response({
        scored: [1, 2, 3].map((n) => job({ apply_url: `https://jobs.example/odd/${n}`, title: `Odd ${n}`, score: score({ bin: "Excellent" as ScoreBin }) })),
      }),
    ];
    for (const result of responses) {
      const { container, unmount } = draw(result);
      const group = container.querySelector('[aria-label="Show results by fit"]');
      if (group === null) {
        unmount();
        continue;
      }
      for (const button of [...group.querySelectorAll("button")]) {
        click(button);
        expect(rowsOf(container).length, words(button)).toBeGreaterThan(0);
      }
      unmount();
    }
  });
});

describe("the details switch", () => {
  it("opens every row's detail when checked and closes them all when unchecked, with a description that names what it does", () => {
    const { container } = draw(MIXED());
    const box = switchBox(container);
    expect(words(query(container, ".bj-disc-toggle"))).toBe("Show score details");
    expect(box.closest("label")?.textContent).toBe("Show score details");
    const note = query(container, `#${box.getAttribute("aria-describedby")}`);
    expect(note.closest("label")).toBeNull();
    expect(words(note)).toBe("Opens Why this score on every row.");
    expect(note.classList.contains("bj-visually-hidden")).toBe(true);
    expect(openCount(container)).toBe(0);
    click(box);
    expect(box.checked).toBe(true);
    expect(openCount(container)).toBe(18);
    expect(detailToggles(container).every((toggle) => toggle.getAttribute("aria-expanded") === "true")).toBe(true);
    click(box);
    expect(box.checked).toBe(false);
    expect(openCount(container)).toBe(0);
  });

  it("changes only one row when that row's own button is pressed after the switch, and the next use of the switch overwrites it", () => {
    const { container } = draw(MIXED());
    const box = switchBox(container);
    click(box);
    click(detailToggles(container)[3]);
    expect(openCount(container)).toBe(17);
    expect(detailToggles(container)[3].getAttribute("aria-expanded")).toBe("false");
    expect(box.checked).toBe(true);
    click(box);
    expect(box.checked).toBe(false);
    expect(openCount(container)).toBe(0);
    click(box);
    expect(openCount(container)).toBe(18);
  });

  it("changes only one row when its button is pressed with the switch off, and the switch then overwrites that too", () => {
    const { container } = draw(MIXED());
    click(detailToggles(container)[0]);
    expect(openCount(container)).toBe(1);
    click(switchBox(container));
    expect(openCount(container)).toBe(18);
    click(switchBox(container));
    expect(openCount(container)).toBe(0);
  });

  it("is drawn only while a shown row has a detail: Not scored shows none, so the switch goes", () => {
    const { container } = draw(MIXED());
    expect(container.querySelector(".bj-disc-toggle")).not.toBeNull();
    click(chip(container, "Not scored"));
    expect(container.querySelector(".bj-disc-toggle")).toBeNull();
    click(chip(container, "Poor"));
    expect(container.querySelector(".bj-disc-toggle")).not.toBeNull();
  });

  it("opens the details of the rows a later choice brings in", () => {
    const { container } = draw(MIXED());
    click(switchBox(container));
    click(chip(container, "Poor"));
    expect(openCount(container)).toBe(3);
    expect(switchBox(container).checked).toBe(true);
  });
});

describe("what a screen reader is told", () => {
  it("has one polite status region, inside the results section, empty until a person presses something", () => {
    const { container } = draw(MIXED());
    const regions = container.querySelectorAll("[role='status']");
    expect(regions).toHaveLength(1);
    const region = regions[0];
    expect(region.textContent).toBe("");
    expect(region.getAttribute("aria-live")).toBe("polite");
    expect(region.getAttribute("aria-atomic")).toBe("true");
    expect(region.classList.contains("bj-visually-hidden")).toBe(true);
    expect(region.closest("section.bj-disc-results")).not.toBeNull();
    expect(region.parentElement?.classList.contains("bj-discover")).toBe(false);
  });

  it("says what a chip did, in the count line's words, and what a sort did", () => {
    const { container } = draw(MIXED());
    click(chip(container, "Strong"));
    expect(live(container).textContent).toBe("Showing 6 of 25 results, Strong fit only. 7 not scored are not shown.");
    click(chip(container, "Not scored"));
    expect(live(container).textContent).toBe("Showing 7 of 25 results, not scored only.");
    click(chip(container, "All"));
    expect(live(container).textContent).toBe("Showing all 25 results.");
    click(sortButton(container, "Best fit"));
    expect(live(container).textContent).toBe("Sorted by best fit first.");
    click(sortButton(container, "Newest"));
    expect(live(container).textContent).toBe("Sorted by newest first.");
    click(sortButton(container, "As returned"));
    expect(live(container).textContent).toBe("Sorted as the search returned them.");
  });

  it("keeps the visible count line out of the live region, so it is not read twice", () => {
    const { container } = draw(MIXED());
    click(chip(container, "Good"));
    const line = query(container, ".bj-disc-count");
    expect(line.closest("[role='status']")).toBeNull();
    expect(line.getAttribute("aria-live")).toBeNull();
    expect(line.getAttribute("role")).toBeNull();
  });

  it("keeps focus on the control that was pressed while the list changes under it", () => {
    const { container } = draw(MIXED());
    const strong = chip(container, "Strong");
    act(() => strong.focus());
    click(strong);
    expect(document.activeElement).toBe(strong);
    expect(strong.isConnected).toBe(true);
    const newest = sortButton(container, "Newest");
    act(() => newest.focus());
    click(newest);
    expect(document.activeElement).toBe(newest);
    const box = switchBox(container);
    act(() => box.focus());
    click(box);
    expect(document.activeElement).toBe(box);
  });
});

describe("a new answer starts over", () => {
  it("returns to All, As returned, details closed and a silent live region when a different answer arrives", () => {
    const first = MIXED();
    const view = draw(first);
    click(chip(view.container, "Poor"));
    click(sortButton(view.container, "Newest"));
    click(switchBox(view.container));
    expect(live(view.container).textContent).not.toBe("");
    view.again(mixedResponse({ Poor: 2 }));
    expect(pressed(fitGroup(view.container))).toEqual(["All 24 results"]);
    expect(pressed(sortGroup(view.container))).toEqual(["As returned"]);
    expect(switchBox(view.container).checked).toBe(false);
    expect(openCount(view.container)).toBe(0);
    expect(live(view.container).textContent).toBe("");
    expect(countLine(view.container)).toBeNull();
    expect(titles(view.container)).toEqual(ALL_TITLES(mixedResponse({ Poor: 2 })));
  });

  it("does not bring a choice back when the next answer has that tier again", () => {
    const view = draw(MIXED());
    click(chip(view.container, "Poor"));
    view.again(mixedResponse({ Poor: 0 }));
    view.again(mixedResponse());
    expect(pressed(fitGroup(view.container))).toEqual(["All 25 results"]);
    expect(titles(view.container)).toEqual(ALL_TITLES(mixedResponse()));
  });

  it("keeps every choice across a change that keeps the same answer, such as tracking a row", () => {
    const result = MIXED();
    const view = draw(result);
    click(chip(view.container, "Good"));
    click(sortButton(view.container, "Best fit"));
    click(detailToggles(view.container)[1]);
    view.again(result, { tracking: new Set([result.scored[0].apply_url]) });
    view.again(result, { tracked: { [result.scored[0].apply_url]: "app-1" } });
    view.again(result, { trackErrors: { [result.scored[0].apply_url]: "No." } });
    expect(pressed(fitGroup(view.container))).toEqual(["Good 6 results"]);
    expect(pressed(sortGroup(view.container))).toEqual(["Best fit"]);
    expect(openCount(view.container)).toBe(1);
  });
});

describe("what it keeps", () => {
  it("writes nothing to any storage while every control is used", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const cookie = vi.spyOn(document, "cookie", "set");
    const open = vi.fn();
    const original = Object.getOwnPropertyDescriptor(window, "indexedDB");
    Object.defineProperty(window, "indexedDB", { configurable: true, value: { open } });
    try {
      const { container } = draw(MIXED());
      for (const label of ["Strong", "Good", "Mixed", "Poor", "Not scored", "All"]) click(chip(container, label));
      for (const label of ["Best fit", "Newest", "As returned"]) click(sortButton(container, label));
      click(switchBox(container));
      click(detailToggles(container)[0]);
      click(switchBox(container));
      expect(setItem).not.toHaveBeenCalled();
      expect(cookie).not.toHaveBeenCalled();
      expect(open).not.toHaveBeenCalled();
    } finally {
      setItem.mockRestore();
      cookie.mockRestore();
      if (original === undefined) delete (window as { indexedDB?: unknown }).indexedDB;
      else Object.defineProperty(window, "indexedDB", original);
    }
  });
});

const sortNames = (container: HTMLElement) => [...sortGroup(container).querySelectorAll("button")].map((button) => words(button));
const printedDays = (container: HTMLElement) => [...container.querySelectorAll(".bj-disc-trail")].map((trail) => /Posted ([A-Z][a-z]{2} \d+)/.exec(words(trail))?.[1] ?? null);

describe("what Newest promises", () => {
  const three = (posted: Array<string | null>) =>
    response({ scored: posted.map((posted_at, index) => job({ apply_url: `https://jobs.example/dated/${index}`, title: `Dated ${index + 1}`, posted_at })) });

  it("is not offered when no row prints a date, so no order is announced that was not made", () => {
    const { container } = draw(three([null, "not a date", "2026-02-31"]));
    expect(sortNames(container)).toEqual(["As returned", "Best fit"]);
    expect(words(container)).not.toContain("Newest");
  });

  it("is not offered for a single dated row, and is for two", () => {
    expect(sortNames(draw(three(["2026-09-01", null, null])).container)).toEqual(["As returned", "Best fit"]);
    expect(sortNames(draw(three(["2026-09-01", "2026-09-02", null])).container)).toEqual(["As returned", "Best fit", "Newest"]);
  });

  it("orders rows by the day each prints in the viewer's zone, so a later printed day is never listed below an earlier one", () => {
    const result = response({
      scored: [
        job({ apply_url: "https://jobs.example/z/older", title: "Older", posted_at: "2026-09-20" }),
        job({ apply_url: "https://jobs.example/z/instant", title: "Instant", posted_at: "2026-09-22T02:00:00Z" }),
        job({ apply_url: "https://jobs.example/z/dateonly", title: "DateOnly", posted_at: "2026-09-22" }),
      ],
    });
    const { container } = draw(result, { timeZone: "America/New_York" });
    click(sortButton(container, "Newest"));
    expect(titles(container)).toEqual(["DateOnly", "Instant", "Older"]);
    expect(printedDays(container)).toEqual(["Sep 22", "Sep 21", "Sep 20"]);
  });
});

describe("the fallback notice and the order", () => {
  const notice = (container: HTMLElement) => words(container.querySelector(".bj-disc-fallback"));

  it("says rows are listed by source quality only while that is the order shown", () => {
    const { container } = draw(mixedResponse({}, { scoring_strategy: "neutral_fallback" }));
    expect(notice(container)).toContain("Rows are listed by source quality, not by fit.");
    click(sortButton(container, "Newest"));
    expect(notice(container)).toContain("these scores are placeholders");
    expect(notice(container)).toContain("Rows are listed newest first, not by fit.");
    expect(notice(container)).not.toContain("source quality");
    click(sortButton(container, "As returned"));
    expect(notice(container)).toContain("Rows are listed by source quality, not by fit.");
  });

  it("draws no placeholder notice, and no sentence about nothing being scored, when the fallback left nothing in the scored list", () => {
    const rows = [1, 2, 3].map((n) => job({ apply_url: `https://jobs.example/past/${n}`, title: `Past ${n}`, score: null }));
    const { container } = draw(response({ scoring_strategy: "neutral_fallback", scored: [], more: rows }));
    expect(container.querySelector(".bj-disc-fallback")).toBeNull();
    expect(words(container)).not.toContain(NONE_SCORED_NOTE);
    expect(words(container)).not.toContain("placeholders");
    expect(titles(container)).toEqual(["Past 1", "Past 2", "Past 3"]);
  });
});

describe("the sorts follow the rows that are shown", () => {
  it("leaves Best fit out while only unscored rows are shown and brings it back, chosen, with All", () => {
    const { container } = draw(MIXED());
    expect(sortNames(container)).toEqual(["As returned", "Best fit", "Newest"]);
    click(sortButton(container, "Best fit"));
    expect(pressed(sortGroup(container))).toEqual(["Best fit"]);
    click(chip(container, "Not scored"));
    expect(sortNames(container)).toEqual(["As returned", "Newest"]);
    expect(pressed(sortGroup(container))).toEqual(["As returned"]);
    expect(titles(container)).toEqual(ALL_TITLES(MIXED()).filter((title) => title.startsWith("Unscored")));
    click(chip(container, "All"));
    expect(sortNames(container)).toEqual(["As returned", "Best fit", "Newest"]);
    expect(pressed(sortGroup(container))).toEqual(["Best fit"]);
  });

  it("leaves Best fit out for a tier that holds one row", () => {
    const { container } = draw(mixedResponse({ Strong: 3, Good: 3, Mixed: 1, Poor: 0, unscored: 0 }));
    click(chip(container, "Mixed"));
    expect(sortNames(container)).not.toContain("Best fit");
    click(chip(container, "Strong"));
    expect(sortNames(container)).toContain("Best fit");
  });
});

describe("a choice that no longer exists in an answer that was changed under it", () => {
  it("falls back to All when the chosen tier has no rows left, and shows every row with no count line", () => {
    const result = mixedResponse();
    const view = draw(result);
    click(chip(view.container, "Poor"));
    expect(pressed(fitGroup(view.container))).toEqual(["Poor 3 results"]);
    expect(rowsOf(view.container)).toHaveLength(3);
    // The same answer object, edited: the view is not told it is a new search.
    result.scored = result.scored.filter((entry) => entry.score?.bin !== "Poor");
    view.again(result);
    expect(pressed(fitGroup(view.container))).toEqual(["All 22 results"]);
    expect(rowsOf(view.container)).toHaveLength(22);
    expect(countLine(view.container)).toBeNull();
  });

  it("falls back to As returned when the chosen sort is no longer on offer", () => {
    const result = mixedResponse();
    const view = draw(result);
    click(sortButton(view.container, "Best fit"));
    expect(pressed(sortGroup(view.container))).toEqual(["Best fit"]);
    result.scored = result.scored.slice(0, 1);
    view.again(result);
    expect(pressed(sortGroup(view.container))).toEqual(["As returned"]);
    expect(sortNames(view.container)).not.toContain("Best fit");
  });
});

describe("accessibility", () => {
  const states: Array<[string, () => DiscoverResponse, (container: HTMLElement) => void]> = [
    ["the toolbar with every detail closed", MIXED, () => undefined],
    ["every detail open through the switch", MIXED, (container) => click(switchBox(container))],
    ["a tier chosen, with its count line and no More results", MIXED, (container) => click(chip(container, "Strong"))],
    ["Not scored chosen", MIXED, (container) => click(chip(container, "Not scored"))],
    ["a sort chosen", MIXED, (container) => click(sortButton(container, "Newest"))],
    ["nothing scored", () => mixedResponse({ Strong: 0, Good: 0, Mixed: 0, Poor: 0, unscored: 4 }), () => undefined],
    ["the scoring fallback with Sort only", () => mixedResponse({}, { scoring_strategy: "neutral_fallback" }), () => undefined],
  ];

  it.each(states)("%s has no serious or critical axe violation", async (_name, build, step) => {
    const { container } = draw(response({ ...build(), warnings: ["brave: down"], dead_removed: 2 }));
    step(container);
    await expectNoSeriousA11yViolations(container);
  });
});
