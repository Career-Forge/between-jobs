// @vitest-environment jsdom
import { act } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { job, mixedResponse, response, score } from "../../testing/discoverFixtures";
import { buttonNamed, click, installViewport, query, render } from "../../testing/render";
import type { DiscoverResponse, JobCard } from "../../lib/discoverTypes";
import { DetailPane, PANE_EMPTY, PANE_LABEL } from "./DetailPane";
import { ResultsView, type ResultsViewProps } from "./ResultsView";

// A finished search on a screen of 1280px and wider: compact rows in a list, and beside it a pane that
// shows one row in full. What is pinned here is which row the pane shows and how that is said, what the
// pane holds for each kind of row, and that a narrower screen draws none of it. The rows' own rules (the
// fits, the sorts, the notices) are ResultsView.test.tsx, which draws the full rows.

const NOW = new Date("2026-09-22T12:00:00Z");

function props(result: DiscoverResponse, overrides: Partial<ResultsViewProps> = {}): ResultsViewProps {
  return { result, now: NOW, tracking: new Set(), tracked: {}, trackErrors: {}, onTrack: vi.fn(), timeZone: "UTC", wide: true, ...overrides };
}

function draw(result: DiscoverResponse, overrides: Partial<ResultsViewProps> = {}) {
  installViewport(overrides.wide === false ? 1000 : 1440);
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

const words = (element: Element | null) => (element?.textContent ?? "").replace(/\s+/g, " ").trim();
const rowsOf = (container: HTMLElement) => [...container.querySelectorAll<HTMLElement>("li.bj-disc-row")];
const pane = (container: HTMLElement) => query(container, "section.bj-disc-pane");
const titleButtons = (container: HTMLElement) => [...container.querySelectorAll<HTMLButtonElement>("button.bj-disc-select")];
const pressedTitles = (container: HTMLElement) => titleButtons(container).filter((button) => button.getAttribute("aria-pressed") === "true").map((button) => words(button));
const paneTitle = (container: HTMLElement) => words(query(pane(container), "h3.bj-disc-title"));
const chip = (container: HTMLElement, label: string) => buttonNamed(query(container, '[role="group"][aria-label="Show results by fit"]'), new RegExp(`^${label} \\d+ results?$`));
const sortButton = (container: HTMLElement, label: string) => buttonNamed(query(container, '[role="group"][aria-label="Sort results"]'), label);

const second = job({ apply_url: "https://jobs.example/acme/2", title: "Analytics Engineer", company: "Initech" });
const third = job({ apply_url: "https://jobs.example/acme/3", title: "ML Engineer", company: "Orbit" });

describe("a narrower screen", () => {
  it("draws full rows and no pane, no title button and no second column", () => {
    const { container } = draw(response({ scored: [job(), second] }), { wide: false });
    expect(container.querySelector(".bj-disc-pane, .bj-disc-split, .bj-disc-row--compact, button.bj-disc-select")).toBeNull();
    expect(rowsOf(container)).toHaveLength(2);
    for (const row of rowsOf(container)) {
      expect(row.querySelector("h3.bj-disc-title button")).toBeNull();
      expect(row.querySelector(".bj-disc-snippet")).not.toBeNull();
      expect(row.querySelector("button.bj-fit-toggle")).not.toBeNull();
      expect(row.querySelector(".bj-disc-trail")).not.toBeNull();
    }
  });

  it("is what is drawn when no width is given at all", () => {
    installViewport(1000);
    const { container } = render(
      <MemoryRouter>
        <ResultsView result={response()} now={NOW} tracking={new Set()} tracked={{}} trackErrors={{}} onTrack={vi.fn()} timeZone="UTC" />
      </MemoryRouter>,
    );
    expect(container.querySelector(".bj-disc-pane")).toBeNull();
    expect(query(container, "li.bj-disc-row").className).not.toContain("bj-disc-row--compact");
  });
});

describe("which row the pane shows", () => {
  it("shows the first row when an answer arrives, and says so on that row's title", () => {
    const { container } = draw(response({ scored: [job(), second, third] }));
    expect(pressedTitles(container)).toEqual(["Data Engineer"]);
    expect(paneTitle(container)).toBe("Data Engineer");
    expect(titleButtons(container).map((button) => button.getAttribute("aria-pressed"))).toEqual(["true", "false", "false"]);
  });

  it("names the pane as the thing each title controls, and the pane is a labelled region", () => {
    const { container } = draw(response({ scored: [job(), second] }));
    const region = pane(container);
    expect(region.id).not.toBe("");
    for (const button of titleButtons(container)) expect(button.getAttribute("aria-controls")).toBe(region.id);
    const label = document.getElementById(region.getAttribute("aria-labelledby") ?? "");
    expect(words(label)).toBe(PANE_LABEL);
    expect(region.contains(label)).toBe(true);
  });

  it("gives each title a name of its own that holds the words on show, and the shared selection class", () => {
    const { container } = draw(response({ scored: [job(), second] }));
    const [first, other] = titleButtons(container);
    expect(first.getAttribute("aria-label")).toBe("Data Engineer at Acme, show details");
    expect(other.getAttribute("aria-label")).toBe("Analytics Engineer at Initech, show details");
    // The name starts with the words on show, so what is seen is what is said first.
    expect(first.getAttribute("aria-label")?.startsWith(words(first))).toBe(true);
    expect(first.classList.contains("bj-selectable")).toBe(true);
    expect(first.type).toBe("button");
    expect(first.parentElement?.tagName).toBe("H3");
  });

  it("moves to a row whose title is pressed, without moving focus and without announcing anything", () => {
    const { container } = draw(mixedResponse());
    const before = [...container.querySelectorAll("[role='status'], [role='alert'], [aria-live]")].map((region) => words(region));
    const target = titleButtons(container)[3];
    act(() => target.focus());
    click(target);
    expect(pressedTitles(container)).toEqual([words(target)]);
    expect(paneTitle(container)).toBe(words(target));
    expect(document.activeElement).toBe(target);
    expect([...container.querySelectorAll("[role='status'], [role='alert'], [aria-live]")].map((region) => words(region))).toEqual(before);
    expect(pane(container).querySelector("[role='status'], [role='alert'], [aria-live]")).toBeNull();
  });

  it("can show a row of More results, and tells the two lists' copies of one posting apart", () => {
    const shared = "https://jobs.example/acme/shared";
    const result = response({ scored: [job({ apply_url: shared })], more: [job({ apply_url: shared, title: "Data Engineer (listed again)", score: null })] });
    const { container } = draw(result);
    expect(pressedTitles(container)).toEqual(["Data Engineer"]);
    click(titleButtons(container)[1]);
    expect(pressedTitles(container)).toEqual(["Data Engineer (listed again)"]);
    expect(paneTitle(container)).toBe("Data Engineer (listed again)");
  });

  it("moves to the first row left when a fit choice hides the chosen one, and stays there when the fit is cleared", () => {
    const { container } = draw(mixedResponse());
    // The first row returned is in the engine's Good bin, which a person reads as Decent.
    expect(paneTitle(container)).toBe("Good 1");
    click(chip(container, "Bad fit"));
    const firstLeft = words(titleButtons(container)[0]);
    expect(firstLeft).not.toBe("Good 1");
    expect(pressedTitles(container)).toEqual([firstLeft]);
    expect(paneTitle(container)).toBe(firstLeft);
    click(chip(container, "All"));
    expect(pressedTitles(container)).toEqual([firstLeft]);
    expect(paneTitle(container)).toBe(firstLeft);
  });

  it("keeps the chosen row through a fit choice that still shows it", () => {
    const { container } = draw(mixedResponse());
    click(buttonNamed(container, "Strong 2 at Acme, show details"));
    click(chip(container, "Good"));
    expect(pressedTitles(container)).toEqual(["Strong 2"]);
    expect(paneTitle(container)).toBe("Strong 2");
  });

  it("keeps the chosen row when a sort moves it", () => {
    const { container } = draw(mixedResponse());
    click(buttonNamed(container, "Mixed 2 at Acme, show details"));
    click(sortButton(container, "Best fit"));
    expect(pressedTitles(container)).toEqual(["Mixed 2"]);
    expect(paneTitle(container)).toBe("Mixed 2");
    expect(words(titleButtons(container)[0])).not.toBe("Mixed 2");
  });

  it("starts again from the first row when a different answer arrives", () => {
    const view = draw(response({ scored: [job(), second, third] }));
    click(titleButtons(view.container)[2]);
    expect(paneTitle(view.container)).toBe("ML Engineer");
    view.again(response({ scored: [third, second] }));
    expect(pressedTitles(view.container)).toEqual(["ML Engineer"]);
    view.again(response({ scored: [second, job()] }));
    expect(pressedTitles(view.container)).toEqual(["Analytics Engineer"]);
    expect(paneTitle(view.container)).toBe("Analytics Engineer");
  });

  it("keeps the chosen row across a re-render that keeps the same answer, as a Track does", () => {
    const result = response({ scored: [job(), second] });
    const view = draw(result);
    click(titleButtons(view.container)[1]);
    view.again(result, { tracking: new Set([second.apply_url]) });
    expect(pressedTitles(view.container)).toEqual(["Analytics Engineer"]);
  });

  it("says so in one plain line when there is no row to show", () => {
    const { container } = render(
      <MemoryRouter>
        <DetailPane id="pane" selected={null} scoringStrategy="direct" now={NOW} trackedApplicationId={null} tracking={false} trackError={null} onTrack={vi.fn()} />
      </MemoryRouter>,
    );
    const region = pane(container);
    expect(words(region)).toBe(`${PANE_LABEL}${PANE_EMPTY}`);
    expect(words(document.getElementById(region.getAttribute("aria-labelledby") ?? ""))).toBe(PANE_LABEL);
    expect(region.querySelector("a, button, h3")).toBeNull();
  });
});

describe("a compact row", () => {
  it("holds the score, the identity and the actions, and leaves the rest to the pane", () => {
    const { container } = draw(response({ scored: [job({ salary_min: 120000, salary_max: 150000, salary_currency: "USD" }), second] }));
    const row = rowsOf(container)[1];
    expect(row.className).toContain("bj-disc-row--compact");
    expect(words(query(row, ".bj-disc-score-num"))).toBe("61");
    expect(query(row, ".bj-ladder-row").getAttribute("aria-label")).toBe("Fit: Decent -- 61 / 100");
    expect([...row.querySelectorAll(".bj-disc-meta")].map((line) => words(line))).toEqual(["Initech -- New York, NY"]);
    expect([...rowsOf(container)[0].querySelectorAll(".bj-disc-meta")].map((line) => words(line))).toEqual(["Acme -- New York, NY", "USD 120,000-150,000"]);
    expect(query(row, "a").getAttribute("href")).toBe(second.apply_url);
    expect(buttonNamed(row, "Track Analytics Engineer at Initech")).not.toBeNull();
    expect(row.querySelector(".bj-disc-snippet, .bj-disc-signal, .bj-fit-oneliner, .bj-fit-preview, .bj-fit-toggle, .bj-fit-detail, .bj-disc-trail")).toBeNull();
  });

  it("keeps every row's controls in the tab order, in reading order: its title, View posting, Track", () => {
    const { container } = draw(response({ scored: [job(), second] }));
    for (const row of rowsOf(container)) {
      const controls = [...row.querySelectorAll<HTMLElement>("a, button")];
      expect(controls.map((control) => words(control).replace(/^(Data|Analytics) Engineer$/, "title"))).toEqual(["title", "View posting", "Track"]);
      for (const control of controls) expect(control.getAttribute("tabindex")).toBeNull();
    }
  });

  it("draws no switch for opening every row's detail: the pane has nothing folded", () => {
    const wide = draw(mixedResponse());
    expect(wide.container.querySelector(".bj-disc-toggle")).toBeNull();
    expect(wide.container.querySelector('[role="group"][aria-label="Show results by fit"]')).not.toBeNull();
    wide.unmount();
    const narrow = draw(mixedResponse(), { wide: false });
    expect(narrow.container.querySelector(".bj-disc-toggle")).not.toBeNull();
  });

  it("says Not scored on a row past the scoring batch, and Placeholder with no number when the scoring fell back", () => {
    const unscored = draw(response({ scored: [job()], more: [job({ apply_url: "https://jobs.example/acme/9", title: "Analyst", score: null })] }));
    const row = rowsOf(unscored.container)[1];
    expect(row.className).toContain("bj-disc-row--unscored");
    expect(words(query(row, ".bj-fit-unscored"))).toBe("Not scored");
    expect(row.querySelector(".bj-disc-score")).toBeNull();
    unscored.unmount();
    const fallback = draw(response({ scored: [job(), second], scoring_strategy: "neutral_fallback" }));
    for (const placeholder of rowsOf(fallback.container)) {
      expect(placeholder.className).toContain("bj-disc-row--placeholder");
      expect(query(placeholder, ".bj-ladder-row").getAttribute("aria-label")).toBe("Fit: Placeholder");
      expect(placeholder.querySelector(".bj-disc-score-num")).toBeNull();
    }
  });

  it("keeps the lists, the More results heading and its note in the left column, with the pane after them", () => {
    const { container } = draw(mixedResponse());
    const split = query(container, ".bj-disc-split");
    expect([...split.children].map((child) => child.className)).toEqual(["bj-disc-split-list", "bj-disc-pane"]);
    const left = split.children[0];
    expect([...left.children].map((child) => child.tagName)).toEqual(["UL", "H2", "P", "UL"]);
    expect(words(query(left, "h2"))).toBe("More results");
    // The heading, the notices and the toolbar stay above both columns.
    expect(query(container, ".bj-disc-toolbar").parentElement).toBe(split.parentElement);
  });
});

describe("what the pane holds", () => {
  it("shows a scored row in full: identity, score, actions, the model's line, every part of the score, the posting text and the trail", () => {
    const full = job({
      salary_min: 120000,
      salary_max: 150000,
      salary_currency: "USD",
      sponsorship_signal: "explicit_yes",
      snippet: "Build and run data pipelines. ".repeat(40).trim(),
    });
    const { container } = draw(response({ scored: [full, second] }));
    const region = pane(container);
    expect(paneTitle(container)).toBe("Data Engineer");
    expect([...region.querySelectorAll(".bj-disc-meta")].map((line) => words(line))).toEqual(["Acme -- New York, NY", "USD 120,000-150,000"]);
    expect(words(query(region, ".bj-disc-score-num"))).toBe("61");
    expect(query(region, ".bj-ladder-row").getAttribute("aria-label")).toBe("Fit: Decent -- 61 / 100");
    expect(words(query(region, ".bj-fit-oneliner"))).toBe("Strong overlap on data pipelines.");
    expect(words(query(region, ".bj-fit-preview"))).toBe("Lowest: Experience 52 -- 2 parts not applied");
    // The detail is open with no button to open it.
    expect(region.querySelector("button.bj-fit-toggle")).toBeNull();
    expect([...region.querySelectorAll("button")].some((button) => /Why this score/.test(button.getAttribute("aria-label") ?? words(button)))).toBe(false);
    const detail = query(region, ".bj-fit-detail");
    expect(words(detail)).toContain("Not applied: Company Health -- company isn't in the tier list");
    expect(words(detail)).toContain("Lowest part: Experience");
    expect(words(detail)).toContain("Location matches -- the model read it as New York, NY");
    // One bar for each part that was applied: the two that were not have a line, not a bar at 0.
    expect([...detail.querySelectorAll(".bj-segbar-label")].map((label) => words(label)).sort()).toEqual(["Experience", "Location", "Skills", "Work Authorization"].sort());
    // The posting's text is whole, and is text.
    expect(words(query(region, ".bj-disc-snippet"))).toBe(full.snippet);
    expect(words(query(region, ".bj-disc-signal"))).toContain("The posting text mentions visa sponsorship");
    expect(words(query(region, ".bj-disc-trail"))).toContain("Link not found closed");
    expect(words(query(region, ".bj-disc-trail"))).toContain("via serper");
    expect(query(region, ".bj-disc-actions a").getAttribute("href")).toBe(full.apply_url);
    expect(buttonNamed(region, "Track Data Engineer at Acme")).not.toBeNull();
    expect([...region.querySelectorAll("h2, h3, h4")].map((heading) => `${heading.tagName} ${words(heading)}`)).toEqual([
      `H2 ${PANE_LABEL}`,
      "H3 Data Engineer",
      "H4 Why this score",
      "H4 Posting",
    ]);
  });

  it("shows a placeholder row with the hollow ladder, the posting text and the actions, and no number, model line or detail", () => {
    const { container } = draw(response({ scored: [job(), second], scoring_strategy: "neutral_fallback" }));
    const region = pane(container);
    expect(query(region, ".bj-ladder-row").getAttribute("aria-label")).toBe("Fit: Placeholder");
    expect(region.querySelector(".bj-disc-score-num, .bj-disc-score-tier, .bj-fit-oneliner, .bj-fit-preview, .bj-fit-detail, .bj-fit-unscored")).toBeNull();
    expect(words(region)).not.toContain("61");
    expect(words(region)).not.toContain("Strong overlap on data pipelines.");
    expect(words(region)).not.toContain("Why this score");
    expect(words(query(region, ".bj-disc-snippet"))).toBe("Build and run data pipelines.");
    expect(buttonNamed(region, "Track Data Engineer at Acme")).not.toBeNull();
    // The notice that the scores are placeholders is above both columns, as it is above full rows.
    expect(words(query(container, ".bj-disc-fallback"))).toContain("these scores are placeholders");
    // The receipt behind the trail names the fallback.
    click(query(region, ".bj-disc-trail button"));
    expect(words(query(region, ".bj-receipt"))).toContain("neutral_fallback");
  });

  it("shows a row that was not scored as Not scored, with nothing of the response's own reading", () => {
    // A bin the engine does not have: the row carries a score object, and none of it is shown.
    const odd = job({ score: score({ bin: "Excellent" as never, score100: 97, one_liner: "A made-up reading." }) });
    const { container } = draw(response({ scored: [odd, second] }));
    const region = pane(container);
    expect(words(query(region, ".bj-fit-unscored"))).toBe("Not scored");
    expect(region.querySelector(".bj-disc-score, .bj-ladder, .bj-fit-oneliner, .bj-fit-preview, .bj-fit-detail")).toBeNull();
    expect(words(region)).not.toContain("97");
    expect(words(region)).not.toContain("A made-up reading.");
    expect(words(region)).not.toContain("Excellent");
    expect(words(query(region, ".bj-disc-snippet"))).toBe("Build and run data pipelines.");
    expect(buttonNamed(region, "Track Data Engineer at Acme")).not.toBeNull();
  });

  it("shows a row past the scoring batch the same way, sponsorship line first", () => {
    const past = job({ apply_url: "https://jobs.example/acme/9", title: "Analyst", score: null, sponsorship_signal: "explicit_no" });
    const { container } = draw(response({ scored: [], more: [past] }));
    const region = pane(container);
    expect(paneTitle(container)).toBe("Analyst");
    expect(words(query(region, ".bj-fit-unscored"))).toBe("Not scored");
    expect([...query(region, ".bj-disc-body").children].map((child) => child.className)).toEqual(["bj-disc-signal", "bj-disc-snippet"]);
  });

  it("links only an http(s) address, in the row and in the pane, and draws the posting's words as text", () => {
    const hostile = job({ apply_url: "javascript:alert(1)", title: "<img src=x onerror=alert(1)>", snippet: "<script>alert(1)</script> Apply now" });
    const { container } = draw(response({ scored: [hostile] }));
    const region = pane(container);
    expect(container.querySelectorAll("a[href^='javascript']").length).toBe(0);
    expect(region.querySelector(".bj-disc-actions a")).toBeNull();
    expect(rowsOf(container)[0].querySelector(".bj-disc-actions a")).toBeNull();
    expect(container.querySelector("script, img")).toBeNull();
    expect(words(query(region, ".bj-disc-snippet"))).toBe("<script>alert(1)</script> Apply now");
    expect(paneTitle(container)).toBe("<img src=x onerror=alert(1)>");
    expect(buttonNamed(region, /^Track /)).not.toBeNull();
  });

  it("leaves out what the posting did not come with: no pay line, no text block, no date", () => {
    const bare = job({ snippet: "", posted_at: null, company: null, location: null });
    const { container } = draw(response({ scored: [bare] }));
    const region = pane(container);
    expect([...region.querySelectorAll(".bj-disc-meta")].map((line) => words(line))).toEqual(["Unknown company -- Location unknown"]);
    expect(region.querySelector(".bj-disc-body")).toBeNull();
    expect([...region.querySelectorAll("h4")].map((heading) => words(heading))).toEqual(["Why this score"]);
    expect(words(query(region, ".bj-disc-trail"))).not.toContain("Posted");
  });
});

describe("tracking from the list and from the pane", () => {
  it("asks for the row on show from the pane's Track, and for any row from its own", () => {
    const onTrack = vi.fn();
    const { container } = draw(response({ scored: [job(), second] }), { onTrack });
    click(buttonNamed(pane(container), "Track Data Engineer at Acme"));
    expect(onTrack.mock.calls.map((call) => (call[0] as JobCard).title)).toEqual(["Data Engineer"]);
    click(buttonNamed(rowsOf(container)[1], "Track Analytics Engineer at Initech"));
    expect(onTrack.mock.calls.map((call) => (call[0] as JobCard).title)).toEqual(["Data Engineer", "Analytics Engineer"]);
    // Pressing a row's Track does not change which row is on show.
    expect(pressedTitles(container)).toEqual(["Data Engineer"]);
  });

  it("offers Open workspace in the row and in the pane once the posting is tracked", () => {
    const first = job();
    const { container } = draw(response({ scored: [first, second] }), { tracked: { [first.apply_url]: "app-7" } });
    for (const place of [rowsOf(container)[0], pane(container)]) {
      expect(query(place, "a[href='/applications/app-7']").getAttribute("aria-label")).toBe("Open workspace for Data Engineer at Acme");
      expect([...place.querySelectorAll("button")].some((button) => /^Track/.test(button.getAttribute("aria-label") ?? ""))).toBe(false);
    }
  });

  it("moves focus to the pane's Open workspace when the pane's Track was the one pressed", () => {
    const first = job();
    const result = response({ scored: [first, second] });
    const view = draw(result);
    const track = buttonNamed(pane(view.container), "Track Data Engineer at Acme");
    act(() => track.focus());
    click(track);
    view.again(result, { tracked: { [first.apply_url]: "app-7" } });
    expect(document.activeElement).toBe(query(pane(view.container), "a[href='/applications/app-7']"));
  });

  it("says Tracking... on both while the request is out, and keeps both in place", () => {
    const first = job();
    const { container } = draw(response({ scored: [first, second] }), { tracking: new Set([first.apply_url]) });
    for (const place of [rowsOf(container)[0], pane(container)]) {
      const busy = buttonNamed(place, "Tracking... Data Engineer at Acme");
      expect(busy.getAttribute("aria-disabled")).toBe("true");
    }
  });

  it("shows a failed Track on the row and in the pane, each with a Retry, and announces it once", () => {
    const onTrack = vi.fn();
    const first = job();
    const { container } = draw(response({ scored: [first, second] }), { trackErrors: { [first.apply_url]: "Too many requests" }, onTrack });
    const inRow = query(rowsOf(container)[0], ".bj-disc-problem .bj-state--failure");
    const inPane = query(pane(container), ".bj-disc-problem .bj-state--failure");
    for (const callout of [inRow, inPane]) expect(words(callout)).toContain("Couldn't track Data Engineer at Acme: Too many requests");
    expect(inRow.getAttribute("role")).toBe("alert");
    expect(inPane.getAttribute("role")).toBeNull();
    expect(container.querySelectorAll("[role='alert']")).toHaveLength(1);
    click(buttonNamed(inPane, "Retry"));
    click(buttonNamed(inRow, "Retry"));
    expect(onTrack).toHaveBeenCalledTimes(2);
    // A row that is not on show keeps its failure to itself.
    expect(rowsOf(container)[1].querySelector(".bj-disc-problem")).toBeNull();
  });
});

describe("accessibility", () => {
  const cases: [string, DiscoverResponse, Partial<ResultsViewProps>][] = [
    ["scored rows and More results", mixedResponse(), {}],
    ["placeholders", mixedResponse({}, { scoring_strategy: "neutral_fallback" }), {}],
    ["a failed Track and a tracked row", response({ scored: [job(), second, third] }), { trackErrors: { [job().apply_url]: "Too many requests" }, tracked: { [second.apply_url]: "app-7" } }],
  ];
  for (const [name, result, overrides] of cases) {
    it(`has no serious violation: ${name}`, async () => {
      const { container } = draw(result, overrides);
      await expectNoSeriousA11yViolations(container);
    });
  }

  it("uses each id once, and every aria reference names something on the page", () => {
    const { container } = draw(mixedResponse());
    const ids = [...container.querySelectorAll("[id]")].map((element) => element.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const attribute of ["aria-controls", "aria-labelledby", "aria-describedby"]) {
      for (const element of container.querySelectorAll(`[${attribute}]`)) {
        for (const id of (element.getAttribute(attribute) ?? "").split(/\s+/)) expect(document.getElementById(id), `${attribute}=${id}`).not.toBeNull();
      }
    }
  });

  it("keeps the headings in order: Results, the rows, More results, then the pane's own", () => {
    const { container } = draw(response({ scored: [job()], more: [job({ apply_url: "https://jobs.example/acme/9", title: "Analyst", score: null })] }));
    expect([...container.querySelectorAll("h2, h3, h4")].map((heading) => `${heading.tagName} ${words(heading)}`)).toEqual([
      "H2 Results",
      "H3 Data Engineer",
      "H2 More results",
      "H3 Analyst",
      `H2 ${PANE_LABEL}`,
      "H3 Data Engineer",
      "H4 Why this score",
      "H4 Posting",
    ]);
  });
});
