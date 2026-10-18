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

  it("holds the reserved palette row at 40px, dashed in the border color", () => {
    expect(base(".bj-palette-slot")).toMatchObject({ height: "40px", border: "1px dashed var(--bj-border)" });
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

  it("makes five equal columns of the tab bar, padded by the home indicator", () => {
    expect(base(".bj-tabbar")).toMatchObject({ position: "fixed", "grid-template-columns": "repeat(5, minmax(0, 1fr))", gap: "2px" });
    expect(base(".bj-tabbar").padding).toBe("4px 4px calc(4px + env(safe-area-inset-bottom))");
  });

  it("sets each tab at 12px and weight 600, the one place text on the selection is under 13px, in a cell over 44px tall", () => {
    const tab = base(".bj-tab");
    expect(size(tab["font-size"])).toBe(12);
    expect(tab["font-weight"]).toBe("600");
    expect(size(tab["min-height"])).toBeGreaterThanOrEqual(44);
    expect(base(".bj-tab span")["white-space"]).toBe("nowrap");
  });

  it("has a 44px top-bar link, a 44px Close button and 52px rows in the More sheet", () => {
    expect(size(base(".bj-topbar-brand")["min-height"])).toBeGreaterThanOrEqual(44);
    expect(size(base(".bj-more-close").width)).toBe(44);
    expect(size(base(".bj-more-close").height)).toBe(44);
    expect(size(base(".bj-more-row")["min-height"])).toBe(52);
    expect(size(base(".bj-skip-link")["min-height"])).toBeGreaterThanOrEqual(44);
  });

  it("pins the More sheet to the bottom edge, no wider than the screen, and stops the page behind it scrolling", () => {
    expect(base(".bj-more-sheet")).toMatchObject({ position: "fixed", inset: "auto 0 0 0", width: "100%", "max-width": "none" });
    expect(withoutMedia(appCss)).toContain("body:has(.bj-more-sheet[open])");
  });

  it("holds the sign-in card to the screen, so 375px (and less) never scrolls sideways", () => {
    expect(base(".bj-login-card")).toMatchObject({ width: "100%", "max-width": "360px" });
    expect(size(base(".bj-login").padding)).toBe(16);
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
