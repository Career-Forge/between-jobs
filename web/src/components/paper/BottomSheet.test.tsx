// @vitest-environment jsdom
import { act, useRef, useState } from "react";
import { describe, expect, it } from "vitest";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { buttonNamed, click, keydown, query, render } from "../../testing/render";
import { BottomSheet } from "./BottomSheet";

function Harness({ extra = false }: { extra?: boolean }) {
  const [open, setOpen] = useState(false);
  const opener = useRef<HTMLButtonElement>(null);
  return (
    <div>
      <main id="page">
        <button ref={opener} type="button" onClick={() => setOpen(true)}>
          Open
        </button>
        <button type="button">Behind</button>
      </main>
      <BottomSheet open={open} onClose={() => setOpen(false)} title="Details" returnFocusTo={opener}>
        <p>Receipt body</p>
        {extra && (
          <>
            <button type="button">First inside</button>
            <button type="button">Last inside</button>
          </>
        )}
      </BottomSheet>
    </div>
  );
}

const dialog = () => query(document.body, '[role="dialog"]');

describe("BottomSheet", () => {
  it("renders nothing while closed and a labelled modal dialog while open", () => {
    const { container } = render(<Harness />);
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    click(buttonNamed(container, "Open"));
    const sheet = dialog();
    expect(sheet.getAttribute("aria-modal")).toBe("true");
    expect(sheet.getAttribute("aria-labelledby")).toBe(query(sheet, "h2").id);
    expect(query(sheet, "h2").textContent).toBe("Details");
    expect(sheet.textContent).toContain("Receipt body");
  });

  it("moves focus to Close when it opens", () => {
    const { container } = render(<Harness />);
    click(buttonNamed(container, "Open"));
    expect(document.activeElement).toBe(buttonNamed(dialog(), "Close"));
  });

  it("closes on Esc and gives focus back to what opened it", () => {
    const { container } = render(<Harness />);
    const opener = buttonNamed(container, "Open");
    opener.focus();
    click(opener);
    keydown("Escape");
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it("gives focus back to the opener even when the click never focused it (Safari, Firefox on a Mac)", () => {
    const { container } = render(<Harness />);
    const opener = buttonNamed(container, "Open");
    expect(document.activeElement).toBe(document.body);
    click(opener);
    click(buttonNamed(dialog(), "Close"));
    expect(document.activeElement).toBe(opener);
  });

  it("closes from the Close button and from a press on the dimmed area", () => {
    const { container } = render(<Harness />);
    click(buttonNamed(container, "Open"));
    click(buttonNamed(dialog(), "Close"));
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    click(buttonNamed(container, "Open"));
    click(query(document.body, ".bj-sheet-scrim"));
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });

  it("traps Tab: from the last stop it wraps to the first, and Shift+Tab from the first to the last", () => {
    const { container } = render(<Harness extra />);
    click(buttonNamed(container, "Open"));
    const stops = [buttonNamed(dialog(), "Close"), buttonNamed(dialog(), "First inside"), buttonNamed(dialog(), "Last inside")];
    expect(document.activeElement).toBe(stops[0]);
    stops[2].focus();
    expect(keydown("Tab")).toBe(false);
    expect(document.activeElement).toBe(stops[0]);
    expect(keydown("Tab", { shift: true })).toBe(false);
    expect(document.activeElement).toBe(stops[2]);
    // A Tab from a middle stop is left to the browser.
    stops[1].focus();
    expect(keydown("Tab")).toBe(true);
  });

  it("keeps focus on Close when it is the only stop", () => {
    const { container } = render(<Harness />);
    click(buttonNamed(container, "Open"));
    keydown("Tab");
    expect(document.activeElement).toBe(buttonNamed(dialog(), "Close"));
    keydown("Tab", { shift: true });
    expect(document.activeElement).toBe(buttonNamed(dialog(), "Close"));
  });

  it("pulls focus back in when it lands outside the sheet", () => {
    const { container } = render(<Harness />);
    click(buttonNamed(container, "Open"));
    act(() => buttonNamed(container, "Behind").focus());
    expect(dialog().contains(document.activeElement)).toBe(true);
  });

  it("makes the page inert and stops it scrolling while open, and restores both", () => {
    const { container } = render(<Harness />);
    document.body.style.overflow = "scroll";
    click(buttonNamed(container, "Open"));
    expect(container.hasAttribute("inert")).toBe(true);
    expect(dialog().closest("[inert]")).toBeNull();
    expect(document.body.style.overflow).toBe("hidden");
    click(buttonNamed(dialog(), "Close"));
    expect(container.hasAttribute("inert")).toBe(false);
    expect(document.body.style.overflow).toBe("scroll");
  });

  it("leaves alone an element that was already inert", () => {
    const { container } = render(<Harness />);
    const other = document.createElement("div");
    other.setAttribute("inert", "");
    document.body.append(other);
    click(buttonNamed(container, "Open"));
    click(buttonNamed(dialog(), "Close"));
    expect(other.hasAttribute("inert")).toBe(true);
  });

  it("has no serious accessibility violation", async () => {
    const { container } = render(<Harness extra />);
    click(buttonNamed(container, "Open"));
    await expectNoSeriousA11yViolations(document.body.querySelector(".bj-sheet-root") as Element);
  });
});
