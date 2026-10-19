import { describe, expect, it } from "vitest";
import { EMPTY_REQUEST, discoverQuery, filtersSet, resultsFactOf, rowsOf, warningsOf } from "./discoverFacts";
import type { DiscoverResponse, JobCard } from "./discoverTypes";

// The counts a finished search holds, and the request a search makes: pure, so each is a table.

function card(provider: string, n = 1): JobCard {
  return {
    provider,
    title: `Job ${n}`,
    company: "Acme",
    location: null,
    remote: null,
    apply_url: `https://example.com/${provider}/${n}`,
    snippet: "",
    posted_at: null,
    salary_min: null,
    salary_max: null,
    salary_currency: null,
    sponsorship_signal: "unknown",
    source_tier: 1,
    location_verified: null,
    link_checked: true,
    score: null,
  };
}

function response(overrides: Partial<DiscoverResponse> = {}): DiscoverResponse {
  return { query: "", scored: [], more: [], dead_removed: 0, scoring_strategy: "direct", warnings: [], ...overrides };
}

describe("the request", () => {
  it("sends only the filters that hold something, as typed", () => {
    expect(discoverQuery(EMPTY_REQUEST)).toBe("");
    expect(discoverQuery({ q: "data engineer", location: "", companies: "", remoteOnly: false })).toBe("q=data+engineer");
    expect(discoverQuery({ q: "ml", location: "New York, NY", companies: "Anthropic, Stripe", remoteOnly: true })).toBe(
      "q=ml&location=New+York%2C+NY&companies=Anthropic%2C+Stripe&remote_only=true",
    );
  });

  it("counts the filters that hold something: whitespace is not a filter", () => {
    expect(filtersSet(EMPTY_REQUEST)).toBe(0);
    expect(filtersSet({ q: "  ", location: " ", companies: "", remoteOnly: false })).toBe(0);
    expect(filtersSet({ q: "a", location: "", companies: "", remoteOnly: false })).toBe(1);
    expect(filtersSet({ q: "a", location: "b", companies: "c", remoteOnly: true })).toBe(4);
  });
});

describe("the facts of a finished search", () => {
  it("counts scored rows, rows past the batch and the listings the link check dropped", () => {
    const fact = resultsFactOf(response({ scored: [card("registry"), card("registry", 2)], more: [card("serper")], dead_removed: 3 }), EMPTY_REQUEST);
    expect(fact).toMatchObject({ scored: 2, more: 1, deadRemoved: 3, fallback: false });
  });

  it("counts rows per provider across scored and unscored rows, most first", () => {
    const fact = resultsFactOf(
      response({ scored: [card("serper"), card("registry"), card("registry", 2)], more: [card("registry", 3), card("brave")] }),
      EMPTY_REQUEST,
    );
    expect(fact.providers).toEqual([
      { provider: "registry", count: 3 },
      { provider: "serper", count: 1 },
      { provider: "brave", count: 1 },
    ]);
  });

  it("names a provider that ran and failed, once, and counts the notes that name none", () => {
    const fact = resultsFactOf(
      response({
        scored: [card("registry")],
        warnings: ["brave: request failed", "brave: and again", "serper: unexpected failure", "The job registry could not be searched just now."],
      }),
      EMPTY_REQUEST,
    );
    expect(fact.failedProviders).toEqual(["brave", "serper"]);
    expect(fact.otherNotes).toBe(1);
  });

  it("reads the neutral fallback from the strategy and no other strategy as one", () => {
    expect(resultsFactOf(response({ scoring_strategy: "neutral_fallback" }), EMPTY_REQUEST).fallback).toBe(true);
    for (const strategy of ["direct", "markdown_fence", "regex_object", "item_regex"]) {
      expect(resultsFactOf(response({ scoring_strategy: strategy }), EMPTY_REQUEST).fallback).toBe(false);
    }
  });

  it("counts the filters the search ran with, and whether Companies was one", () => {
    const fact = resultsFactOf(response(), { q: "ml", location: "", companies: "Stripe", remoteOnly: false });
    expect(fact.filtersSet).toBe(2);
    expect(fact.companiesSet).toBe(true);
    expect(resultsFactOf(response(), { q: "ml", location: "", companies: " ", remoteOnly: false }).companiesSet).toBe(false);
  });

  it("survives a reply that is missing its lists, and a dropped count that is not a count", () => {
    const odd = { query: "", scored: undefined, more: null, dead_removed: "3", scoring_strategy: "direct", warnings: undefined } as unknown as DiscoverResponse;
    expect(resultsFactOf(odd, EMPTY_REQUEST)).toMatchObject({ scored: 0, more: 0, deadRemoved: 0, otherNotes: 0, failedProviders: [] });
    expect(rowsOf("nope")).toEqual([]);
    expect(warningsOf(odd)).toEqual([]);
  });

  it("drops blank and non-text warnings", () => {
    expect(warningsOf(response({ warnings: ["brave: down", "  ", 4 as unknown as string] }))).toEqual(["brave: down"]);
  });

  it("caps a provider id so one strange value cannot make the line unreadable", () => {
    const fact = resultsFactOf(response({ scored: [card("x".repeat(200))] }), EMPTY_REQUEST);
    expect(fact.providers[0].provider).toHaveLength(40);
  });
});
