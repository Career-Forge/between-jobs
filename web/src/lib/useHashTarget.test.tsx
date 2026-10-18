// @vitest-environment jsdom
import { Link, MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { click, query, render } from "../testing/render";
import { useHashTarget } from "./useHashTarget";

// An in-page link has to scroll to its section and put focus on the section's heading, on arrival and on a
// click, and again when the same link is followed twice. The scroll is instant under prefers-reduced-motion.

function Page() {
  useHashTarget();
  return (
    <div>
      <nav>
        <Link to={{ hash: "#circuit" }}>Product</Link>
        <Link to={{ hash: "#how" }}>How it works</Link>
        <Link to={{ hash: "#nowhere" }}>Nowhere</Link>
        <Link to={{ hash: "#bare" }}>Bare</Link>
      </nav>
      <section id="circuit">
        <h2 id="circuit-title">Circuit</h2>
        <p>text</p>
      </section>
      <section id="how">
        <p>no heading in here</p>
      </section>
      <h2 id="bare">A heading that is the target itself</h2>
    </div>
  );
}

const scrollIntoView = vi.fn();
let reducedMotion = false;

beforeEach(() => {
  scrollIntoView.mockReset();
  reducedMotion = false;
  Element.prototype.scrollIntoView = scrollIntoView;
  window.matchMedia = ((q: string) => ({ matches: reducedMotion && q.includes("prefers-reduced-motion"), media: q })) as unknown as typeof window.matchMedia;
});

afterEach(() => {
  document.body.innerHTML = "";
});

const open = (entry: string) =>
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Page />
    </MemoryRouter>,
  ).container;

const link = (root: HTMLElement, name: string) => [...root.querySelectorAll("a")].find((a) => a.textContent === name) as HTMLAnchorElement;

describe("arriving at an address with a fragment", () => {
  it("scrolls to the section and focuses its first heading, which becomes focusable without joining the tab order", () => {
    const root = open("/#circuit");
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
    const heading = query(root, "#circuit-title");
    expect(document.activeElement).toBe(heading);
    expect(heading.getAttribute("tabindex")).toBe("-1");
  });

  it("does nothing for no fragment, an id that is not on the page, or an address that cannot be decoded", () => {
    for (const entry of ["/", "/#nowhere", "/#%E0%A4%A"]) {
      scrollIntoView.mockClear();
      expect(() => open(entry)).not.toThrow();
      expect(scrollIntoView, entry).not.toHaveBeenCalled();
    }
  });

  it("scrolls without animation for someone who asked their system for less motion", () => {
    reducedMotion = true;
    open("/#circuit");
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "auto", block: "start" });
  });
});

describe("following an in-page link", () => {
  it("moves focus to the section it names, and to the section itself when it has no heading", () => {
    const root = open("/");
    click(link(root, "Product"));
    expect(document.activeElement).toBe(query(root, "#circuit-title"));
    click(link(root, "How it works"));
    expect(document.activeElement).toBe(query(root, "#how"));
    expect(query(root, "#how").getAttribute("tabindex")).toBe("-1");
  });

  it("treats a target that is itself a heading as the heading", () => {
    const root = open("/");
    click(link(root, "Bare"));
    expect(document.activeElement).toBe(query(root, "#bare"));
  });

  it("scrolls again when the same link is followed twice", () => {
    const root = open("/");
    click(link(root, "Product"));
    click(link(root, "Product"));
    expect(scrollIntoView).toHaveBeenCalledTimes(2);
  });

  it("leaves the page where it is for a link to an id that is not there", () => {
    const root = open("/");
    click(link(root, "Nowhere"));
    expect(scrollIntoView).not.toHaveBeenCalled();
  });
});
