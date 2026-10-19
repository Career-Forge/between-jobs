// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { findForbiddenWord } from "../../lib/trail";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { click, keydown, query, render } from "../../testing/render";
import { ChecklistGroup, ChecklistRow, ChecklistSummary, type ChecklistState } from "./ChecklistRow";

const text = (element: Element) => (element.textContent ?? "").replace(/\s+/g, " ").trim();
const STATES: ChecklistState[] = ["pass", "fail", "not_checked"];

describe("ChecklistRow", () => {
  it("says each state as a word and a mark: ✓ Pass, ✕ Fail, ○ Not checked", () => {
    const seen = STATES.map((state) => {
      const { container, unmount } = render(<ChecklistRow as="div" state={state} label="One page" />);
      const chip = query(container, ".bj-checkrow-state");
      const result = [text(chip), query(chip, "[aria-hidden='true']").className, chip.classList.contains(`bj-checkrow-state--${state}`)] as const;
      unmount();
      return result;
    });
    expect(seen.map(([words]) => words)).toEqual(["✓ Pass", "✕ Fail", "○ Not checked"]);
    expect(seen.map(([, glyph]) => glyph)).toEqual([
      "bj-trail-glyph bj-trail-glyph--emerald",
      "bj-trail-glyph bj-trail-glyph--danger",
      "bj-trail-glyph bj-trail-glyph--muted",
    ]);
    expect(seen.every(([, , flagged]) => flagged)).toBe(true);
  });

  it("is never a tick box and never a control: nothing in it takes a click or a key on its own", () => {
    for (const state of STATES) {
      const { container, unmount } = render(<ChecklistRow state={state} label="Links work" hint="Open each one." />);
      expect(container.querySelector("input, button, a, [tabindex], [role='checkbox'], [role='button']")).toBeNull();
      unmount();
    }
  });

  it("is never a pass for a row that was not checked, whatever else it says", () => {
    const { container } = render(<ChecklistRow as="div" state="not_checked" label="Fonts are embedded" hint="Open the PDF's properties." />);
    expect(container.textContent).not.toContain("✓");
    expect(container.textContent).not.toMatch(/\bpass/i);
    expect(container.querySelector(".bj-trail-glyph--emerald")).toBeNull();
  });

  it("shows the do-it-yourself hint and the caveat, each as its own line", () => {
    const { container } = render(
      <ChecklistRow as="div" state="pass" label="One page" hint="Count the pages." caveat="This check assumes one page; your settings allow two." />,
    );
    expect([...container.querySelectorAll(".bj-checkrow-note")].map(text)).toEqual(["Count the pages.", "This check assumes one page; your settings allow two."]);
  });

  it("leaves out the hint, caveat and actions it was not given", () => {
    const { container } = render(<ChecklistRow as="div" state="pass" label="One page" />);
    expect(container.querySelector(".bj-checkrow-note")).toBeNull();
    expect(container.querySelector(".bj-checkrow-actions")).toBeNull();
  });

  it("reaches only what is put in actions, with the keyboard", () => {
    let pressed = 0;
    const { container } = render(
      <ul>
        <ChecklistRow
          state="fail"
          label="No unsupported claims"
          actions={
            <button type="button" onClick={() => (pressed += 1)}>
              Show the flagged claim
            </button>
          }
        />
      </ul>,
    );
    const stops = container.querySelectorAll<HTMLElement>("button, a, [tabindex]");
    expect(stops).toHaveLength(1);
    stops[0].focus();
    expect(document.activeElement).toBe(stops[0]);
    click(stops[0]);
    expect(pressed).toBe(1);
    expect(keydown("Tab")).toBe(true);
  });

  it("never says 'verified'", () => {
    for (const state of STATES) {
      const { container, unmount } = render(<ChecklistRow as="div" state={state} label="One page" />);
      expect(findForbiddenWord(text(container))).toBeNull();
      unmount();
    }
  });
});

describe("ChecklistGroup", () => {
  const COMPUTED = [
    { id: "one_page", state: "pass" as const, label: "One page" },
    { id: "no_engine_warnings", state: "pass" as const, label: "No engine warnings" },
    { id: "no_unsupported_claims", state: "fail" as const, label: "No unsupported claims" },
  ];

  it("is a named list under a heading that counts its rows, one li per row, in order", () => {
    const { container } = render(<ChecklistGroup title="Checked by Between Jobs" rows={COMPUTED} />);
    const heading = query(container, "h3");
    expect(text(heading)).toBe("Checked by Between Jobs (3)");
    const list = query(container, "ul");
    expect(list.getAttribute("aria-label")).toBe("Checked by Between Jobs");
    expect([...list.querySelectorAll("li .bj-checkrow-label")].map(text)).toEqual(["One page", "No engine warnings", "No unsupported claims"]);
  });

  it("takes its heading level from the page", () => {
    const { container } = render(<ChecklistGroup title="Yours to check" level={4} rows={[]} />);
    expect(container.querySelectorAll("h4")).toHaveLength(1);
    expect(text(query(container, "h4"))).toBe("Yours to check (0)");
  });

  it("shows a fail on one row without touching the others", () => {
    const { container } = render(<ChecklistGroup title="Checked by Between Jobs" rows={COMPUTED} />);
    expect([...container.querySelectorAll(".bj-checkrow-state")].map(text)).toEqual(["✓ Pass", "✓ Pass", "✕ Fail"]);
  });

  it("has no serious accessibility violations with both groups and the summary", async () => {
    const mine = [{ id: "links_valid", state: "not_checked" as const, label: "Links work", hint: "Open each link in the PDF." }];
    const { container } = render(
      <main>
        <ChecklistSummary states={[...COMPUTED, ...mine].map((row) => row.state)} />
        <ChecklistGroup title="Checked by Between Jobs" rows={COMPUTED} />
        <ChecklistGroup title="Yours to check" rows={mine} />
      </main>,
    );
    await expectNoSeriousA11yViolations(container);
  });
});

describe("ChecklistSummary", () => {
  it("says counts only", () => {
    const states: ChecklistState[] = ["pass", "pass", "pass", "pass", "pass", "fail", "not_checked", "not_checked", "not_checked", "not_checked"];
    const { container } = render(<ChecklistSummary states={states} />);
    expect(text(query(container, "p"))).toBe("5 pass · 1 fail · 4 not checked");
  });
});
