import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { PublicLayout } from "../components/PublicLayout";
import { LANDING } from "../content/landing";
import { PRIVACY_EMAIL, REPO_URL, SUPPORT_EMAIL } from "../content/site";
import { anchorsOf, headingLevels, tagsOf, textOfMarkup } from "../testing/markup";
import Landing from "./Landing";
import Privacy from "./Privacy";
import Terms from "./Terms";

// What a signed-out visitor is shown: the landing page, and the two legal pages, each inside the
// public layout. Rendered to markup (the package has no DOM) and read back for what a person
// and a screen reader get: landmarks, one h1, headings in order, link targets and link text.

function page(element: React.ReactElement): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <PublicLayout>{element}</PublicLayout>
    </MemoryRouter>,
  );
}

const PAGES: [string, React.ReactElement][] = [
  ["the landing page", <Landing key="landing" />],
  ["the Privacy Policy", <Privacy key="privacy" />],
  ["the Terms of Service", <Terms key="terms" />],
];

describe.each(PAGES)("%s, in the public layout", (_name, element) => {
  const markup = page(element);

  it("has the three landmarks once each, and exactly one h1 inside the main one", () => {
    for (const landmark of ["header", "main", "footer"]) {
      expect(tagsOf(markup, landmark), landmark).toHaveLength(1);
    }
    expect(headingLevels(markup).filter((level) => level === 1)).toHaveLength(1);
    const main = markup.slice(markup.indexOf("<main"), markup.indexOf("</main>"));
    expect(main).toContain("<h1");
    expect(markup.indexOf("<header")).toBeLessThan(markup.indexOf("<main"));
    expect(markup.indexOf("</main>")).toBeLessThan(markup.indexOf("<footer"));
  });

  it("labels its two navigations, so a screen reader can tell them apart", () => {
    const labels = tagsOf(markup, "nav").map((tag) => tag["aria-label"]);
    expect(labels).toContain("Account");
    expect(labels).toContain("Legal");
    expect(labels.every((label) => typeof label === "string" && label.length > 0)).toBe(true);
  });

  it("never skips a heading level", () => {
    const levels = headingLevels(markup);
    expect(levels[0]).toBe(1);
    for (let i = 1; i < levels.length; i++) expect(levels[i] - levels[i - 1]).toBeLessThanOrEqual(1);
  });

  it("offers the way in, the legal pages, the source and both contact addresses on every page", () => {
    const hrefs = anchorsOf(markup).map((anchor) => anchor.attributes.href);
    expect(hrefs).toContain("/login");
    expect(hrefs).toContain("/login?mode=register");
    expect(hrefs).toContain("/privacy");
    expect(hrefs).toContain("/terms");
    expect(hrefs).toContain(REPO_URL);
    expect(hrefs).toContain(`mailto:${SUPPORT_EMAIL}`);
    expect(hrefs).toContain(`mailto:${PRIVACY_EMAIL}`);
  });

  it("has link text that says where it goes, and never opens a new tab without saying so", () => {
    for (const anchor of anchorsOf(markup)) {
      const name = (anchor.attributes["aria-label"] ?? anchor.text).trim();
      expect(name.length, JSON.stringify(anchor)).toBeGreaterThan(0);
      expect(anchor.text.toLowerCase()).not.toMatch(/^(here|click here|link|this link|read more|more)$/);
      if (anchor.attributes.target === "_blank") {
        expect(anchor.attributes.rel).toBe("noopener noreferrer");
        expect(`${anchor.attributes["aria-label"] ?? ""} ${anchor.text}`).toContain("(opens in a new tab)");
      }
    }
  });

  it("keeps button-styled links out of running text, where the prose-link underline would reach them", () => {
    for (const paragraph of markup.matchAll(/<p[\s>][\s\S]*?<\/p>/g)) {
      expect(paragraph[0]).not.toContain("bj-button-link");
    }
  });

  it("has no inline style, script, image or form", () => {
    expect(markup).not.toContain("style=");
    expect(markup).not.toMatch(/<(script|img|iframe|form|input|button)\b/);
  });
});

describe("the landing page", () => {
  const markup = page(<Landing />);
  const text = textOfMarkup(markup);

  it("says what Between Jobs is, in one h1 and a plain lead", () => {
    expect(markup).toContain(`<h1 id="bj-landing-title">${LANDING.headline}</h1>`);
    expect(text).toContain(LANDING.lead);
    for (const thing of ["find roles", "keep track of your applications", "tailored resumes and cover letters", "practice for interviews"]) {
      expect(text).toContain(thing);
    }
  });

  it("carries the promise, the keys and the source, each under its own heading", () => {
    for (const section of [LANDING.promise, LANDING.keys, LANDING.source, LANDING.early]) {
      expect(markup).toContain(`>${section.title}</h2>`);
    }
    expect(text).toContain(LANDING.promise.text);
    expect(text).toContain(LANDING.keys.text);
    expect(text).toContain(LANDING.source.text);
    expect(text).toContain(LANDING.source.linkText);
  });

  it("says what the Gmail connection does beyond drafts, in its own paragraph with a link to the Privacy Policy", () => {
    expect(text).toContain(LANDING.gmail.text);
    expect(text).not.toMatch(/only creates drafts/i);
    const paragraph = [...markup.matchAll(/<p>([\s\S]*?)<\/p>/g)]
      .map((match) => match[1])
      .find((candidate) => candidate.includes("optional Gmail connection"));
    expect(paragraph).toBeDefined();
    expect(textOfMarkup(paragraph ?? "")).toBe(
      `${LANDING.gmail.text} ${LANDING.gmail.policyLead}${LANDING.gmail.policyLinkText}${LANDING.gmail.policyTail}`,
    );
    expect(anchorsOf(paragraph ?? "").map((a) => [a.attributes.href, a.text])).toEqual([["/privacy", "Privacy Policy"]]);
  });

  it("lists the four things it does", () => {
    for (const feature of LANDING.features) {
      expect(markup).toContain(`<h3>${feature.title}</h3>`);
      expect(text).toContain(feature.text);
    }
    expect((markup.match(/<li class="bj-landing-item">/g) ?? []).length).toBe(LANDING.features.length);
  });

  it("puts Create account and Sign in in the hero as well as the header", () => {
    const hrefs = anchorsOf(markup).map((anchor) => anchor.attributes.href);
    expect(hrefs.filter((href) => href === "/login?mode=register")).toHaveLength(2);
    expect(hrefs.filter((href) => href === "/login")).toHaveLength(2);
  });

  it("labels every section by a heading that exists", () => {
    const ids = new Set([...markup.matchAll(/<h[1-6] id="([^"]+)"/g)].map((match) => match[1]));
    const sections = tagsOf(markup, "section");
    expect(sections.length).toBeGreaterThan(3);
    for (const section of sections) expect(ids.has(section["aria-labelledby"])).toBe(true);
  });

  it("gives the contact line for questions", () => {
    expect(text).toContain(LANDING.early.lead.trim());
    expect(text).toContain(SUPPORT_EMAIL);
  });
});

describe("the legal pages in the public layout", () => {
  it("are the documents themselves: their h1 is the page's h1", () => {
    expect(page(<Privacy />)).toContain("<h1>Privacy Policy</h1>");
    expect(page(<Terms />)).toContain("<h1>Terms of Service</h1>");
  });

  it("can be drawn alone, for the signed-in shell, which has its own <main>", () => {
    const alone = renderToStaticMarkup(
      <MemoryRouter>
        <Privacy />
      </MemoryRouter>,
    );
    expect(alone).not.toContain("<main");
    expect(alone).toContain("<h1>Privacy Policy</h1>");
  });
});
