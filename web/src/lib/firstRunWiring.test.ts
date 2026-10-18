import { describe, expect, it } from "vitest";
import discover from "../pages/Discover.tsx?raw";
import today from "../pages/Today.tsx?raw";
import todayView from "../components/today/TodayView.tsx?raw";
import hook from "./useFirstRun.ts?raw";

// The pieces of the setup checklist that only exist as wiring: that Today shows it, that Discover
// leaves the "I have searched" mark only after a search that came back, for the signed-in person,
// and that the reads behind it are the four plain GETs and the hook keys everything by the person.
// A reverted line would leave every pure test green. (What the hook does with storage is run, not
// read, in useFirstRun.test.tsx; the keying of the connected component is run in
// components/FirstRunChecklist.test.tsx.)

describe("the first-run checklist's wiring", () => {
  it("is shown on Today, before the feed and the empty state", () => {
    expect(today).toContain("<FirstRunChecklist");
    expect(today).toContain("checklist={");
    expect(todayView).toContain("{props.checklist}");
    expect(todayView.indexOf("{props.checklist}")).toBeLessThan(todayView.indexOf("<TodayEmptyState />"));
    expect(todayView.indexOf("{props.checklist}")).toBeLessThan(todayView.indexOf('className="bj-today-list"'));
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

  it("is fed by Today's one read of the account, which only makes the four plain GETs", () => {
    expect(today).toContain("readAccount(apiFetch)");
    expect(today).toContain("firstRunFactsOf(account.reads)");
    // The hook asks the API for nothing: not through the client, and never for a search.
    expect(hook).not.toContain("apiFetch");
    expect(hook).not.toContain("/discover");
    expect(hook).toContain("useFirstRun(userId: string, facts: FirstRunFacts)");
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
    expect(hook).toContain("onFirstRunFactsLoaded(facts, browserStorage(), userId)");
    // The old shape: the hook assembling the derivation itself, with a flag it could get wrong.
    expect(hook).not.toContain("deriveFirstRun(");
    expect(hook).not.toMatch(/dismissed: (false|true)/);
  });
});
