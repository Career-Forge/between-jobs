import { describe, expect, it } from "vitest";
import tokensCss from "../tokens.css?raw";
import { PHONE_QUERY } from "../lib/useMediaQuery";
import { blockOf, declarationsFor, rulesOf, tokenResolver, withoutMedia } from "../testing/css";
import workspaceCss from "./workspace.css?raw";

// workspace.css is read as text. It pins what can be measured from the rules: that it defines no color of
// its own, never fills anything cyan, keeps the page to one cyan (the panels' own primary buttons are
// drawn outlined inside their slots), and the few rules the page's structure depends on.

const resolve = tokenResolver(tokensCss);
const base = withoutMedia(workspaceCss);
const decl = (selector: string) => declarationsFor(base, selector);

describe("workspace.css", () => {
  it("was loaded as text", () => {
    expect(workspaceCss.length).toBeGreaterThan(3000);
  });

  it("defines no color: no hex value, no rgb or hsl, only tokens", () => {
    const code = workspaceCss.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(code).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(code).not.toMatch(/\b(?:rgb|rgba|hsl|hsla)\(/);
  });

  it("only uses tokens that tokens.css defines", () => {
    const defined = new Set([...tokensCss.matchAll(/(--[\w-]+)\s*:/g)].map((match) => match[1]));
    const used = new Set([...workspaceCss.matchAll(/var\((--[\w-]+)/g)].map((match) => match[1]));
    expect([...used].filter((name) => !defined.has(name))).toEqual([]);
  });

  it("never fills anything cyan: the page's one cyan fill is the next-step band's button", () => {
    for (const rule of rulesOf(workspaceCss)) {
      for (const property of ["background", "background-color"]) {
        expect(rule.declarations[property] ?? "").not.toContain("--bj-cyan");
      }
    }
  });

  it("asks for a width only at the shell's own phone width", () => {
    const headers = [...workspaceCss.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/@media\s*([^{]+)\{/g)].map((match) => match[1].trim());
    expect(headers).toEqual([PHONE_QUERY]);
    expect(blockOf(workspaceCss, `@media ${PHONE_QUERY}`)).toContain(".bj-ws-status");
  });

  it("draws a primary button inside a research or prepare panel outlined, so the band keeps the page's only cyan", () => {
    const rule = decl(".bj-ws-panel button.bj-primary");
    expect(rule.background).toBe("transparent");
    expect(rule["border-color"]).toBe("var(--bj-outline)");
    expect(rule.color).toBe("var(--bj-text)");
  });

  it("draws the link form of the cyan button inside a panel outlined too (Contacts' Gmail links), hover included", () => {
    const rule = decl(".bj-ws-panel a.bj-button-link-primary");
    expect(rule.background).toBe("transparent");
    expect(rule["border-color"]).toBe("var(--bj-outline)");
    expect(rule.color).toBe("var(--bj-text)");
    expect(decl(".bj-ws-panel a.bj-button-link-primary:hover").background).toBe("var(--bj-hover-bg)");
    // It has to out-rank app.css's a.bj-button-link-primary (and its :hover) on specificity, not on order.
    expect(workspaceCss).toContain(".bj-ws-panel a.bj-button-link-primary:hover");
  });

  it("underlines a link inside a sentence, because its color alone is about 1.5:1 against the text around it", () => {
    for (const selector of [".bj-ws-callout a", ".bj-ws-intro a", ".bj-ws-caption a", ".bj-ws-section p a:not(.bj-button-link)"]) {
      expect(decl(selector)["text-decoration"], selector).toBe("underline");
    }
  });

  it("hides a panel's own leading h2: the section's chapter is its heading", () => {
    expect(decl(".bj-ws-panel .bj-doc-main > .bj-card > h2:first-child").display).toBe("none");
  });

  it("draws the one solid red block with the canvas color for its text, never white", () => {
    const rule = decl(".bj-ws-callout--danger");
    expect(rule.background).toBe("var(--bj-danger)");
    expect(rule.color).toBe("var(--bj-bg)");
    expect(workspaceCss).not.toMatch(/color:\s*(?:#fff|white)/i);
  });

  it("draws a missing keyword with a dashed muted outline, and a flagged skill dashed, so neither relies on color", () => {
    expect(decl(".bj-ws-chip--missing")["border-style"]).toBe("dashed");
    expect(decl(".bj-ws-chip--missing").color).toBe("var(--bj-text-muted)");
    expect(decl(".bj-ws-skill--adjacent")["border-style"]).toBe("dashed");
  });

  it("sets must-have in ivory bold and preferred muted", () => {
    expect(decl(".bj-ws-priority--must").color).toBe("var(--bj-text)");
    expect(decl(".bj-ws-priority--must")["font-weight"]).toBe("700");
    expect(decl(".bj-ws-priority").color).toBe("var(--bj-text-muted)");
  });

  it("keeps embedded controls inside the phone's width and a job description in a well that scrolls inside itself", () => {
    expect(decl(".bj-ws-section .bj-card select")["max-width"]).toBe("100%");
    expect(decl(".bj-ws-well")["overflow"]).toBe("auto");
    expect(decl(".bj-ws-well")["max-height"]).toBe("280px");
  });

  it("gives the status select a 44px target on a phone", () => {
    const phone = blockOf(workspaceCss, `@media ${PHONE_QUERY}`) ?? "";
    expect(declarationsFor(phone, "select.bj-ws-status-select")["min-height"]).toBe("44px");
  });

  it("gives every control the page draws a 44px target on a phone: buttons, button-links, checkbox labels, the posting link", () => {
    const phone = blockOf(workspaceCss, `@media ${PHONE_QUERY}`) ?? "";
    for (const selector of [".bj-ws-section button", ".bj-ws-check", ".bj-ws-section a.bj-button-link", ".bj-ws-facts a"]) {
      expect(declarationsFor(phone, selector)["min-height"], selector).toBe("44px");
    }
    // A link has no centered label of its own: it needs flex to keep the text in the middle of 44px.
    expect(declarationsFor(phone, ".bj-ws-section a.bj-button-link")["display"]).toBe("inline-flex");
  });

  it("uses the contrast pairs the tokens pin: canvas on danger is at least 4.5", () => {
    const hex = (name: string) => resolve(`var(${name})`);
    const channel = (c: number) => ((c /= 255) <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
    const luminance = (value: string) => {
      const n = parseInt(value.replace("#", ""), 16);
      return 0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255);
    };
    const a = luminance(hex("--bj-bg"));
    const b = luminance(hex("--bj-danger"));
    expect((Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)).toBeGreaterThanOrEqual(4.5);
  });
});
