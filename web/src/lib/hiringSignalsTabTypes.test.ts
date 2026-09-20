import { describe, expect, it } from "vitest";
import {
  TAB_COUNT_KEYS,
  type TabCounts,
  countsAddUp,
  isLocale,
  parseSavedHiringSearch,
  parseSavedSearchesResponse,
  parseTabCounts,
  parseTabSearchResponse,
  parseTabSignal,
  toLocaleChoice,
} from "./hiringSignalsTabTypes";

// Synthetic ids, names and urls throughout -- nothing here came from a real
// capture, and the linkedin.com strings are only ever parsed, never fetched.
const ID = "7000000000000000001";
const ID_2 = "7000000000000000002";
const ID_3 = "7000000000000000003";

function rawSignal(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    activity_id: ID,
    post_url: `https://www.linkedin.com/posts/example-${ID}`,
    embed_url: `https://www.linkedin.com/embed/feed/update/urn:li:activity:${ID}`,
    author_name: "Jane Example",
    posted_at: "2026-09-17T10:00:00Z",
    age_hint: "2 days ago",
    species: "hiring_drive",
    comment_count: 12,
    registry_match: "unmatched",
    aggregator: false,
    saved: false,
    ...overrides,
  };
}

// raw_hits = shown + rejected + duplicates + role_mismatch + echoes + job_seekers + too_old
const RAW_COUNTS = {
  raw_hits: 12,
  rejected: 1,
  duplicates: 2,
  role_mismatch_hidden: 3,
  echoes_hidden: 1,
  job_seekers_hidden: 1,
  too_old_hidden: 2,
  shown: 2,
};

function rawResponse(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    provider: "firecrawl",
    cached: false,
    freshness: "3days",
    locale: "india",
    query_label: "software engineer -- Bengaluru -- last 3 days",
    signals: [rawSignal(), rawSignal({ activity_id: ID_2, species: "unclassified" })],
    counts: RAW_COUNTS,
    ...overrides,
  };
}

// The contract's TabSignal, field for field.
const SIGNAL_KEYS = [
  "activity_id",
  "post_url",
  "embed_url",
  "author_name",
  "posted_at",
  "age_hint",
  "species",
  "comment_count",
  "registry_match",
  "aggregator",
  "saved",
].sort();

describe("parseTabSignal", () => {
  it("parses a well-formed signal into exactly the contract's shape", () => {
    expect(parseTabSignal(rawSignal())).toEqual({
      activity_id: ID,
      post_url: `https://www.linkedin.com/posts/example-${ID}`,
      embed_url: `https://www.linkedin.com/embed/feed/update/urn:li:activity:${ID}`,
      author_name: "Jane Example",
      posted_at: "2026-09-17T10:00:00Z",
      age_hint: "2 days ago",
      species: "hiring_drive",
      comment_count: 12,
      registry_match: "unmatched",
      aggregator: false,
      saved: false,
    });
  });

  it("carries only contract fields: a leaked title, snippet, handle or role tag is dropped on the floor", () => {
    const leaky = rawSignal({
      title: "We are hiring engineers -- Jane Example | LinkedIn",
      snippet: "3 days ago · Come and join our team, DM me...",
      text: "the full body of somebody's post",
      author_handle: "jane-example-123",
      author_headline: "Head of Talent",
      role_match: true,
      description: "a description",
      raw: { anything: 1 },
    });
    const parsed = parseTabSignal(leaky);
    expect(Object.keys(parsed ?? {}).sort()).toEqual(SIGNAL_KEYS);
    const serialized = JSON.stringify(parsed);
    for (const secret of ["We are hiring", "Come and join", "somebody's post", "jane-example-123", "Head of Talent"]) {
      expect(serialized).not.toContain(secret);
    }
    // role_match belongs to the per-application panel; the tab has no such field.
    expect(parsed).not.toHaveProperty("role_match");
  });

  it.each([
    ["a missing id", { activity_id: undefined }],
    ["a numeric id", { activity_id: 7000000000000000001 }],
    ["a leading zero", { activity_id: "0700000000000000001" }],
    ["a non-digit id", { activity_id: "70000abc" }],
    ["a unicode-digit id", { activity_id: "٧٠٠٠٠" }],
    ["an over-long id", { activity_id: "7".repeat(26) }],
    ["an empty id", { activity_id: "" }],
  ])("drops a signal with %s (it has no embed key and cannot be saved)", (_name, override) => {
    expect(parseTabSignal(rawSignal(override))).toBeNull();
  });

  it.each([null, undefined, "x", 5, [], true])("returns null for a non-object: %j", (bad) => {
    expect(parseTabSignal(bad)).toBeNull();
  });

  it("turns an unknown species into 'unclassified' -- the honest neutral tag, never a guess", () => {
    for (const species of ["job_seeker", "aggregator_source", "HIRING_DRIVE", 4, null, undefined]) {
      expect(parseTabSignal(rawSignal({ species }))?.species).toBe("unclassified");
    }
    for (const species of ["unclassified", "ats_echo", "referral_offer", "hiring_drive"]) {
      expect(parseTabSignal(rawSignal({ species }))?.species).toBe(species);
    }
  });

  it("keeps aggregator three-state: true, false, or null when it cannot tell", () => {
    expect(parseTabSignal(rawSignal({ aggregator: true }))?.aggregator).toBe(true);
    expect(parseTabSignal(rawSignal({ aggregator: false }))?.aggregator).toBe(false);
    // Anything that is not a boolean is "could not tell", never coerced to false.
    for (const aggregator of [null, undefined, "true", 1, 0, {}]) {
      expect(parseTabSignal(rawSignal({ aggregator }))?.aggregator).toBeNull();
    }
  });

  it("degrades an unreadable field to null instead of failing the signal", () => {
    const parsed = parseTabSignal(
      rawSignal({
        author_name: 42,
        posted_at: "yesterday-ish",
        age_hint: {},
        comment_count: -3,
        registry_match: "definitely",
        saved: "yes",
      }),
    );
    expect(parsed).toMatchObject({
      activity_id: ID,
      author_name: null,
      posted_at: null,
      age_hint: null,
      comment_count: null,
      registry_match: null,
      saved: false,
    });
  });

  it("cleans display text a stranger wrote: bidi overrides and zero-width characters are stripped", () => {
    const parsed = parseTabSignal(rawSignal({ author_name: "Jane‮ Example​" }));
    expect(parsed?.author_name).toBe("Jane Example");
  });
});

describe("parseTabCounts", () => {
  it("parses a set that adds up", () => {
    expect(parseTabCounts(RAW_COUNTS)).toEqual(RAW_COUNTS);
  });

  it("has exactly the tab's eight keys -- role_mismatch_hidden, not the per-application off_topic_hidden", () => {
    expect([...TAB_COUNT_KEYS].sort()).toEqual(
      [
        "raw_hits",
        "rejected",
        "duplicates",
        "role_mismatch_hidden",
        "echoes_hidden",
        "job_seekers_hidden",
        "too_old_hidden",
        "shown",
      ].sort(),
    );
    const perApplicationShape = { ...RAW_COUNTS, off_topic_hidden: 3 } as Record<string, number>;
    delete perApplicationShape.role_mismatch_hidden;
    expect(parseTabCounts(perApplicationShape)).toBeNull();
  });

  it("is all-or-nothing: one missing or unreadable count makes them ALL unknown", () => {
    for (const key of TAB_COUNT_KEYS) {
      const missing: Record<string, unknown> = { ...RAW_COUNTS };
      delete missing[key];
      expect(parseTabCounts(missing)).toBeNull();
      for (const bad of [-1, 1.5, "3", null, Number.NaN]) {
        expect(parseTabCounts({ ...RAW_COUNTS, [key]: bad })).toBeNull();
      }
    }
    for (const bad of [null, undefined, "x", 3, [], [1, 2]]) {
      expect(parseTabCounts(bad)).toBeNull();
    }
  });

  it("is unknown when the breakdown does not add up to its own total (the contract's invariant)", () => {
    expect(parseTabCounts({ ...RAW_COUNTS, raw_hits: RAW_COUNTS.raw_hits + 1 })).toBeNull();
    expect(parseTabCounts({ ...RAW_COUNTS, raw_hits: RAW_COUNTS.raw_hits - 1 })).toBeNull();
    // Every hidden category counts toward the total: dropping any one of them
    // from the sum breaks it.
    for (const key of TAB_COUNT_KEYS) {
      if (key === "raw_hits") continue;
      expect(parseTabCounts({ ...RAW_COUNTS, [key]: RAW_COUNTS[key] + 1 })).toBeNull();
    }
  });

  it("accepts the all-zero and the all-shown cases", () => {
    const zero = Object.fromEntries(TAB_COUNT_KEYS.map((key) => [key, 0]));
    expect(parseTabCounts(zero)).toEqual(zero);
    expect(parseTabCounts({ ...zero, raw_hits: 4, shown: 4 })).toEqual({
      ...zero,
      raw_hits: 4,
      shown: 4,
    });
  });

  it("countsAddUp pins which sums count", () => {
    const counts = RAW_COUNTS as TabCounts;
    expect(countsAddUp(counts)).toBe(true);
    expect(countsAddUp({ ...counts, shown: counts.shown + 1 })).toBe(false);
  });
});

describe("parseTabSearchResponse", () => {
  it("parses a well-formed response into the contract's shape", () => {
    const outcome = parseTabSearchResponse(rawResponse(), "week");
    expect(outcome).not.toBeNull();
    expect(outcome?.unreadable).toBe(0);
    const { response } = outcome!;
    expect(response.provider).toBe("firecrawl");
    expect(response.cached).toBe(false);
    expect(response.freshness).toBe("3days");
    expect(response.locale).toBe("india");
    expect(response.query_label).toBe("software engineer -- Bengaluru -- last 3 days");
    expect(response.counts).toEqual(RAW_COUNTS);
    expect(response.signals).toHaveLength(2);
    expect(response.signals.map((s) => s.activity_id)).toEqual([ID, ID_2]);
  });

  it("keeps the server's own order (it ranks: non-aggregator accounts first, then freshest)", () => {
    const outcome = parseTabSearchResponse(
      rawResponse({
        signals: [
          rawSignal({ activity_id: ID_3, aggregator: false }),
          rawSignal({ activity_id: ID, aggregator: true }),
          rawSignal({ activity_id: ID_2, aggregator: null }),
        ],
      }),
      "week",
    );
    expect(outcome?.response.signals.map((s) => s.activity_id)).toEqual([ID_3, ID, ID_2]);
  });

  it("fails the whole response only when it is not an object with a signals list", () => {
    for (const bad of [null, undefined, "x", 5, [], {}, { signals: "nope" }, { signals: {} }]) {
      expect(parseTabSearchResponse(bad, "week")).toBeNull();
    }
    expect(parseTabSearchResponse({ signals: [] }, "week")).not.toBeNull();
  });

  it("drops and COUNTS a signal with a bad id, and a repeat of an id already shown", () => {
    const outcome = parseTabSearchResponse(
      rawResponse({
        signals: [
          rawSignal(),
          rawSignal({ activity_id: "not-an-id" }),
          rawSignal(), // a repeat of ID: it would also collide as a React key
          "garbage",
          rawSignal({ activity_id: ID_2 }),
        ],
      }),
      "week",
    );
    expect(outcome?.response.signals.map((s) => s.activity_id)).toEqual([ID, ID_2]);
    expect(outcome?.unreadable).toBe(3);
  });

  it("falls back to the requested window when the server's echo is missing or unknown", () => {
    for (const freshness of [undefined, "month", 3, null]) {
      expect(parseTabSearchResponse(rawResponse({ freshness }), "day")?.response.freshness).toBe(
        "day",
      );
    }
    expect(parseTabSearchResponse(rawResponse({ freshness: "week" }), "day")?.response.freshness).toBe(
      "week",
    );
  });

  it("reads the locale echo, and claims nothing for one it does not recognise", () => {
    expect(parseTabSearchResponse(rawResponse({ locale: "global" }), "week")?.response.locale).toBe(
      "global",
    );
    for (const locale of [undefined, null, "us", "INDIA", 1]) {
      expect(parseTabSearchResponse(rawResponse({ locale }), "week")?.response.locale).toBeNull();
    }
  });

  it("names a provider it knows and says nothing for one it does not", () => {
    for (const provider of ["you_com", "brave", "serper", "firecrawl"]) {
      expect(parseTabSearchResponse(rawResponse({ provider }), "week")?.response.provider).toBe(
        provider,
      );
    }
    for (const provider of ["bing", undefined, null, 7]) {
      expect(parseTabSearchResponse(rawResponse({ provider }), "week")?.response.provider).toBeNull();
    }
  });

  it("reads cached strictly: only a real true counts", () => {
    expect(parseTabSearchResponse(rawResponse({ cached: true }), "week")?.response.cached).toBe(true);
    for (const cached of ["true", 1, undefined, null]) {
      expect(parseTabSearchResponse(rawResponse({ cached }), "week")?.response.cached).toBe(false);
    }
  });

  it("cleans and caps the query label the server sent", () => {
    const outcome = parseTabSearchResponse(
      rawResponse({ query_label: `  software \n  engineer‮ ${"x".repeat(300)}` }),
      "week",
    );
    const label = outcome?.response.query_label ?? "";
    expect(label.startsWith("software engineer x")).toBe(true);
    expect(label).not.toContain("‮");
    expect(Array.from(label).length).toBeLessThanOrEqual(200);
    expect(parseTabSearchResponse(rawResponse({ query_label: 5 }), "week")?.response.query_label).toBe("");
  });

  it("keeps the signals when only the counts are bad (counts are unknown, the list is not lost)", () => {
    const outcome = parseTabSearchResponse(
      rawResponse({ counts: { ...RAW_COUNTS, raw_hits: 999 } }),
      "week",
    );
    expect(outcome?.response.counts).toBeNull();
    expect(outcome?.response.signals).toHaveLength(2);
  });
});

describe("saved searches", () => {
  const raw = (overrides: Record<string, unknown> = {}) => ({
    id: "5c0d0000-0000-4000-8000-000000000001",
    query: "software engineer",
    location: "Bengaluru",
    created_at: "2026-09-18T12:00:00Z",
    ...overrides,
  });

  it("parses one row", () => {
    expect(parseSavedHiringSearch(raw())).toEqual({
      id: "5c0d0000-0000-4000-8000-000000000001",
      query: "software engineer",
      location: "Bengaluru",
      created_at: "2026-09-18T12:00:00Z",
    });
  });

  it("collapses whitespace in what the person typed, and reads a blank or missing location as none", () => {
    expect(parseSavedHiringSearch(raw({ query: "  data   scientist " }))?.query).toBe("data scientist");
    for (const location of [null, undefined, "", "   ", 5]) {
      expect(parseSavedHiringSearch(raw({ location }))?.location).toBeNull();
    }
  });

  it("drops a row with no usable id or role: there is nothing to show, re-run or delete by", () => {
    for (const override of [
      { id: undefined },
      { id: "" },
      { id: "   " },
      { id: "x".repeat(65) },
      { id: 5 },
      { query: undefined },
      { query: "" },
      { query: "​​" },
      { query: 9 },
    ]) {
      expect(parseSavedHiringSearch(raw(override))).toBeNull();
    }
    expect(parseSavedHiringSearch("x")).toBeNull();
    expect(parseSavedHiringSearch(null)).toBeNull();
  });

  it("keeps the row when only its date is unreadable, so it stays deletable", () => {
    expect(parseSavedHiringSearch(raw({ created_at: 5 }))?.created_at).toBe("");
  });

  it("carries only the four fields, whatever else the server sent", () => {
    const parsed = parseSavedHiringSearch(raw({ user_id: "u1", secret: "x", companies: ["A"] }));
    expect(Object.keys(parsed ?? {}).sort()).toEqual(["created_at", "id", "location", "query"]);
  });

  it("parses the list, leaving out unusable rows and collapsing repeats of one id", () => {
    const list = parseSavedSearchesResponse({
      searches: [raw({ id: "a" }), raw({ id: "a", query: "again" }), { nope: 1 }, raw({ id: "b" })],
    });
    expect(list?.map((s) => s.id)).toEqual(["a", "b"]);
    expect(list?.[0].query).toBe("software engineer");
  });

  it("is null when the body is not { searches: [...] }", () => {
    for (const bad of [null, undefined, [], "x", {}, { searches: "no" }, { saves: [] }]) {
      expect(parseSavedSearchesResponse(bad)).toBeNull();
    }
    expect(parseSavedSearchesResponse({ searches: [] })).toEqual([]);
  });
});

describe("locale", () => {
  it("knows exactly india and global; 'auto' is a form choice, never a wire value", () => {
    expect(isLocale("india")).toBe(true);
    expect(isLocale("global")).toBe(true);
    for (const bad of ["auto", "", "INDIA", null, undefined, 3]) expect(isLocale(bad)).toBe(false);
  });

  it("maps a select value back to a choice, falling back to Auto for anything else", () => {
    expect(toLocaleChoice("india")).toBe("india");
    expect(toLocaleChoice("global")).toBe("global");
    expect(toLocaleChoice("auto")).toBe("auto");
    expect(toLocaleChoice("klingon")).toBe("auto");
    expect(toLocaleChoice("")).toBe("auto");
  });
});
