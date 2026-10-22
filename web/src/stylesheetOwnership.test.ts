import { describe, expect, it } from "vitest";

// A rule for the same selector in two stylesheets is a cascade that depends on which file the browser
// happened to load last, and a page that imports one of them and not the other draws differently. Each
// selector has one home: this reads every stylesheet (the global ones and the per-feature ones under
// styles/) and fails on a selector that two files both define, in any media query. Selectors with no
// class, id or attribute in them (`html`, `body`, `*`) are resets and are left out.

const sheets = import.meta.glob<string>("./**/*.css", { query: "?raw", import: "default", eager: true });

// The rule preludes of a stylesheet, with the media or supports condition each sits in, one entry per
// comma-separated selector.
function selectors(css: string): string[] {
  const found: string[] = [];
  const walk = (text: string) => {
    let i = 0;
    while (i < text.length) {
      let open = i;
      while (open < text.length && !"{;}".includes(text[open])) open += 1;
      if (open >= text.length) return;
      if (text[open] !== "{") {
        i = open + 1;
        continue;
      }
      let depth = 1;
      let close = open + 1;
      while (close < text.length && depth > 0) {
        if (text[close] === "{") depth += 1;
        else if (text[close] === "}") depth -= 1;
        close += 1;
      }
      const prelude = text.slice(i, open).trim();
      const body = text.slice(open + 1, close - 1);
      if (prelude.startsWith("@")) {
        if (/^@(media|supports|container|layer)\b/.test(prelude)) walk(body);
      } else {
        for (const part of prelude.split(",")) found.push(part.replace(/\s+/g, " ").trim());
      }
      i = close;
    }
  };
  walk(css.replace(/\/\*[\s\S]*?\*\//g, ""));
  return found;
}

describe("the stylesheets", () => {
  it("read at least the global and the per-feature ones", () => {
    const names = Object.keys(sheets);
    expect(names).toContain("./app.css");
    expect(names).toContain("./tokens.css");
    expect(names.filter((name) => name.startsWith("./styles/")).length).toBeGreaterThan(10);
  });

  it("define no selector in more than one file", () => {
    const homes = new Map<string, Set<string>>();
    for (const [file, css] of Object.entries(sheets)) {
      for (const selector of selectors(css)) {
        if (!/[.#[]/.test(selector)) continue;
        if (!homes.has(selector)) homes.set(selector, new Set());
        homes.get(selector)?.add(file);
      }
    }
    const shared = [...homes].filter(([, files]) => files.size > 1).map(([selector, files]) => `${selector}  in ${[...files].sort().join(", ")}`);
    expect(shared).toEqual([]);
  });
});
