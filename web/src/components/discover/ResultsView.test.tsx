// @vitest-environment jsdom
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { job, response } from "../../testing/discoverFixtures";
import { buttonNamed, click, installViewport, query, render } from "../../testing/render";
import type { DiscoverResponse } from "../../lib/discoverTypes";
import { ResultsView, type ResultsViewProps } from "./ResultsView";

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
