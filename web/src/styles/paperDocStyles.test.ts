import { describe, expect, it } from "vitest";
import tokensCss from "../tokens.css?raw";
import { PHONE_QUERY } from "../lib/useMediaQuery";
import { blockOf, declarationsFor, rulesOf, tokenResolver, withoutMedia } from "../testing/css";
import docCss from "./paper-doc.css?raw";

// paper-doc.css is read as text (the test run has no layout engine). It pins what can be measured from
// the rules: the margin, the measures, the sizes the spec gives, that it defines no color of its own and
// that it never fills anything cyan.

const resolve = tokenResolver(tokensCss);
const base = withoutMedia(docCss);
const phone = blockOf(docCss, "@media (max-width: 959px)") ?? "";
const reduced = blockOf(docCss, "@media (prefers-reduced-motion: reduce)") ?? "";
const decl = (selector: string) => declarationsFor(base, selector);

describe("paper-doc.css", () => {
  it("was loaded as text and holds the blocks the components use", () => {
    expect(docCss.length).toBeGreaterThan(3000);
    expect(phone).not.toBe("");
    expect(reduced).not.toBe("");
  });

  it("defines no color: no hex value, no rgb or hsl, only tokens", () => {
    const code = docCss.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(code).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(code).not.toMatch(/\b(?:rgb|rgba|hsl|hsla)\(/);
  });

  it("only uses tokens that tokens.css defines, or that it defines itself", () => {
    const defined = new Set([...tokensCss.matchAll(/(--[\w-]+)\s*:/g), ...docCss.matchAll(/(--bj-doc-[\w-]+)\s*:/g)].map((match) => match[1]));
    const used = new Set([...docCss.matchAll(/var\((--[\w-]+)/g)].map((match) => match[1]));
    expect([...used].filter((name) => !defined.has(name))).toEqual([]);
  });

  it("never fills anything cyan: the page's one cyan fill is the band's button", () => {
    for (const rule of rulesOf(docCss)) {
      for (const property of ["background", "background-color"]) {
        expect(rule.declarations[property] ?? "").not.toContain("--bj-cyan");
      }
    }
  });

  it("asks for a width only where it is the shell's own phone width: the margin and the sheet are chosen in script", () => {
    const headers = [...docCss.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/@media\s*([^{]+)\{/g)].map((match) => match[1].trim());
    expect(headers.sort()).toEqual(["(max-width: 959px)", "(prefers-reduced-motion: reduce)"]);
    expect(PHONE_QUERY).toBe("(max-width: 959px)");
    expect(blockOf(docCss, `@media ${PHONE_QUERY}`)).toBe(phone);
  });
});

describe("the document layout and its trail margin", () => {
  it("has a 280px margin, a 32px gap, and the spec's measures: 880px for rows and cards, 680px for prose", () => {
    const layout = decl(".bj-doc");
    expect(layout["--bj-doc-margin"]).toBe("280px");
    expect(layout["--bj-doc-gap"]).toBe("32px");
    expect(layout["--bj-doc-measure"]).toBe("880px");
    expect(decl(".bj-doc--prose")["--bj-doc-measure"]).toBe("680px");
    expect(decl(".bj-doc--full")["max-width"]).toBe("none");
  });

  it("makes the margin cell a second column beside every block, with the same tracks in each", () => {
    const block = decl(".bj-doc--margin .bj-doc-block");
    expect(block.display).toBe("grid");
    expect(block["grid-template-columns"]).toBe("minmax(0, 1fr) var(--bj-doc-margin)");
    expect(block["column-gap"]).toBe("var(--bj-doc-gap)");
  });

  it("draws the margin's rule as one 1px maroon hairline, 280px from the right edge, and never as an indicator", () => {
    const rule = decl(".bj-doc--margin::before");
    expect(rule.width).toBe("1px");
    expect(rule.background).toBe("var(--bj-maroon)");
    expect(rule.left).toBe("calc(100% - var(--bj-doc-margin))");
    expect(rule["pointer-events"]).toBe("none");
  });

  it("spaces blocks with padding in the block's own column, so the margin cell is as tall as the row", () => {
    expect(decl(".bj-doc-main")["padding-bottom"]).toBe("var(--bj-space-5)");
    expect(decl(".bj-doc-main")["margin-bottom"]).toBeUndefined();
    expect(decl(".bj-doc-block")["margin-bottom"]).toBeUndefined();
  });

  it("keeps anything that is not a block inside the document column, so a heading never runs under the rule", () => {
    expect(decl(".bj-doc--margin > :not(.bj-doc-block):not(:has(.bj-doc-block))")["max-width"]).toBe(
      "calc(100% - var(--bj-doc-margin) - var(--bj-doc-gap))",
    );
  });

  it("does not cap a wrapper that holds blocks, so its blocks' margin cells stay under the layout's rule", () => {
    // A <section> grouping a chapter with its blocks must stay full width (the rule is drawn from the
    // layout, not the wrapper); the chapter inside it is capped on its own.
    const capped = rulesOf(docCss).find((rule) => rule.selectors.includes(".bj-doc--margin > :not(.bj-doc-block):not(:has(.bj-doc-block))"));
    expect(capped?.selectors).toContain(".bj-doc--margin .bj-chapter");
    expect(capped?.declarations["max-width"]).toBe("calc(100% - var(--bj-doc-margin) - var(--bj-doc-gap))");
    expect(rulesOf(docCss).some((rule) => rule.selectors.includes(".bj-doc--margin > :not(.bj-doc-block)"))).toBe(false);
  });

  it("lifts the shell's 960px cap only where the margin is drawn", () => {
    const lift = decl(".bj-main:has(.bj-doc--margin)");
    expect(lift["max-width"]).toBe("none");
    expect(lift.padding).toBe("40px 32px");
    expect(docCss).not.toMatch(/\.bj-main:has\(\.bj-doc\)/);
  });

  it("lets a receipt in the margin be as wide as the margin", () => {
    expect(decl(".bj-doc-margin .bj-trail-receipt")["max-width"]).toBe("none");
  });
});

describe("the jump bar and chapter", () => {
  it("has group labels in 10.5px uppercase mono, muted", () => {
    const label = decl(".bj-jump-label");
    expect(label["font-size"]).toBe("10.5px");
    expect(label["font-family"]).toBe("var(--bj-font-mono)");
    expect(label["text-transform"]).toBe("uppercase");
    expect(resolve(label.color)).toBe(resolve("var(--bj-text-muted)"));
  });

  it("has links 36px tall with 10px side padding and 13px sans, and keeps a group on one line", () => {
    const link = decl("a.bj-jump-link");
    expect(link["min-height"]).toBe("36px");
    expect(link.padding).toBe("0 10px");
    expect(resolve(link["font-size"])).toBe("13px");
    expect(link["font-family"]).toBe("var(--bj-font-body)");
    expect(decl(".bj-jump-segments")["flex-wrap"]).toBe("nowrap");
  });

  it("reserves the chosen link's heavier width, so choosing one does not shift its neighbours", () => {
    const hold = decl("a.bj-jump-link::after");
    expect(hold.content).toBe("attr(data-label)");
    expect(hold["font-weight"]).toBe("600");
    expect(hold.height).toBe("0");
    expect(hold.visibility).toBe("hidden");
  });

  it("sticks the bar to the top at 1280px and up, and lets the wrapped bar scroll away", () => {
    expect(decl(".bj-jump--sticky").position).toBe("sticky");
    expect(decl(".bj-jump--sticky").top).toBe("0");
    expect(decl(".bj-jump--bar").position).toBeUndefined();
    expect(decl(".bj-jump--bar")["flex-wrap"]).toBe("wrap");
  });

  it("docks a 44px row that takes no room in the page, so its arrival moves nothing", () => {
    const dock = decl(".bj-jump--dock");
    expect(dock.position).toBe("sticky");
    expect(dock.height).toBe("44px");
    expect(dock["margin-bottom"]).toBe("-44px");
    expect(reduced).toContain(".bj-jump--dock");
    expect(declarationsFor(reduced, ".bj-jump--dock").animation).toBe("none");
  });

  it("is a 50px row outlined with the group outline holding a 44px select, below the 56px top bar", () => {
    const row = decl(".bj-jump-select--row");
    expect(row["min-height"]).toBe("50px");
    expect(row.border).toBe("1px solid var(--bj-outline)");
    expect(decl("select.bj-jump-select-control")["min-height"]).toBe("44px");
    expect(decl(".bj-jump--select").top).toBe("calc(56px + env(safe-area-inset-top))");
    expect(decl(".bj-jump--select").position).toBe("sticky");
  });

  it("leaves hash targets clear of whichever bar is showing, by scroll padding on the page", () => {
    expect(decl(":root:has(.bj-jump--sticky)")["scroll-padding-top"]).toBe("76px");
    expect(decl(":root:has(.bj-jump--bar:not(.bj-jump--sticky))")["scroll-padding-top"]).toBe("52px");
    expect(decl(":root:has(.bj-jump--select)")["scroll-padding-top"]).toContain("56px");
  });

  it("sets the chapter heading in the serif at 500, never numbered, with the eyebrow in mono", () => {
    const title = decl(".bj-chapter-title");
    expect(title["font-family"]).toBe("var(--bj-font-display)");
    expect(title["font-weight"]).toBe("500");
    expect(decl(".bj-chapter-eyebrow")["font-family"]).toBe("var(--bj-font-mono)");
    expect(docCss).not.toMatch(/counter-(?:increment|reset)|\bcounter\(/);
  });
});

describe("the score patterns", () => {
  it("draws a segment as a 7 by 9px cell with 1.5px corners and 2px gaps, hollow unless filled", () => {
    const cell = decl(".bj-segbar-cell");
    expect(cell.width).toBe("7px");
    expect(cell.height).toBe("9px");
    expect(cell["border-radius"]).toBe("1.5px");
    expect(cell.border).toBe("1px solid var(--bj-outline)");
    expect(decl(".bj-segbar-cells").gap).toBe("2px");
    expect(cell.background).toBeUndefined();
  });

  it("fills with gold and hatches headroom with a gold pattern, never a solid fill", () => {
    expect(decl(".bj-segbar-cell--filled").background).toBe("var(--bj-gold)");
    const hatched = decl(".bj-segbar-cell--hatched");
    expect(hatched.background).toContain("repeating-linear-gradient");
    expect(hatched.background).toContain("var(--bj-gold)");
    expect(hatched.background).toContain("transparent");
  });

  it("keeps a filled cell filled under forced colors, where the author's colors are replaced", () => {
    expect(decl(".bj-segbar-cell--filled")["forced-color-adjust"]).toBe("none");
    expect(decl(".bj-segbar-cell--hatched")["forced-color-adjust"]).toBe("none");
    expect(decl(".bj-ladder-cell--filled")["forced-color-adjust"]).toBe("none");
  });

  it("sets the score numeral in the gold serif at 46px with lining, tabular figures", () => {
    const numeral = decl(".bj-scoreblock-numeral");
    expect(numeral["font-size"]).toBe("46px");
    expect(numeral.color).toBe("var(--bj-gold)");
    expect(numeral["font-family"]).toBe("var(--bj-font-display)");
    expect(numeral["font-variant-numeric"]).toBe("lining-nums tabular-nums");
  });

  it("draws the fit ladder in four gold cells", () => {
    expect(decl(".bj-ladder-cell--filled").background).toBe("var(--bj-gold)");
    expect(decl(".bj-ladder-cell").border).toBe("1px solid var(--bj-outline)");
  });

  it("draws no gauge, ring or radar: no conic or radial gradient, no clip-path arc", () => {
    expect(docCss).not.toMatch(/conic-gradient|radial-gradient|clip-path|stroke-dash/);
  });
});

describe("the claim and checklist rows", () => {
  it("sets a claim in the italic serif, and only a claim", () => {
    const claim = decl(".bj-claimrow-claim");
    expect(claim["font-family"]).toBe("var(--bj-font-display)");
    expect(claim["font-style"]).toBe("italic");
    expect(claim["font-size"]).toBe("16.5px");
    const italic = rulesOf(docCss).filter((rule) => rule.declarations["font-style"] === "italic");
    expect(italic.map((rule) => rule.selectors)).toEqual([[".bj-claimrow-claim"]]);
  });

  it("outlines a chip: gold for supported and unverifiable, red only for contradicted, muted for the rest", () => {
    expect(decl(".bj-claimrow-chip").border).toBe("1px solid var(--bj-outline)");
    expect(declarationsFor(base, ".bj-claimrow-chip--unverifiable")["border-color"]).toBe("var(--bj-gold)");
    expect(declarationsFor(base, ".bj-claimrow-chip--supported")["border-color"]).toBe("var(--bj-gold)");
    expect(declarationsFor(base, ".bj-claimrow-chip--contradicted")["border-color"]).toBe("var(--bj-danger)");
    expect(declarationsFor(base, ".bj-claimrow-chip--unchecked")["border-color"]).toBeUndefined();
    // Flagged is not backed: the two gold chips differ in the outline's style as well as the word.
    expect(declarationsFor(base, ".bj-claimrow-chip--unverifiable")["border-style"]).toBe("dashed");
    expect(declarationsFor(base, ".bj-claimrow-chip--supported")["border-style"]).toBeUndefined();
    // Red is an outline, never a fill.
    for (const rule of rulesOf(docCss)) {
      expect(rule.declarations.background ?? "").not.toContain("--bj-danger");
      expect(rule.declarations.background ?? "").not.toContain("--bj-emerald");
    }
  });

  it("stacks the chip above the claim on a phone", () => {
    expect(declarationsFor(phone, ".bj-claimrow")["flex-direction"]).toBe("column");
    expect(decl(".bj-claimrow").display).toBe("flex");
  });

  it("keeps the claim row's link 44px tall on its own, so a wrapped row does not shrink it to a line of text", () => {
    expect(declarationsFor(phone, ".bj-claimrow-actions a")).toMatchObject({ "min-height": "44px", display: "inline-flex", "align-items": "center" });
  });

  it("holds the state column at one width so every label starts in the same place", () => {
    expect(decl(".bj-checkrow-state").width).toBe("112px");
    expect(decl(".bj-checkrow-state").flex).toBe("none");
  });

  it("lets every user-text slot wrap rather than overflow", () => {
    for (const selector of [".bj-claimrow-claim", ".bj-claimrow-reason", ".bj-checkrow-label", ".bj-checkrow-note", ".bj-segbar-detail", ".bj-chapter-title"]) {
      expect(decl(selector)["overflow-wrap"]).toBe("anywhere");
    }
    for (const selector of [".bj-doc", ".bj-doc-main", ".bj-doc-margin", ".bj-claimrow", ".bj-claimrow-body", ".bj-checkrow", ".bj-checkrow-body", ".bj-segbar"]) {
      expect(decl(selector)["min-width"]).toBe("0");
    }
  });
});
