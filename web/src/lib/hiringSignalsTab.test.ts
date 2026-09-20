import { describe, expect, it } from "vitest";
import {
  AGGREGATOR_BADGE,
  AGGREGATOR_LEGEND,
  NO_POSTS_SEEN_NOTE,
  YOU_COM_NO_LINKEDIN_NOTE,
} from "./hiringSignals";
import {
  ALREADY_SAVED_REASON,
  HINT_DROP_LOCATION,
  HINT_SHORTER_ROLE,
  INITIAL_FORM,
  LOCALE_OPTIONS,
  LOCATION_TOO_LONG_MESSAGE,
  LOOSE_ROLE_NOTE,
  PROVIDER_RESULT_LIMIT,
  QUERY_REQUIRED_MESSAGE,
  QUERY_TOO_LONG_MESSAGE,
  SAVE_NEEDS_ROLE_REASON,
  TAB_DEFAULT_FRESHNESS,
  TAB_INTRO,
  TAB_LOCATION_HINT,
  TAB_NOT_FOUND_MESSAGE,
  TAB_NOTHING_SHOWN_NOTE,
  TAB_NO_POSTS_SEEN_NOTE,
  TAB_TRUNCATED_NOTE,
  TAB_YOU_COM_NO_LINKEDIN_NOTE,
  type TabForm,
  type TabSearchRequest,
  checkForm,
  codePointLength,
  collapseText,
  equivalenceKey,
  findEquivalentSearch,
  formFromRequest,
  isLooseRole,
  localeLabel,
  looseRoleNote,
  sameRequest,
  saveSearchGate,
  savedSearchBody,
  savedSearchLabel,
  searchRequestBody,
  tabCountsSummary,
  tabEmptyHints,
  tabEmptyNote,
  tabFailureMessage,
  tabRegistryMatchNote,
  tabResultHeadline,
  tabResultSource,
  tabRoleHint,
  tabTruncationNote,
  withoutSearchErrors,
} from "./hiringSignalsTab";
import type { SavedHiringSearch, TabCounts } from "./hiringSignalsTabTypes";

function makeForm(overrides: Partial<TabForm> = {}): TabForm {
  return { ...INITIAL_FORM, query: "software engineer", ...overrides };
}

function makeSaved(query: string, location: string | null = null, id = "s1"): SavedHiringSearch {
  return { id, query, location, created_at: "2026-09-18T12:00:00Z" };
}

function makeCounts(overrides: Partial<TabCounts> = {}): TabCounts {
  return {
    raw_hits: 0,
    rejected: 0,
    duplicates: 0,
    role_mismatch_hidden: 0,
    echoes_hidden: 0,
    job_seekers_hidden: 0,
    too_old_hidden: 0,
    shown: 0,
    ...overrides,
  };
}

describe("the form's starting point", () => {
  it("starts on the 3-day window with Auto wording and empty boxes", () => {
    expect(TAB_DEFAULT_FRESHNESS).toBe("3days");
    expect(INITIAL_FORM).toEqual({ query: "", location: "", freshness: "3days", locale: "auto" });
  });
});

describe("normalization, matching the server's", () => {
  it("collapses whitespace and trims", () => {
    expect(collapseText("  software \t engineer\n ")).toBe("software engineer");
    expect(collapseText("   ")).toBe("");
  });

  it("counts characters by code point, as the server does", () => {
    expect(codePointLength("abc")).toBe(3);
    expect(codePointLength("\u{1F600}")).toBe(1); // one emoji is two UTF-16 units
    expect("\u{1F600}".length).toBe(2);
  });

  it("gives two searches the same identity when they differ only by case and whitespace", () => {
    expect(equivalenceKey("Software  Engineer", "Bengaluru")).toBe(
      equivalenceKey("  software engineer ", " BENGALURU "),
    );
  });

  it("treats a blank location and no location as the same search, and any real location as different", () => {
    expect(equivalenceKey("data scientist", null)).toBe(equivalenceKey("data scientist", ""));
    expect(equivalenceKey("data scientist", null)).toBe(equivalenceKey("data scientist", "   "));
    expect(equivalenceKey("data scientist", null)).not.toBe(equivalenceKey("data scientist", "Pune"));
    expect(equivalenceKey("data scientist", "Pune")).not.toBe(
      equivalenceKey("data scientist", "Mumbai"),
    );
  });

  it("cannot confuse a role with a location: the two halves stay separate", () => {
    expect(equivalenceKey("a b", null)).not.toBe(equivalenceKey("a", "b"));
  });

  it("finds an equivalent saved search, or null", () => {
    const saved = [makeSaved("Software Engineer", "Bengaluru", "a"), makeSaved("designer", null, "b")];
    expect(findEquivalentSearch(saved, "software   engineer", "bengaluru")?.id).toBe("a");
    expect(findEquivalentSearch(saved, "DESIGNER", "")?.id).toBe("b");
    expect(findEquivalentSearch(saved, "software engineer", null)).toBeNull();
    expect(findEquivalentSearch([], "x", null)).toBeNull();
  });
});

describe("checkForm", () => {
  it("normalizes into the request, with a blank location as null and Auto as no locale", () => {
    const checked = checkForm(
      makeForm({ query: "  software   engineer ", location: "   ", freshness: "week" }),
    );
    expect(checked).toEqual({
      ok: true,
      request: { query: "software engineer", location: null, freshness: "week", locale: null },
    });
  });

  it("keeps a chosen wording and a real location", () => {
    const checked = checkForm(makeForm({ location: " Pune ", locale: "india", freshness: "day" }));
    expect(checked).toEqual({
      ok: true,
      request: { query: "software engineer", location: "Pune", freshness: "day", locale: "india" },
    });
  });

  it("refuses an empty role, whatever else is filled in", () => {
    for (const query of ["", "   ", "\n\t"]) {
      const checked = checkForm(makeForm({ query, location: "Pune" }));
      expect(checked).toEqual({
        ok: false,
        errors: { query: QUERY_REQUIRED_MESSAGE, location: null },
      });
    }
  });

  it("accepts exactly 200 characters of role and refuses 201, counted after collapsing", () => {
    expect(checkForm(makeForm({ query: "a".repeat(200) })).ok).toBe(true);
    expect(checkForm(makeForm({ query: "a".repeat(201) }))).toEqual({
      ok: false,
      errors: { query: QUERY_TOO_LONG_MESSAGE, location: null },
    });
    // 200 letters plus padding and doubled spaces is still 200 after trimming.
    expect(checkForm(makeForm({ query: `  ${"a".repeat(100)}   ${"b".repeat(99)}  ` })).ok).toBe(true);
    // 200 emoji are 400 UTF-16 units but 200 characters to the server.
    expect(checkForm(makeForm({ query: "\u{1F600}".repeat(200) })).ok).toBe(true);
    expect(checkForm(makeForm({ query: "\u{1F600}".repeat(201) })).ok).toBe(false);
  });

  it("accepts exactly 100 characters of location and refuses 101, with the error on the location", () => {
    expect(checkForm(makeForm({ location: "p".repeat(100) })).ok).toBe(true);
    expect(checkForm(makeForm({ location: "p".repeat(101) }))).toEqual({
      ok: false,
      errors: { query: null, location: LOCATION_TOO_LONG_MESSAGE },
    });
  });

  it("reports both errors at once, so each shows next to its own field", () => {
    expect(checkForm(makeForm({ query: "", location: "p".repeat(101) }))).toEqual({
      ok: false,
      errors: { query: QUERY_REQUIRED_MESSAGE, location: LOCATION_TOO_LONG_MESSAGE },
    });
  });
});

describe("requests", () => {
  const request: TabSearchRequest = {
    query: "software engineer",
    location: "Pune",
    freshness: "3days",
    locale: null,
  };

  it("turns a request back into the form that would produce it", () => {
    expect(formFromRequest(request)).toEqual({
      query: "software engineer",
      location: "Pune",
      freshness: "3days",
      locale: "auto",
    });
    expect(formFromRequest({ ...request, location: null, locale: "global" })).toEqual({
      query: "software engineer",
      location: "",
      freshness: "3days",
      locale: "global",
    });
    // A request survives the round trip through the form unchanged.
    for (const r of [request, { ...request, location: null, locale: "india" as const }]) {
      const back = checkForm(formFromRequest(r));
      expect(back).toEqual({ ok: true, request: r });
    }
  });

  it("compares requests field by field", () => {
    expect(sameRequest(request, { ...request })).toBe(true);
    expect(sameRequest(request, { ...request, query: "designer" })).toBe(false);
    expect(sameRequest(request, { ...request, location: null })).toBe(false);
    expect(sameRequest(request, { ...request, freshness: "week" })).toBe(false);
    expect(sameRequest(request, { ...request, locale: "india" })).toBe(false);
  });

  it("sends the role, location and window -- and NO locale key when the person left it on Auto", () => {
    const body = searchRequestBody(request);
    expect(body).toEqual({ query: "software engineer", location: "Pune", freshness: "3days" });
    expect(Object.keys(body).sort()).toEqual(["freshness", "location", "query"]);
    expect("locale" in body).toBe(false);
  });

  it("sends a chosen locale, and a null location explicitly", () => {
    expect(searchRequestBody({ ...request, location: null, locale: "india" })).toEqual({
      query: "software engineer",
      location: null,
      freshness: "3days",
      locale: "india",
    });
  });

  it("saves ONLY the role and location -- not the window, not the wording", () => {
    const body = savedSearchBody({ ...request, locale: "india", freshness: "week" });
    expect(body).toEqual({ query: "software engineer", location: "Pune" });
    expect(Object.keys(body).sort()).toEqual(["location", "query"]);
  });
});

describe("saveSearchGate", () => {
  it("is open for a real role that is not saved yet", () => {
    expect(saveSearchGate(makeForm(), [])).toEqual({ kind: "ok" });
    expect(saveSearchGate(makeForm(), [makeSaved("designer")])).toEqual({ kind: "ok" });
  });

  it("is blocked, with the reason in the save button's own terms, when there is no role", () => {
    for (const query of ["", "   ", "\n\t"]) {
      expect(saveSearchGate(makeForm({ query }), [])).toEqual({
        kind: "blocked",
        reason: SAVE_NEEDS_ROLE_REASON,
      });
    }
    expect(SAVE_NEEDS_ROLE_REASON).toBe("Enter a role to save this search.");
    // ... and not the search button's field error, which says something else.
    expect(SAVE_NEEDS_ROLE_REASON).not.toBe(QUERY_REQUIRED_MESSAGE);
  });

  it("is blocked when an equivalent search is already saved -- case and spacing do not matter", () => {
    const saved = [makeSaved("Software Engineer", "Bengaluru")];
    expect(
      saveSearchGate(makeForm({ query: "  software   ENGINEER ", location: "bengaluru" }), saved),
    ).toEqual({ kind: "blocked", reason: ALREADY_SAVED_REASON });
  });

  it("is NOT blocked by a same role with a different (or no) location", () => {
    const saved = [makeSaved("software engineer", "Bengaluru")];
    expect(saveSearchGate(makeForm({ location: "Pune" }), saved).kind).toBe("ok");
    expect(saveSearchGate(makeForm({ location: "" }), saved).kind).toBe("ok");
  });

  it("does not pre-empt the server's cap: 25 saved searches still leave it open", () => {
    const many = Array.from({ length: 25 }, (_, i) => makeSaved(`role ${i}`, null, `id${i}`));
    expect(saveSearchGate(makeForm({ query: "a new one" }), many)).toEqual({ kind: "ok" });
  });

  it("is blocked, with the field's own message, for an over-long role or location", () => {
    expect(saveSearchGate(makeForm({ query: "a".repeat(201) }), [])).toEqual({
      kind: "blocked",
      reason: QUERY_TOO_LONG_MESSAGE,
    });
    expect(saveSearchGate(makeForm({ location: "p".repeat(101) }), [])).toEqual({
      kind: "blocked",
      reason: LOCATION_TOO_LONG_MESSAGE,
    });
  });
});

describe("labels", () => {
  it("reads a saved search as the person typed it", () => {
    expect(savedSearchLabel({ query: "software engineer", location: null })).toBe("software engineer");
    expect(savedSearchLabel({ query: "software engineer", location: "Pune" })).toBe(
      "software engineer -- Pune",
    );
  });

  it("offers Auto, Global and India, in that order, and labels a locale", () => {
    expect(LOCALE_OPTIONS.map((o) => o.value)).toEqual(["auto", "global", "india"]);
    expect(LOCALE_OPTIONS[0].label).toContain("Auto");
    expect(localeLabel("india")).toBe("India");
    expect(localeLabel("global")).toBe("Global");
  });

  it("words the aggregator tag as a fact about the search, with a visible legend and no verdict", () => {
    expect(AGGREGATOR_BADGE).toBe("Posts often");
    expect(AGGREGATOR_LEGEND).toContain("many posts in this search");
    expect(AGGREGATOR_LEGEND).toContain("may be a job-alert or reposting account");
    // It never says the account IS an aggregator or bot, and never a confirmed one.
    expect(AGGREGATOR_LEGEND).not.toMatch(/\bis (a|an) (bot|aggregator|spam)/i);
    expect(AGGREGATOR_BADGE).not.toMatch(/spam|bot|fake|aggregator/i);
  });

  it("rewords a missing thing for the tab, and shows every other failure's own message verbatim", () => {
    expect(tabFailureMessage({ kind: "not_found", message: "Application 6b1f-... not found" })).toBe(
      TAB_NOT_FOUND_MESSAGE,
    );
    expect(TAB_NOT_FOUND_MESSAGE).not.toMatch(/application/i);
    // The 25-search cap reaches the person in the server's own words.
    const cap = "You can keep at most 25 saved searches. Delete one to save another.";
    expect(tabFailureMessage({ kind: "error", message: cap, retryable: false })).toBe(cap);
    expect(tabFailureMessage({ kind: "setup_required", message: "Connect a key" })).toBe(
      "Connect a key",
    );
  });

  it("clears only the results' card errors when a new search replaces the results", () => {
    expect(
      withoutSearchErrors({ "search:1": "a", "saved:9": "b", "search:2": "c" }),
    ).toEqual({ "saved:9": "b" });
    expect(withoutSearchErrors({})).toEqual({});
  });
});

describe("tabResultHeadline", () => {
  it("counts posts, not hiring posts", () => {
    expect(tabResultHeadline(1)).toBe("1 post found");
    expect(tabResultHeadline(7)).toBe("7 posts found");
  });

  it("says nothing about the role's market when nothing is shown", () => {
    expect(tabResultHeadline(0)).toBe("No posts to show for this search");
    expect(tabResultHeadline(0)).not.toMatch(/company|hiring/i);
  });

  it("owns up to posts this page could not display instead of saying there is nothing", () => {
    expect(tabResultHeadline(0, 2)).toBe("This page could not display the posts the search found");
    expect(tabResultHeadline(3, 2)).toBe("3 posts found");
  });
});

describe("tabResultSource", () => {
  it("joins what was searched, the provider and the wording the server used", () => {
    expect(tabResultSource("software engineer -- Pune -- last 3 days", "firecrawl", "india")).toBe(
      "software engineer -- Pune -- last 3 days -- via Firecrawl -- India wording",
    );
    expect(tabResultSource("designer -- last 7 days", "brave", "global")).toBe(
      "designer -- last 7 days -- via Brave Search -- Global wording",
    );
  });

  it("claims nothing for a provider or wording it does not know, and skips an empty label", () => {
    expect(tabResultSource("x", null, null)).toBe("x -- via your search provider");
    expect(tabResultSource("", "serper", null)).toBe("via Serper");
  });
});

describe("tabCountsSummary", () => {
  const base = { unreadable: 0, freshness: "3days" as const };

  it("is null when the counts are unknown -- better no sentence than a partial breakdown", () => {
    expect(tabCountsSummary({ ...base, counts: null, shown: 3 })).toBeNull();
  });

  it("says the search returned nothing when it did", () => {
    expect(tabCountsSummary({ ...base, counts: makeCounts(), shown: 0 })).toBe(
      "The search returned no results.",
    );
  });

  it("says all shown, or none shown, when nothing was hidden", () => {
    expect(tabCountsSummary({ ...base, counts: makeCounts({ raw_hits: 4, shown: 4 }), shown: 4 })).toBe(
      "The search returned 4 results; all shown.",
    );
    expect(tabCountsSummary({ ...base, counts: makeCounts({ raw_hits: 1, shown: 1 }), shown: 1 })).toBe(
      "The search returned 1 result; all shown.",
    );
  });

  it("words role_mismatch_hidden as posts without the ROLE's words -- never 'not about this company'", () => {
    const sentence = tabCountsSummary({
      ...base,
      counts: makeCounts({ raw_hits: 9, shown: 4, role_mismatch_hidden: 5 }),
      shown: 4,
    });
    expect(sentence).toBe(
      "The search returned 9 results; 4 shown. Not shown: 5 that did not contain all the words of your role.",
    );
    expect(sentence).not.toMatch(/company/i);
    expect(sentence).not.toMatch(/about this/i);
  });

  it("names every other hidden category, singular and plural, in a fixed order", () => {
    const counts = makeCounts({
      raw_hits: 13,
      shown: 2,
      role_mismatch_hidden: 1,
      duplicates: 2,
      echoes_hidden: 1,
      job_seekers_hidden: 3,
      too_old_hidden: 3,
      rejected: 1,
    });
    expect(tabCountsSummary({ ...base, counts, shown: 2 })).toBe(
      "The search returned 13 results; 2 shown. Not shown: 1 that did not contain all the words of your role, " +
        "2 duplicates, 1 automatic job-listing share that repeats a listing we already track, " +
        "3 possible job-seeker posts, 3 older than the last 3 days and 1 that was not a usable post link.",
    );
  });

  it("uses the plural forms where they apply", () => {
    const counts = makeCounts({ raw_hits: 9, shown: 1, duplicates: 1, echoes_hidden: 2, job_seekers_hidden: 1, rejected: 4 });
    expect(tabCountsSummary({ ...base, counts, shown: 1 })).toBe(
      "The search returned 9 results; 1 shown. Not shown: 1 duplicate, " +
        "2 automatic job-listing shares that repeat listings we already track, 1 possible job-seeker post " +
        "and 4 that were not usable post links.",
    );
  });

  it("names the window in the too-old reason", () => {
    const counts = makeCounts({ raw_hits: 5, shown: 3, too_old_hidden: 2 });
    for (const [freshness, label] of [
      ["day", "last 24 hours"],
      ["3days", "last 3 days"],
      ["week", "last 7 days"],
    ] as const) {
      expect(tabCountsSummary({ counts, shown: 3, unreadable: 0, freshness })).toContain(
        `2 older than the ${label}`,
      );
    }
  });

  it("owns up to entries this page could not display", () => {
    const counts = makeCounts({ raw_hits: 4, shown: 4 });
    expect(tabCountsSummary({ ...base, counts, shown: 3, unreadable: 1 })).toBe(
      "The search returned 4 results; 3 shown. Not shown: 1 that this page could not display.",
    );
  });

  it("uses the number of cards actually on screen, not the server's own shown", () => {
    const counts = makeCounts({ raw_hits: 4, shown: 4 });
    expect(tabCountsSummary({ ...base, counts, shown: 2, unreadable: 2 })).toContain("2 shown");
  });
});

describe("what an empty result says", () => {
  const empty = (overrides: Partial<TabCounts>, provider: "firecrawl" | "you_com" | "brave" | null = "firecrawl", unreadable = 0) =>
    tabEmptyNote({ provider, counts: makeCounts(overrides), unreadable });

  it("is honest that no posts at all says little about the role -- never that nobody is hiring", () => {
    expect(empty({})).toBe(TAB_NO_POSTS_SEEN_NOTE);
    expect(empty({ raw_hits: 3, rejected: 3 })).toBe(TAB_NO_POSTS_SEEN_NOTE);
    expect(TAB_NO_POSTS_SEEN_NOTE).toContain("says little about whether anyone is hiring for this role");
    expect(TAB_NO_POSTS_SEEN_NOTE).toContain("coverage depends on your search provider");
  });

  it("blames the index, not the role, for a provider known to hold no such posts", () => {
    expect(empty({}, "you_com")).toBe(TAB_YOU_COM_NO_LINKEDIN_NOTE);
    expect(TAB_YOU_COM_NO_LINKEDIN_NOTE).toContain("says nothing about this role");
    expect(TAB_YOU_COM_NO_LINKEDIN_NOTE).not.toMatch(/company/i);
    // Not P3's company-worded notes.
    expect(TAB_YOU_COM_NO_LINKEDIN_NOTE).not.toBe(YOU_COM_NO_LINKEDIN_NOTE);
    expect(TAB_NO_POSTS_SEEN_NOTE).not.toBe(NO_POSTS_SEEN_NOTE);
  });

  it("says the index returned nothing it could SHOW when posts were seen but all hidden", () => {
    expect(empty({ raw_hits: 5, role_mismatch_hidden: 5 })).toBe(TAB_NOTHING_SHOWN_NOTE);
    expect(empty({ raw_hits: 5, role_mismatch_hidden: 5 }, "you_com")).toBe(TAB_NOTHING_SHOWN_NOTE);
    expect(TAB_NOTHING_SHOWN_NOTE).toContain("does not mean nobody is hiring");
    expect(TAB_NOTHING_SHOWN_NOTE).not.toMatch(/company/i);
  });

  it("falls back to the neutral note when the counts are unknown", () => {
    expect(tabEmptyNote({ provider: "firecrawl", counts: null, unreadable: 0 })).toBe(
      TAB_NOTHING_SHOWN_NOTE,
    );
  });

  it("says nothing when entries came back that this page could not display", () => {
    expect(empty({}, "firecrawl", 2)).toBeNull();
    expect(empty({ raw_hits: 2, shown: 2 }, "you_com", 2)).toBeNull();
  });

  it("offers hints only where they are true of THIS search", () => {
    expect(tabEmptyHints({ counts: makeCounts({ raw_hits: 4, role_mismatch_hidden: 4 }), location: null })).toEqual([
      HINT_SHORTER_ROLE,
    ]);
    expect(tabEmptyHints({ counts: makeCounts(), location: "Pune" })).toEqual([HINT_DROP_LOCATION]);
    expect(
      tabEmptyHints({ counts: makeCounts({ raw_hits: 1, role_mismatch_hidden: 1 }), location: "Pune" }),
    ).toEqual([HINT_SHORTER_ROLE, HINT_DROP_LOCATION]);
    expect(tabEmptyHints({ counts: makeCounts(), location: null })).toEqual([]);
    expect(tabEmptyHints({ counts: null, location: null })).toEqual([]);
  });

  it("states the location honestly: it narrows the search, it does not filter each post", () => {
    expect(HINT_DROP_LOCATION).toContain("only narrows the search");
  });
});


describe("the role hint outside an empty result (YH-2)", () => {
  it("is offered when the role filter hid at least half of what came back", () => {
    expect(tabRoleHint(makeCounts({ raw_hits: 10, shown: 2, role_mismatch_hidden: 8 }))).toBe(
      HINT_SHORTER_ROLE,
    );
    expect(tabRoleHint(makeCounts({ raw_hits: 10, shown: 5, role_mismatch_hidden: 5 }))).toBe(
      HINT_SHORTER_ROLE,
    );
  });

  it("is not offered for a smaller share, or when nothing was hidden for the role", () => {
    expect(tabRoleHint(makeCounts({ raw_hits: 10, shown: 6, role_mismatch_hidden: 4 }))).toBeNull();
    expect(tabRoleHint(makeCounts({ raw_hits: 10, shown: 10 }))).toBeNull();
    expect(tabRoleHint(makeCounts({ raw_hits: 0 }))).toBeNull();
    expect(tabRoleHint(null)).toBeNull();
  });

  it("is about the ROLE, never a company", () => {
    expect(HINT_SHORTER_ROLE).toContain("words of your role");
    expect(HINT_SHORTER_ROLE).not.toMatch(/company/i);
  });
});

describe("a search that stopped at the provider's cap (YH-5)", () => {
  it("names the cap at 20 -- the number the server asks a provider for", () => {
    expect(PROVIDER_RESULT_LIMIT).toBe(20);
    expect(TAB_TRUNCATED_NOTE).toContain("20 results");
    expect(TAB_TRUNCATED_NOTE).toContain("may have stopped early");
  });

  it("is said when the search returned as many as the cap, or more", () => {
    expect(tabTruncationNote(makeCounts({ raw_hits: 20, shown: 17, role_mismatch_hidden: 3 }))).toBe(
      TAB_TRUNCATED_NOTE,
    );
    expect(tabTruncationNote(makeCounts({ raw_hits: 25 }))).toBe(TAB_TRUNCATED_NOTE);
  });

  it("is not said below the cap, for no counts, or for an empty answer", () => {
    expect(tabTruncationNote(makeCounts({ raw_hits: 19 }))).toBeNull();
    expect(tabTruncationNote(makeCounts({ raw_hits: 0 }))).toBeNull();
    expect(tabTruncationNote(null)).toBeNull();
  });

  it("does not claim more posts exist, only that the search may have stopped", () => {
    expect(TAB_TRUNCATED_NOTE).not.toMatch(/more posts exist|there are more/i);
  });
});

describe("a role too short to search safely (YH-3)", () => {
  it.each(["PM", "pm", " qa ", "ML", "UX", "hr", "C#", "C++", "5G"])(
    "%s is loose: one word of three characters or fewer",
    (role) => {
      expect(isLooseRole(role)).toBe(true);
      expect(looseRoleNote(role)).toBe(LOOSE_ROLE_NOTE);
    },
  );

  it.each([
    "data engineer",
    "qa engineer",
    "product manager",
    "SDE 2",
    "developer",
    "react",
    "node.js",
    ".NET",
    "engineer",
    "full-stack",
    "",
    "   ",
  ])("%s is not", (role) => {
    expect(isLooseRole(role)).toBe(false);
    expect(looseRoleNote(role)).toBeNull();
  });

  it("explains itself without blaming the person or the provider", () => {
    expect(LOOSE_ROLE_NOTE).toContain("very short");
    expect(LOOSE_ROLE_NOTE).toContain("time of day");
    expect(LOOSE_ROLE_NOTE).not.toMatch(/error|invalid|wrong/i);
  });
});

describe("the page's own words on the tab (FS-6, YH-4)", () => {
  it("never calls every result a hiring post, and never names the source site", () => {
    expect(TAB_INTRO).not.toMatch(/hiring posts for a role/i);
    expect(TAB_INTRO).toContain("Some are hiring calls and others are not.");
    for (const text of [TAB_INTRO, TAB_LOCATION_HINT, LOOSE_ROLE_NOTE, TAB_TRUNCATED_NOTE]) {
      expect(text).not.toMatch(/linkedin/i);
    }
  });

  it("says the location is searched only up to its first comma and is never checked in a post", () => {
    expect(TAB_LOCATION_HINT).toContain("only the part before the first comma is searched");
    expect(TAB_LOCATION_HINT).toContain("no post is checked against it");
  });

  it("words a registry match on the tab about the company that SHARED the listing", () => {
    expect(tabRegistryMatchNote("unmatched")).toBe(
      "We track the company that shared this, but not this listing yet.",
    );
    expect(tabRegistryMatchNote("possible")).toBe("May match a job listing we already track.");
    expect(tabRegistryMatchNote("matched")).toBe("Matches a job listing we already track.");
    expect(tabRegistryMatchNote(null)).toBeNull();
    expect(tabRegistryMatchNote("unmatched")).not.toContain("this company");
  });
});
