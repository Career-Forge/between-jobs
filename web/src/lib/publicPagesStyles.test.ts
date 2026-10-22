import { describe, expect, it } from "vitest";
import appCss from "../app.css?raw";
import authCss from "../styles/auth.css?raw";
import landingCss from "../styles/landing.css?raw";
import legalCss from "../styles/legal.css?raw";
import paperCss from "../styles/paper.css?raw";
import publicCss from "../styles/public.css?raw";
import siteCss from "../styles/site-pages.css?raw";
import tourCss from "../styles/tour.css?raw";
import tokensCss from "../tokens.css?raw";
import { blockOf, declarationsFor, horizontalPaddingPx, rulesOf, tokenResolver, withoutMedia } from "../testing/css";

// What the public pages' stylesheets promise that no rendered-markup test can see (the package has no
// layout engine, so no computed style): that a link inside a sentence carries a cue other than colour,
// that the header, the page and the footer sit in one column, that nothing has a width that could push a
// phone sideways, that the reading measure and the type roles hold, and that nothing moves for someone
// who asked for less motion. The files are public.css (the frame), landing.css and tour.css (the
// landing page), site-pages.css (How it works and Open source), auth.css (sign in) and legal.css (the two
// documents).

const ALL = { publicCss, landingCss, tourCss, siteCss, authCss, legalCss };
const resolve = tokenResolver(tokensCss);

// A prose-link rule: the selector, and the stylesheet it is written in.
const PROSE_LINKS: [string, string][] = [
  [".bj-legal p a", legalCss],
  [".bj-legal ul a", legalCss],
  [".bj-legal dd a", legalCss],
  [".bj-landing section p a", landingCss],
  [".bj-site p a", siteCss],
  [".bj-public-footer p a", publicCss],
  [".bj-consent-notice a", authCss],
];

describe("links inside running text", () => {
  it.each(PROSE_LINKS)(
    "%s is underlined at rest: cyan beside ivory or muted text is about 1.5:1, and WCAG 1.4.1 asks for 3:1 or another cue",
    (selector, css) => {
      expect(declarationsFor(css, selector)["text-decoration"]).toBe("underline");
    },
  );

  it.each(PROSE_LINKS)("%s keeps the underline on hover and gets a thicker one", (selector, css) => {
    const hover = declarationsFor(css, `${selector}:hover`);
    expect(hover["text-decoration-thickness"]).toBe("2px");
    expect(hover.color).toBe("var(--bj-cyan-hover)");
  });

  it("leaves link lists and button-styled links plain: the table of contents, the footer's legal row, the header, the buttons", () => {
    const all = Object.values(ALL).join("\n");
    for (const selector of [
      ".bj-legal-links a",
      ".bj-legal-toc a",
      ".bj-legal li a",
      "a.bj-button-link",
      ".bj-public-wordmark",
      ".bj-public-tabs a",
      ".bj-public-menu a.bj-selectable",
      ".bj-public-signin",
    ]) {
      expect(declarationsFor(all, selector)["text-decoration"], selector).toBeUndefined();
    }
    // The prose rules name `ul`, not `li`, so the table of contents (an `ol`) is not in them.
    const underlined = rulesOf(all)
      .filter((rule) => rule.declarations["text-decoration"] === "underline")
      .flatMap((rule) => rule.selectors);
    for (const [selector] of PROSE_LINKS) expect(underlined).toContain(selector);
    expect(underlined.some((selector) => /\bli\b|\bol\b|bj-legal-toc|bj-legal-links/.test(selector))).toBe(false);
  });
});

describe("the public frame's column", () => {
  // rulesOf cannot tell which @media a rule is in, so the wide values are read with the queries removed.
  const wide = withoutMedia(publicCss);
  const header = declarationsFor(wide, ".bj-public-header");
  const main = declarationsFor(wide, ".bj-public-main");
  const footer = declarationsFor(wide, ".bj-public-footer");
  const footerInner = declarationsFor(wide, ".bj-public-footer-inner");

  it("has the header, the page and the footer's content all in a 1200px column", () => {
    for (const rule of [header, main, footerInner]) expect(rule["max-width"]).toBe("1200px");
  });

  it("insets the header, the page and the footer's content by the same amount, so their left edges line up", () => {
    expect(horizontalPaddingPx(header)).toEqual([40, 40]);
    expect(horizontalPaddingPx(main)).toEqual([40, 40]);
    expect(horizontalPaddingPx(footerInner)).toEqual([40, 40]);
    // And the same on a phone, where the inset is 20px for all three.
    const phone = blockOf(publicCss, "@media (max-width: 719px)") ?? "";
    expect(horizontalPaddingPx(declarationsFor(phone, ".bj-public-header"))).toEqual([20, 20]);
    expect(horizontalPaddingPx(declarationsFor(phone, ".bj-public-main"))).toEqual([20, 20]);
    expect(declarationsFor(phone, ".bj-public-footer-inner")).toMatchObject({ "padding-left": "20px", "padding-right": "20px" });
  });

  it("puts the footer's inset on its inner wrapper, not on the footer, which would pull the content left of the rest", () => {
    expect(horizontalPaddingPx(footer)).toEqual([0, 0]);
    expect(footerInner["margin-left"]).toBe("auto");
    expect(footerInner["margin-right"]).toBe("auto");
  });

  it("is written phone first: a Menu button and its panel by default, the tabs and Sign in only from 960px, Create account at every width", () => {
    const base = withoutMedia(publicCss);
    expect(declarationsFor(base, ".bj-public-tabs").display).toBe("none");
    expect(declarationsFor(base, ".bj-public-signin").display).toBe("none");
    expect(declarationsFor(base, ".bj-public-actions").display).toBe("flex");
    expect(declarationsFor(base, ".bj-public-menu-button").display).toBe("inline-flex");
    expect(declarationsFor(base, ".bj-public-menu").display).toBe("flex");
    // The panel is a row of its own under the wordmark and the button.
    expect(declarationsFor(base, ".bj-public-menu")).toMatchObject({ flex: "1 0 100%", order: "3" });
    const wide = blockOf(publicCss, "@media (min-width: 960px)") ?? "";
    expect(declarationsFor(wide, ".bj-public-tabs").display).toBe("flex");
    expect(declarationsFor(wide, ".bj-public-signin").display).toBe("inline-flex");
    expect(declarationsFor(wide, ".bj-public-menu-button").display).toBe("none");
    expect(declarationsFor(wide, ".bj-public-menu").display).toBe("none");
    // Nothing else takes anything out of the page at any width. In particular nothing hides the way in's
    // box (.bj-public-actions) or Create account inside it: a phone visitor on a page with no hero of its
    // own would have no cyan fill and no way in without opening the menu.
    const hiding = rulesOf(publicCss).filter((rule) => rule.declarations.display === "none").flatMap((rule) => rule.selectors);
    expect(hiding.sort()).toEqual([".bj-public-menu", ".bj-public-menu-button", ".bj-public-signin", ".bj-public-tabs"]);
  });

  it("makes every tab, the menu button and every link in the menu at least 44px tall", () => {
    const base = withoutMedia(publicCss);
    expect(Number.parseFloat(declarationsFor(base, ".bj-public-tabs a")["min-height"])).toBeGreaterThanOrEqual(44);
    expect(Number.parseFloat(declarationsFor(base, ".bj-public-menu-button")["min-height"])).toBeGreaterThanOrEqual(44);
    expect(Number.parseFloat(declarationsFor(base, ".bj-public-menu-button")["min-width"])).toBeGreaterThanOrEqual(44);
    expect(Number.parseFloat(declarationsFor(base, ".bj-public-menu a.bj-selectable")["min-height"])).toBeGreaterThanOrEqual(44);
    expect(Number.parseFloat(declarationsFor(base, ".bj-public-menu a.bj-public-menu-signin")["min-height"])).toBeGreaterThanOrEqual(44);
    expect(Number.parseFloat(declarationsFor(base, ".bj-public-actions a.bj-button-link")["min-height"])).toBeGreaterThanOrEqual(44);
  });

  it("lets the current page in the menu keep the selection style's hover fill: the plain hover fill is only for the other links", () => {
    // `.bj-public-menu a.bj-selectable:hover` is (0,3,1) and tokens.css's `.bj-selectable[aria-current="page"]:hover` is
    // (0,3,0), so a plain hover rule would replace the maroon fill of the current page with the grey one.
    const hovers = rulesOf(publicCss).filter((rule) => rule.selectors.some((selector) => selector.includes(".bj-public-menu a.bj-selectable") && selector.endsWith(":hover")));
    for (const rule of hovers) {
      if (rule.declarations.background === undefined) continue;
      expect(rule.selectors.every((selector) => selector.includes(":not([aria-current])")), rule.selectors.join(", ")).toBe(true);
    }
    expect(declarationsFor(publicCss, ".bj-public-menu a.bj-selectable:not([aria-current]):hover").background).toBe("var(--bj-hover-bg)");
    expect(declarationsFor(withoutMedia(tokensCss), '.bj-selectable[aria-current="page"]:hover').background).toBe("var(--bj-selected-bg-hover)");
  });

  it("marks the current tab with the selection style (a fill) and never an underline, an edge bar or a maroon line", () => {
    const rules = rulesOf(publicCss).filter((rule) => rule.selectors.some((selector) => /bj-public-(tabs|menu)/.test(selector)));
    for (const rule of rules) {
      for (const property of ["text-decoration", "text-decoration-color", "border-bottom", "border-left", "box-shadow"]) {
        // Removing the link underline (`none`) is fine; drawing one is not.
        const value = rule.declarations[property];
        expect(value === undefined || value === "none", `${rule.selectors.join(", ")} ${property}`).toBe(true);
      }
    }
  });

  it("gives a phone 44px targets in the frame: Sign in, the footer's link rows and its mailto links", () => {
    // The base height of the sign-in link is already 44, and it is a tab-sized target wherever it is drawn.
    const phone = blockOf(publicCss, "@media (max-width: 719px)") ?? "";
    for (const selector of [".bj-public-footer .bj-legal-links a", ".bj-public-footer p a"]) {
      expect(Number.parseFloat(declarationsFor(phone, selector)["min-height"]), selector).toBeGreaterThanOrEqual(44);
    }
    // The base height of the sign-in link is already 44, and the table of contents gets its own under 720px.
    expect(Number.parseFloat(declarationsFor(withoutMedia(publicCss), ".bj-public-signin")["min-height"])).toBeGreaterThanOrEqual(44);
    const legalPhone = blockOf(legalCss, "@media (max-width: 719px)") ?? "";
    expect(Number.parseFloat(declarationsFor(legalPhone, ".bj-legal-toc a")["min-height"])).toBeGreaterThanOrEqual(44);
  });
});

describe("nothing can push a 375px phone sideways", () => {
  // A plain pixel `width` or `min-width` wider than the room a phone leaves (375 less the 20px inset on
  // each side is 335) would do it, so none may be set. A `max-width`, or a width written with min(), is
  // fine: it can shrink. The stylesheets are written as plain `property: value` pairs, so this reads them.
  it.each(Object.entries(ALL))("%s sets no fixed width or min-width over 320px", (_name, css) => {
    const offenders: string[] = [];
    for (const rule of rulesOf(css)) {
      for (const property of ["width", "min-width"]) {
        const value = rule.declarations[property];
        if (value !== undefined && /^\d+(\.\d+)?px$/.test(value) && Number.parseFloat(value) > 320) {
          offenders.push(`${rule.selectors.join(", ")} { ${property}: ${value} }`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("lets every grid or flex child that holds text shrink: the layout blocks all say min-width: 0", () => {
    for (const selector of [".bj-landing", ".bj-landing-hero-copy", ".bj-landing-hero-art", ".bj-landing-card", ".bj-landing-source-copy"]) {
      expect(declarationsFor(landingCss, selector)["min-width"], selector).toBe("0");
    }
    for (const selector of [".bj-tour", ".bj-tour-tabs", ".bj-tour-panel", ".bj-tour-column", ".bj-tour-card", ".bj-tour-sheet", ".bj-tour-next-live"]) {
      expect(declarationsFor(tourCss, selector)["min-width"], selector).toBe("0");
    }
    for (const selector of [".bj-site", ".bj-how-body"]) {
      expect(declarationsFor(siteCss, selector)["min-width"], selector).toBe("0");
    }
    expect(declarationsFor(authCss, ".bj-login-column")["min-width"]).toBe("0");
    expect(declarationsFor(authCss, ".bj-login-card")["min-width"]).toBe("0");
    expect(declarationsFor(legalCss, ".bj-legal")["min-width"]).toBe("0");
  });

  it("stacks the hero into one column under 960px", () => {
    const phone = blockOf(landingCss, "@media (max-width: 959px)") ?? "";
    expect(declarationsFor(phone, ".bj-landing-hero")["grid-template-columns"]).toBe("minmax(0, 1fr)");
  });

  it("stacks every tour drawing into one column under 960px, with the trail line under both columns when wide", () => {
    const phone = blockOf(tourCss, "@media (max-width: 959px)") ?? "";
    expect(declarationsFor(phone, ".bj-tour-grid")["grid-template-columns"]).toBe("minmax(0, 1fr)");
    expect(declarationsFor(withoutMedia(tourCss), ".bj-tour-grid")["grid-template-columns"]).toBe("repeat(2, minmax(0, 1fr))");
    expect(declarationsFor(withoutMedia(tourCss), ".bj-tour-foot")["grid-column"]).toBe("1 / -1");
  });

  it("scrolls the tab strip sideways inside its own box, never the page, and keeps each tab from shrinking below its words", () => {
    const strip = declarationsFor(tourCss, ".bj-tour-tabs");
    expect(strip["overflow-x"]).toBe("auto");
    expect(strip["overscroll-behavior-x"]).toBe("contain");
    expect(strip["min-width"]).toBe("0");
    const tab = declarationsFor(tourCss, "button.bj-tour-tab");
    expect(tab.flex).toBe("1 0 auto");
    expect(tab["white-space"]).toBe("nowrap");
    expect(Number.parseFloat(tab["min-height"])).toBeGreaterThanOrEqual(44);
  });

  it("sets the sample next step as the tab's display line, with nothing in a drawing as large, and the sample disclosure as readable body text", () => {
    const px = (value: string | undefined): number => Number.parseFloat(value ?? "0");
    const sentence = declarationsFor(withoutMedia(tourCss), ".bj-landing-section .bj-tour p.bj-tour-next-sentence");
    expect(sentence["font-family"]).toBe("var(--bj-font-display)");
    expect(px(sentence["font-size"])).toBeGreaterThanOrEqual(30);
    // On a phone it is the 26px the page's other display lines use, and still not smaller than a drawing's own.
    const phone = blockOf(tourCss, "@media (max-width: 719px)") ?? "";
    expect(px(declarationsFor(phone, ".bj-landing-section .bj-tour p.bj-tour-next-sentence")["font-size"])).toBeGreaterThanOrEqual(26);
    // The drawings' own lines stay smaller than the step: the role title is 30px, and the Reach out sentence is body size.
    expect(px(declarationsFor(tourCss, ".bj-tour-statement")["font-size"])).toBeLessThanOrEqual(18);
    expect(declarationsFor(tourCss, ".bj-tour-statement")["font-family"]).toBeUndefined();
    const label = declarationsFor(tourCss, ".bj-landing-section .bj-tour p.bj-tour-sample-label");
    expect(label["text-transform"]).toBeUndefined();
    expect(label["font-family"]).toBeUndefined();
    expect(label["font-size"]).toBe("var(--bj-text-body)");
    expect(px(resolve("var(--bj-text-body)"))).toBeGreaterThanOrEqual(14);
    // The room kept for the step is set at every width, so a longer or shorter sentence does not move the drawing.
    expect(declarationsFor(withoutMedia(tourCss), ".bj-tour-next-text")["min-height"]).toBeDefined();
    expect(declarationsFor(phone, ".bj-tour-next-text")["min-height"]).toBeDefined();
  });

  it("stacks How it works' steps and Open source's list rows into one column on a phone", () => {
    const phone = blockOf(siteCss, "@media (max-width: 719px)") ?? "";
    expect(declarationsFor(phone, ".bj-how-step")["grid-template-columns"]).toBe("minmax(0, 1fr)");
    expect(declarationsFor(phone, ".bj-site-list-row")["grid-template-columns"]).toBe("minmax(0, 1fr)");
    expect(declarationsFor(withoutMedia(siteCss), ".bj-site-list-row")["grid-template-columns"]).toBe("220px minmax(0, 1fr)");
  });

  it("keeps the sign-in column and its card inside the screen", () => {
    expect(declarationsFor(authCss, ".bj-login-column")).toMatchObject({ width: "100%", "max-width": "440px" });
    expect(declarationsFor(authCss, ".bj-login-card").width).toBe("100%");
    expect(horizontalPaddingPx(declarationsFor(authCss, ".bj-login"))).toEqual([16, 16]);
  });
});

describe("the tester agreement keeps its compact type next to the legal pages' sheet", () => {
  const h3 = declarationsFor(appCss, ".bj-legal.bj-agreement h3");
  it("sets its headings at 16px and its text at the page's own size and line, with the 720px measure it had", () => {
    expect(h3["font-size"]).toBe("16px");
    for (const selector of [".bj-legal.bj-agreement p", ".bj-legal.bj-agreement li", ".bj-legal.bj-agreement dd"]) {
      expect(declarationsFor(appCss, selector)).toMatchObject({ "font-size": "inherit", "line-height": "inherit" });
    }
    expect(declarationsFor(appCss, ".bj-legal.bj-agreement")["max-width"]).toBe("720px");
  });
});

describe("the landing page's titles sit tight", () => {
  it("sets the 26px promise titles on a 1.2 line, not the page's 1.55", () => {
    expect(declarationsFor(landingCss, ".bj-landing-card h3")["line-height"]).toBe("1.2");
  });
});

describe("type and measure", () => {
  it("holds reading prose to 680px: the legal pages, the landing's paragraphs and the explainer pages'", () => {
    expect(declarationsFor(legalCss, ".bj-legal")["max-width"]).toBe("680px");
    expect(declarationsFor(landingCss, ".bj-landing-section p")["max-width"]).toBe("680px");
    expect(declarationsFor(siteCss, ".bj-site p")["max-width"]).toBe("680px");
  });

  it("sets legal body text at 16px on a 1.65 line, with serif headings that are not bold", () => {
    for (const selector of [".bj-legal p", ".bj-legal li", ".bj-legal dd"]) {
      const text = declarationsFor(legalCss, selector);
      expect(text["font-size"], selector).toBe("16px");
      expect(text["line-height"], selector).toBe("1.65");
    }
    // The headings inherit the display serif from tokens.css's h1-h3 rule; here they only lighten it.
    for (const selector of [".bj-legal h1", ".bj-legal h2"]) {
      expect(declarationsFor(legalCss, selector)["font-weight"], selector).toBe("400");
    }
    expect(declarationsFor(legalCss, ".bj-legal h1")["font-family"]).toBeUndefined();
  });

  it("sets the landing headline in the display serif and keeps controls out of it", () => {
    expect(declarationsFor(landingCss, ".bj-landing-hero h1")["font-family"]).toBe("var(--bj-font-display)");
    // A control is never serif: no rule that styles a tab, the tour's depicted buttons or the header's links names the display face.
    const controls = [
      ...rulesOf(tourCss).filter((rule) => rule.selectors.some((selector) => /bj-tour-tab|mock-button/.test(selector))),
      ...rulesOf(publicCss).filter((rule) => rule.selectors.some((selector) => /bj-public-(tabs|menu|signin)/.test(selector))),
    ];
    expect(controls.length).toBeGreaterThan(5);
    for (const rule of controls) expect(rule.declarations["font-family"] ?? "").not.toContain("display");

  });

  it("makes the sign-in inputs 16px, so a phone does not zoom the page when one takes focus, and 44px tall", () => {
    const input = declarationsFor(authCss, ".bj-login-card input");
    expect(resolve(input["font-size"])).toBe("16px");
    expect(Number.parseFloat(input["min-height"])).toBeGreaterThanOrEqual(44);
  });
});

describe("colour and motion", () => {
  it("defines no colour of its own except the printed resume page, which stands for white paper", () => {
    for (const [name, css] of Object.entries({ publicCss, landingCss, siteCss, authCss, legalCss })) {
      const code = css.replace(/\/\*[\s\S]*?\*\//g, "");
      expect(code, name).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
      expect(code, name).not.toMatch(/\b(?:rgb|rgba|hsl|hsla)\(/);
    }
    // The tour's resume page stands for white paper: its colors are custom properties on the sheet, and
    // nothing outside that one rule names a color.
    const sheetFree = tourCss.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\.bj-tour-sheet \{[^}]*\}/, "");
    expect(sheetFree).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(sheetFree).not.toMatch(/\b(?:rgb|rgba|hsl|hsla)\(/);
  });

  it("only uses tokens that tokens.css defines", () => {
    const defined = new Set([...tokensCss.matchAll(/(--[\w-]+)\s*:/g)].map((match) => match[1]));
    for (const [name, css] of Object.entries(ALL)) {
      // The sheet's and the tour's own custom properties are declared in the same file.
      for (const own of css.matchAll(/(--[\w-]+)\s*:/g)) defined.add(own[1]);
      const used = [...css.matchAll(/var\((--[\w-]+)/g)].map((match) => match[1]);
      expect(used.filter((token) => !defined.has(token)), name).toEqual([]);
    }
  });

  it("fills nothing cyan: the page's one cyan fill is the next-step band's button", () => {
    for (const [name, css] of Object.entries(ALL)) {
      for (const rule of rulesOf(css)) {
        for (const property of ["background", "background-color"]) {
          expect(rule.declarations[property] ?? "", `${name} ${rule.selectors.join(", ")}`).not.toContain("--bj-cyan");
        }
      }
    }
  });

  it("turns off every transition and animation of the tour, the explainer pages and the landing page under prefers-reduced-motion", () => {
    for (const [css, selector] of [
      [tourCss, ".bj-tour *"],
      [siteCss, ".bj-site *"],
      [landingCss, ".bj-landing *"],
    ] as const) {
      const block = blockOf(css, "@media (prefers-reduced-motion: reduce)") ?? "";
      expect(block, selector).not.toBe("");
      expect(declarationsFor(block, selector), selector).toMatchObject({ transition: "none", animation: "none" });
    }
  });

  it("fades the sample next step for 200ms and no longer", () => {
    expect(declarationsFor(tourCss, ".bj-tour-next-text").animation).toBe("bj-tour-fade 200ms ease-out");
  });
});

describe("the rules that two stylesheets once both wrote", () => {
  it("keeps the sign-in card's field rules inside the card: .bj-field is also Profile's, Applications' and the dialogs' field", () => {
    const selectors = rulesOf(authCss).flatMap((rule) => rule.selectors);
    const fieldSelectors = selectors.filter((selector) => selector.includes(".bj-field"));
    expect(fieldSelectors.length).toBeGreaterThan(0);
    for (const selector of fieldSelectors) expect(selector, "auth.css must scope .bj-field to the card").toMatch(/^\.bj-login-card\s/);
  });

  it("has one breadcrumb rule: paper.css owns .bj-breadcrumb, and the workspace, which now draws its breadcrumb through the page header, has no rule of its own", () => {
    const names = (css: string) => rulesOf(css).flatMap((rule) => rule.selectors);
    expect(names(appCss).filter((selector) => /\.bj-breadcrumb\b/.test(selector))).toEqual([]);
    expect(names(appCss).filter((selector) => /\.bj-workspace-breadcrumb\b/.test(selector))).toEqual([]);
    expect(names(paperCss).filter((selector) => /\.bj-workspace-breadcrumb\b/.test(selector))).toEqual([]);
    expect(names(paperCss).some((selector) => /^\.bj-breadcrumb\b/.test(selector))).toBe(true);
  });
});

// ── contrast of the pairs the tour and the explainer pages introduce ───────────────────────────────

function luminance(hex: string): number {
  const channels = [1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r, g, b] = channels.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(a: string, b: string): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (light + 0.05) / (dark + 0.05);
}

describe("contrast of what the tour and the explainer pages draw", () => {
  const token = (name: string): string => resolve(`var(${name})`);
  const sheet = (name: string): string => new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`).exec(tourCss)?.[1] ?? "";

  it("meets 4.5:1 for every text pair: the tabs, the labels and notes on a surface, the selected tab, the printed page", () => {
    const text: [string, string, string][] = [
      ["a tab, and the header's, at rest", token("--bj-text-muted"), token("--bj-bg")],
      ["a hovered tab", token("--bj-text"), token("--bj-surface")],
      ["the selected tab (ivory on the maroon fill, never muted)", token("--bj-selected-text"), token("--bj-selected-bg")],
      ["a kicker or note on the tour's cards", token("--bj-text-muted"), token("--bj-surface")],
      ["a note on the extension's panel", token("--bj-text-muted"), token("--bj-surface-raised")],
      ["code in a sentence", token("--bj-text"), token("--bj-surface-raised")],
      ["the printed page's text", sheet("--sheet-ink"), sheet("--sheet-paper")],
      ["the printed page's faint labels", sheet("--sheet-faint"), sheet("--sheet-paper")],
      ["the flagged word on its mark", sheet("--sheet-ink"), sheet("--sheet-mark")],
    ];
    for (const [name, foreground, background] of text) {
      expect(foreground, name).toMatch(/^#/);
      expect(background, name).toMatch(/^#/);
      expect(contrast(foreground, background), name).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("bounds the tour's tab strip in the group outline, as every group of selectable segments is, not in the 1.3:1 hairline", () => {
    const strip = declarationsFor(tourCss, ".bj-tour-tabs");
    expect(strip.border).toBe("1px solid var(--bj-outline)");
    expect(strip["border-top"]).toBeUndefined();
    expect(strip["border-bottom"]).toBeUndefined();
    expect(contrast(token("--bj-outline"), token("--bj-bg"))).toBeGreaterThanOrEqual(3);
    // The hairline it replaced is far under 3:1, which is why a maroon fill alone does not bound a group.
    expect(contrast(token("--bj-border"), token("--bj-bg"))).toBeLessThan(3);
    expect(contrast(token("--bj-selected-bg"), token("--bj-bg"))).toBeLessThan(3);
  });

  it("meets 3:1 for the lines that carry meaning: the filled and hollow cells, the dashed 'left for you' field", () => {
    expect(contrast(token("--bj-gold"), token("--bj-surface"))).toBeGreaterThanOrEqual(3);
    expect(contrast(token("--bj-outline"), token("--bj-surface"))).toBeGreaterThanOrEqual(3);
    expect(contrast(token("--bj-outline"), token("--bj-surface-raised"))).toBeGreaterThanOrEqual(3);
  });

  it("does not let the flagged word rest on its fill alone (the mark is 1.3:1 against the paper): it is also underlined and named", () => {
    expect(contrast(sheet("--sheet-mark"), sheet("--sheet-paper"))).toBeLessThan(3);
    expect(declarationsFor(tourCss, ".bj-tour-flag")["text-decoration"]).toBe("underline");
  });
});
