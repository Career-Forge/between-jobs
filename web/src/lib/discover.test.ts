import { describe, expect, it } from "vitest";
import type { JobCard, SavedSearch } from "./discoverTypes";
import {
  binBadgeClass,
  formatPostedDate,
  formatSalary,
  locationLabel,
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

describe("binBadgeClass", () => {
  it("maps every bin to a distinct badge class", () => {
    expect(binBadgeClass("Strong")).toBe("bj-badge-emerald");
    expect(binBadgeClass("Good")).toBe("bj-badge-cyan");
    expect(binBadgeClass("Mixed")).toBe("bj-badge-gold");
    expect(binBadgeClass("Poor")).toBe("bj-badge-danger");
  });
});

describe("subScoreLabel", () => {
  it("returns a friendly label for a known key", () => {
    expect(subScoreLabel("workauth")).toBe("Work Authorization");
    expect(subScoreLabel("company_health")).toBe("Company Health");
  });

  it("falls back to the raw key for an unknown one", () => {
    expect(subScoreLabel("mystery_dim")).toBe("mystery_dim");
  });
});

describe("formatPostedDate", () => {
  it("formats a real ISO date", () => {
    expect(formatPostedDate("2026-08-01T00:00:00Z")).toMatch(/^Posted /);
  });

  it("returns null for null", () => {
    expect(formatPostedDate(null)).toBeNull();
  });

  it("returns null for an unparseable date", () => {
    expect(formatPostedDate("not a date")).toBeNull();
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
