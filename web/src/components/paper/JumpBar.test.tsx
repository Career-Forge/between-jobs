// @vitest-environment jsdom
import { act } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { click, installViewport, query, render } from "../../testing/render";
import { JumpBar, type JumpGroup } from "./JumpBar";

const GROUPS: JumpGroup[] = [
  {
    label: "Resume",
    links: [
      { id: "ch-sources", label: "Tailor" },
      { id: "ch-draft", label: "Draft" },
      { id: "generate", label: "Generate" },
      { id: "ch-check", label: "Review" },
    ],
  },
  {
    label: "Research",
    links: [
      { id: "company-intel", label: "Company intel" },
      { id: "contacts", label: "Contacts" },
    ],
  },
  { label: "Prepare", links: [{ id: "your-play", label: "Your play" }] },
];
const ALL_IDS = GROUPS.flatMap((group) => group.links.map((link) => link.id));

function Where() {
  const location = useLocation();
  return <output data-where="">{`${location.pathname}${location.search}${location.hash}`}</output>;
}

function Page({ groups = GROUPS, ready = true, entry = "/applications/7" }: { groups?: JumpGroup[]; ready?: boolean; entry?: string }) {
  return (
    <MemoryRouter initialEntries={[entry]}>
      <button type="button">Elsewhere</button>
      <JumpBar groups={groups} ready={ready} />
      <Where />
      <main>
        {ALL_IDS.map((id) => (
          <section key={id} id={id}>
            <h2 tabIndex={-1}>{`Heading of ${id}`}</h2>
          </section>
        ))}
      </main>
    </MemoryRouter>
  );
}

const where = (root: ParentNode) => query(root, "[data-where]").textContent;
const links = (root: ParentNode) => [...root.querySelectorAll<HTMLAnchorElement>("a.bj-jump-link")];
const current = (root: ParentNode) => [...root.querySelectorAll('[aria-current="location"]')].map((element) => element.textContent);

// Each section's top edge, as the browser would report it; jsdom has no layout.
let tops: Record<string, number> = {};
let scrolls: { id: string; options: ScrollIntoViewOptions }[] = [];

function scrollTo(position: Record<string, number>) {
  tops = position;
  act(() => {
    window.dispatchEvent(new Event("scroll"));
  });
}

// Section tops for a viewer who has read down to `id` (it sits at the top of the screen, the
// sections after it below, the ones before it above).
function readingAt(id: string) {
  const at = ALL_IDS.indexOf(id);
  return Object.fromEntries(ALL_IDS.map((section, index) => [section, 100 + (index - at) * 400]));
}

class FakeObserver {
  static latest: FakeObserver | null = null;
  constructor(private callback: IntersectionObserverCallback) {
    FakeObserver.latest = this;
  }
  observe() {}
  disconnect() {}
  unobserve() {}
  takeRecords() {
    return [];
  }
  report(isIntersecting: boolean, top: number) {
    act(() => this.callback([{ isIntersecting, boundingClientRect: { top } } as IntersectionObserverEntry], this as unknown as IntersectionObserver));
  }
}

beforeEach(() => {
  tops = Object.fromEntries(ALL_IDS.map((id, index) => [id, 600 + index * 400]));
  scrolls = [];
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    callback(0);
    return 1;
  });
  vi.stubGlobal("cancelAnimationFrame", () => undefined);
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const top = tops[this.id] ?? 0;
    return { top, bottom: top + 300, left: 0, right: 0, width: 0, height: 300, x: 0, y: top, toJSON: () => ({}) };
  });
  Element.prototype.scrollIntoView = function (this: Element, options?: boolean | ScrollIntoViewOptions) {
    scrolls.push({ id: this.id, options: options as ScrollIntoViewOptions });
  };
  FakeObserver.latest = null;
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("JumpBar at 1280px and up", () => {
  it("is a labelled navigation of three labelled groups of real links, in the page's order", () => {
    installViewport(1280);
    const { container } = render(<Page />);
    const nav = query(container, 'nav[aria-label="Sections on this page"]');
    expect(nav.classList.contains("bj-jump--sticky")).toBe(true);
    const groups = [...nav.querySelectorAll('[role="group"]')];
    expect(groups.map((group) => group.getAttribute("aria-labelledby")).every((id) => id !== null && nav.querySelector(`#${CSS.escape(id)}`) !== null)).toBe(true);
    expect(groups.map((group) => group.querySelector(".bj-jump-label")?.textContent)).toEqual(["Resume", "Research", "Prepare"]);
    expect(links(nav).map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      ["Tailor", "#ch-sources"],
      ["Draft", "#ch-draft"],
      ["Generate", "#generate"],
      ["Review", "#ch-check"],
      ["Company intel", "#company-intel"],
      ["Contacts", "#contacts"],
      ["Your play", "#your-play"],
    ]);
    // The labels are not targets.
    expect(nav.querySelectorAll(".bj-jump-label a")).toHaveLength(0);
    expect(nav.querySelector("select")).toBeNull();
  });

  it("outlines each group as one shared segment group and uses the shared selection class", () => {
    installViewport(1280);
    const { container } = render(<Page />);
    expect(container.querySelectorAll(".bj-segmented.bj-jump-segments")).toHaveLength(3);
    for (const link of links(container)) expect(link.classList.contains("bj-selectable")).toBe(true);
  });

  it("leaves out a group with no links, and draws nothing when no group has any (the hiring-posts link while the feature is off)", () => {
    installViewport(1280);
    const { container, rerender } = render(<Page groups={[GROUPS[0], { label: "Research", links: [] }]} />);
    expect(container.querySelectorAll('[role="group"]')).toHaveLength(1);
    rerender(<Page groups={[{ label: "Resume", links: [] }]} />);
    expect(container.querySelector("nav")).toBeNull();
  });

  it("marks the section being read with aria-current=location, exactly one, and nothing before the first is reached", () => {
    installViewport(1280);
    const { container } = render(<Page />);
    expect(current(container)).toEqual([]);
    scrollTo(readingAt("ch-draft"));
    expect(current(container)).toEqual(["Draft"]);
    scrollTo(readingAt("contacts"));
    expect(current(container)).toEqual(["Contacts"]);
    scrollTo(readingAt("ch-sources"));
    expect(current(container)).toEqual(["Tailor"]);
    scrollTo(Object.fromEntries(ALL_IDS.map((id, index) => [id, 900 + index * 400])));
    expect(current(container)).toEqual([]);
  });

  it("marks the last section at the very bottom of the page, where a short last section never gets its top high enough", () => {
    installViewport(1280);
    const { container } = render(<Page />);
    Object.defineProperty(document.documentElement, "scrollHeight", { configurable: true, value: 5000 });
    Object.defineProperty(window, "scrollY", { configurable: true, value: 5000 - window.innerHeight });
    scrollTo(readingAt("contacts"));
    expect(current(container)).toEqual(["Your play"]);
    Reflect.deleteProperty(document.documentElement, "scrollHeight");
    Reflect.deleteProperty(window, "scrollY");
  });

  it("only marks: it never moves keyboard focus and never scrolls the page itself", () => {
    installViewport(1280);
    const { container } = render(<Page />);
    const elsewhere = query<HTMLButtonElement>(container, "button");
    elsewhere.focus();
    scrollTo(readingAt("generate"));
    scrollTo(readingAt("your-play"));
    expect(document.activeElement).toBe(elsewhere);
    expect(scrolls).toEqual([]);
  });

  it("follows a link: the hash changes, the page scrolls smoothly to the section, and focus lands on its heading", () => {
    installViewport(1280);
    const { container } = render(<Page />);
    click(links(container)[2]);
    expect(where(container)).toBe("/applications/7#generate");
    expect(scrolls).toEqual([{ id: "generate", options: { behavior: "smooth", block: "start" } }]);
    expect(document.activeElement?.textContent).toBe("Heading of generate");
  });

  it("keeps the path and the query string when it moves to a hash", () => {
    installViewport(1280);
    const { container } = render(<Page entry="/applications/7?from=today" />);
    click(links(container)[0]);
    expect(where(container)).toBe("/applications/7?from=today#ch-sources");
  });

  it("scrolls again when the link for the section already in the address is pressed again", () => {
    installViewport(1280);
    const { container } = render(<Page entry="/applications/7#generate" />);
    expect(scrolls).toHaveLength(1);
    click(links(container)[2]);
    expect(scrolls).toHaveLength(2);
    expect(where(container)).toBe("/applications/7#generate");
    click(links(container)[3]);
    expect(scrolls.map((scroll) => scroll.id)).toEqual(["generate", "generate", "ch-check"]);
  });

  it("scrolls instantly when the person asked for less motion", () => {
    const viewport = installViewport(1280);
    void viewport;
    const base = window.matchMedia;
    window.matchMedia = ((query: string) => {
      const list = base(query);
      return query === "(prefers-reduced-motion: reduce)" ? ({ ...list, matches: true } as MediaQueryList) : list;
    }) as typeof window.matchMedia;
    const { container } = render(<Page />);
    click(links(container)[1]);
    expect(scrolls).toEqual([{ id: "ch-draft", options: { behavior: "auto", block: "start" } }]);
  });

  it("leaves a modified click (new tab, new window) to the browser", () => {
    installViewport(1280);
    const { container } = render(<Page />);
    const event = new MouseEvent("click", { bubbles: true, cancelable: true, ctrlKey: true });
    act(() => {
      links(container)[0].dispatchEvent(event);
    });
    expect(event.defaultPrevented).toBe(false);
    expect(where(container)).toBe("/applications/7");
  });

  it("scrolls to a hash already in the address as soon as the page says its sections are there, not before", () => {
    installViewport(1280);
    const { rerender } = render(<Page entry="/applications/7#ch-check" ready={false} />);
    expect(scrolls).toEqual([]);
    rerender(<Page entry="/applications/7#ch-check" ready />);
    expect(scrolls.map((scroll) => scroll.id)).toEqual(["ch-check"]);
  });

  it("does not watch the scroll while the sections are not there yet", () => {
    installViewport(1280);
    const { container } = render(<Page ready={false} />);
    scrollTo(readingAt("ch-draft"));
    expect(current(container)).toEqual([]);
  });

  it("has no serious accessibility violations", async () => {
    installViewport(1280);
    const { container } = render(<Page />);
    scrollTo(readingAt("ch-draft"));
    await expectNoSeriousA11yViolations(container);
  });
});

describe("JumpBar from 960px to 1279px", () => {
  it("is the same bar, wrapping, not sticky", () => {
    installViewport(1024);
    const { container } = render(<Page />);
    const nav = query(container, "nav.bj-jump--bar");
    expect(nav.classList.contains("bj-jump--sticky")).toBe(false);
    expect(links(container)).toHaveLength(7);
    expect(container.querySelector(".bj-jump--dock")).toBeNull();
  });

  it("docks a 'Jump to' row once the bar has scrolled off the top, and takes it away when the bar is back", () => {
    vi.stubGlobal("IntersectionObserver", FakeObserver);
    installViewport(1024);
    const { container } = render(<Page />);
    expect(container.querySelector(".bj-jump--dock")).toBeNull();
    FakeObserver.latest?.report(false, -120);
    const dock = query(container, ".bj-jump--dock");
    expect(query(dock, "label").textContent).toBe("Jump to");
    expect(dock.querySelectorAll("optgroup")).toHaveLength(3);
    FakeObserver.latest?.report(true, 10);
    expect(container.querySelector(".bj-jump--dock")).toBeNull();
  });

  it("does not dock while the bar is merely below the fold", () => {
    vi.stubGlobal("IntersectionObserver", FakeObserver);
    installViewport(1024);
    const { container } = render(<Page />);
    FakeObserver.latest?.report(false, 900);
    expect(container.querySelector(".bj-jump--dock")).toBeNull();
  });

  it("the docked row shows the section in view and follows its choice like the bar", () => {
    vi.stubGlobal("IntersectionObserver", FakeObserver);
    installViewport(1024);
    const { container } = render(<Page />);
    FakeObserver.latest?.report(false, -300);
    scrollTo(readingAt("company-intel"));
    const select = query<HTMLSelectElement>(container, ".bj-jump--dock select");
    expect(select.value).toBe("company-intel");
    expect(current(container).sort()).toEqual(["Company intel", "Company intel"]);
    act(() => {
      select.value = "ch-check";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(where(container)).toBe("/applications/7#ch-check");
    expect(scrolls.map((scroll) => scroll.id)).toEqual(["ch-check"]);
  });

  it("never docks in a browser without IntersectionObserver", () => {
    installViewport(1024);
    const { container } = render(<Page />);
    expect(container.querySelector(".bj-jump--dock")).toBeNull();
  });

  it("never docks at 1280px, where the bar itself is sticky", () => {
    vi.stubGlobal("IntersectionObserver", FakeObserver);
    installViewport(1280);
    const { container } = render(<Page />);
    expect(FakeObserver.latest).toBeNull();
    expect(container.querySelector(".bj-jump--dock")).toBeNull();
  });

  it("has no serious accessibility violations, docked or not", async () => {
    vi.stubGlobal("IntersectionObserver", FakeObserver);
    installViewport(1024);
    const { container } = render(<Page />);
    await expectNoSeriousA11yViolations(container);
    FakeObserver.latest?.report(false, -50);
    await expectNoSeriousA11yViolations(container);
  });
});

describe("JumpBar under 960px", () => {
  it("is a 'Jump to' select with a visible label, one optgroup per group, the bar's own labels as options", () => {
    installViewport(375);
    const { container } = render(<Page />);
    const nav = query(container, 'nav[aria-label="Sections on this page"]');
    expect(nav.classList.contains("bj-jump--select")).toBe(true);
    expect(container.querySelectorAll("a.bj-jump-link")).toHaveLength(0);
    const label = query<HTMLLabelElement>(nav, "label");
    expect(label.textContent).toBe("Jump to");
    const select = query<HTMLSelectElement>(nav, "select");
    expect(label.htmlFor).toBe(select.id);
    expect([...select.querySelectorAll("optgroup")].map((group) => group.label)).toEqual(["Resume", "Research", "Prepare"]);
    expect([...select.querySelectorAll("option")].map((option) => [option.textContent, option.value])).toEqual(
      GROUPS.flatMap((group) => group.links.map((link) => [link.label, link.id])),
    );
  });

  it("shows the first section until one is reached, then the section in view, and marks that option", () => {
    installViewport(375);
    const { container } = render(<Page />);
    const select = query<HTMLSelectElement>(container, "select");
    expect(select.value).toBe("ch-sources");
    expect(current(container)).toEqual([]);
    scrollTo(readingAt("contacts"));
    expect(select.value).toBe("contacts");
    expect(current(container)).toEqual(["Contacts"]);
  });

  it("choosing an option moves to its hash, scrolls there and focuses its heading", () => {
    installViewport(375);
    const { container } = render(<Page />);
    const select = query<HTMLSelectElement>(container, "select");
    act(() => {
      select.value = "your-play";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(where(container)).toBe("/applications/7#your-play");
    expect(scrolls).toEqual([{ id: "your-play", options: { behavior: "smooth", block: "start" } }]);
    expect(document.activeElement?.textContent).toBe("Heading of your-play");
  });

  it("is sticky and carries no horizontally scrolling chip strip", () => {
    installViewport(375);
    const { container } = render(<Page />);
    expect(container.querySelector(".bj-jump--sticky")).toBeNull();
    expect(container.querySelectorAll("nav.bj-jump--select select")).toHaveLength(1);
  });

  it("switches between the bar and the select as the width changes", () => {
    const viewport = installViewport(1280);
    const { container } = render(<Page />);
    expect(links(container)).toHaveLength(7);
    viewport.setWidth(600);
    expect(links(container)).toHaveLength(0);
    expect(container.querySelector("select")).not.toBeNull();
    viewport.setWidth(1100);
    expect(links(container)).toHaveLength(7);
    expect(container.querySelector("select")).toBeNull();
  });

  it("has no serious accessibility violations", async () => {
    installViewport(375);
    const { container } = render(<Page />);
    scrollTo(readingAt("ch-draft"));
    await expectNoSeriousA11yViolations(container);
  });
});
