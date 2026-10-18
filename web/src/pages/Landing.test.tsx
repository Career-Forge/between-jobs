// @vitest-environment jsdom
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { PublicLayout } from "../components/PublicLayout";
import { LANDING } from "../content/landing";
import { STAGES } from "../content/landingCircuit";
import { REPO_URL } from "../content/site";
import indexHtml from "../../index.html?raw";
import landingSource from "./Landing.tsx?raw";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { textOfMarkup } from "../testing/markup";
import { render } from "../testing/render";
import Landing from "./Landing";

// What the landing page is allowed to say, and that it still says what Google's review and a visitor's
// common sense need it to say. The words are data (content/landing.ts, content/landingCircuit.ts) so
// they can be scanned: every claim on the page was checked against the code when it was written, and
// this is the guard that keeps a later edit from putting an unchecked one back.

function strings(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(strings);
  if (typeof value === "object" && value !== null) return Object.values(value).flatMap(strings);
  return [];
}

const DATA = [...strings(LANDING), ...strings(STAGES)];
const page = (): string =>
  renderToStaticMarkup(
    <MemoryRouter>
      <PublicLayout variant="landing">
        <Landing />
      </PublicLayout>
    </MemoryRouter>,
  );
const rendered = textOfMarkup(page());

// Words a page like this reaches for and this product cannot back. Each is a claim about results, scale,
// proof or speed; none is built, measured or true of what the code does today.
const FORBIDDEN: [string, RegExp][] = [
  ["verified (the product says 'checked', and never calls a link or a claim verified)", /\bverified\b/i],
  ["guarantee", /guarant/i],
  ["certified", /\bcertified\b/i],
  ["proven", /\bproven\b/i],
  ["superlatives", /\b(?:best|fastest|leading|world-class|revolutionary|cutting-edge|game-chang\w*|unrivall?ed|#1|number one)\b/i],
  ["marketing about AI", /\bai-powered\b|\bsupercharge\w*|\b10x\b|\bmagic\w*/i],
  ["scale and popularity", /\b(?:thousands?|millions?|hundreds)\b|\btrusted by\b|\bloved by\b|\bjoin\b[^.]*\b(?:users|people|job seekers)\b/i],
  ["a testimonial", /testimonial|\bcustomers? (?:say|said|love)\b|[“"][^”"]{10,}[”"]\s*(?:--|-|\u2014)\s*[A-Z]/],
  ["results", /\bget hired\b|\bland(?:ed)? (?:a|the|your) (?:job|offer)\b|\bhours? saved\b|\bsave[sd]? you\b|\bhigher response\b|\bmore interviews\b/i],
  ["automation the product refuses", /\bauto-?appl\w*|\bone-click appl\w*|\bapplies for you\b|\bsubmits for you\b|\bsends for you\b/i],
  ["a platform the extension does not support", /\bworkday\b|\bicims\b|\blinkedin easy apply\b/i],
  ["the product called a studio (it is always 'Between Jobs')", /\bstudio\b/i],
  ["a promise that every block or piece of work carries a provenance line, which only some pages draw", /every (?:block|result|piece|surface)\b[^.]*\b(?:trail|came from)|where each piece came from/i],
  ["every result rated, when only a batch is and none is without a model key", /\b(?:each|every) (?:result|listing)\b[^.]*\b(?:rated|scored|fits)\b/i],
  ["the brand dot, which is a mark and never copy", /\bred dot\b/i],
];

describe("the claims the landing page does not make", () => {
  it("reads the page's words: the data and the rendered page both have plenty to scan", () => {
    expect(DATA.length).toBeGreaterThan(60);
    expect(rendered.length).toBeGreaterThan(2000);
  });

  it.each(FORBIDDEN)("never says %s", (_name, pattern) => {
    for (const words of [...DATA, rendered]) expect(words).not.toMatch(pattern);
  });

  it("carries no number or statistic: no digit in any sentence, apart from the license's own name", () => {
    for (const words of DATA) expect(words.replace(/AGPL-3\.0/g, ""), words).not.toMatch(/\d/);
    expect(rendered.replace(/AGPL-3\.0/g, "")).not.toMatch(/\d+\s*(?:%|\+|k\b|x\b)/);
  });

  it("keeps the em dash out, as the project writes ' -- '", () => {
    for (const words of [...DATA, rendered]) expect(words).not.toContain("\u2014");
  });

  it("calls the sample role a fictional sample wherever it shows one, and never presents it as the visitor's", () => {
    expect(rendered).toContain("a fictional company");
    expect(rendered).toContain(LANDING.dossier.sampleChip);
    expect(rendered).toContain(LANDING.dossier.sampleNote);
    expect(rendered).toContain(LANDING.trailExample.sampleNote);
    expect(rendered).toContain("(sample)");
  });
});

describe("the hero's worked example", () => {
  it("labels its legend as a list, not a paragraph (aria-label is not allowed on a paragraph), with words for every mark", () => {
    const markup = page();
    expect(markup).not.toMatch(/<p\b[^>]*aria-label=/);
    const legend = /<ul class="bj-trail-example-legend"([^>]*)>([\s\S]*?)<\/ul>/.exec(markup);
    expect(legend?.[1]).toContain('aria-label="What each mark means"');
    expect(legend?.[1]).toContain('role="list"');
    const items = [...(legend?.[2] ?? "").matchAll(/<li>([\s\S]*?)<\/li>/g)].map((match) => textOfMarkup(match[1].replace(/<span[^>]*aria-hidden="true"[^>]*>[\s\S]*?<\/span>/g, "")));
    // What a screen reader reads: the marks are hidden from it, so these words are all it gets.
    expect(items).toEqual(["written by a model", "plain code, no model", "done by you", "waiting for your review"]);
  });

  it("calls itself a sample and claims no provenance line on blocks the page does not draw one on", () => {
    expect(LANDING.trailExample.eyebrow).toBe("A sample flagged claim");
    expect(LANDING.dossier.text).not.toMatch(/where each piece came from/i);
    expect(LANDING.dossier.eyebrow).toBe("A workspace for what comes next");
  });
});

describe("what the page has to disclose", () => {
  it("describes the Gmail connection in full and links the Privacy Policy, which Google's review of the app looks for on the home page", () => {
    expect(rendered).toContain(LANDING.gmail.text);
    expect(rendered).toContain("reads the thread of each draft it created");
    expect(rendered).toContain("the text of the newest reply goes to your AI provider to be classified");
    expect(rendered).toContain("a confident result can move that application to a new stage on your board");
    expect(page()).toMatch(/<a [^>]*href="\/privacy"[^>]*>Privacy Policy<\/a>/);
  });

  it("says Gmail drafting is optional and invite-only for now", () => {
    expect(rendered).toContain("optional Gmail connection");
    expect(rendered).toContain("Gmail drafting is currently invite-only while Google reviews the app.");
    expect(rendered).toContain("Gmail drafting is optional, and invite-only for now.");
  });

  it("says the browser extension is for early testers and not in the Chrome Web Store, and that it never submits", () => {
    expect(rendered).toContain("The extension is for early testers and is not in the Chrome Web Store yet.");
    expect(rendered).toContain("It never clicks Next and never clicks Submit.");
    expect(rendered).toContain("never submits an application for you");
  });

  it("says what is live: Discover, the board and practice", () => {
    expect(rendered).toContain("Discover, the application board and interview practice are live.");
  });

  it("says the keys are the visitor's own, and links the source and the license it is under", () => {
    expect(rendered).toContain(LANDING.keys.connect);
    expect(rendered).toContain("Between Jobs has no AI provider of its own.");
    expect(rendered).toContain("AGPL-3.0");
    expect(page()).toContain(`href="${REPO_URL}"`);
    // The private engine is not in the repository, and the page says so.
    expect(rendered).toContain("is separate, and it is not in the repository yet");
  });

  it("has a TODO-free, placeholder-free page", () => {
    for (const words of [...DATA, rendered]) expect(words).not.toMatch(/TODO|FIXME|TBD|lorem|ipsum|coming soon/i);
  });
});

describe("the page works before any script has run", () => {
  it("has a hero that is plain React content: no lazy loading, no fetch, no state of its own", () => {
    expect(landingSource).not.toMatch(/\blazy\(|Suspense|\bfetch\(|useState|useReducer/);
    // The one hook it calls only moves focus to a section named in the address; it draws nothing.
    expect((landingSource.match(/\buse[A-Z]\w*\(/g) ?? []).sort()).toEqual(["useHashTarget("]);
    // The static markup the server-side renderer produced has the headline, the lead and the band's link.
    const markup = page();
    expect(markup).toContain('<h1 id="bj-landing-title">');
    expect(textOfMarkup(markup)).toContain(LANDING.lead);
    expect(markup).toContain('href="/login?mode=register"');
  });

  it("keeps the two font preloads the page needs for its first paint, and adds no image", () => {
    expect(indexHtml).toContain('href="/src/fonts/ibm-plex-sans/IBMPlexSans-Regular-Latin1.woff2"');
    expect(indexHtml).toContain('href="/src/fonts/newsreader/newsreader-latin-opsz-normal.woff2"');
    expect((indexHtml.match(/rel="preload"/g) ?? []).length).toBe(2);
    expect(page()).not.toMatch(/<(img|picture|video|svg)\b/);
  });
});

describe("accessibility", () => {
  it("has no serious or critical axe violation, with the layout around it", async () => {
    const { container } = render(
      <MemoryRouter>
        <PublicLayout variant="landing">
          <Landing />
        </PublicLayout>
      </MemoryRouter>,
    );
    await expectNoSeriousA11yViolations(container);
  });

  it("has one h1, headings that never skip a level, and only labeled landmarks beyond the three", async () => {
    const { container } = render(
      <MemoryRouter>
        <PublicLayout variant="landing">
          <Landing />
        </PublicLayout>
      </MemoryRouter>,
    );
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    const levels = [...container.querySelectorAll("h1, h2, h3, h4")].map((heading) => Number(heading.tagName.slice(1)));
    for (let i = 1; i < levels.length; i++) expect(levels[i] - levels[i - 1]).toBeLessThanOrEqual(1);
    for (const nav of container.querySelectorAll("nav")) expect(nav.getAttribute("aria-label")).toBeTruthy();
  });
});
