import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import {
  LEGAL_EFFECTIVE_DATE,
  LEGAL_VERSION,
  LEGAL_WHAT_CHANGED,
  PRIVACY,
  TERMS,
  type LegalDocument,
} from "../content/legal";
import { PRIVACY_EMAIL, REPO_URL, SUPPORT_EMAIL } from "../content/site";
import { anchorsOf, headingLevels, tagsOf, textOfMarkup } from "../testing/markup";
import { LegalDocumentView } from "./LegalDocumentView";

// What a legal document looks like on the page: one title, which version and date, a table of
// contents that points at real sections, and each section under its own heading.

function html(doc: LegalDocument): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <LegalDocumentView doc={doc} />
    </MemoryRouter>,
  );
}

const DOCS: [string, LegalDocument][] = [
  ["Privacy Policy", PRIVACY],
  ["Terms of Service", TERMS],
];

describe.each(DOCS)("%s", (_name, doc) => {
  const markup = html(doc);

  it("has one h1, its title, and no <main> of its own (the page around it supplies that)", () => {
    expect(headingLevels(markup).filter((level) => level === 1)).toHaveLength(1);
    expect(markup).toContain(`<h1>${doc.title}</h1>`);
    expect(markup).not.toContain("<main");
    expect(markup.startsWith('<article class="bj-legal">')).toBe(true);
  });

  it("says its version, the day it took effect, and what changed", () => {
    const text = textOfMarkup(markup);
    expect(text).toContain(`Version ${LEGAL_VERSION}`);
    expect(text).toContain(LEGAL_EFFECTIVE_DATE.label);
    // "Effective", not "In effect since": the pages are not served until they are deployed, and
    // a past-tense claim of effect would be untrue on the day of the first publish.
    expect(text).toContain(`, effective ${LEGAL_EFFECTIVE_DATE.label}.`);
    expect(text).not.toMatch(/in effect since/i);
    expect(text).toContain(`What changed: ${LEGAL_WHAT_CHANGED}`);
    expect(markup).toMatch(new RegExp(`<time datetime="${LEGAL_EFFECTIVE_DATE.iso}">`, "i"));
  });

  it("never skips a heading level: h1, then h2 sections, with h3 only inside them", () => {
    const levels = headingLevels(markup);
    expect(levels[0]).toBe(1);
    for (let i = 1; i < levels.length; i++) expect(levels[i] - levels[i - 1]).toBeLessThanOrEqual(1);
    expect(levels.filter((level) => level === 2)).toHaveLength(doc.sections.length);
  });

  it("has a table of contents whose every link points at a section that exists", () => {
    const ids = new Set(tagsOf(markup, "section").map((tag) => tag.id));
    expect(ids.size).toBe(doc.sections.length);
    const toc = anchorsOf(markup).filter((anchor) => anchor.attributes.href?.startsWith("#"));
    expect(toc).toHaveLength(doc.sections.length);
    for (const link of toc) expect(ids.has(link.attributes.href.slice(1)), link.attributes.href).toBe(true);
    expect(toc.map((link) => link.text)).toEqual(doc.sections.map((section) => section.heading));
    expect(markup).toContain(`<nav class="bj-legal-toc" aria-label="${doc.title}, on this page">`);
  });

  it("labels every section by its own heading", () => {
    const headingIds = new Set(
      [...markup.matchAll(/<h2 id="([^"]+)"/g)].map((match) => match[1]),
    );
    for (const section of tagsOf(markup, "section")) {
      expect(headingIds.has(section["aria-labelledby"]), section.id).toBe(true);
    }
  });

  it("links its contact addresses, and opens anything outside the app in a new tab, safely", () => {
    const anchors = anchorsOf(markup);
    const mailto = new Set(
      anchors.filter((a) => a.attributes.href?.startsWith("mailto:")).map((a) => a.attributes.href.slice(7)),
    );
    expect([...mailto].sort()).toEqual([PRIVACY_EMAIL, SUPPORT_EMAIL].sort());
    for (const anchor of anchors) {
      const href = anchor.attributes.href ?? "";
      if (href.startsWith("https://")) {
        expect(href).toBe(REPO_URL);
        expect(anchor.attributes.target).toBe("_blank");
        expect(anchor.attributes.rel).toBe("noopener noreferrer");
        expect(markup).toContain("(opens in a new tab)");
      }
    }
    // The internal links are real routes.
    for (const anchor of anchors) {
      const href = anchor.attributes.href ?? "";
      if (href.startsWith("/")) expect(["/privacy", "/terms"]).toContain(href);
    }
  });

  it("is plain text and links: no script, no image, no inline style, nothing a stranger's text could carry", () => {
    expect(markup).not.toMatch(/<(script|img|iframe|style|svg|form|input)\b/);
    expect(markup).not.toContain("style=");
    expect(markup).not.toContain("dangerouslySetInnerHTML");
  });
});

describe("the Privacy Policy's definition lists", () => {
  it("draw each term and its detail as a <dt> and a <dd>, in equal number", () => {
    const markup = html(PRIVACY);
    const dt = (markup.match(/<dt>/g) ?? []).length;
    const dd = (markup.match(/<dd>/g) ?? []).length;
    expect(dt).toBeGreaterThan(20);
    expect(dd).toBe(dt);
  });
});
