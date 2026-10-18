import { describe, expect, it } from "vitest";
import appCss from "./app.css?raw";
import tokensCss from "./tokens.css?raw";
import { blockOf, declarationsFor, rulesOf, tokenResolver, withoutMedia } from "./testing/css";

// What the shell's stylesheet promises about layout, read as text (there is no DOM here, so no
// computed style): where the rail gives way to the tab bar, how big the touch targets are, and
// that nothing can push the page wider than a 375px phone. The markup side is AppShell.test.tsx.

const px = tokenResolver(tokensCss);
const base = (selector: string): Record<string, string> => declarationsFor(withoutMedia(appCss), selector);
const phoneBlock = blockOf(appCss, "@media (max-width: 959px)") ?? "";
const phone = (selector: string): Record<string, string> => declarationsFor(phoneBlock, selector);
const size = (value: string | undefined): number => Number.parseFloat(px(value ?? "NaN"));

describe("the rail", () => {
  it("is 220px wide, stays in view while the page scrolls, and leaves no room for a horizontal scroll", () => {
    expect(base(".bj-rail")).toMatchObject({ width: "220px", position: "sticky", top: "0", height: "100vh", "overflow-y": "auto" });
    expect(base(".bj-rail").flex).toBe("none");
  });

  it("wraps a long signed-in address instead of widening the rail", () => {
    expect(base(".bj-rail-email")["overflow-wrap"]).toBe("anywhere");
  });

  it("has 40px rows, 44px on a touch screen", () => {
    expect(size(base(".bj-nav-item")["min-height"])).toBe(40);
    const coarse = blockOf(appCss, "@media (pointer: coarse)") ?? "";
    expect(size(declarationsFor(coarse, ".bj-nav-item")["min-height"])).toBe(44);
    expect(size(declarationsFor(coarse, ".bj-rail-signout")["min-height"])).toBe(44);
  });

  it("keeps the reserved palette slot's geometry but never displays it by a rule, so `hidden` holds", () => {
    expect(base(".bj-palette-slot")).toMatchObject({ height: "40px" });
    expect(base(".bj-palette-slot")).not.toHaveProperty("display");
    expect(base(".bj-palette-slot")).not.toHaveProperty("border");
  });

  it("leaves selection to the shared primitive: no rule of its own colors an active rail row", () => {
    const own = rulesOf(appCss).filter((rule) => rule.selectors.some((selector) => /^\.bj-nav-item.*(\.active|aria-current)/.test(selector) && !selector.includes(":not(")));
    expect(own).toEqual([]);
  });
});

describe("under 960px", () => {
  it("swaps the rail for a top bar and a bottom tab bar, and starts the layout in a column", () => {
    expect(base(".bj-topbar").display).toBe("none");
    expect(base(".bj-tabbar").display).toBe("none");
    expect(phone(".bj-rail").display).toBe("none");
    expect(phone(".bj-topbar").display).toBe("flex");
    expect(phone(".bj-tabbar").display).toBe("grid");
    expect(phone(".bj-shell")["flex-direction"]).toBe("column");
  });

  it("pads the page 32px rather than 48px up to 1279px, where the content column is still 740px at 1024", () => {
    const mid = blockOf(appCss, "@media (max-width: 1279px)") ?? "";
    expect(declarationsFor(mid, ".bj-main").padding).toBe("40px 32px");
    expect(base(".bj-main").padding).toBe("40px 48px");
  });

  it("lets the page use the whole width and clear the tab bar and the home indicator", () => {
    expect(phone(".bj-main")).toMatchObject({ width: "100%", "max-width": "none" });
    expect(phone(".bj-main").padding).toContain("env(safe-area-inset-bottom)");
    expect(base(".bj-main")["min-width"]).toBe("0");
  });

  it("makes five equal columns of the tab bar, padded by the home indicator and by a notch on either side", () => {
    expect(base(".bj-tabbar")).toMatchObject({ position: "fixed", "grid-template-columns": "repeat(5, minmax(0, 1fr))", gap: "2px" });
    expect(base(".bj-tabbar").padding).toBe(
      "4px max(4px, env(safe-area-inset-right)) calc(4px + env(safe-area-inset-bottom)) max(4px, env(safe-area-inset-left))",
    );
  });

  it("keeps whatever is focused or jumped to clear of the opaque top bar and tab bar", () => {
    // Without this, tabbing down a long page parks the focused control, ring and all, behind the
    // fixed tab bar (and Shift+Tab behind the sticky top bar); a legal page's table-of-contents
    // links land their heading under the top bar. 56px and 65px are the two bars' heights.
    const html = phone("html");
    expect(html["scroll-padding-top"]).toBe("calc(56px + env(safe-area-inset-top) + var(--bj-space-2))");
    expect(html["scroll-padding-bottom"]).toBe("calc(65px + env(safe-area-inset-bottom) + var(--bj-space-2))");
    expect(size(base(".bj-tab")["min-height"])).toBe(56); // 4 + 56 + 4 + the 1px border = 65
    expect(base(".bj-topbar").height).toBe("calc(56px + env(safe-area-inset-top))");
  });

  it("lets a tab label wrap as a last resort, and fits the longest one on a single line down to 320px", () => {
    expect(base(".bj-tab span")["overflow-wrap"]).toBe("anywhere");
    expect(base(".bj-tab span")).not.toHaveProperty("white-space");
    // "Applications" is 69.7px at 12px and 63.9px at 11px (measured with the shipped fonts). At 12px
    // it fits a 375px phone's 71.8px cell but not 68.8px at 360px, so under 375px the bar drops its
    // gaps and the label goes to 11px, which fits the 62.4px cell of a 320px phone.
    const narrow = blockOf(appCss, "@media (max-width: 374px)") ?? "";
    expect(declarationsFor(narrow, ".bj-tab")["font-size"]).toBe("11px");
    expect(declarationsFor(narrow, ".bj-tabbar").gap).toBe("0");
  });

  it("pads the page, the fixed bars and the sheet by a notch's left and right insets", () => {
    expect(declarationsFor(tokensCss, "body")).toMatchObject({
      "padding-left": "env(safe-area-inset-left)",
      "padding-right": "env(safe-area-inset-right)",
    });
    expect(base(".bj-more-body").padding).toContain("env(safe-area-inset-left)");
    expect(base(".bj-more-body").padding).toContain("env(safe-area-inset-right)");
    expect(base(".bj-skip-link").left).toContain("env(safe-area-inset-left)");
  });

  it("sets each tab at 12px and weight 600, the one place text on the selection is under 13px, in a cell over 44px tall", () => {
    const tab = base(".bj-tab");
    expect(size(tab["font-size"])).toBe(12);
    expect(tab["font-weight"]).toBe("600");
    expect(size(tab["min-height"])).toBeGreaterThanOrEqual(44);
  });

  it("has a 44px top-bar link, a 44px Close button and 52px rows in the More sheet", () => {
    expect(size(base(".bj-topbar-brand")["min-height"])).toBeGreaterThanOrEqual(44);
    expect(size(base(".bj-more-close").width)).toBe(44);
    expect(size(base(".bj-more-close").height)).toBe(44);
    expect(size(base(".bj-more-row")["min-height"])).toBe(52);
    expect(size(base(".bj-skip-link")["min-height"])).toBeGreaterThanOrEqual(44);
  });

  it("gives the legal links in the More sheet the same 44px rows as the rest of it", () => {
    expect(base(".bj-more-sheet .bj-legal-links")["flex-direction"]).toBe("column");
    expect(size(base(".bj-more-sheet .bj-legal-links a")["min-height"])).toBeGreaterThanOrEqual(44);
    expect(base(".bj-more-sheet .bj-legal-links a").display).toBe("flex");
  });

  it("pins the More sheet to the bottom edge, no wider than the screen, and stops the page behind it scrolling", () => {
    expect(base(".bj-more-sheet")).toMatchObject({ position: "fixed", inset: "auto 0 0 0", width: "100%", "max-width": "none" });
    expect(withoutMedia(appCss)).toContain("body:has(.bj-more-sheet[open])");
  });

  // The sign-in screen's own stylesheet is styles/auth.css (pinned in lib/publicPagesStyles.test.ts: its
  // column is 100% wide up to 440px and its page keeps a 16px inset). The shell's stylesheet no longer
  // has any rule for it.
  it("leaves the sign-in screen to its own stylesheet", () => {
    expect(base(".bj-login-card")).toEqual({});
    expect(base(".bj-login")).toEqual({});
  });
});

describe("the skip link", () => {
  it("sits off screen until it has focus, and then shows in the corner", () => {
    expect(base(".bj-skip-link").transform).toBe("translateY(-200%)");
    expect(base(".bj-skip-link:focus").transform).toBe("none");
  });
});

describe("the reserved assistant drawer", () => {
  it("has the 360px right-edge geometry ready, and is never displayed by a rule", () => {
    expect(base(".bj-assistant-slot")).toMatchObject({ position: "fixed", right: "0", width: "360px" });
    expect(base(".bj-assistant-slot")).not.toHaveProperty("display");
  });
});
