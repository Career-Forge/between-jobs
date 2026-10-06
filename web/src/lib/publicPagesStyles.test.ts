import { describe, expect, it } from "vitest";
import appCss from "../app.css?raw";
import { declarationsFor, horizontalPaddingPx, rulesOf } from "../testing/css";

// Two things about the public pages' stylesheet that no rendered-markup test can see (the
// package has no DOM, so no computed style): that a link inside a sentence carries a cue other
// than colour, and that the footer sits in the same column as the header and the page.

const PROSE_LINK_SELECTORS = [
  ".bj-legal p a",
  ".bj-legal ul a",
  ".bj-legal dd a",
  ".bj-landing section p a",
  ".bj-public-footer p a",
  ".bj-consent-notice a",
];

describe("links inside running text", () => {
  it.each(PROSE_LINK_SELECTORS)(
    "%s is underlined at rest: cyan beside ivory or muted text is about 1.5:1, and WCAG 1.4.1 asks for 3:1 or another cue",
    (selector) => {
      expect(declarationsFor(appCss, selector)["text-decoration"]).toBe("underline");
    },
  );

  it.each(PROSE_LINK_SELECTORS)("%s keeps the underline on hover and gets a thicker one", (selector) => {
    const hover = declarationsFor(appCss, `${selector}:hover`);
    expect(hover["text-decoration-thickness"]).toBe("2px");
    expect(hover.color).toBe("var(--bj-cyan-hover)");
  });

  it("leaves link lists and button-styled links plain: the table of contents, the footer's legal row, the nav, the buttons", () => {
    for (const selector of [
      ".bj-legal-links a",
      ".bj-legal-toc a",
      ".bj-legal li a",
      "a.bj-button-link",
      ".bj-public-wordmark",
    ]) {
      expect(declarationsFor(appCss, selector)["text-decoration"], selector).toBeUndefined();
    }
    // The prose rule names `ul`, not `li`, so the table of contents (an `ol`) is not in it.
    const underlined = rulesOf(appCss)
      .filter((rule) => rule.declarations["text-decoration"] === "underline")
      .flatMap((rule) => rule.selectors);
    for (const selector of PROSE_LINK_SELECTORS) expect(underlined).toContain(selector);
    expect(underlined.some((selector) => /\bli\b|\bol\b|bj-legal-toc|bj-legal-links/.test(selector))).toBe(false);
  });
});

describe("the public footer's column", () => {
  const header = declarationsFor(appCss, ".bj-public-header");
  const main = declarationsFor(appCss, ".bj-public-main");
  const footer = declarationsFor(appCss, ".bj-public-footer");
  const footerChild = declarationsFor(appCss, ".bj-public-footer > *");

  it("has the header, the page and the footer's content all in a 960px column", () => {
    for (const rule of [header, main, footerChild]) expect(rule["max-width"]).toBe("960px");
  });

  it("insets the header, the page and the footer's content by the same 16px, so their left edges line up", () => {
    expect(horizontalPaddingPx(header)).toEqual([16, 16]);
    expect(horizontalPaddingPx(main)).toEqual([16, 16]);
    expect(horizontalPaddingPx(footerChild)).toEqual([16, 16]);
  });

  it("puts the footer's inset on its children, not on the footer, which would pull them 16px left of the rest", () => {
    expect(horizontalPaddingPx(footer)).toEqual([0, 0]);
    expect(footerChild["margin-left"]).toBe("auto");
    expect(footerChild["margin-right"]).toBe("auto");
  });
});
