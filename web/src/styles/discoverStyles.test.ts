import { describe, expect, it } from "vitest";
import { PHONE_QUERY } from "../lib/useMediaQuery";
import tokensCss from "../tokens.css?raw";
import { blockOf, declarationsFor, rulesOf, tokenResolver, withoutMedia } from "../testing/css";
import discoverCss from "./discover.css?raw";
import hiringCss from "./hiring-signals.css?raw";
import savedCss from "./saved-searches.css?raw";
import discoverView from "../components/discover/DiscoverView.tsx?raw";
import discoverForm from "../components/discover/DiscoverForm.tsx?raw";
import resultRow from "../components/discover/ResultRow.tsx?raw";
import resultsView from "../components/discover/ResultsView.tsx?raw";
import fitBlock from "../components/discover/FitBlock.tsx?raw";
import searchTimer from "../components/discover/SearchTimer.tsx?raw";
import savedView from "../components/SavedSearchesCardView.tsx?raw";
import hiringPage from "../components/HiringSignalsPageView.tsx?raw";
import hiringTabView from "../components/HiringSignalsTabView.tsx?raw";
import hiringForm from "../components/HiringSignalsTabForm.tsx?raw";
import hiringResults from "../components/HiringSignalsTabResults.tsx?raw";

// The Discover, saved-search and Hiring signals stylesheets are read as text (there is no layout engine
// in the test run). What they pin is what the pages rest on: no colors of their own, no cyan fill, the
// one breakpoint, the 44px targets on a phone, the row layout, and that long text wraps. The markup
// that uses them has no inline style attribute, because the production policy allows styles from the
// site's own origin only.

const sheets: Array<[string, string]> = [
  ["discover.css", discoverCss],
  ["hiring-signals.css", hiringCss],
  ["saved-searches.css", savedCss],
];
const resolve = tokenResolver(tokensCss);

function blocksOf(css: string, header: string): string {
  const parts: string[] = [];
  let rest = css;
  for (;;) {
    const block = blockOf(rest, header);
    if (block === null) return parts.join("\n");
    parts.push(block);
    rest = rest.slice(rest.indexOf(block) + block.length);
  }
}

describe.each(sheets)("%s", (name, css) => {
  const code = css.replace(/\/\*[\s\S]*?\*\//g, "");

  it("was loaded as text", () => {
    expect(css.length).toBeGreaterThan(500);
  });

  it("defines no color: no hex value, no rgb or hsl, only tokens", () => {
    expect(code).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(code).not.toMatch(/\b(?:rgb|rgba|hsl|hsla)\(/);
  });

  it("only uses tokens that tokens.css defines", () => {
    const defined = new Set([...tokensCss.matchAll(/(--[\w-]+)\s*:/g)].map((match) => match[1]));
    const used = new Set([...css.matchAll(/var\((--[\w-]+)/g)].map((match) => match[1]));
    expect([...used].filter((token) => !defined.has(token))).toEqual([]);
  });

  it("never fills anything cyan: the band's button is the page's one cyan fill", () => {
    for (const rule of rulesOf(css)) {
      for (const property of ["background", "background-color"]) {
        expect(rule.declarations[property] ?? "", `${name} ${rule.selectors.join(", ")}`).not.toContain("--bj-cyan");
      }
    }
  });

  it("states no width of its own but the shell's phone breakpoint and the 960px and 600px steps", () => {
    const queries = [...css.matchAll(/@media ([^{]+)\{/g)].map((match) => match[1].trim());
    for (const query of queries) {
      expect(["(max-width: 959px)", "(min-width: 960px)", "(min-width: 600px)", "(min-width: 600px) and (max-width: 959px)"], `${name}: ${query}`).toContain(query);
    }
    expect(blocksOf(css, `@media ${PHONE_QUERY}`)).not.toBe("");
  });

  it("uses no animation of its own, so there is nothing for prefers-reduced-motion to turn off", () => {
    expect(code).not.toMatch(/@keyframes|animation\s*:|transition\s*:/);
  });
});

describe("the Discover rows", () => {
  const base = withoutMedia(discoverCss);
  const wide = blocksOf(discoverCss, "@media (min-width: 960px)");
  const phone = blocksOf(discoverCss, `@media ${PHONE_QUERY}`);

  it("are at most the list measure, 880px, and let the page shrink around long text", () => {
    expect(declarationsFor(base, ".bj-discover")["max-width"]).toBe("880px");
    expect(declarationsFor(base, ".bj-discover")["min-width"]).toBe("0");
    expect(declarationsFor(withoutMedia(savedCss), ".bj-ss")["max-width"]).toBe("880px");
  });

  it("are one column, and from 960px the identity on the left with a 300px fit block on the right", () => {
    expect(declarationsFor(base, ".bj-disc-row")["grid-template-columns"]).toBe("minmax(0, 1fr)");
    expect(declarationsFor(wide, ".bj-disc-row")["grid-template-columns"]).toBe("minmax(0, 1fr) 300px");
    for (const selector of [".bj-disc-actions", ".bj-disc-trail"]) {
      expect(wide.replace(/\s+/g, " ")).toContain(selector);
    }
    expect(declarationsFor(wide, ".bj-disc-trail")["grid-column"]).toBe("1 / -1");
  });

  it("wrap a company, a snippet and a title that are one long string instead of widening the page", () => {
    for (const selector of [".bj-disc-title", ".bj-disc-meta", ".bj-disc-snippet", ".bj-disc-signal", ".bj-fit-oneliner", ".bj-fit-notes p", ".bj-disc-note", ".bj-disc-fallback"]) {
      expect(declarationsFor(base, selector)["overflow-wrap"], selector).toBe("anywhere");
    }
    for (const selector of [".bj-disc-row", ".bj-disc-main", ".bj-fit", ".bj-disc-results"]) {
      expect(declarationsFor(base, selector)["min-width"], selector).toBe("0");
    }
  });

  it("make every control at least 44px tall on a phone", () => {
    for (const selector of [".bj-disc-form-actions > button", ".bj-disc-actions > a", ".bj-disc-actions > button", ".bj-disc-problem button", ".bj-disc-remote"]) {
      expect(declarationsFor(phone, selector)["min-height"], selector).toBe("44px");
    }
    expect(declarationsFor(phone, '.bj-disc-form input[type="text"]')["min-height"]).toBe("44px");
  });

  it("draw the elapsed counter in the mono face and cyan as text, never a fill", () => {
    const timer = declarationsFor(base, ".bj-disc-timer");
    expect(timer.color).toBe("var(--bj-cyan)");
    expect(timer["font-family"]).toBe("var(--bj-font-mono)");
    expect(timer.background).toBeUndefined();
  });

  it("outline the fallback notice in gold and never fill it", () => {
    const notice = declarationsFor(base, ".bj-disc-fallback");
    expect(notice.border).toBe("1px solid var(--bj-gold)");
    expect(notice.background).toBeUndefined();
  });

  it("set a row's title in the display serif and its notes in the muted color", () => {
    expect(declarationsFor(base, ".bj-disc-title")["font-family"]).toBe("var(--bj-font-display)");
    expect(declarationsFor(base, ".bj-fit-notes p").color).toBe("var(--bj-text-muted)");
    expect(resolve(declarationsFor(base, ".bj-disc-meta").color)).toBe(resolve("var(--bj-text-muted)"));
  });

  it("put the sub-score bars in two columns on a tablet and one on a phone", () => {
    expect(declarationsFor(base, ".bj-fit-bars")["grid-template-columns"]).toBe("minmax(0, 1fr)");
    expect(discoverCss).toMatch(/@media \(min-width: 600px\) and \(max-width: 959px\)/);
  });
});

describe("the saved-searches card", () => {
  const base = withoutMedia(savedCss);
  const phone = blocksOf(savedCss, `@media ${PHONE_QUERY}`);

  it("lets a long label wrap inside its row and keeps the buttons from shrinking the text away", () => {
    expect(declarationsFor(base, ".bj-ss-label")["overflow-wrap"]).toBe("anywhere");
    expect(declarationsFor(base, ".bj-ss-text")["min-width"]).toBe("0");
    expect(declarationsFor(base, ".bj-ss-row").display).toBe("flex");
    expect(declarationsFor(base, ".bj-ss-row")["flex-wrap"]).toBe("wrap");
  });

  it("makes each button at least 44px tall on a phone", () => {
    expect(declarationsFor(phone, ".bj-ss-actions > button")["min-height"]).toBe("44px");
  });

  it("shows an error in the danger color as text", () => {
    expect(declarationsFor(base, ".bj-ss-error").color).toBe("var(--bj-danger)");
  });
});

describe("the Hiring signals sections", () => {
  const base = withoutMedia(hiringCss);
  const phone = blocksOf(hiringCss, `@media ${PHONE_QUERY}`);

  it("are at most 880px, with no card chrome on the blocks that were cards", () => {
    expect(declarationsFor(base, ".bj-hs-page")["max-width"]).toBe("880px");
    const block = declarationsFor(base, ".bj-hs-tab .bj-hs-block");
    expect(block.border).toBe("0");
    expect(block.padding).toBe("0");
    expect(block.background).toBe("none");
  });

  it("put hairline rules above and below the form, not a box", () => {
    const form = declarationsFor(base, ".bj-hs-tab .bj-hs-form-block");
    expect(form["border-top"]).toBe("1px solid var(--bj-border)");
    expect(form["border-bottom"]).toBe("1px solid var(--bj-border)");
  });

  it("wrap a saved role or location inside its row", () => {
    expect(declarationsFor(base, ".bj-hs-tab-search-label")["overflow-wrap"]).toBe("anywhere");
    expect(declarationsFor(base, ".bj-hs-tab-search-text")["min-width"]).toBe("0");
  });

  it("make every control at least 44px tall on a phone", () => {
    const controls = phone.replace(/\s+/g, " ");
    expect(controls).toContain(".bj-hs-tab button");
    expect(declarationsFor(phone, ".bj-hs-tab button")["min-height"]).toBe("44px");
    expect(declarationsFor(phone, ".bj-hs-tab-form")).toEqual({});
    expect(declarationsFor(phone, '.bj-hs-tab-form input[type="text"]')["min-height"]).toBe("44px");
  });

  it("marks a field in error by color and, in the markup, by aria-invalid: color is never the only signal", () => {
    expect(declarationsFor(base, '.bj-hs-tab-form input[aria-invalid="true"]')["border-color"]).toBe("var(--bj-danger)");
    expect(hiringForm).toContain("aria-invalid");
  });
});

describe("the markup that uses them", () => {
  const sources: Array<[string, string]> = [
    ["DiscoverView", discoverView],
    ["DiscoverForm", discoverForm],
    ["ResultRow", resultRow],
    ["ResultsView", resultsView],
    ["FitBlock", fitBlock],
    ["SearchTimer", searchTimer],
    ["SavedSearchesCardView", savedView],
    ["HiringSignalsPageView", hiringPage],
    ["HiringSignalsTabView", hiringTabView],
    ["HiringSignalsTabForm", hiringForm],
    ["HiringSignalsTabResults", hiringResults],
  ];

  it.each(sources)("%s has no inline style attribute, no data: address and no third-party address", (_name, source) => {
    const code = source.replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "");
    expect(code).not.toMatch(/\bstyle=\{/);
    expect(code).not.toMatch(/\bstyle="/);
    expect(code).not.toMatch(/data:/);
    expect(code).not.toMatch(/https?:\/\/(?!jobs\.example)/);
  });

  it.each(sources)("%s uses no em dash in anything it says", (_name, source) => {
    expect(source).not.toContain("—");
  });

  it("gives the page's primary class to nothing but the band: no row, form or card of these pages is bj-primary", () => {
    for (const [, source] of sources) {
      const code = source.replace(/\/\/.*$/gm, "");
      expect(code).not.toMatch(/bj-primary/);
    }
  });
});
