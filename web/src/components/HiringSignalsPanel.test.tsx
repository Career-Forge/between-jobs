import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { initialPanelState } from "../lib/hiringSignalsPanelModel";
import { HiringSignalsPanel } from "./HiringSignalsPanel";
import { HiringSignalsPanelView, type PanelActions } from "./HiringSignalsPanelView";

// The real API client pulls in the Supabase client, which throws at import time
// without env vars, and nothing in a static render should reach a network
// anyway. (vi.mock is hoisted above the import.)
vi.mock("../lib/api", () => ({ apiFetch: vi.fn() }));

// A static render shows the panel as it first appears: effects do not run, so
// this pins the idle wording and controls -- the copy rules from the design --
// and nothing about the async flows. Those live where the logic lives:
// hiringSignalsPanelModel.test.ts (every guard and race, against a fetcher
// the test controls) and HiringSignalsPanelView.test.tsx (what each state
// renders and what pressing each control calls).

function visibleText(html: string): string {
  return html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");
}

const NO_ACTIONS: PanelActions = {
  setFreshness: () => {},
  search: () => {},
  toggleEmbed: () => {},
  save: () => {},
  unsave: () => {},
  reloadSaves: () => {},
};

const NOW = new Date("2026-09-19T12:00:00Z");

// The panel once the first saved-posts reply has arrived and nothing else has
// happened: the "idle" panel.
function renderIdle(): string {
  return renderToStaticMarkup(
    <HiringSignalsPanelView
      state={initialPanelState({ savesStatus: "ready" })}
      now={NOW}
      actions={NO_ACTIONS}
    />,
  );
}

describe("HiringSignalsPanel, idle", () => {
  it("is titled plainly and explains itself in one line, naming the user's own provider", () => {
    const html = renderIdle();
    expect(html).toContain("<h2>Hiring posts</h2>");
    expect(visibleText(html)).toContain(
      "Recent public hiring posts about this company, found through the search provider you connected.",
    );
  });

  it("never uses the source site's name or marks in anything a person can see or a browser can load", () => {
    const html = renderIdle();
    expect(html).not.toMatch(/linkedin/i);
    expect(html).not.toMatch(/<img|<svg|<iframe|<a /i);
  });

  it("offers one primary action, 'Find hiring posts', and it is enabled", () => {
    const html = renderIdle();
    expect(html).toMatch(/<button[^>]*class="bj-primary"[^>]*>Find hiring posts<\/button>/);
    expect((html.match(/class="bj-primary"/g) ?? []).length).toBe(1);
    // enabled in BOTH senses: neither the attribute nor the ARIA state
    expect(html).not.toContain("disabled");
  });

  it("offers 24 hours, 3 days and 7 days as a labelled group, defaulting to 7 days", () => {
    const html = renderIdle();
    expect(html).toContain('role="group" aria-label="How recent"');
    const order = ["24 hours", "3 days", "7 days"].map((label) => html.indexOf(`>${label}<`));
    expect(order.every((i) => i > -1)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    // The selection is announced (aria-pressed) and bold, never color alone.
    expect(html).toMatch(/aria-pressed="false"[^>]*>24 hours</);
    expect(html).toMatch(/aria-pressed="false"[^>]*>3 days</);
    expect(html).toMatch(/class="bj-toggle-active"[^>]*aria-pressed="true"[^>]*>7 days</);
  });

  it("has an always-mounted polite live region, so later results are announced", () => {
    expect(renderIdle()).toMatch(/role="status" aria-live="polite"/);
  });

  it("shows no results, no saved-posts section and no embed before anything has happened", () => {
    const html = renderIdle();
    expect(html).not.toContain("Saved posts");
    expect(html).not.toContain("<li");
    expect(html).not.toContain("bj-error");
    expect(html).not.toContain("Searching");
  });
});

describe("HiringSignalsPanel, connected", () => {
  it("renders nothing until the first saved-posts reply, so a server with the feature off never shows it (F5)", () => {
    // A static render runs no effects: no reply has arrived, which is exactly
    // the first moment on a real page.
    expect(
      renderToStaticMarkup(<HiringSignalsPanel applicationId="6b1f0c1e-0000-4000-8000-0000000000aa" />),
    ).toBe("");
  });
});
