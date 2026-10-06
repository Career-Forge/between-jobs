import { describe, expect, it } from "vitest";

// The server keeps a table of named company groups (search_aggregation.py `_COHORTS`). One of
// them, "dream", is the maintainer's own target list, not something to offer testers, and the
// web app has never shown it: the Companies field in Discover takes company names typed by the
// person, with a placeholder of two ordinary examples. This keeps it that way. If a screen ever
// wants to offer named groups, it should offer neutral ones on purpose, and this test is where
// that decision gets made.
//
// Every source file of the web app except the tests, read as text (the package has no DOM and
// no @types/node, so vite's glob, not node:fs).
const sources = import.meta.glob("../**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const NAMES = [/\bdream\b/i, /\bmaango\b/i];

describe("no personal company group is offered by the web app", () => {
  const files = Object.entries(sources).filter(([path]) => !/\.test\.tsx?$/.test(path));

  it("really scans the app's sources", () => {
    expect(files.length).toBeGreaterThan(100);
    expect(files.some(([path]) => path.endsWith("/pages/Discover.tsx"))).toBe(true);
  });

  it("names neither the maintainer's 'dream' list nor 'MAANGO' anywhere", () => {
    const found = files.flatMap(([path, text]) =>
      NAMES.filter((name) => name.test(text)).map((name) => `${path}: ${name}`),
    );
    expect(found).toEqual([]);
  });

  it("Discover's Companies field is free text with plain examples, not a list of groups", () => {
    const discover = files.find(([path]) => path.endsWith("/pages/Discover.tsx"))?.[1] ?? "";
    expect(discover).toContain('placeholder="e.g. Anthropic, Stripe"');
    expect(discover).not.toMatch(/<select[^>]*>[\s\S]*?cohort/i);
  });
});
