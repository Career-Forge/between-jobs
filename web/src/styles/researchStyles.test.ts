import { describe, expect, it } from "vitest";
import tokensCss from "../tokens.css?raw";
import { PHONE_QUERY } from "../lib/useMediaQuery";
import { blockOf, declarationsFor, rulesOf, tokenResolver, withoutMedia } from "../testing/css";
import researchCss from "./research.css?raw";

// research.css is read as text. It pins what can be measured from the rules: that it defines no color of
// its own, never fills anything cyan (the page's one cyan fill is the next-step band's), keeps evidence
// gold and never green, and the touch targets the phone needs.

const resolve = tokenResolver(tokensCss);
const base = withoutMedia(researchCss);
const decl = (selector: string) => declarationsFor(base, selector);

function luminance(hex: string): number {
  const channels = [1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r, g, b] = channels.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(a: string, b: string): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (light + 0.05) / (dark + 0.05);
}

describe("research.css", () => {
  it("was loaded as text", () => {
    expect(researchCss.length).toBeGreaterThan(3000);
  });

  it("defines no color: no hex value, no rgb or hsl, only tokens", () => {
    const code = researchCss.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(code).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(code).not.toMatch(/\b(?:rgb|rgba|hsl|hsla)\(/);
  });

  it("only uses tokens that tokens.css defines", () => {
    const defined = new Set([...tokensCss.matchAll(/(--[\w-]+)\s*:/g)].map((match) => match[1]));
    const used = new Set([...researchCss.matchAll(/var\((--[\w-]+)/g)].map((match) => match[1]));
    expect([...used].filter((name) => !defined.has(name))).toEqual([]);
  });

  it("never fills anything cyan, and uses no green or red for evidence", () => {
    for (const rule of rulesOf(researchCss)) {
      for (const property of ["background", "background-color"]) {
        expect(rule.declarations[property] ?? "").not.toContain("--bj-cyan");
        expect(rule.declarations[property] ?? "").not.toContain("--bj-emerald");
        expect(rule.declarations[property] ?? "").not.toContain("--bj-danger");
      }
    }
    const code = researchCss.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(code).not.toContain("--bj-emerald");
    expect(code).not.toContain("--bj-danger");
  });

  it("asks for a width only at the shell's own phone width", () => {
    const headers = [...researchCss.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/@media\s*([^{]+)\{/g)].map((match) => match[1].trim());
    expect(headers).toEqual([PHONE_QUERY]);
  });
});

describe("evidence", () => {
  it("draws a confidence cell the shared way, so a filled one is solid gold and an empty one an outline", () => {
    // The cells use paper-doc.css's .bj-segbar-cell classes; this file only lays them out.
    expect(decl(".bj-rs-conf-cells").display).toBe("inline-flex");
    expect(researchCss).not.toContain(".bj-segbar-cell--filled");
  });

  it("outlines a chip: solid gold is firmer, dashed gold weaker, and muted the neutral reading", () => {
    expect(decl(".bj-rs-chip--solid")["border-color"]).toBe("var(--bj-gold)");
    expect(decl(".bj-rs-chip--dashed")["border-style"]).toBe("dashed");
    expect(decl(".bj-rs-chip--muted").color).toBe("var(--bj-text-muted)");
  });

  it("draws the note that is not a failure as a gold outline", () => {
    expect(decl(".bj-rs-note")["border"]).toBe("1px solid var(--bj-gold)");
  });

  it("underlines a link inside a sentence, because its color alone is not enough", () => {
    expect(decl(".bj-rs-fact a")["text-decoration"]).toBe("underline");
  });
});

describe("a word the server sent that the page does not know", () => {
  it("wraps a chip inside its card instead of widening the page: nowrap is gone and the card's column can shrink", () => {
    const chip = decl(".bj-rs-chip");
    expect(chip["white-space"]).toBeUndefined();
    expect(chip["max-width"]).toBe("100%");
    expect(chip["overflow-wrap"]).toBe("anywhere");
    expect(decl(".bj-rs-item")["grid-template-columns"]).toBe("minmax(0, 1fr)");
  });
});

describe("the status region", () => {
  it("is hidden like screen-reader text while empty, never display:none (an announcer has to exist before its words change)", () => {
    const empty = decl(".bj-rs-live:empty");
    expect(empty.display).toBeUndefined();
    expect(empty.position).toBe("absolute");
    expect(empty["clip-path"]).toBe("inset(50%)");
  });
});

describe("practice", () => {
  it("fills a STAR part gold where the model found it and outlines it where it did not", () => {
    const on = decl(".bj-rs-star-part--on");
    expect(on.background).toBe("var(--bj-gold)");
    expect(on.color).toBe("var(--bj-bg)");
    expect(decl(".bj-rs-star-part")["border"]).toBe("1px solid var(--bj-outline)");
  });

  it("keeps the words in a filled STAR part readable: canvas text on gold", () => {
    expect(contrast(resolve("var(--bj-bg)"), resolve("var(--bj-gold)"))).toBeGreaterThan(7);
  });

  it("keeps the muted words legible on the canvas and the surface", () => {
    expect(contrast(resolve("var(--bj-text-muted)"), resolve("var(--bj-bg)"))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(resolve("var(--bj-text-muted)"), resolve("var(--bj-surface)"))).toBeGreaterThanOrEqual(4.5);
  });

  it("makes an earlier answer's row a 44px target", () => {
    expect(declarationsFor(base, "button.bj-rs-answer-row")["min-height"]).toBe("44px");
  });
});

describe("a phone", () => {
  const phone = blockOf(researchCss, `@media ${PHONE_QUERY}`) ?? "";

  it("makes a source link and a lookup link a 44px target", () => {
    const rule = declarationsFor(phone, ".bj-rs-ev a");
    expect(rule["min-height"]).toBe("44px");
    expect(rule.display).toBe("inline-flex");
  });

  it("makes the setup notice's link, a Copy or Retry link-button and the Hiring post link 44px targets", () => {
    for (const selector of [".bj-rs .bj-setup-notice a", ".bj-ws-panel .bj-hs-actions a"]) {
      expect(declarationsFor(phone, selector)["min-height"], selector).toBe("44px");
    }
    for (const selector of [".bj-rs-field .bj-link-button", ".bj-rs-fact .bj-link-button"]) {
      const rule = declarationsFor(phone, selector);
      expect(rule["min-height"], selector).toBe("44px");
      expect(rule["min-width"], selector).toBe("44px");
    }
  });

  it("lets long unbroken text from a search result wrap inside its column instead of widening the page", () => {
    for (const selector of [".bj-rs-text", ".bj-rs-source", ".bj-rs-well", ".bj-rs-sub", "h3.bj-rs-title", ".bj-rs-cite"]) {
      expect(decl(selector)["overflow-wrap"], selector).toBe("anywhere");
    }
    expect(decl(".bj-rs")["min-width"]).toBe("0");
  });
});
