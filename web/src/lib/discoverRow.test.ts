import { describe, expect, it } from "vitest";
import { isUnscored } from "./discoverArrange";
import type { JobCard, JobScore } from "./discoverTypes";
import {
  COMPENSATION_DETAIL,
  REGISTRY_BASIS,
  REGISTRY_STALE_BASIS,
  fitPreviewOf,
  fitViewOf,
  hasSponsorshipSignal,
  sponsorshipTag,
  SPONSORSHIP_PROVIDER_TAG,
  SPONSORSHIP_TAG,
  isFallback,
  locationReading,
  rowReceipt,
  rowTrail,
  sponsorshipWords,
  tierWords,
  type ScoredFit,
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

  it("says a live-search row the liveness check kept was not found closed, in the muted code glyph: a probe that never answered keeps a row too, so it is no green pass", () => {
    expect(trailText(rowTrail(job(), NOW, "UTC"))).toBe("Link not found closed · via serper · Posted Sep 20");
    expect(buildTrail(rowTrail(job(), NOW, "UTC"))?.glyphs).toEqual(["code"]);
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
    expect(trailText(rowTrail(job({ posted_at: null }), NOW, "UTC"))).toBe("Link not found closed · via serper");
    expect(trailText(rowTrail(job({ posted_at: "yesterday-ish" }), NOW, "UTC"))).toBe("Link not found closed · via serper");
    expect(trailText(rowTrail(job({ provider: "registry", posted_at: null }), NOW, "UTC"))).toBe(`via registry · ${REGISTRY_BASIS}`);
  });

  it("writes the year of a posting from another year", () => {
    expect(trailText(rowTrail(job({ posted_at: "2025-12-30T08:00:00Z" }), NOW, "UTC"))).toContain("Posted Dec 30, 2025");
  });

  it("reads a date-only posting date as that calendar day", () => {
    expect(trailText(rowTrail(job({ posted_at: "2026-09-20" }), NOW, "UTC"))).toContain("Posted Sep 20");
  });

  it("tells a registry row the database calls fresh from one it does not: the stale one never claims the last poll", () => {
    const fresh = trailText(rowTrail(job({ provider: "registry", link_checked: true }), NOW, "UTC"));
    const stale = trailText(rowTrail(job({ provider: "registry", link_checked: false }), NOW, "UTC"));
    expect(fresh).toBe(`via registry · ${REGISTRY_BASIS} · Posted Sep 20`);
    expect(stale).toBe(`via registry · ${REGISTRY_STALE_BASIS} · Posted Sep 20`);
    expect(stale).not.toContain("at last poll");
    expect(findForbiddenWord(stale ?? "")).toBeNull();
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

  it("skips a sub-score outside 0 to 100 as it skips a missing one, and keeps the ends and a fraction", () => {
    const parts = { skills: 150, experience: -20, workauth: 0, location: 100, company_health: 33.5, compensation: Number.POSITIVE_INFINITY };
    const view = fitViewOf(job({ score: score({ sub_scores: parts as unknown as JobScore["sub_scores"], inapplicable_dims: [] }) }), "direct");
    if (view.kind !== "scored") throw new Error("expected a scored view");
    expect(view.bars.map((bar) => [bar.key, bar.value])).toEqual([["workauth", 0], ["location", 100], ["company_health", 33.5]]);
  });

  it("reads a row whose score is missing, not only null, as not scored, in the view and in the receipt", () => {
    // The API sends null, but the arranging code already counts an absent score as "Not scored", so the
    // row must be drawn that way too rather than throw on it.
    const absent = job({ score: undefined as unknown as null });
    expect(fitViewOf(absent, "direct")).toEqual({ kind: "unscored" });
    expect(() => rowReceipt(absent, "direct")).not.toThrow();
    expect(() => rowReceipt(absent, "neutral_fallback")).not.toThrow();
  });

  it("survives a score with no list of dropped dimensions", () => {
    const view = fitViewOf(job({ score: score({ inapplicable_dims: undefined as unknown as string[] }) }), "direct");
    expect(view.kind).toBe("scored");
  });

  it("is nothing at all for a bin the ladder does not know: the row is unscored, whatever else its score holds", () => {
    const bins = ["Excellent", "strong", " Strong", "Strong bj-primary", "", "constructor", "__proto__", "toString", null, undefined, 3, {}, ["Strong"]];
    for (const bin of bins) {
      const odd = job({ score: score({ bin: bin as never, score100: 77, one_liner: "A model line." }) });
      expect(fitViewOf(odd, "direct"), JSON.stringify(bin)).toEqual({ kind: "unscored" });
    }
  });

  it("agrees with the arranging code about every row: unscored exactly when the row has no tier", () => {
    const rows = [job({ score: null }), job({ score: undefined as unknown as null }), job({ score: score({ bin: "odd" as never }) }), job(), job({ score: score({ bin: "Poor" }) })];
    for (const row of rows) expect(fitViewOf(row, "direct").kind === "unscored", JSON.stringify(row.score?.bin)).toBe(isUnscored(row));
  });

  it("keeps a placeholder a placeholder under the fallback for a bin it knows, and calls an unreadable bin unscored there too", () => {
    expect(fitViewOf(job({ score: score({ bin: "Strong" }) }), "neutral_fallback")).toEqual({ kind: "placeholder" });
    expect(fitViewOf(job({ score: score({ bin: "Excellent" as never }) }), "neutral_fallback")).toEqual({ kind: "unscored" });
  });

  it("keeps a score outside 0 to 100 on a scored row, as the number alone is what is left out when it is drawn", () => {
    for (const score100 of [150, -5, Number.NaN]) {
      const view = fitViewOf(job({ score: score({ score100 }) }), "direct");
      expect(view.kind, String(score100)).toBe("scored");
    }
  });
});

describe("the number of the lowest part", () => {
  const scored = (overrides: Partial<JobScore>): ScoredFit => {
    const view = fitViewOf(job({ score: score(overrides) }), "direct");
    if (view.kind !== "scored") throw new Error("expected a scored view");
    return view;
  };

  it("is the sub-score of the part the response named, when that part was applied", () => {
    expect(scored({}).lowestValue).toBe(52);
    expect(scored({ bottleneck: "skills", sub_scores: { skills: 31, experience: 52, workauth: 60, location: 100, company_health: 60, compensation: 60 } }).lowestValue).toBe(31);
  });

  it("is a real zero, not a missing number", () => {
    expect(scored({ bottleneck: "skills", sub_scores: { skills: 0, experience: 52, workauth: 60, location: 100, company_health: 60, compensation: 60 } }).lowestValue).toBe(0);
  });

  it("is null when the lowest part was itself not applied", () => {
    const view = scored({ bottleneck: "compensation" });
    expect(view.lowest).toBe("Compensation");
    expect(view.lowestValue).toBeNull();
  });

  it("is null when no lowest part was named, whatever is in the sub-scores", () => {
    expect(scored({ bottleneck: null }).lowestValue).toBeNull();
    expect(scored({ bottleneck: "" }).lowestValue).toBeNull();
  });

  it("is null when the named part is outside 0 to 100, as for a part that is missing, and a real 100 or fraction stays", () => {
    const parts = (skills: number) => ({ skills, experience: 52, workauth: 60, location: 100, company_health: 60, compensation: 60 });
    for (const bad of [150, -20, 100.01, -0.5]) {
      const view = scored({ bottleneck: "skills", sub_scores: parts(bad) });
      expect(view.lowest, String(bad)).toBe("Skills");
      expect(view.lowestValue, String(bad)).toBeNull();
    }
    expect(scored({ bottleneck: "skills", sub_scores: parts(100) }).lowestValue).toBe(100);
    expect(scored({ bottleneck: "skills", sub_scores: parts(41.5) }).lowestValue).toBe(41.5);
  });

  it("is null when the named part has no usable number", () => {
    expect(scored({ bottleneck: "nonsense" }).lowestValue).toBeNull();
    expect(scored({ bottleneck: "skills", sub_scores: { skills: Number.NaN } as unknown as JobScore["sub_scores"] }).lowestValue).toBeNull();
    expect(scored({ bottleneck: "skills", sub_scores: { skills: "70" } as unknown as JobScore["sub_scores"] }).lowestValue).toBeNull();
    expect(scored({ sub_scores: null as unknown as JobScore["sub_scores"] }).lowestValue).toBeNull();
  });
});

describe("the one line a row says about what its score left out", () => {
  const preview = (overrides: Partial<JobScore>): string | null => {
    const view = fitViewOf(job({ score: score(overrides) }), "direct");
    if (view.kind !== "scored") throw new Error("expected a scored view");
    return fitPreviewOf(view);
  };
  const clean = { inapplicable_dims: [] as string[], location_match: "match" as const };

  it("names the lowest part with its number and how many parts were not applied", () => {
    expect(preview({})).toBe("Lowest: Experience 52 -- 2 parts not applied");
  });

  it("names the lowest part with its number alone when every part was applied and the location matches", () => {
    expect(preview(clean)).toBe("Lowest: Experience 52");
  });

  it("says one part, not one parts", () => {
    expect(preview({ ...clean, inapplicable_dims: ["compensation"] })).toBe("Lowest: Experience 52 -- 1 part not applied");
    expect(preview({ ...clean, bottleneck: null, inapplicable_dims: ["compensation"] })).toBe("1 part not applied");
  });

  it("says only the dropped parts when no lowest part was named", () => {
    expect(preview({ bottleneck: null })).toBe("2 parts not applied");
  });

  it("names a lowest part that was not applied without inventing a number for it", () => {
    expect(preview({ ...clean, bottleneck: "compensation", inapplicable_dims: ["compensation"] })).toBe("Lowest: Compensation -- 1 part not applied");
  });

  it("names a lowest part whose number is outside 0 to 100 without printing the number, as for a missing one", () => {
    const parts = (skills: number) => ({ skills, experience: 52, workauth: 60, location: 100, company_health: 60, compensation: 60 });
    expect(preview({ ...clean, bottleneck: "skills", sub_scores: parts(150) })).toBe("Lowest: Skills");
    expect(preview({ ...clean, bottleneck: "skills", sub_scores: parts(-20) })).toBe("Lowest: Skills");
    expect(preview({ bottleneck: "skills", sub_scores: parts(150) })).toBe("Lowest: Skills -- 2 parts not applied");
    expect(preview({ ...clean, bottleneck: "skills", sub_scores: parts(150), location_match: "mismatch" })).toBe("Lowest: Skills · Location doesn't match");
    expect(preview({ ...clean, bottleneck: "skills", sub_scores: parts(100) })).toBe("Lowest: Skills 100");
  });

  it("adds a location that does not match, or that the model could not place, and adds nothing for a match", () => {
    expect(preview({ ...clean, location_match: "mismatch" })).toBe("Lowest: Experience 52 · Location doesn't match");
    expect(preview({ ...clean, location_match: "unknown" })).toBe("Lowest: Experience 52 · Location: can't tell");
    expect(preview({ ...clean, location_match: "match" })).toBe("Lowest: Experience 52");
    expect(preview({ ...clean, location_match: "maybe" as never })).toBe("Lowest: Experience 52 · Location: can't tell");
  });

  it("joins all three clauses with a middle dot", () => {
    expect(preview({ location_match: "mismatch" })).toBe("Lowest: Experience 52 -- 2 parts not applied · Location doesn't match");
  });

  it("is a location clause alone when there is nothing else to say", () => {
    expect(preview({ ...clean, bottleneck: null, location_match: "mismatch" })).toBe("Location doesn't match");
  });

  it("is null for a clean score with nothing to say, never a filler", () => {
    expect(preview({ ...clean, bottleneck: null })).toBeNull();
  });

  it("does not repeat the place the model read the job to be, which the detail already says", () => {
    expect(preview({ ...clean, location_match: "mismatch", detected_location: "London" })).not.toContain("London");
  });

  it("never says a word the trail refuses and never uses an em dash", () => {
    for (const overrides of [{}, clean, { location_match: "mismatch" as const }, { location_match: "unknown" as const }, { bottleneck: null }]) {
      const line = preview(overrides) ?? "";
      expect(findForbiddenWord(line)).toBeNull();
      expect(line).not.toContain("\u2014");
    }
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

  it("words USAJobs' explicit_no as the provider's category fact, never as a text match", () => {
    expect(sponsorshipWords("explicit_no", "usajobs")).toBe("Federal roles generally require US work authorization");
    expect(sponsorshipTag("explicit_no", "usajobs")).toBe(SPONSORSHIP_PROVIDER_TAG);
    expect(sponsorshipWords("explicit_no", "serper")).toBe("The posting text rules out visa sponsorship");
    expect(sponsorshipTag("explicit_no", "serper")).toBe(SPONSORSHIP_TAG);
    expect(sponsorshipTag("explicit_yes", "usajobs")).toBe(SPONSORSHIP_TAG);
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

  it("tells a stale registry row's link check from a fresh one's, and words a USAJobs sponsorship by provider", () => {
    expect(value(job({ provider: "registry", link_checked: false }), "link check")).toContain("not confirmed on the company's board recently");
    expect(value(job({ provider: "registry", link_checked: true }), "link check")).not.toContain("not confirmed");
    expect(value(job({ provider: "usajobs", sponsorship_signal: "explicit_no" }), "sponsorship")).toBe("explicit_no (by provider, no text read)");
    expect(value(job({ provider: "serper", sponsorship_signal: "explicit_no" }), "sponsorship")).toBe("explicit_no (text match, no model)");
  });

  it("says a row past the liveness check was not checked yet", () => {
    expect(value(job({ link_checked: false }), "link check")).toBe("not checked yet");
  });

  it("holds the applicable sub-scores, what was not applied and the lowest part", () => {
    expect(value(job(), "sub-scores")).toBe("Skills 78 · Experience 52 · Work Authorization 60 · Location 100");
    expect(value(job(), "not applied")).toBe("Company Health, Compensation");
    expect(value(job(), "lowest part")).toBe("Experience");
  });

  it("leaves a sub-score outside 0 to 100 out of the receipt, as a missing one, and keeps the ends", () => {
    const parts = { skills: 150, experience: -20, workauth: 0, location: 100, company_health: 60, compensation: 60 };
    const card = job({ score: score({ sub_scores: parts as unknown as JobScore["sub_scores"], inapplicable_dims: [] }) });
    expect(value(card, "sub-scores")).toBe("Work Authorization 0 · Location 100 · Company Health 60 · Compensation 60");
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

  it("holds no score rows for a bin it cannot read either: that row is unscored, and quotes none of its reading", () => {
    const odd = job({ score: score({ bin: "Excellent" as never }) });
    for (const label of ["sub-scores", "not applied", "lowest part"]) expect(value(odd, label), label).toBeNull();
    expect(value(odd, "scoring", "neutral_fallback")).toBeNull();
    expect(value(odd, "location")).toBe("New York, NY, not marked remote");
    expect(value(odd, "provider")).toBe("serper");
  });

  it("says a location is not given rather than leave a gap", () => {
    expect(value(job({ location: null, remote: null, score: null }), "location")).toBe("none given");
  });
});
