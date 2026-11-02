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
  reordersByDate,
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

// A row whose bin nobody can read but which carries a number: it reads "Not scored", whatever the number.
function unreadable(title: string, score100 = 95, extra: Partial<JobCard> = {}): JobCard {
  return job({ title, apply_url: `https://jobs.example/${encodeURIComponent(title)}`, score: score({ bin: "Excellent" as ScoreBin, score100 }), ...extra });
}

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

  it("calls a row unscored when it has no score object, or a bin the ladder does not know", () => {
    expect(isUnscored(row("a", null))).toBe(true);
    expect(isUnscored(job({ score: undefined as unknown as null }))).toBe(true);
    expect(isUnscored(row("a", "Poor"))).toBe(false);
    for (const bin of ["odd", "strong", " Strong", "", "constructor", "__proto__", "toString", null, 3, {}, ["Strong"]]) {
      expect(isUnscored(job({ score: score({ bin: bin as unknown as ScoreBin }) })), JSON.stringify(bin)).toBe(true);
    }
  });

  it("agrees with the tier on every row: a row is unscored exactly when it has no tier", () => {
    const rows = [row("a", "Strong"), row("b", null), job({ score: score({ bin: "odd" as ScoreBin }) }), job({ score: undefined as unknown as null })];
    expect(rows.map((entry) => isUnscored(entry))).toEqual(rows.map((entry) => tierOf(entry) === null));
  });

  it("counts every row once under All, once under its tier, and unscored rows apart", () => {
    const counts = fitCounts([row("a", "Strong"), row("b", "Strong"), row("c", "Good"), row("d", "Poor"), row("e", null), row("f", null), row("g", null)]);
    expect(counts).toEqual({ all: 7, tiers: { Strong: 2, Good: 1, Mixed: 0, Poor: 1 }, unscored: 3 });
    expect(countFor(counts, "all")).toBe(7);
    expect(countFor(counts, "Strong")).toBe(2);
    expect(countFor(counts, "Mixed")).toBe(0);
    expect(countFor(counts, "unscored")).toBe(3);
  });

  it("counts a row with an unrecognised bin as not scored, so the groups always add up to All", () => {
    const counts = fitCounts([row("a", "Good"), job({ title: "odd", score: score({ bin: "Excellent" as ScoreBin }) }), row("c", null)]);
    expect(counts.all).toBe(3);
    expect(counts.tiers).toEqual({ Strong: 0, Good: 1, Mixed: 0, Poor: 0 });
    expect(counts.unscored).toBe(2);
    const rows = [row("a", "Good"), row("b", "Poor"), row("c", null), job({ title: "x", score: score({ bin: "?" as ScoreBin }) }), job({ title: "y", score: score({ bin: 7 as unknown as ScoreBin }) })];
    const all = fitCounts(rows);
    expect(Object.values(all.tiers).reduce((sum, n) => sum + n, 0) + all.unscored).toBe(all.all);
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
    // One row, or none, has nothing to be put before or after: neither Best fit nor Newest is on offer.
    expect(offeredSorts({ tiersMeaningful: true, scoredRows: [row("a", "Good", 70)] })).toEqual(["returned"]);
    expect(offeredSorts({ tiersMeaningful: true, scoredRows: [] })).toEqual(["returned"]);
  });

  it("offers Newest only when some section holds two rows with a date the trail can read", () => {
    const dated = (title: string, at: string | null) => row(title, "Good", 50, { posted_at: at });
    const none = [dated("a", null), dated("b", "not a date"), dated("c", "2026-02-31")];
    const one = [dated("a", "2026-09-01"), dated("b", null)];
    const two = [dated("a", "2026-09-01"), dated("b", "2026-09-02T08:00:00Z")];
    // Tiers are left out of meaning here so that Best fit is not on offer and only Newest is in question.
    const base = { tiersMeaningful: false, timeZone: "UTC" };
    expect(offeredSorts({ ...base, scoredRows: none })).toEqual(["returned"]);
    expect(offeredSorts({ ...base, scoredRows: one })).toEqual(["returned"]);
    expect(offeredSorts({ ...base, scoredRows: two })).toEqual(["returned", "newest"]);
    // One dated row in each section reorders neither section, so it is not enough.
    expect(offeredSorts({ ...base, scoredRows: one, moreRows: one })).toEqual(["returned"]);
    expect(offeredSorts({ ...base, scoredRows: none, moreRows: two })).toEqual(["returned", "newest"]);
    expect(offeredSorts({ ...base, scoredRows: [], moreRows: [] })).toEqual(["returned"]);
  });

  it("does not count a score that is not a real number", () => {
    const rows = [row("a", "Good", 70), row("b", "Good", Number.NaN), row("c", "Good", Number.POSITIVE_INFINITY), row("d", null)];
    expect(offeredSorts({ tiersMeaningful: true, scoredRows: rows })).toEqual(["returned", "newest"]);
    expect(offeredSorts({ tiersMeaningful: true, scoredRows: [...rows, row("e", "Good", 10)] })).toEqual(["returned", "fit", "newest"]);
  });

  it("does not count a score outside 0 to 100, which is no score, and counts the ends", () => {
    const rows = [row("a", "Strong", 70), row("b", "Strong", 150), row("c", "Poor", -5), row("d", "Poor", 100.5)];
    expect(offeredSorts({ tiersMeaningful: true, scoredRows: rows })).toEqual(["returned", "newest"]);
    expect(offeredSorts({ tiersMeaningful: true, scoredRows: [...rows, row("e", "Poor", 0)] })).toEqual(["returned", "fit", "newest"]);
    expect(offeredSorts({ tiersMeaningful: true, scoredRows: [row("a", "Poor", 0), row("b", "Strong", 100)] })).toEqual(["returned", "fit", "newest"]);
  });

  it("does not count a number on a row whose bin nobody can read: that row reads Not scored and has no rank", () => {
    const two = [row("a", "Strong", 80), unreadable("u")];
    expect(offeredSorts({ tiersMeaningful: true, scoredRows: two })).toEqual(["returned", "newest"]);
    expect(offeredSorts({ tiersMeaningful: true, scoredRows: [unreadable("u1"), unreadable("u2", 90)] })).toEqual(["returned", "newest"]);
    expect(offeredSorts({ tiersMeaningful: true, scoredRows: [...two, row("b", "Good", 60)] })).toEqual(["returned", "fit", "newest"]);
  });

  it("offers no Newest when the clock the rows print against is not a date, since no row then prints one", () => {
    const rows = [row("a", "Good", 70, { posted_at: "2026-09-01" }), row("b", "Good", 60, { posted_at: "2026-09-02" })];
    const base = { tiersMeaningful: false, scoredRows: rows, timeZone: "UTC" };
    expect(offeredSorts(base)).toEqual(["returned", "newest"]);
    expect(offeredSorts({ ...base, now: new Date("2026-10-18T12:00:00Z") })).toEqual(["returned", "newest"]);
    expect(offeredSorts({ ...base, now: new Date(Number.NaN) })).toEqual(["returned"]);
    expect(offeredSorts({ ...base, timeZone: "Not/AZone" })).toEqual(["returned"]);
  });

  it("falls back to As returned for a sort that is not offered", () => {
    expect(resolveSort("fit", ["returned", "newest"])).toBe("returned");
    expect(resolveSort("newest", ["returned", "newest"])).toBe("newest");
    expect(resolveSort("fit", [])).toBe("returned");
  });
});

describe("which section Newest reorders", () => {
  const dated = (title: string, at: string | null) => row(title, "Good", 50, { posted_at: at });

  it("is a section with at least two rows whose date the trail can read, and no other", () => {
    expect(reordersByDate([], "UTC")).toBe(false);
    expect(reordersByDate([dated("a", "2026-09-01")], "UTC")).toBe(false);
    expect(reordersByDate([dated("a", "2026-09-01"), dated("b", null), dated("c", "not a date"), dated("d", "2026-02-31")], "UTC")).toBe(false);
    expect(reordersByDate([dated("a", "2026-09-01"), dated("b", "2026-09-02")], "UTC")).toBe(true);
    expect(reordersByDate([dated("a", "2026-09-01"), dated("b", null), dated("c", "2026-09-03T08:00:00Z")], "UTC")).toBe(true);
  });

  it("reads each date as the trail does: not in a zone it does not know, and not against a clock that is not a date", () => {
    const two = [dated("a", "2026-09-01"), dated("b", "2026-09-02")];
    expect(reordersByDate(two, "Not/AZone")).toBe(false);
    expect(reordersByDate(two, "UTC", new Date(Number.NaN))).toBe(false);
    expect(reordersByDate(two, "UTC", new Date("2026-10-18T12:00:00Z"))).toBe(true);
  });

  it("is what arrange does: a section with fewer than two readable dates keeps the order it came in under Newest", () => {
    const one = [dated("a", null), dated("b", "2026-09-05"), dated("c", null)];
    const two = [dated("x", null), dated("y", "2026-09-01"), dated("z", "2026-09-09")];
    const out = arrange({ scored: one, more: two, filter: "all", sort: "newest", timeZone: "UTC" });
    expect(titles(out.scored)).toEqual(["a", "b", "c"]);
    expect(titles(out.more)).toEqual(["z", "y", "x"]);
    const swapped = arrange({ scored: two, more: one, filter: "all", sort: "newest", timeZone: "UTC" });
    expect(titles(swapped.scored)).toEqual(["z", "y", "x"]);
    expect(titles(swapped.more)).toEqual(["a", "b", "c"]);
  });

  it("leaves both sections as returned when neither has two readable dates", () => {
    const one = [dated("a", null), dated("b", "2026-09-05")];
    const out = arrange({ scored: one, more: [dated("c", "2026-09-09"), dated("d", null)], filter: "all", sort: "newest", timeZone: "UTC" });
    expect(titles(out.scored)).toEqual(["a", "b"]);
    expect(titles(out.more)).toEqual(["c", "d"]);
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

  it("puts a row whose bin nobody can read after every scored row, whatever number it carries", () => {
    const rows = [unreadable("u1", 95), row("a", "Strong", 80), unreadable("u2", 99), row("b", "Good", 60), row("none", null)];
    expect(titles(sortRows(rows, "fit"))).toEqual(["a", "b", "u1", "u2", "none"]);
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
    expect(titles(sortRows(rows, "newest", "UTC"))).toEqual(["offset", "new", "dateonly", "old"]);
  });

  it("orders by the day the row prints before the instant, so Sep 22 never sits below Sep 21 west of UTC", () => {
    const rows = [
      row("Older", "Good", 50, { posted_at: "2026-09-20" }),
      // 02:00 UTC on the 22nd is 22:00 on the 21st in New York, where the trail prints "Sep 21".
      row("Instant", "Good", 50, { posted_at: "2026-09-22T02:00:00Z" }),
      row("DateOnly", "Good", 50, { posted_at: "2026-09-22" }),
    ];
    expect(titles(sortRows(rows, "newest", "America/New_York"))).toEqual(["DateOnly", "Instant", "Older"]);
    // In UTC both print Sep 22, and the later instant leads within the day.
    expect(titles(sortRows(rows, "newest", "UTC"))).toEqual(["Instant", "DateOnly", "Older"]);
  });

  it("breaks a tie within one printed day by the later instant, then by the order returned", () => {
    const rows = [
      row("early", "Good", 50, { posted_at: "2026-09-22T09:00:00Z" }),
      row("late", "Good", 50, { posted_at: "2026-09-22T20:00:00Z" }),
      row("same", "Good", 50, { posted_at: "2026-09-22T20:00:00Z" }),
    ];
    expect(titles(sortRows(rows, "newest", "UTC"))).toEqual(["late", "same", "early"]);
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

  it("leaves a More results row where it was returned under Best fit, even one that carries a score", () => {
    const withScores = [row("m1", "Poor", 10), row("m2", "Strong", 95), row("m3", "Good", 60)];
    const out = arrange({ scored, more: withScores, filter: "all", sort: "fit" });
    expect(titles(out.more)).toEqual(["m1", "m2", "m3"]);
    expect(titles(out.scored)).toEqual(["s1", "s2", "g1", "m1", "p1"]);
  });

  it("orders each section by the printed day for Newest when given a zone", () => {
    const rows = [row("a", null, 0, { posted_at: "2026-09-22T02:00:00Z" }), row("b", null, 0, { posted_at: "2026-09-22" })];
    expect(titles(arrange({ scored: [], more: rows, filter: "all", sort: "newest", timeZone: "America/New_York" }).more)).toEqual(["b", "a"]);
    expect(titles(arrange({ scored: [], more: rows, filter: "all", sort: "newest", timeZone: "UTC" }).more)).toEqual(["a", "b"]);
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

  it("shows a row with an unrecognised bin under All and under Not scored, and under no tier", () => {
    const odd = job({ title: "odd", apply_url: "https://jobs.example/odd", score: score({ bin: "Excellent" as ScoreBin }) });
    const rows = [row("a", "Good"), odd];
    expect(titles(arrange({ scored: rows, more: [], filter: "all", sort: "returned" }).scored)).toEqual(["a", "odd"]);
    expect(titles(arrange({ scored: rows, more: [], filter: "unscored", sort: "returned" }).scored)).toEqual(["odd"]);
    for (const filter of TIER_ORDER) {
      expect(titles(arrange({ scored: rows, more: [], filter, sort: "returned" }).scored)).not.toContain("odd");
    }
  });

  it("keeps each chip's count and the rows its filter keeps the same number, for rows that all read one way or another", () => {
    const odd = job({ title: "odd", apply_url: "https://jobs.example/odd", score: score({ bin: "Excellent" as ScoreBin }) });
    const scoredRows = [row("a", "Good"), row("b", "Strong"), odd];
    const moreRows = [row("u1", null), job({ title: "odd2", apply_url: "https://jobs.example/odd2", score: score({ bin: 9 as unknown as ScoreBin }) })];
    const counts = fitCounts([...scoredRows, ...moreRows]);
    for (const filter of ["all", ...TIER_ORDER, "unscored"] as const) {
      const out = arrange({ scored: scoredRows, more: moreRows, filter, sort: "returned" });
      expect(out.scored.length + out.more.length, filter).toBe(countFor(counts, filter));
    }
  });

  it("ranks no row by a score outside 0 to 100: Best fit puts it with the rows that have none", () => {
    const rows = [row("high", "Strong", 150), row("ok", "Good", 60), row("low", "Poor", -5), row("top", "Strong", 100), row("zero", "Poor", 0)];
    expect(titles(sortRows(rows, "fit"))).toEqual(["top", "ok", "zero", "high", "low"]);
    expect(titles(arrange({ scored: rows, more: [], filter: "all", sort: "fit" }).scored)).toEqual(["top", "ok", "zero", "high", "low"]);
  });

  it("ranks no row that reads Not scored: Best fit puts a row with an unreadable bin after the scored ones", () => {
    const rows = [unreadable("u", 95), row("a", "Strong", 80), row("b", "Good", 60)];
    expect(titles(arrange({ scored: rows, more: [], filter: "all", sort: "fit" }).scored)).toEqual(["a", "b", "u"]);
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

  it("adds the sort that replaced the one shown to a filter's announcement, and only then", () => {
    const base = { kind: "filter" as const, filter: "unscored" as const, shown: 7, total: 25, hiddenUnscored: 0 };
    expect(arrangeAnnouncement({ ...base, sort: "returned" })).toBe("Showing 7 of 25 results, not scored only. Sorted as the search returned them.");
    expect(arrangeAnnouncement({ ...base, sort: "newest" })).toBe("Showing 7 of 25 results, not scored only. Sorted by newest first.");
    expect(arrangeAnnouncement({ kind: "filter", filter: "all", shown: 25, total: 25, hiddenUnscored: 0, sort: "returned" })).toBe(
      "Showing all 25 results. Sorted as the search returned them.",
    );
    expect(arrangeAnnouncement(base)).toBe("Showing 7 of 25 results, not scored only.");
  });

  it("announces each sort", () => {
    expect(arrangeAnnouncement({ kind: "sort", sort: "returned" })).toBe("Sorted as the search returned them.");
    expect(arrangeAnnouncement({ kind: "sort", sort: "fit" })).toBe("Sorted by best fit first.");
    expect(arrangeAnnouncement({ kind: "sort", sort: "newest" })).toBe("Sorted by newest first.");
  });

  it("names the section that kept its order when Newest did not move every section", () => {
    const newest = (keptOrder: readonly ("scored" | "more")[]) => arrangeAnnouncement({ kind: "sort", sort: "newest", keptOrder });
    expect(newest([])).toBe("Sorted by newest first.");
    expect(newest(["more"])).toBe("Sorted by newest first. More results keep the search's order.");
    expect(newest(["scored"])).toBe("Sorted by newest first. The rows above More results keep the search's order.");
    // Nothing was moved, so no newest-first order is claimed at all.
    expect(newest(["scored", "more"])).toBe("Sorted as the search returned them.");
  });

  it("says nothing about kept sections for a sort that is not Newest", () => {
    expect(arrangeAnnouncement({ kind: "sort", sort: "fit", keptOrder: ["more"] })).toBe("Sorted by best fit first.");
    expect(arrangeAnnouncement({ kind: "sort", sort: "returned", keptOrder: ["more"] })).toBe("Sorted as the search returned them.");
  });

  it("uses no em dash and not the word verified", () => {
    const filters: FitFilter[] = ["all", "Strong", "Good", "Mixed", "Poor", "unscored"];
    const lines = [
      ...filters.map((filter) => arrangeAnnouncement({ kind: "filter", filter, shown: 2, total: 9, hiddenUnscored: 3 })),
      ...(["returned", "fit", "newest"] as const).map((sort) => arrangeAnnouncement({ kind: "sort", sort })),
      ...([["more"], ["scored"], ["scored", "more"]] as const).map((keptOrder) => arrangeAnnouncement({ kind: "sort", sort: "newest", keptOrder })),
      ...Object.values(FILTER_LABEL),
      ...Object.values(SORT_LABEL),
    ];
    for (const line of lines) {
      expect(line).not.toContain("\u2014");
      expect(line.toLowerCase()).not.toContain("verified");
    }
  });
});
