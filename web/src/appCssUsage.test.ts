import { describe, expect, it } from "vitest";
import appCss from "./app.css?raw";

// A class rule in app.css that nothing draws is dead weight, and a dead `.bj-badge-cyan` is also a
// standing invitation to a second cyan fill. This reads every `.bj-` class the stylesheet names and
// looks for it in the source of the app (tests and test helpers do not count as a user).

const sources = import.meta.glob<string>(["./**/*.ts", "./**/*.tsx", "!./**/*.test.ts", "!./**/*.test.tsx", "!./testing/**"], {
  query: "?raw",
  import: "default",
  eager: true,
});
const appSource = Object.values(sources).join("\n");

const classes = new Set([...appCss.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/\.(bj-[A-Za-z0-9_-]+)/g)].map((match) => match[1]));

describe("app.css", () => {
  it("names at least the classes the shell is known to draw", () => {
    expect(classes.has("bj-rail")).toBe(true);
    expect(classes.size).toBeGreaterThan(20);
  });

  it("holds no class rule that no source file uses", () => {
    const unused = [...classes].filter((name) => !new RegExp(`(?<![A-Za-z0-9_-])${name}(?![A-Za-z0-9_-])`).test(appSource)).sort();
    expect(unused).toEqual([]);
  });
});
