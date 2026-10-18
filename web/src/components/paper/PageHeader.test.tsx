// @vitest-environment jsdom
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { query, render } from "../../testing/render";
import { PageHeader } from "./PageHeader";

const text = (element: Element) => (element.textContent ?? "").replace(/\s+/g, " ").trim();
const view = (ui: React.ReactNode) => render(<MemoryRouter>{ui}</MemoryRouter>);

describe("PageHeader", () => {
  it("has exactly one h1, the title, and the subtitle in a paragraph after it", () => {
    const { container } = view(<PageHeader title="Applications" subtitle="From your tracked jobs" />);
    const headings = container.querySelectorAll("h1");
    expect(headings).toHaveLength(1);
    expect(headings[0].textContent).toBe("Applications");
    expect(container.querySelectorAll("h2, h3")).toHaveLength(0);
    expect(text(query(container, "p.bj-page-subtitle"))).toBe("From your tracked jobs");
    expect(headings[0].compareDocumentPosition(query(container, "p.bj-page-subtitle")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("draws a breadcrumb as a labelled list: links up the trail, the current page last and not a link", () => {
    const { container } = view(
      <PageHeader
        title="Northstar Labs"
        breadcrumb={[{ label: "Applications", to: "/applications" }, { label: "Northstar Labs" }]}
      />,
    );
    const nav = query(container, 'nav[aria-label="Breadcrumb"]');
    const items = [...nav.querySelectorAll("li")];
    expect(items.map(text)).toEqual(["Applications", "Northstar Labs"]);
    expect(items[0].querySelector("a")?.getAttribute("href")).toBe("/applications");
    expect(items[1].querySelector("a")).toBeNull();
    expect(items[1].querySelector('[aria-current="page"]')?.textContent).toBe("Northstar Labs");
    expect(nav.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
  });

  it("never links the last crumb, even if it was given a route", () => {
    const { container } = view(<PageHeader title="X" breadcrumb={[{ label: "Home", to: "/" }, { label: "Here", to: "/here" }]} />);
    expect(container.querySelectorAll("nav a")).toHaveLength(1);
  });

  it("leaves out the breadcrumb, subtitle and actions it was not given", () => {
    const { container } = view(<PageHeader title="Today" />);
    expect(container.querySelector("nav")).toBeNull();
    expect(container.querySelector(".bj-page-subtitle")).toBeNull();
    expect(container.querySelector(".bj-page-actions")).toBeNull();
  });

  it("puts actions in their own slot beside the title", () => {
    const { container } = view(
      <PageHeader
        title="Discover"
        actions={
          <button type="button">Save search</button>
        }
      />,
    );
    expect(text(query(container, ".bj-page-actions"))).toBe("Save search");
    expect(query(container, ".bj-page-actions").closest("header")).not.toBeNull();
  });

  it("has no serious accessibility violation", async () => {
    const { container } = view(
      <main>
        <PageHeader
          title="Discover"
          breadcrumb={[{ label: "Home", to: "/" }, { label: "Discover" }]}
          subtitle="Company job boards and your search providers"
          actions={<button type="button">Save search</button>}
        />
      </main>,
    );
    await expectNoSeriousA11yViolations(container);
  });
});
