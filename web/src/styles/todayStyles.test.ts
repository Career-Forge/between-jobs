import { describe, expect, it } from "vitest";
import { PHONE_QUERY } from "../lib/useMediaQuery";
import tokensCss from "../tokens.css?raw";
import { blockOf, declarationsFor, rulesOf, tokenResolver, withoutMedia } from "../testing/css";
import todayCss from "./today.css?raw";
import pageSource from "../pages/Today.tsx?raw";

// today.css is read as text (there is no layout engine in the test run). What it pins is what the page
// rests on: the margin at 1280px and up, the 44px targets on a phone, that no rule fills anything cyan,
// that it defines no color of its own, and that the held-back card's only red is a mark.

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

  it("never fills anything cyan: the band's button is the page's one cyan fill", () => {
    for (const rule of rulesOf(todayCss)) {
      for (const property of ["background", "background-color"]) {
        expect(rule.declarations[property] ?? "").not.toContain("--bj-cyan");
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

describe("the margin", () => {
  it("is 280px beside a feed of at most 880px, from 1280px up, with a 1px maroon rule on its left", () => {
    expect(declarationsFor(wide, ".bj-today-grid")["grid-template-columns"]).toBe("minmax(0, 880px) 280px");
    const margin = declarationsFor(wide, ".bj-today-margin");
    expect(margin["border-left"]).toBe("1px solid var(--bj-maroon)");
    expect(todayCss).toMatch(/@media \(min-width: 1280px\)/);
  });

  it("is not a rule at any narrower width: the grid exists only inside the 1280px block", () => {
    expect(declarationsFor(base, ".bj-today-grid")).toEqual({});
    expect(declarationsFor(base, ".bj-today-margin")).toEqual({});
    expect(declarationsFor(phone, ".bj-today-grid")).toEqual({});
  });

  it("lifts the shell's 960px cap on the page column where the margin needs the room", () => {
    const main = declarationsFor(wide, ".bj-main:has(.bj-today)");
    expect(main["max-width"]).toBe("none");
    expect(main.padding).toBe("40px 32px");
  });

  it("caps the feed at the list measure, and lets it shrink inside the grid", () => {
    const feed = declarationsFor(base, ".bj-today-main");
    expect(feed["max-width"]).toBe("880px");
    expect(feed["min-width"]).toBe("0");
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
  it("are underlined in the empty state and the background trail, so colour is not the only cue", () => {
    for (const selector of [".bj-state--empty .bj-state-body a", ".bj-bg-line a"]) {
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
