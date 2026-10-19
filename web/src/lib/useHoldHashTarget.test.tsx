// @vitest-environment jsdom
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "../testing/render";
import { HOLD_LIMIT_MS, useHoldHashTarget } from "./useHoldHashTarget";

function Probe({ hash, ready = true }: { hash: string; ready?: boolean }) {
  useHoldHashTarget(ready, hash);
  return <section id="company-intel">Company intel</section>;
}

let callbacks: (() => void)[] = [];
let disconnected = 0;
let scrolled: { id: string; options: unknown }[] = [];

beforeEach(() => {
  callbacks = [];
  disconnected = 0;
  scrolled = [];
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback: () => void) {
        callbacks.push(callback);
      }
      observe() {}
      disconnect() {
        disconnected += 1;
      }
    },
  );
  Element.prototype.scrollIntoView = function (this: Element, options?: boolean | ScrollIntoViewOptions) {
    scrolled.push({ id: this.id, options });
  };
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const resized = () => act(() => callbacks.forEach((callback) => callback()));

describe("holding a deep link's section in view while the page above it grows", () => {
  it("ignores the size the observer reports on starting, then brings the target back to the top at each later change", () => {
    render(<Probe hash="#company-intel" />);
    resized();
    expect(scrolled).toEqual([]);
    resized();
    resized();
    expect(scrolled).toEqual([
      { id: "company-intel", options: { block: "start", behavior: "instant" } },
      { id: "company-intel", options: { block: "start", behavior: "instant" } },
    ]);
  });

  it.each(["wheel", "touchstart", "pointerdown", "keydown"])("hands over for good when the person takes over with a %s", (type) => {
    render(<Probe hash="#company-intel" />);
    resized();
    act(() => {
      window.dispatchEvent(new Event(type));
    });
    expect(disconnected).toBeGreaterThan(0);
  });

  it("stops at a hard cap, and does nothing with no hash, before the page is ready, or for a target that is not there", () => {
    vi.useFakeTimers();
    render(<Probe hash="#company-intel" />);
    const before = disconnected;
    act(() => {
      vi.advanceTimersByTime(HOLD_LIMIT_MS);
    });
    expect(disconnected).toBeGreaterThan(before);

    callbacks = [];
    render(<Probe hash="" />);
    render(<Probe hash="#company-intel" ready={false} />);
    expect(callbacks).toEqual([]);

    render(<Probe hash="#nothing-here" />);
    resized();
    resized();
    expect(scrolled.filter((entry) => entry.id === "nothing-here")).toEqual([]);
  });

  it("starts over for a new hash and lets go of the old observer", () => {
    const { rerender } = render(<Probe hash="#company-intel" />);
    const before = disconnected;
    rerender(<Probe hash="#contacts" />);
    expect(disconnected).toBeGreaterThan(before);
  });
});
