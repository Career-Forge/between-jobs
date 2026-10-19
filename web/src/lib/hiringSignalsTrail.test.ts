import { describe, expect, it } from "vitest";
import type { TabSearchResponse, TabSignal } from "./hiringSignalsTabTypes";
import { savedTrail, signalTrail, sourceTrail } from "./hiringSignalsTrail";
import { buildTrail, findForbiddenWord } from "./trail";

// The trail lines of the Hiring signals page: built from returned fields only, never naming the site
// the posts come from and never claiming a post is a hiring call.

const NOW = new Date("2026-09-22T12:00:00Z");

function response(overrides: Partial<TabSearchResponse> = {}): TabSearchResponse {
  return {
    provider: "firecrawl",
    cached: false,
    freshness: "3days",
    locale: "global",
    query_label: "software engineer -- Pune -- last 3 days",
    signals: [],
    counts: null,
    ...overrides,
  };
}

function signal(overrides: Partial<TabSignal> = {}): TabSignal {
  return {
    activity_id: "7000000000000000001",
    post_url: "https://example.test/post",
    embed_url: "https://example.test/embed",
    author_name: null,
    posted_at: "2026-09-18T12:00:00Z",
    age_hint: null,
    species: "unclassified",
    comment_count: null,
    registry_match: null,
    aggregator: null,
    saved: false,
    ...overrides,
  };
}

const text = (input: Parameters<typeof buildTrail>[0]) => buildTrail(input)?.text ?? null;

describe("the search's own trail", () => {
  it("says via which provider, for what, in which wording, in the plain-code glyph", () => {
    const built = buildTrail(sourceTrail(response()));
    expect(built?.text).toBe("via Firecrawl · software engineer -- Pune -- last 3 days · Global wording");
    expect(built?.glyphs).toEqual(["code"]);
  });

  it("says 'your search provider' for a provider it does not know, and names no model", () => {
    expect(text(sourceTrail(response({ provider: null })))).toBe("via your search provider · software engineer -- Pune -- last 3 days · Global wording");
    expect(buildTrail(sourceTrail(response()))?.glyphs).not.toContain("model");
  });

  it("leaves the wording out when the server's echo was missing", () => {
    expect(text(sourceTrail(response({ locale: null })))).toBe("via Firecrawl · software engineer -- Pune -- last 3 days");
  });

  it("names India's wording as the server echoed it", () => {
    expect(text(sourceTrail(response({ locale: "india" })))).toContain("India wording");
  });
});

describe("a result card's trail", () => {
  it("says the tag came from code with no model, and the post's own date", () => {
    const built = buildTrail(signalTrail(signal(), NOW, "UTC"));
    expect(built?.text).toBe("Tagged by code · no model involved · Posted Sep 18");
    expect(built?.glyphs).toEqual(["code"]);
  });

  it("leaves the date and its separator out when the post's time is not returned or unreadable", () => {
    expect(text(signalTrail(signal({ posted_at: null }), NOW, "UTC"))).toBe("Tagged by code · no model involved");
    expect(text(signalTrail(signal({ posted_at: "last week" }), NOW, "UTC"))).toBe("Tagged by code · no model involved");
  });

  it("writes the year of a post from another year", () => {
    expect(text(signalTrail(signal({ posted_at: "2025-12-30T08:00:00Z" }), NOW, "UTC"))).toContain("Posted Dec 30, 2025");
  });

  it("never claims a post is a hiring call, an active search or a connection, and never names the source site", () => {
    for (const species of ["unclassified", "hiring_drive", "referral_offer", "ats_echo"] as const) {
      const line = text(signalTrail(signal({ species }), NOW, "UTC")) ?? "";
      expect(line).not.toMatch(/actively hiring|hiring call|connection|linkedin/i);
      expect(findForbiddenWord(line)).toBeNull();
    }
  });
});

describe("a saved post's or search's trail", () => {
  it("says the person saved it, in the ivory 'you' glyph, with the day", () => {
    const built = buildTrail(savedTrail({ created_at: "2026-09-20T09:00:00Z" }, NOW, "UTC"));
    expect(built?.text).toBe("You saved this · Sep 20");
    expect(built?.glyphs).toEqual(["you"]);
  });

  it("leaves the day out when the stored date cannot be read, and keeps the line", () => {
    expect(text(savedTrail({ created_at: "" }, NOW, "UTC"))).toBe("You saved this");
    expect(text(savedTrail({ created_at: "garbage" }, NOW, "UTC"))).toBe("You saved this");
  });
});
