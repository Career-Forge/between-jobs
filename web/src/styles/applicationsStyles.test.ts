import { describe, expect, it } from "vitest";
import appCss from "../app.css?raw";
import { PHONE_QUERY } from "../lib/useMediaQuery";
import tokensCss from "../tokens.css?raw";
import { blockOf, declarationsFor, horizontalPaddingPx, rulesOf, withoutMedia } from "../testing/css";
import applicationsCss from "./applications.css?raw";

// applications.css is read as text (there is no layout engine in the test run). What it pins is what
// can be measured in it: no color of its own, no gold fill, the board scrolling inside itself, the
// list's measure, the 44px targets on a phone, and that the old board's rules are gone from app.css.

const code = applicationsCss.replace(/\/\*[\s\S]*?\*\//g, "");
const base = withoutMedia(applicationsCss);
const phone = blockOf(applicationsCss, "@media (max-width: 959px)") ?? "";
const mid = blockOf(applicationsCss, "@media (min-width: 960px)") ?? "";
const wide = blockOf(applicationsCss, "@media (min-width: 1280px)") ?? "";

// Every block under one query, joined: a rule that has to come after the one it overrides sits in a
// block of its own further down the file.
function blocksOf(header: string): string {
  const parts: string[] = [];
  let rest = applicationsCss;
  for (;;) {
    const block = blockOf(rest, header);
    if (block === null) return parts.join("\n");
    parts.push(block);
    rest = rest.slice(rest.indexOf(block) + block.length);
  }
}
const everyWide = blocksOf("@media (min-width: 1280px)");

describe("applications.css", () => {
  it("was loaded as text and holds its three width blocks", () => {
    expect(applicationsCss.length).toBeGreaterThan(2000);
    expect([phone, mid, wide].every((block) => block !== "")).toBe(true);
  });

  it("defines no color: no hex value, no rgb or hsl, only tokens", () => {
    expect(code).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(code).not.toMatch(/\b(?:rgb|rgba|hsl|hsla)\(/);
  });

  it("only uses tokens that tokens.css defines", () => {
    const defined = new Set([...tokensCss.matchAll(/(--[\w-]+)\s*:/g)].map((match) => match[1]));
    const used = new Set([...applicationsCss.matchAll(/var\((--[\w-]+)/g)].map((match) => match[1]));
    expect([...used].filter((name) => !defined.has(name))).toEqual([]);
  });

  it("never fills anything gold: the page's one gold fill is the next-step band's button", () => {
    for (const rule of rulesOf(applicationsCss)) {
      for (const property of ["background", "background-color"]) {
        expect(rule.declarations[property] ?? "").not.toContain("--bj-primary");
      }
    }
  });

  it("uses red nowhere: a rejection is not a failure, and the failed-move callout is the shared state view's", () => {
    expect(code).not.toContain("--bj-danger");
  });

  it("switches its phone rules at the shell's breakpoint, and uses only the three widths", () => {
    expect(blockOf(applicationsCss, `@media ${PHONE_QUERY}`)).toBe(phone);
    const queries = [...applicationsCss.matchAll(/@media ([^{]+)\{/g)].map((match) => match[1].trim());
    expect([...new Set(queries)].sort()).toEqual(["(max-width: 959px)", "(min-width: 1280px)", "(min-width: 960px)"]);
  });
});

describe("the list and the board", () => {
  it("keeps the list to the measure for lists, 880px, under 1280px, as one column", () => {
    const list = declarationsFor(base, ".bj-app-list");
    expect(list["max-width"]).toBe("880px");
    expect(list.display).toBe("flex");
    expect(list["flex-direction"]).toBe("column");
    expect(declarationsFor(base, ".bj-app-track")["max-width"]).toBe("880px");
    for (const block of [phone, mid]) expect(declarationsFor(block, ".bj-app-list")).toEqual({});
  });

  it("sets the list's cards two to a row from 1280px, as wide together as the shell's column less the page's padding", () => {
    const list = declarationsFor(everyWide, ".bj-app-list");
    expect(list).toEqual({ display: "grid", "grid-template-columns": "repeat(2, minmax(0, 1fr))", "max-width": "1376px" });
    // 1440px, the shell's cap (app.css), less what this page pads each side with.
    const [left, right] = horizontalPaddingPx(declarationsFor(wide, ".bj-main:has(.bj-apps)"));
    const shellCap = Number.parseFloat(declarationsFor(blockOf(appCss, "@media (min-width: 1280px)") ?? "", ".bj-main")["max-width"]);
    expect(shellCap - (left ?? Number.NaN) - (right ?? Number.NaN)).toBe(1376);
    // The list has two rules, and the two-column one comes after the one-column one, so it is the one
    // that applies from 1280px (a media query adds no weight of its own).
    expect(code.match(/\.bj-app-list \{/g)).toHaveLength(2);
    expect(code.indexOf("max-width: 1376px")).toBeGreaterThan(code.indexOf(".bj-app-list {"));
    expect(code.indexOf("max-width: 1376px")).toBeGreaterThan(code.lastIndexOf(".bj-app-list {"));
  });

  it("keeps the tracking forms and the callouts at 880px at every width: they are forms and sentences", () => {
    expect(declarationsFor(base, ".bj-app-track")["max-width"]).toBe("880px");
    expect(declarationsFor(base, ".bj-app-section > .bj-state")["max-width"]).toBe("880px");
    expect(declarationsFor(everyWide, ".bj-app-track")).toEqual({});
    expect(declarationsFor(everyWide, ".bj-app-section > .bj-state")).toEqual({});
  });

  it("lifts the shell's cap from 1280px, so the board can use the content column however wide the window", () => {
    expect(declarationsFor(wide, ".bj-main:has(.bj-apps)")["max-width"]).toBe("none");
  });

  it("keeps Practice, a page of sentences, to the list measure from 1280px, where the shell's column grows", () => {
    expect(declarationsFor(everyWide, ".bj-practice")).toEqual({ "max-width": "880px" });
    // Under 1280px the shell's own cap is narrower than that, so the page states none there.
    expect(declarationsFor(base, ".bj-practice")["max-width"]).toBeUndefined();
  });

  it("lays the seven columns in one row that scrolls inside itself, never the page", () => {
    const board = declarationsFor(base, ".bj-app-board");
    expect(board["grid-auto-flow"]).toBe("column");
    expect(board["overflow-x"]).toBe("auto");
    expect(board["max-width"]).toBe("100%");
    expect(declarationsFor(base, ".bj-apps")["min-width"]).toBe("0");
    // Flex and grid children holding the user's own text can shrink, and their words wrap anywhere.
    expect(declarationsFor(base, ".bj-app-card")["min-width"]).toBe("0");
    expect(declarationsFor(base, ".bj-app-identity")["min-width"]).toBe("0");
    expect(declarationsFor(base, ".bj-app-title")["overflow-wrap"]).toBe("anywhere");
    expect(declarationsFor(base, ".bj-app-company")["overflow-wrap"]).toBe("anywhere");
  });

  it("marks the column a card is over with an outline and a surface step, never a color", () => {
    const over = declarationsFor(base, ".bj-app-column--dragover");
    expect(over["border-color"]).toBe("var(--bj-outline)");
    expect(over["border-style"]).toBe("dashed");
    expect(over.background).toBe("var(--bj-surface-raised)");
  });

  it("draws every status as a neutral outline chip, with emerald only on the Offer glyph and muted for the two at rest", () => {
    expect(declarationsFor(base, ".bj-app-chip").border).toBe("1px solid var(--bj-outline)");
    expect(declarationsFor(base, ".bj-app-chip--emerald .bj-app-chip-glyph").color).toBe("var(--bj-emerald)");
    expect(declarationsFor(base, ".bj-app-chip--muted").color).toBe("var(--bj-text-muted)");
    expect(declarationsFor(base, ".bj-app-chip--emerald").color).toBeUndefined();
  });

  it("draws the resume badge as a 1px outline with a violet star and ivory words", () => {
    const badge = declarationsFor(base, ".bj-app-badge");
    expect(badge.border).toBe("1px solid var(--bj-border)");
    expect(badge["border-radius"]).toBe("5px");
    expect(badge.color).toBe("var(--bj-text)");
    expect(declarationsFor(base, ".bj-app-badge-glyph").color).toBe("var(--bj-violet)");
  });

  it("draws the tracking forms' fields with the outline token, not the card's hairline (a 3:1 boundary)", () => {
    for (const selector of [".bj-app-track-card .bj-field input", ".bj-app-track-card .bj-field textarea"]) {
      expect(declarationsFor(base, selector)["border-color"], selector).toBe("var(--bj-outline)");
    }
  });

  it("puts the tracking forms side by side from 960px and stacked below it", () => {
    expect(declarationsFor(base, ".bj-app-track-grid")["grid-template-columns"]).toBe("minmax(0, 1fr)");
    expect(declarationsFor(mid, ".bj-app-track-grid")["grid-template-columns"]).toBe("repeat(2, minmax(0, 1fr))");
  });
});

describe("a phone", () => {
  it("makes every control at least 44px tall", () => {
    const selectors = [".bj-app-select", ".bj-app-track-card button", ".bj-app-track-card .bj-field input", ".bj-app-track-link", ".bj-app-link", ".bj-app-track-card .bj-state-missing a", ".bj-app-problem button"];
    for (const selector of selectors) {
      expect(declarationsFor(phone, selector)["min-height"], selector).toBe("44px");
    }
    expect(declarationsFor(base, ".bj-app-picker-select")["min-height"]).toBe("44px");
  });

  it("gives the card's short 'Open' link a 44px width as well as height", () => {
    expect(declarationsFor(phone, ".bj-app-link")["min-width"]).toBe("44px");
  });

  it("lets the selects in a card share its width instead of overflowing it", () => {
    expect(declarationsFor(phone, ".bj-app-controls .bj-app-select")["flex"]).toBe("1 1 160px");
    expect(declarationsFor(base, ".bj-app-select")["max-width"]).toBe("100%");
    expect(declarationsFor(base, ".bj-app-select")["min-width"]).toBe("0");
  });
});

describe("app.css", () => {
  it("no longer carries the old list and board rules this stylesheet replaces", () => {
    expect(appCss).not.toMatch(/\.bj-kanban/);
    expect(appCss).not.toMatch(/\.bj-application-(?:list|row|title)/);
  });
});
