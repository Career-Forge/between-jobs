import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { PublicLayout } from "../components/PublicLayout";
import { LANDING, LANDING_PROVIDERS, LANDING_SECTION_IDS } from "../content/landing";
import { SITE_TABS } from "../content/siteNav";
import { FONT_LICENSES_PATH, PRIVACY_EMAIL, REPO_URL, SUPPORT_EMAIL } from "../content/site";
import { TOUR, TOUR_TABS } from "../content/tour";
import { HOW_IT_WORKS_PATH, LANDING_PATH, OPEN_SOURCE_PATH, PRIVACY_PATH, TERMS_PATH } from "../lib/publicRoutes";
import { anchorsOf, headingLevels, tagsOf, textOfMarkup } from "../testing/markup";
import HowItWorks from "./HowItWorks";
import Landing from "./Landing";
import OpenSource from "./OpenSource";
import Privacy from "./Privacy";
import Terms from "./Terms";

// What a signed-out visitor is shown: the landing page, the two explainer pages and the two legal pages, each
// inside the public layout. Rendered to markup (the package has no DOM) and read back for what a person
// and a screen reader get: landmarks, one h1, headings in order, link targets and link text.

// The landing page is the one public page with a next-step band, so App draws it in the "landing" variant
// of the layout; every other public page has no band and uses the plain one. `path` is the address the
// page is at, which decides which of the header's tabs is the current one.
function page(element: React.ReactElement, variant: "landing" | "page" = "page", path = "/"): string {
  return renderToStaticMarkup(
    <MemoryRouter initialEntries={[path]}>
      <PublicLayout variant={variant}>{element}</PublicLayout>
    </MemoryRouter>,
  );
}

const PAGES: [string, React.ReactElement, "landing" | "page", string][] = [
  ["the landing page", <Landing key="landing" />, "landing", LANDING_PATH],
  ["How it works", <HowItWorks key="how" />, "page", HOW_IT_WORKS_PATH],
  ["Open source", <OpenSource key="open" />, "page", OPEN_SOURCE_PATH],
  ["the Privacy Policy", <Privacy key="privacy" />, "page", PRIVACY_PATH],
  ["the Terms of Service", <Terms key="terms" />, "page", TERMS_PATH],
];

describe.each(PAGES)("%s, in the public layout", (_name, element, variant, path) => {
  const markup = page(element, variant, path);

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

  it("labels its navigations, so a screen reader can tell them apart", () => {
    const labels = tagsOf(markup, "nav").map((tag) => tag["aria-label"]);
    expect(labels).toEqual(expect.arrayContaining(["Main", "Account", "Legal"]));
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

  it("has no inline style, script, image or form, and the only buttons are the menu button and, on the home page, the tour's tabs", () => {
    expect(markup).not.toContain("style=");
    expect(markup).not.toMatch(/<(script|img|iframe|form|input|svg|picture|video|canvas)\b/);
    const buttons = tagsOf(markup, "button");
    expect(buttons).toHaveLength(variant === "landing" ? TOUR_TABS.length + 1 : 1);
    expect(buttons.filter((button) => button.class === "bj-public-menu-button")).toHaveLength(1);
    for (const button of buttons.filter((candidate) => candidate.class !== "bj-public-menu-button")) {
      expect(button.class).toContain("bj-tour-tab");
      expect(button.role).toBe("tab");
    }
  });

  it("holds exactly one cyan fill: the hero band's button on the home page, the header's Create account everywhere else", () => {
    expect(markup.match(/bj-button-link-primary/g) ?? []).toHaveLength(1);
    expect(markup).not.toMatch(/class="[^"]*\bbj-primary\b/);
    const header = markup.slice(markup.indexOf("<header"), markup.indexOf("</header>"));
    if (variant === "landing") {
      expect(header).not.toContain("bj-button-link-primary");
      expect(markup).toContain("bj-button-link bj-button-link-primary bj-band-action");
    } else {
      expect(header).toContain("bj-button-link-primary");
    }
  });

  it("marks the header's tab for the page it is on, and no other, with aria-current", () => {
    const tabsNav = /<nav class="bj-public-tabs"[^>]*>([\s\S]*?)<\/nav>/.exec(markup);
    const tabs = anchorsOf(tabsNav?.[1] ?? "");
    expect(tabs.map((tab) => tab.attributes.href)).toEqual(SITE_TABS.map((tab) => tab.path));
    const current = tabs.filter((tab) => tab.attributes["aria-current"] === "page").map((tab) => tab.attributes.href);
    expect(current).toEqual(path === TERMS_PATH ? [] : [path]);
  });

  it("fetches nothing by itself: no src, srcset or data attribute, no url()", () => {
    expect(markup).not.toMatch(/\b(?:src|srcset|data)="/);
    expect(markup).not.toMatch(/url\(/);
  });
});

describe("the landing page", () => {
  const markup = page(<Landing />, "landing");
  const text = textOfMarkup(markup);
  const main = markup.slice(markup.indexOf("<main"), markup.indexOf("</main>"));

  it("says what Between Jobs is, in one h1 and a plain lead", () => {
    const h1 = /<h1 id="bj-landing-title">([\s\S]*?)<\/h1>/.exec(markup);
    expect(h1).not.toBeNull();
    expect(textOfMarkup(h1?.[1] ?? "")).toBe(LANDING.headlineLines.join(" "));
    expect(textOfMarkup(h1?.[1] ?? "")).toBe("One career. Every surface. Nothing forgotten.");
    expect(text).toContain(LANDING.lead);
    for (const thing of ["find roles", "keep track of your applications", "tailored resumes and cover letters", "practice for interviews"]) {
      expect(text).toContain(thing);
    }
  });

  it("carries the promise, the source and the early-days note, each under its own h2, and the keys under an h3", () => {
    for (const section of [LANDING.promise, LANDING.source, LANDING.early]) {
      expect(markup).toContain(`>${section.title}</h2>`);
    }
    expect(markup).toContain(`>${LANDING.keys.title}</h3>`);
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
    // Only one paragraph describes the connection's reading, so the policy link is never missing from it.
    expect([...markup.matchAll(/<p[^>]*>([\s\S]*?)<\/p>/g)].filter((match) => match[1].includes("optional Gmail connection"))).toHaveLength(1);
  });

  it("draws the tour: five tabs, one panel showing, and the sample next step under them", () => {
    expect(markup).toContain(`>${TOUR.title}</h2>`);
    expect(text).toContain("fictional");
    expect(tagsOf(markup, "button").filter((button) => button.role === "tab")).toHaveLength(TOUR_TABS.length);
    for (const tab of TOUR_TABS) expect(text).toContain(tab.label);
    expect((markup.match(/role="tabpanel"/g) ?? []).length).toBe(TOUR_TABS.length);
    expect(tagsOf(markup, "div").filter((div) => div.role === "tabpanel" && "hidden" in div)).toHaveLength(TOUR_TABS.length - 1);
    expect(markup).toContain(TOUR.nextLabel);
  });

  it("links the font licenses from the footer, in a new tab, with the same-origin text file", () => {
    const footer = markup.slice(markup.indexOf("<footer"));
    const link = anchorsOf(footer).find((anchor) => anchor.attributes.href === FONT_LICENSES_PATH);
    expect(FONT_LICENSES_PATH).toBe("/licenses/fonts.txt");
    expect(link?.attributes.target).toBe("_blank");
    expect(link?.attributes.rel).toBe("noopener noreferrer");
    expect(link?.text).toContain("Font licenses");
  });

  it("keeps the header's and the closing section's Create account outlined: the hero band's is the page's one fill", () => {
    const anchors = anchorsOf(markup).filter((anchor) => anchor.attributes.href === "/login?mode=register");
    expect(anchors.map((anchor) => anchor.attributes.class)).toEqual([
      "bj-button-link",
      "bj-button-link bj-button-link-primary bj-band-action",
      "bj-button-link",
    ]);
    // The one filled link is inside the band, which is inside the hero.
    const hero = markup.slice(markup.indexOf('class="bj-landing-hero"'), markup.indexOf(`id="${LANDING_SECTION_IDS.tour}"`));
    expect(hero).toContain("bj-button-link-primary");
  });

  it("puts the next-step band in the hero, in the landing row's words, with the signed-out why line", () => {
    const bands = markup.match(/<section class="bj-band[^"]*" aria-label="Next step"/g) ?? [];
    expect(bands).toHaveLength(1);
    expect(text).toContain("Create an account, or sign in if you have one.");
    expect(text).toContain("why: you're signed out");
    expect(anchorsOf(markup).find((anchor) => anchor.text === "Create an account")?.attributes.href).toBe("/login?mode=register");
  });

  it("offers Create account and Sign in in the header, the band and the closing section", () => {
    const hrefs = anchorsOf(markup).map((anchor) => anchor.attributes.href);
    expect(hrefs.filter((href) => href === "/login?mode=register")).toHaveLength(3);
    expect(hrefs.filter((href) => href === "/login")).toHaveLength(2);
  });

  it("starts with Skip to next step, then Skip to content, both ahead of the header", () => {
    const next = markup.indexOf("Skip to next step");
    const content = markup.indexOf("Skip to content");
    expect(next).toBeGreaterThan(-1);
    expect(next).toBeLessThan(content);
    expect(content).toBeLessThan(markup.indexOf("<header"));
    expect(markup).toContain('href="#next-step-focus"');
    // The band's button carries the id the first one points at.
    expect(markup).toContain('id="next-step-focus"');
  });

  it("labels the header's navigations: the site's tabs and the way in, and has no in-page section links", () => {
    const header = markup.slice(markup.indexOf("<header"), markup.indexOf("</header>"));
    expect(tagsOf(header, "nav").map((tag) => tag["aria-label"])).toEqual(["Main", "Account"]);
    expect(markup).not.toContain("On this page");
  });

  it("points its hero link at the tour, which exists", () => {
    const hero = markup.slice(markup.indexOf('class="bj-landing-hero"'), markup.indexOf(`id="${LANDING_SECTION_IDS.tour}"`));
    expect(anchorsOf(hero).map((anchor) => anchor.attributes.href)).toContain(`/#${LANDING_SECTION_IDS.tour}`);
    expect(main).toContain(`<section id="${LANDING_SECTION_IDS.tour}"`);
  });

  it("links the Open source page from the source strip", () => {
    const strip = markup.slice(markup.indexOf(`<section id="${LANDING_SECTION_IDS.source}"`), markup.indexOf(`<section id="${LANDING_SECTION_IDS.early}"`));
    expect(anchorsOf(strip).map((anchor) => anchor.attributes.href)).toContain(OPEN_SOURCE_PATH);
  });

  it("makes every in-page target a heading that can take focus", () => {
    for (const id of Object.values(LANDING_SECTION_IDS)) {
      const section = main.slice(main.indexOf(`<section id="${id}"`));
      expect(section.slice(0, section.indexOf("</h2>"))).toMatch(/<h2 id="[^"]+" tabindex="-1">/);
    }
  });

  it("labels every section by a heading that exists", () => {
    const ids = new Set([...markup.matchAll(/<h[1-6] id="([^"]+)"/g)].map((match) => match[1]));
    const sections = tagsOf(markup, "section");
    expect(sections.length).toBeGreaterThan(5);
    for (const section of sections) {
      const label = section["aria-labelledby"];
      if (label === undefined) expect(section["aria-label"]).toBeTruthy();
      else expect(ids.has(label)).toBe(true);
    }
  });

  it("lists the model providers the key routes support, once each, and says to connect a key from the one you already use", () => {
    const list = /<ul class="bj-landing-providers"[^>]*>([\s\S]*?)<\/ul>/.exec(markup);
    const items = [...(list?.[1] ?? "").matchAll(/<li>([^<]*)<\/li>/g)].map((match) => match[1]);
    expect(items).toEqual([...LANDING_PROVIDERS]);
    expect(text).toContain("Connect a key from the provider you already use.");
  });

  it("gives the contact line for questions", () => {
    expect(text).toContain(LANDING.early.lead.trim());
    expect(text).toContain(SUPPORT_EMAIL);
  });
});

describe("the legal pages in the public layout", () => {
  it("have no hero, so the header's Create account is their one cyan fill", () => {
    for (const element of [<Privacy key="p" />, <Terms key="t" />]) {
      expect(page(element).match(/bj-button-link-primary/g) ?? []).toHaveLength(1);
    }
  });

  it("have no next-step band, no Skip to next step and no tour: a legal page has no 'now'", () => {
    for (const element of [<Privacy key="p" />, <Terms key="t" />]) {
      const markup = page(element);
      expect(markup).not.toContain("Next step");
      expect(markup).not.toContain("bj-band");
      expect(markup).not.toContain("Skip to next step");
      expect(markup).not.toContain("bj-tour");
      expect(markup).toContain("Skip to content");
    }
  });

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
