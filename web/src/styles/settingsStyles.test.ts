import { describe, expect, it } from "vitest";
import tokensCss from "../tokens.css?raw";
import { blockOf, declarationsFor, rulesOf, withoutMedia } from "../testing/css";
import { PHONE_QUERY } from "../lib/useMediaQuery";
import settingsCss from "./settings.css?raw";

// settings.css is read as text (there is no layout engine in the test run). What it pins is what can
// be measured in it: it defines no color, it fills nothing cyan, and its phone rules sit at the
// shell's own breakpoint and keep the page inside 375px.

const base = withoutMedia(settingsCss);
const phone = blockOf(settingsCss, "@media (max-width: 959px)") ?? "";

describe("settings.css", () => {
  it("was loaded as text and has its phone block", () => {
    expect(settingsCss.length).toBeGreaterThan(1500);
    expect(phone).not.toBe("");
  });

  it("switches its phone rules at the same width as the shell and the paper primitives", () => {
    expect(blockOf(settingsCss, `@media ${PHONE_QUERY}`)).toBe(phone);
  });

  it("defines no color: no hex value, no rgb or hsl, only tokens", () => {
    const code = settingsCss.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(code).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(code).not.toMatch(/\b(?:rgb|rgba|hsl|hsla)\(/);
  });

  it("only uses tokens that tokens.css defines", () => {
    const defined = new Set([...tokensCss.matchAll(/(--[\w-]+)\s*:/g)].map((match) => match[1]));
    const used = new Set([...settingsCss.matchAll(/var\((--[\w-]+)/g)].map((match) => match[1]));
    expect([...used].filter((name) => !defined.has(name))).toEqual([]);
  });

  it("fills nothing cyan: the page's one cyan fill is the next-step band's", () => {
    for (const rule of rulesOf(settingsCss)) {
      for (const property of ["background", "background-color"]) {
        expect(rule.declarations[property] ?? "").not.toContain("--bj-cyan");
      }
    }
  });

  it("marks a chosen model the way every chosen item is marked: the maroon fill with the ivory text", () => {
    const chosen = declarationsFor(base, ".bj-set-model:has(input:checked)");
    expect(chosen.background).toBe("var(--bj-selected-bg)");
    expect(chosen.color).toBe("var(--bj-selected-text)");
    // and not with an underline, an edge bar or a colored border
    expect(chosen["border-left"]).toBeUndefined();
    expect(chosen["border-color"]).toBeUndefined();
    expect(chosen["text-decoration"]).toBeUndefined();
  });

  it("keeps focus separate from the choice: a 2px ring drawn inside the row, so the scroller does not clip it", () => {
    const ring = declarationsFor(base, ".bj-set-model:has(input:focus-visible)");
    expect(ring.outline).toBe("var(--bj-focus-ring-width) solid var(--bj-focus-ring)");
    expect(ring["outline-offset"]).toContain("-1");
  });

  it("redraws the chosen model in the system colors under forced colors, like the shared primitive", () => {
    const forced = blockOf(settingsCss, "@media (forced-colors: active)") ?? "";
    expect(forced).toContain("Highlight");
    expect(forced).toContain("HighlightText");
  });

  it("scrolls the model list inside itself, so 500 models never lengthen the page or widen it", () => {
    const list = declarationsFor(base, ".bj-set-models");
    expect(list["max-height"]).toBe("340px");
    expect(list["overflow-y"]).toBe("auto");
    expect(declarationsFor(phone, ".bj-set-models")["max-height"]).toBe("300px");
  });

  it("lets every flex child that holds text shrink, and wraps long text, so a long id cannot widen the page", () => {
    for (const selector of [".bj-settings", ".bj-set-section", ".bj-set-item", ".bj-set-row-main", ".bj-set-card", ".bj-set-field", ".bj-set-model-text"]) {
      expect(declarationsFor(base, selector)["min-width"], selector).toBe("0");
    }
    for (const selector of [".bj-set-note", ".bj-set-hint", ".bj-set-mono", ".bj-set-model-name", ".bj-set-error", ".bj-set-notice"]) {
      expect(declarationsFor(base, selector)["overflow-wrap"], selector).toBe("anywhere");
    }
  });

  it("stacks a row's actions under its text on a phone, and gives touch targets 44px", () => {
    expect(declarationsFor(phone, ".bj-set-row")["flex-direction"]).toBe("column");
    for (const selector of [
      ".bj-set-field input",
      ".bj-set-field select",
      ".bj-set-actions > button",
      ".bj-set-oneclick > button",
      ".bj-set-row-actions > button",
      ".bj-set-chips > button",
      ".bj-set-card-head > button",
      "button.bj-set-reveal",
    ]) {
      expect(declarationsFor(phone, selector)["min-height"], selector).toBe("44px");
    }
  });

  it("underlines a link inside a sentence, because its color alone is under 3:1 against the text around it", () => {
    for (const selector of [".bj-set-cost a", ".bj-set-hint a", ".bj-set-notice a", ".bj-set-lede a"]) {
      expect(declarationsFor(base, selector)["text-decoration"], selector).toBe("underline");
    }
  });

  it("draws the side-note as quiet text: muted, no border, no fill, no red, in a mono label", () => {
    const note = declarationsFor(base, ".bj-set-sidenote");
    expect(note.color).toBe("var(--bj-text-muted)");
    for (const property of ["border", "border-color", "background", "background-color"]) expect(note[property], property).toBeUndefined();
    const label = declarationsFor(base, ".bj-set-sidenote-label");
    expect(label["font-family"]).toBe("var(--bj-font-mono)");
    expect(label["text-transform"]).toBe("uppercase");
    // nothing that styles the note, its label or the default tag reaches for the failure color
    for (const rule of rulesOf(settingsCss)) {
      const selector = rule.selectors.join(", ");
      if (/sidenote|default-tag|extras|set-group|set-extra/.test(selector)) {
        for (const value of Object.values(rule.declarations)) expect(value, selector).not.toContain("--bj-danger");
      }
    }
  });

  it("draws the optional extras' toggle as the heading itself, 44px tall, with a chevron that turns and holds still under reduced motion", () => {
    const toggle = declarationsFor(base, "button.bj-extras-toggle");
    expect(toggle["min-height"]).toBe("44px");
    expect(toggle.font).toBe("inherit");
    expect(toggle["border-color"]).toBe("transparent");
    expect(declarationsFor(base, 'button.bj-extras-toggle[aria-expanded="true"] .bj-extras-chevron').transform).toBe("rotate(90deg)");
    expect(blockOf(settingsCss, "@media (prefers-reduced-motion: reduce)") ?? "").toContain("transition: none");
    expect(declarationsFor(phone, "button.bj-extras-toggle")["min-height"]).toBe("44px");
    expect(declarationsFor(phone, ".bj-set-extra-actions > button")["min-height"]).toBe("44px");
  });

  it("never sets display on the disclosure's body, so the hidden attribute still hides it", () => {
    expect(declarationsFor(base, ".bj-extras-body").display).toBeUndefined();
  });

  it("lets the extras' rows shrink and wrap, so a long name cannot widen the page", () => {
    for (const selector of [".bj-set-extra", ".bj-set-group", ".bj-extras-body", ".bj-set-extra-name"]) {
      expect(declarationsFor(base, selector)["min-width"], selector).toBe("0");
    }
    expect(declarationsFor(base, ".bj-set-sidenote")["overflow-wrap"]).toBe("anywhere");
    expect(declarationsFor(base, ".bj-set-extra-line")["flex-wrap"]).toBe("wrap");
  });

  it("measures the page as a list: 880px at most", () => {
    expect(declarationsFor(base, ".bj-settings")["max-width"]).toBe("880px");
  });
});
