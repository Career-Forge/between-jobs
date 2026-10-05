import { describe, expect, it } from "vitest";

// Opening the Discover page must not run a search. A search is a full discovery -- provider
// calls, liveness probes, an LLM scoring call -- and each one spends a slot of the per-user
// "discover" rate limit (20 an hour). A search on mount would spend the budget on page visits
// alone (twice a visit under React StrictMode in development), and then the page's own
// button would answer "too often". There is no DOM test setup in this package, so this reads
// the page's source: every `useEffect` body (what runs on mount) must leave `search` alone.
import source from "./Discover.tsx?raw";

function effectBodies(code: string): string[] {
  const bodies: string[] = [];
  const effect = /useEffect\(\s*\(\)\s*=>\s*\{([\s\S]*?)\n\s*\},\s*\[/g;
  for (const match of code.matchAll(effect)) {
    bodies.push(match[1]);
  }
  return bodies;
}

describe("the Discover page on open", () => {
  const bodies = effectBodies(source);

  it("has the effect that loads the saved searches (so this check is not looking at nothing)", () => {
    expect(bodies.length).toBeGreaterThanOrEqual(1);
    expect(bodies.some((body) => body.includes("loadSavedSearches("))).toBe(true);
  });

  it("does not search from any effect", () => {
    for (const body of bodies) {
      expect(body).not.toMatch(/\bsearch\(/);
      expect(body).not.toMatch(/\brunSearch\(/);
      expect(body).not.toContain("/discover");
    }
  });

  it("starts idle, not loading, and runs a search only through runSearch", () => {
    expect(source).toMatch(/useState<State>\(\{ kind: "idle" \}\)/);
    const calls = source.match(/\bvoid search\(/g) ?? [];
    expect(calls).toHaveLength(1);
    const runSearch = source.match(/function runSearch\(\) \{([\s\S]*?)\n  \}/);
    expect(runSearch?.[1]).toContain("search(");
  });
});
