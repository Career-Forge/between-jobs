// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Link, MemoryRouter, useLocation } from "react-router-dom";
import { click, query, render } from "../testing/render";
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

describe("following an in-page link, as the landing page's header does", () => {
  function Page() {
    const { hash, key } = useLocation();
    useScrollToHash(true, hash, key);
    return (
      <div>
        <nav>
          <Link to={{ hash: "#circuit" }}>Product</Link>
          <Link to={{ hash: "#nowhere" }}>Nowhere</Link>
        </nav>
        <section id="circuit">
          <h2 id="circuit-title" tabIndex={-1}>
            Circuit
          </h2>
        </section>
      </div>
    );
  }
  const open = (entry: string) =>
    render(
      <MemoryRouter initialEntries={[entry]}>
        <Page />
      </MemoryRouter>,
    ).container;
  const link = (root: HTMLElement, name: string) => [...root.querySelectorAll("a")].find((a) => a.textContent === name) as HTMLAnchorElement;

  it("scrolls and focuses the heading on arrival, and does nothing for no fragment or an id that is not there", () => {
    const root = open("/#circuit");
    expect(scrolled).toEqual([{ behavior: "smooth", block: "start" }]);
    expect(document.activeElement).toBe(query(root, "#circuit-title"));
    scrolled = [];
    open("/");
    open("/#nowhere");
    expect(scrolled).toEqual([]);
  });

  it("moves focus on a click, and scrolls again when the same link is followed twice", () => {
    const root = open("/");
    click(link(root, "Product"));
    expect(document.activeElement).toBe(query(root, "#circuit-title"));
    click(link(root, "Product"));
    expect(scrolled).toHaveLength(2);
  });

  it("leaves the page where it is for a link to an id that is not there", () => {
    const root = open("/");
    click(link(root, "Nowhere"));
    expect(scrolled).toEqual([]);
  });
});
