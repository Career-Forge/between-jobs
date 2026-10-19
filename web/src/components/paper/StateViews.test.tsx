// @vitest-environment jsdom
import { act, useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { buttonNamed, click, query, render } from "../../testing/render";
import { EmptyState, FailureState, LoadingState, PartialState, SetupRequiredState, StaleNote } from "./StateViews";

const text = (element: Element) => (element.textContent ?? "").replace(/\s+/g, " ").trim();
const view = (ui: React.ReactNode) => render(<MemoryRouter>{ui}</MemoryRouter>);

describe("LoadingState", () => {
  it("says Loading... to a screen reader and draws static placeholder rows, hidden from it", () => {
    const { container } = view(<LoadingState />);
    const region = query(container, '[role="status"]');
    expect(region.getAttribute("aria-busy")).toBe("true");
    expect(text(region)).toBe("Loading...");
    const rows = region.querySelectorAll(".bj-skeleton-row");
    expect(rows).toHaveLength(3);
    expect(rows[0].parentElement?.getAttribute("aria-hidden")).toBe("true");
  });

  it("is not a live region when the page already says the same thing (announce off), and stays aria-busy", () => {
    const { container } = view(<LoadingState announce={false} label="Searching..." />);
    expect(container.querySelector('[role="status"]')).toBeNull();
    expect(query(container, ".bj-state--loading").getAttribute("aria-busy")).toBe("true");
  });

  it("reserves the height of the real rows up front, so content arriving shifts nothing", () => {
    const { container } = view(<LoadingState rows={4} rowHeight={72} label="Loading header..." />);
    const rows = [...container.querySelectorAll<HTMLElement>(".bj-skeleton-row")];
    expect(rows).toHaveLength(4);
    expect(rows.every((row) => row.style.height === "72px")).toBe(true);
    expect(text(container)).toBe("Loading header...");
  });

  it("has no serious accessibility violation", async () => {
    const { container } = view(<LoadingState />);
    await expectNoSeriousA11yViolations(container);
  });
});

describe("EmptyState", () => {
  it("names what will appear and links on to the place that fills it, as a text link", () => {
    const { container } = view(
      <EmptyState heading="Nothing tracked yet" link={{ label: "Paste a posting", to: "/applications#start-tracking" }}>
        One card per job appears here, with its status.
      </EmptyState>,
    );
    expect(text(query(container, "h2"))).toBe("Nothing tracked yet");
    expect(text(container)).toContain("One card per job appears here, with its status.");
    const link = query<HTMLAnchorElement>(container, "a");
    expect(link.getAttribute("href")).toBe("/applications#start-tracking");
    expect(container.querySelector("button")).toBeNull();
    expect(container.querySelector(".bj-primary, .bj-button-link-primary")).toBeNull();
  });

  it("is just the explanation when there is nowhere to go", () => {
    const { container } = view(<EmptyState heading="No contacts yet">Run a search to find them.</EmptyState>);
    expect(container.querySelector("a")).toBeNull();
  });

  it("has no serious accessibility violation", async () => {
    const { container } = view(
      <EmptyState heading="Nothing tracked yet" link={{ label: "Paste a posting", to: "/applications" }}>
        Explained.
      </EmptyState>,
    );
    await expectNoSeriousA11yViolations(container);
  });
});

describe("PartialState", () => {
  it("names what did not arrive, each in its own words, under the lead", () => {
    const { container } = view(
      <PartialState lead="2 of this run's searches failed:" notices={["Brave couldn't complete that search.", "Serper couldn't complete that search."]} />,
    );
    expect(text(query(container, ".bj-state-lead"))).toBe("2 of this run's searches failed:");
    expect([...container.querySelectorAll("li")].map(text)).toEqual([
      "Brave couldn't complete that search.",
      "Serper couldn't complete that search.",
    ]);
  });

  it("renders nothing when nothing is missing", () => {
    expect(view(<PartialState notices={[]} />).container.innerHTML).toBe("");
  });

  it("has no serious accessibility violation", async () => {
    const { container } = view(<PartialState lead="Lead" notices={["One"]} />);
    await expectNoSeriousA11yViolations(container);
  });
});

describe("StaleNote", () => {
  it("shows the age only when a returned field carried one", () => {
    const withDate = view(<StaleNote when="Sep 20">Restored from an earlier run</StaleNote>);
    expect(text(withDate.container)).toBe("Restored from an earlier run · Sep 20");
    expect(query(withDate.container, ".bj-trail-mono").textContent).toBe("Sep 20");
    for (const when of [undefined, null, ""]) {
      const without = view(<StaleNote when={when}>From an earlier successful generation</StaleNote>);
      expect(text(without.container)).toBe("From an earlier successful generation");
    }
  });
});

describe("SetupRequiredState", () => {
  it("says what is missing and gives every missing thing a link to where it is fixed", () => {
    const { container } = view(
      <SetupRequiredState
        message="Discover scores jobs against your profile with your model."
        missing={[
          { label: "Add your profile", to: "/profile" },
          { label: "Add a model key", to: "/settings" },
        ]}
      />,
    );
    expect(text(query(container, "p"))).toBe("Discover scores jobs against your profile with your model.");
    expect(text(query(container, ".bj-state-missing span"))).toBe("Missing:");
    expect([...container.querySelectorAll("a")].map(text)).toEqual(["Add your profile", "Add a model key"]);
    expect([...container.querySelectorAll("a")].map((link) => link.getAttribute("href"))).toEqual(["/profile", "/settings"]);
  });

  it("is a plain callout, not an alarm: setup is a to-do, not a failure", () => {
    const { container } = view(<SetupRequiredState message="Needs a key." missing={[{ label: "Add", to: "/settings" }]} />);
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it("requires at least one link in its type", () => {
    // @ts-expect-error -- setup-required always carries an actionable link
    const bad = <SetupRequiredState message="x" missing={[]} />;
    expect(bad).toBeDefined();
  });

  it("has no serious accessibility violation", async () => {
    const { container } = view(<SetupRequiredState message="Needs a key." missing={[{ label: "Add", to: "/settings" }]} />);
    await expectNoSeriousA11yViolations(container);
  });
});

describe("FailureState", () => {
  it("names what could not be read as an alert, with the red cross hidden from assistive technology", () => {
    const { container } = view(<FailureState message="Couldn't load your applications." />);
    const alert = query(container, '[role="alert"]');
    expect(text(alert)).toBe("✕Couldn't load your applications.".replace("✕", "✕"));
    expect(query(alert, ".bj-state-glyph").getAttribute("aria-hidden")).toBe("true");
  });

  it("offers a Retry only when the block has its own, and calls it", () => {
    const retry = vi.fn();
    const own = view(<FailureState message="Couldn't read the header." onRetry={retry} />);
    click(buttonNamed(own.container, "Retry"));
    expect(retry).toHaveBeenCalledOnce();
    const page = view(<FailureState message="Couldn't read the page." />);
    expect(page.container.querySelector("button")).toBeNull();
  });

  it("takes its own retry label", () => {
    const { container } = view(<FailureState message="Couldn't run the search." onRetry={() => undefined} retryLabel="Search again" />);
    expect(buttonNamed(container, "Search again")).toBeDefined();
  });

  it("draws Retry as a secondary button, never a cyan fill", () => {
    const { container } = view(<FailureState message="x" onRetry={() => undefined} />);
    expect(container.querySelector(".bj-primary, .bj-button-link-primary")).toBeNull();
  });

  describe("restoreFocusTo", () => {
    // A section whose Retry clears its own failure, with a control that stays.
    function Section({ restore = true }: { restore?: boolean }) {
      const [failed, setFailed] = useState(true);
      return (
        <div>
          <button type="button">Run</button>
          {failed && <FailureState message="It failed." onRetry={() => setFailed(false)} restoreFocusTo={restore ? () => document.querySelector<HTMLElement>("button[data-keep]") : undefined} />}
          <button type="button" data-keep>
            Stays
          </button>
        </div>
      );
    }
    const settleMicrotasks = () => act(async () => { await Promise.resolve(); });

    it("hands focus to a control that stays when Retry removes the callout it was pressed in", async () => {
      const { container } = view(<Section />);
      const retry = buttonNamed(container, "Retry");
      retry.focus();
      click(retry);
      await settleMicrotasks();
      expect(container.querySelector("[role='alert']")).toBeNull();
      expect(document.activeElement).toBe(query(container, "button[data-keep]"));
    });

    it("leaves focus alone when it was not inside the callout", async () => {
      const { container } = view(<Section />);
      const run = buttonNamed(container, "Run");
      run.focus();
      click(buttonNamed(container, "Retry"));
      await settleMicrotasks();
      expect(document.activeElement).toBe(run);
    });

    it("moves nothing when no target was named", async () => {
      const { container } = view(<Section restore={false} />);
      const retry = buttonNamed(container, "Retry");
      retry.focus();
      click(retry);
      await settleMicrotasks();
      expect(document.activeElement).toBe(document.body);
    });
  });

  it("has no serious accessibility violation", async () => {
    const { container } = view(<FailureState message="Couldn't read it." onRetry={() => undefined} />);
    await expectNoSeriousA11yViolations(container);
  });
});
