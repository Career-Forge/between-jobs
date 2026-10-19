import { describe, expect, it } from "vitest";
import { checklistCounts, checklistSummary, type ChecklistState } from "./checklistSummary";

describe("checklistSummary", () => {
  it("reads like this: one flagged claim fails only one row, 5 pass · 1 fail · 4 not checked", () => {
    const states: ChecklistState[] = [
      "pass", "pass", "fail", "pass", "pass", "pass",
      "not_checked", "not_checked", "not_checked", "not_checked",
    ];
    expect(checklistSummary(states)).toBe("5 pass · 1 fail · 4 not checked");
  });

  it("always says all three counts, zero included, and an empty list is all zeros", () => {
    expect(checklistSummary([])).toBe("0 pass · 0 fail · 0 not checked");
    expect(checklistSummary(["not_checked"])).toBe("0 pass · 0 fail · 1 not checked");
  });

  it("counts every row exactly once, over random lists", () => {
    const all: ChecklistState[] = ["pass", "fail", "not_checked"];
    let seed = 3;
    for (let run = 0; run < 500; run++) {
      const states: ChecklistState[] = Array.from({ length: run % 17 }, () => {
        seed = (seed * 1103515245 + 12345) % 2147483648;
        return all[seed % 3];
      });
      const counts = checklistCounts(states);
      expect(counts.pass + counts.fail + counts.notChecked).toBe(states.length);
      expect(counts.pass).toBe(states.filter((state) => state === "pass").length);
    }
  });

  it("never says a row was verified", () => {
    expect(checklistSummary(["pass", "fail", "not_checked"])).not.toMatch(/verified/i);
  });
});
