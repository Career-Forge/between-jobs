import { describe, expect, it } from "vitest";
import {
  ACTIVITY_ID_RX,
  cleanText,
  isIsoTimestamp,
  parseSavedPost,
  parseSavesResponse,
  parseSearchResponse,
  parseSignal,
  parseSpecies,
} from "./hiringSignalsTypes";

// Synthetic ids, names and urls throughout -- nothing here came from a real
// capture, and the linkedin.com strings are only ever parsed, never fetched.
const ID = "7000000000000000001";
const ID_2 = "7000000000000000002";

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
    role_match: true,
    registry_match: "unmatched",
    saved: false,
    ...overrides,
  };
}

const RAW_COUNTS = {
  raw_hits: 10,
  rejected: 1,
  duplicates: 2,
  off_topic_hidden: 3,
  echoes_hidden: 1,
  job_seekers_hidden: 1,
  too_old_hidden: 0,
  shown: 2,
};

function rawResponse(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    provider: "you_com",
    cached: false,
    freshness: "week",
    query_label: "Acme -- software engineer -- last 7 days",
    signals: [rawSignal(), rawSignal({ activity_id: ID_2, species: "unclassified" })],
    counts: RAW_COUNTS,
    ...overrides,
  };
}

describe("parseSearchResponse", () => {
  it("parses a well-formed response into the contract's shape", () => {
    const outcome = parseSearchResponse(rawResponse(), "week");
    expect(outcome).not.toBeNull();
    expect(outcome?.unreadable).toBe(0);
    const { response } = outcome!;
    expect(response.provider).toBe("you_com");
    expect(response.cached).toBe(false);
    expect(response.freshness).toBe("week");
    expect(response.query_label).toBe("Acme -- software engineer -- last 7 days");
    expect(response.counts).toEqual(RAW_COUNTS);
    expect(response.signals).toHaveLength(2);
    expect(response.signals[0]).toEqual({
      activity_id: ID,
      post_url: `https://www.linkedin.com/posts/example-${ID}`,
      embed_url: `https://www.linkedin.com/embed/feed/update/urn:li:activity:${ID}`,
      author_name: "Jane Example",
      posted_at: "2026-09-17T10:00:00Z",
      age_hint: "2 days ago",
      species: "hiring_drive",
      comment_count: 12,
      role_match: true,
      registry_match: "unmatched",
      saved: false,
    });
  });

  it("fails the whole response only when it is not an object with a signals list", () => {
    for (const bad of [null, undefined, "x", 5, [], {}, { signals: "nope" }, { signals: {} }]) {
      expect(parseSearchResponse(bad, "week")).toBeNull();
    }
    expect(parseSearchResponse({ signals: [] }, "week")).not.toBeNull();
  });

  it("carries ONLY the contract's fields -- a leaked title or snippet never reaches state", () => {
    const leaky = rawResponse({
      signals: [
        rawSignal({
          title: "Hiring: Staff Engineer | Jane Example - LinkedIn",
          snippet: "We are hiring a staff engineer, DM me at jane@example.com",
          description: "post body text",
          author_handle: "jane-example",
        }),
      ],
      snippet: "top-level snippet",
      raw_query: "site:linkedin.com/posts hiring Acme",
    });
    const outcome = parseSearchResponse(leaky, "week")!;
    expect(Object.keys(outcome.response.signals[0]).sort()).toEqual(
      [
        "activity_id",
        "age_hint",
        "author_name",
        "comment_count",
        "embed_url",
        "post_url",
        "posted_at",
        "registry_match",
        "role_match",
        "saved",
        "species",
      ].sort(),
    );
    expect(Object.keys(outcome.response).sort()).toEqual(
      ["cached", "counts", "freshness", "provider", "query_label", "signals"].sort(),
    );
    const serialised = JSON.stringify(outcome);
    for (const secret of ["staff engineer, DM", "jane@example.com", "post body text", "site:"]) {
      expect(serialised).not.toContain(secret);
    }
  });

  it("drops a signal with no usable activity id and counts it instead of hiding it", () => {
    const outcome = parseSearchResponse(
      rawResponse({
        signals: [
          rawSignal(),
          rawSignal({ activity_id: "not-digits" }),
          rawSignal({ activity_id: 12345 }),
          rawSignal({ activity_id: "" }),
          null,
          "a string",
          rawSignal({ activity_id: "9".repeat(26) }),
        ],
      }),
      "week",
    )!;
    expect(outcome.response.signals.map((s) => s.activity_id)).toEqual([ID]);
    expect(outcome.unreadable).toBe(6);
  });

  it("keeps the first of a repeated activity id (a React key must be unique) and counts the rest", () => {
    const outcome = parseSearchResponse(
      rawResponse({ signals: [rawSignal(), rawSignal({ author_name: "Someone Else" })] }),
      "week",
    )!;
    expect(outcome.response.signals).toHaveLength(1);
    expect(outcome.response.signals[0].author_name).toBe("Jane Example");
    expect(outcome.unreadable).toBe(1);
  });

  it("turns an unknown species into 'unclassified' rather than guessing", () => {
    expect(parseSpecies("hiring_drive")).toBe("hiring_drive");
    expect(parseSpecies("job_seeker")).toBe("unclassified");
    expect(parseSpecies("brand_new_kind")).toBe("unclassified");
    expect(parseSpecies(null)).toBe("unclassified");
    expect(parseSpecies(3)).toBe("unclassified");
  });

  it("labels unknown things as unknown: nulls, not defaults that look like data", () => {
    const signal = parseSignal(
      rawSignal({
        author_name: 42,
        posted_at: "garbage",
        age_hint: {},
        comment_count: -3,
        role_match: "yes",
        registry_match: "definitely",
        saved: "true",
      }),
    )!;
    expect(signal.author_name).toBeNull();
    expect(signal.posted_at).toBeNull();
    expect(signal.age_hint).toBeNull();
    expect(signal.comment_count).toBeNull();
    expect(signal.role_match).toBeNull();
    expect(signal.registry_match).toBeNull();
    // `saved` is the one field where "not exactly true" is honestly "not saved".
    expect(signal.saved).toBe(false);
  });

  it("accepts a real zero comment count and both booleans for role_match", () => {
    expect(parseSignal(rawSignal({ comment_count: 0 }))?.comment_count).toBe(0);
    expect(parseSignal(rawSignal({ role_match: false }))?.role_match).toBe(false);
    expect(parseSignal(rawSignal({ role_match: true }))?.role_match).toBe(true);
  });

  it("rejects a fractional or non-finite comment count", () => {
    expect(parseSignal(rawSignal({ comment_count: 1.5 }))?.comment_count).toBeNull();
    expect(parseSignal(rawSignal({ comment_count: Number.NaN }))?.comment_count).toBeNull();
    expect(parseSignal(rawSignal({ comment_count: Number.POSITIVE_INFINITY }))?.comment_count).toBeNull();
  });

  it("makes counts all-or-nothing: one bad count makes them all unknown", () => {
    expect(parseSearchResponse(rawResponse({ counts: null }), "week")!.response.counts).toBeNull();
    expect(
      parseSearchResponse(rawResponse({ counts: { ...RAW_COUNTS, duplicates: "2" } }), "week")!
        .response.counts,
    ).toBeNull();
    const { shown: _omitted, ...missingOne } = RAW_COUNTS;
    expect(parseSearchResponse(rawResponse({ counts: missingOne }), "week")!.response.counts).toBeNull();
    expect(
      parseSearchResponse(rawResponse({ counts: { ...RAW_COUNTS, raw_hits: -1 } }), "week")!.response
        .counts,
    ).toBeNull();
  });

  it("does not know a provider it has never heard of, and says so with null", () => {
    expect(parseSearchResponse(rawResponse({ provider: "bing" }), "week")!.response.provider).toBeNull();
    expect(parseSearchResponse(rawResponse({ provider: 7 }), "week")!.response.provider).toBeNull();
    for (const provider of ["you_com", "brave", "serper", "firecrawl"]) {
      expect(parseSearchResponse(rawResponse({ provider }), "week")!.response.provider).toBe(provider);
    }
  });

  it("uses the requested window when the server's own echo of it is missing or unknown", () => {
    expect(parseSearchResponse(rawResponse({ freshness: "3days" }), "week")!.response.freshness).toBe(
      "3days",
    );
    expect(parseSearchResponse(rawResponse({ freshness: "month" }), "day")!.response.freshness).toBe(
      "day",
    );
    expect(parseSearchResponse(rawResponse({ freshness: undefined }), "3days")!.response.freshness).toBe(
      "3days",
    );
  });

  it("reads cached strictly, and tolerates a missing query label", () => {
    expect(parseSearchResponse(rawResponse({ cached: true }), "week")!.response.cached).toBe(true);
    expect(parseSearchResponse(rawResponse({ cached: "true" }), "week")!.response.cached).toBe(false);
    expect(parseSearchResponse(rawResponse({ query_label: undefined }), "week")!.response.query_label).toBe(
      "",
    );
  });
});

describe("cleanText", () => {
  it("collapses whitespace and trims", () => {
    expect(cleanText("  Jane \n\t Example  ", 100)).toBe("Jane Example");
  });

  it("strips control characters and bidi overrides, which can make a name read backwards", () => {
    expect(cleanText("Jane\u202eExample\u202c", 100)).toBe("JaneExample");
    expect(cleanText("A\u0000B\u0007C\u200fD\u2066E\u061cF", 100)).toBe("ABCDEF");
  });

  it("leaves real right-to-left and other scripts intact", () => {
    expect(cleanText("محمد", 100)).toBe("محمد");
    expect(cleanText("张伟", 100)).toBe("张伟");
  });

  it("caps by code point, so a cut never leaves half of a surrogate pair", () => {
    const text = "\u{1F600}".repeat(10);
    const cut = cleanText(text, 3)!;
    expect(Array.from(cut)).toHaveLength(3);
    expect(cut).toBe("\u{1F600}".repeat(3));
  });

  it("is null for non-strings and for text with nothing left", () => {
    for (const bad of [null, undefined, 5, {}, "", "   ", "\u202e\u0000"]) {
      expect(cleanText(bad, 100)).toBeNull();
    }
  });

  it("is applied to author names and the query label inside a parsed response", () => {
    const outcome = parseSearchResponse(
      rawResponse({
        query_label: "Acme\u202e -- eng",
        signals: [rawSignal({ author_name: `${"x".repeat(150)}\u202e` })],
      }),
      "week",
    )!;
    expect(outcome.response.query_label).toBe("Acme -- eng");
    expect(outcome.response.signals[0].author_name).toBe("x".repeat(100));
  });
});

describe("saved posts", () => {
  function rawSave(overrides: Record<string, unknown> = {}): Record<string, unknown> {
    return {
      id: "6b1f0c1e-0000-4000-8000-000000000001",
      activity_id: ID,
      post_url: `https://www.linkedin.com/feed/update/urn:li:activity:${ID}`,
      embed_url: `https://www.linkedin.com/embed/feed/update/urn:li:activity:${ID}`,
      created_at: "2026-09-18T12:00:00Z",
      ...overrides,
    };
  }

  it("parses a saved post into the contract's shape", () => {
    expect(parseSavedPost(rawSave())).toEqual(rawSave());
  });

  it("needs a row id and a digit-only activity id, and nothing else", () => {
    expect(parseSavedPost(rawSave({ id: "" }))).toBeNull();
    expect(parseSavedPost(rawSave({ id: 5 }))).toBeNull();
    expect(parseSavedPost(rawSave({ activity_id: "abc" }))).toBeNull();
    expect(parseSavedPost(null)).toBeNull();
    expect(parseSavedPost("x")).toBeNull();
  });

  it("keeps a row whose date is unreadable, so a real saved post can still be removed", () => {
    expect(parseSavedPost(rawSave({ created_at: null }))?.created_at).toBe("");
    expect(parseSavedPost(rawSave({ post_url: 5, embed_url: undefined }))).toMatchObject({
      post_url: "",
      embed_url: "",
    });
  });

  it("carries only the contract's fields", () => {
    const saved = parseSavedPost(rawSave({ author: "Jane Example", snippet: "post body" }))!;
    expect(Object.keys(saved).sort()).toEqual(
      ["activity_id", "created_at", "embed_url", "id", "post_url"].sort(),
    );
  });

  it("parses a saves list, skipping unusable rows and collapsing a repeated row id", () => {
    const list = parseSavesResponse({
      saves: [
        rawSave({ id: "a" }),
        rawSave({ id: "b", activity_id: ID_2 }),
        rawSave({ id: "a" }),
        rawSave({ id: "" }),
        null,
      ],
    })!;
    expect(list.map((s) => s.id)).toEqual(["a", "b"]);
  });

  it("returns an empty list for no saves, and null only when the body is the wrong shape", () => {
    expect(parseSavesResponse({ saves: [] })).toEqual([]);
    for (const bad of [null, undefined, [], {}, { saves: "x" }, { saves: {} }]) {
      expect(parseSavesResponse(bad)).toBeNull();
    }
  });
});


describe("cleanText and format characters (F9)", () => {
  it("strips zero-width and other format characters, not only bidi controls", () => {
    // U+200B zero-width space, U+200C/D joiners, U+2060 word joiner
    expect(cleanText("A\u200bB\u2060C\u200cD\u200dE", 100)).toBe("ABCDE");
  });

  it("returns null for a name made only of invisible characters, not a blank author line", () => {
    expect(cleanText("\u200b\u200b", 100)).toBeNull();
    expect(cleanText("\u2060\ufeff", 100)).toBeNull();
    const outcome = parseSearchResponse(
      rawResponse({ signals: [rawSignal({ author_name: "\u200b\u200b" })] }),
      "week",
    )!;
    expect(outcome.response.signals[0].author_name).toBeNull();
  });

  it("collapses line and paragraph separators (and a BOM) to plain spaces", () => {
    expect(cleanText("A\u2028B\u2029C\ufeffD", 100)).toBe("A B C D");
  });
});

describe("timestamps (F9)", () => {
  it("accepts the server's ISO-8601 forms", () => {
    for (const ok of [
      "2026-09-17T10:00:00Z",
      "2026-09-17T10:00:00+00:00",
      "2026-09-17T10:00:00.123456+00:00",
      "2026-09-17T10:00+05:30",
      "2026-09-17T10:00:00-07:00",
    ]) {
      expect(isIsoTimestamp(ok)).toBe(true);
    }
  });

  it("rejects what Date.parse alone would let through", () => {
    for (const bad of ["1", "foo 12", "2026", "Sep 17 2026", "2026-09-17", "2026-13-45T10:00:00Z", 5, null, ""]) {
      expect(isIsoTimestamp(bad)).toBe(false);
    }
  });

  it("turns a signal's non-ISO posted_at into unknown (null), never 'Posted 25 years ago'", () => {
    for (const bad of ["1", "foo 12"]) {
      const outcome = parseSearchResponse(
        rawResponse({ signals: [rawSignal({ posted_at: bad })] }),
        "week",
      )!;
      expect(outcome.response.signals[0].posted_at).toBeNull();
    }
  });
});

describe("activity ids never start with a zero (BC-10)", () => {
  it("matches the server: 1-25 ASCII digits, no leading zero", () => {
    for (const ok of ["1", ID, "9".repeat(25)]) expect(ACTIVITY_ID_RX.test(ok)).toBe(true);
    for (const bad of ["0", "0" + ID, "007", "", "9".repeat(26), "12a"]) {
      expect(ACTIVITY_ID_RX.test(bad)).toBe(false);
    }
  });

  it("drops a signal or a saved post whose id has a leading zero", () => {
    const outcome = parseSearchResponse(
      rawResponse({ signals: [rawSignal(), rawSignal({ activity_id: "0" + ID })] }),
      "week",
    )!;
    expect(outcome.response.signals.map((s) => s.activity_id)).toEqual([ID]);
    expect(outcome.unreadable).toBe(1);
    const save = {
      id: "save-1",
      activity_id: "0" + ID,
      post_url: "",
      embed_url: "",
      created_at: "2026-09-18T12:00:00Z",
    };
    expect(parseSavedPost(save)).toBeNull();
    expect(parseSavedPost({ ...save, activity_id: ID })).not.toBeNull();
  });
});
