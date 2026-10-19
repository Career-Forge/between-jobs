// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useFocusClearOfBars } from "./useFocusClearOfBars";
import { render } from "../testing/render";

// jsdom has no layout, so every rectangle here is stated by the test; what is checked is that the hook
// reads the bars, works out the scroll and applies it only where it should.

function Probe({ jump = false }: { jump?: boolean }) {
  useFocusClearOfBars();
  return (
    <div>
      <header className="bj-topbar" />
      {jump && <nav className="bj-jump bj-jump--sticky" />}
      <main>
        <textarea aria-label="Field" />
        <button type="button">Plain button</button>
        <div style={{ position: "sticky" }}>
          <textarea aria-label="Sticky field" />
        </div>
        <dialog open>
          <textarea aria-label="Modal field" />
        </dialog>
      </main>
      <nav className="bj-tabbar" />
    </div>
  );
}

function setRect(element: Element, top: number, bottom: number) {
  element.getBoundingClientRect = () => ({ top, bottom, left: 0, right: 100, width: 100, height: bottom - top, x: 0, y: top, toJSON: () => ({}) }) as DOMRect;
}

const scrollBy = vi.fn();
const originalRaf = window.requestAnimationFrame;
const originalCancel = window.cancelAnimationFrame;

beforeEach(() => {
  scrollBy.mockClear();
  window.scrollBy = scrollBy as unknown as typeof window.scrollBy;
  window.requestAnimationFrame = ((callback: FrameRequestCallback) => {
    callback(0);
    return 1;
  }) as typeof window.requestAnimationFrame;
  window.cancelAnimationFrame = () => undefined;
  Object.defineProperty(window, "innerHeight", { configurable: true, value: 800 });
});

afterEach(() => {
  window.requestAnimationFrame = originalRaf;
  window.cancelAnimationFrame = originalCancel;
});

function mount(bars: { top?: [number, number]; tab?: [number, number]; jump?: [number, number] } = { top: [0, 56], tab: [735, 800] }) {
  const { container } = render(<Probe jump={bars.jump !== undefined} />);
  const topbar = container.querySelector(".bj-topbar")!;
  const tabbar = container.querySelector(".bj-tabbar")!;
  if (bars.top) setRect(topbar, bars.top[0], bars.top[1]);
  if (bars.tab) setRect(tabbar, bars.tab[0], bars.tab[1]);
  const jump = container.querySelector(".bj-jump");
  if (jump !== null && bars.jump) setRect(jump, bars.jump[0], bars.jump[1]);
  return { container, field: container.querySelector<HTMLElement>("textarea[aria-label='Field']")!, topbar, tabbar };
}

describe("keyboard focus landing under the phone's bars", () => {
  it("scrolls a field that is half under the tab bar up until it is clear of it", () => {
    const { field } = mount();
    setRect(field, 698, 777);
    field.focus();
    expect(scrollBy).toHaveBeenCalledWith({ top: 777 - (735 - 8), behavior: "instant" });
  });

  it("scrolls a field under the top bar down", () => {
    const { field } = mount();
    setRect(field, 30, 100);
    field.focus();
    expect(scrollBy).toHaveBeenCalledWith({ top: 30 - (56 + 8), behavior: "instant" });
  });

  it("leaves a field that is already clear alone", () => {
    const { field } = mount();
    setRect(field, 300, 380);
    field.focus();
    expect(scrollBy).not.toHaveBeenCalled();
  });

  // From 1280px the workspace has one sticky section bar at the top of the page and no phone bars.
  it("counts a stuck section bar as a top bar, and a field under it is scrolled clear of it", () => {
    const { field } = mount({ top: [0, 0], jump: [0, 60] });
    setRect(field, 20, 90);
    field.focus();
    expect(scrollBy).toHaveBeenCalledWith({ top: 20 - (60 + 8), behavior: "instant" });
  });

  it("stacks a section bar that sticks under the phone's top bar on top of it", () => {
    const { field } = mount({ top: [0, 56], tab: [735, 800], jump: [56, 108] });
    setRect(field, 70, 140);
    field.focus();
    expect(scrollBy).toHaveBeenCalledWith({ top: 70 - (108 + 8), behavior: "instant" });
  });

  it("ignores a section bar that has not stuck yet: it is part of the page further down, over nothing", () => {
    const { field } = mount({ top: [0, 0], jump: [400, 460] });
    setRect(field, 20, 90);
    field.focus();
    expect(scrollBy).not.toHaveBeenCalled();
  });

  it("does nothing on a wide screen, where both bars are display:none", () => {
    const { field, topbar, tabbar } = mount();
    (topbar as HTMLElement).style.display = "none";
    (tabbar as HTMLElement).style.display = "none";
    setRect(field, 698, 777);
    field.focus();
    expect(scrollBy).not.toHaveBeenCalled();
  });

  it("does nothing when a bar has no size (not laid out)", () => {
    const { field } = mount({ top: [0, 0], tab: [800, 800] });
    setRect(field, 698, 777);
    field.focus();
    expect(scrollBy).not.toHaveBeenCalled();
  });

  it("does not scroll for a control that is sticky, or inside a modal: scrolling the page would not move it", () => {
    const { container } = mount();
    for (const label of ["Sticky field", "Modal field"]) {
      const element = container.querySelector<HTMLElement>(`textarea[aria-label='${label}']`)!;
      setRect(element, 698, 777);
      element.focus();
    }
    expect(scrollBy).not.toHaveBeenCalled();
  });

  it("does not move the page for a button the person pressed with a pointer", () => {
    const { container } = mount();
    const button = container.querySelector<HTMLElement>("button")!;
    setRect(button, 698, 777);
    vi.spyOn(button, "matches").mockImplementation((selector: string) => selector !== ":focus-visible"); // a click on a button is not keyboard focus
    button.focus();
    expect(scrollBy).not.toHaveBeenCalled();
  });

  it("stops listening when the shell goes away", () => {
    const { field } = mount();
    document.body.innerHTML = "";
    setRect(field, 698, 777);
    field.dispatchEvent(new FocusEvent("focusin", { bubbles: true }));
    expect(scrollBy).not.toHaveBeenCalled();
  });
});
