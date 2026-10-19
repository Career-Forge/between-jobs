import { describe, expect, it } from "vitest";
import { reachedSection, readingLine } from "./useScrollSpy";

const at = (...tops: (number | null)[]) => tops.map((top, index) => ({ id: `s${index + 1}`, top }));

describe("readingLine", () => {
  it("is 35% of the viewport, and never less than 160px", () => {
    expect(readingLine(1000)).toBe(350);
    expect(readingLine(375)).toBe(160);
    expect(readingLine(0)).toBe(160);
  });
});

describe("reachedSection", () => {
  it("is the last section whose top is at or above the line", () => {
    expect(reachedSection(at(-900, -300, 50, 500), 250, false)).toBe("s3");
    expect(reachedSection(at(-900, -300, 250, 500), 250, false)).toBe("s3");
    expect(reachedSection(at(-900, -300, 251, 500), 250, false)).toBe("s2");
  });

  it("is null before the first section is reached", () => {
    expect(reachedSection(at(400, 800), 250, false)).toBeNull();
    expect(reachedSection([], 250, false)).toBeNull();
  });

  it("goes by the order given, not by position, so a section that is not on the page is skipped", () => {
    expect(reachedSection(at(-500, null, 600), 250, false)).toBe("s1");
    expect(reachedSection(at(null, null), 250, true)).toBeNull();
  });

  it("is the last section that is on the page at the bottom, however far below the line its top is", () => {
    expect(reachedSection(at(-900, 40, 700, null), 250, true)).toBe("s3");
  });
});
