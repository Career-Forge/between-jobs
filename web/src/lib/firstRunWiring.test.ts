import { describe, expect, it } from "vitest";
import discover from "../pages/Discover.tsx?raw";
import today from "../pages/Today.tsx?raw";
import hook from "./useFirstRun.ts?raw";

// The pieces of the first-run checklist that only exist as wiring in components the package
// cannot render (there is no DOM environment): that Today shows it, that Discover leaves the
// "I have searched" mark only after a search that came back, for the signed-in person, and
// that the checklist's own hook asks for nothing a search would spend and keys everything by
// the person. A reverted line would leave every pure test green. (What the hook does with
// storage and the API is run, not read, in useFirstRun.test.tsx; the keying of the connected
// component is run in components/FirstRunChecklist.test.tsx.)

describe("the first-run checklist's wiring", () => {
  it("is shown at the top of Today", () => {
    expect(today).toContain("<FirstRunChecklist />");
    expect(today.indexOf("<FirstRunChecklist />")).toBeLessThan(today.indexOf("<TodayEmptyState />"));
  });

  it("is marked by Discover only after a search that returned, never on a failed one", () => {
    const search = discover.slice(discover.indexOf("const search = useCallback"), discover.indexOf("const loadSavedSearches"));
    const fetched = search.indexOf("apiFetch<DiscoverResponse>");
    const mark = search.indexOf("markSearched(");
    const failure = search.indexOf("catch (e)");
    expect(fetched).toBeGreaterThan(-1);
    expect(mark).toBeGreaterThan(fetched);
    expect(mark).toBeLessThan(failure);
    expect((discover.match(/markSearched\(/g) ?? []).length).toBe(1);
  });

  it("is marked by Discover for the signed-in person, and not at all when nobody is signed in", () => {
    // The key is the person's id, taken from the session, and the mark sits behind the
    // check that there is one: a literal or another variable here is a mark nothing reads.
    expect(discover).toContain("const userId = session?.user.id ?? null;");
    expect(discover).toMatch(/if \(userId !== null\) markSearched\(browserStorage\(\), userId\);/);
  });

  it("asks for its data through the checklist's loader, which only makes the four plain GETs", () => {
    expect(hook).toContain("loadFirstRunFacts(apiFetch)");
    expect(hook).not.toContain("/discover");
  });

  it("hands the person's id to every call that reads or writes storage, and never a literal", () => {
    expect(hook).toContain("initialFirstRunState(browserStorage(), userId)");
    expect(hook).toContain("onFirstRunFactsLoaded(facts, browserStorage(), userId)");
    expect(hook).toContain("onFirstRunDismissed(state, browserStorage(), userId)");
    // Every reach for storage is followed by the id: none by a string, none by nothing.
    const reaches = hook.match(/browserStorage\(\)/g) ?? [];
    const keyed = hook.match(/browserStorage\(\), userId\)/g) ?? [];
    expect(reaches.length).toBe(3);
    expect(keyed.length).toBe(reaches.length);
    expect(hook).not.toMatch(/browserStorage\(\), ["'`]/);
  });

  it("shows what the machine says: the view comes from the state, with no flag of its own", () => {
    expect(hook).toContain("viewOfFirstRun(state)");
    expect(hook).toContain("shouldLoadFirstRun(state)");
    // The old shape: the hook assembling the derivation itself, with a flag it could get wrong.
    expect(hook).not.toContain("deriveFirstRun(");
    expect(hook).not.toMatch(/dismissed: (false|true)/);
  });
});
