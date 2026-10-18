import { describe, expect, it } from "vitest";
import tokensCss from "../tokens.css?raw";
import { blockOf, declarationsFor, rulesOf, tokenResolver, withoutMedia } from "../testing/css";
import { PHONE_QUERY } from "../lib/useMediaQuery";
import paperCss from "./paper.css?raw";

// paper.css is read as text (there is no layout engine in the test run). What it pins is the
// stylesheet's measurable rules: sizes, the band's shape, motion, and that it defines no color of its own.

const base = withoutMedia(paperCss);
const phone = blockOf(paperCss, "@media (max-width: 959px)") ?? "";
const reduced = blockOf(paperCss, "@media (prefers-reduced-motion: reduce)") ?? "";
const resolve = tokenResolver(tokensCss);

describe("paper.css", () => {
  it("was loaded as text and holds every block the components use", () => {
    expect(paperCss.length).toBeGreaterThan(1000);
    expect(phone).not.toBe("");
    expect(reduced).not.toBe("");
  });

  it("stacks at the same width the components switch to a sheet at", () => {
    // TrailLine decides sheet or inline from PHONE_QUERY while this file decides the stacked layout;
    // the two have to name one breakpoint or the band and a receipt disagree at that width.
    expect(blockOf(paperCss, `@media ${PHONE_QUERY}`)).toBe(phone);
    expect(phone).not.toBe("");
    expect(PHONE_QUERY).toBe("(max-width: 959px)");
  });

  it("defines no color: no hex value, no rgb or hsl, only tokens", () => {
    const code = paperCss.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(code).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(code).not.toMatch(/\b(?:rgb|rgba|hsl|hsla)\(/);
  });

  it("only uses tokens that tokens.css defines", () => {
    const defined = new Set([...tokensCss.matchAll(/(--[\w-]+)\s*:/g)].map((match) => match[1]));
    const used = new Set([...paperCss.matchAll(/var\((--[\w-]+)/g)].map((match) => match[1]));
    expect([...used].filter((name) => !defined.has(name))).toEqual([]);
  });

  it("never fills anything cyan: the band's fill comes from .bj-primary, the one primary class", () => {
    for (const rule of rulesOf(paperCss)) {
      for (const property of ["background", "background-color"]) {
        expect(rule.declarations[property] ?? "").not.toContain("--bj-cyan");
      }
    }
  });
});

describe("the trail line", () => {
  it("is 12px muted body text, never italic, with mono dates at 11px", () => {
    const line = declarationsFor(base, ".bj-trail");
    expect(resolve(line["font-size"])).toBe("12px");
    expect(line["font-style"]).toBe("normal");
    expect(line["font-family"]).toBe("var(--bj-font-body)");
    expect(resolve(line.color)).toBe(resolve("var(--bj-text-muted)"));
    expect(resolve(declarationsFor(base, ".bj-trail-mono")["font-size"])).toBe("11px");
    expect(declarationsFor(base, ".bj-trail-mono")["font-family"]).toBe("var(--bj-font-mono)");
  });

  it("puts the words inside a 44px hit area", () => {
    expect(declarationsFor(base, "button.bj-trail-trigger")["min-height"]).toBe("44px");
  });

  it("gives each glyph its token color", () => {
    const colors = {
      violet: "--bj-violet",
      muted: "--bj-text-muted",
      ivory: "--bj-text",
      gold: "--bj-gold",
      danger: "--bj-danger",
      emerald: "--bj-emerald",
    };
    for (const [tone, token] of Object.entries(colors)) {
      expect(declarationsFor(base, `.bj-trail-glyph--${tone}`).color).toBe(`var(${token})`);
    }
  });

  it("holds the receipt in a bounded well on the canvas color, in mono", () => {
    const well = declarationsFor(base, ".bj-receipt");
    expect(well.background).toBe("var(--bj-bg)");
    expect(resolve(well.background)).toBe("#070607");
    expect(well["max-height"]).toBe("240px");
    expect(well.overflow).toBe("auto");
    expect(well["font-family"]).toBe("var(--bj-font-mono)");
  });
});

describe("the bottom sheet", () => {
  it("sits above the phone tab bar and pins to the bottom edge", () => {
    expect(Number(declarationsFor(base, ".bj-sheet-root")["z-index"])).toBeGreaterThan(40);
    const sheet = declarationsFor(base, ".bj-sheet");
    expect(sheet.bottom).toBe("0");
    expect(sheet["border-radius"]).toBe("16px 16px 0 0");
  });

  it("has a 44px Close button", () => {
    const close = declarationsFor(base, "button.bj-sheet-close");
    expect([close.width, close.height]).toEqual(["44px", "44px"]);
  });
});

describe("the next-step band", () => {
  it("is a row between two 1px rules with no fill and no shadow", () => {
    const band = declarationsFor(base, ".bj-band");
    expect(band["border-top"]).toBe("1px solid var(--bj-border)");
    expect(band["border-bottom"]).toBe("1px solid var(--bj-border)");
    expect(band.background).toBeUndefined();
    expect(band["box-shadow"]).toBeUndefined();
    expect(band["border-radius"]).toBeUndefined();
    expect(declarationsFor(base, ".bj-band-body").display).toBe("flex");
  });

  it("sets the eyebrow in 11px mono capitals, the sentence in the display serif and the why line in 12px mono", () => {
    const eyebrow = declarationsFor(base, ".bj-band-eyebrow");
    expect(resolve(eyebrow["font-size"])).toBe("11px");
    expect(eyebrow["font-family"]).toBe("var(--bj-font-mono)");
    expect(eyebrow["text-transform"]).toBe("uppercase");
    expect(eyebrow["letter-spacing"]).toBe("0.14em");
    const sentence = declarationsFor(base, ".bj-band-sentence");
    expect(sentence["font-family"]).toBe("var(--bj-font-display)");
    expect(sentence["line-height"]).toBe("1.25");
    expect(declarationsFor(base, ".bj-band-why")["font-family"]).toBe("var(--bj-font-mono)");
    expect(declarationsFor(base, ".bj-band-why")["font-size"]).toBe("12px");
  });

  it("makes the button 46px tall on a desktop and a full-width 48px on a phone, with the band stacked", () => {
    expect(declarationsFor(base, "button.bj-band-action")["min-height"]).toBe("46px");
    const stacked = declarationsFor(phone, ".bj-band-body");
    expect(stacked["flex-direction"]).toBe("column");
    const action = declarationsFor(phone, "button.bj-band-action");
    expect([action.width, action["min-height"]]).toEqual(["100%", "48px"]);
  });

  it("draws the locked button as a dashed cyan outline, not a fill", () => {
    const locked = declarationsFor(base, "button.bj-band-locked");
    expect(locked.border).toBe("1px dashed var(--bj-cyan)");
    expect(locked.background).toBe("transparent");
  });

  it("keeps one minimum height for loading, unlit and lit, so a step arriving does not move the page", () => {
    expect(declarationsFor(base, ".bj-band")["min-height"]).toBe("112px");
  });
});

describe("motion", () => {
  it("fades in over 200ms, and not at all for someone who asked for less motion", () => {
    for (const selector of [".bj-band-body--fade-a", ".bj-trail-receipt", ".bj-sheet", ".bj-sheet-scrim"]) {
      expect(declarationsFor(base, selector).animation).toBe("bj-paper-fade 200ms ease-out");
      expect(declarationsFor(reduced, selector).animation).toBe("none");
    }
  });

  it("restarts the band's fade without a remount: two classes, two names for one set of keyframes", () => {
    // A class swap alone would not restart an animation that keeps its name, and a remount would drop
    // keyboard focus, so the band alternates between these two.
    expect(declarationsFor(base, ".bj-band-body--fade-b").animation).toBe("bj-paper-fade-again 200ms ease-out");
    expect(declarationsFor(reduced, ".bj-band-body--fade-b").animation).toBe("none");
    expect(blockOf(paperCss, "@keyframes bj-paper-fade-again")?.replace(/\s+/g, "")).toBe(
      blockOf(paperCss, "@keyframes bj-paper-fade")?.replace(/\s+/g, ""),
    );
  });
});

describe("the page header and state views", () => {
  it("sets the title in the display serif at the h1 token", () => {
    const title = declarationsFor(base, "h1.bj-page-title");
    expect(title["font-family"]).toBe("var(--bj-font-display)");
    expect(resolve(title["font-size"])).toBe("34px");
  });

  it("draws placeholders on the raised surface with no shimmer", () => {
    expect(declarationsFor(base, ".bj-skeleton-row").background).toBe("var(--bj-surface-raised)");
    expect(paperCss.replace(/\/\*[\s\S]*?\*\//g, "")).not.toMatch(/shimmer|linear-gradient/i);
    expect(declarationsFor(base, ".bj-skeleton-row").animation).toBeUndefined();
  });

  it("outlines a failure in the danger color and a partial or missing thing in the neutral outline", () => {
    expect(declarationsFor(base, ".bj-state--failure")["border-color"]).toBe("var(--bj-danger)");
    expect(declarationsFor(base, ".bj-state--partial").border).toBe("1px solid var(--bj-outline)");
  });
});
