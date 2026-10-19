import { describe, expect, it } from "vitest";
import type { JobCard, JobScore } from "./discoverTypes";
import {
  COMPENSATION_DETAIL,
  REGISTRY_BASIS,
  fitViewOf,
  hasSponsorshipSignal,
  isFallback,
  locationReading,
  rowReceipt,
  rowTrail,
  sponsorshipWords,
  tierWords,
} from "./discoverRow";
import { buildReceipt, buildTrail, findForbiddenWord } from "./trail";

// A row's fit block, trail and receipt, built from the fields a response returned and nothing else.

const NOW = new Date("2026-09-22T12:00:00Z");

function score(overrides: Partial<JobScore> = {}): JobScore {
  return {
    fit_score: 7,
    one_liner: "Strong overlap on data pipelines.",
    score100: 61,
    bin: "Good",
    bottleneck: "experience",
    sub_scores: { skills: 78, experience: 52, workauth: 60, location: 100, company_health: 60, compensation: 60 },
    inapplicable_dims: ["company_health", "compensation"],
    detected_location: "New York, NY",
    location_match: "match",
    ...overrides,
  };
}

function job(overrides: Partial<JobCard> = {}): JobCard {
  return {
    provider: "serper",
    title: "Data Engineer",
    company: "Acme",
    location: "New York, NY",
    remote: false,
    apply_url: "https://jobs.example/1",
    snippet: "Build pipelines.",
    posted_at: "2026-09-20T08:00:00Z",
    salary_min: null,
    salary_max: null,
    salary_currency: null,
    sponsorship_signal: "unknown",
    source_tier: 1,
    location_verified: null,
    link_checked: true,
    score: score(),
    ...overrides,
  };
}

const trailText = (input: Parameters<typeof buildTrail>[0]) => buildTrail(input)?.text ?? null;

describe("the trail line of a row", () => {
  it("says a registry row came from the registry, on the company's job board at the last poll, with the posting's own date", () => {
    expect(trailText(rowTrail(job({ provider: "registry" }), NOW, "UTC"))).toBe(`via registry · ${REGISTRY_BASIS} · Posted Sep 20`);
  });

  it("says a live-search row the liveness check kept had its link checked, and by whom it was found", () => {
    expect(trailText(rowTrail(job(), NOW, "UTC"))).toBe("Link checked · via serper · Posted Sep 20");
    expect(buildTrail(rowTrail(job(), NOW, "UTC"))?.glyphs).toEqual(["passed"]);
  });

  it("says a live-search row that never reached the check was not checked, in the muted code glyph", () => {
    const input = rowTrail(job({ link_checked: false }), NOW, "UTC");
    expect(trailText(input)).toBe("Link not checked yet · via serper · Posted Sep 20");
    expect(buildTrail(input)?.glyphs).toEqual(["code"]);
  });

  it("never says a link was verified, in any row shape", () => {
    for (const row of [job(), job({ link_checked: false }), job({ provider: "registry", link_checked: true })]) {
      const text = trailText(rowTrail(row, NOW, "UTC")) ?? "";
      expect(findForbiddenWord(text)).toBeNull();
      expect(text.toLowerCase()).not.toContain("verified");
    }
  });

  it("leaves the 'when' and its separator out when the posting has no readable date", () => {
    expect(trailText(rowTrail(job({ posted_at: null }), NOW, "UTC"))).toBe("Link checked · via serper");
    expect(trailText(rowTrail(job({ posted_at: "yesterday-ish" }), NOW, "UTC"))).toBe("Link checked · via serper");
    expect(trailText(rowTrail(job({ provider: "registry", posted_at: null }), NOW, "UTC"))).toBe(`via registry · ${REGISTRY_BASIS}`);
  });

  it("writes the year of a posting from another year", () => {
    expect(trailText(rowTrail(job({ posted_at: "2025-12-30T08:00:00Z" }), NOW, "UTC"))).toContain("Posted Dec 30, 2025");
  });

  it("reads a date-only posting date as that calendar day", () => {
    expect(trailText(rowTrail(job({ posted_at: "2026-09-20" }), NOW, "UTC"))).toContain("Posted Sep 20");
  });

  it("keeps the registry line the same whether or not the row says its link was checked (the registry stamps every row)", () => {
    const a = trailText(rowTrail(job({ provider: "registry", link_checked: true }), NOW, "UTC"));
    const b = trailText(rowTrail(job({ provider: "registry", link_checked: false }), NOW, "UTC"));
    expect(a).toBe(b);
  });
});

describe("the fit block", () => {
  it("is nothing at all for a row past the scoring batch", () => {
    expect(fitViewOf(job({ score: null }), "direct")).toEqual({ kind: "unscored" });
  });

  it("is a placeholder, with no rung and no number, when the scoring fell back", () => {
    const view = fitViewOf(job(), "neutral_fallback");
    expect(view).toEqual({ kind: "placeholder" });
    expect(isFallback("neutral_fallback")).toBe(true);
    expect(isFallback("direct")).toBe(false);
  });

  it("is a placeholder for an unscored-by-model strategy only when it is the fallback, not for the partial ones", () => {
    for (const strategy of ["direct", "markdown_fence", "regex_object", "item_regex"]) {
      expect(fitViewOf(job(), strategy).kind).toBe("scored");
    }
  });

  it("carries the bin, the number and the model's one line", () => {
    const view = fitViewOf(job(), "direct");
    if (view.kind !== "scored") throw new Error("expected a scored view");
    expect(view.level).toBe("Good");
    expect(view.score100).toBe(61);
    expect(view.oneLiner).toBe("Strong overlap on data pipelines.");
  });

  it("draws a bar only for a dimension that was applied: a dropped one is never drawn as 0", () => {
    const view = fitViewOf(job(), "direct");
    if (view.kind !== "scored") throw new Error("expected a scored view");
    expect(view.bars.map((bar) => bar.key)).toEqual(["skills", "experience", "workauth", "location"]);
    expect(view.bars.every((bar) => bar.value > 0)).toBe(true);
  });

  it("names every dropped dimension and why, from the fields the card holds", () => {
    const view = fitViewOf(job({ salary_min: null }), "direct");
    if (view.kind !== "scored") throw new Error("expected a scored view");
    expect(view.notApplied).toEqual([
      "Not applied: Company Health -- company isn't in the tier list",
      "Not applied: Compensation -- no salary listed",
    ]);
  });

  it("gives Compensation, when it counts, the caption that says it is not compared to expectations", () => {
    const view = fitViewOf(job({ salary_min: 120000, score: score({ inapplicable_dims: ["company_health"] }) }), "direct");
    if (view.kind !== "scored") throw new Error("expected a scored view");
    const compensation = view.bars.find((bar) => bar.key === "compensation");
    expect(compensation?.value).toBe(60);
    expect(compensation?.detail).toBe(COMPENSATION_DETAIL);
    expect(view.bars.filter((bar) => bar.key !== "compensation").every((bar) => bar.detail === null)).toBe(true);
  });

  it("says nothing of why a dropped dimension was dropped when the fields do not say", () => {
    const view = fitViewOf(job({ salary_min: 90000, score: score({ inapplicable_dims: ["compensation"] }) }), "direct");
    if (view.kind !== "scored") throw new Error("expected a scored view");
    expect(view.notApplied).toEqual(["Not applied: Compensation"]);
  });

  it("names the lowest part in words, and leaves the line out when the response named none", () => {
    const named = fitViewOf(job(), "direct");
    const unnamed = fitViewOf(job({ score: score({ bottleneck: null }) }), "direct");
    if (named.kind !== "scored" || unnamed.kind !== "scored") throw new Error("expected scored views");
    expect(named.lowest).toBe("Experience");
    expect(unnamed.lowest).toBeNull();
  });

  it("skips a sub-score that is not a number rather than draw it as 0", () => {
    const view = fitViewOf(job({ score: score({ sub_scores: { skills: 70, experience: Number.NaN } as unknown as JobScore["sub_scores"], inapplicable_dims: [] }) }), "direct");
    if (view.kind !== "scored") throw new Error("expected a scored view");
    expect(view.bars.map((bar) => bar.key)).toEqual(["skills"]);
  });

  it("survives a score with no list of dropped dimensions", () => {
    const view = fitViewOf(job({ score: score({ inapplicable_dims: undefined as unknown as string[] }) }), "direct");
    expect(view.kind).toBe("scored");
  });
});

describe("the location, in three states", () => {
  it("says a match, a mismatch and 'can't tell' in their own words, with where the model read the job to be", () => {
    expect(locationReading(score({ location_match: "match", detected_location: "Austin, TX" })).words).toBe("Location matches -- the model read it as Austin, TX");
    expect(locationReading(score({ location_match: "mismatch", detected_location: "London" })).words).toBe("Location doesn't match -- the model read it as London");
    expect(locationReading(score({ location_match: "unknown", detected_location: null })).words).toBe("Location: can't tell");
  });

  it("leaves the detected place out when none was returned, and reads an unreadable state as 'can't tell'", () => {
    expect(locationReading(score({ location_match: "match", detected_location: null })).words).toBe("Location matches");
    expect(locationReading(score({ location_match: "maybe" as never, detected_location: "  " })).words).toBe("Location: can't tell");
    expect(locationReading(score({ location_match: "maybe" as never })).state).toBe("unknown");
  });
});

describe("sponsorship, as a text signal", () => {
  it("words each of the three values and never calls it a judgement", () => {
    expect(sponsorshipWords("explicit_yes")).toBe("The posting text mentions visa sponsorship");
    expect(sponsorshipWords("explicit_no")).toBe("The posting text rules out visa sponsorship");
    expect(sponsorshipWords("unknown")).toBe("Visa sponsorship is not stated in the text read");
    expect(sponsorshipWords("something new")).toBe("Visa sponsorship is not stated in the text read");
  });

  it("is a signal worth a line in the row only when the text said something", () => {
    expect(hasSponsorshipSignal("explicit_yes")).toBe(true);
    expect(hasSponsorshipSignal("explicit_no")).toBe(true);
    expect(hasSponsorshipSignal("unknown")).toBe(false);
  });
});

describe("the source tier, in words", () => {
  it("words the tiers the engine assigns", () => {
    expect(tierWords(1)).toBe("tier 1 -- a company's job board");
    expect(tierWords(1.5)).toBe("tier 1.5 -- a job-search provider's listing");
    expect(tierWords(2)).toBe("tier 2 -- a curated job board");
    expect(tierWords(2.5)).toBe("tier 2.5 -- a company careers page");
  });

  it("gives a tier it does not know as the number alone, and nothing for a value that is not one", () => {
    expect(tierWords(2.75)).toBe("tier 2.75");
    expect(tierWords(Number.NaN)).toBeNull();
  });
});

describe("the receipt", () => {
  const rows = (row: JobCard, strategy = "direct") => buildReceipt(rowReceipt(row, strategy))?.rows ?? [];
  const value = (row: JobCard, label: string, strategy = "direct") => {
    const found = rows(row, strategy).find((entry) => "label" in entry && entry.label === label);
    return found !== undefined && "value" in found ? found.value : null;
  };

  it("holds the provider, the source tier in words, the link check, the location and the sponsorship signal", () => {
    expect(value(job(), "provider")).toBe("serper");
    expect(value(job(), "source")).toBe("tier 1 -- a company's job board");
    expect(value(job(), "link check")).toContain("probed and not found closed");
    expect(value(job(), "location")).toBe("New York, NY, not marked remote. Location matches -- the model read it as New York, NY");
    expect(value(job(), "sponsorship")).toBe("unknown (text match, no model)");
  });

  it("says a registry row was not probed, and that its poll is not a check of this link", () => {
    expect(value(job({ provider: "registry" }), "link check")).toContain("not probed");
  });

  it("says a row past the liveness check was not checked yet", () => {
    expect(value(job({ link_checked: false }), "link check")).toBe("not checked yet");
  });

  it("holds the applicable sub-scores, what was not applied and the lowest part", () => {
    expect(value(job(), "sub-scores")).toBe("Skills 78 · Experience 52 · Work Authorization 60 · Location 100");
    expect(value(job(), "not applied")).toBe("Company Health, Compensation");
    expect(value(job(), "lowest part")).toBe("Experience");
  });

  it("names the scoring strategy only when it is the neutral fallback", () => {
    expect(value(job(), "scoring")).toBeNull();
    expect(value(job(), "scoring", "markdown_fence")).toBeNull();
    expect(value(job(), "scoring", "neutral_fallback")).toContain("neutral_fallback");
  });

  it("holds no sub-scores, dropped dimensions or bottleneck for a placeholder: nothing was read", () => {
    for (const label of ["sub-scores", "not applied", "lowest part"]) {
      expect(value(job(), label, "neutral_fallback")).toBeNull();
    }
    expect(value(job(), "location", "neutral_fallback")).toBe("New York, NY, not marked remote");
  });

  it("holds no score rows for a row that was not scored, and still holds where it came from", () => {
    const unscored = job({ score: null });
    expect(value(unscored, "sub-scores")).toBeNull();
    expect(value(unscored, "scoring", "neutral_fallback")).toBeNull();
    expect(value(unscored, "provider")).toBe("serper");
    expect(value(unscored, "location")).toBe("New York, NY, not marked remote");
  });

  it("says a location is not given rather than leave a gap", () => {
    expect(value(job({ location: null, remote: null, score: null }), "location")).toBe("none given");
  });
});
