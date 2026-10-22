// @vitest-environment jsdom
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { PublicLayout } from "../components/PublicLayout";
import { LANDING, LANDING_PROVIDER_CAVEAT } from "../content/landing";
import { REPO_URL } from "../content/site";
import { TOUR, TOUR_TABS } from "../content/tour";
import indexHtml from "../../index.html?raw";
import landingSource from "./Landing.tsx?raw";
import { expectNoSeriousA11yViolations } from "../testing/a11y";
import { FORBIDDEN, strings } from "../testing/claims";
import { textOfMarkup } from "../testing/markup";
import { render } from "../testing/render";
import Landing from "./Landing";

// What the landing page is allowed to say, and that it still says what Google's review and a visitor's
// common sense need it to say. The words are data (content/landing.ts, content/tour.ts) so
// they can be scanned: every claim on the page was checked against the code when it was written, and
// this is the guard that keeps a later edit from putting an unchecked one back.

const DATA = [...strings(LANDING), ...strings(TOUR), ...strings(TOUR_TABS)];
const page = (): string =>
  renderToStaticMarkup(
    <MemoryRouter>
      <PublicLayout variant="landing">
        <Landing />
      </PublicLayout>
    </MemoryRouter>,
  );
const rendered = textOfMarkup(page());

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
    expect(rendered).toContain(TOUR.sampleLabel);
    expect(rendered).toContain(LANDING.trailExample.sampleNote);
    // Every drawing of the tour says it is a sample in a visible chip or word of its own, so it cannot be
    // read as the visitor's.
    expect(TOUR.sampleTag).toBe("Sample");
    expect(TOUR.discover.postingNote).toContain("Sample");
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

  it("says Gmail drafting is optional and may be limited to invited people, because a server decides that", () => {
    expect(rendered).toContain("optional Gmail connection");
    expect(rendered).toContain("Gmail drafting may be limited to invited people while Google reviews the app.");
    expect(rendered).toContain("Gmail drafting is optional, and may be limited to invited people for now.");
    // The invite list is optional on the server: unset, every signed-in person may connect Gmail.
    expect(rendered).not.toContain("currently invite-only");
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
    // The hosted version's larger engine is not in the repository, and the page says so.
    expect(rendered).toContain("A basic resume and cover-letter writer is built in");
    expect(rendered).toContain("The larger engine service the hosted version uses is not in the repository.");
  });

  it("says what Settings says about the model providers, on the page itself", () => {
    expect(LANDING_PROVIDER_CAVEAT).not.toBeNull();
    expect(rendered).toContain(LANDING_PROVIDER_CAVEAT);
  });

  it("has a TODO-free, placeholder-free page", () => {
    for (const words of [...DATA, rendered]) expect(words).not.toMatch(/TODO|FIXME|TBD|lorem|ipsum|coming soon/i);
  });
});

describe("the page works before any script has run", () => {
  it("has a hero that is plain React content: no lazy loading, no fetch, no state of its own", () => {
    expect(landingSource).not.toMatch(/\blazy\(|Suspense|\bfetch\(|useState|useReducer/);
    // The two hooks it calls only read the address and move focus to a section named in it; they draw nothing.
    // The tour, the one piece with state, is its own component.
    expect((landingSource.match(/\buse[A-Z]\w*\(/g) ?? []).sort()).toEqual(["useLocation(", "useScrollToHash("]);
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
