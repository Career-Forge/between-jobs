// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { render } from "../testing/render";
import { useScrollToHash } from "./useScrollToHash";

function Probe({ hash }: { hash: string }) {
  useScrollToHash(true, hash);
  return (
    <div>
      <button type="button">before</button>
      <section id="ai-provider">
        <h2 tabIndex={-1}>AI provider</h2>
      </section>
      <section id="plain">
        <h2>Not focusable</h2>
      </section>
      <h3 id="own" tabIndex={-1}>
        A heading that is the target
      </h3>
    </div>
  );
}

function reduceMotion(reduce: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: (query: string) => ({ matches: reduce && query === "(prefers-reduced-motion: reduce)", addEventListener: () => undefined, removeEventListener: () => undefined }),
  });
}

let scrolled: ScrollIntoViewOptions[] = [];

beforeEach(() => {
  scrolled = [];
  Element.prototype.scrollIntoView = function (options?: boolean | ScrollIntoViewOptions) {
    scrolled.push(options as ScrollIntoViewOptions);
  };
  reduceMotion(false);
});

afterEach(() => {
  Reflect.deleteProperty(window, "matchMedia");
});

describe("arriving by a link to part of a page", () => {
  it("scrolls smoothly, and puts keyboard focus on the heading of the section it points at", () => {
    render(<Probe hash="#ai-provider" />);
    expect(scrolled).toEqual([{ behavior: "smooth", block: "start" }]);
    expect(document.activeElement?.textContent).toBe("AI provider");
  });

  it("focuses the target when it is itself a heading", () => {
    render(<Probe hash="#own" />);
    expect(document.activeElement?.textContent).toBe("A heading that is the target");
  });

  it("does not scroll with animation when the person asked for less motion", () => {
    reduceMotion(true);
    render(<Probe hash="#ai-provider" />);
    expect(scrolled).toEqual([{ behavior: "auto", block: "start" }]);
  });

  it("leaves focus alone when the heading has not asked to be focusable", () => {
    render(<Probe hash="#plain" />);
    expect(scrolled).toHaveLength(1);
    expect(document.activeElement).toBe(document.body);
  });
});
