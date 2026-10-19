import { describe, expect, it } from "vitest";
import type { JobCard, SavedSearch } from "./discoverTypes";
import {
  formatSalary,
  locationLabel,
  parseWarning,
  providerName,
  savedSearchLabel,
  subScoreLabel,
  trackRequestBody,
} from "./discover";

function makeSavedSearch(overrides: Partial<SavedSearch> = {}): SavedSearch {
  return {
    id: "s1",
    query: "",
    location: null,
    companies: [],
    remote_only: false,
    is_active: true,
    created_at: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

function makeJobCard(overrides: Partial<JobCard> = {}): JobCard {
  return {
    provider: "registry",
    title: "Backend Engineer",
    company: "Acme",
    location: "New York, NY",
    remote: false,
    apply_url: "https://example.com/jobs/1",
    snippet: "Build things.",
    posted_at: null,
    salary_min: null,
    salary_max: null,
    salary_currency: null,
    sponsorship_signal: "unknown",
    source_tier: 1.0,
    location_verified: null,
    link_checked: true,
    score: null,
    ...overrides,
  };
}

describe("subScoreLabel", () => {
  it("returns a friendly label for a known key", () => {
    expect(subScoreLabel("workauth")).toBe("Work Authorization");
    expect(subScoreLabel("company_health")).toBe("Company Health");
  });

  it("falls back to the raw key for an unknown one", () => {
    expect(subScoreLabel("mystery_dim")).toBe("mystery_dim");
  });
});

describe("formatSalary", () => {
  it("returns null when both are missing", () => {
    expect(formatSalary(null, null, "USD")).toBeNull();
  });

  it("formats a range when both are present and differ", () => {
    expect(formatSalary(120000, 150000, "USD")).toBe("USD 120,000-150,000");
  });

  it("formats a single value when min equals max", () => {
    expect(formatSalary(100000, 100000, "USD")).toBe("USD 100,000");
  });

  it("formats a single value when only min is present", () => {
    expect(formatSalary(100000, null, "USD")).toBe("USD 100,000");
  });

  it("formats a single value when only max is present", () => {
    expect(formatSalary(null, 100000, "USD")).toBe("USD 100,000");
  });

  it("handles a missing currency gracefully", () => {
    expect(formatSalary(100000, null, null)).toBe("100,000");
  });
});

describe("locationLabel", () => {
  it("combines location and remote when both present", () => {
    expect(locationLabel(makeJobCard({ location: "NYC", remote: true }))).toBe("NYC -- Remote");
  });

  it("shows just the location when not remote", () => {
    expect(locationLabel(makeJobCard({ location: "NYC", remote: false }))).toBe("NYC");
  });

  it("shows just Remote when location is unknown", () => {
    expect(locationLabel(makeJobCard({ location: null, remote: true }))).toBe("Remote");
  });

  it("falls back to a clear unknown label", () => {
    expect(locationLabel(makeJobCard({ location: null, remote: false }))).toBe(
      "Location unknown",
    );
  });
});

describe("trackRequestBody", () => {
  it("carries exactly the fields the track endpoint needs", () => {
    const job = makeJobCard({
      apply_url: "https://example.com/jobs/1",
      title: "Backend Engineer",
      company: "Acme",
      location: "NYC",
      snippet: "A preview.",
      provider: "registry",
    });
    expect(trackRequestBody(job)).toEqual({
      apply_url: "https://example.com/jobs/1",
      title: "Backend Engineer",
      company: "Acme",
      location: "NYC",
      snippet: "A preview.",
      provider: "registry",
    });
  });
});

describe("savedSearchLabel", () => {
  it("combines query, companies, location, and remote", () => {
    const label = savedSearchLabel(
      makeSavedSearch({
        query: "backend engineer",
        companies: ["Anthropic", "Stripe"],
        location: "New York, NY",
        remote_only: true,
      }),
    );
    expect(label).toBe('"backend engineer" at Anthropic, Stripe in New York, NY remote only');
  });

  it("falls back to a clear label when nothing is set", () => {
    expect(savedSearchLabel(makeSavedSearch())).toBe("Any job");
  });

  it("shows just the query when that's all that's set", () => {
    expect(savedSearchLabel(makeSavedSearch({ query: "ml engineer" }))).toBe('"ml engineer"');
  });
});

describe("providerName", () => {
  it("names the providers a search can use", () => {
    expect(providerName("brave")).toBe("Brave Search");
    expect(providerName("you_com")).toBe("You.com");
    expect(providerName("registry")).toBe("The job registry");
  });

  it("shows an id it has not heard of as the server sent it, never a guess", () => {
    expect(providerName("newboard")).toBe("newboard");
  });
});

describe("parseWarning", () => {
  it("splits a provider's failure into the id and its own message", () => {
    expect(parseWarning("brave: request failed (429)")).toEqual({ provider: "brave", message: "request failed (429)" });
    expect(parseWarning("serper: unexpected failure")).toEqual({ provider: "serper", message: "unexpected failure" });
  });

  it("leaves a sentence that names no provider whole, even when it holds a colon", () => {
    const registry = "The job registry could not be searched just now, so these results come from the live search providers only. Try again in a moment.";
    expect(parseWarning(registry)).toEqual({ provider: null, message: registry });
    expect(parseWarning("Note: the registry is slow")).toEqual({ provider: null, message: "Note: the registry is slow" });
  });

  it("does not read a prefix that is not shaped like a provider id", () => {
    expect(parseWarning("two words: nope").provider).toBeNull();
    expect(parseWarning("x: too short an id").provider).toBeNull();
  });

  it("trims the text it was given", () => {
    expect(parseWarning("  brave: down  ")).toEqual({ provider: "brave", message: "down" });
  });
});
