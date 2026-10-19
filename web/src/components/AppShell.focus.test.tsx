// @vitest-environment jsdom
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installViewport, query, render } from "../testing/render";
import { AppShell } from "./AppShell";

// The shell is what mounts lib/useFocusClearOfBars.ts: the hook has tests of its own (it mounts itself), but only
// a test that mounts the shell notices the call being dropped, after which a control under the phone's tab bar is
// left hidden when a keyboard reaches it. jsdom has no layout, so the bars' rectangles are stated here.

vi.mock("../lib/useHiringSignalsStatus", () => ({ useHiringSignalsEnabled: () => false }));
vi.mock("../lib/api", () => ({ apiFetch: vi.fn() }));

function setRect(element: Element, top: number, bottom: number) {
  element.getBoundingClientRect = () => ({ top, bottom, left: 0, right: 100, width: 100, height: bottom - top, x: 0, y: top, toJSON: () => ({}) }) as DOMRect;
}

const scrollBy = vi.fn();
const originalRaf = window.requestAnimationFrame;
const originalCancel = window.cancelAnimationFrame;

beforeEach(() => {
  installViewport(375);
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

describe("the shell keeps a keyboard-focused control clear of the phone's bars", () => {
  it("scrolls the page when a field the tab bar covers takes keyboard focus", () => {
    const { container } = render(
      <MemoryRouter initialEntries={["/profile"]}>
        <AppShell email={null} onSignOut={() => undefined}>
          <textarea aria-label="Field" />
        </AppShell>
      </MemoryRouter>,
    );
    setRect(query(container, ".bj-topbar"), 0, 56);
    setRect(query(container, ".bj-tabbar"), 735, 800);
    const field = query<HTMLTextAreaElement>(container, "textarea");
    setRect(field, 698, 777);
    field.focus();
    expect(scrollBy).toHaveBeenCalledWith({ top: 777 - (735 - 8), behavior: "instant" });
  });
});
