import { describe, expect, it } from "vitest";
import appCss from "./app.css?raw";
import tokensCss from "./tokens.css?raw";
import { blockOf, declarationsFor, rulesOf, tokenResolver, withoutMedia } from "./testing/css";

// The design tokens, read as text (no DOM here, so no computed style): the palette contract stays as
// it was, what was added is what the design calls for, and every text and non-text pair this work
// introduced meets WCAG contrast. The ratios are computed here, so a changed token that breaks one
// fails a test rather than waiting for someone to look.

const rootBlock = blockOf(tokensCss, ":root") ?? "";
const tokens: Record<string, string> = Object.fromEntries(
  [...rootBlock.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)].map((match) => [match[1], match[2].replace(/\s+/g, " ").trim()]),
);
const resolve = tokenResolver(tokensCss);
const token = (name: string): string => resolve(tokens[name]);

function luminance(hex: string): number {
  const channels = [1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r, g, b] = channels.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(a: string, b: string): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (light + 0.05) / (dark + 0.05);
}

describe("the palette contract", () => {
  it("is unchanged", () => {
    expect({
      bg: token("--bj-bg"),
      surface: token("--bj-surface"),
      raised: token("--bj-surface-raised"),
      border: token("--bj-border"),
      text: token("--bj-text"),
      muted: token("--bj-text-muted"),
      maroon: token("--bj-maroon"),
      maroonHover: token("--bj-maroon-hover"),
      gold: token("--bj-gold"),
      cyan: token("--bj-cyan"),
      cyanHover: token("--bj-cyan-hover"),
      emerald: token("--bj-emerald"),
      violet: token("--bj-violet"),
      danger: token("--bj-danger"),
      focus: token("--bj-focus-ring"),
    }).toEqual({
      bg: "#070607",
      surface: "#100d0f",
      raised: "#171216",
      border: "#34252b",
      text: "#f2eadf",
      muted: "#a99da0",
      maroon: "#641e2e",
      maroonHover: "#7a2839",
      gold: "#c7a15a",
      cyan: "#38d6d0",
      cyanHover: "#55dfda",
      emerald: "#3cb878",
      violet: "#9a7be8",
      danger: "#e25555",
      focus: "#38d6d0",
    });
  });
});

describe("what was added", () => {
  it("names the outline, the brand dot and the selection primitive", () => {
    expect(token("--bj-outline")).toBe("#7d6870");
    expect(token("--bj-brand-dot")).toBe("#e0414f");
    expect(token("--bj-selected-bg")).toBe(token("--bj-maroon"));
    expect(token("--bj-selected-bg-hover")).toBe(token("--bj-maroon-hover"));
    expect(token("--bj-selected-text")).toBe(token("--bj-text"));
    expect(token("--bj-hover-bg")).toBe(token("--bj-surface"));
    expect(token("--bj-hover-bg-raised")).toBe(token("--bj-surface-raised"));
  });

  it("sets the focus ring at 2px with a 2px offset", () => {
    expect(token("--bj-focus-ring-width")).toBe("2px");
    expect(token("--bj-focus-ring-offset")).toBe("2px");
    // rulesOf splits a selector list on commas, which :where(...) contains, so this reads the text.
    expect(tokensCss).toMatch(
      /:where\(button, a, input, select, textarea, summary, \[tabindex\]\):focus-visible\s*\{\s*outline: var\(--bj-focus-ring-width\) solid var\(--bj-focus-ring\);\s*outline-offset: var\(--bj-focus-ring-offset\);/,
    );
  });

  it("has a 4px spacing scale and the type scale of the design, the trail line at 12px", () => {
    expect([1, 2, 3, 4, 5, 6, 7].map((n) => token(`--bj-space-${n}`))).toEqual(["4px", "8px", "12px", "16px", "24px", "32px", "48px"]);
    expect({
      title: token("--bj-text-title"),
      h1: token("--bj-text-h1"),
      body: token("--bj-text-body"),
      control: token("--bj-text-control"),
      small: token("--bj-text-small"),
      badge: token("--bj-text-badge"),
      eyebrow: token("--bj-text-eyebrow"),
      trail: token("--bj-text-trail"),
      trailMono: token("--bj-text-trail-mono"),
    }).toEqual({
      title: "22px",
      h1: "34px",
      body: "15px",
      control: "14px",
      small: "13px",
      badge: "12px",
      eyebrow: "11px",
      trail: "12px",
      trailMono: "11px",
    });
  });

  it("bumps the trail line to 12.5px, in the same families, on a low-density screen", () => {
    const bump = blockOf(tokensCss, "@media (max-resolution: 1.49dppx)") ?? "";
    expect(bump).toMatch(/--bj-text-trail:\s*12\.5px/);
    expect(bump).not.toMatch(/font-family|--bj-font/);
  });

  it("puts the three self-hosted faces first, each with a system fallback", () => {
    expect(tokens["--bj-font-display"]).toMatch(/^"Newsreader", .*serif$/);
    expect(tokens["--bj-font-body"]).toMatch(/^"IBM Plex Sans", .*sans-serif$/);
    expect(tokens["--bj-font-mono"]).toMatch(/^"IBM Plex Mono", .*monospace$/);
  });

  it("declares the dark color scheme", () => {
    expect(rootBlock).toContain("color-scheme: dark;");
  });

  it("uses the brand dot for the wordmark's dot and nothing else (red still means failure)", () => {
    expect(appCss.match(/var\(--bj-brand-dot\)/g)).toHaveLength(1);
    expect(declarationsFor(appCss, ".bj-wordmark-dot").background).toBe("var(--bj-brand-dot)");
  });
});

describe("selection and buttons", () => {
  it("turns a chosen control into a maroon fill with ivory text and weight 600, and nothing else", () => {
    // The unconditional rules only: the forced-colors block (tested below) restates the fill.
    const selected = declarationsFor(withoutMedia(tokensCss), '.bj-selectable[aria-current="page"]');
    expect(selected).toEqual({
      background: "var(--bj-selected-bg)",
      color: "var(--bj-selected-text)",
      "font-weight": "600",
    });
    expect(selected).not.toHaveProperty("border");
    expect(selected).not.toHaveProperty("text-decoration");
    expect(selected).not.toHaveProperty("box-shadow");
  });

  it("applies it for every state the design names", () => {
    const rule = rulesOf(tokensCss).find((r) => r.selectors.includes('.bj-selectable[aria-current="page"]'));
    expect(rule?.selectors).toEqual([
      '.bj-selectable[aria-current="page"]',
      '.bj-selectable[aria-current="location"]',
      '.bj-selectable[aria-pressed="true"]',
      '.bj-selectable[aria-selected="true"]',
      '.bj-selectable[aria-checked="true"]',
    ]);
  });

  it("outlines a group of segments in the group outline, and a secondary button too", () => {
    expect(declarationsFor(tokensCss, ".bj-segmented").border).toBe("1px solid var(--bj-outline)");
    expect(declarationsFor(tokensCss, "button").border).toBe("1px solid var(--bj-outline)");
    expect(declarationsFor(tokensCss, "button").background).toBe("transparent");
    expect(declarationsFor(appCss, "a.bj-button-link").border).toBe("1px solid var(--bj-outline)");
  });

  it("fills only the primary: the cyan fill is on .bj-primary and its link twin, nowhere else", () => {
    const filled = [...rulesOf(tokensCss), ...rulesOf(appCss)]
      .filter((r) => /var\(--bj-cyan(-hover)?\)/.test(r.declarations.background ?? ""))
      .flatMap((r) => r.selectors);
    expect([...new Set(filled)].sort()).toEqual(
      ["a.bj-button-link-primary", "a.bj-button-link-primary:hover", "button.bj-primary", "button.bj-primary:hover"].sort(),
    );
  });

  it("paints no hover fill behind a flat text-style button, which the global button:hover fill would otherwise reach", () => {
    // Each of these is declared in app.css with no background and no border, and a class selector
    // (0,1,0) loses to the global `button:hover` (0,1,1), so tokens.css has to switch the fill off.
    const flat = [
      "button.bj-link-button",
      "button.bj-modal-close",
      "button.bj-score-disclosure-toggle",
      "button.bj-workauth-disclosure-toggle",
      "button.bj-gap-ghost",
      ".bj-chip-removable button",
    ];
    for (const selector of flat) {
      const own = declarationsFor(appCss, selector.replace(/^button\./, "."));
      expect(own.border, selector).toBe("none");
      expect(["none", "transparent"], selector).toContain(own.background);
      expect(declarationsFor(tokensCss, `${selector}:hover`).background, selector).toBe("transparent");
    }
    expect(declarationsFor(tokensCss, "button:hover").background).toBe("var(--bj-hover-bg)");
  });

  it("dims a busy control that keeps focus the way a disabled one is dimmed", () => {
    const rule = rulesOf(tokensCss).find((r) => r.selectors.includes('button[aria-disabled="true"]'));
    expect(rule?.selectors).toContain("button:disabled");
    expect(rule?.declarations.opacity).toBe("0.45");
  });
});

describe("forced colors", () => {
  // Windows High Contrast replaces author colors, and a maroon fill with no other cue (the design
  // forbids underlines and edge bars) would vanish: every tab would look the same as the chosen one.
  const selectedStates = ["page", "location"].map((v) => `[aria-current="${v}"]`).concat(["pressed", "selected", "checked"].map((v) => `[aria-${v}="true"]`));

  it("redraws a chosen item in the system selection colors, for every state the primitive reads", () => {
    const block = blockOf(tokensCss, "@media (forced-colors: active)") ?? "";
    for (const state of selectedStates) {
      for (const suffix of ["", ":hover"]) {
        expect(declarationsFor(block, `.bj-selectable${state}${suffix}`), state + suffix).toMatchObject({
          "forced-color-adjust": "none",
          background: "Highlight",
          color: "HighlightText",
        });
      }
    }
  });

  it("does the same for the chosen segment of a view toggle, and uses no author color for it", () => {
    const block = blockOf(appCss, "@media (forced-colors: active)") ?? "";
    for (const suffix of ["", ":hover"]) {
      expect(declarationsFor(block, `.bj-view-toggle button[aria-pressed="true"]${suffix}`)).toMatchObject({
        "forced-color-adjust": "none",
        background: "Highlight",
        color: "HighlightText",
      });
    }
    expect(block).not.toMatch(/#[0-9a-f]{3,6}|var\(/i);
  });
});

describe("motion", () => {
  it("collapses animation, transition and smooth scrolling for anyone who asks for less motion", () => {
    const block = blockOf(tokensCss, "@media (prefers-reduced-motion: reduce)") ?? "";
    expect(block).toMatch(/animation-duration:\s*0\.01ms !important/);
    expect(block).toMatch(/transition-duration:\s*0\.01ms !important/);
    expect(block).toMatch(/scroll-behavior:\s*auto !important/);
  });
});

// Every text and non-text pair this work introduced, with the ratio WCAG asks for: 4.5 for text, 3 for
// large text (24px and up) and for the lines and shapes a person has to see.
const PAIRS: [label: string, foreground: string, background: string, minimum: number][] = [
  ["selected item: ivory on maroon", "--bj-selected-text", "--bj-selected-bg", 4.5],
  ["selected item, hovered: ivory on maroon hover", "--bj-selected-text", "--bj-selected-bg-hover", 4.5],
  ["rail item at rest: muted on canvas", "--bj-text-muted", "--bj-bg", 4.5],
  ["rail item hovered: ivory on surface", "--bj-text", "--bj-hover-bg", 4.5],
  ["rail item hovered on a surface: ivory on raised", "--bj-text", "--bj-hover-bg-raised", 4.5],
  ["rail item hovered: muted on surface (email, notes)", "--bj-text-muted", "--bj-hover-bg", 4.5],
  ["phone tab at rest: muted on canvas", "--bj-text-muted", "--bj-bg", 4.5],
  ["quiet button at rest: muted on canvas", "--bj-text-muted", "--bj-bg", 4.5],
  ["quiet button on a surface: muted on surface", "--bj-text-muted", "--bj-surface", 4.5],
  ["More sheet: ivory on surface", "--bj-text", "--bj-surface", 4.5],
  ["More sheet: muted on surface", "--bj-text-muted", "--bj-surface", 4.5],
  ["More sheet link: cyan on surface", "--bj-cyan", "--bj-surface", 4.5],
  ["skip link: ivory on raised", "--bj-text", "--bj-surface-raised", 4.5],
  ["secondary button: ivory on canvas", "--bj-text", "--bj-bg", 4.5],
  ["primary button: dark teal on cyan", "#06211f", "--bj-cyan", 4.5],
  ["primary button, hovered: dark teal on cyan hover", "#06211f", "--bj-cyan-hover", 4.5],
  ["danger button: canvas on danger", "--bj-bg", "--bj-danger", 4.5],
  ["text link: cyan on canvas", "--bj-cyan", "--bj-bg", 4.5],
  ["wordmark (22px and up in places, 21px in the rail): ivory on canvas", "--bj-text", "--bj-bg", 4.5],
  ["focus ring against the canvas", "--bj-focus-ring", "--bj-bg", 3],
  ["focus ring against a surface", "--bj-focus-ring", "--bj-surface", 3],
  ["focus ring against a raised surface", "--bj-focus-ring", "--bj-surface-raised", 3],
  ["focus ring against the selected fill", "--bj-focus-ring", "--bj-selected-bg", 3],
  ["focus ring against the selected fill, hovered", "--bj-focus-ring", "--bj-selected-bg-hover", 3],
  ["group and secondary outline against the canvas", "--bj-outline", "--bj-bg", 3],
  ["group and secondary outline against a surface", "--bj-outline", "--bj-surface", 3],
  ["brand dot against the canvas", "--bj-brand-dot", "--bj-bg", 3],
];

describe("contrast", () => {
  it.each(PAIRS)("%s", (label, foreground, background, minimum) => {
    const value = (name: string): string => (name.startsWith("#") ? name : token(name));
    const ratio = contrast(value(foreground), value(background));
    expect(ratio, `${label}: ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(minimum);
  });
});
