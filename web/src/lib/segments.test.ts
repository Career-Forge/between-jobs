import { describe, expect, it } from "vitest";
import { COARSE_SEGMENTS, defaultSegments, filledSegments, hatchedSegments } from "./segments";

// Small deterministic generator, so the property checks below are the same run to run.
function sequence(seed: number): () => number {
  let state = seed;
  return () => {
    state = (state * 1664525 + 1013904223) % 4294967296;
    return state / 4294967296;
  };
}

describe("defaultSegments", () => {
  it("is one cell per point up to 35, and twenty past that or for a maximum that is not a whole number", () => {
    expect([1, 10, 15, 30, 35].map(defaultSegments)).toEqual([1, 10, 15, 30, 35]);
    expect([36, 100, 10.5, 0, -3, Number.NaN].map(defaultSegments)).toEqual(Array(6).fill(COARSE_SEGMENTS));
  });
});

describe("filledSegments", () => {
  it("is the value itself when there is one cell per point (the ATS rows: 27 of 35, 22 of 30, 12 of 15, 6 of 10, 5 of 10)", () => {
    for (const [value, max] of [[27, 35], [22, 30], [12, 15], [6, 10], [5, 10]]) {
      expect(filledSegments(value, max, max)).toBe(value);
    }
  });

  it("holds for every whole value of every maximum from 1 to 35", () => {
    for (let max = 1; max <= 35; max++) {
      for (let value = 0; value <= max; value++) expect(filledSegments(value, max, max)).toBe(value);
    }
  });

  it("fills nothing for zero, a negative number or an unknown value, and everything at or past the maximum", () => {
    for (const value of [0, -5, null, undefined, Number.NaN, Number.NEGATIVE_INFINITY]) {
      expect(filledSegments(value, 35, 35)).toBe(0);
    }
    expect(filledSegments(35, 35, 20)).toBe(20);
    expect(filledSegments(80, 35, 20)).toBe(20);
    expect(filledSegments(Number.POSITIVE_INFINITY, 35, 20)).toBe(0);
  });

  it("fills nothing when the maximum or the cell count cannot be used", () => {
    for (const [max, cells] of [[0, 10], [-1, 10], [Number.NaN, 10], [10, 0], [10, 2.5], [10, -1]]) {
      expect(filledSegments(5, max, cells)).toBe(0);
    }
  });

  it("never rounds a value between 0 and the maximum to an empty bar or to a full one", () => {
    expect(filledSegments(1, 100, 20)).toBe(1);
    expect(filledSegments(0.01, 100, 20)).toBe(1);
    expect(filledSegments(99, 100, 20)).toBe(19);
    expect(filledSegments(99.9, 100, 4)).toBe(3);
  });

  it("is a four-cell ladder at 4 cells: a score of 61 / 100 fills 2 of 4, an exact quarter step fills one", () => {
    expect(filledSegments(61, 100, 4)).toBe(2);
    expect(filledSegments(25, 100, 4)).toBe(1);
    expect(filledSegments(75, 100, 4)).toBe(3);
  });

  it("is monotone, bounded, and consistent with the full-and-empty rules, over a few thousand random inputs", () => {
    const next = sequence(7);
    for (let run = 0; run < 3000; run++) {
      const max = 1 + Math.floor(next() * 120);
      const segments = 1 + Math.floor(next() * 40);
      const a = next() * max * 1.2;
      const b = next() * max * 1.2;
      const [low, high] = a <= b ? [a, b] : [b, a];
      const filledLow = filledSegments(low, max, segments);
      const filledHigh = filledSegments(high, max, segments);
      expect(filledLow).toBeLessThanOrEqual(filledHigh);
      expect(filledLow).toBeGreaterThanOrEqual(0);
      expect(filledHigh).toBeLessThanOrEqual(segments);
      if (low >= max) expect(filledLow).toBe(segments);
      if (low > 0 && low < max && segments >= 2) {
        expect(filledLow).toBeGreaterThanOrEqual(1);
        expect(filledLow).toBeLessThanOrEqual(segments - 1);
      }
    }
  });
});

describe("hatchedSegments", () => {
  it("is nothing without headroom, with unusable headroom, or once the bar is full", () => {
    for (const headroom of [0, -2, null, undefined, Number.NaN]) expect(hatchedSegments(20, headroom, 30, 30)).toBe(0);
    expect(hatchedSegments(30, 5, 30, 30)).toBe(0);
    expect(hatchedSegments(Number.NaN, 5, 30, 30)).toBe(0);
  });

  it("is the headroom in cells after the filled ones when there is one cell per point (18 of 30 plus 4 exact)", () => {
    expect(hatchedSegments(18, 4, 30, 30)).toBe(4);
  });

  it("is capped by the room left, so filled and hatched never pass the bar", () => {
    expect(hatchedSegments(28, 10, 30, 30)).toBe(2);
  });

  it("is at least one cell for any headroom at all while there is room, so it is never invisible", () => {
    expect(hatchedSegments(10, 0.1, 100, 20)).toBe(1);
  });

  it("keeps filled plus hatched inside the bar over random inputs", () => {
    const next = sequence(11);
    for (let run = 0; run < 3000; run++) {
      const max = 1 + Math.floor(next() * 100);
      const segments = 1 + Math.floor(next() * 40);
      const value = next() * max * 1.1;
      const headroom = next() * max;
      const filled = filledSegments(value, max, segments);
      const hatched = hatchedSegments(value, headroom, max, segments);
      expect(hatched).toBeGreaterThanOrEqual(0);
      expect(filled + hatched).toBeLessThanOrEqual(segments);
      if (filled < segments && headroom > 0) expect(hatched).toBeGreaterThanOrEqual(1);
    }
  });
});
