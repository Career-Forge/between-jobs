import { describe, expect, it } from "vitest";
import appCss from "../app.css?raw";
import { PHONE_QUERY } from "../lib/useMediaQuery";
import tokensCss from "../tokens.css?raw";
import { blockOf, declarationsFor, rulesOf, withoutMedia } from "../testing/css";
import applicationsCss from "./applications.css?raw";

// applications.css is read as text (there is no layout engine in the test run). What it pins is what
// can be measured in it: no color of its own, no cyan fill, the board scrolling inside itself, the
// list's measure, the 44px targets on a phone, Check posting's button and its answer, and that the old
// board's rules are gone from app.css.

const code = applicationsCss.replace(/\/\*[\s\S]*?\*\//g, "");
const base = withoutMedia(applicationsCss);
const phone = blockOf(applicationsCss, "@media (max-width: 959px)") ?? "";
const mid = blockOf(applicationsCss, "@media (min-width: 960px)") ?? "";
const wide = blockOf(applicationsCss, "@media (min-width: 1280px)") ?? "";

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

  it("never fills anything cyan: the page's one cyan fill is the next-step band's button", () => {
    for (const rule of rulesOf(applicationsCss)) {
      for (const property of ["background", "background-color"]) {
        expect(rule.declarations[property] ?? "").not.toContain("--bj-cyan");
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
  it("keeps the list to the measure for lists, 880px", () => {
    expect(declarationsFor(base, ".bj-app-list")["max-width"]).toBe("880px");
    expect(declarationsFor(base, ".bj-app-track")["max-width"]).toBe("880px");
  });

  it("lifts the shell's 960px cap from 1280px, so the board can use the content column", () => {
    expect(declarationsFor(wide, ".bj-main:has(.bj-apps)")["max-width"]).toBe("none");
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
    const selectors = [".bj-app-select", ".bj-app-check", ".bj-app-track-card button", ".bj-app-track-card .bj-field input", ".bj-app-track-link", ".bj-app-link", ".bj-app-track-card .bj-state-missing a", ".bj-app-problem button"];
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

describe("Check posting", () => {
  const answer = declarationsFor(base, ".bj-app-check-result");

  it("draws the button as an outlined one the height of the selects beside it, smaller on a board card", () => {
    expect(declarationsFor(base, ".bj-app-check")["min-height"]).toBe("36px");
    expect(declarationsFor(base, ".bj-app-select")["min-height"]).toBe("36px");
    expect(declarationsFor(base, ".bj-app-card--board .bj-app-check")["font-size"]).toBe("var(--bj-text-small)");
    // The outline, the fill and the colour are the shared button's: this sheet adds none of them.
    const own = declarationsFor(base, ".bj-app-check");
    for (const property of ["background", "background-color", "border", "border-color", "color"]) expect(own[property], property).toBeUndefined();
  });

  it("dims a button that is waiting on its request the way the selects are, and shows progress", () => {
    const waiting = declarationsFor(base, '.bj-app-check[aria-disabled="true"]');
    expect(waiting.opacity).toBe("0.65");
    expect(waiting.cursor).toBe("progress");
    expect(declarationsFor(base, ".bj-app-select[aria-disabled=\"true\"]").cursor).toBe(waiting.cursor);
  });

  it("draws the answer in the page's text colour at the small size, with no colour that says good or bad", () => {
    expect(answer.color).toBe("var(--bj-text)");
    expect(answer["font-size"]).toBe("var(--bj-text-small)");
    expect(answer["overflow-wrap"]).toBe("anywhere");
    expect(answer["min-width"]).toBe("0");
    expect(code).not.toMatch(/\.bj-app-check[^{]*\{[^}]*(?:--bj-danger|--bj-emerald|--bj-cyan|--bj-gold)/);
    expect(code).not.toMatch(/\.bj-app-check-result\[data-state/);
  });

  it("takes no room while it is empty (not even the row's column gap) and a row of its own once it has words", () => {
    expect(answer.margin).toBe("0");
    expect(declarationsFor(base, ".bj-app-check-result:not(:empty)").flex).toBe("1 1 100%");
    expect(answer.flex).toBeUndefined();
    // The column gap of .bj-app-controls, taken back, so a growing select is the same width with or without an answer.
    expect(declarationsFor(base, ".bj-app-controls").gap).toBe("var(--bj-space-2) var(--bj-space-3)");
    expect(declarationsFor(base, ".bj-app-check-result:empty")["margin-left"]).toBe("calc(var(--bj-space-3) * -1)");
  });

  it("draws when it was checked as the answer is drawn, on a line of its own after the answer", () => {
    const when = declarationsFor(base, ".bj-app-check-when");
    for (const property of ["color", "font-size", "overflow-wrap", "min-width", "margin"]) expect(when[property], property).toBe(answer[property]);
    expect(when.flex).toBe("1 1 100%");
  });

  it("keeps a board select wide enough to read, so the button beside the Actions menu wraps instead of squeezing it", () => {
    const select = declarationsFor(base, ".bj-app-card--board .bj-app-select");
    expect(select["min-width"]).toBe("120px");
    expect(select.flex).toBe("1 1 0");
  });

  it("never takes the answer out of the page's accessibility tree: it is a live region, so no rule hides it when it is empty", () => {
    for (const rule of rulesOf(applicationsCss)) {
      if (!rule.selectors.some((selector) => selector.includes(".bj-app-check-result"))) continue;
      expect(rule.declarations.display, rule.selectors.join()).toBeUndefined();
      expect(rule.declarations.visibility, rule.selectors.join()).toBeUndefined();
      expect(rule.declarations.height, rule.selectors.join()).toBeUndefined();
      expect(rule.declarations.position, rule.selectors.join()).toBeUndefined();
    }
  });
});

describe("app.css", () => {
  it("no longer carries the old list and board rules this stylesheet replaces", () => {
    expect(appCss).not.toMatch(/\.bj-kanban/);
    expect(appCss).not.toMatch(/\.bj-application-(?:list|row|title)/);
  });
});
