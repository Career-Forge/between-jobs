import { describe, expect, it } from "vitest";
import { isScoreWithin } from "./scoreRange";

describe("a score as a number a page may print and rank by", () => {
  it("is a finite number from 0 to the top of the scale, ends included", () => {
    for (const value of [0, 1, 61, 99.5, 100, 12.25]) expect(isScoreWithin(value), String(value)).toBe(true);
  });

  it("is not a number below 0 or above 100, whatever its tier word says", () => {
    for (const value of [-0.01, -5, 100.01, 150, 1e9, -Infinity, Infinity]) expect(isScoreWithin(value), String(value)).toBe(false);
  });

  it("is not NaN, and not anything that is not a number", () => {
    for (const value of [Number.NaN, "88", "", null, undefined, {}, [], [88], true]) expect(isScoreWithin(value), String(value)).toBe(false);
  });

  it("takes the top of the scale from its caller", () => {
    expect(isScoreWithin(35, 35)).toBe(true);
    expect(isScoreWithin(36, 35)).toBe(false);
    expect(isScoreWithin(0, 35)).toBe(true);
  });
});
