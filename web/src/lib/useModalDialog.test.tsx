// @vitest-environment jsdom
import { createPortal } from "react-dom";
import { useRef, useState } from "react";
import { describe, expect, it } from "vitest";
import { buttonNamed, click, keydown, query, render } from "../testing/render";
import { useModalDialog } from "./useModalDialog";

// What the dialog hook owes the keyboard: focus in, focus kept in, Esc, focus back, the page inert.

function Dialog({ onClose, canClose = true, focusSecond = false, returnTo }: { onClose: () => void; canClose?: boolean; focusSecond?: boolean; returnTo?: () => HTMLElement | null }) {
  const root = useRef<HTMLDivElement>(null);
  const second = useRef<HTMLInputElement>(null);
  useModalDialog(root, { onClose, canClose, initialFocus: focusSecond ? () => second.current : undefined, returnFocus: returnTo });
  return createPortal(
    <div ref={root} role="dialog" aria-modal="true" aria-label="Edit" tabIndex={-1}>
      <button type="button">First</button>
      <input ref={second} aria-label="Second" />
      <button type="button">Last</button>
    </div>,
    document.body,
  );
}

function Page({ canClose = true, focusSecond = false, returnTo }: { canClose?: boolean; focusSecond?: boolean; returnTo?: () => HTMLElement | null }) {
  const [open, setOpen] = useState(false);
  return (
    <main>
      <button type="button" onClick={() => setOpen(true)}>
        Open
      </button>
      <button type="button">Behind</button>
      {open && <Dialog canClose={canClose} focusSecond={focusSecond} returnTo={returnTo} onClose={() => setOpen(false)} />}
    </main>
  );
}

const dialog = () => query(document.body, '[role="dialog"]');

describe("useModalDialog", () => {
  it("moves focus to the first control when it opens, or to the one it was told to start on", () => {
    const first = render(<Page />);
    click(buttonNamed(first.container, "Open"));
    expect(document.activeElement).toBe(buttonNamed(dialog(), "First"));
    first.unmount();
    const second = render(<Page focusSecond />);
    click(buttonNamed(second.container, "Open"));
    expect(document.activeElement).toBe(query(dialog(), 'input[aria-label="Second"]'));
  });

  it("keeps Tab inside: from the last control it wraps to the first, and Shift+Tab from the first wraps to the last", () => {
    const { container } = render(<Page />);
    click(buttonNamed(container, "Open"));
    buttonNamed(dialog(), "Last").focus();
    expect(keydown("Tab")).toBe(false);
    expect(document.activeElement).toBe(buttonNamed(dialog(), "First"));
    expect(keydown("Tab", { shift: true })).toBe(false);
    expect(document.activeElement).toBe(buttonNamed(dialog(), "Last"));
  });

  it("leaves Tab alone between two controls inside, so the browser moves on as usual", () => {
    const { container } = render(<Page />);
    click(buttonNamed(container, "Open"));
    expect(keydown("Tab")).toBe(true);
  });

  it("pulls focus back when it lands outside", () => {
    const { container } = render(<Page />);
    click(buttonNamed(container, "Open"));
    buttonNamed(container, "Behind").focus();
    expect(dialog().contains(document.activeElement)).toBe(true);
  });

  it("closes on Esc and gives focus back to what opened it", () => {
    const { container } = render(<Page />);
    const opener = buttonNamed(container, "Open");
    opener.focus();
    click(opener);
    keydown("Escape");
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it("gives focus back to the element it is told to, when that is asked for, and otherwise to what opened it", () => {
    const { container } = render(<Page returnTo={() => buttonNamed(container, "Behind")} />);
    click(buttonNamed(container, "Open"));
    keydown("Escape");
    expect(document.activeElement).toBe(buttonNamed(container, "Behind"));
  });

  it("falls back to what opened it when the element it is told to is not there", () => {
    const { container } = render(<Page returnTo={() => null} />);
    const opener = buttonNamed(container, "Open");
    opener.focus();
    click(opener);
    keydown("Escape");
    expect(document.activeElement).toBe(opener);
  });

  it("does not close on Esc while it is told it cannot (a save is out)", () => {
    const { container } = render(<Page canClose={false} />);
    click(buttonNamed(container, "Open"));
    keydown("Escape");
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
  });

  it("makes the rest of the page inert and unscrollable while open, and restores both on close", () => {
    const { container } = render(<Page />);
    click(buttonNamed(container, "Open"));
    expect(container.hasAttribute("inert")).toBe(true);
    expect(dialog().hasAttribute("inert")).toBe(false);
    expect(document.body.style.overflow).toBe("hidden");
    keydown("Escape");
    expect(container.hasAttribute("inert")).toBe(false);
    expect(document.body.style.overflow).toBe("");
  });
});
