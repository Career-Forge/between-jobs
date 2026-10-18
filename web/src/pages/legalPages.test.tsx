// @vitest-environment jsdom
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { PublicLayout } from "../components/PublicLayout";
import { PRIVACY, TERMS } from "../content/legal";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { render } from "../testing/render";
import Privacy from "./Privacy";
import Terms from "./Terms";

// The two legal pages in the signed-out frame, run in a DOM: no serious axe violation, one h1 and a
// heading order with no gaps, every table-of-contents link aimed at a section that exists, and every
// section of the document drawn (the text itself is content/legal.ts, pinned in content/legal.test.ts and
// not restyled here).

const PAGES = [
  ["the Privacy Policy", <Privacy key="p" />, PRIVACY],
  ["the Terms of Service", <Terms key="t" />, TERMS],
] as const;

describe.each(PAGES)("%s in the public layout", (_name, element, doc) => {
  const open = () =>
    render(
      <MemoryRouter>
        <PublicLayout>{element}</PublicLayout>
      </MemoryRouter>,
    ).container;

  it("has no serious or critical axe violation", async () => {
    await expectNoSeriousA11yViolations(open());
  });

  it("has one h1, and headings that never skip a level", () => {
    const root = open();
    expect(root.querySelectorAll("h1")).toHaveLength(1);
    const levels = [...root.querySelectorAll("h1, h2, h3, h4")].map((heading) => Number(heading.tagName.slice(1)));
    expect(levels[0]).toBe(1);
    for (let i = 1; i < levels.length; i++) expect(levels[i] - levels[i - 1]).toBeLessThanOrEqual(1);
  });

  it("draws every section of the document, each reachable from the table of contents", () => {
    const root = open();
    const links = [...root.querySelectorAll<HTMLAnchorElement>(".bj-legal-toc a")];
    expect(links).toHaveLength(doc.sections.length);
    for (const link of links) {
      const target = root.querySelector(link.getAttribute("href") ?? "");
      expect(target?.tagName).toBe("SECTION");
      expect(root.querySelector(`#${CSS.escape(target?.getAttribute("aria-labelledby") ?? "")}`)?.tagName).toBe("H2");
    }
  });

  it("is a reading column, not a band or a hero: an article inside the page's one main, with no next step", () => {
    const root = open();
    expect(root.querySelectorAll("main article.bj-legal")).toHaveLength(1);
    expect(root.querySelector(".bj-band, .bj-primary, .bj-button-link-primary")).toBeNull();
    expect(root.querySelector("a.bj-skip-link")?.textContent).toBe("Skip to content");
    expect(root.querySelectorAll("a.bj-skip-link")).toHaveLength(1);
  });
});
