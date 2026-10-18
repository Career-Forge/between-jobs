// @vitest-environment jsdom
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { NextStep, NextStepOutcome } from "../../lib/nextStep";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { click, query, render } from "../../testing/render";
import { NEXT_STEP_FOCUS_ID, NextStepBand } from "./NextStepBand";

const step = (overrides: Partial<NextStep> = {}): NextStep => ({
  sentence: "Generate a tailored resume for this posting.",
  action: { kind: "button", label: "Generate resume", intent: "generate" },
  why: ["status saved", "no resume yet", "profile active"],
  ...overrides,
});
const ready = (s: NextStep): NextStepOutcome => ({ status: "ready", step: s });
const band = (outcome: NextStepOutcome, onAction?: (intent: string) => void) => (
  <MemoryRouter>
    <NextStepBand outcome={outcome} onAction={onAction} />
  </MemoryRouter>
);
const text = (element: Element) => (element.textContent ?? "").replace(/\s+/g, " ").trim();
const CYAN_FILLS = ".bj-primary, .bj-button-link-primary";

describe("a lit band", () => {
  it("shows the eyebrow, the sentence, a why line that starts `why:`, and one cyan button", () => {
    const { container } = render(band(ready(step())));
    const section = query(container, 'section[aria-label="Next step"]');
    expect(text(query(section, ".bj-band-eyebrow"))).toBe("Next step");
    expect(text(query(section, ".bj-band-sentence"))).toBe("Generate a tailored resume for this posting.");
    expect(text(query(section, ".bj-band-why"))).toBe("why: status saved · no resume yet · profile active");
    const buttons = section.querySelectorAll(CYAN_FILLS);
    expect(buttons).toHaveLength(1);
    expect(text(buttons[0])).toBe("Generate resume");
    expect(section.classList.contains("bj-band--lit")).toBe(true);
  });

  it("makes a handler action a button that calls the page's handler with the action's intent", () => {
    const onAction = vi.fn();
    const { container } = render(band(ready(step()), onAction));
    const button = query<HTMLButtonElement>(container, "button.bj-primary");
    expect(button.type).toBe("button");
    click(button);
    expect(onAction).toHaveBeenCalledExactlyOnceWith("generate");
  });

  it("makes a route action a link to that route, hash included", () => {
    const { container } = render(
      band(ready(step({ action: { kind: "link", label: "Review the import", to: "/profile#import-review" } }))),
    );
    const link = query<HTMLAnchorElement>(container, "a.bj-button-link-primary");
    expect(link.getAttribute("href")).toBe("/profile#import-review");
    expect(link.classList.contains("bj-button-link")).toBe(true);
    expect(container.querySelector("button")).toBeNull();
  });

  it("makes a form action a submit button outside the form", () => {
    const { container } = render(band(ready(step({ action: { kind: "submit", label: "Sign in", form: "auth-form" } }))));
    const button = query<HTMLButtonElement>(container, "button.bj-primary");
    expect(button.type).toBe("submit");
    expect(button.getAttribute("form")).toBe("auth-form");
  });

  it("opens an external posting in a new tab without handing it the opener", () => {
    const { container } = render(
      band(ready(step({ action: { kind: "external", label: "Open the posting", href: "https://jobs.example.test/123" } }))),
    );
    const link = query<HTMLAnchorElement>(container, "a.bj-button-link-primary");
    expect(link.getAttribute("href")).toBe("https://jobs.example.test/123");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("refuses an external address that is not http(s), such as a scraped javascript: URL", () => {
    for (const href of ["javascript:alert(1)", "data:text/html,x", "//evil.test", "/relative"]) {
      const { container, unmount } = render(band(ready(step({ action: { kind: "external", label: "Open the posting", href } }))));
      expect(container.querySelector("a")).toBeNull();
      expect(container.querySelector(CYAN_FILLS)).toBeNull();
      unmount();
    }
  });

  it("gives the cyan button the id a skip link focuses", () => {
    const { container } = render(band(ready(step())));
    expect(query(container, "button.bj-primary").id).toBe(NEXT_STEP_FOCUS_ID);
  });

  it("holds an outlined secondary beside the cyan one, never a second cyan", () => {
    const onAction = vi.fn();
    const { container } = render(
      band(ready(step({ secondary: { kind: "button", label: "Generate anyway", intent: "force" } })), onAction),
    );
    expect(container.querySelectorAll(CYAN_FILLS)).toHaveLength(1);
    const secondary = [...container.querySelectorAll("button")].find((button) => text(button) === "Generate anyway");
    expect(secondary?.className).toContain("bj-band-secondary");
    expect(secondary?.className).not.toContain("bj-primary");
    click(secondary as Element);
    expect(onAction).toHaveBeenCalledWith("force");
  });

  it("draws the red cross before a failure's sentence, hidden from assistive technology", () => {
    const { container } = render(
      band(ready(step({ tone: "failure", sentence: "Couldn't load Today. You didn't do anything wrong, and nothing was lost.", action: { kind: "button", label: "Try again", intent: "retry" } }))),
    );
    const glyph = query(container, ".bj-band-glyph");
    expect(glyph.textContent).toBe("✕");
    expect(glyph.getAttribute("aria-hidden")).toBe("true");
    expect(text(query(container, ".bj-band-sentence"))).toContain("Couldn't load Today.");
  });

  it("leaves the why line out rather than print an empty `why:`", () => {
    const { container } = render(band(ready(step({ why: [] }))));
    expect(container.querySelector(".bj-band-why")).toBeNull();
  });
});

describe("an unlit band", () => {
  const unlit = ready(step({ sentence: "Nothing needs a decision right now.", action: null, why: ["0 items need a decision"] }));

  it("shows the sentence and the why line and no button of any kind", () => {
    const { container } = render(band(unlit));
    const section = query(container, "section");
    expect(section.classList.contains("bj-band--unlit")).toBe(true);
    expect(text(query(section, ".bj-band-sentence"))).toBe("Nothing needs a decision right now.");
    expect(text(query(section, ".bj-band-why"))).toBe("why: 0 items need a decision");
    expect(section.querySelector("button, a")).toBeNull();
    expect(container.querySelector(CYAN_FILLS)).toBeNull();
  });

  it("lets a skip link focus the sentence, which is not a tab stop", () => {
    const { container } = render(band(unlit));
    const sentence = query(container, ".bj-band-sentence");
    expect(sentence.id).toBe(NEXT_STEP_FOCUS_ID);
    expect(sentence.getAttribute("tabindex")).toBe("-1");
  });

  it("ignores a secondary action: with no cyan button there is nothing to sit beside", () => {
    const { container } = render(band(ready(step({ action: null, secondary: { kind: "button", label: "Generate anyway", intent: "force" } }))));
    expect(container.querySelector("button")).toBeNull();
  });
});

describe("a band with nothing to show", () => {
  it("renders nothing at all when the page has no next step", () => {
    const { container } = render(band({ status: "none" }));
    expect(container.innerHTML).toBe("");
  });

  it("shows only the eyebrow and `Loading...` until its inputs arrive", () => {
    const { container } = render(band({ status: "loading" }));
    const section = query(container, "section");
    expect(section.getAttribute("aria-busy")).toBe("true");
    expect(text(query(section, ".bj-band-eyebrow"))).toBe("Next step");
    expect(text(query(section, ".bj-band-loading"))).toBe("Loading...");
    expect(section.querySelector("button, a, .bj-band-why, .bj-band-sentence")).toBeNull();
  });
});

describe("the one cyan fill", () => {
  it("is the band's alone: a lit band beside quiet content leaves no other element with the primary class", () => {
    const { container } = render(
      <MemoryRouter>
        <NextStepBand outcome={ready(step())} />
        <main>
          <button type="button">Save</button>
          <button type="button" className="bj-quiet">
            Dismiss
          </button>
          <a className="bj-button-link" href="/x">
            Open workspace
          </a>
          <a href="/y">A text link</a>
          <button type="button" className="bj-link-button">
            Link button
          </button>
        </main>
      </MemoryRouter>,
    );
    const fills = [...container.querySelectorAll(CYAN_FILLS)];
    expect(fills).toHaveLength(1);
    expect(fills[0].closest("section.bj-band")).not.toBeNull();
  });

  it("does not light anything on an unlit, loading or empty page", () => {
    for (const outcome of [ready(step({ action: null })), { status: "loading" } as const, { status: "none" } as const]) {
      const { container, unmount } = render(band(outcome));
      expect(container.querySelectorAll(CYAN_FILLS)).toHaveLength(0);
      unmount();
    }
  });
});

describe("the locked button", () => {
  const locked = ready(
    step({
      action: { kind: "button", label: "Confirm and make active", intent: "confirm" },
      locked: { hint: "Accept or edit every found field to unlock this." },
    }),
  );

  it("is a focusable aria-disabled button with a hint, not a cyan fill, and does nothing when pressed", () => {
    const onAction = vi.fn();
    const { container } = render(band(locked, onAction));
    const button = query<HTMLButtonElement>(container, "button.bj-band-locked");
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(button.disabled).toBe(false);
    expect(button.classList.contains("bj-primary")).toBe(false);
    expect(container.querySelectorAll(CYAN_FILLS)).toHaveLength(0);
    expect(text(query(container, `#${button.getAttribute("aria-describedby")}`))).toBe("Accept or edit every found field to unlock this.");
    expect(button.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
    click(button);
    expect(onAction).not.toHaveBeenCalled();
  });
});

describe("when the sentence changes", () => {
  const first = ready(step());
  const second = ready(step({ sentence: "Read the 2 flagged claims, then download.", action: { kind: "button", label: "Download PDF", intent: "download" } }));

  it("announces nothing on the first render: that is the page arriving", () => {
    const { container } = render(band(first));
    const live = query(container, ".bj-band-live");
    expect(live.getAttribute("role")).toBe("status");
    expect(live.getAttribute("aria-live")).toBe("polite");
    expect(live.textContent).toBe("");
    expect(container.querySelector(".bj-band-body--fade")).toBeNull();
  });

  it("announces the new sentence once through a polite live region, and fades it in", () => {
    const view = render(band(first));
    view.rerender(band(second));
    expect(query(view.container, ".bj-band-live").textContent).toBe("Read the 2 flagged claims, then download.");
    expect(view.container.querySelector(".bj-band-body--fade")).not.toBeNull();
  });

  it("does not announce a rerender with the same sentence, or the move from loading to the first step", () => {
    const view = render(band({ status: "loading" }));
    view.rerender(band(first));
    expect(query(view.container, ".bj-band-live").textContent).toBe("");
    view.rerender(band(ready(step({ why: ["status saved"] }))));
    expect(query(view.container, ".bj-band-live").textContent).toBe("");
    expect(view.container.querySelector(".bj-band-body--fade")).toBeNull();
  });
});

describe("accessibility", () => {
  it("has no serious violation lit, unlit, loading, failing or locked", async () => {
    const outcomes: NextStepOutcome[] = [
      ready(step()),
      ready(step({ action: { kind: "link", label: "Go", to: "/x" } })),
      ready(step({ action: null })),
      { status: "loading" },
      ready(step({ tone: "failure" })),
      ready(step({ locked: { hint: "Finish the review." } })),
    ];
    for (const outcome of outcomes) {
      const { container, unmount } = render(band(outcome));
      await expectNoSeriousA11yViolations(container);
      unmount();
    }
  });
});
