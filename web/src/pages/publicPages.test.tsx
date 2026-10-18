import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { PublicLayout } from "../components/PublicLayout";
import { LANDING, LANDING_PROVIDERS, LANDING_SECTION_LINKS } from "../content/landing";
import { STAGES } from "../content/landingCircuit";
import { FONT_LICENSES_PATH, PRIVACY_EMAIL, REPO_URL, SUPPORT_EMAIL } from "../content/site";
import { anchorsOf, headingLevels, tagsOf, textOfMarkup } from "../testing/markup";
import Landing from "./Landing";
import Privacy from "./Privacy";
import Terms from "./Terms";

// What a signed-out visitor is shown: the landing page, and the two legal pages, each inside the
// public layout. Rendered to markup (the package has no DOM) and read back for what a person
// and a screen reader get: landmarks, one h1, headings in order, link targets and link text.

// The landing page is the one public page with a next-step band, so App draws it in the "landing" variant
// of the layout; the two legal pages have no band and use the plain one.
function page(element: React.ReactElement, variant: "landing" | "legal" = "legal"): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <PublicLayout variant={variant}>{element}</PublicLayout>
    </MemoryRouter>,
  );
}

const PAGES: [string, React.ReactElement, "landing" | "legal"][] = [
  ["the landing page", <Landing key="landing" />, "landing"],
  ["the Privacy Policy", <Privacy key="privacy" />, "legal"],
  ["the Terms of Service", <Terms key="terms" />, "legal"],
];

describe.each(PAGES)("%s, in the public layout", (_name, element, variant) => {
  const markup = page(element, variant);

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

  it("has no inline style, script, image or form, and the only buttons are the circuit's stage buttons", () => {
    expect(markup).not.toContain("style=");
    expect(markup).not.toMatch(/<(script|img|iframe|form|input|svg|picture|video|canvas)\b/);
    const buttons = tagsOf(markup, "button");
    expect(buttons).toHaveLength(variant === "landing" ? STAGES.length : 0);
    for (const button of buttons) expect(button.class).toContain("bj-circuit-stage-button");
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

  it("names the three chapters and what each does", () => {
    for (const chapter of LANDING.chapters) {
      expect(markup).toContain(`<h3>${chapter.title}</h3>`);
      expect(text).toContain(chapter.text);
    }
    expect((markup.match(/<li class="bj-landing-chapter">/g) ?? []).length).toBe(LANDING.chapters.length);
  });

  it("draws the circuit: five stages in a list, each with its sample card, the board and the sample next step", () => {
    expect(markup).toContain(`>${LANDING.circuit.title}</h2>`);
    expect(text).toContain("Sample role");
    expect(text).toContain("fictional");
    expect((markup.match(/class="bj-circuit-card"/g) ?? []).length).toBe(STAGES.length);
    for (const stage of STAGES) expect(text).toContain(stage.label);
    expect(markup).toContain(LANDING.circuit.demoLabel);
  });

  it("links the font licenses from the footer, in a new tab, with the same-origin text file", () => {
    const footer = markup.slice(markup.indexOf("<footer"));
    const link = anchorsOf(footer).find((anchor) => anchor.attributes.href === FONT_LICENSES_PATH);
    expect(FONT_LICENSES_PATH).toBe("/licenses/fonts.txt");
    expect(link?.attributes.target).toBe("_blank");
    expect(link?.attributes.rel).toBe("noopener noreferrer");
    expect(link?.text).toContain("Font licenses");
  });

  it("holds exactly one cyan fill, the hero band's Create an account, so the header's and the closing section's are outlined", () => {
    expect(markup.match(/bj-button-link-primary/g) ?? []).toHaveLength(1);
    expect(markup).not.toMatch(/class="[^"]*\bbj-primary\b/);
    const anchors = anchorsOf(markup).filter((anchor) => anchor.attributes.href === "/login?mode=register");
    expect(anchors.map((anchor) => anchor.attributes.class)).toEqual([
      "bj-button-link",
      "bj-button-link bj-button-link-primary bj-band-action",
      "bj-button-link",
    ]);
    // The one filled link is inside the band, which is inside the hero.
    const hero = markup.slice(markup.indexOf('class="bj-landing-hero"'), markup.indexOf('id="circuit"'));
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

  it("links the header's section links to sections that exist, and labels that nav", () => {
    const header = markup.slice(markup.indexOf("<header"), markup.indexOf("</header>"));
    expect(tagsOf(header, "nav").map((tag) => tag["aria-label"])).toEqual(["On this page", "Account"]);
    const hrefs = anchorsOf(header).map((anchor) => anchor.attributes.href);
    for (const link of LANDING_SECTION_LINKS) {
      expect(hrefs).toContain(`/#${link.id}`);
      expect(main).toContain(`<section id="${link.id}"`);
    }
  });

  it("makes every in-page target a heading that can take focus", () => {
    for (const link of LANDING_SECTION_LINKS) {
      const section = main.slice(main.indexOf(`<section id="${link.id}"`));
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
  it("have no filled cyan button at all: their header link is outlined and there is no hero", () => {
    for (const element of [<Privacy key="p" />, <Terms key="t" />]) {
      expect(page(element)).not.toContain("bj-button-link-primary");
    }
  });

  it("have no next-step band, no Skip to next step and no section links: a legal page has no 'now'", () => {
    for (const element of [<Privacy key="p" />, <Terms key="t" />]) {
      const markup = page(element);
      expect(markup).not.toContain("Next step");
      expect(markup).not.toContain("bj-band");
      expect(markup).not.toContain("Skip to next step");
      expect(markup).not.toContain("On this page");
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
