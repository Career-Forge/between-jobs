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
import compactRow from "../components/discover/CompactRow.tsx?raw";
import detailPane from "../components/discover/DetailPane.tsx?raw";
import rowParts from "../components/discover/RowParts.tsx?raw";
import savedFold from "../components/discover/SavedSearchesFold.tsx?raw";
import resultsToolbar from "../components/discover/ResultsToolbar.tsx?raw";
import resultsView from "../components/discover/ResultsView.tsx?raw";
import fitBlock from "../components/discover/FitBlock.tsx?raw";
import searchStatus from "../components/discover/SearchStatus.tsx?raw";
import searchTimer from "../components/discover/SearchTimer.tsx?raw";
import savedView from "../components/SavedSearchesCardView.tsx?raw";
import hiringPage from "../components/HiringSignalsPageView.tsx?raw";
import hiringTabView from "../components/HiringSignalsTabView.tsx?raw";
import hiringForm from "../components/HiringSignalsTabForm.tsx?raw";
import hiringResults from "../components/HiringSignalsTabResults.tsx?raw";
import arrangeSource from "../lib/discoverArrange.ts?raw";

// The Discover, saved-search and Hiring signals stylesheets are read as text (there is no layout engine
// in the test run). What they pin is what the pages rest on: no colors of their own, no gold fill, the
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

  it("never fills anything gold: the band's button is the page's one gold fill", () => {
    for (const rule of rulesOf(css)) {
      for (const property of ["background", "background-color"]) {
        expect(rule.declarations[property] ?? "", `${name} ${rule.selectors.join(", ")}`).not.toContain("--bj-primary");
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

  it("are at most the list measure, 880px, under 1280px, and let the page shrink around long text", () => {
    expect(declarationsFor(base, ".bj-discover")["max-width"]).toBe("880px");
    expect(declarationsFor(base, ".bj-discover")["min-width"]).toBe("0");
    expect(declarationsFor(withoutMedia(savedCss), ".bj-ss")["max-width"]).toBe("880px");
  });

  // Whitespace folded, so a template written over several lines reads as one string.
  const flat = (value: string | undefined) => (value ?? "").replace(/\s+/g, " ").trim();
  const templates = (css: string, selector: string) => ({
    columns: flat(declarationsFor(css, selector)["grid-template-columns"]),
    areas: flat(declarationsFor(css, selector)["grid-template-areas"]),
  });

  it("lay a scored row out as a gutter, then text, the model's line and the actions, on a phone", () => {
    expect(templates(base, ".bj-disc-row")).toEqual({
      columns: "76px minmax(0, 1fr) auto",
      areas: '"score main main" "body body body" "fit fit fit" "actions actions why" "detail detail detail" "problem problem problem" "trail trail trail"',
    });
    expect(declarationsFor(base, ".bj-disc-row")["column-gap"]).toBe("var(--bj-space-3)");
  });

  it("lay a scored row out from 960px as gutter, text and the model's line, with the actions and the toggle under it", () => {
    expect(templates(wide, ".bj-disc-row")).toEqual({
      columns: "100px minmax(0, 1fr) 296px",
      areas: '"score main fit" "score body fit" "score actions why" ". detail detail" ". problem problem" ". trail trail"',
    });
    expect(declarationsFor(wide, ".bj-disc-row")["column-gap"]).toBe("var(--bj-space-5)");
  });

  it("lay a placeholder row out with the hollow ladder in its gutter, and no areas for what it has no part of", () => {
    expect(templates(base, ".bj-disc-row--placeholder")).toEqual({
      columns: "96px minmax(0, 1fr)",
      areas: '"score main" "body body" "actions actions" "problem problem" "trail trail"',
    });
    expect(templates(wide, ".bj-disc-row--placeholder")).toEqual({
      columns: "96px minmax(0, 1fr)",
      areas: '"score main" "score body" "score actions" ". problem" ". trail"',
    });
  });

  it("lay a row past the scoring batch out as one band with no gutter", () => {
    expect(templates(base, ".bj-disc-row--unscored")).toEqual({
      columns: "minmax(0, 1fr) auto",
      areas: '"main main" "body body" "fit actions" "problem problem" "trail trail"',
    });
    expect(templates(wide, ".bj-disc-row--unscored")).toEqual({
      columns: "minmax(0, 1fr) auto auto",
      areas: '"main fit actions" "body . ." "problem problem problem" "trail trail trail"',
    });
  });

  it("give each part of a row the area its name says, and no part an area its row kind does not draw", () => {
    const areas: Record<string, string> = {
      ".bj-disc-main": "main",
      ".bj-disc-score": "score",
      ".bj-disc-body": "body",
      ".bj-fit": "fit",
      ".bj-disc-actions": "actions",
      ".bj-fit-toggle": "why",
      ".bj-fit-detail": "detail",
      ".bj-disc-problem": "problem",
      ".bj-disc-trail": "trail",
    };
    for (const [selector, area] of Object.entries(areas)) {
      expect(declarationsFor(base, selector)["grid-area"], selector).toBe(area);
      expect(declarationsFor(base, selector)["min-width"], selector).toBe("0");
    }
    const kinds = { score: templates(base, ".bj-disc-row").areas, placeholder: templates(base, ".bj-disc-row--placeholder").areas, unscored: templates(base, ".bj-disc-row--unscored").areas };
    for (const area of ["score", "main", "body", "fit", "actions", "why", "detail", "problem", "trail"]) expect(kinds.score, area).toContain(area);
    for (const area of ["score", "main", "body", "actions", "problem", "trail"]) expect(kinds.placeholder, area).toContain(area);
    for (const area of ["main", "body", "fit", "actions", "problem", "trail"]) expect(kinds.unscored, area).toContain(area);
  });

  it("space a row's parts with margins on the parts, never a row-gap, which an empty optional area would still take", () => {
    expect(declarationsFor(base, ".bj-disc-row")["row-gap"]).toBeUndefined();
    expect(declarationsFor(base, ".bj-disc-row")["gap"]).toBeUndefined();
    expect(declarationsFor(wide, ".bj-disc-row")["row-gap"]).toBeUndefined();
    const margins: Record<string, string> = {
      ".bj-disc-body": "var(--bj-space-2)",
      ".bj-fit": "var(--bj-space-2)",
      ".bj-disc-actions": "var(--bj-space-2)",
      ".bj-fit-toggle": "var(--bj-space-2)",
      ".bj-fit-detail": "var(--bj-space-3)",
      ".bj-disc-problem": "var(--bj-space-3)",
      ".bj-disc-trail": "var(--bj-space-2)",
    };
    for (const [selector, margin] of Object.entries(margins)) expect(declarationsFor(base, selector)["margin-top"], selector).toBe(margin);
    expect(declarationsFor(wide, ".bj-fit")["margin-top"]).toBe("0");
    expect(declarationsFor(base, ".bj-fit-preview").margin).toBe("var(--bj-space-1) 0 0");
  });

  it("use none of the layout tricks that would hide a row from assistive technology or need a newer engine", () => {
    const code = discoverCss.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(code).not.toMatch(/display:\s*contents/);
    expect(code).not.toContain(":has(");
  });

  it("let the list bleed one row padding into the page so the text lines up with the page edge", () => {
    expect(declarationsFor(base, ".bj-disc-list").margin).toBe("0 calc(-1 * var(--bj-space-3))");
    expect(declarationsFor(base, ".bj-disc-row").padding).toBe("var(--bj-space-4) var(--bj-space-3)");
    expect(declarationsFor(base, ".bj-disc-row")["align-items"]).toBe("start");
    expect(declarationsFor(base, ".bj-disc-row")["border-bottom"]).toBe("1px solid var(--bj-border)");
  });

  it("wrap a company, a snippet and a title that are one long string instead of widening the page", () => {
    for (const selector of [".bj-disc-title", ".bj-disc-meta", ".bj-disc-snippet", ".bj-disc-signal", ".bj-fit-oneliner", ".bj-fit-preview", ".bj-fit-notes p", ".bj-disc-note", ".bj-disc-fallback", ".bj-disc-sources", ".bj-disc-score"]) {
      expect(declarationsFor(base, selector)["overflow-wrap"], selector).toBe("anywhere");
    }
    for (const selector of [".bj-disc-row", ".bj-disc-main", ".bj-fit", ".bj-disc-results", ".bj-disc-status", ".bj-disc-score-text", ".bj-disc-toolbar", ".bj-disc-tools", ".bj-disc-tool", ".bj-disc-chips", ".bj-disc-sort"]) {
      expect(declarationsFor(base, selector)["min-width"], selector).toBe("0");
    }
  });

  it("make every control at least 44px tall on a phone", () => {
    for (const selector of [".bj-disc-form-actions > button", ".bj-disc-actions > a", ".bj-disc-actions > button", ".bj-disc-problem button", ".bj-disc-remote", ".bj-fit-toggle", ".bj-disc-tools .bj-segmented > button", ".bj-disc-toggle"]) {
      expect(declarationsFor(phone, selector)["min-height"], selector).toBe("44px");
    }
    expect(declarationsFor(phone, '.bj-disc-form input[type="text"]')["min-height"]).toBe("44px");
    expect(declarationsFor(phone, ".bj-discover .bj-state-missing a")["min-height"]).toBe("44px");
  });

  it("draw the elapsed counter in the mono face and link blue as text, never a fill", () => {
    const timer = declarationsFor(base, ".bj-disc-timer");
    expect(timer.color).toBe("var(--bj-link)");
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

  it("put the sub-score bars in one column on a phone and two from 600px", () => {
    expect(declarationsFor(base, ".bj-fit-bars")["grid-template-columns"]).toBe("minmax(0, 1fr)");
    expect(declarationsFor(blocksOf(discoverCss, "@media (min-width: 600px)"), ".bj-fit-bars")["grid-template-columns"]).toBe("minmax(0, 1fr) minmax(0, 1fr)");
  });
});

describe("the wide layout: a compact list beside the chosen row", () => {
  const base = withoutMedia(discoverCss);
  const code = discoverCss.replace(/\/\*[\s\S]*?\*\//g, "");
  const flat = (value: string | undefined) => (value ?? "").replace(/\s+/g, " ").trim();
  // Where a rule's selector first appears in the file: a later rule of the same weight is the one that applies.
  const at = (selector: string) => code.indexOf(`${selector} {`);

  it("states no width of its own: the page is told it is wide and the rules key on the class it then carries", () => {
    expect(code).not.toMatch(/1280|1440/);
    expect(discoverView).toContain('wide ? "bj-discover bj-discover--wide" : "bj-discover"');
    // Every rule for the wide layout names the wide page or markup only the wide page has.
    const wideOnly = /\.bj-discover--wide|\.bj-disc-split|\.bj-disc-row--compact|\.bj-disc-select|\.bj-disc-pane/;
    const start = code.indexOf(".bj-discover--wide {");
    const end = code.indexOf("@media (max-width: 959px)", start);
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start);
    for (const rule of rulesOf(code.slice(start, end))) {
      for (const selector of rule.selectors) expect(selector, selector).toMatch(wideOnly);
    }
    // And no rule outside that part of the file does.
    for (const rule of rulesOf(code.slice(0, start) + code.slice(end))) {
      for (const selector of rule.selectors) expect(selector, selector).not.toMatch(wideOnly);
    }
  });

  it("lets the page take the whole of the shell's column, in a rule that comes after the 880px one", () => {
    expect(declarationsFor(base, ".bj-discover--wide")).toEqual({ "max-width": "none" });
    expect(at(".bj-discover--wide")).toBeGreaterThan(at(".bj-discover"));
  });

  it("puts the form's three fields in one row, the search words taking half of it", () => {
    expect(declarationsFor(base, ".bj-discover--wide .bj-disc-fields")["grid-template-columns"]).toBe("minmax(0, 2fr) minmax(0, 1fr) minmax(0, 1fr)");
    expect(declarationsFor(base, ".bj-discover--wide .bj-disc-field-wide")["grid-column"]).toBe("auto");
    // The field that spans the row of two is the first of the three in the markup.
    expect(discoverForm.indexOf("bj-disc-field-wide")).toBeLessThan(discoverForm.indexOf("<span>Location</span>"));
  });

  it("keeps sentences and callouts to 880px however wide the column is", () => {
    for (const selector of [
      ".bj-discover--wide .bj-disc-form > p",
      ".bj-discover--wide > .bj-state",
      ".bj-discover--wide .bj-disc-results > .bj-disc-note",
      ".bj-discover--wide .bj-disc-fallback",
      ".bj-discover--wide .bj-disc-status",
      ".bj-discover--wide .bj-disc-count",
    ]) {
      expect(declarationsFor(base, selector)["max-width"], selector).toBe("880px");
    }
  });

  it("lays the results out as a list that takes the room left and a pane of 400px", () => {
    const split = declarationsFor(base, ".bj-disc-split");
    expect(split.display).toBe("grid");
    expect(split["grid-template-columns"]).toBe("minmax(0, 1fr) 400px");
    expect(split["align-items"]).toBe("start");
    expect(split["min-width"]).toBe("0");
    const list = declarationsFor(base, ".bj-disc-split-list");
    expect(list["min-width"]).toBe("0");
    // The two lists and the heading between them are spaced as the results block spaces them in one column.
    expect(list.display).toBe(declarationsFor(base, ".bj-disc-results").display);
    expect(list["flex-direction"]).toBe(declarationsFor(base, ".bj-disc-results")["flex-direction"]);
    expect(list.gap).toBe(declarationsFor(base, ".bj-disc-results").gap);
  });

  it("keeps the pane in view while the list scrolls, and scrolls it inside itself past the window's height", () => {
    const pane = declarationsFor(base, ".bj-disc-pane");
    expect(pane.position).toBe("sticky");
    expect(pane.top).toBe("var(--bj-space-5)");
    expect(pane["max-height"]).toBe("calc(100vh - 2 * var(--bj-space-5))");
    expect(pane["overflow-y"]).toBe("auto");
    expect(pane["min-width"]).toBe("0");
    // A hairline at its left, not a card: no fill and no selection colour.
    expect(pane["border-left"]).toBe("1px solid var(--bj-border)");
    expect(pane.background).toBeUndefined();
  });

  it("leaves room inside the pane for a focus ring, which its scrolling would otherwise cut off", () => {
    const padding = declarationsFor(base, ".bj-disc-pane").padding.split(" ").map((value) => Number.parseFloat(resolve(value)));
    const ring = Number.parseFloat(resolve("var(--bj-focus-ring-width)")) + Number.parseFloat(resolve("var(--bj-focus-ring-offset)"));
    expect(padding).toHaveLength(4);
    for (const side of padding) expect(side).toBeGreaterThanOrEqual(ring);
  });

  it("draws a compact row as the gutter with the identity beside it and the actions under the identity", () => {
    const areas = '"score main" "score actions" ". problem"';
    expect(flat(declarationsFor(base, ".bj-disc-row--compact")["grid-template-columns"])).toBe("100px minmax(0, 1fr)");
    expect(flat(declarationsFor(base, ".bj-disc-row--compact")["grid-template-areas"])).toBe(areas);
    expect(flat(declarationsFor(base, ".bj-disc-row--compact.bj-disc-row--placeholder")["grid-template-columns"])).toBe("96px minmax(0, 1fr)");
    expect(flat(declarationsFor(base, ".bj-disc-row--compact.bj-disc-row--placeholder")["grid-template-areas"])).toBe(areas);
    // The gutters are the full row's own widths, so "100 / 100" and "Placeholder" fit on one line here too.
    expect(flat(declarationsFor(blocksOf(discoverCss, "@media (min-width: 960px)"), ".bj-disc-row")["grid-template-columns"]).split(" ")[0]).toBe("100px");
    // No area for what the pane draws.
    expect(areas).not.toMatch(/body|fit|why|detail|trail/);
    // It overrides the full row's grid, so it has to come after every rule that sets one: each of these
    // selectors opens a rule of the full row, at the start of a line.
    for (const selector of [".bj-disc-row", ".bj-disc-row--placeholder", ".bj-disc-row--unscored"]) {
      const rules = [...code.matchAll(new RegExp(`^\\s*${selector.replace(/\./g, "\\.")} \\{`, "gm"))].map((match) => match.index);
      expect(rules.length, selector).toBeGreaterThanOrEqual(2);
      expect(at(".bj-disc-row--compact"), selector).toBeGreaterThan(Math.max(...rules));
    }
  });

  it("draws a compact row that was not scored as one line that wraps, with no gutter", () => {
    const row = declarationsFor(base, ".bj-disc-row--compact.bj-disc-row--unscored");
    expect(row.display).toBe("flex");
    expect(row["flex-wrap"]).toBe("wrap");
    expect(row["align-items"]).toBe("center");
    expect(declarationsFor(base, ".bj-disc-row--compact.bj-disc-row--unscored .bj-disc-main").flex).toBe("1 1 240px");
    for (const part of [".bj-fit", ".bj-disc-actions"]) {
      expect(declarationsFor(base, `.bj-disc-row--compact.bj-disc-row--unscored ${part}`), part).toMatchObject({ flex: "none", "margin-top": "0" });
    }
    // A failed Track takes a whole line under them.
    expect(declarationsFor(base, ".bj-disc-row--compact.bj-disc-row--unscored .bj-disc-problem").flex).toBe("1 1 100%");
  });

  it("keeps the identity ahead of the gutter in the markup, as the full row does: the grid places the gutter, not the order of the source", () => {
    for (const source of [compactRow, resultRow]) {
      expect(source.indexOf('<div className="bj-disc-main">')).toBeGreaterThan(-1);
      expect(source.indexOf('<div className="bj-disc-main">')).toBeLessThan(source.indexOf("<FitScore"));
    }
    expect(code).not.toMatch(/\border\s*:/);
  });

  it("makes the title of a compact row a button that still reads as the title, and styles no state of it", () => {
    const select = declarationsFor(base, "button.bj-disc-select");
    expect(select.font).toBe("inherit");
    expect(select.color).toBe("inherit");
    expect(select["border-color"]).toBe("transparent");
    expect(select["text-align"]).toBe("left");
    expect(select["overflow-wrap"]).toBe("anywhere");
    expect(select["max-width"]).toBe("100%");
    expect(select.background).toBeUndefined();
    // Its padding is the hit area, and it is pulled back by that padding so the words line up.
    expect(select.padding).toBe("2px var(--bj-space-2)");
    expect(select.margin).toBe("0 0 0 calc(-1 * var(--bj-space-2))");
    // Which row is on show is the shared selection fill, turned on by aria-pressed: nothing here names a state.
    expect(code).not.toMatch(/aria-pressed|aria-current|aria-selected|\.bj-selectable|--bj-royal|--bj-selected/);
    expect(compactRow).toContain('className="bj-disc-select bj-selectable"');
    expect(compactRow).toContain("aria-pressed={selected}");
    expect(compactRow).toContain("aria-controls={paneId}");
  });

  it("gives the title button a hover that is not the selection: one surface step, no outline", () => {
    const hover = declarationsFor(base, "button.bj-disc-select:hover");
    expect(hover).toEqual({ "border-color": "transparent", background: "var(--bj-hover-bg-raised)" });
    // The shared rule for a pressed control outweighs it (two classes or attributes against one and an element),
    // so a chosen title keeps its fill under the pointer.
    expect(declarationsFor(withoutMedia(tokensCss), '.bj-selectable[aria-pressed="true"]:hover').background).toBe("var(--bj-selected-bg-hover)");
  });

  it("folds nothing in the pane: the posting's text and the model's line are whole, and the bars are one to a line", () => {
    for (const selector of [".bj-disc-pane .bj-disc-snippet", ".bj-disc-pane .bj-fit-oneliner"]) {
      expect(declarationsFor(base, selector), selector).toEqual({ display: "block", "-webkit-line-clamp": "none", overflow: "visible" });
    }
    expect(declarationsFor(base, ".bj-disc-pane .bj-fit-bars")["grid-template-columns"]).toBe("minmax(0, 1fr)");
    // The row's own clamps are untouched.
    expect(declarationsFor(base, ".bj-disc-snippet")["-webkit-line-clamp"]).toBe("2");
  });

  it("spaces the pane's parts with its gap in place of the top margins they carry in a row", () => {
    expect(declarationsFor(base, ".bj-disc-pane").gap).toBe("var(--bj-space-3)");
    expect(declarationsFor(base, ".bj-disc-pane > *")).toEqual({ "margin-top": "0" });
    for (const part of [".bj-disc-actions", ".bj-fit-detail", ".bj-disc-problem", ".bj-disc-trail", ".bj-disc-body"]) {
      expect(at(".bj-disc-pane > *"), part).toBeGreaterThan(at(part));
    }
  });

  it("sets the score beside the identity in the pane, with no gutter for a row that was not scored", () => {
    expect(flat(declarationsFor(base, ".bj-disc-pane-head")["grid-template-columns"])).toBe("100px minmax(0, 1fr)");
    expect(flat(declarationsFor(base, ".bj-disc-pane-head")["grid-template-areas"])).toBe('"score main"');
    expect(flat(declarationsFor(base, ".bj-disc-pane-head--placeholder")["grid-template-columns"])).toBe("96px minmax(0, 1fr)");
    expect(flat(declarationsFor(base, ".bj-disc-pane-head--unscored")["grid-template-columns"])).toBe("minmax(0, 1fr)");
    expect(flat(declarationsFor(base, ".bj-disc-pane-head--unscored")["grid-template-areas"])).toBe('"main"');
    // The three classes are the three kinds of fit, which is what the pane builds the class from.
    expect(detailPane).toContain("bj-disc-pane-head--${fit.kind}");
  });

  it("names the pane in small mono capitals, muted, from the type scale", () => {
    const label = declarationsFor(base, ".bj-disc-pane-label");
    expect(label["font-family"]).toBe("var(--bj-font-mono)");
    expect(label["font-size"]).toBe("var(--bj-text-eyebrow)");
    expect(label["text-transform"]).toBe("uppercase");
    expect(label.color).toBe("var(--bj-text-muted)");
    expect(declarationsFor(base, ".bj-disc-pane-heading")["font-size"]).toBe("var(--bj-text-small)");
  });
});

describe("the score, the tiers and the clamps", () => {
  const base = withoutMedia(discoverCss);
  const wide = blocksOf(discoverCss, "@media (min-width: 960px)");
  const phone = blocksOf(discoverCss, `@media ${PHONE_QUERY}`);
  const code = discoverCss.replace(/\/\*[\s\S]*?\*\//g, "");

  it("set every font size from the type scale, apart from the literals the page already had and the one a phone needs", () => {
    const sizes = rulesOf(discoverCss).flatMap((rule) => (rule.declarations["font-size"] === undefined ? [] : [{ selector: rule.selectors.join(", "), value: rule.declarations["font-size"] }]));
    const literals = sizes.filter((entry) => !/^var\(--bj-text-[a-z0-9-]+\)$/.test(entry.value));
    expect(literals).toEqual([
      { selector: ".bj-disc-title", value: "20px" },
      { selector: ".bj-fit-notes p", value: "12.5px" },
      { selector: '.bj-disc-form input[type="text"]', value: "16px" },
    ]);
  });

  it("sets a field's text at 16px under 960px, the size below which a phone zooms the page when a field takes focus, and only there", () => {
    expect(declarationsFor(phone, '.bj-disc-form input[type="text"]')["font-size"]).toBe("16px");
    expect(declarationsFor(base, '.bj-disc-form input[type="text"]')["font-size"]).toBeUndefined();
    expect(declarationsFor(wide, '.bj-disc-form input[type="text"]')["font-size"]).toBeUndefined();
  });

  it("uses no shadow, gradient, transition or animation, and no rule's own colour literal", () => {
    expect(code).not.toMatch(/box-shadow|gradient|transition|animation|@keyframes/);
  });

  it("lifts a row in the engine's Strong bin (shown as Good) onto the surface step and does nothing else to it", () => {
    expect(declarationsFor(base, ".bj-disc-row--strong")).toEqual({ background: "var(--bj-surface)" });
  });

  it("does not dim, recolor, outline or lift any other tier or kind of row", () => {
    for (const selector of [".bj-disc-row--good", ".bj-disc-row--mixed", ".bj-disc-row--poor", ".bj-disc-row--placeholder", ".bj-disc-row--unscored", ".bj-disc-row--open"]) {
      for (const rule of rulesOf(discoverCss)) {
        if (!rule.selectors.includes(selector)) continue;
        for (const property of ["color", "background", "background-color", "opacity", "filter", "border", "border-color", "outline", "box-shadow"]) {
          expect(rule.declarations[property], `${selector} ${property}`).toBeUndefined();
        }
      }
    }
    expect(code).not.toMatch(/\.bj-disc-row--(?:good|mixed|poor)[^{]*\{[^}]*(?:color|background|opacity)/);
    expect(declarationsFor(base, ".bj-disc-row--unscored .bj-disc-title").color).toBeUndefined();
    expect(declarationsFor(base, ".bj-disc-row--unscored .bj-disc-title")["font-size"]).toBe("var(--bj-text-body)");
  });

  it("sets the number in plain ivory text, and uses gold nowhere but the fallback notice's outline", () => {
    const gold = rulesOf(discoverCss).filter((rule) => Object.values(rule.declarations).some((value) => value.includes("--bj-gold")));
    expect(gold.map((rule) => rule.selectors.join(", ")).sort()).toEqual([".bj-disc-fallback"]);
    expect(declarationsFor(base, ".bj-disc-score-num").color).toBe("var(--bj-text)");
    expect(declarationsFor(wide, ".bj-disc-score-num").color).toBeUndefined();
    expect(declarationsFor(base, ".bj-disc-fallback").border).toBe("1px solid var(--bj-gold)");
  });

  it("colours nothing of its own by fit grade: the ladder's cells are the only grade colour on a row, and 'Not scored' is the grey of no grade", () => {
    const graded = rulesOf(discoverCss).filter((rule) => Object.values(rule.declarations).some((value) => /--bj-fit-(?:good|decent|bad)/.test(value)));
    expect(graded).toEqual([]);
    expect(declarationsFor(base, ".bj-fit-unscored").color).toBe("var(--bj-fit-unscored)");
  });

  it("sets the number from the type scale only: the title size, and the page title size from 960px", () => {
    expect(declarationsFor(base, ".bj-disc-score-num")["font-size"]).toBe("var(--bj-text-title)");
    expect(declarationsFor(wide, ".bj-disc-score-num")["font-size"]).toBe("var(--bj-text-h1)");
    expect(declarationsFor(base, ".bj-disc-score-num")["font-family"]).toBe("var(--bj-font-display)");
    expect(declarationsFor(base, ".bj-disc-score-num")["font-variant-numeric"]).toBe("lining-nums tabular-nums");
  });

  it("sets '/ 100' muted and small, and the grade word in the text color, neither gold nor a grade's colour", () => {
    expect(declarationsFor(base, ".bj-disc-score-of").color).toBe("var(--bj-text-muted)");
    expect(declarationsFor(base, ".bj-disc-score-of")["font-size"]).toBe("var(--bj-text-trail)");
    expect(declarationsFor(base, ".bj-disc-score-tier").color).toBe("var(--bj-text)");
    expect(declarationsFor(base, ".bj-disc-score-tier")["font-size"]).toBe("var(--bj-text-small)");
  });

  it("narrows the ladder's cells to fit the gutter", () => {
    expect(declarationsFor(base, ".bj-disc-ladder .bj-ladder-cell").width).toBe("12px");
  });

  it("clamps the posting text of a scored row to two lines and the model's line to three, and releases both once the row is open", () => {
    expect(declarationsFor(base, ".bj-disc-snippet")["-webkit-line-clamp"]).toBe("2");
    expect(declarationsFor(base, ".bj-fit-oneliner")["-webkit-line-clamp"]).toBe("3");
    // The API holds the model's line to 100 characters, which is three lines of the body size down to 320px: the clamp
    // is a guard against a longer string, and the line is never cut for any real answer.
    expect(declarationsFor(wide, ".bj-fit-oneliner")["-webkit-line-clamp"]).toBeUndefined();
    for (const selector of [".bj-disc-row--open .bj-disc-snippet", ".bj-disc-row--open .bj-fit-oneliner"]) {
      const open = declarationsFor(base, selector);
      expect(open["-webkit-line-clamp"], selector).toBe("none");
      expect(open.overflow, selector).toBe("visible");
      expect(open.display, selector).toBe("block");
    }
    for (const selector of [".bj-disc-snippet", ".bj-fit-oneliner"]) {
      expect(declarationsFor(base, selector).display, selector).toBe("-webkit-box");
      expect(declarationsFor(base, selector)["-webkit-box-orient"], selector).toBe("vertical");
      expect(declarationsFor(base, selector).overflow, selector).toBe("hidden");
    }
  });

  it("clamps the posting text of a row that has no Why this score to four lines, and gives it nothing to release them: a row past the batch and a placeholder row", () => {
    for (const selector of [".bj-disc-row--unscored .bj-disc-snippet", ".bj-disc-row--placeholder .bj-disc-snippet"]) {
      const capped = declarationsFor(base, selector);
      expect(capped["-webkit-line-clamp"], selector).toBe("4");
      // The box, its orientation and the hidden overflow are the base rule's: nothing here lets the text out.
      expect(capped.display, selector).toBeUndefined();
      expect(capped.overflow, selector).toBeUndefined();
      expect(wide.includes(selector), selector).toBe(false);
    }
    // The release belongs to the open scored row alone: no rule that names an unscored or a placeholder row sets "none".
    for (const rule of rulesOf(base)) {
      if (rule.selectors.some((selector) => /--(unscored|placeholder)/.test(selector) && selector.includes(".bj-disc-snippet"))) {
        expect(rule.declarations["-webkit-line-clamp"], rule.selectors.join(", ")).not.toBe("none");
        expect(rule.declarations.overflow, rule.selectors.join(", ")).not.toBe("visible");
      }
    }
  });

  it("sets the model's line at the body size, and at the control size from 960px", () => {
    expect(declarationsFor(base, ".bj-fit-oneliner")["font-size"]).toBe("var(--bj-text-body)");
    expect(declarationsFor(base, ".bj-fit-oneliner").color).toBe("var(--bj-text)");
    expect(declarationsFor(wide, ".bj-fit-oneliner")["font-size"]).toBe("var(--bj-text-control)");
    expect(declarationsFor(base, ".bj-fit-preview").color).toBe("var(--bj-text-muted)");
    expect(declarationsFor(base, ".bj-fit-preview")["font-size"]).toBe("var(--bj-text-small)");
  });

  it("puts the toggle at the right of its row on a phone and the left from 960px, level with the actions", () => {
    expect(declarationsFor(base, ".bj-fit-toggle")["justify-self"]).toBe("end");
    expect(declarationsFor(wide, ".bj-fit-toggle")["justify-self"]).toBe("start");
    expect(declarationsFor(base, ".bj-fit-toggle")["align-self"]).toBe("center");
  });

  it("puts the toggle on the last line of the row's controls on a phone, so a narrow row that wraps its actions still reads in the order it is tabbed", () => {
    expect(declarationsFor(phone, ".bj-fit-toggle")["align-self"]).toBe("end");
  });

  it("gives the toggle a narrower side padding on a phone, so View posting and Track (and Open workspace at 360px) fit beside it, and keeps the wider one from 960px", () => {
    expect(declarationsFor(phone, ".bj-fit-toggle").padding).toBe("0 var(--bj-space-1)");
    expect(declarationsFor(base, ".bj-fit-toggle").padding).toBe("0 var(--bj-space-2)");
    // From 960px the toggle starts the column and is pulled back by its own padding, which is the wider one there.
    expect(declarationsFor(wide, ".bj-fit-toggle")["margin-left"]).toBe("calc(-1 * var(--bj-space-2))");
    expect(declarationsFor(wide, ".bj-fit-toggle").padding).toBeUndefined();
    // Its hit area stays 44px tall.
    expect(declarationsFor(phone, ".bj-fit-toggle")["min-height"]).toBe("44px");
  });

  it("sits the score and the identity on one baseline from 960px, on every kind of row that has a number", () => {
    for (const selector of [".bj-disc-row:not(.bj-disc-row--placeholder) .bj-disc-score", ".bj-disc-row:not(.bj-disc-row--placeholder) .bj-disc-main"]) {
      expect(declarationsFor(wide, selector)["align-self"], selector).toBe("baseline");
      expect(declarationsFor(base, selector)["align-self"], selector).toBeUndefined();
    }
  });

  it("sets the results heading and the saved-searches title from the same step of the type scale", () => {
    const heading = declarationsFor(base, ".bj-disc-heading");
    const title = declarationsFor(base, ".bj-disc-saved-title");
    expect(heading["font-size"]).toBe("var(--bj-text-title)");
    expect(title["font-size"]).toBe(heading["font-size"]);
    expect(title["font-weight"]).toBe(heading["font-weight"]);
    expect(title["font-family"]).toBe(heading["font-family"]);
  });

  it("sets 'Not scored' level with the actions on a row past the scoring batch, on a phone and from 960px", () => {
    expect(declarationsFor(base, ".bj-disc-row--unscored .bj-fit")["align-self"]).toBe("center");
    expect(declarationsFor(wide, ".bj-disc-row--unscored .bj-fit")["align-self"]).toBe("start");
    expect(declarationsFor(wide, ".bj-disc-row--unscored .bj-disc-actions")["margin-top"]).toBe("0");
  });
});

describe("the toolbar", () => {
  const base = withoutMedia(discoverCss);
  const phone = blocksOf(discoverCss, `@media ${PHONE_QUERY}`);

  it("lays its groups and its switch out in a wrapping flex row, so six chips and a sort never widen the page", () => {
    const tools = declarationsFor(base, ".bj-disc-tools");
    expect(tools.display).toBe("flex");
    expect(tools["flex-wrap"]).toBe("wrap");
    expect(tools["align-items"]).toBe("center");
    expect(tools.gap).toMatch(/^var\(--bj-space-\d\) var\(--bj-space-5\)$/);
    expect(declarationsFor(base, ".bj-disc-tool").display).toBe("flex");
  });

  it("sets a group's name in the plain sans, small and muted: not the mono capitals the band's eyebrow uses", () => {
    const label = declarationsFor(base, ".bj-disc-tool-label");
    expect(label["font-family"]).toBe("var(--bj-font-body)");
    expect(label["font-size"]).toBe("var(--bj-text-small)");
    expect(label.color).toBe("var(--bj-text-muted)");
    for (const property of ["text-transform", "letter-spacing"]) expect(label[property]).toBeUndefined();
  });

  it("sets a chip's count in the mono face with tabular figures, and the count line muted and wrapping", () => {
    expect(declarationsFor(base, ".bj-disc-chip-count")["font-family"]).toBe("var(--bj-font-mono)");
    expect(declarationsFor(base, ".bj-disc-chip-count")["font-variant-numeric"]).toBe("tabular-nums");
    const count = declarationsFor(base, ".bj-disc-count");
    expect(count.color).toBe("var(--bj-text-muted)");
    expect(count["overflow-wrap"]).toBe("anywhere");
    expect(count.margin).toBe("var(--bj-space-1) 0 0");
  });

  it("mirrors the form's Remote only switch, in size, color and cursor", () => {
    const remote = declarationsFor(base, ".bj-disc-remote");
    const toggle = declarationsFor(base, ".bj-disc-toggle");
    for (const property of ["display", "align-items", "gap", "font-size", "color", "cursor"]) expect(toggle[property], property).toBe(remote[property]);
  });

  it("narrows a chip's padding on a phone so more share a line, and keeps it 44px tall", () => {
    const chip = declarationsFor(phone, ".bj-disc-tools .bj-segmented > button");
    expect(chip.padding).toBe("6px 10px");
    expect(chip["min-height"]).toBe("44px");
  });

  it("defines no selection style of its own: the chosen chip is the shared royal fill", () => {
    const code = discoverCss.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(code).not.toMatch(/aria-pressed|\.bj-selectable|--bj-royal|--bj-selected/);
  });

  it("uses only the pattern the shared stylesheet defines for a group of choices", () => {
    expect(resultsToolbar).toContain("bj-segmented");
    expect(resultsToolbar).toContain("bj-selectable");
    expect(resultsToolbar).not.toContain("bj-view-toggle");
    expect(resultsToolbar).not.toContain("RadioSegments");
    expect(resultsToolbar).not.toMatch(/onKeyDown|onKeyUp|addEventListener|tabIndex/);
  });
});

describe("the saved-searches row and the form's actions", () => {
  const base = withoutMedia(discoverCss);
  const phone = blocksOf(discoverCss, `@media ${PHONE_QUERY}`);

  it("draws the row as a full-width flex button with a hairline under it and no box, and a heading with no margin", () => {
    expect(declarationsFor(base, ".bj-disc-saved-heading").margin).toBe("0");
    const toggle = declarationsFor(base, ".bj-disc-saved-toggle");
    expect(toggle.display).toBe("flex");
    expect(toggle.width).toBe("100%");
    expect(toggle["border-bottom"]).toBe("1px solid var(--bj-border)");
    expect(toggle.border).toBe("0");
    expect(toggle.background).toBe("transparent");
    expect(declarationsFor(phone, ".bj-disc-saved-toggle")["min-height"]).toBe("44px");
  });

  it("sets its name in the display serif at the title size, and its hint muted, small and wrapping", () => {
    const title = declarationsFor(base, ".bj-disc-saved-title");
    expect(title["font-family"]).toBe("var(--bj-font-display)");
    expect(title["font-size"]).toBe("var(--bj-text-title)");
    expect(title["font-weight"]).toBe("500");
    expect(title.flex).toBe("none");
    const hint = declarationsFor(base, ".bj-disc-saved-hint");
    expect(hint.color).toBe("var(--bj-text-muted)");
    expect(hint["font-size"]).toBe("var(--bj-text-small)");
    expect(hint["overflow-wrap"]).toBe("anywhere");
    expect(hint["min-width"]).toBe("0");
    expect(declarationsFor(base, ".bj-disc-saved")["min-width"]).toBe("0");
  });

  it("keeps the card inside it from adding a bottom margin of its own", () => {
    expect(declarationsFor(base, ".bj-disc-saved-body .bj-ss")["margin-bottom"]).toBe("0");
  });

  it("takes the left padding off the quiet Save this search so it lines up with the fields", () => {
    expect(declarationsFor(base, ".bj-disc-form-actions > .bj-quiet")["padding-inline"]).toBe("0");
  });

  it("uses no color but the muted and ordinary text colors, and no gold, gold or royal", () => {
    for (const selector of [".bj-disc-saved-title", ".bj-disc-saved-hint", ".bj-disc-saved-glyph", ".bj-disc-saved-toggle"]) {
      for (const value of Object.values(declarationsFor(base, selector))) expect(value, selector).not.toMatch(/--bj-(?:gold|gold|royal|violet|emerald|danger)/);
    }
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

describe("the form fields of Discover and Hiring signals", () => {
  const phoneOf = (css: string) => blocksOf(css, "@media (max-width: 959px)");

  it("draw a text field's edge in the outline color, which clears 3:1 against the page, not the hairline rule color", () => {
    expect(declarationsFor(discoverCss, '.bj-disc-form input[type="text"]')["border-color"]).toBe("var(--bj-outline)");
    expect(declarationsFor(hiringCss, '.bj-hs-tab-form input[type="text"]')["border-color"]).toBe("var(--bj-outline)");
    expect(declarationsFor(hiringCss, ".bj-hs-tab-wording select")["border"]).toBe("1px solid var(--bj-outline)");
  });

  it("set a placeholder in the muted text color, not the browser's dimmer default", () => {
    expect(declarationsFor(discoverCss, ".bj-disc-form input::placeholder")["color"]).toBe("var(--bj-text-muted)");
    expect(declarationsFor(hiringCss, ".bj-hs-tab-form input::placeholder")["color"]).toBe("var(--bj-text-muted)");
  });

  it("keep the Open Settings link of an empty You.com result 44px tall on a phone", () => {
    expect(declarationsFor(phoneOf(hiringCss), ".bj-hs-tab .bj-hs-empty-link a")["min-height"]).toBe("44px");
    expect(hiringResults).toContain("bj-hs-empty-link");
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

  it("close the form with a hairline rule (the band's own rule is above it), not a box", () => {
    const form = declarationsFor(base, ".bj-hs-tab .bj-hs-form-block");
    expect(form["border-top"]).toBeUndefined();
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
    ["CompactRow", compactRow],
    ["DetailPane", detailPane],
    ["RowParts", rowParts],
    ["ResultsView", resultsView],
    ["ResultsToolbar", resultsToolbar],
    ["FitBlock", fitBlock],
    ["SavedSearchesFold", savedFold],
    ["SearchStatus", searchStatus],
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

describe("what the results hold", () => {
  const sources: Array<[string, string]> = [
    ["ResultsView", resultsView],
    ["ResultsToolbar", resultsToolbar],
    ["ResultRow", resultRow],
    ["CompactRow", compactRow],
    ["DetailPane", detailPane],
    ["RowParts", rowParts],
    ["FitBlock", fitBlock],
    ["SavedSearchesFold", savedFold],
    ["lib/discoverArrange.ts", arrangeSource],
  ];

  it.each(sources)("%s keeps a person's choices in memory only: no storage of any kind", (_name, source) => {
    const code = source.replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "");
    expect(code).not.toMatch(/localStorage|sessionStorage|indexedDB|document\.cookie|browserStorage/);
  });
});
