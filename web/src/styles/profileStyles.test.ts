import { describe, expect, it } from "vitest";
import { PHONE_QUERY } from "../lib/useMediaQuery";
import appCss from "../app.css?raw";
import tokensCss from "../tokens.css?raw";
import { blockOf, declarationsFor, rulesOf, withoutMedia } from "../testing/css";
import paperCss from "./paper.css?raw";
import paperDocCss from "./paper-doc.css?raw";
import profileCss from "./profile.css?raw";
import studioCss from "./profile-studio.css?raw";

// profile.css and profile-studio.css are read as text (there is no layout engine in the test run). What they
// pin is what the Profile page rests on: tokens only, no cyan fill, the phone's 44px targets, user text that
// wraps inside its column, and that every class the components use has a rule somewhere.

const SHEETS: Record<string, string> = { "profile.css": profileCss, "profile-studio.css": studioCss };

const sources = import.meta.glob(
  [
    "../components/profile/*.tsx",
    "../components/{SectionedProfile,EntryEditModal,HeaderComposer,SectionOrderEditor,ShapeSettingsPanel,RadioSegments,PersonalDetailsCardView,AccountCardView,ResumeImportView}.tsx",
    "../pages/Profile.tsx",
    "!../components/profile/*.test.tsx",
  ],
  { query: "?raw", import: "default", eager: true },
) as Record<string, string>;

const phone = (css: string) => blockOf(css, "@media (max-width: 959px)") ?? "";

describe.each(Object.entries(SHEETS))("%s", (_name, css) => {
  const code = css.replace(/\/\*[\s\S]*?\*\//g, "");

  it("was loaded as text", () => {
    expect(css.length).toBeGreaterThan(1000);
  });

  it("defines no color: no hex value, no rgb or hsl, only tokens", () => {
    expect(code).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(code).not.toMatch(/\b(?:rgb|rgba|hsl|hsla)\(/);
  });

  it("only uses tokens that tokens.css defines", () => {
    const defined = new Set([...tokensCss.matchAll(/(--[\w-]+)\s*:/g)].map((match) => match[1]));
    const used = new Set([...css.matchAll(/var\((--[\w-]+)/g)].map((match) => match[1]));
    expect([...used].filter((name) => !defined.has(name))).toEqual([]);
  });

  it("never fills anything cyan: the next-step band's button is the page's one cyan fill", () => {
    for (const rule of rulesOf(css)) {
      for (const property of ["background", "background-color"]) {
        expect(rule.declarations[property] ?? "", rule.selectors.join(", ")).not.toContain("--bj-cyan");
      }
    }
  });

  it("never removes a focus ring or forces a style", () => {
    expect(code).not.toContain("!important");
    for (const rule of rulesOf(css)) {
      if (rule.declarations.outline === "none") expect(rule.selectors.every((selector) => selector.endsWith(":focus")), rule.selectors.join(", ")).toBe(true);
    }
  });

  it("uses only the shell's phone breakpoint, and one narrower step for a long field row", () => {
    const queries = [...css.matchAll(/@media ([^{]+)\{/g)].map((match) => match[1].trim());
    for (const query of queries) expect([PHONE_QUERY, "(max-width: 560px)"]).toContain(query);
  });

  it("draws red for failure and destructive actions only", () => {
    const reds = rulesOf(css).filter((rule) => Object.values(rule.declarations).some((value) => value.includes("--bj-danger")));
    const selectors = reds.flatMap((rule) => rule.selectors).sort();
    expect(selectors).toEqual(css === profileCss ? [".bj-account-delete", ".bj-link-button-danger"] : []);
  });

  it("uses no inline-style escape hatches: no data: URI, no url() at all", () => {
    expect(code).not.toMatch(/url\(/);
  });
});

describe("profile.css", () => {
  const base = withoutMedia(profileCss);
  const phoneBlock = phone(profileCss);

  it("lets user text wrap inside its column instead of widening the page", () => {
    for (const selector of [".bj-prof-block", ".bj-profile-headline", ".bj-chip", ".bj-simple-body", ".bj-project-body", ".bj-language-chip", ".bj-metric-pill", ".bj-import-text pre", ".bj-timeline-title", ".bj-citation-body"]) {
      const own = declarationsFor(base, selector);
      expect(own["overflow-wrap"] ?? own["white-space"], selector).toBeDefined();
    }
    // The title and company of an experience entry sit in a flex item beside the dates, and the author line of a
    // publication in a grid cell: each may shrink below its longest unbroken word (an address, a URL), or the whole
    // page scrolls sideways on a phone.
    expect(declarationsFor(base, ".bj-timeline-title")["min-width"]).toBe("0");
    expect(declarationsFor(base, ".bj-citation-body")["min-width"]).toBe("0");
    expect(declarationsFor(base, ".bj-profile")["min-width"]).toBe("0");
    expect(declarationsFor(base, ".bj-profile-sections")["min-width"]).toBe("0");
  });

  it("scrolls the raw JSON inside itself", () => {
    const json = declarationsFor(base, ".bj-json-view");
    expect(json["overflow-x"]).toBe("auto");
    expect(json["max-width"]).toBe("100%");
  });

  it("makes every control of the page at least 44px tall on a phone", () => {
    const flat = phoneBlock.replace(/\s+/g, " ");
    for (const selector of [".bj-profile button", ".bj-profile select", '.bj-profile input[type="text"]', ".bj-profile .bj-button-link"]) {
      expect(flat, selector).toContain(selector);
    }
    expect(declarationsFor(phoneBlock, ".bj-profile .bj-link-button")["min-height"]).toBe("44px");
    expect(declarationsFor(phoneBlock, ".bj-profile .bj-view-toggle button")["min-height"]).toBe("44px");
    expect(declarationsFor(phoneBlock, ".bj-profile .bj-import-check")["min-height"]).toBe("44px");
  });

  it("makes the page's link-style buttons and anchor links 44 wide as well as tall on a phone", () => {
    for (const selector of [".bj-profile .bj-link-button", ".bj-profile .bj-anchor-nav a"]) {
      const own = declarationsFor(phoneBlock, selector);
      expect([own["min-width"], own["min-height"]], selector).toEqual(["44px", "44px"]);
    }
  });

  it("makes a link-style button at least a 24px target on a wide screen too (WCAG 2.5.8)", () => {
    const own = declarationsFor(base, ".bj-profile .bj-link-button");
    expect([own["min-width"], own["min-height"]]).toEqual(["24px", "24px"]);
  });

  it("draws the dashed add-a-skill box with the outline token, which is 3:1 against the page", () => {
    expect(declarationsFor(base, ".bj-inline-chip-input").border).toContain("var(--bj-outline)");
  });

  it("underlines a link inside a sentence", () => {
    expect(declarationsFor(base, ".bj-prof-block p a")["text-decoration"]).toBe("underline");
  });

  it("marks the part of the file a value came from with a tint AND an underline, so it shows without color", () => {
    const mark = declarationsFor(base, ".bj-import-mark");
    expect(mark["border-bottom"]).toContain("2px solid");
    expect(mark.background).toContain("color-mix(");
  });
});

describe("profile-studio.css", () => {
  const base = withoutMedia(studioCss);
  const phoneBlock = phone(studioCss);

  it("lets the rows wrap and their text shrink, so a long label never widens the page", () => {
    expect(declarationsFor(base, ".bj-header-composer-row")["flex-wrap"]).toBe("wrap");
    expect(declarationsFor(base, ".bj-header-composer-row")["min-width"]).toBe("0");
    expect(declarationsFor(base, ".bj-header-composer-field")["overflow-wrap"]).toBe("anywhere");
    expect(declarationsFor(base, ".bj-contact-chip")["overflow-wrap"]).toBe("anywhere");
  });

  it("makes the move buttons, presets, radio segments and dialog controls 44px on a phone", () => {
    const flat = phoneBlock.replace(/\s+/g, " ");
    for (const selector of [".bj-row-moves button", ".bj-shape-preset", ".bj-radio-segments > button", ".bj-modal-footer button", ".bj-entry-modal select"]) {
      expect(flat, selector).toContain(selector);
    }
    expect(declarationsFor(phoneBlock, ".bj-row-moves button")["min-height"]).toBe("44px");
    expect(declarationsFor(phoneBlock, ".bj-row-moves button")["min-width"]).toBe("44px");
  });

  // The dialog is portaled into the body, outside .bj-profile, so the page's phone rules never reach it.
  it("gives the dialog's small controls 44 by 44 under the dialog itself, not under the page", () => {
    for (const selector of [
      ".bj-entry-modal .bj-modal-close",
      ".bj-entry-modal .bj-chip-removable button",
      ".bj-entry-modal .bj-bullets-editor > button",
      ".bj-entry-modal .bj-bullet-row-actions button",
    ]) {
      const own = declarationsFor(phoneBlock, selector);
      expect([own["min-width"], own["min-height"]], selector).toEqual(["44px", "44px"]);
    }
  });

  it("dims a hidden section's name and nothing else, since the words that say it is hidden must stay at 4.5:1", () => {
    expect(declarationsFor(base, ".bj-section-hidden")).toEqual({});
    expect(declarationsFor(base, ".bj-section-hidden .bj-header-composer-field").opacity).toBe("0.7");
  });

  it("does not reach outside its own components: no rule here styles every button on a phone", () => {
    for (const rule of rulesOf(phoneBlock)) {
      for (const selector of rule.selectors) expect(selector, selector).not.toMatch(/^(\.bj-actions|button|input|select)\b/);
    }
  });

  it("draws the entry dialog as a full-width sheet on a phone", () => {
    expect(declarationsFor(phoneBlock, ".bj-modal-backdrop")["align-items"]).toBe("flex-end");
    expect(declarationsFor(phoneBlock, ".bj-entry-modal")["max-width"]).toBe("none");
  });

  it("outlines the dialog's Save instead of filling it", () => {
    const save = declarationsFor(base, "button.bj-dialog-confirm");
    expect(save["border-color"]).toBe("var(--bj-text)");
    expect(save.background).toBeUndefined();
  });

  it("collapses an empty live region, so 'Saving...' appearing does not move what is below it", () => {
    expect(declarationsFor(base, ".bj-studio-saving:empty").display).toBe("none");
  });
});

describe("every class the page's components use has a rule", () => {
  const defined = new Set(
    [appCss, tokensCss, paperCss, paperDocCss, profileCss, studioCss].flatMap((css) => rulesOf(css).flatMap((rule) => rule.selectors.flatMap((selector) => selector.match(/\.bj-[a-z0-9-]+/g) ?? []))),
  );

  // The classes written as string literals in a className, in a ternary or not.
  function classesIn(source: string): string[] {
    const found: string[] = [];
    for (const match of source.matchAll(/className=(?:"([^"]+)"|\{([^}]*)\})/g)) {
      const literals = match[1] !== undefined ? [match[1]] : [...match[2].matchAll(/"([^"]*)"/g)].map((literal) => literal[1]);
      for (const literal of literals) found.push(...literal.split(/\s+/).filter((name) => name.startsWith("bj-")));
    }
    return found;
  }

  it("covers the components of the Profile page", () => {
    expect(Object.keys(sources).length).toBeGreaterThanOrEqual(10);
  });

  it.each(Object.entries(sources))("%s", (_path, source) => {
    const missing = [...new Set(classesIn(source))].filter((name) => !defined.has(`.${name}`));
    expect(missing).toEqual([]);
  });
});
