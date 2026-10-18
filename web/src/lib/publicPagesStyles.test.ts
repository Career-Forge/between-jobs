import { describe, expect, it } from "vitest";
import authCss from "../styles/auth.css?raw";
import circuitCss from "../styles/circuit.css?raw";
import landingCss from "../styles/landing.css?raw";
import legalCss from "../styles/legal.css?raw";
import publicCss from "../styles/public.css?raw";
import tokensCss from "../tokens.css?raw";
import { blockOf, declarationsFor, horizontalPaddingPx, rulesOf, tokenResolver, withoutMedia } from "../testing/css";

// What the public pages' stylesheets promise that no rendered-markup test can see (the package has no
// layout engine, so no computed style): that a link inside a sentence carries a cue other than colour,
// that the header, the page and the footer sit in one column, that nothing has a width that could push a
// phone sideways, that the reading measure and the type roles hold, and that nothing moves for someone
// who asked for less motion. The files are public.css (the frame), landing.css and circuit.css (the
// landing page), auth.css (sign in) and legal.css (the two documents).

const ALL = { publicCss, landingCss, circuitCss, authCss, legalCss };
const resolve = tokenResolver(tokensCss);

// A prose-link rule: the selector, and the stylesheet it is written in.
const PROSE_LINKS: [string, string][] = [
  [".bj-legal p a", legalCss],
  [".bj-legal ul a", legalCss],
  [".bj-legal dd a", legalCss],
  [".bj-landing section p a", landingCss],
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
      ".bj-public-sections a",
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

  it("folds the header's section links away under 960px, where the circuit becomes a list too", () => {
    const phone = blockOf(publicCss, "@media (max-width: 959px)") ?? "";
    expect(declarationsFor(phone, ".bj-public-sections").display).toBe("none");
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
    for (const selector of [".bj-landing", ".bj-landing-hero-copy", ".bj-landing-hero-art", ".bj-landing-dossier-copy", ".bj-dossier-figure", ".bj-landing-card", ".bj-landing-chapter > div", ".bj-landing-source-copy"]) {
      expect(declarationsFor(landingCss, selector)["min-width"], selector).toBe("0");
    }
    for (const selector of [".bj-circuit", ".bj-circuit-frame", ".bj-circuit-stage", ".bj-circuit-card", ".bj-circuit-card-body", ".bj-circuit-demo-body"]) {
      expect(declarationsFor(circuitCss, selector)["min-width"], selector).toBe("0");
    }
    expect(declarationsFor(authCss, ".bj-login-column")["min-width"]).toBe("0");
    expect(declarationsFor(authCss, ".bj-login-card")["min-width"]).toBe("0");
    expect(declarationsFor(legalCss, ".bj-legal")["min-width"]).toBe("0");
  });

  it("stacks the hero, the dossier and the chapters into one column under 960px", () => {
    const phone = blockOf(landingCss, "@media (max-width: 959px)") ?? "";
    for (const selector of [".bj-landing-hero", ".bj-landing-dossier", ".bj-landing-chapters"]) {
      expect(declarationsFor(phone, selector)["grid-template-columns"], selector).toBe("minmax(0, 1fr)");
    }
  });

  it("turns the circuit into a vertical list under 960px: no horizontal trace, one column, only the stage in view shows its card", () => {
    const phone = blockOf(circuitCss, "@media (max-width: 959px)") ?? "";
    expect(declarationsFor(phone, ".bj-circuit-stages").display).toBe("block");
    expect(declarationsFor(phone, ".bj-circuit-trunk").display).toBe("none");
    expect(declarationsFor(phone, '.bj-circuit-stage[data-active="false"] .bj-circuit-card').display).toBe("none");
    // On a wide screen it is five columns, each able to shrink.
    expect(declarationsFor(withoutMedia(circuitCss), ".bj-circuit-stages")["grid-template-columns"]).toBe("repeat(5, minmax(0, 1fr))");
  });

  it("keeps the sign-in column and its card inside the screen", () => {
    expect(declarationsFor(authCss, ".bj-login-column")).toMatchObject({ width: "100%", "max-width": "440px" });
    expect(declarationsFor(authCss, ".bj-login-card").width).toBe("100%");
    expect(horizontalPaddingPx(declarationsFor(authCss, ".bj-login"))).toEqual([16, 16]);
  });
});

describe("type and measure", () => {
  it("holds reading prose to 680px: the legal pages and the landing's paragraphs", () => {
    expect(declarationsFor(legalCss, ".bj-legal")["max-width"]).toBe("680px");
    expect(declarationsFor(landingCss, ".bj-landing-section p")["max-width"]).toBe("680px");
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
    // A control is never serif: no rule that styles a button or the stage buttons names the display face.
    const controls = rulesOf(circuitCss).filter((rule) => rule.selectors.some((selector) => /stage-button|stage-label|node|mock/.test(selector)));
    expect(controls.length).toBeGreaterThan(3);
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
    for (const [name, css] of Object.entries({ publicCss, circuitCss, authCss, legalCss })) {
      const code = css.replace(/\/\*[\s\S]*?\*\//g, "");
      expect(code, name).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
      expect(code, name).not.toMatch(/\b(?:rgb|rgba|hsl|hsla)\(/);
    }
    const sheetFree = landingCss.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\.bj-resume-sheet \{[^}]*\}/, "");
    expect(sheetFree).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(sheetFree).not.toMatch(/\b(?:rgb|rgba|hsl|hsla)\(/);
  });

  it("only uses tokens that tokens.css defines", () => {
    const defined = new Set([...tokensCss.matchAll(/(--[\w-]+)\s*:/g)].map((match) => match[1]));
    for (const [name, css] of Object.entries(ALL)) {
      // The sheet's and the circuit's own custom properties are declared in the same file.
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

  it("turns off every transition and animation of the circuit and the landing page under prefers-reduced-motion", () => {
    const circuit = blockOf(circuitCss, "@media (prefers-reduced-motion: reduce)") ?? "";
    expect(circuit).not.toBe("");
    expect(declarationsFor(circuit, ".bj-circuit *")).toMatchObject({ transition: "none", animation: "none" });
    const landing = blockOf(landingCss, "@media (prefers-reduced-motion: reduce)") ?? "";
    expect(declarationsFor(landing, ".bj-landing *")).toMatchObject({ transition: "none", animation: "none" });
  });

  it("fades the sample next step for 200ms and no longer", () => {
    expect(declarationsFor(circuitCss, ".bj-circuit-demo-text").animation).toBe("bj-circuit-fade 200ms ease-out");
  });
});
