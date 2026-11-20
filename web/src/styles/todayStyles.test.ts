import { describe, expect, it } from "vitest";
import { PHONE_QUERY, WIDE_QUERY } from "../lib/useMediaQuery";
import tokensCss from "../tokens.css?raw";
import { blockOf, declarationsFor, horizontalPaddingPx, rulesOf, tokenResolver, withoutMedia } from "../testing/css";
import todayCss from "./today.css?raw";
import pageSource from "../pages/Today.tsx?raw";

// today.css is read as text (there is no layout engine in the test run). What it pins is what the page
// rests on: the two columns at 1280px and up, the 44px targets on a phone, that no rule fills anything
// gold, that it defines no color of its own, and that the held-back card's only red is a mark.

// Every block under one query, joined: a section of the file keeps its own query beside its rules, so
// the same query may appear more than once.
function blocksOf(header: string): string {
  const parts: string[] = [];
  let rest = todayCss;
  for (;;) {
    const block = blockOf(rest, header);
    if (block === null) return parts.join("\n");
    parts.push(block);
    rest = rest.slice(rest.indexOf(block) + block.length);
  }
}

const base = withoutMedia(todayCss);
const wide = blocksOf("@media (min-width: 1280px)");
const mid = blocksOf("@media (min-width: 960px)");
const phone = blocksOf("@media (max-width: 959px)");
const resolve = tokenResolver(tokensCss);

// The band's quiet Dismiss is pulled left by its own padding to line its words up with the text, but
// not on a phone, where that would put its focus ring (2px outline, 2px offset) off the screen.
describe("the band's quiet Dismiss", () => {
  it("lines up with the text on a wide screen and keeps its focus ring on screen on a phone", () => {
    expect(declarationsFor(base, ".bj-band-item .bj-today-item-actions")["margin-left"]).toBe("calc(-1 * var(--bj-space-4))");
    expect(declarationsFor(phone, ".bj-band-item .bj-today-item-actions")["margin-left"]).toBe("0");
  });
});
const code = todayCss.replace(/\/\*[\s\S]*?\*\//g, "");

describe("today.css", () => {
  it("was loaded as text and holds the three width blocks", () => {
    expect(todayCss.length).toBeGreaterThan(1000);
    expect([wide, mid, phone].every((block) => block !== "")).toBe(true);
  });

  it("defines no color: no hex value, no rgb or hsl, only tokens", () => {
    expect(code).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(code).not.toMatch(/\b(?:rgb|rgba|hsl|hsla)\(/);
  });

  it("only uses tokens that tokens.css defines", () => {
    const defined = new Set([...tokensCss.matchAll(/(--[\w-]+)\s*:/g)].map((match) => match[1]));
    const used = new Set([...todayCss.matchAll(/var\((--[\w-]+)/g)].map((match) => match[1]));
    expect([...used].filter((name) => !defined.has(name))).toEqual([]);
  });

  it("never fills anything gold: the band's button is the page's one gold fill", () => {
    for (const rule of rulesOf(todayCss)) {
      for (const property of ["background", "background-color"]) {
        expect(rule.declarations[property] ?? "").not.toContain("--bj-primary");
      }
    }
  });

  it("stacks at the shell's phone breakpoint, the same one the components switch on", () => {
    expect(blocksOf(`@media ${PHONE_QUERY}`)).toBe(phone);
    expect(phone).not.toBe("");
    // Every media query the file uses is one of the three widths, so none can drift from the others.
    const queries = [...todayCss.matchAll(/@media ([^{]+)\{/g)].map((match) => match[1].trim());
    expect([...new Set(queries)].sort()).toEqual(["(max-width: 959px)", "(min-width: 1280px)", "(min-width: 960px)"]);
  });
});

describe("the two columns", () => {
  it("are a feed that takes the room left beside a right column of 320px, from 1280px up", () => {
    const grid = declarationsFor(wide, ".bj-today-grid");
    expect(grid.display).toBe("grid");
    expect(grid["grid-template-columns"]).toBe("minmax(0, 1fr) 320px");
    expect(grid["align-items"]).toBe("start");
    expect(todayCss).toMatch(/@media \(min-width: 1280px\)/);
  });

  it("start at the width the page switches its markup on, so the styles and the markup cannot disagree", () => {
    expect(blocksOf(`@media ${WIDE_QUERY}`)).toBe(wide);
    expect(wide).not.toBe("");
    expect(pageSource).toContain("useIsWide()");
  });

  it("keep the right column in view, let it scroll inside itself, and rule it off with a 1px royal line", () => {
    const side = declarationsFor(wide, ".bj-today-side");
    expect(side.position).toBe("sticky");
    expect(side.top).toBe("var(--bj-space-5)");
    expect(side["max-height"]).toBe("calc(100vh - 2 * var(--bj-space-5))");
    expect(side["overflow-y"]).toBe("auto");
    expect(side["border-left"]).toBe("1px solid var(--bj-royal)");
    expect(side["min-width"]).toBe("0");
    // Stacked with a gap: the checklist, the Pipeline card, the trail.
    expect(side.display).toBe("flex");
    expect(side["flex-direction"]).toBe("column");
    expect(side.gap).toBe("var(--bj-space-5)");
  });

  it("leave room inside the right column for a focus ring, which its scrolling would otherwise cut off", () => {
    const [left, right] = horizontalPaddingPx(Object.fromEntries(Object.entries(declarationsFor(wide, ".bj-today-side")).map(([property, value]) => [property, value.replace(/var\((--[\w-]+)\)/g, (_, name: string) => resolve(`var(${name})`))])));
    const ring = Number.parseFloat(resolve("var(--bj-focus-ring-width)")) + Number.parseFloat(resolve("var(--bj-focus-ring-offset)"));
    expect(left).toBeGreaterThanOrEqual(ring);
    expect(right).toBeGreaterThanOrEqual(ring);
  });

  it("are not rules at any narrower width: the grid and the column exist only inside the 1280px blocks", () => {
    expect(declarationsFor(base, ".bj-today-grid")).toEqual({});
    expect(declarationsFor(base, ".bj-today-side")).toEqual({});
    expect(declarationsFor(base, ".bj-today-margin")).toEqual({});
    expect(declarationsFor(phone, ".bj-today-grid")).toEqual({});
    expect(declarationsFor(mid, ".bj-today-grid")).toEqual({});
  });

  it("use the shell's 1440px column and do not lift its cap, padding it 32px where the two columns need the room", () => {
    const main = declarationsFor(wide, ".bj-main:has(.bj-today)");
    expect(main).toEqual({ padding: "40px 32px" });
    expect(todayCss.replace(/\/\*[\s\S]*?\*\//g, "")).not.toMatch(/max-width:\s*none/);
  });

  it("cap the feed at the list measure, 880px, under 1280px, and let it shrink inside the grid", () => {
    const feed = declarationsFor(base, ".bj-today-main");
    expect(feed["max-width"]).toBe("880px");
    expect(feed["min-width"]).toBe("0");
  });

  it("cap the feed's content at 1000px from 1280px, in a rule that comes after the 880px one so it applies", () => {
    expect(declarationsFor(wide, ".bj-today-main")).toEqual({ "max-width": "1000px" });
    expect(code.indexOf("max-width: 1000px")).toBeGreaterThan(code.indexOf("max-width: 880px"));
  });
});

describe("the checklist in the right column", () => {
  it("stacks a step in the order the markup has it, and drops the rule and the margin the one column gives it", () => {
    expect(declarationsFor(wide, ".bj-today-side .bj-setup-step")["grid-template-columns"]).toBe("minmax(0, 1fr)");
    expect((declarationsFor(wide, ".bj-today-side .bj-setup-step")["grid-template-areas"] ?? "").replace(/\s+/g, " ")).toBe('"state" "link" "note" "retry"');
    expect(declarationsFor(wide, ".bj-today-side > .bj-setup")["border-top"]).toBe("0");
    expect(declarationsFor(wide, ".bj-today-side > .bj-setup-line")["margin-bottom"]).toBe("0");
    expect(declarationsFor(wide, ".bj-today-side .bj-setup-retry")["justify-self"]).toBe("start");
  });

  it("is laid out that way only there: the one column's own rules are as they were", () => {
    expect(declarationsFor(base, ".bj-setup-step")["grid-template-columns"]).toBe("9em minmax(0, 1fr) auto");
    expect(declarationsFor(base, ".bj-setup")["border-top"]).toBe("1px solid var(--bj-border)");
    // The column's rules come after the ones they override.
    const own = [...code.matchAll(/(?<!\.bj-today-side )\.bj-setup-step \{/g)].map((match) => match.index);
    expect(own.length).toBeGreaterThanOrEqual(2);
    expect(Math.max(...own)).toBeLessThan(code.indexOf(".bj-today-side .bj-setup-step"));
  });
});

describe("the Pipeline card", () => {
  it("draws a stage as one link line: the name at the left, the count at the right, with a hairline under it", () => {
    const line = declarationsFor(base, ".bj-pipeline-line");
    expect(line.display).toBe("flex");
    expect(line["justify-content"]).toBe("space-between");
    expect(line["border-bottom"]).toBe("1px solid var(--bj-border)");
    expect(resolve(line["font-size"])).toBe(resolve("var(--bj-text-control)"));
    expect(declarationsFor(base, ".bj-pipeline-lines")["list-style"]).toBe("none");
  });

  it("is at least 24px tall on a wide screen and 44px on a phone", () => {
    expect(Number.parseFloat(declarationsFor(base, ".bj-pipeline-line")["min-height"])).toBeGreaterThanOrEqual(24);
    expect(declarationsFor(phone, ".bj-pipeline-line")["min-height"]).toBe("44px");
  });

  it("sets a count in the mono face with tabular figures, and lets a long stage name wrap", () => {
    const count = declarationsFor(base, ".bj-pipeline-count");
    expect(count["font-family"]).toBe("var(--bj-font-mono)");
    expect(count["font-variant-numeric"]).toBe("tabular-nums");
    expect(declarationsFor(base, ".bj-pipeline-stage")["overflow-wrap"]).toBe("anywhere");
    expect(declarationsFor(base, ".bj-pipeline")["min-width"]).toBe("0");
    // As the last section of the one-column page it is no wider than the feed above it.
    expect(declarationsFor(base, ".bj-pipeline")["max-width"]).toBe(declarationsFor(base, ".bj-today-main")["max-width"]);
  });

  it("colors nothing by stage: a stage is a word and a number, never a status color", () => {
    const rules = rulesOf(todayCss).filter((rule) => rule.selectors.some((selector) => selector.includes("bj-pipeline")));
    const colors = rules.flatMap((rule) => Object.entries(rule.declarations).filter(([property]) => property === "color" || property.startsWith("background") || property === "border-color").map(([, value]) => value));
    expect([...new Set(colors)].sort()).toEqual(["var(--bj-text)", "var(--bj-text-muted)"]);
  });
});

describe("the feed rows", () => {
  it("are a marker and a body, with the controls on the right from 960px and under the text below", () => {
    expect(declarationsFor(base, ".bj-today-row")["grid-template-columns"]).toBe("20px minmax(0, 1fr)");
    expect(declarationsFor(mid, ".bj-today-row")["grid-template-columns"]).toBe("20px minmax(0, 1fr) auto");
    expect(declarationsFor(mid, ".bj-today-row-actions")["grid-column"]).toBe("3");
    expect(declarationsFor(base, ".bj-today-row-actions")["grid-column"]).toBe("2");
  });

  it("let long user text wrap inside its column instead of widening the page", () => {
    for (const selector of [".bj-today-row-body", ".bj-today-main", ".bj-today"]) {
      expect(declarationsFor(base, selector)["min-width"], selector).toBe("0");
    }
    for (const selector of [".bj-today-title", ".bj-today-detail", ".bj-today-quote", ".bj-today-chip", ".bj-setup-note", ".bj-bg-line"]) {
      expect(declarationsFor(base, selector)["overflow-wrap"], selector).toBe("anywhere");
    }
  });

  it("make every control at least 44px tall on a phone", () => {
    const controls = phone.replace(/\s+/g, " ");
    expect(controls).toContain(".bj-today-row-actions > button");
    expect(declarationsFor(phone, "button.bj-today-action")["min-height"]).toBe("44px");
    expect(declarationsFor(phone, "a.bj-today-action")["min-height"]).toBe("44px");
  });

  it("draws a held-back resume's red as a mark and nothing else: the marker is a color, never a fill or an outline", () => {
    const marker = declarationsFor(base, ".bj-today-marker--danger");
    expect(marker).toEqual({ color: "var(--bj-danger)" });
    // No other rule of this file turns anything danger-colored except the two crosses.
    const reds = rulesOf(todayCss).filter((rule) => Object.values(rule.declarations).some((value) => value.includes("--bj-danger")));
    expect(reds.flatMap((rule) => rule.selectors).sort()).toEqual([".bj-band-eyebrow-glyph", ".bj-today-marker--danger"]);
    for (const rule of reds) {
      expect(Object.keys(rule.declarations).filter((property) => property !== "color" && property !== "margin-right" && property !== "font-family" && property !== "font-size")).toEqual([]);
    }
  });

  it("outlines a gold chip and never fills it", () => {
    const chip = declarationsFor(base, ".bj-today-chip");
    expect(chip.border).toBe("1px solid var(--bj-gold)");
    expect(chip.background).toBeUndefined();
    expect(chip.color).toBe("var(--bj-gold)");
  });

  it("outlines a match's chip in its fit grade's colour in place of gold, and never fills it or turns it danger red", () => {
    for (const [tone, token] of [["fit-good", "--bj-fit-good"], ["fit-decent", "--bj-fit-decent"], ["fit-bad", "--bj-fit-bad"], ["fit-unscored", "--bj-fit-unscored"]] as const) {
      expect(declarationsFor(base, `.bj-today-chip--${tone}`), tone).toEqual({ "border-color": `var(${token})`, color: `var(${token})` });
    }
  });

  it("sets the reply's own words in the serif italic, the one use of it", () => {
    const quote = declarationsFor(base, ".bj-today-quote");
    expect(quote["font-style"]).toBe("italic");
    expect(quote["font-family"]).toBe("var(--bj-font-display)");
  });
});

describe("the setup checklist", () => {
  it("is 44px per row link and per one-line form", () => {
    expect(declarationsFor(base, ".bj-setup-link")["min-height"]).toBe("44px");
    expect(declarationsFor(base, "button.bj-setup-line")["min-height"]).toBe("44px");
  });

  it("gives each state its color: gold to do, emerald done, muted otherwise", () => {
    expect(declarationsFor(base, ".bj-setup-state--todo .bj-setup-glyph").color).toBe("var(--bj-gold)");
    expect(declarationsFor(base, ".bj-setup-state--done .bj-setup-glyph").color).toBe("var(--bj-emerald)");
    expect(declarationsFor(base, ".bj-setup-state").color).toBe("var(--bj-text-muted)");
  });

  it("keeps the state beside the link on a phone, with the hint and Try again under it, all 44px", () => {
    expect(declarationsFor(phone, ".bj-setup-step")["grid-template-columns"]).toBe("9em minmax(0, 1fr)");
    expect(declarationsFor(phone, ".bj-setup-retry")["min-height"]).toBe("44px");
    expect(declarationsFor(phone, ".bj-setup-head button")["min-height"]).toBe("44px");
  });
});

describe("links in a sentence", () => {
  it("are underlined in the empty state, the Pipeline card's sentence and the background trail, so colour is not the only cue", () => {
    for (const selector of [".bj-state--empty .bj-state-body a", ".bj-pipeline-note a", ".bj-bg-line a"]) {
      expect(declarationsFor(base, selector)["text-decoration"], selector).toBe("underline");
    }
  });

  it("keep the phone's 44px target for the link a failed action leaves under its item", () => {
    const link = declarationsFor(phone, ".bj-today-problem .bj-state-missing a");
    expect(link["min-height"]).toBe("44px");
    expect(link.display).toBe("inline-flex");
  });

  it("leaves the centering of a link drawn as the band's button to the shared primitives", () => {
    expect(rulesOf(base).filter((rule) => rule.selectors.some((selector) => selector.endsWith("a.bj-band-action")))).toEqual([]);
  });
});

describe("the background trail", () => {
  it("is a 44px disclosure line under 1280px", () => {
    expect(declarationsFor(base, "button.bj-bg-toggle")["min-height"]).toBe("44px");
    expect(declarationsFor(base, "button.bj-bg-toggle").width).toBe("100%");
  });

  it("keeps its lines at the trail's size, muted", () => {
    const line = declarationsFor(base, ".bj-bg-line");
    expect(resolve(line["font-size"])).toBe(resolve("var(--bj-text-trail)"));
    expect(line.color).toBe("var(--bj-text-muted)");
  });
});

describe("what the page file imports", () => {
  it("gets the stylesheet through the components that use it", () => {
    expect(pageSource).not.toContain("today.css");
  });
});
