import { describe, expect, it } from "vitest";
import { job, score } from "../testing/discoverFixtures";
import {
  FILTER_LABEL,
  SORT_LABEL,
  TIER_ORDER,
  TOOLBAR_MIN_ROWS,
  arrange,
  arrangeAnnouncement,
  countFor,
  fitCounts,
  filterSummary,
  isUnscored,
  offeredFilters,
  offeredSorts,
  resolveFilter,
  resolveSort,
  sortRows,
  tierOf,
  type FitFilter,
} from "./discoverArrange";
import type { JobCard, ScoreBin } from "./discoverTypes";
import { FIT_LEVELS } from "./discoverRow";

// Narrowing and ordering a page of results: pure, so each rule is a table. Rows are identified by title.

function row(title: string, bin: ScoreBin | null, score100 = 50, extra: Partial<JobCard> = {}): JobCard {
  return job({
    title,
    apply_url: `https://jobs.example/${encodeURIComponent(title)}`,
    score: bin === null ? null : score({ bin, score100 }),
    ...extra,
  });
}

const titles = (rows: readonly JobCard[]) => rows.map((entry) => entry.title);

describe("tiers and counts", () => {
  it("lists the tiers best first, which is the ladder reversed", () => {
    expect(TIER_ORDER).toEqual(["Strong", "Good", "Mixed", "Poor"]);
    expect([...TIER_ORDER].reverse()).toEqual([...FIT_LEVELS]);
    expect(TOOLBAR_MIN_ROWS).toBe(3);
  });

  it("reads a row's tier from its bin, and nothing from a row with no score or a bin it does not know", () => {
    expect(tierOf(row("a", "Strong"))).toBe("Strong");
    expect(tierOf(row("a", "Poor"))).toBe("Poor");
    expect(tierOf(row("a", null))).toBeNull();
    expect(tierOf(job({ score: undefined as unknown as null }))).toBeNull();
    expect(tierOf(job({ score: score({ bin: "Strong bj-primary" as ScoreBin }) }))).toBeNull();
    expect(tierOf(job({ score: score({ bin: "strong" as ScoreBin }) }))).toBeNull();
  });

  it("calls a row unscored only when it has no score object", () => {
    expect(isUnscored(row("a", null))).toBe(true);
    expect(isUnscored(job({ score: undefined as unknown as null }))).toBe(true);
    expect(isUnscored(row("a", "Poor"))).toBe(false);
    expect(isUnscored(job({ score: score({ bin: "odd" as ScoreBin }) }))).toBe(false);
  });

  it("counts every row once under All, once under its tier, and unscored rows apart", () => {
    const counts = fitCounts([row("a", "Strong"), row("b", "Strong"), row("c", "Good"), row("d", "Poor"), row("e", null), row("f", null), row("g", null)]);
    expect(counts).toEqual({ all: 7, tiers: { Strong: 2, Good: 1, Mixed: 0, Poor: 1 }, unscored: 3 });
    expect(countFor(counts, "all")).toBe(7);
    expect(countFor(counts, "Strong")).toBe(2);
    expect(countFor(counts, "Mixed")).toBe(0);
    expect(countFor(counts, "unscored")).toBe(3);
  });

  it("counts a row with an unrecognised bin in All only, so the groups need not add up to All", () => {
    const counts = fitCounts([row("a", "Good"), job({ title: "odd", score: score({ bin: "Excellent" as ScoreBin }) }), row("c", null)]);
    expect(counts.all).toBe(3);
    expect(counts.tiers).toEqual({ Strong: 0, Good: 1, Mixed: 0, Poor: 0 });
    expect(counts.unscored).toBe(1);
  });

  it("counts nothing for no rows", () => {
    expect(fitCounts([])).toEqual({ all: 0, tiers: { Strong: 0, Good: 0, Mixed: 0, Poor: 0 }, unscored: 0 });
  });
});

describe("which filters are offered", () => {
  const counts = (tiers: Partial<Record<ScoreBin, number>>, unscored = 0) => ({
    all: Object.values(tiers).reduce((sum, n) => sum + n, 0) + unscored,
    tiers: { Strong: 0, Good: 0, Mixed: 0, Poor: 0, ...tiers },
    unscored,
  });

  it("offers nothing when fewer than two groups hold rows", () => {
    expect(offeredFilters(counts({}), true)).toEqual([]);
    expect(offeredFilters(counts({ Good: 5 }), true)).toEqual([]);
    expect(offeredFilters(counts({}, 4), true)).toEqual([]);
  });

  it("offers All, the non-empty tiers best first, then Not scored, once there are two groups", () => {
    expect(offeredFilters(counts({ Good: 2, Poor: 1 }), true)).toEqual(["all", "Good", "Poor"]);
    expect(offeredFilters(counts({ Poor: 1, Strong: 3, Mixed: 2 }, 4), true)).toEqual(["all", "Strong", "Mixed", "Poor", "unscored"]);
    expect(offeredFilters(counts({ Good: 2 }, 1), true)).toEqual(["all", "Good", "unscored"]);
  });

  it("offers no group when the tiers mean nothing, whatever the rows hold", () => {
    expect(offeredFilters(counts({ Good: 2, Poor: 1 }), false)).toEqual([]);
    expect(offeredFilters(counts({ Good: 2 }, 3), false)).toEqual([]);
  });

  it("falls back to All for a choice that is not offered", () => {
    expect(resolveFilter("Poor", ["all", "Strong", "Good"])).toBe("all");
    expect(resolveFilter("Strong", ["all", "Strong", "Good"])).toBe("Strong");
    expect(resolveFilter("unscored", [])).toBe("all");
    expect(resolveFilter("all", [])).toBe("all");
  });

  it("names every filter and every sort in the words the toolbar prints", () => {
    expect(FILTER_LABEL).toEqual({ all: "All", Strong: "Strong", Good: "Good", Mixed: "Mixed", Poor: "Poor", unscored: "Not scored" });
    expect(SORT_LABEL).toEqual({ returned: "As returned", fit: "Best fit", newest: "Newest" });
  });
});

describe("which sorts are offered", () => {
  it("offers Best fit only with meaningful tiers and two rows that carry a number", () => {
    const two = [row("a", "Good", 70), row("b", "Poor", 20)];
    expect(offeredSorts({ tiersMeaningful: true, scoredRows: two })).toEqual(["returned", "fit", "newest"]);
    expect(offeredSorts({ tiersMeaningful: false, scoredRows: two })).toEqual(["returned", "newest"]);
    expect(offeredSorts({ tiersMeaningful: true, scoredRows: [row("a", "Good", 70)] })).toEqual(["returned", "newest"]);
    expect(offeredSorts({ tiersMeaningful: true, scoredRows: [] })).toEqual(["returned", "newest"]);
  });

  it("does not count a score that is not a real number", () => {
    const rows = [row("a", "Good", 70), row("b", "Good", Number.NaN), row("c", "Good", Number.POSITIVE_INFINITY), row("d", null)];
    expect(offeredSorts({ tiersMeaningful: true, scoredRows: rows })).toEqual(["returned", "newest"]);
    expect(offeredSorts({ tiersMeaningful: true, scoredRows: [...rows, row("e", "Good", 10)] })).toEqual(["returned", "fit", "newest"]);
  });

  it("falls back to As returned for a sort that is not offered", () => {
    expect(resolveSort("fit", ["returned", "newest"])).toBe("returned");
    expect(resolveSort("newest", ["returned", "newest"])).toBe("newest");
    expect(resolveSort("fit", [])).toBe("returned");
  });
});

describe("sorting one section", () => {
  it("returns a copy in the order given for As returned, and for nothing", () => {
    const rows = [row("a", "Good", 10), row("b", "Good", 90)];
    const copy = sortRows(rows, "returned");
    expect(titles(copy)).toEqual(["a", "b"]);
    expect(copy).not.toBe(rows);
    expect(sortRows([], "fit")).toEqual([]);
    expect(sortRows([], "newest")).toEqual([]);
    expect(titles(sortRows([row("only", "Good")], "fit"))).toEqual(["only"]);
  });

  it("puts the highest score first for Best fit and keeps ties in the order returned", () => {
    const rows = [row("a", "Good", 61), row("b", "Strong", 88), row("c", "Good", 61), row("d", "Strong", 88), row("e", "Poor", 12)];
    expect(titles(sortRows(rows, "fit"))).toEqual(["b", "d", "a", "c", "e"]);
  });

  it("puts rows with no real score last under Best fit, in the order returned", () => {
    const rows = [row("u1", null), row("a", "Good", 50), row("n1", "Good", Number.NaN), row("b", "Good", 70), row("u2", null), row("i1", "Good", Number.POSITIVE_INFINITY)];
    expect(titles(sortRows(rows, "fit"))).toEqual(["b", "a", "u1", "n1", "u2", "i1"]);
  });

  it("treats a tie of every row as no reordering", () => {
    const rows = ["a", "b", "c", "d", "e"].map((title) => row(title, "Good", 40));
    expect(titles(sortRows(rows, "fit"))).toEqual(["a", "b", "c", "d", "e"]);
    const undated = ["a", "b", "c"].map((title) => row(title, "Good", 40, { posted_at: null }));
    expect(titles(sortRows(undated, "newest"))).toEqual(["a", "b", "c"]);
  });

  it("puts the latest posting first for Newest, mixing date-only values and instants", () => {
    const rows = [
      row("old", "Good", 50, { posted_at: "2026-08-01" }),
      row("new", "Good", 50, { posted_at: "2026-09-22T08:00:00Z" }),
      row("dateonly", "Good", 50, { posted_at: "2026-09-22" }),
      row("offset", "Good", 50, { posted_at: "2026-09-22T12:00:00+02:00" }),
    ];
    expect(titles(sortRows(rows, "newest"))).toEqual(["offset", "new", "dateonly", "old"]);
  });

  it("puts a missing, empty or unreadable date last, in the order returned", () => {
    const rows = [
      row("none", "Good", 50, { posted_at: null }),
      row("dated", "Good", 50, { posted_at: "2026-09-01" }),
      row("empty", "Good", 50, { posted_at: "" }),
      row("text", "Good", 50, { posted_at: "not a date" }),
      row("later", "Good", 50, { posted_at: "2026-09-10T00:00:00Z" }),
      row("absent", "Good", 50, { posted_at: undefined as unknown as null }),
    ];
    expect(titles(sortRows(rows, "newest"))).toEqual(["later", "dated", "none", "empty", "text", "absent"]);
  });

  it("sorts by the date the row prints: an impossible day is undated, a date-only day is read as written", () => {
    const rows = [
      row("impossible", "Good", 50, { posted_at: "2026-02-31" }),
      row("feb", "Good", 50, { posted_at: "2026-02-27" }),
      row("impossibleInstant", "Good", 50, { posted_at: "2026-02-31T10:00:00Z" }),
      row("mar", "Good", 50, { posted_at: "2026-03-01" }),
    ];
    expect(titles(sortRows(rows, "newest"))).toEqual(["mar", "feb", "impossible", "impossibleInstant"]);
  });

  it("keeps equal times in the order returned", () => {
    const rows = [
      row("a", "Good", 50, { posted_at: "2026-09-22T08:00:00Z" }),
      row("b", "Good", 50, { posted_at: "2026-09-22T10:00:00+02:00" }),
      row("c", "Good", 50, { posted_at: "2026-09-23" }),
      row("d", "Good", 50, { posted_at: "2026-09-22T08:00:00.000Z" }),
    ];
    expect(titles(sortRows(rows, "newest"))).toEqual(["c", "a", "b", "d"]);
  });

  it("never mutates its input and never hands it back", () => {
    const rows = [row("a", "Good", 10), row("b", "Good", 90), row("c", "Good", 50)];
    const frozen = Object.freeze([...rows]);
    for (const mode of ["returned", "fit", "newest"] as const) {
      const out = sortRows(frozen, mode);
      expect(out).not.toBe(frozen);
      expect(titles(frozen)).toEqual(["a", "b", "c"]);
    }
  });

  it("is a stable permutation over a hundred rows with many ties", () => {
    const rows = Array.from({ length: 100 }, (_, index) =>
      row(`r${String(index).padStart(3, "0")}`, "Good", (index * 7) % 5, {
        posted_at: index % 4 === 0 ? null : `2026-09-${String(1 + (index % 3)).padStart(2, "0")}`,
      }),
    );
    for (const mode of ["fit", "newest"] as const) {
      const out = sortRows(rows, mode);
      expect(out).toHaveLength(100);
      expect(new Set(out).size).toBe(100);
      expect([...titles(out)].sort()).toEqual(titles(rows));
      // Within a run of equal keys the order the search returned survives.
      const key = (entry: JobCard) => (mode === "fit" ? entry.score?.score100 : entry.posted_at);
      for (let at = 1; at < out.length; at++) {
        if (key(out[at - 1]) === key(out[at])) expect(titles([out[at - 1]])[0] < titles([out[at]])[0]).toBe(true);
      }
    }
  });
});

describe("arranging both sections", () => {
  const scored = [row("g1", "Good", 60), row("s1", "Strong", 90), row("p1", "Poor", 10), row("s2", "Strong", 80), row("m1", "Mixed", 40)];
  const more = [row("u1", null, 0, { posted_at: "2026-09-01" }), row("u2", null, 0, { posted_at: "2026-09-20" }), row("u3", null, 0, { posted_at: null })];

  it("returns copies in the order given for All and As returned", () => {
    const out = arrange({ scored, more, filter: "all", sort: "returned" });
    expect(titles(out.scored)).toEqual(titles(scored));
    expect(titles(out.more)).toEqual(titles(more));
    expect(out.scored).not.toBe(scored);
    expect(out.more).not.toBe(more);
  });

  it("returns empty sections for no rows", () => {
    expect(arrange({ scored: [], more: [], filter: "all", sort: "fit" })).toEqual({ scored: [], more: [] });
    expect(arrange({ scored: [], more: [], filter: "Strong", sort: "newest" })).toEqual({ scored: [], more: [] });
  });

  it("keeps one tier and hides the rows past the scoring batch", () => {
    const out = arrange({ scored, more, filter: "Strong", sort: "returned" });
    expect(titles(out.scored)).toEqual(["s1", "s2"]);
    expect(out.more).toEqual([]);
  });

  it("keeps a tier by section membership, and finds a row of that tier in either section", () => {
    const stray = row("stray", "Good", 30);
    const out = arrange({ scored, more: [...more, stray], filter: "Good", sort: "returned" });
    expect(titles(out.scored)).toEqual(["g1"]);
    expect(titles(out.more)).toEqual(["stray"]);
  });

  it("keeps the unscored rows of both sections for Not scored, even one sitting in the scored section", () => {
    const hole = row("hole", null);
    const out = arrange({ scored: [...scored, hole], more, filter: "unscored", sort: "returned" });
    expect(titles(out.scored)).toEqual(["hole"]);
    expect(titles(out.more)).toEqual(["u1", "u2", "u3"]);
  });

  it("returns empty sections for a tier nobody holds, though the toolbar never offers it", () => {
    expect(arrange({ scored: [row("a", "Good")], more: [], filter: "Poor", sort: "returned" })).toEqual({ scored: [], more: [] });
  });

  it("shows a row with an unrecognised bin under All only", () => {
    const odd = job({ title: "odd", apply_url: "https://jobs.example/odd", score: score({ bin: "Excellent" as ScoreBin }) });
    const rows = [row("a", "Good"), odd];
    expect(titles(arrange({ scored: rows, more: [], filter: "all", sort: "returned" }).scored)).toEqual(["a", "odd"]);
    for (const filter of [...TIER_ORDER, "unscored"] as const) {
      expect(titles(arrange({ scored: rows, more: [], filter, sort: "returned" }).scored)).not.toContain("odd");
    }
  });

  it("sorts the sections apart: Best fit orders scored rows and leaves More results as returned", () => {
    const out = arrange({ scored, more, filter: "all", sort: "fit" });
    expect(titles(out.scored)).toEqual(["s1", "s2", "g1", "m1", "p1"]);
    expect(titles(out.more)).toEqual(["u1", "u2", "u3"]);
  });

  it("sorts the sections apart: Newest orders each by date and never merges them", () => {
    const dated = [row("a", "Good", 50, { posted_at: "2026-09-01" }), row("b", "Good", 50, { posted_at: "2026-09-21" })];
    const out = arrange({ scored: dated, more, filter: "all", sort: "newest" });
    expect(titles(out.scored)).toEqual(["b", "a"]);
    expect(titles(out.more)).toEqual(["u2", "u1", "u3"]);
  });

  it("does not mutate the sections it is given", () => {
    const before = [titles(scored), titles(more)];
    arrange({ scored, more, filter: "Strong", sort: "fit" });
    arrange({ scored, more, filter: "all", sort: "newest" });
    expect([titles(scored), titles(more)]).toEqual(before);
  });
});

describe("the invariant: every filter the toolbar offers keeps at least one row", () => {
  const table: { name: string; scored: JobCard[]; more: JobCard[]; tiersMeaningful: boolean }[] = [
    {
      name: "mixed tiers and unscored rows",
      scored: [row("s", "Strong", 90), row("g", "Good", 70), row("m", "Mixed", 45), row("p", "Poor", 10)],
      more: [row("u1", null), row("u2", null)],
      tiersMeaningful: true,
    },
    { name: "one tier and unscored rows", scored: [row("g1", "Good", 70), row("g2", "Good", 65)], more: [row("u1", null)], tiersMeaningful: true },
    { name: "unscored only", scored: [], more: [row("u1", null), row("u2", null), row("u3", null)], tiersMeaningful: true },
    { name: "the scoring fallback", scored: [row("a", "Good"), row("b", "Poor")], more: [row("u1", null)], tiersMeaningful: false },
    {
      name: "every bin unknown",
      scored: [job({ title: "x", score: score({ bin: "?" as ScoreBin }) }), job({ title: "y", score: score({ bin: "??" as ScoreBin }) })],
      more: [],
      tiersMeaningful: true,
    },
    { name: "two tiers, nothing past the batch", scored: [row("s", "Strong", 90), row("p", "Poor", 10), row("p2", "Poor", 9)], more: [], tiersMeaningful: true },
    { name: "an unscored row inside the scored section", scored: [row("g", "Good"), row("hole", null)], more: [], tiersMeaningful: true },
  ];

  it.each(table)("holds for $name", ({ scored, more, tiersMeaningful }) => {
    const counts = fitCounts([...scored, ...more]);
    const offered = offeredFilters(counts, tiersMeaningful);
    for (const filter of offered) {
      const out = arrange({ scored, more, filter, sort: "returned" });
      const shown = out.scored.length + out.more.length;
      expect(shown).toBeGreaterThan(0);
      expect(shown).toBe(countFor(counts, filter));
    }
  });
});

describe("the sentences", () => {
  it("has no count line for All", () => {
    expect(filterSummary("all", 25, 25, 0)).toBeNull();
  });

  it("says how many of the total a tier keeps, and how many unscored rows that leaves out", () => {
    expect(filterSummary("Strong", 6, 25, 7)).toBe("Showing 6 of 25 results, Strong fit only. 7 not scored are not shown.");
    expect(filterSummary("Poor", 3, 25, 1)).toBe("Showing 3 of 25 results, Poor fit only. 1 not scored is not shown.");
    expect(filterSummary("Mixed", 3, 18, 0)).toBe("Showing 3 of 18 results, Mixed fit only.");
    expect(filterSummary("Good", 1, 4, 0)).toBe("Showing 1 of 4 results, Good fit only.");
  });

  it("says Not scored only", () => {
    expect(filterSummary("unscored", 7, 25, 0)).toBe("Showing 7 of 25 results, not scored only.");
    expect(filterSummary("unscored", 1, 3, 0)).toBe("Showing 1 of 3 results, not scored only.");
  });

  it("announces each filter in the count line's words, and All in its own", () => {
    expect(arrangeAnnouncement({ kind: "filter", filter: "all", shown: 25, total: 25, hiddenUnscored: 0 })).toBe("Showing all 25 results.");
    expect(arrangeAnnouncement({ kind: "filter", filter: "Strong", shown: 6, total: 25, hiddenUnscored: 7 })).toBe(
      "Showing 6 of 25 results, Strong fit only. 7 not scored are not shown.",
    );
    expect(arrangeAnnouncement({ kind: "filter", filter: "unscored", shown: 7, total: 25, hiddenUnscored: 0 })).toBe("Showing 7 of 25 results, not scored only.");
    expect(arrangeAnnouncement({ kind: "filter", filter: "all", shown: 1, total: 1, hiddenUnscored: 0 })).toBe("Showing all 1 result.");
  });

  it("announces each sort", () => {
    expect(arrangeAnnouncement({ kind: "sort", sort: "returned" })).toBe("Sorted as the search returned them.");
    expect(arrangeAnnouncement({ kind: "sort", sort: "fit" })).toBe("Sorted by best fit first.");
    expect(arrangeAnnouncement({ kind: "sort", sort: "newest" })).toBe("Sorted by newest first.");
  });

  it("uses no em dash and not the word verified", () => {
    const filters: FitFilter[] = ["all", "Strong", "Good", "Mixed", "Poor", "unscored"];
    const lines = [
      ...filters.map((filter) => arrangeAnnouncement({ kind: "filter", filter, shown: 2, total: 9, hiddenUnscored: 3 })),
      ...(["returned", "fit", "newest"] as const).map((sort) => arrangeAnnouncement({ kind: "sort", sort })),
      ...Object.values(FILTER_LABEL),
      ...Object.values(SORT_LABEL),
    ];
    for (const line of lines) {
      expect(line).not.toContain("\u2014");
      expect(line.toLowerCase()).not.toContain("verified");
    }
  });
});
