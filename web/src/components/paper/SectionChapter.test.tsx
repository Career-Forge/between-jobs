// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { expectNoSeriousA11yViolations } from "../../testing/a11y";
import { query, render } from "../../testing/render";
import { StaticTrailLine } from "./TrailLine";
import { SectionChapter } from "./SectionChapter";

const text = (element: Element) => (element.textContent ?? "").replace(/\s+/g, " ").trim();

describe("SectionChapter", () => {
  it("is the anchor, with a focusable h2 whose name is the title alone", () => {
    const { container } = render(<SectionChapter id="ch-draft" title="Draft" eyebrow="Resume" />);
    const anchor = query(container, "#ch-draft");
    const heading = query(anchor, "h2");
    expect(heading.textContent).toBe("Draft");
    expect(heading.getAttribute("tabindex")).toBe("-1");
    // The eyebrow is above the heading and is not part of it.
    expect(text(query(anchor, ".bj-chapter-eyebrow"))).toBe("Resume");
    expect(query(anchor, ".bj-chapter-eyebrow").compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(container.querySelectorAll("h1, h3")).toHaveLength(0);
  });

  it("takes the heading level it is given and keeps one heading", () => {
    for (const level of [2, 3, 4, 5, 6] as const) {
      const { container, unmount } = render(<SectionChapter title="Practice" level={level} />);
      expect(container.querySelectorAll(`h${level}`)).toHaveLength(1);
      expect(container.querySelectorAll("h1, h2, h3, h4, h5, h6")).toHaveLength(1);
      unmount();
    }
  });

  it("has no numeral, whatever the title: nothing numbers the chapters", () => {
    const { container } = render(<SectionChapter id="generate" title="Generate" eyebrow="Resume" />);
    expect([...container.querySelectorAll("p, h2")].map(text)).toEqual(["Resume", "Generate"]);
    expect(container.textContent).not.toMatch(/\b[IVX]+\b|\d/);
  });

  it("leaves out the id, eyebrow, trail and intro it was not given", () => {
    const { container } = render(<SectionChapter title="Tailor" />);
    expect(query(container, ".bj-chapter").hasAttribute("id")).toBe(false);
    expect(container.querySelector(".bj-chapter-eyebrow")).toBeNull();
    expect(container.querySelector(".bj-chapter-trail")).toBeNull();
    expect(container.querySelector(".bj-chapter-intro")).toBeNull();
  });

  it("puts the trail slot after the heading and the intro after that", () => {
    const { container } = render(
      <SectionChapter id="ch-check" title="Review" trail={<StaticTrailLine trail={{ glyph: "code", actor: "Checked by code" }} />}>
        <p>What was checked.</p>
      </SectionChapter>,
    );
    const heading = query(container, "h2");
    const trail = query(container, ".bj-chapter-trail");
    const intro = query(container, ".bj-chapter-intro");
    expect(heading.compareDocumentPosition(trail) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(trail.compareDocumentPosition(intro) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(text(trail)).toBe("○ Checked by code");
    expect(text(intro)).toBe("What was checked.");
  });

  it("is where useScrollToHash looks: the anchored element holds a heading that can take focus", () => {
    const { container } = render(<SectionChapter id="your-play" title="Your play" />);
    const anchor = query(container, "#your-play");
    const heading = query<HTMLElement>(anchor, "h2[tabindex]");
    heading.focus();
    expect(document.activeElement).toBe(heading);
  });

  it("has no serious accessibility violations", async () => {
    const { container } = render(
      <main>
        <SectionChapter id="a" title="Draft" eyebrow="Resume">
          <p>Intro</p>
        </SectionChapter>
        <SectionChapter id="b" title="Header" level={3} />
      </main>,
    );
    await expectNoSeriousA11yViolations(container);
  });
});
