// @vitest-environment jsdom
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { deriveFirstRun, type FirstRunFacts, type FirstRunFlags, type FirstRunView } from "../lib/firstRun";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { buttonNamed, click, query, render } from "../testing/render";
import { FirstRunChecklistView } from "./FirstRunChecklistView";

// The checklist as a person meets it: a labelled region, an ordered list of five steps each linking
// to where it is done, every state said in words (the glyphs are decoration), a way to hide it that
// never hides it for good, and the two one-line forms that open it again. What is done, and how it
// is presented, is tested in lib/firstRun.test.ts; this pins what the view does with a derived view.

const ok = <T,>(value: T) => ({ kind: "ok", value }) as const;
const failed = { kind: "failed" } as const;

const BRAND_NEW: FirstRunFacts = {
  profile: ok(false),
  modelKey: ok(false),
  savedSearches: ok(0),
  applications: ok({ count: 0, fromDiscover: 0, withResume: 0, resumeTargetId: null }),
};
const FINISHED: FirstRunFacts = {
  profile: ok(true),
  profileActiveSince: "2026-09-12T10:00:00Z",
  modelKey: ok(true),
  savedSearches: ok(1),
  applications: ok({ count: 2, fromDiscover: 0, withResume: 1, resumeTargetId: "app-1" }),
};
const FLAGS: FirstRunFlags = { dismissed: false, searched: false };
const NOW = new Date("2026-10-18T12:00:00Z");

function draw(view: FirstRunView, handlers: { onDismiss?: () => void; onRetry?: () => void } = {}) {
  return render(
    <MemoryRouter>
      <FirstRunChecklistView view={view} onDismiss={handlers.onDismiss ?? (() => {})} onRetry={handlers.onRetry} now={NOW} />
    </MemoryRouter>,
  );
}

const words = (element: Element) => (element.textContent ?? "").replace(/\s+/g, " ").trim();
const steps = (container: HTMLElement) => [...container.querySelectorAll("li.bj-setup-step")];

describe("the open checklist", () => {
  it("is a labelled region holding an ordered list of the five steps, with a count in mono", () => {
    const { container } = draw(deriveFirstRun(BRAND_NEW, FLAGS));
    const section = query(container, "section");
    expect(section.getAttribute("aria-labelledby")).toBe("bj-setup-title");
    expect(words(query(container, "#bj-setup-title"))).toBe("Setup");
    expect(query(container, "ol")).toBeTruthy();
    expect(steps(container)).toHaveLength(5);
    expect(words(query(container, ".bj-setup-count"))).toBe("0 of 5 done");
  });

  it("links each step to the page where it is done, each link at least 44px by its class", () => {
    const { container } = draw(deriveFirstRun(BRAND_NEW, FLAGS));
    const links = [...container.querySelectorAll("a.bj-setup-link")].map((a) => [a.getAttribute("href"), words(a)]);
    expect(links).toEqual([
      ["/profile", "Add your profile"],
      ["/settings", "Add a model key"],
      ["/discover", "Run a first search"],
      ["/applications", "Track a job"],
      ["/applications", "Generate a resume"],
    ]);
  });

  it("says To do in words with a hint, and keeps the glyph out of what a screen reader hears", () => {
    const { container } = draw(deriveFirstRun(BRAND_NEW, FLAGS));
    const first = steps(container)[0];
    expect(words(query(first, ".bj-setup-state"))).toBe("◆To do");
    expect(query(first, ".bj-setup-glyph").getAttribute("aria-hidden")).toBe("true");
    expect(words(query(first, ".bj-setup-note"))).toBe("Import your resume as a profile. Everything else is built from it.");
  });

  it("says Done with the real fact each step returned, and a date from the profile row", () => {
    const { container } = draw(deriveFirstRun(FINISHED, FLAGS));
    click(query(container, "button.bj-setup-line"));
    const notes = steps(container).map((step) => words(query(step, ".bj-setup-note")));
    expect(notes).toEqual(["Active since Sep 12", "Key saved and checked", "1 saved search", "2 tracked", "1 with a resume"]);
    expect(steps(container).map((step) => words(query(step, ".bj-setup-state")))).toEqual(Array(5).fill("✓Done"));
  });

  it("leaves a done step with no fact without a note, rather than inventing one", () => {
    const { container } = draw(deriveFirstRun({ ...FINISHED, profileActiveSince: undefined }, FLAGS));
    click(query(container, "button.bj-setup-line"));
    expect(steps(container)[0].querySelector(".bj-setup-note")).toBeNull();
  });

  it("says Couldn't check, with its own Try again, for a read that failed, and Can't tell yet for none that holds the answer", () => {
    const retry = vi.fn();
    const view = deriveFirstRun({ ...BRAND_NEW, profile: failed }, { dismissed: false, searched: null });
    const { container } = draw(view, { onRetry: retry });
    const [profile, , search] = steps(container);
    expect(words(query(profile, ".bj-setup-state"))).toBe("○Couldn't check");
    click(buttonNamed(profile, "Try again: Add your profile"));
    expect(retry).toHaveBeenCalledTimes(1);
    expect(words(query(search, ".bj-setup-state"))).toBe("○Can't tell yet");
    // Trying again would change nothing for a step no read can answer, so it has no button.
    expect(search.querySelector("button")).toBeNull();
  });

  it("has no Try again when the page gave it nothing to run", () => {
    const view = deriveFirstRun({ ...BRAND_NEW, profile: failed }, FLAGS);
    const { container } = draw(view);
    expect(container.querySelector(".bj-setup-retry")).toBeNull();
  });

  it("draws nothing for a view that is hidden while its reads load", () => {
    const loading = { kind: "loading" } as const;
    const { container } = draw(deriveFirstRun({ profile: loading, modelKey: loading, savedSearches: loading, applications: loading }, FLAGS));
    expect(container.innerHTML).toBe("");
  });

  it("holds no cyan fill: the next-step band holds the page's only one", () => {
    const { container } = draw(deriveFirstRun(BRAND_NEW, FLAGS));
    expect(container.querySelector(".bj-primary, .bj-button-link-primary")).toBeNull();
  });

  it("has no serious accessibility violation", async () => {
    const { container } = draw(deriveFirstRun({ ...BRAND_NEW, profile: failed }, FLAGS), { onRetry: () => {} });
    await expectNoSeriousA11yViolations(container);
  });
});

describe("hiding", () => {
  it("is a quiet button named for what it hides, and remembers the choice for an unfinished account", () => {
    const onDismiss = vi.fn();
    const { container } = draw(deriveFirstRun(BRAND_NEW, FLAGS), { onDismiss });
    const hide = query<HTMLButtonElement>(container, 'button[aria-label="Hide the setup checklist"]');
    expect(hide.className).toContain("bj-quiet");
    expect(words(hide)).toBe("Hide");
    click(hide);
    expect(onDismiss).toHaveBeenCalledTimes(1);
    // It folds to the counting line at once, and the line opens it again.
    expect(container.querySelector("section")).toBeNull();
    const line = query<HTMLButtonElement>(container, "button.bj-setup-line");
    expect(words(line)).toBe("Setup · 5 to do›");
    expect(line.getAttribute("aria-expanded")).toBe("false");
    click(line);
    expect(container.querySelector("section")).not.toBeNull();
  });

  it("only folds the list away for an account that is finished, and remembers nothing", () => {
    const onDismiss = vi.fn();
    const { container } = draw(deriveFirstRun(FINISHED, FLAGS), { onDismiss });
    click(query(container, "button.bj-setup-line"));
    click(buttonNamed(container, "Hide the setup checklist"));
    expect(onDismiss).not.toHaveBeenCalled();
    expect(words(query(container, "button.bj-setup-line"))).toBe("✓Setup ready›");
  });
});

describe("the one-line forms", () => {
  it("is '✓ Setup ready ›' for an account with all five done, and opens on a click", () => {
    const { container } = draw(deriveFirstRun(FINISHED, FLAGS));
    const line = query<HTMLButtonElement>(container, "button.bj-setup-line");
    expect(words(line)).toBe("✓Setup ready›");
    expect(line.getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector("section")).toBeNull();
    click(line);
    expect(steps(container)).toHaveLength(5);
  });

  it("counts what is to do and what cannot be told, and never says ready", () => {
    const view = deriveFirstRun({ ...FINISHED, applications: failed }, FLAGS);
    const { container } = draw(view);
    expect(words(query(container, "button.bj-setup-line"))).toBe("Setup · 2 can't tell›");
    const hidden = deriveFirstRun(BRAND_NEW, { dismissed: true, searched: false });
    const again = draw(hidden);
    expect(words(query(again.container, "button.bj-setup-line"))).toBe("Setup · 5 to do›");
  });

  it("says only that it is checking, and is not a button, while the reads are out for an account this browser saw finished", () => {
    const loading = { kind: "loading" } as const;
    const view = deriveFirstRun(
      { profile: loading, modelKey: loading, savedSearches: loading, applications: loading },
      { dismissed: false, searched: null, seenFinished: true },
    );
    const { container } = draw(view);
    expect(words(query(container, "p.bj-setup-line"))).toBe("Setup · checking");
    expect(container.querySelector("button")).toBeNull();
    expect(container.textContent).not.toContain("ready");
  });

  it("is a 44px line by its class, and has no serious accessibility violation", async () => {
    const { container } = draw(deriveFirstRun(FINISHED, FLAGS));
    await expectNoSeriousA11yViolations(container);
  });
});

describe("keeping a keyboard user's place", () => {
  it("moves focus to the list's heading when the one-line form opens it", () => {
    const { container } = draw(deriveFirstRun(FINISHED, FLAGS));
    const line = query<HTMLButtonElement>(container, "button.bj-setup-line");
    expect(line.getAttribute("aria-expanded")).toBe("false");
    line.focus();
    expect(document.activeElement).toBe(line);
    click(line);
    const heading = query(container, "#bj-setup-title");
    expect(document.activeElement).toBe(heading);
    expect(heading.getAttribute("tabindex")).toBe("-1");
  });

  it("moves focus back to the line when Hide folds a finished account's list away", () => {
    const { container } = draw(deriveFirstRun(FINISHED, FLAGS));
    click(query(container, "button.bj-setup-line"));
    const hide = buttonNamed(container, "Hide the setup checklist");
    hide.focus();
    click(hide);
    const line = query<HTMLButtonElement>(container, "button.bj-setup-line");
    expect(document.activeElement).toBe(line);
    expect(line.getAttribute("aria-expanded")).toBe("false");
  });

  it("moves focus to the line when Hide dismisses an unfinished list too", () => {
    const onDismiss = vi.fn();
    const { container, rerender } = draw(deriveFirstRun(BRAND_NEW, FLAGS), { onDismiss });
    const hide = buttonNamed(container, "Hide the setup checklist");
    hide.focus();
    click(hide);
    expect(onDismiss).toHaveBeenCalledOnce();
    // The hook then reports the list as dismissed; the line takes the list's place.
    rerender(
      <MemoryRouter>
        <FirstRunChecklistView view={deriveFirstRun(BRAND_NEW, { ...FLAGS, dismissed: true })} onDismiss={onDismiss} now={NOW} />
      </MemoryRouter>,
    );
    expect(document.activeElement).toBe(query(container, "button.bj-setup-line"));
  });

  it("does not take focus when the view changes by itself", () => {
    const { container, rerender } = draw(deriveFirstRun(FINISHED, FLAGS));
    const outside = document.createElement("button");
    document.body.append(outside);
    outside.focus();
    rerender(
      <MemoryRouter>
        <FirstRunChecklistView view={deriveFirstRun(BRAND_NEW, FLAGS)} onDismiss={() => {}} now={NOW} />
      </MemoryRouter>,
    );
    expect(container.querySelector("section")).not.toBeNull();
    expect(document.activeElement).toBe(outside);
  });
});
